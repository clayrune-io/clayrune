"""Backlog 40260b57: the venv must follow requirements.txt, at boot and on update.

Two gaps, one background sync (`mc/update_requirements.py`):

* The update that first ships pip-on-update runs the OLD in-memory
  `system_update`, and the next update sees an unchanged requirements.txt, so
  installs already behind never got psutil. Boot now compares a digest stamp
  with requirements.txt and installs when it is missing or different.
* pip must never run inside the HTTP request or delay boot: both triggers run
  it in a daemon thread, the stamp is written only after success, and the
  state is exposed for the Settings update row.
"""
import ast
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import update_requirements as ur  # noqa: E402
# Real upstream/clone rig from the force-push tests (used by the endpoint tests).
from tests.test_system_update_resync import _run, _sha, lab  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _fresh_sync_state():
    """The sync state is process-global; start and end every test idle."""
    def reset():
        ur.wait_for_sync(10)
        ur._state.update(status=ur.IDLE, trigger='', reason='', rc=None, detail='',
                         started_at=None, finished_at=None)
        ur._thread = None
    reset()
    yield
    reset()


class _Runner:
    """subprocess.run stand-in. `gate` (a threading.Event) holds pip open so a
    test can look at the state while it is 'running'."""

    def __init__(self, rc=0, out='Successfully installed psutil', err='', exc=None, gate=None):
        self.calls, self._rc, self._out, self._err = [], rc, out, err
        self._exc, self._gate = exc, gate

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self._gate is not None:
            assert self._gate.wait(10), 'test never released pip'
        if self._exc:
            raise self._exc
        return SimpleNamespace(returncode=self._rc, stdout=self._out, stderr=self._err)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / 'checkout'
    r.mkdir()
    (r / 'requirements.txt').write_text('flask\npsutil>=5.9.0\n')
    return r


@pytest.fixture()
def stamp(tmp_path):
    return tmp_path / 'home' / 'requirements_installed.sha256'


def _go(repo, stamp, runner, trigger='boot', **kw):
    snap = ur.start_background_sync(repo, trigger=trigger, runner=runner, stamp=stamp,
                                    frozen=kw.pop('frozen', False), **kw)
    ur.wait_for_sync(10)
    return snap, ur.get_state()


# ── boot: stamp decides ────────────────────────────────────────────────────

def test_missing_stamp_installs_and_writes_the_stamp(repo, stamp):
    runner = _Runner()
    snap, final = _go(repo, stamp, runner)
    assert final['status'] == ur.OK and final['trigger'] == 'boot'
    assert stamp.read_text().strip() == ur.requirements_digest(repo)
    (cmd, kw), = runner.calls
    assert cmd == [sys.executable, '-m', 'pip', 'install', '-r', str(repo / 'requirements.txt')]
    assert kw['timeout'] == ur.PIP_TIMEOUT_S and kw['capture_output'] is True


def test_matching_stamp_skips_pip(repo, stamp):
    stamp.parent.mkdir(parents=True)
    stamp.write_text(ur.requirements_digest(repo) + '\n')
    runner = _Runner()
    snap, final = _go(repo, stamp, runner)
    assert runner.calls == []
    assert final['status'] == ur.OK and 'already installed' in final['reason']


def test_changed_requirements_reinstall_even_with_an_old_stamp(repo, stamp):
    runner = _Runner()
    _go(repo, stamp, runner)
    (repo / 'requirements.txt').write_text('flask\npsutil>=5.9.0\nnewdep\n')
    runner2 = _Runner()
    _, final = _go(repo, stamp, runner2, trigger='update')
    assert len(runner2.calls) == 1 and final['status'] == ur.OK
    assert stamp.read_text().strip() == ur.requirements_digest(repo)


def test_start_returns_running_without_waiting_for_pip(repo, stamp):
    gate = threading.Event()
    runner = _Runner(gate=gate)
    snap = ur.start_background_sync(repo, trigger='boot', runner=runner, stamp=stamp, frozen=False)
    try:
        assert snap['status'] == ur.RUNNING            # returned while pip is still blocked
        assert ur.get_state()['status'] == ur.RUNNING
        assert not stamp.exists()                      # nothing is stamped before success
    finally:
        gate.set()
        ur.wait_for_sync(10)
    assert ur.get_state()['status'] == ur.OK and stamp.exists()


def test_a_second_trigger_while_running_does_not_start_a_second_pip(repo, stamp):
    gate = threading.Event()
    runner = _Runner(gate=gate)
    ur.start_background_sync(repo, trigger='boot', runner=runner, stamp=stamp, frozen=False)
    try:
        again = ur.start_background_sync(repo, trigger='update', runner=_Runner(),
                                         stamp=stamp, frozen=False)
        assert again['status'] == ur.RUNNING and again['trigger'] == 'boot'
    finally:
        gate.set()
        ur.wait_for_sync(10)
    assert len(runner.calls) == 1


# ── failure: reported, no stamp, retried ───────────────────────────────────

def test_pip_failure_is_reported_leaves_no_stamp_and_is_retried(repo, stamp):
    _, final = _go(repo, stamp, _Runner(rc=1, out='', err='ERROR: no matching distribution'))
    assert (final['status'], final['rc']) == (ur.FAILED, 1)
    assert 'no matching distribution' in final['detail']
    assert not stamp.exists()
    retry = _Runner()
    _, final2 = _go(repo, stamp, retry)
    assert len(retry.calls) == 1 and final2['status'] == ur.OK and stamp.exists()


@pytest.mark.parametrize('exc,needle', [
    (subprocess.TimeoutExpired('pip', 1), 'timed out'),
    (OSError('no exe'), 'no exe'),
])
def test_pip_timeout_or_launch_failure_is_reported(repo, stamp, exc, needle):
    _, final = _go(repo, stamp, _Runner(exc=exc))
    assert final['status'] == ur.FAILED and needle in final['detail']
    assert not stamp.exists()


def test_a_stamp_that_cannot_be_written_still_reports_ok(repo, tmp_path):
    blocker = tmp_path / 'file'
    blocker.write_text('x')
    _, final = _go(repo, blocker / 'sub' / 'stamp', _Runner())
    assert final['status'] == ur.OK


# ── skips ──────────────────────────────────────────────────────────────────

def test_frozen_build_is_skipped(repo, stamp):
    runner = _Runner()
    _, final = _go(repo, stamp, runner, frozen=True)
    assert runner.calls == [] and final['status'] == ur.SKIPPED and 'frozen' in final['reason']


def test_no_requirements_file_is_skipped(tmp_path, stamp):
    runner = _Runner()
    _, final = _go(tmp_path, stamp, runner)
    assert runner.calls == [] and final['status'] == ur.SKIPPED


def test_real_pip_is_never_run_under_pytest(repo, stamp, monkeypatch):
    called = []
    monkeypatch.setattr(ur.subprocess, 'run', lambda *a, **k: called.append(a))
    snap = ur.start_background_sync(repo, trigger='boot', stamp=stamp, frozen=False)
    ur.wait_for_sync(10)
    assert snap['status'] == ur.SKIPPED and called == []


# ── the digest ─────────────────────────────────────────────────────────────

def test_digest_ignores_line_endings_and_tracks_interpreter(repo, monkeypatch):
    (repo / 'requirements.txt').write_bytes(b'flask\npsutil\n')
    lf = ur.requirements_digest(repo)
    (repo / 'requirements.txt').write_bytes(b'flask\r\npsutil\r\n')
    assert ur.requirements_digest(repo) == lf
    monkeypatch.setattr(sys, 'executable', sys.executable + '.other')
    assert ur.requirements_digest(repo) != lf


def test_digest_is_none_without_the_file(tmp_path):
    assert ur.requirements_digest(tmp_path) is None


def test_get_state_is_a_copy(repo, stamp):
    _go(repo, stamp, _Runner())
    snap = ur.get_state()
    snap['status'] = 'tampered'
    assert ur.get_state()['status'] == ur.OK


# ── boot wiring ────────────────────────────────────────────────────────────

def test_server_boot_maintenance_kicks_the_sync_with_trigger_boot():
    """The boot chain is a long run of real backfills, so pin the wiring in the
    source instead of running it: it must call start_background_sync on the
    app dir with trigger='boot'."""
    tree = ast.parse((PROJECT_ROOT / 'server.py').read_text(encoding='utf-8'))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == '_startup_memory_maintenance')
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == 'start_background_sync']
    assert len(calls) == 1
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert isinstance(kw['trigger'], ast.Constant) and kw['trigger'].value == 'boot'
    assert isinstance(calls[0].args[0], ast.Name) and calls[0].args[0].id == '_APP_DIR'


# ── through the real endpoints ─────────────────────────────────────────────

@pytest.fixture()
def endpoint(lab, tmp_path, monkeypatch):
    """Flask client on the lab checkout; the sync gets a fake pip and a temp stamp."""
    import server
    from mc.blueprints import system_routes as sr
    runner = _Runner()
    real = ur.start_background_sync
    monkeypatch.setattr(sr, '_APP_DIR', lab['checkout'])
    monkeypatch.setattr(
        ur, 'start_background_sync',
        lambda root, **kw: real(root, runner=runner, stamp=tmp_path / 'stamp', frozen=False, **kw))
    server.app.config['TESTING'] = True
    return server.app.test_client(), runner


def _publish(lab, name, text):
    (lab['work'] / name).write_text(text)
    _run(['git', 'add', name], lab['work'])
    _run(['git', 'commit', '-m', f'edit {name}'], lab['work'])
    _run(['git', 'push', 'origin', 'master'], lab['work'])


def test_update_returns_before_pip_finishes(lab, endpoint):
    client, runner = endpoint
    runner._gate = gate = threading.Event()
    _publish(lab, 'requirements.txt', 'psutil>=5.9.0\n')
    try:
        resp = client.post('/api/system/update')
        body = resp.get_json()
        assert resp.status_code == 200 and body['ok'] is True
        assert body['requirements']['status'] == ur.RUNNING   # response came back mid-install
        assert body['requirements']['trigger'] == 'update'
    finally:
        gate.set()
        ur.wait_for_sync(10)
    assert ur.get_state()['status'] == ur.OK
    assert [c[0][1:5] for c in runner.calls] == [['-m', 'pip', 'install', '-r']]


def test_pip_failure_keeps_the_pull_and_shows_on_the_status_route(lab, endpoint):
    client, runner = endpoint
    runner._rc, runner._err = 1, 'ERROR: could not install'
    _publish(lab, 'requirements.txt', 'psutil>=5.9.0\n')
    tip = _sha(lab['upstream'], 'master')
    resp = client.post('/api/system/update')
    assert resp.status_code == 200 and resp.get_json()['ok'] is True
    ur.wait_for_sync(10)
    assert _sha(lab['checkout']) == tip                       # pull not undone
    status = client.get('/api/system/update/status').get_json()
    assert status['requirements']['status'] == ur.FAILED
    assert 'could not install' in status['requirements']['detail']


def test_status_route_reports_an_idle_sync_before_any_run(lab, endpoint):
    client, _ = endpoint
    status = client.get('/api/system/update/status').get_json()
    assert status['requirements']['status'] == ur.IDLE
