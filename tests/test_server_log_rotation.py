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


def _deny_copy(*a, **k):
    raise PermissionError(13, 'copy denied')


_HOLDER = ("import sys, time; f = open(sys.argv[1], 'ab'); "
           "print('held', flush=True); time.sleep(120)")


@pytest.fixture
def foreign_holder():
    """A second PROCESS holding the log open, the way the venv redirector
    (.venv/Scripts/python.exe) holds the server's inherited stdout for the
    server's whole life. Killed by its own PID only."""
    procs = []

    def hold(path):
        Path(path).touch()
        p = subprocess.Popen([sys.executable, '-c', _HOLDER, str(path)],
                             stdout=subprocess.PIPE, text=True)
        procs.append(p)
        assert p.stdout is not None and p.stdout.readline().strip() == 'held'
        return p

    yield hold
    for p in procs:
        p.kill()
        p.wait(timeout=10)
        if p.stdout is not None:
            p.stdout.close()


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
    real_replace, real_copy = os.replace, slr.RotatingLog._copy_truncate

    def deny(src, dst):
        raise PermissionError(32, 'in use')

    os.replace, slr.RotatingLog._copy_truncate = deny, _deny_copy
    try:
        rl.open()                               # boot attempt fails both ways
    finally:
        os.replace, slr.RotatingLog._copy_truncate = real_replace, real_copy
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
    real_replace, real_copy = os.replace, slr.RotatingLog._copy_truncate

    def deny(src, dst):
        raise PermissionError(32, 'in use')

    os.replace, slr.RotatingLog._copy_truncate = deny, _deny_copy
    try:
        rl.open()
    finally:
        os.replace, slr.RotatingLog._copy_truncate = real_replace, real_copy
    clock[0] += slr.RETRY_SECONDS + 1
    rl.write(b'b\n')
    rl.close()
    kept = (tmp_path / 'clayrune.pre-cap.log').read_bytes()
    assert kept.startswith(b'legacy\n' * 200)      # whole; the failure note rides after it
    assert not (tmp_path / 'clayrune.log.1').exists()


def test_failed_rotation_keeps_appending_announces_once_and_retries(tmp_path, monkeypatch):
    clock = [0.0]
    rl = _log(tmp_path, clock=lambda: clock[0])

    def deny(src, dst):
        raise PermissionError(32, 'in use')

    monkeypatch.setattr(os, 'replace', deny)
    monkeypatch.setattr(slr.RotatingLog, '_copy_truncate', _deny_copy)
    for _ in range(15):                         # past the bound (1000), under twice it
        rl.write(LINE.encode())                 # rotation denied
    text = _read(tmp_path / 'clayrune.log')
    assert text.count(LINE) == 15               # nothing dropped
    assert text.count('could not rotate') == 1  # one announcement, not one per write
    monkeypatch.undo()                          # both ways allowed again
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


# -- a process we do not own holds the file (MC-1213 reopened) --------------
#
# The live server on 2026-11-18: rotation never happened and clayrune.log
# reached 530 MB, because its parent, the venv redirector, held the file open
# without FILE_SHARE_DELETE, so every rename failed with WinError 32. These
# fail on Windows without the copy-truncate fallback.

def test_rotation_holds_the_bound_while_another_process_holds_the_file(
        tmp_path, foreign_holder):
    log = tmp_path / 'clayrune.log'
    foreign_holder(log)
    rl = _log(tmp_path, max_bytes=1000, generations=2)
    for _ in range(80):                          # 8000 bytes through a 1000 bound
        rl.write(LINE.encode())
    rl.close()
    sizes = {q.name: q.stat().st_size for q in tmp_path.iterdir()}
    assert set(sizes) == {'clayrune.log', 'clayrune.log.1', 'clayrune.log.2'}
    assert all(v <= 1000 + 100 + 300 for v in sizes.values()), sizes
    assert 'could not rotate' not in ''.join(_read(q) for q in tmp_path.iterdir())
    assert b'\0' not in log.read_bytes()                      # no hole after truncate


def test_no_line_is_lost_by_a_copy_truncate(tmp_path, foreign_holder):
    log = tmp_path / 'clayrune.log'
    foreign_holder(log)
    rl = _log(tmp_path, max_bytes=1000, generations=5)
    for i in range(40):                          # 4000 bytes: 4 rotations, all kept
        rl.write(f'{i:04d}'.encode() + b'z' * 95 + b'\n')
    rl.close()
    text = ''.join(_read(q) for q in tmp_path.iterdir())
    for i in range(40):
        assert text.count(f'{i:04d}' + 'z' * 95) == 1, i   # once: none lost, none doubled


def test_file_from_before_the_bound_is_kept_while_another_process_holds_it(
        tmp_path, foreign_holder):
    log = tmp_path / 'clayrune.log'
    log.write_bytes(b'legacy\n' * 1000)          # 7000 bytes, 7x the bound
    foreign_holder(log)
    rl = _log(tmp_path)
    rl.write(b'fresh\n')
    rl.close()
    assert (tmp_path / 'clayrune.pre-cap.log').read_bytes() == b'legacy\n' * 1000
    active = _read(log)
    assert active.startswith('[log-rotate] clayrune.log was')
    assert 'could not rotate' not in active
    assert active.rstrip().endswith('fresh')


def test_raw_write_through_a_duplicated_fd_after_copy_truncate_leaves_no_hole(
        tmp_path, foreign_holder):
    """fd 1/2 are dup2'd onto the log fd and share its file position. After a
    truncate that position must go back to 0, or a raw C-level write (a child,
    a crash dump) lands at the old offset behind a run of zero bytes."""
    log = tmp_path / 'clayrune.log'
    foreign_holder(log)
    std = os.open(os.devnull, os.O_RDWR)        # stands in for fd 1
    try:
        rl = slr.RotatingLog(log, 1000, 2)
        rl.open(std_fds=[std])
        for _ in range(11):
            rl.write(LINE.encode())             # the 11th write rotates
        os.write(std, b'RAW\n')
        rl.close()
    finally:
        os.close(std)
    data = log.read_bytes()
    assert b'\0' not in data
    assert b'RAW\n' in data


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


_REDIRECTOR = ("import subprocess, sys; "
               "sys.exit(subprocess.call([sys.executable] + sys.argv[1:]))")


@pytest.mark.parametrize('via_redirector', [False, True],
                         ids=['direct', 'via-venv-redirector'])
def test_restart_child_takes_over_an_inherited_log_handle(tmp_path, via_redirector):
    """The real path: the parent opens clayrune.log 'ab' and hands it to the
    child as stdout/stderr (_redirect_restart_child_output), then exits a moment
    later. The child must release that handle, own the file, keep the pre-bound
    file, rotate, and still capture output from a grandchild and a raw fd write.

    `via-venv-redirector` is how every Windows launch actually runs:
    .venv/Scripts/python.exe starts the base interpreter as a child and keeps
    the handed-in handle open until it exits. The wrapper does the same on any
    interpreter, so the case does not depend on how pytest itself was started
    (the shipped suite passed only under the base interpreter)."""
    log = tmp_path / 'clayrune.log'
    log.write_bytes(b'legacy\n' * 4000)         # 28000 bytes, over the 20000 bound
    go = tmp_path / 'go'
    script = tmp_path / 'child.py'
    script.write_text(_CHILD.format(repo=str(REPO), log=str(log), go=str(go)),
                      encoding='utf-8')
    handed = open(log, 'ab')
    try:
        argv = [sys.executable, str(script)]
        if via_redirector:
            argv = [sys.executable, '-c', _REDIRECTOR, str(script)]
        p = subprocess.Popen(argv, stdout=handed, stderr=subprocess.STDOUT)
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
    assert boot.index("_boot_phase('port-conflict wait'")         < boot.index('_server_log_rotation.install_std_logs(')         < boot.index('ensure_exec_token(PORT)')


# -- install_std_logs(): whatever file under the log dir stdout/stderr are -----

_STD_CHILD = textwrap.dedent('''
    import os, sys, time
    sys.path.insert(0, {repo!r})
    from mc import server_log_rotation as slr
    slr.RETRY_SECONDS = 0.1
    made = slr.install_std_logs({logdir!r}, max_bytes=20000, generations=2)
    print('installed', len(made), flush=True)
    while not os.path.exists({go!r}):          # the launcher has let go
        time.sleep(0.05)
    time.sleep(0.2)
    for i in range(400):
        print('out line %04d ' % i + 'x' * 80)
        sys.stderr.write('err line %04d ' % i + 'y' * 80 + '\\n')
        if i == 200:
            os.write(1, b'RAW-FD1\\n')
            os.write(2, b'RAW-FD2\\n')
    print('done', flush=True)
''')


def _run_std_child(tmp_path, out_name, err_name, via_redirector=False,
                   marker='installed '):
    """Run the child with stdout/stderr redirected to `out_name`/`err_name`
    (paths relative to tmp_path), the way tools/clayrune-autostart.ps1's
    Start-Process -RedirectStandardOutput/-RedirectStandardError does; the
    launcher's handles stay open until the child reports in. Returns the log
    dir."""
    logdir = tmp_path / 'logs'
    logdir.mkdir(exist_ok=True)
    go = tmp_path / 'go'
    script = tmp_path / 'child.py'
    script.write_text(_STD_CHILD.format(repo=str(REPO), logdir=str(logdir),
                                        go=str(go)), encoding='utf-8')
    out_p, err_p = tmp_path / out_name, tmp_path / err_name
    h_out = open(out_p, 'ab')
    h_err = h_out if err_p == out_p else open(err_p, 'ab')
    try:
        argv = [sys.executable, str(script)]
        if via_redirector:
            argv = [sys.executable, '-c', _REDIRECTOR, str(script)]
        p = subprocess.Popen(argv, stdout=h_out, stderr=h_err)
        deadline = time.time() + 30
        while marker not in out_p.read_bytes().decode('utf-8', 'replace'):
            assert time.time() < deadline and p.poll() is None
            time.sleep(0.05)
        h_out.close()
        h_err.close()                            # the launcher exits
        go.write_text('1')
        assert p.wait(timeout=60) == 0
    finally:
        h_out.close()
        h_err.close()
    return logdir


def _names(d):
    return {q.name: _read(q) for q in Path(d).iterdir()}


def test_install_std_logs_is_a_no_op_when_stdout_is_not_under_the_dir(tmp_path):
    before = (sys.stdout, sys.stderr)
    assert slr.install_std_logs(tmp_path) == []
    assert (sys.stdout, sys.stderr) == before
    assert list(tmp_path.iterdir()) == []         # did not create anything either
    assert slr.install_std_logs(tmp_path / 'no-such-dir') == []


@pytest.mark.parametrize('via_redirector', [False, True],
                         ids=['direct', 'via-venv-redirector'])
def test_autostart_style_out_and_err_files_are_each_bounded(tmp_path, via_redirector):
    """tools/clayrune-autostart.ps1 sends stdout and stderr to two DIFFERENT
    files under data/logs, truncated only at each launch. Both must be taken
    over and rotated, each on its own, and nothing may be lost."""
    (tmp_path / 'logs').mkdir()
    logdir = _run_std_child(tmp_path,
                            'logs/clayrune-autostart-server.log',
                            'logs/clayrune-autostart-server.err.log',
                            via_redirector=via_redirector)
    files = _names(logdir)
    out = {k: v for k, v in files.items() if '.err.' not in k}
    err = {k: v for k, v in files.items() if '.err.' in k}
    assert 'clayrune-autostart-server.log.1' in out                   # stdout rotated
    assert 'clayrune-autostart-server.err.log.1' in err               # stderr rotated
    assert set(out) <= {'clayrune-autostart-server.log',
                        'clayrune-autostart-server.log.1',
                        'clayrune-autostart-server.log.2'}
    assert set(err) <= {'clayrune-autostart-server.err.log',
                        'clayrune-autostart-server.err.log.1',
                        'clayrune-autostart-server.err.log.2'}
    for k, v in files.items():
        assert len(v.encode()) < 20000 + 400, k                       # each file bounded
    out_text, err_text = ''.join(out.values()), ''.join(err.values())
    for m in ('installed 2', 'RAW-FD1', 'done'):
        assert m in out_text, m
    assert 'RAW-FD2' in err_text
    assert 'err line' not in out_text and 'out line' not in err_text  # not mixed up


def test_one_file_behind_both_fds_gets_one_log(tmp_path):
    (tmp_path / 'logs').mkdir()
    logdir = _run_std_child(tmp_path, 'logs/clayrune.log', 'logs/clayrune.log',
                            marker='installed 1')
    files = _names(logdir)
    assert 'clayrune.log.1' in files                                  # it rotated
    combined = ''.join(files.values())
    # 'installed 1' (the marker _run_std_child waited for) has rotated out by now
    for m in ('out line 0399', 'err line 0399', 'RAW-FD1', 'RAW-FD2'):
        assert m in combined, m


def test_a_file_outside_the_log_dir_is_left_alone(tmp_path):
    """Only files directly under the log dir are bounded: stdout on a file
    elsewhere is never taken over, even when it is far over the bound."""
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'elsewhere.log').write_bytes(b'before\n' * 4000)      # 28000 > bound
    logdir = _run_std_child(tmp_path, 'elsewhere.log', 'elsewhere.log',
                            marker='installed 0')
    assert list(logdir.iterdir()) == []
    assert sorted(q.name for q in tmp_path.iterdir()) == [
        'child.py', 'elsewhere.log', 'go', 'logs']                    # no .1, no .pre-cap
    text = _read(tmp_path / 'elsewhere.log')
    assert text.startswith('before\n' * 4000) and 'done' in text      # grew unbounded
