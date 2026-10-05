"""The only door out of the temporary browser pane used by unknown-service discovery.

The pane's Chromium is launched with `--proxy-server` pointing here and loopback
removed from its bypass list, so EVERY request it makes (the first navigation, each
redirect, every subresource, a websocket, a page script reaching for 127.0.0.1) arrives
as a `CONNECT host:port` and is judged by `net_guard` BEFORE a socket is opened:

  * only `CONNECT` to port 443 is served; anything else is answered 403;
  * the host name must be a public service name and EVERY address it resolves to must
    be global (`net_guard.resolve_public`), otherwise 403 and the refusal is recorded;
  * the proxy connects to one of the addresses it just vetted, never to the name again,
    so a DNS record that changes between the check and the connect (rebinding) has
    nothing to act on: the name is resolved once, here, and Chromium never resolves it.

A page's own "Is this one of ours?" question is answered by the same function that
guards the typed address, so the pane cannot be walked to a private service by a
redirect the typed address would have been refused for. The proxy is read-only plumbing:
it never inspects, stores or alters traffic, and caps what one discovery can pull
(`max_bytes` across all tunnels, `max_tunnels` at once). `close()` drops every open
socket; discovery always calls it.

Refusals are kept (`refused`) so the reader can tell "the page was blocked by Clayrune"
from "the site is down", and say which.
"""
from __future__ import annotations

import socket
import threading

from mc.core import _log
from mc.desk_connect import net_guard

_HEAD_MAX = 8192
_REFUSED_KEEP = 20


class GuardProxy:
    def __init__(self, *, own_hosts=(), resolver=None, connector=None,
                 max_tunnels: int = 64, max_bytes: int = 30_000_000,
                 connect_timeout: float = 5.0, idle_timeout: float = 10.0):
        self.own_hosts = tuple(h for h in own_hosts if h)
        self._resolver = resolver
        self._connector = connector or (lambda addr, port, timeout: socket.create_connection((addr, port), timeout))
        self.max_tunnels = max_tunnels
        self.max_bytes = max_bytes
        self.connect_timeout = connect_timeout
        self.idle_timeout = idle_timeout
        self.refused: list[dict] = []
        self.served: list[str] = []            # host names a tunnel was opened to
        self._lock = threading.Lock()
        self._socks: set = set()
        self._bytes = 0
        self._tunnels = 0
        self._closed = False
        self._srv: socket.socket | None = None
        self.port = 0

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(self) -> int:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(('127.0.0.1', 0))
        srv.listen(64)
        self._srv = srv
        self.port = srv.getsockname()[1]
        threading.Thread(target=self._accept_loop, name='desk-guard-proxy', daemon=True).start()
        return self.port

    def close(self) -> None:
        with self._lock:
            self._closed = True
            socks = list(self._socks)
            self._socks.clear()
        for s in [self._srv, *socks]:
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass

    def _track(self, s) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._socks.add(s)
            return True

    def _untrack(self, s) -> None:
        with self._lock:
            self._socks.discard(s)
        try:
            s.close()
        except OSError:
            pass

    # ── serving ────────────────────────────────────────────────────────────
    def _accept_loop(self) -> None:
        while True:
            try:
                conn, _ = self._srv.accept()          # type: ignore[union-attr]
            except OSError:
                return
            if not self._track(conn):
                conn.close()
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _refuse(self, conn, host: str, code: str, message: str, status: str = '403 Forbidden') -> None:
        with self._lock:
            if len(self.refused) < _REFUSED_KEEP and not any(r['host'] == host and r['code'] == code for r in self.refused):
                self.refused.append({'host': host, 'code': code, 'message': message})
        try:
            conn.sendall(f'HTTP/1.1 {status}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n'.encode('ascii'))
        except OSError:
            pass

    @staticmethod
    def _read_head(conn) -> tuple[bytes, bytes] | None:
        conn.settimeout(5.0)
        buf = b''
        while b'\r\n\r\n' not in buf:
            if len(buf) > _HEAD_MAX:
                return None
            try:
                chunk = conn.recv(2048)
            except OSError:
                return None
            if not chunk:
                return None
            buf += chunk
        head, _, rest = buf.partition(b'\r\n\r\n')
        return head, rest

    @staticmethod
    def _split_target(target: str) -> tuple[str, int | None]:
        if target.startswith('['):                      # [v6]:port
            host, _, tail = target[1:].partition(']')
            port = tail[1:] if tail.startswith(':') else ''
        else:
            host, _, port = target.rpartition(':')
            if not host:
                host, port = target, ''
        try:
            return host.lower().rstrip('.'), int(port) if port else None
        except ValueError:
            return host.lower(), None

    def _serve(self, conn) -> None:
        upstream = None
        try:
            got = self._read_head(conn)
            if got is None:
                return
            head, rest = got
            parts = head.split(b'\r\n', 1)[0].decode('latin-1', 'replace').split()
            if len(parts) < 2 or parts[0].upper() != 'CONNECT':
                self._refuse(conn, '', 'method', 'Only secure (https) connections are allowed.', '405 Method Not Allowed')
                return
            host, port = self._split_target(parts[1])
            if port != 443:
                self._refuse(conn, host, 'bad_port', 'Only the standard https port is allowed.')
                return
            with self._lock:
                if self._tunnels >= self.max_tunnels:
                    over = True
                else:
                    over = False
                    self._tunnels += 1
            if over:
                self._refuse(conn, host, 'too_many', 'Too many connections for one lookup.', '429 Too Many Requests')
                return
            try:
                addrs = net_guard.resolve_public(host, self.own_hosts, self._resolver)
            except net_guard.Blocked as e:
                self._refuse(conn, host, e.code, str(e))
                return
            for addr in addrs:                          # every one already vetted
                try:
                    upstream = self._connector(addr, 443, self.connect_timeout)
                    break
                except OSError:
                    continue
            if upstream is None:
                self._refuse(conn, host, 'unreachable', 'The service did not answer.', '502 Bad Gateway')
                return
            if not self._track(upstream):
                return
            with self._lock:
                self.served.append(host)
            conn.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            if rest:
                upstream.sendall(rest)
            self._pump(conn, upstream)
        except OSError:
            pass
        except Exception as e:                           # a proxy bug must not become a hang
            _log(f'[desk_connect] guard proxy error: {type(e).__name__}', flush=True)
        finally:
            if upstream is not None:
                self._untrack(upstream)
            self._untrack(conn)

    def _pump(self, a, b) -> None:
        a.settimeout(self.idle_timeout)
        b.settimeout(self.idle_timeout)

        def copy(src, dst):
            try:
                while True:
                    data = src.recv(16384)
                    if not data:
                        break
                    with self._lock:
                        self._bytes += len(data)
                        if self._bytes > self.max_bytes:
                            break
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                for s in (src, dst):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        t = threading.Thread(target=copy, args=(b, a), daemon=True)
        t.start()
        copy(a, b)
        t.join(timeout=self.idle_timeout + 1)
