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
import threading
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
                  wait_for_holder: bool = False,
                  _clock=time) -> socket.socket:
    """Return a bound, listening socket; raise PortInUse if the port stays taken.

    A refused bind is retried only while nothing ANSWERS on the port — that is
    the previous instance of a restart still releasing it, and it clears in a
    second or two. The moment a connect succeeds a live server holds the port,
    so we fail at once instead of waiting out the window (the loser of a boot
    race must exit promptly, not hang for 15s).

    `wait_for_holder=True` is for a server restart (MC_RESTART_FROM_PID): the
    holder IS expected to still answer — it is the parent we just replaced and
    it is on its way out — so keep retrying for the whole window instead of
    failing on the first answer. Still bounded: a parent that never leaves is a
    conflict.

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
            if (not wait_for_holder and port_answers(port)) or _clock.time() >= deadline:
                raise PortInUse(e.errno, f"port {port} is already in use: {e.strerror}") from e
        _clock.sleep(_RETRY_STEP_S)


class Reserved:
    """Listening sockets reserved for the server, not yet serving.

    The point of reserving them before `boot()` (MC-1060): the bind is the only
    single-instance check that cannot be raced, so it has to be the FIRST thing
    a starting server does. Done after boot, the loser of a boot race had
    already reconciled the agent log, adopted or marked stale the winner's
    runs, delivered callbacks and rewritten guardrail hooks before its bind
    failed. The sockets are already listening (clients that connect during boot
    queue in the backlog and are answered once `serve` starts).
    """

    def __init__(self, port: int, listeners: list[tuple[str, socket.socket]]):
        self.port = port
        # (host, socket); the LAST one is served on the calling thread.
        self.listeners = listeners

    def close(self) -> None:
        for _host, sock in self.listeners:
            try:
                sock.close()
            except OSError:
                pass

    def serve(self, app, *, log=print) -> None:
        """Serve `app` from the reserved sockets; blocks on the last one."""
        from werkzeug.serving import make_server
        *rest, (host, sock) = self.listeners
        for h, s in rest:
            try:
                srv = make_server(h, self.port, app, threaded=True, fd=s.fileno())
                threading.Thread(target=srv.serve_forever, daemon=True,
                                 name='serve-loopback-v6').start()
            except OSError as e:
                log(f"[serve] {h} bind unavailable ({e}); serving {host} only.")
        make_server(host, self.port, app, threaded=True, fd=sock.fileno()).serve_forever()


def reserve(port: int, *, loopback_only: bool = False, shared: bool = False,
            wait_for_holder: bool = False, retry_window_s: float = RETRY_WINDOW_S,
            log=print) -> Reserved:
    """Bind the server's listening socket(s) now; raise PortInUse if refused.

    Same three modes the server always had:

    - default: ONE dual-stack AF_INET6 socket on `::` (IPV6_V6ONLY=0), so
      `localhost` -> ::1 and 127.0.0.1 are both served without the ~200ms
      Happy-Eyeballs fallback per connection. If the host has no IPv6 at all,
      fall back to IPv4-only on 0.0.0.0.
    - `loopback_only` (MC_BIND_LOOPBACK=1): ::1 and 127.0.0.1 each get a socket;
      if ::1 is unavailable, 127.0.0.1 alone.
    - `shared` (MC_ALLOW_PORT_CONFLICT=1): the old shared bind on Windows.

    A PortInUse is never swallowed by the fallbacks: a taken port is a taken
    port on every address family.
    """
    def bind(family: int, host: str, *, v6only: bool | None = None) -> socket.socket:
        return bind_listener(family, host, port, v6only=v6only, shared=shared,
                             wait_for_holder=wait_for_holder, retry_window_s=retry_window_s)

    if loopback_only:
        listeners: list[tuple[str, socket.socket]] = []
        try:
            listeners.append(('::1', bind(socket.AF_INET6, '::1', v6only=True)))
        except PortInUse:
            raise
        except OSError as e:
            log(f"[serve] ::1 bind unavailable ({e}); serving 127.0.0.1 only.")
        try:
            listeners.append(('127.0.0.1', bind(socket.AF_INET, '127.0.0.1')))
        except BaseException:
            Reserved(port, listeners).close()
            raise
        return Reserved(port, listeners)
    try:
        return Reserved(port, [('::', bind(socket.AF_INET6, '::', v6only=False))])
    except PortInUse:
        raise
    except OSError as e:
        log(f"[serve] dual-stack bind unavailable ({e}); falling back to IPv4-only. "
            f"http://localhost:{port} will be ~200ms/request slower than "
            f"http://127.0.0.1:{port}.")
    return Reserved(port, [('0.0.0.0', bind(socket.AF_INET, '0.0.0.0'))])
