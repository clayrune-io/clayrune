"""Backlog 40260b57 follow-up: /api/system/update installs changed requirements.

`git pull` moved the code but never the venv, so an updated install lacked
psutil and dispatch attribution silently fell back to trusting Origin. After a
successful pull the endpoint now runs `pip install -r requirements.txt` when
that file changed, reports the outcome in the response, and never undoes the
pull on a pip failure.
"""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import update_requirements as ur  # noqa: E402
# Real upstream/clone rig and Flask client from the force-push tests.
from tests.test_system_update_resync import _run, _sha, lab  # noqa: E402,F401


def _commit(repo, name, text):
    (repo / name).write_text(text)
    _run(['git', 'add', name], repo)
    _run(['git', 'commit', '-m', f'edit {name}'], repo)


def _git(args, cwd, timeout=30):
    r = subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


class _Runner:
    """subprocess.run stand-in recording every call."""

    def __init__(self, rc=0, out='Successfully installed psutil', err='', exc=None):
        self.calls, self._rc, self._out, self._err, self._exc = [], rc, out, err, exc

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self._exc:
            raise self._exc
        return SimpleNamespace(returncode=self._rc, stdout=self._out, stderr=self._err)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / 'r'
    r.mkdir()
    _run(['git', 'init', '-b', 'master', str(r)], tmp_path)
    _run(['git', 'config', 'user.email', 't@example.com'], r)
    _run(['git', 'config', 'user.name', 'T'], r)
    _commit(r, 'requirements.txt', 'flask\n')
    return r


def _sync(repo, old, runner, **kw):
    return ur.sync_requirements(_git, repo, old, frozen=kw.pop('frozen', False),
                                runner=runner, **kw)


def test_changed_requirements_run_pip_with_this_interpreter(repo):
    old = _sha(repo)
    _commit(repo, 'requirements.txt', 'flask\npsutil>=5.9.0\n')
    runner = _Runner()
    res = _sync(repo, old, runner)
    assert res['ran'] is True and res['ok'] is True and res['rc'] == 0
    (cmd, kw), = runner.calls
    assert cmd == [sys.executable, '-m', 'pip', 'install', '-r', str(repo / 'requirements.txt')]
    assert kw['timeout'] == ur.PIP_TIMEOUT_S and kw['capture_output'] is True


def test_unchanged_requirements_do_not_run_pip(repo):
    old = _sha(repo)
    _commit(repo, 'other.txt', 'x\n')
    runner = _Runner()
    res = _sync(repo, old, runner)
    assert runner.calls == []
    assert (res['ran'], res['ok']) == (False, True)


def test_pip_failure_is_reported_not_swallowed_or_raised(repo):
    old = _sha(repo)
    _commit(repo, 'requirements.txt', 'flask\npsutil>=5.9.0\n')
    res = _sync(repo, old, _Runner(rc=1, out='', err='ERROR: no matching distribution'))
    assert (res['ran'], res['ok'], res['rc']) == (True, False, 1)
    assert 'no matching distribution' in res['detail']


def test_pip_timeout_is_reported(repo):
    old = _sha(repo)
    _commit(repo, 'requirements.txt', 'flask\npsutil>=5.9.0\n')
    res = _sync(repo, old, _Runner(exc=subprocess.TimeoutExpired('pip', 1)))
    assert (res['ran'], res['ok']) == (True, False) and 'timed out' in res['detail']


def test_pip_that_cannot_start_is_reported(repo):
    old = _sha(repo)
    _commit(repo, 'requirements.txt', 'flask\npsutil>=5.9.0\n')
    res = _sync(repo, old, _Runner(exc=OSError('no exe')))
    assert (res['ran'], res['ok']) == (True, False) and 'no exe' in res['detail']


def test_frozen_build_skips_pip(repo):
    old = _sha(repo)
    _commit(repo, 'requirements.txt', 'flask\npsutil>=5.9.0\n')
    runner = _Runner()
    res = _sync(repo, old, runner, frozen=True)
    assert runner.calls == [] and (res['ran'], res['ok']) == (False, True)
    assert 'frozen' in res['reason']


def test_unknown_previous_commit_installs_anyway(repo):
    runner = _Runner()
    res = _sync(repo, '', runner)
    assert len(runner.calls) == 1 and res['ran'] is True


def test_missing_requirements_file_is_a_noop(tmp_path):
    runner = _Runner()
    res = ur.sync_requirements(_git, tmp_path, 'abc', frozen=False, runner=runner)
    assert runner.calls == [] and res['ok'] is True


# ── through the real endpoint ──────────────────────────────────────────────

@pytest.fixture()
def endpoint(lab, monkeypatch):
    """Flask client on the lab checkout; every pip call goes to a recorder."""
    import server
    from mc.blueprints import system_routes as sr
    runner = _Runner()
    real = sr.sync_requirements
    monkeypatch.setattr(sr, '_APP_DIR', lab['checkout'])
    monkeypatch.setattr(sr, 'sync_requirements',
                        lambda git, root, old, **kw: real(git, root, old, runner=runner, **kw))
    server.app.config['TESTING'] = True
    return server.app.test_client(), runner


def _publish(lab, name, text):
    _commit(lab['work'], name, text)
    _run(['git', 'push', 'origin', 'master'], lab['work'])


def test_update_installs_requirements_that_the_pull_changed(lab, endpoint):
    client, runner = endpoint
    _publish(lab, 'requirements.txt', 'psutil>=5.9.0\n')
    resp = client.post('/api/system/update')
    body = resp.get_json()
    assert resp.status_code == 200 and body['ok'] is True
    assert body['requirements']['ran'] is True and body['requirements']['ok'] is True
    assert [c[0][1:5] for c in runner.calls] == [['-m', 'pip', 'install', '-r']]


def test_update_without_a_requirements_change_does_not_run_pip(lab, endpoint):
    client, runner = endpoint
    _publish(lab, 'feature.md', 'x\n')
    body = client.post('/api/system/update').get_json()
    assert body['ok'] is True and body['requirements']['ran'] is False
    assert runner.calls == []


def test_pip_failure_keeps_the_pull_and_is_visible_in_the_response(lab, endpoint):
    client, runner = endpoint
    runner._rc, runner._err = 1, 'ERROR: could not install'
    _publish(lab, 'requirements.txt', 'psutil>=5.9.0\n')
    tip = _sha(lab['upstream'], 'master')
    resp = client.post('/api/system/update')
    body = resp.get_json()
    assert resp.status_code == 200 and body['ok'] is True
    assert body['requirements']['ok'] is False and body['requirements']['rc'] == 1
    assert 'could not install' in body['requirements']['detail']
    assert _sha(lab['checkout']) == tip          # pull not undone
