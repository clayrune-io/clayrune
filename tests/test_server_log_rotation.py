"""MC-1213: mc/server_log_rotation.py bounds data/logs/clayrune.log."""
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from mc import server_log_rotation as slr

REPO = Path(__file__).resolve().parent.parent
LINE = 'x' * 99 + '\n'            # 100 bytes


def _log(tmp_path, max_bytes=1000, generations=2, **kw):
    rl = slr.RotatingLog(tmp_path / 'clayrune.log', max_bytes, generations, **kw)
    rl.open()
    return rl


def _read(p):
    return Path(p).read_bytes().decode('utf-8')


# -- rotation ---------------------------------------------------------------

def test_active_file_is_rotated_to_dot_one_at_the_bound(tmp_path):
    rl = _log(tmp_path)
    for _ in range(11):                       # 1100 bytes
        rl.write(LINE.encode())
    rl.close()
    active, one = tmp_path / 'clayrune.log', tmp_path / 'clayrune.log.1'
    assert one.exists()
    assert one.stat().st_size >= 1000          # rotated at the bound, not before
    assert 'log-rotate' in _read(active)       # the new file says why it started
    assert (_read(one) + _read(active)).count(LINE) == 11   # no line lost


def test_only_the_configured_generations_are_kept(tmp_path):
    rl = _log(tmp_path, max_bytes=1000, generations=2)
    for _ in range(80):
        rl.write(LINE.encode())
    rl.close()
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ['clayrune.log', 'clayrune.log.1', 'clayrune.log.2']
    # newest content is in the lowest generation
    assert _read(tmp_path / 'clayrune.log.1').count(LINE) > 0


def test_disk_use_is_bounded(tmp_path):
    rl = _log(tmp_path, max_bytes=1000, generations=3)
    for _ in range(2000):
        rl.write(LINE.encode())
    rl.close()
    files = list(tmp_path.iterdir())
    assert len(files) == 3 + 1                  # active + 3 generations, never more
    # a file is rotated at the bound: at most one line and one announcement over
    assert all(p.stat().st_size <= 1000 + 100 + 250 for p in files)


# -- the file from before the bound ----------------------------------------

def test_file_from_before_the_bound_is_kept_not_rotated_away(tmp_path):
    legacy = tmp_path / 'clayrune.log'
    legacy.write_bytes(b'legacy\n' * 1000)     # 7000 bytes, 7x the bound
    rl = _log(tmp_path)
    rl.write(b'fresh\n')
    rl.close()
    kept = tmp_path / 'clayrune.pre-cap.log'
    assert kept.read_bytes() == b'legacy\n' * 1000        # untouched, not deleted
    active = _read(tmp_path / 'clayrune.log')
    assert 'kept as clayrune.pre-cap.log (not deleted)' in active   # said so
    assert 'fresh' in active


def test_second_pre_bound_file_does_not_overwrite_the_first(tmp_path):
    (tmp_path / 'clayrune.pre-cap.log').write_bytes(b'first\n')
    (tmp_path / 'clayrune.log').write_bytes(b'second\n' * 1000)
    _log(tmp_path).close()
    assert (tmp_path / 'clayrune.pre-cap.log').read_bytes() == b'first\n'
    assert (tmp_path / 'clayrune.pre-cap-2.log').read_bytes() == b'second\n' * 1000


def test_oversize_file_met_at_runtime_is_kept_not_shifted_into_generations(tmp_path):
    """A rotation that failed at boot (old parent still held the file) is
    retried later, when the file is far past the bound. It must be preserved,
    not shifted into .1 where the generation chain would drop it."""
    clock = [0.0]
    legacy = tmp_path / 'clayrune.log'
    legacy.write_bytes(b'legacy\n' * 1000)
    rl = slr.RotatingLog(legacy, 1000, 2, clock=lambda: clock[0])
    real_rename = os.rename
    calls = {'n': 0}

    def flaky(src, dst):
        calls['n'] += 1
        if calls['n'] == 1:
            raise PermissionError(32, 'in use')
        return real_rename(src, dst)

    os.rename = flaky
    try:
        rl.open()                               # boot attempt fails
    finally:
        os.rename = real_rename
    rl.write(b'a\n')                            # inside the backoff: no retry
    assert not (tmp_path / 'clayrune.pre-cap.log').exists()
    clock[0] += slr.RETRY_SECONDS + 1
    rl.write(b'b\n')                            # retry succeeds
    rl.close()
    assert (tmp_path / 'clayrune.pre-cap.log').read_bytes().startswith(b'legacy\n')
    assert not (tmp_path / 'clayrune.log.1').exists()


# -- failure handling -------------------------------------------------------

def test_file_between_one_and_two_bounds_whose_boot_move_failed_is_still_kept(tmp_path):
    clock = [0.0]
    legacy = tmp_path / 'clayrune.log'
    legacy.write_bytes(b'legacy\n' * 200)       # 1400 bytes: over 1000, under 2000
    rl = slr.RotatingLog(legacy, 1000, 2, clock=lambda: clock[0])
    real_rename = os.rename

    def deny(src, dst):
        raise PermissionError(32, 'in use')

    os.rename = deny
    try:
        rl.open()
    finally:
        os.rename = real_rename
    clock[0] += slr.RETRY_SECONDS + 1
    rl.write(b'b\n')
    rl.close()
    kept = (tmp_path / 'clayrune.pre-cap.log').read_bytes()
    assert kept.startswith(b'legacy\n' * 200)      # whole; the failure note rides after it
    assert not (tmp_path / 'clayrune.log.1').exists()


def test_failed_rotation_keeps_appending_announces_once_and_retries(tmp_path, monkeypatch):
    clock = [0.0]
    rl = _log(tmp_path, clock=lambda: clock[0])
    real_replace = os.replace

    def deny(src, dst):
        raise PermissionError(32, 'in use')

    monkeypatch.setattr(os, 'replace', deny)
    for _ in range(15):                         # past the bound (1000), under twice it
        rl.write(LINE.encode())                 # rotation denied
    text = _read(tmp_path / 'clayrune.log')
    assert text.count(LINE) == 15               # nothing dropped
    assert text.count('could not rotate') == 1  # one announcement, not one per write
    monkeypatch.setattr(os, 'replace', real_replace)
    rl.write(LINE.encode())                     # still inside the backoff window
    assert not (tmp_path / 'clayrune.log.1').exists()
    clock[0] += slr.RETRY_SECONDS + 1
    rl.write(LINE.encode())                     # backoff over: rotates
    rl.close()
    assert (tmp_path / 'clayrune.log.1').exists()


def test_write_never_raises(tmp_path):
    rl = _log(tmp_path)
    rl.close()
    rl.write(b'after close\n')                  # closed: dropped, no exception
    slr.RotatingLog(tmp_path / 'never-opened.log').write(b'x\n')


# -- the stream stand-in ----------------------------------------------------

def test_stream_never_splits_a_line_across_a_rotation(tmp_path):
    rl = _log(tmp_path, max_bytes=1000, generations=3)
    stream = slr.LogStream(rl, 1)
    for i in range(60):
        print(f'line {i:03d} ' + 'y' * 85, file=stream)   # print() = text, then '\n'
    stream.flush()
    rl.close()
    for p in tmp_path.iterdir():
        for ln in _read(p).splitlines():
            assert ln.startswith(('line ', '[log-rotate]')), (p.name, ln[:40])


def test_stream_holds_a_partial_line_until_flush(tmp_path):
    rl = _log(tmp_path)
    stream = slr.LogStream(rl, 1)
    stream.write('no newline yet')
    assert _read(tmp_path / 'clayrune.log') == ''
    stream.flush()
    assert _read(tmp_path / 'clayrune.log') == 'no newline yet'
    rl.close()


def test_stream_encodes_unencodable_text_instead_of_raising(tmp_path):
    rl = _log(tmp_path)
    stream = slr.LogStream(rl, 1)
    stream.write('arrow → and lone surrogate \ud800\n')
    rl.close()
    assert 'arrow →' in _read(tmp_path / 'clayrune.log')


# -- install() --------------------------------------------------------------

def test_install_is_a_no_op_when_stdout_is_not_the_log(tmp_path):
    before = (sys.stdout, sys.stderr)
    assert slr.install(tmp_path / 'clayrune.log') is None
    assert (sys.stdout, sys.stderr) == before
    assert not (tmp_path / 'clayrune.log').exists()   # did not create it either


_CHILD = textwrap.dedent('''
    import os, subprocess, sys, time
    sys.path.insert(0, {repo!r})
    from mc import server_log_rotation as slr
    slr.RETRY_SECONDS = 0.1
    rl = slr.install({log!r}, max_bytes=20000, generations=2)
    print('installed', rl is not None, flush=True)
    while not os.path.exists({go!r}):          # the old parent has let go
        time.sleep(0.05)
    time.sleep(0.2)
    for i in range(400):
        print('line %04d ' % i + 'x' * 80)
        if i == 150:
            subprocess.run([sys.executable, '-c', 'print("GRANDCHILD-OUT")'])
        if i == 160:
            os.write(1, b'RAW-FD1\\n')
    sys.stderr.write('STDERR-LINE\\n')
    print('done', flush=True)
''')


def test_restart_child_takes_over_an_inherited_log_handle(tmp_path):
    """The real path: the parent opens clayrune.log 'ab' and hands it to the
    child as stdout/stderr (_redirect_restart_child_output), then exits a moment
    later. The child must release that handle, own the file, keep the pre-bound
    file, rotate, and still capture output from a grandchild and a raw fd write."""
    log = tmp_path / 'clayrune.log'
    log.write_bytes(b'legacy\n' * 4000)         # 28000 bytes, over the 20000 bound
    go = tmp_path / 'go'
    script = tmp_path / 'child.py'
    script.write_text(_CHILD.format(repo=str(REPO), log=str(log), go=str(go)),
                      encoding='utf-8')
    handed = open(log, 'ab')
    try:
        p = subprocess.Popen([sys.executable, str(script)], stdout=handed,
                             stderr=subprocess.STDOUT)
        deadline = time.time() + 30
        while 'installed True' not in log.read_bytes().decode('utf-8', 'replace'):
            assert time.time() < deadline and p.poll() is None
            time.sleep(0.05)
        handed.close()                           # the old parent exits
        go.write_text('1')
        assert p.wait(timeout=60) == 0
    finally:
        handed.close()
    files = {q.name: _read(q) for q in tmp_path.iterdir()
             if q.name not in ('child.py', 'go')}
    # the lines it took in while the old parent still held it ride along
    assert files['clayrune.pre-cap.log'].startswith('legacy\n' * 4000)   # kept whole
    assert set(files) <= {'clayrune.log', 'clayrune.log.1', 'clayrune.log.2',
                          'clayrune.pre-cap.log'}
    assert 'clayrune.log.1' in files                                  # it rotated
    combined = ''.join(files.values())
    for marker in ('installed True', 'GRANDCHILD-OUT', 'RAW-FD1', 'STDERR-LINE', 'done'):
        assert marker in combined, marker
    for k, v in files.items():
        if k != 'clayrune.pre-cap.log':
            assert len(v.encode()) < 20000 + 400, k                   # each file bounded


def test_boot_installs_rotation_after_the_port_check_and_before_the_exec_token():
    src = (REPO / 'server.py').read_text(encoding='utf-8')
    boot = src[src.index('def boot('):]
    assert boot.index("_boot_phase('port-conflict wait'")         < boot.index('_server_log_rotation.install(')         < boot.index('ensure_exec_token(PORT)')
