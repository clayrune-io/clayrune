"""Every `data/` state writer must be atomic, and every reader must survive a
file that isn't (MC-946, widened scope).

The agent log was one victim of a general defect. `/api/system/restart` spawns
the replacement server BEFORE stopping the old one and hard-`os._exit`s the old
process on a 10s watchdog, so two MC processes write `data/projects/*.json`
concurrently for up to ten seconds. Every writer was a bare
`write_text(json.dumps(...))` — truncate first, then write — so a kill in that
window leaves a partial file.

The two symptoms Ron actually saw are the same defect twice: a truncated
PROJECT record made the whole project vanish from the dashboard (`load_projects`
skips what it cannot parse), and a truncated AGENT LOG became permanent when
the startup backfill rewrote it.

The restart ordering is deliberate and stays; atomic writes make it safe.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.atomic_json import write_json_atomic          # noqa: E402
from mc.blueprints import project_routes as P         # noqa: E402

TRUNCATED = '{\n  "id": "half_written",\n  "status": "act'


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / 'projects'
    d.mkdir()
    monkeypatch.setattr(P, 'DATA_DIR', d)
    return d


# ── the helper ──────────────────────────────────────────────────────────────

def test_write_is_atomic_and_round_trips(data_dir):
    target = data_dir / 'p.json'
    write_json_atomic(target, {'id': 'p', 'n': 1}, indent=2, ensure_ascii=False)
    assert json.loads(target.read_text(encoding='utf-8')) == {'id': 'p', 'n': 1}


def test_failed_serialization_leaves_the_previous_content_intact(data_dir):
    target = data_dir / 'p.json'
    write_json_atomic(target, {'id': 'p', 'keep': True}, indent=2)
    good = target.read_text(encoding='utf-8')

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        write_json_atomic(target, {'id': 'p', 'bad': Unserializable()}, indent=2)

    assert target.read_text(encoding='utf-8') == good
    assert list(data_dir.iterdir()) == [target], \
        'no temp file may survive a failed write'


def test_replace_retries_a_transient_windows_lock_then_succeeds(data_dir, monkeypatch):
    """MC-959: `os.replace` can raise WinError 5 (Access is denied) or 32
    (used by another process) for a few milliseconds while an AV scanner or
    the Windows Search indexer holds the just-written temp file open. A
    dispatch's project-record save hit exactly this on 2026-09-18, and the
    caller retried believing dispatch itself had failed -- but it had
    already spawned the child. A short retry here should clear a transient
    lock without the caller ever seeing an exception."""
    import mc.atomic_json as aj

    real_replace = aj.os.replace
    calls = {'n': 0}

    def flaky(src, dst):
        calls['n'] += 1
        if calls['n'] < 3:
            err = OSError('Access is denied')
            err.winerror = 5
            raise err
        return real_replace(src, dst)

    monkeypatch.setattr(aj.os, 'replace', flaky)
    monkeypatch.setattr(aj.time, 'sleep', lambda s: None)  # don't actually wait in tests

    target = data_dir / 'p.json'
    write_json_atomic(target, {'id': 'p'}, indent=2)

    assert calls['n'] == 3
    assert json.loads(target.read_text(encoding='utf-8')) == {'id': 'p'}


def test_replace_gives_up_after_max_attempts_and_cleans_up_the_temp(data_dir, monkeypatch):
    """A permanently-locked target (not just a transient one) must still
    raise eventually -- the retry is a bounded mitigation, not a hang -- and
    must not strand its temp file when it does."""
    import mc.atomic_json as aj

    def always_locked(src, dst):
        err = OSError('used by another process')
        err.winerror = 32
        raise err

    monkeypatch.setattr(aj.os, 'replace', always_locked)
    monkeypatch.setattr(aj.time, 'sleep', lambda s: None)

    target = data_dir / 'p.json'
    with pytest.raises(OSError):
        write_json_atomic(target, {'id': 'p'}, indent=2)

    assert list(data_dir.iterdir()) == [], \
        'a permanently-locked replace must not strand a temp file'


def test_replace_does_not_retry_a_non_transient_oserror(data_dir, monkeypatch):
    """Retrying is scoped to the two known-transient Windows codes -- any
    other OSError (disk full, a genuine permission denial with no winerror,
    etc.) must raise immediately rather than burn through the retry budget."""
    import mc.atomic_json as aj

    calls = {'n': 0}

    def other_error(src, dst):
        calls['n'] += 1
        raise OSError('disk full')  # no winerror attribute at all

    monkeypatch.setattr(aj.os, 'replace', other_error)

    target = data_dir / 'p.json'
    with pytest.raises(OSError):
        write_json_atomic(target, {'id': 'p'}, indent=2)

    assert calls['n'] == 1, 'a non-transient OSError must not be retried'


def test_temp_file_is_never_mistaken_for_a_project_record(data_dir, monkeypatch):
    """The temp lands in DATA_DIR, which load_projects globs for `*.json`.

    Its name must not end in .json (CLAUDE.md's DATA_DIR pollution rule), and
    it must match the `.{name}.tmp{pid}` shape mc.core.sweep_orphan_tmpfiles
    already cleans up, so a crash between write and replace strands nothing
    permanent.
    """
    import re
    seen = {}
    real_replace = __import__('os').replace

    def spy(src, dst):
        seen['tmp'] = Path(src).name
        return real_replace(src, dst)

    monkeypatch.setattr('mc.atomic_json.os.replace', spy)
    write_json_atomic(data_dir / 'p.json', {'id': 'p'})

    assert not seen['tmp'].endswith('.json')
    assert re.match(r'^\..+\.tmp\d+$', seen['tmp']), seen['tmp']


# ── the readers ─────────────────────────────────────────────────────────────

def test_load_projects_skips_a_truncated_record_and_says_so(data_dir, capsys):
    (data_dir / 'good.json').write_text(
        json.dumps({'id': 'good', 'status': 'active'}), encoding='utf-8')
    (data_dir / 'half_written.json').write_text(TRUNCATED, encoding='utf-8')

    ids = [p.get('id') for p in P.load_projects()]

    # Skipping is correct — one bad record must not 500 the dashboard.
    assert ids == ['good']
    # But the project has DISAPPEARED, so it must not be a debug-level shrug.
    out = capsys.readouterr().out
    assert 'CORRUPT RECORD' in out and 'half_written.json' in out


def test_load_project_returns_none_instead_of_raising(data_dir, capsys):
    (data_dir / 'half_written.json').write_text(TRUNCATED, encoding='utf-8')

    # Used to propagate json.JSONDecodeError → a 500 on every route that
    # touches the project. None is what a missing record already returns,
    # so every caller already handles it.
    assert P.load_project('half_written') is None
    assert 'CORRUPT RECORD' in capsys.readouterr().out
    # The record is reported absent, never rewritten from the failed read.
    assert (data_dir / 'half_written.json').read_text(encoding='utf-8') == TRUNCATED


def test_load_project_still_returns_a_good_record(data_dir):
    (data_dir / 'good.json').write_text(
        json.dumps({'id': 'good', 'backlog': []}), encoding='utf-8')
    assert (P.load_project('good') or {}).get('id') == 'good'
    assert P.load_project('never_existed') is None


def test_save_project_round_trips_through_the_atomic_writer(data_dir):
    P.save_project('p', {'id': 'p', 'status': 'active', 'backlog': []})
    assert (P.load_project('p') or {}).get('status') == 'active'


# ── the canary ──────────────────────────────────────────────────────────────

def test_no_new_non_atomic_json_writers(repo_root):
    """Fail when someone adds `path.write_text(json.dumps(...))` back.

    This is the defect class, not one bug: MC-946 was caused by six of these,
    and the previous restart lost a whole project record to a seventh. Every
    JSON state write goes through mc.atomic_json.write_json_atomic. A grep is
    the only thing that catches the NEXT one, because the failure is invisible
    until a process dies at the wrong microsecond.

    If you are adding a genuinely throwaway write (a scratch file nothing
    reads back), put it outside mc/ or use a different call shape -- do not
    add an exemption list here.
    """
    import re
    offenders = []
    pattern = re.compile(r'write_text\(\s*json\.dumps')
    roots = [repo_root / 'mc', repo_root / 'server.py']
    for root in roots:
        files = [root] if root.is_file() else sorted(root.rglob('*.py'))
        for f in files:
            if f.name == 'atomic_json.py':
                continue
            for i, line in enumerate(
                    f.read_text(encoding='utf-8').splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f'{f.relative_to(repo_root)}:{i}')
    assert not offenders, (
        'non-atomic JSON writes found -- use mc.atomic_json.write_json_atomic:\n'
        + '\n'.join(offenders))


# ── MC-965: on Windows a concurrent READER breaks the WRITE ────────────────
# `os.replace` is atomic everywhere, but on Windows it refuses to land on a
# target any other handle has open (CPython's open() omits FILE_SHARE_DELETE).
# Measured on this box before the fix -- 2 writer threads, 3 reader threads,
# 5 seconds: 344 successful writes against 5090 PermissionError/WinError 5.
# Every caller wraps this in a swallowing `except`, so the loss was silent.

def test_write_survives_a_reader_holding_the_target_open(tmp_path):
    """The exact shape that failed: the target is open when replace fires.

    The reader releases after 150ms, well inside the retry deadline -- a
    bare `os.replace` raises on the FIRST attempt and loses the write.
    A handle held past the deadline is still a hard failure, on purpose.
    """
    import threading
    import time as _t
    from mc.atomic_json import write_json_atomic
    p = tmp_path / 'state.json'
    write_json_atomic(p, {'v': 0})
    opened = threading.Event()

    def _hold():
        with open(p, encoding='utf-8') as holder:
            holder.read(1)
            opened.set()
            _t.sleep(0.15)

    t = threading.Thread(target=_hold)
    t.start()
    try:
        assert opened.wait(timeout=5)
        write_json_atomic(p, {'v': 1})             # bare os.replace -> WinError 5
    finally:
        t.join(timeout=5)
    assert json.loads(p.read_text(encoding='utf-8')) == {'v': 1}


def test_concurrent_readers_do_not_drop_writes(tmp_path):
    """End to end under real thread contention: zero writes may be lost."""
    import threading
    import time as _t
    from mc.atomic_json import write_json_atomic
    p = tmp_path / 'agent_log.json'
    write_json_atomic(p, [])
    stop = threading.Event()
    failures: list[BaseException] = []

    def _read():
        while not stop.is_set():
            try:
                p.read_text(encoding='utf-8')
            except OSError:
                pass                                # reader-side, not under test

    readers = [threading.Thread(target=_read, daemon=True) for _ in range(3)]
    for t in readers:
        t.start()
    try:
        deadline = _t.monotonic() + 2.0
        n = 0
        while _t.monotonic() < deadline:
            try:
                write_json_atomic(p, [{'i': n}])
                n += 1
            except BaseException as e:              # noqa: BLE001 - recording it IS the test
                failures.append(e)
    finally:
        stop.set()
        for t in readers:
            t.join(timeout=2)
    assert n > 0, 'no writes attempted -- the loop never ran'
    assert not failures, f'{len(failures)} of {n + len(failures)} writes lost: {failures[0]!r}'


def test_read_retry_surfaces_a_persistent_error_rather_than_empty_text(tmp_path):
    """`read_text_with_retry` must RAISE, never return '' -- the caller's
    whole ability to tell "unreadable" from "corrupt" depends on it."""
    from mc.atomic_json import read_text_with_retry
    missing = tmp_path / 'nope.json'
    with pytest.raises(OSError):
        read_text_with_retry(missing, attempts=1)


def test_read_retry_rides_out_a_transient_sharing_violation(tmp_path, monkeypatch):
    from pathlib import Path as _P

    from mc import atomic_json
    p = tmp_path / 'state.json'
    p.write_text('{"v": 1}', encoding='utf-8')
    calls = {'n': 0}
    real = _P.read_text

    def _flaky(self, *a, **k):
        calls['n'] += 1
        if calls['n'] < 3:
            raise PermissionError(13, 'Permission denied')
        return real(self, *a, **k)

    monkeypatch.setattr(_P, 'read_text', _flaky)
    assert atomic_json.read_text_with_retry(p) == '{"v": 1}'
    assert calls['n'] == 3
