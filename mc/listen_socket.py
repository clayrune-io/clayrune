"""The main server's listening socket, built so a second Clayrune cannot share it.

Why this exists (2026-10-06): after a reboot two servers were both LISTENING on
5199 — one from the logon task (start.bat), one from the S4U boot task. Both
single-instance guards (a TCP probe, `_check_port_conflict`) are check-then-bind,
so two processes starting 14s apart can both pass before either has bound. The
bind itself was the only thing that could have stopped the loser, and it did not,
because the socket set SO_REUSEADDR. **On Windows SO_REUSEADDR means "another
socket that also set it may bind this exact port and share it"** (POSIX means
"skip TIME_WAIT" — the two are different options that share a name). Measured
2026-10-06 on this box, one port, two AF_INET6 sockets:

    reuse + reuse          -> both bind (the bug)
    plain  vs  reuse       -> WSAEADDRINUSE 10048
    reuse  vs  exclusive   -> WSAEACCES     10013
    exclusive after the previous listener closed with a connection in
    TIME_WAIT                -> binds fine

so on Windows the listener sets SO_EXCLUSIVEADDRUSE and leaves SO_REUSEADDR off.
POSIX keeps SO_REUSEADDR: there it only skips TIME_WAIT (a restart needs it) and
cannot make two live listeners share a port.

`mc/desk_oauth.py` already does the equivalent for the OAuth loopback port.
"""
from __future__ import annotations

import errno
import socket
import sys
import time

# Windows reports "port taken" as WSAEADDRINUSE (10048) — or WSAEACCES (10013)
# when the holder is exclusive and we asked to share. Both mean "somebody else".
_WSAEACCES = 10013
_IN_USE = {errno.EADDRINUSE, _WSAEACCES, 10048}

# How long a bind may keep retrying while NOTHING answers on the port: the old
# process of a restart is still closing its socket. Matches the restart wait in
# server._check_port_conflict (15s).
RETRY_WINDOW_S = 15.0
_RETRY_STEP_S = 0.3


class PortInUse(OSError):
    """The port is held by another socket (exclusive bind refused)."""


def port_answers(port: int) -> bool:
    """True if something accepts a TCP connection on `port` over loopback.

    A connect probe cannot be fooled the way a bind probe can: if anything
    accepts, the port is taken by a live listener, not a socket still closing.
    """
    for fam, addr in ((socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')):
        s = socket.socket(fam, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            s.connect((addr, port))
            return True
        except OSError:
            pass
        finally:
            s.close()
    return False


def _new_socket(family: int, *, v6only: bool | None, shared: bool) -> socket.socket:
    sock = socket.socket(family, socket.SOCK_STREAM)
    if sys.platform == 'win32' and not shared:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        # POSIX: skip TIME_WAIT so a restart can rebind. Windows + shared: the
        # explicit MC_ALLOW_PORT_CONFLICT=1 opt-in to the old shared bind.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if v6only is not None:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1 if v6only else 0)
    return sock


def bind_listener(family: int, host: str, port: int, *, v6only: bool | None = None,
                  shared: bool = False, backlog: int = 128,
                  retry_window_s: float = RETRY_WINDOW_S,
                  _clock=time) -> socket.socket:
    """Return a bound, listening socket; raise PortInUse if the port stays taken.

    A refused bind is retried only while nothing ANSWERS on the port — that is
    the previous instance of a restart still releasing it, and it clears in a
    second or two. The moment a connect succeeds a live server holds the port,
    so we fail at once instead of waiting out the window (the loser of a boot
    race must exit promptly, not hang for 15s).

    `shared=True` restores the old Windows semantics (MC_ALLOW_PORT_CONFLICT=1).
    """
    deadline = _clock.time() + retry_window_s
    while True:
        sock = _new_socket(family, v6only=v6only, shared=shared)
        try:
            sock.bind((host, port))
            sock.listen(backlog)
            return sock
        except OSError as e:
            sock.close()
            if e.errno not in _IN_USE and getattr(e, 'winerror', None) not in _IN_USE:
                raise
            if port_answers(port) or _clock.time() >= deadline:
                raise PortInUse(e.errno, f"port {port} is already in use: {e.strerror}") from e
        _clock.sleep(_RETRY_STEP_S)
