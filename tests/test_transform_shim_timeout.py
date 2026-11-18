"""MC-1179 A2: a Claude transform timeout must hold through a shim wrapper.

On Windows the Claude binary resolves to an npm `.cmd` shim, so the child is
cmd.exe and the CLI is a grandchild. `subprocess.run(timeout=)` killed only the
shim and then waited, with no limit, for the grandchild to release the pipe: a
2 s timeout returned after the CLI's own 8 s. The tests below use a fake CLI
(a Python sleeper behind a real `.cmd` / `.sh` shim). No vendor CLI is launched.
"""
import os
import stat
import sys
import time

import pytest

from mc import agent_runtime as rt

SLEEP_SECONDS = 9
TIMEOUT = 1


def _running(pid):
    """True only while the process is really running.

    `rt._pid_is_alive` answers True for an exited process whose handle is still
    open somewhere (OpenProcess succeeds), so ask for the exit code instead.
    """
    if sys.platform == 'win32':
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@pytest.fixture
def fake_cli(tmp_path):
    """A shim whose grandchild records its PID, then sleeps."""
    pid_file = tmp_path / 'grandchild.pid'
    slow = tmp_path / 'slow.py'
    slow.write_text(
        'import os, sys, time\n'
        f'open({str(pid_file)!r}, "w").write(str(os.getpid()))\n'
        'import json\n'
        'print(json.dumps({"type": "assistant", "message": {"role": "assistant", '
        '"content": [{"type": "text", "text": "early"}]}}), flush=True)\n'
        f'time.sleep({SLEEP_SECONDS})\n', encoding='utf-8')
    if sys.platform == 'win32':
        shim = tmp_path / 'fake-cli.cmd'
        shim.write_text(f'@echo off\r\n"{sys.executable}" "{slow}"\r\n', encoding='utf-8')
    else:
        shim = tmp_path / 'fake-cli.sh'
        shim.write_text(f'#!/bin/sh\n"{sys.executable}" "{slow}"\n', encoding='utf-8')
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    yield shim, pid_file
    # Only the PID our own fake CLI wrote, never by image name.
    if pid_file.exists() and pid_file.read_text().strip().isdigit():
        pid = int(pid_file.read_text().strip())
        if _running(pid):
            rt._kill_pid(pid)


def _gone(pid_file, seconds=4.0):
    deadline = time.monotonic() + seconds
    pid = None
    while time.monotonic() < deadline:
        if pid is None and pid_file.exists() and pid_file.read_text().strip().isdigit():
            pid = int(pid_file.read_text().strip())
        if pid is not None and not _running(pid):
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def runtime(monkeypatch, tmp_path, fake_cli):
    shim, _ = fake_cli
    r = rt.ClaudeRuntime()
    monkeypatch.setattr(r, '_oneshot_argv', lambda **kw: [str(shim)])
    monkeypatch.setattr(r, '_stream_argv', lambda **kw: [str(shim)])
    monkeypatch.setattr(rt._transform_sandbox, 'neutral_cwd', lambda: str(tmp_path))
    monkeypatch.setattr(rt._api_credits_offload, 'try_offload', lambda **kw: None)
    monkeypatch.setattr(rt, '_authorize_text_transform', lambda *a, **kw: None)
    return r


def test_oneshot_timeout_returns_on_time_and_ends_the_cli(runtime, fake_cli):
    _, pid_file = fake_cli
    start = time.monotonic()
    assert runtime.oneshot(prompt='x', timeout=TIMEOUT) is None
    elapsed = time.monotonic() - start
    assert runtime.last_error.startswith(f'timeout after {TIMEOUT}')
    assert elapsed < TIMEOUT + 3, f'oneshot blocked {elapsed:.1f}s on a {TIMEOUT}s timeout'
    assert _gone(pid_file), 'the timed-out CLI grandchild is still running'


def test_describe_image_timeout_returns_on_time_and_ends_the_cli(runtime, fake_cli, tmp_path):
    _, pid_file = fake_cli
    img = tmp_path / 'a.png'
    img.write_bytes(b'\x89PNG')
    start = time.monotonic()
    assert runtime.describe_image(str(img), prompt='what', timeout=TIMEOUT) is None
    elapsed = time.monotonic() - start
    assert runtime.last_error.startswith(f'timeout after {TIMEOUT}')
    assert elapsed < TIMEOUT + 3, f'describe_image blocked {elapsed:.1f}s on a {TIMEOUT}s timeout'
    assert _gone(pid_file), 'the timed-out CLI grandchild is still running'


def test_stream_text_total_timeout_raises_and_ends_the_cli(runtime, fake_cli):
    _, pid_file = fake_cli
    start = time.monotonic()
    with pytest.raises(rt.TransformTimeout):
        list(runtime.stream_text(prompt='x', timeout=TIMEOUT))
    elapsed = time.monotonic() - start
    assert elapsed < TIMEOUT + 3, f'stream_text blocked {elapsed:.1f}s on a {TIMEOUT}s timeout'
    assert _gone(pid_file), 'the timed-out CLI grandchild is still running'


def test_stream_text_close_before_the_end_ends_the_cli(runtime, fake_cli):
    """A client disconnect closes the generator; the CLI must not outlive it."""
    _, pid_file = fake_cli
    gen = runtime.stream_text(prompt='x', timeout=60)
    assert next(gen) == 'early'
    gen.close()
    assert _gone(pid_file), 'the CLI outlived a closed stream_text generator'


def test_stream_text_does_not_deadlock_on_a_chatty_stderr(monkeypatch, tmp_path):
    """Nothing drained stderr while stdout was read: a CLI that fills the
    stderr pipe before it answers blocked both sides forever."""
    noisy = tmp_path / 'noisy.py'
    noisy.write_text(
        'import json, sys\n'
        'sys.stderr.write("e" * 2_000_000)\n'
        'sys.stderr.flush()\n'
        'print(json.dumps({"type": "assistant", "message": {"role": "assistant", '
        '"content": [{"type": "text", "text": "ANSWER"}]}}))\n', encoding='utf-8')
    r = rt.ClaudeRuntime()
    monkeypatch.setattr(r, '_stream_argv', lambda **kw: [sys.executable, str(noisy)])
    monkeypatch.setattr(rt._transform_sandbox, 'neutral_cwd', lambda: str(tmp_path))
    out = []
    t_err = []

    import threading

    def consume():
        try:
            out.extend(r.stream_text(prompt='x', timeout=30))
        except Exception as e:  # noqa: BLE001
            t_err.append(e)

    t = threading.Thread(target=consume, daemon=True)
    t.start()
    t.join(timeout=15)
    assert not t.is_alive(), 'stream_text deadlocked on a full stderr pipe'
    assert not t_err, t_err
    assert out == ['ANSWER']


def test_stream_text_failure_reports_the_drained_stderr(monkeypatch, tmp_path):
    """The stderr thread keeps the tail the error message is built from."""
    failing = tmp_path / 'failing.py'
    failing.write_text('import sys\nsys.stderr.write("auth expired\n")\nsys.exit(3)\n',
                       encoding='utf-8')
    r = rt.ClaudeRuntime()
    monkeypatch.setattr(r, '_stream_argv', lambda **kw: [sys.executable, str(failing)])
    monkeypatch.setattr(rt._transform_sandbox, 'neutral_cwd', lambda: str(tmp_path))
    with pytest.raises(rt.TransformFailure, match='auth expired'):
        list(r.stream_text(prompt='x', timeout=30))
