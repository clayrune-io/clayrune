"""One-use anonymous pipe for an already-unlocked server's replacement.

Only the pipe descriptor/handle travels in the environment. The key is written
before spawning and never serialized to disk, argv, environment or diagnostics.
Fresh launchers do not call spawn_replacement and therefore have no channel.
"""
from __future__ import annotations

import os
import select
import stat
import subprocess
import sys
import time
from typing import Any

from mc.core import _log

CHANNEL_ENV = 'MC_VAULT_RESTART_PIPE'
TIMEOUT_SECONDS = 10.0
_pending_fd: int | None = None
_capture_error = ''
_handover_seen = False


def requires_passphrase_lock() -> bool:
    """A handover must never fall back to legacy key minting if wrapping vanished."""
    return _handover_seen


def capture_inherited_channel() -> None:
    """Entry-point hook: remove metadata and prevent any grandchild inheritance."""
    global _pending_fd, _capture_error, _handover_seen
    token = os.environ.pop(CHANNEL_ENV, None)
    if token is None:
        return
    _handover_seen = True
    try:
        value = int(token)
        if value < 3:
            raise ValueError('reserved descriptor')
        if sys.platform == 'win32':
            import ctypes
            import msvcrt
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetFileType.argtypes = [wintypes.HANDLE]
            kernel.GetFileType.restype = wintypes.DWORD
            if kernel.GetFileType(value) != 3:  # FILE_TYPE_PIPE
                raise ValueError('not a pipe')
            os.set_handle_inheritable(value, False)
            fd = msvcrt.open_osfhandle(value, os.O_RDONLY | os.O_BINARY)
        else:
            if not stat.S_ISFIFO(os.fstat(value).st_mode):
                raise ValueError('not a pipe')
            fd = value
        os.set_inheritable(fd, False)
        _pending_fd = fd
    except Exception as exc:
        # Never echo the token or arbitrary exception text (untrusted startup input).
        _capture_error = type(exc).__name__


def spawn_replacement(args: list[str], **kwargs: Any) -> subprocess.Popen:
    """Spawn only a server replacement (or its macOS swap helper).

    close_fds + pass_fds / STARTUPINFO handle_list limit inheritance to the
    read end. Its inheritable window exists only around CreateProcess.
    Channel setup failure still starts the child, locked.
    """
    from mc import secrets_store, state
    env = dict(kwargs.pop('env', os.environ))
    env.pop(CHANNEL_ENV, None)
    kwargs['env'] = env
    kwargs['close_fds'] = True
    read_fd = write_fd = None
    key = None
    try:
        if state.CONFIG.get('vault_keep_unlocked_on_restart', True) is not True:
            _log('[vault-restart] handover disabled; replacement starts locked')
        else:
            key = secrets_store.peek_passphrase_key()
            if key is None:
                _log('[vault-restart] parent locked or unconfigured; no handover')
            else:
                read_fd, write_fd = os.pipe()
                if len(key) != 32 or os.write(write_fd, key) != 32:
                    raise ValueError('invalid key frame')
                os.close(write_fd)
                write_fd = None
                if sys.platform == 'win32':
                    import msvcrt
                    handle = msvcrt.get_osfhandle(read_fd)
                    info = subprocess.STARTUPINFO()
                    info.lpAttributeList = {'handle_list': [handle]}
                    kwargs['startupinfo'] = info
                    env[CHANNEL_ENV] = str(handle)
                else:
                    kwargs['pass_fds'] = (read_fd,)
                    env[CHANNEL_ENV] = str(read_fd)
    except Exception as exc:
        _log(f'[vault-restart] channel setup failed ({type(exc).__name__}); child starts locked')
        env.pop(CHANNEL_ENV, None)
        kwargs.pop('pass_fds', None)
        kwargs.pop('startupinfo', None)
    finally:
        key = None
        if write_fd is not None:
            os.close(write_fd)
    try:
        if sys.platform == 'win32' and CHANNEL_ENV in env:
            try:
                os.set_handle_inheritable(int(env[CHANNEL_ENV]), True)
            except OSError:
                env.pop(CHANNEL_ENV, None)
                kwargs.pop('startupinfo', None)
                _log('[vault-restart] handle inheritance failed; child starts locked')
        return subprocess.Popen(args, **kwargs)
    finally:
        if read_fd is not None:
            os.close(read_fd)


def _read_key(fd: int, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    frame = bytearray()
    while len(frame) < 32:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('handover timeout')
        if sys.platform == 'win32':
            import ctypes
            import msvcrt
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            peek = kernel.PeekNamedPipe
            peek.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                             wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
            peek.restype = wintypes.BOOL
            available = wintypes.DWORD()
            if not peek(msvcrt.get_osfhandle(fd), None, 0, None,
                        ctypes.byref(available), None):
                raise EOFError('pipe closed')
            if not available.value:
                time.sleep(min(0.01, remaining))
                continue
        elif not select.select([fd], [], [], remaining)[0]:
            raise TimeoutError('handover timeout')
        chunk = os.read(fd, 33 - len(frame))
        if not chunk:
            raise EOFError('short key frame')
        frame.extend(chunk)
    if len(frame) != 32:
        raise ValueError('invalid key frame')
    return bytes(frame)


def consume_inherited_key() -> bool:
    """Boot hook, before jobs start: bounded read, verify, unlock, always close."""
    global _pending_fd, _capture_error, _handover_seen
    from mc import secrets_store, state
    capture_inherited_channel()
    fd, _pending_fd = _pending_fd, None
    error, _capture_error = _capture_error, ''
    _handover_seen = _handover_seen or fd is not None or bool(error)
    try:
        if error:
            _log(f'[vault-restart] invalid channel ({error}); vault stays locked')
            return False
        if fd is None:
            _log('[vault-restart] no inherited pipe; vault stays locked')
            return False
        if state.CONFIG.get('vault_keep_unlocked_on_restart', True) is not True:
            _log('[vault-restart] handover disabled; vault stays locked')
            return False
        secrets_store._unlock_from_restart(_read_key(fd, TIMEOUT_SECONDS))
        return True
    except Exception as exc:
        _log(f'[vault-restart] handover refused ({type(exc).__name__}); vault stays locked')
        return False
    finally:
        if fd is not None:
            os.close(fd)
