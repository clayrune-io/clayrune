"""ce1292a3: on Linux/macOS the restarted server died as soon as the terminal it
was launched from went away.

start.sh / start.command `exec python server.py` inside a terminal (the Linux
.desktop file has Terminal=true). `_perform_server_restart_async` spawned the
replacement with start_new_session=True but left stdin/stdout/stderr inherited,
so the child wrote to the old terminal's pty. The old process exits right after
spawning, the terminal closes, the pty master is gone, and every write from the
child fails with EIO -- the new server died on its first log line. Measured on
WSL Ubuntu and macOS 26 with _scratch/pty_child_probe.py (no signal involved:
the child is in its own session, so there is no SIGHUP; it is the EIO).

The fix points the child's stdin at /dev/null and its output at data/logs, like
the Windows windowless branch always did. Nothing here calls the restart route.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from mc.blueprints import system_routes as sr


class _InlineThread:
    def __init__(self, target=None, daemon=None, **_):
        self._target = target

    def start(self):
        self._target()


def _capture_restart_popen(monkeypatch, tmp_path, platform):
    """Run _perform_server_restart_async inline; return the kwargs it gave Popen."""
    seen = {}

    def fake_popen(args, **kw):
        seen['args'], seen['kw'] = args, kw
        return object()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sr.sys, 'platform', platform)
    monkeypatch.setattr(sr.subprocess, 'Popen', fake_popen)
    monkeypatch.setattr(sr.threading, 'Thread', _InlineThread)
    monkeypatch.setattr(sr._time, 'sleep', lambda *_: None)
    monkeypatch.setattr(sr.os, '_exit', lambda *_: None)
    monkeypatch.setattr(sr, '_stop_all_sessions_for_restart', lambda: None)
    monkeypatch.setattr(sr, '_append_restart_log', lambda *_: None)
    sr._perform_server_restart_async({})
    return seen['kw']


@pytest.mark.parametrize('platform', ['linux', 'darwin'])
def test_posix_restart_child_never_inherits_the_terminal(monkeypatch, tmp_path, platform):
    kw = _capture_restart_popen(monkeypatch, tmp_path, platform)
    assert kw['start_new_session'] is True
    assert kw['stdin'] == subprocess.DEVNULL
    out = kw['stdout']
    assert out.name == str(tmp_path / 'data' / 'logs' / 'clayrune.log')
    assert kw['stderr'] == subprocess.STDOUT


def test_posix_restart_falls_back_to_per_instance_log(monkeypatch, tmp_path):
    (tmp_path / 'data' / 'logs' / 'clayrune.log').mkdir(parents=True)  # unopenable
    kw = _capture_restart_popen(monkeypatch, tmp_path, 'linux')
    assert kw['stdout'].name == str(
        tmp_path / 'data' / 'logs' / f'clayrune-restart-{os.getpid()}.log')


def test_posix_restart_discards_rather_than_inherits_when_no_log(monkeypatch, tmp_path):
    (tmp_path / 'data').write_text('not a dir')  # makedirs(data/logs) fails
    kw = _capture_restart_popen(monkeypatch, tmp_path, 'linux')
    assert kw['stdout'] == subprocess.DEVNULL and kw['stdin'] == subprocess.DEVNULL


@pytest.mark.skipif(sys.platform == 'win32', reason='pty is POSIX-only')
def test_child_with_redirected_io_survives_terminal_close(tmp_path):
    """Control + fix against a real pty: inherited fds die with EIO, the
    redirected ones the restart branch now builds do not."""
    import pty
    code = 'import sys; print("boot", flush=True); sys.stderr.write("err" + chr(10))'

    def run(redirect):
        master, slave = pty.openpty()
        kw = {'start_new_session': True, 'close_fds': True}
        if redirect:
            popen_kw = {}
            old = os.getcwd()
            os.chdir(tmp_path)
            try:
                sr._redirect_restart_child_output(popen_kw)
            finally:
                os.chdir(old)
            kw.update(popen_kw, stdin=subprocess.DEVNULL)
        else:
            kw.update(stdin=slave, stdout=slave, stderr=slave)
        p = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(1.0)\n' + code], **kw)
        os.close(slave)
        os.close(master)  # the terminal goes away
        return p.wait(timeout=20)

    assert run(redirect=False) != 0          # the bug: EIO -> python exits 120
    assert run(redirect=True) == 0           # the fix
    assert b'boot' in (tmp_path / 'data' / 'logs' / 'clayrune.log').read_bytes()
