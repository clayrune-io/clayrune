"""MC-1029: worktree teardown must not destroy what a live process or the
agent's gitignored `_scratch/` still depends on.

Incident 2026-10-01: a registered background script ran from a worktree's
`_scratch/`; the session ended, `_worktree_merge_back_on_end` removed the
worktree, and the script file plus every output vanished while the process kept
waiting. Every test runs against a THROWAWAY git repo in a temp dir.

  1. A registered process that references the worktree defers removal.
  2. Gitignored `_scratch/` files are archived outside the tree before delete
     (and a failed archive keeps the worktree).
  3. Junction safety survives: the archive never follows a link.
  4. `_worktree_merge_back_on_end` retries a deferral on a slow, de-duplicated
     timer instead of dropping it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from mc import project_sync  # noqa: E402
import mc.agent_worktree as w  # noqa: E402


def _git(cwd, *args):
    r = subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError(f'git {" ".join(args)}: {r.stderr.strip()}')
    return r.stdout.strip()


@pytest.fixture(scope='module', autouse=True)
def _register():
    project_sync.register(0, None, lambda p, m: None, lambda p: None,
                          lambda p, v: None, lambda: '',
                          Path(tempfile.gettempdir()))
    w.register(lambda p, m: None, lambda p: None, lambda m: None)


@pytest.fixture
def repo():
    d = Path(tempfile.mkdtemp(prefix='wt_guard_'))
    _git(d, 'init', '-q', '-b', 'master')
    _git(d, 'config', 'user.email', 't@t.local')
    _git(d, 'config', 'user.name', 'T')
    (d / 'app.py').write_text('x = 1\n', encoding='utf-8')
    (d / '.gitignore').write_text('.clayrune/\n_scratch/\n', encoding='utf-8')
    _git(d, 'add', '.')
    _git(d, 'commit', '-q', '-m', 'baseline')
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def project(repo):
    return {'id': 'guardproj', 'project_path': str(repo)}


@pytest.fixture
def archive_root(monkeypatch):
    """Point the scratch archive at a temp data root, and reset the injected
    process source after each test."""
    root = Path(tempfile.mkdtemp(prefix='wt_guard_data_'))
    monkeypatch.setattr(project_sync, '_data_root', root)
    yield root / 'data' / 'agent_scratch'
    w.set_live_process_source(None)
    shutil.rmtree(root, ignore_errors=True)


def _entry(wt, **kw):
    """A registry entry shaped like /api/processes/register writes it."""
    e = {'pid': os.getpid(), 'name': 'bg script', 'type': 'external',
         'session_id': '', 'project_id': 'guardproj',
         'command_preview': f'python {wt}/_scratch/job.py'}
    e.update(kw)
    return e


def _branch_exists(repo, sid):
    return bool(_git(repo, 'branch', '--list', w.branch_name(sid)))


# ── 1. live-process guard ───────────────────────────────────────────────────

def test_registered_process_in_worktree_defers_removal(project, repo, archive_root):
    ok, path = w.create(project, 'live1')
    assert ok, path
    registry = [_entry(path)]
    w.set_live_process_source(lambda: registry)

    ok, msg = w.remove(project, 'live1', delete_branch=True)
    assert not ok and msg.startswith('deferred:'), msg
    assert Path(path).exists(), 'worktree deleted under a live process'
    assert _branch_exists(repo, 'live1'), 'branch deleted under a live process'

    registry.clear()  # the process exited
    ok, msg = w.remove(project, 'live1', delete_branch=True)
    assert ok, msg
    assert not Path(path).exists()
    assert not _branch_exists(repo, 'live1')


def test_sibling_path_and_own_agent_process_do_not_defer(project, archive_root):
    ok, path = w.create(project, 'live2')
    assert ok, path
    # `<wt>x` shares the worktree path as a string prefix but is another dir.
    other = _entry(path + 'x')
    own = _entry(path, type='agent', session_id='live2')
    w.set_live_process_source(lambda: [other, own])

    assert w.live_process_refs(project, 'live2') == []
    ok, msg = w.remove(project, 'live2', delete_branch=True)
    assert ok, msg


def test_force_overrides_the_process_guard(project, archive_root):
    ok, path = w.create(project, 'live3')
    assert ok, path
    w.set_live_process_source(lambda: [_entry(path)])
    ok, msg = w.remove(project, 'live3', delete_branch=True, force=True)
    assert ok, msg
    assert not Path(path).exists()


def test_registry_failure_does_not_block_removal(project, archive_root):
    ok, path = w.create(project, 'live4')
    assert ok, path

    def boom():
        raise RuntimeError('registry unavailable')
    w.set_live_process_source(boom)
    assert w.live_process_refs(project, 'live4') == []
    ok, msg = w.remove(project, 'live4', delete_branch=True)
    assert ok, msg


def test_live_process_matched_by_full_command_line_when_preview_is_truncated(
        project, archive_root, tmp_path):
    """The registry keeps an 80-char preview, shorter than a worktree path, so
    the script path is usually cut off. The live PID's full command line (psutil
    or the stdlib fallback) must still defer removal."""
    ok, path = w.create(project, 'live6')
    assert ok, path
    script = f'{path}/_scratch/x.py'
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)', script],
                             cwd=str(tmp_path), stdin=subprocess.DEVNULL)
    bystander = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                 cwd=str(tmp_path), stdin=subprocess.DEVNULL)
    try:
        w.set_live_process_source(lambda: [
            _entry(path, pid=child.pid, command_preview='python x.py'),
            _entry(path, pid=bystander.pid, command_preview='python y.py')])
        refs = w.live_process_refs(project, 'live6')
        assert [r['pid'] for r in refs] == [child.pid], refs
        ok, msg = w.remove(project, 'live6', delete_branch=True)
        assert not ok and msg.startswith('deferred:'), msg
        assert Path(path).exists()
    finally:
        for c in (child, bystander):
            c.kill()
            c.wait()
    ok, msg = w.remove(project, 'live6', delete_branch=True, force=True)
    assert ok, msg


def test_live_process_matched_by_cwd_when_psutil_present(project, archive_root):
    """The registry keeps only an 80-char command preview, so a script launched
    as `cd <wt> && python _scratch/x.py` is visible only through the PID's cwd."""
    pytest.importorskip('psutil')
    ok, path = w.create(project, 'live5')
    assert ok, path
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                             cwd=path, stdin=subprocess.DEVNULL)
    try:
        w.set_live_process_source(lambda: [
            _entry(path, pid=child.pid, command_preview='python _scratch/x.py')])
        ok, msg = w.remove(project, 'live5', delete_branch=True)
        assert not ok and msg.startswith('deferred:'), msg
    finally:
        child.kill()
        child.wait()
    ok, msg = w.remove(project, 'live5', delete_branch=True, force=True)
    assert ok, msg


def test_gc_stale_does_not_cache_a_deferral(project, archive_root):
    """A deferral is transient. If gc cached its fingerprint, the worktree would
    stay 'preserved' after the process exited, until HEAD/dirty changed."""
    ok, path = w.create(project, 'gcdef1')
    assert ok, path
    registry = [_entry(path)]
    w.set_live_process_source(lambda: registry)

    out = w.gc_stale(project, live_session_ids=(), use_cache=True)
    assert out['preserved'] == ['gcdef1'] and out['removed'] == 0
    assert 'gcdef1' not in w._load_gc_cache(project)

    registry.clear()
    out = w.gc_stale(project, live_session_ids=(), use_cache=True)
    assert out['removed'] == 1
    assert not Path(path).exists()


# ── 2. gitignored _scratch/ is archived, not deleted ────────────────────────

def test_scratch_files_are_archived_before_delete(project, repo, archive_root):
    ok, path = w.create(project, 'scr1')
    assert ok, path
    scratch = Path(path) / '_scratch'
    (scratch / 'out').mkdir(parents=True)
    (scratch / 'job.py').write_text('print("hi")\n', encoding='utf-8')
    (scratch / 'out' / 'result.json').write_text('{"ok": true}', encoding='utf-8')
    (scratch / 'out' / 'blob.bin').write_bytes(bytes(range(256)))
    # Premise: git sees nothing, so has_unmerged_work cannot protect these.
    assert not w.has_unmerged_work(project, 'scr1')

    ok, msg = w.remove(project, 'scr1', delete_branch=True)
    assert ok, msg
    assert not Path(path).exists()
    dest = archive_root / 'scr1'
    assert (dest / 'job.py').read_text(encoding='utf-8') == 'print("hi")\n'
    assert (dest / 'out' / 'result.json').read_text(encoding='utf-8') == '{"ok": true}'
    assert (dest / 'out' / 'blob.bin').read_bytes() == bytes(range(256))


def test_failed_archive_keeps_the_worktree(project, archive_root, monkeypatch):
    ok, path = w.create(project, 'scr2')
    assert ok, path
    (Path(path) / '_scratch').mkdir()
    (Path(path) / '_scratch' / 'only_copy.txt').write_text('irreplaceable', encoding='utf-8')

    def boom(*a, **k):
        raise OSError('disk full')
    monkeypatch.setattr(w.shutil, 'copy2', boom)

    ok, msg = w.remove(project, 'scr2', delete_branch=True)
    assert not ok and msg.startswith('refused:') and '_scratch' in msg, msg
    assert (Path(path) / '_scratch' / 'only_copy.txt').read_text(encoding='utf-8') == 'irreplaceable'
    monkeypatch.undo()
    w.remove(project, 'scr2', delete_branch=True, force=True)


def test_worktree_without_scratch_creates_no_archive(project, archive_root):
    ok, path = w.create(project, 'scr3')
    assert ok, path
    (Path(path) / '_scratch').mkdir()  # present but empty
    ok, msg = w.remove(project, 'scr3', delete_branch=True)
    assert ok, msg
    assert not archive_root.exists()


def test_archive_name_collision_never_overwrites(project, archive_root):
    (archive_root / 'scr4').mkdir(parents=True)
    (archive_root / 'scr4' / 'old.txt').write_text('older run', encoding='utf-8')
    ok, path = w.create(project, 'scr4')
    assert ok, path
    (Path(path) / '_scratch').mkdir()
    (Path(path) / '_scratch' / 'new.txt').write_text('newer run', encoding='utf-8')
    ok, msg = w.remove(project, 'scr4', delete_branch=True)
    assert ok, msg
    assert (archive_root / 'scr4' / 'old.txt').read_text(encoding='utf-8') == 'older run'
    assert (archive_root / 'scr4-2' / 'new.txt').read_text(encoding='utf-8') == 'newer run'


# ── 3. junction safety is preserved ─────────────────────────────────────────

def test_archive_and_remove_never_follow_a_link_in_scratch(project, archive_root, tmp_path):
    main = tmp_path / 'main_nm'
    main.mkdir()
    for i in range(4):
        (main / f'f{i}.txt').write_text('real', encoding='utf-8')
    ok, path = w.create(project, 'lnk1')
    assert ok, path
    scratch = Path(path) / '_scratch'
    scratch.mkdir()
    (scratch / 'keep.txt').write_text('mine', encoding='utf-8')
    link = scratch / 'node_modules'
    if sys.platform == 'win32':
        r = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(main)],
                           capture_output=True, stdin=subprocess.DEVNULL)
        made = r.returncode == 0
    else:
        try:
            link.symlink_to(main, target_is_directory=True)
            made = True
        except OSError:
            made = False
    if not made:
        pytest.skip('cannot create a directory link on this platform')

    ok, msg = w.remove(project, 'lnk1', delete_branch=True)
    assert ok, msg
    assert len(list(main.iterdir())) == 4, 'link target was damaged'
    dest = archive_root / 'lnk1'
    assert (dest / 'keep.txt').read_text(encoding='utf-8') == 'mine'
    assert not (dest / 'node_modules').exists(), 'archive followed the link'


# ── 4. _worktree_merge_back_on_end retries a deferral ───────────────────────

@pytest.fixture
def ar_env(monkeypatch):
    import server  # noqa: F401  (wires blueprints)
    from mc.blueprints import agent_routes as ar
    timers = []

    class _FakeTimer:
        def __init__(self, interval, fn, args=()):
            self.interval, self.fn, self.args, self.daemon = interval, fn, args, False
            timers.append(self)

        def start(self):
            pass

    monkeypatch.setattr(ar.threading, 'Timer', _FakeTimer)
    monkeypatch.setattr(ar, 'load_project', lambda pid: {'id': pid, 'project_path': 'x'})
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    ar._worktree_retry_pending.clear()
    yield ar, timers, monkeypatch
    ar._worktree_retry_pending.clear()


def test_deferred_removal_is_retried_once_on_a_slow_timer(ar_env):
    ar, timers, mp = ar_env
    calls = []
    mp.setattr(ar._agent_worktree, 'merge_back', lambda p, sid: ('nothing', ''))
    mp.setattr(ar._agent_worktree, 'remove',
               lambda p, sid, **k: calls.append(sid) or (False, 'deferred: 1 registered process(es) (pid 7)'))
    session = {'session_id': 'abc123', 'project_id': 'p', '_worktree_isolated': True}

    ar._worktree_merge_back_on_end(session)
    ar._worktree_merge_back_on_end(session)  # a second end-hook must not stack timers

    assert calls == ['abc123', 'abc123']
    assert len(timers) == 1, 'timers stacked for one session'
    assert timers[0].interval == ar._WORKTREE_RETRY_SECONDS >= 60
    assert timers[0].daemon is True
    assert timers[0].args == (session, 1)


def test_retry_runs_removal_and_stops_when_it_succeeds(ar_env):
    ar, timers, mp = ar_env
    results = iter([(False, 'deferred: x'), (True, 'removed')])
    mp.setattr(ar._agent_worktree, 'merge_back', lambda p, sid: ('nothing', ''))
    mp.setattr(ar._agent_worktree, 'remove', lambda p, sid, **k: next(results))
    session = {'session_id': 'abc124', 'project_id': 'p', '_worktree_isolated': True}

    ar._worktree_merge_back_on_end(session)
    assert len(timers) == 1
    timers[0].fn(*timers[0].args)  # the timer fires
    assert len(timers) == 1, 'rescheduled after a successful removal'
    assert 'abc124' not in ar._worktree_retry_pending


def test_retry_skips_a_session_that_resumed(ar_env):
    ar, timers, mp = ar_env
    mp.setattr(ar._agent_worktree, 'merge_back', lambda p, sid: ('nothing', ''))
    removes = []
    mp.setattr(ar._agent_worktree, 'remove',
               lambda p, sid, **k: removes.append(sid) or (False, 'deferred: x'))
    session = {'session_id': 'abc125', 'project_id': 'p', '_worktree_isolated': True}
    ar._worktree_merge_back_on_end(session)
    mp.setitem(ar.agent_sessions, 'abc125', {'status': 'running'})

    timers[0].fn(*timers[0].args)
    assert removes == ['abc125'], 'removal attempted under a resumed session'


def test_retry_gives_up_after_the_attempt_cap(ar_env):
    ar, timers, mp = ar_env
    mp.setattr(ar._agent_worktree, 'merge_back', lambda p, sid: ('nothing', ''))
    mp.setattr(ar._agent_worktree, 'remove', lambda p, sid, **k: (False, 'deferred: x'))
    session = {'session_id': 'abc126', 'project_id': 'p', '_worktree_isolated': True}
    ar._worktree_merge_back_on_end(session, _attempt=ar._WORKTREE_RETRY_MAX_ATTEMPTS)
    assert timers == []


def test_non_deferral_refusal_is_not_retried(ar_env):
    ar, timers, mp = ar_env
    mp.setattr(ar._agent_worktree, 'merge_back', lambda p, sid: ('clean', ''))
    mp.setattr(ar._agent_worktree, 'remove',
               lambda p, sid, **k: (False, 'refused: could not archive _scratch: disk full'))
    ar._worktree_merge_back_on_end(
        {'session_id': 'abc127', 'project_id': 'p', '_worktree_isolated': True})
    assert timers == []


def test_live_registered_processes_filters_dead_pids_and_drops_proc(ar_env):
    ar, _, mp = ar_env
    mp.setitem(ar.tracked_processes, 111111, {'pid': 111111, 'proc': object(), 'name': 'dead'})
    mp.setitem(ar.tracked_processes, os.getpid(), {'pid': os.getpid(), 'proc': object(), 'name': 'me'})
    mp.setattr(ar, '_pid_is_alive', lambda pid: pid == os.getpid())
    out = ar._live_registered_processes()
    assert [e['pid'] for e in out] == [os.getpid()]
    assert 'proc' not in out[0]
