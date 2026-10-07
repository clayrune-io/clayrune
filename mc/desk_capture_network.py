"""Screenshot network boundary: public addresses or the exact user-chosen origin.

All Chromium traffic goes through this proxy, including redirects/subresources.
DNS is resolved once per socket and the connection uses the vetted IP, never a
second lookup. A private exception is host AND port, not the entire network.
"""
from __future__ import annotations

import socket
from urllib.parse import urlsplit

from mc.core import _log
from mc.desk_connect import net_guard
from mc.desk_connect.guard_proxy import GuardProxy


def address(raw) -> str:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 2000:
        raise ValueError('Enter the app address, starting with http:// or https://.')
    raw = raw.strip()
    try:
        u = urlsplit(raw)
        if (u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password
                or any(ord(c) < 33 for c in raw) or '\\' in raw):
            raise ValueError()
        _ = u.port
    except ValueError:
        raise ValueError('Use an http:// or https:// app address without a username or password.') from None
    return raw


def origin(raw: str) -> tuple[str, int]:
    u = urlsplit(address(raw))
    return str(u.hostname).lower().rstrip('.'), u.port or (443 if u.scheme == 'https' else 80)


class CaptureProxy(GuardProxy):
    def __init__(self, *, allowed=(), **kw):
        super().__init__(**kw)
        self.allowed = frozenset(allowed)

    def addresses(self, host: str, port: int) -> list[str]:
        try:
            rows = (self._resolver or socket.getaddrinfo)(host, port, type=socket.SOCK_STREAM)
        except OSError:
            raise net_guard.Blocked('dns_failed', 'The app address could not be found.') from None
        addrs = list(dict.fromkeys(str(r[4][0]) for r in rows))
        if not addrs:
            raise net_guard.Blocked('no_address', 'The app address has no network address.')
        if (host.lower().rstrip('.'), port) not in self.allowed and not all(net_guard.is_public_ip(a) for a in addrs):
            raise net_guard.Blocked('private_address', 'Enter this local app address yourself before capturing it.')
        return addrs

    def _serve(self, conn) -> None:
        upstream = None
        try:
            got = self._read_head(conn)
            if got is None:
                return
            head, rest = got
            first, _, headers = head.partition(b'\r\n')
            parts = first.decode('latin-1').split()
            if len(parts) != 3:
                self._refuse(conn, '', 'bad_request', 'Invalid capture request.')
                return
            method, target, version = parts
            tunnel = method == 'CONNECT'
            if tunnel:
                host, port = self._split_target(target)
            else:
                u = urlsplit(address(target))
                if u.scheme != 'http' or method not in ('GET', 'HEAD'):
                    self._refuse(conn, '', 'method', 'Capture only reads web pages.')
                    return
                host, port = origin(target)
            if not host or not port or not 1 <= port <= 65535:
                self._refuse(conn, host, 'bad_port', 'Invalid app port.')
                return
            try:
                addrs = self.addresses(host, port)
            except net_guard.Blocked as e:
                self._refuse(conn, host, e.code, str(e))
                return
            with self._lock:
                self._tunnels += 1
                over = self._tunnels > self.max_tunnels
            if over:
                self._refuse(conn, host, 'too_many', 'Too many requests for one capture.')
                return
            for ip in addrs:
                try:
                    upstream = self._connector(ip, port, self.connect_timeout)
                    break
                except OSError:
                    continue
            if upstream is None:
                self._refuse(conn, host, 'unreachable', 'The app did not answer.', '502 Bad Gateway')
                return
            if not self._track(upstream):
                return
            if tunnel:
                conn.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            else:
                # One HTTP request per connection; a subsequent absolute target
                # must return through this boundary and be checked again.
                path = u.path or '/'
                if u.query:
                    path += '?' + u.query
                safe_headers = [h for h in headers.split(b'\r\n') if h.split(b':', 1)[0].lower()
                                not in (b'connection', b'proxy-connection', b'proxy-authorization', b'host')]
                authority = u.netloc.encode('ascii')
                upstream.sendall(f'{method} {path} {version}\r\n'.encode('ascii') + b'\r\n'.join(safe_headers)
                                 + b'\r\nHost: ' + authority + b'\r\nConnection: close\r\n\r\n')
            if rest and tunnel:
                upstream.sendall(rest)
            self._pump(conn, upstream)
        except Exception as e:
            _log(f'[desk_capture] proxy request failed: {type(e).__name__}', flush=True)
        finally:
            if upstream is not None:
                self._untrack(upstream)
            self._untrack(conn)
