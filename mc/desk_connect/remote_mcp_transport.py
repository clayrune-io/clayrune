"""The one client for a user-chosen remote MCP server, Streamable HTTP and legacy SSE (docs/
DESK_SERVICE_PROFILES_SPEC.md section 6.3, slice U2d). Used by two callers and no others: the stdio
bridge a credentialed server is registered through (`remote_mcp_bridge`) and the human-started
connection check (`remote_mcp_check`). Standard library only.

What it will not do, because a header may hold a token:

    * follow a redirect. A 3xx is `TransportError('redirect')`, naming the new origin and nothing
      else; the request that carried the header is never repeated anywhere. Moving to the new
      address is a new Review.
    * talk to any origin but the approved one. The legacy SSE `endpoint` event may only name the
      approved scheme, host and port.
    * connect to an address nobody approved. The name is resolved ONCE per request, every address
      it resolves to must be globally routable unless the approval says local or private, and the
      socket is opened to that address (the certificate is checked against the NAME), so there is
      no second resolution to swap. This is the rule `net_guard` holds for discovery.
    * put a header value, or a response body, into an error message. Errors carry a code, a
      status number and a sentence of ours.

A server's own text (its name, its tool descriptions) is untrusted data: this module returns it
parsed and bounded, never interpreted.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
from urllib.parse import urljoin, urlsplit

from mc.desk_connect import net_guard

PROTOCOLS = ('streamable_http', 'sse')
MAX_MESSAGE = 4 * 1024 * 1024
MAX_LINE = 1024 * 1024
CONNECT_TIMEOUT_S = 10.0
IDLE_TIMEOUT_S = 120.0
ENDPOINT_WAIT_S = 15.0
CLIENT_NAME = 'clayrune-remote-mcp'

TOKEN_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,64}$")
# Headers the transport sets itself or that change how a request is framed or routed.
RESERVED_HEADERS = frozenset({'host', 'content-length', 'content-type', 'accept', 'mcp-session-id', 'mcp-protocol-version',
                              'transfer-encoding', 'connection', 'upgrade', 'te', 'trailer', 'keep-alive', 'expect',
                              'cookie', 'origin', 'referer', 'proxy-authorization', 'proxy-connection'})


class TransportError(Exception):
    """A refusal or failure. `code` is machine-readable, `str(e)` is a sentence for a person that
    never holds a header value or a response body; `status` is an HTTP status when there was one,
    `location` the new origin of a redirect."""

    def __init__(self, code: str, message: str, status: int | None = None, location: str | None = None):
        super().__init__(message)
        self.code, self.status, self.location = code, status, location


def check_headers(headers: dict) -> None:
    """Refuse a header the transport must own or that cannot be sent as written. Raises ValueError."""
    for k, v in headers.items():
        if not isinstance(k, str) or not TOKEN_NAME.match(k) or k.lower() in RESERVED_HEADERS:
            raise ValueError(f'"{str(k)[:40]}" cannot be sent as a credential header')
        if not isinstance(v, str) or not v or any(c in v for c in '\r\n\x00') or len(v) > 8192:
            raise ValueError('a header value is empty, too long or has a line break in it')


class Target:
    """The approved address, parsed once: scheme, lowercase host, port and the request path."""

    def __init__(self, url: str, allow_private: bool):
        p = urlsplit(url)
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.fragment:
            raise TransportError('bad_url', 'The server address is not a plain http or https address.')
        self.scheme, self.host = p.scheme, p.hostname.lower()
        self.port = p.port or (443 if p.scheme == 'https' else 80)
        self.path = (p.path or '/') + (f'?{p.query}' if p.query else '')
        self.url = url
        self.allow_private = allow_private

    @property
    def origin(self) -> str:
        return f'{self.scheme}://{self.host}:{self.port}'

    def same_origin(self, url: str) -> bool:
        try:
            p = urlsplit(url)
            port = p.port or (443 if p.scheme == 'https' else 80)
        except ValueError:
            return False
        return (p.scheme, (p.hostname or '').lower(), port) == (self.scheme, self.host, self.port)


def _addresses(target: Target, resolver=None) -> list[str]:
    """Every address the host resolves to. Refuses a name with ANY non-global address unless the
    approval was for a local or private target."""
    try:
        ipaddress.ip_address(target.host)
        addrs = [target.host]
    except ValueError:
        try:
            infos = (resolver or socket.getaddrinfo)(target.host, target.port, type=socket.SOCK_STREAM)
        except OSError as e:
            raise TransportError('dns_failed', 'The server name does not resolve.') from e
        addrs = list(dict.fromkeys(str(i[4][0]) for i in infos))
    if not addrs:
        raise TransportError('dns_failed', 'The server name does not resolve.')
    if not target.allow_private and not all(net_guard.is_public_ip(a) for a in addrs):
        raise TransportError('private_address', 'That server name points at a private or local address, which was not '
                                                'approved. Nothing was sent.')
    return addrs


class _Connection(http.client.HTTPConnection):
    """Opens its socket to the vetted address; TLS is checked against the approved NAME."""

    def __init__(self, target: Target, addr: str, timeout: float):
        super().__init__(target.host, target.port, timeout=timeout)
        self._addr, self._https = addr, target.scheme == 'https'

    def connect(self):
        wait = float(self.timeout or CONNECT_TIMEOUT_S)
        sock = socket.create_connection((self._addr, self.port), min(wait, CONNECT_TIMEOUT_S))
        if self._https:
            try:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
            except (ssl.SSLError, OSError):
                sock.close()
                raise
        sock.settimeout(wait)
        self.sock = sock


def _safe_location(value: str | None, target: Target) -> str:
    """The origin and path a redirect points at, with no query and no control characters."""
    try:
        p = urlsplit(urljoin(target.url, (value or '').strip()))
        return f'{p.scheme}://{p.hostname or ""}{":" + str(p.port) if p.port else ""}{p.path}'[:200]
    except ValueError:
        return 'an address that could not be read'


def _open(target: Target, method: str, url_path: str, headers: dict, body: bytes | None, timeout: float, resolver=None):
    """Send one request; returns `(connection, response)` for a 2xx. Raises TransportError."""
    addr = _addresses(target, resolver)[0]
    conn = _Connection(target, addr, timeout)
    try:
        conn.request(method, url_path, body=body, headers=headers)
        resp = conn.getresponse()
    except TransportError:
        conn.close()
        raise
    except (OSError, http.client.HTTPException) as e:
        conn.close()
        raise TransportError('connect_failed', f'Could not reach the server ({type(e).__name__}).') from e
    s = resp.status
    if 300 <= s < 400:
        loc = _safe_location(resp.getheader('Location'), target)
        conn.close()
        raise TransportError('redirect', f'The server answered with a redirect to {loc}. It was not followed and no '
                                         f'credential was sent there. Review the new address to use it.',
                             status=s, location=loc)
    if s in (401, 403):
        conn.close()
        raise TransportError('auth_rejected', f'The server rejected the credentials (HTTP {s}).', status=s)
    if s == 404:
        conn.close()
        raise TransportError('not_found', 'The server answered 404 for that address.', status=s)
    if s >= 400 or s < 200:
        conn.close()
        raise TransportError('http_error', f'The server answered HTTP {s}.', status=s)
    return conn, resp


def _read_limited(resp, limit: int = MAX_MESSAGE) -> bytes:
    data = resp.read(limit + 1)
    if len(data) > limit:
        raise TransportError('too_large', 'The server sent more data than one message may hold.')
    return data


def iter_sse(fp):
    """`(event, data)` for each complete server-sent event on a binary stream, bounded."""
    event, data, size = 'message', [], 0
    while True:
        try:
            line = fp.readline(MAX_LINE + 1)
        except (OSError, http.client.HTTPException, ValueError) as e:
            raise TransportError('stream_failed', f'The event stream broke ({type(e).__name__}).') from e
        if not line:
            if data:
                yield event, '\n'.join(data)
            return
        if len(line) > MAX_LINE:
            raise TransportError('too_large', 'The server sent a line that is too long.')
        text = line.decode('utf-8', 'replace').rstrip('\r\n')
        if text == '':
            if data:
                yield event, '\n'.join(data)
            event, data, size = 'message', [], 0
            continue
        if text.startswith(':'):
            continue
        name, _, value = text.partition(':')
        value = value[1:] if value.startswith(' ') else value
        if name == 'event':
            event = value[:64]
        elif name == 'data':
            data.append(value)
            size += len(value)
            if size > MAX_MESSAGE:
                raise TransportError('too_large', 'The server sent an event that is too large.')


def _dispatch(raw, on_message) -> None:
    try:
        doc = json.loads(raw)
    except ValueError as e:
        raise TransportError('bad_message', 'The server sent a message that is not JSON.') from e
    for m in (doc if isinstance(doc, list) else [doc]):
        if isinstance(m, dict):
            on_message(m)


class StreamableSession:
    """Streamable HTTP: one POST per client message; the answer is JSON or an event stream."""

    protocol = 'streamable_http'

    def __init__(self, target: Target, headers: dict, on_message, *, timeout: float = IDLE_TIMEOUT_S, resolver=None):
        self.target, self._headers, self._on_message = target, dict(headers), on_message
        self._timeout, self._resolver = timeout, resolver
        self.session_id: str | None = None
        self.protocol_version: str | None = None

    def open(self) -> None:
        """Nothing to open: the session starts with the first POST."""

    def _request_headers(self, accept: str) -> dict:
        h = {'Accept': accept, 'User-Agent': CLIENT_NAME, **self._headers}
        if self.session_id:
            h['Mcp-Session-Id'] = self.session_id
        if self.protocol_version:
            h['MCP-Protocol-Version'] = self.protocol_version
        return h

    def _seen(self, m: dict) -> None:
        result = m.get('result')
        if isinstance(result, dict) and isinstance(result.get('protocolVersion'), str) and 'capabilities' in result:
            self.protocol_version = result['protocolVersion'][:32]
        self._on_message(m)

    def send(self, message: dict) -> None:
        body = json.dumps(message, separators=(',', ':')).encode('utf-8')
        headers = self._request_headers('application/json, text/event-stream')
        headers['Content-Type'] = 'application/json'
        try:
            conn, resp = _open(self.target, 'POST', self.target.path, headers, body, self._timeout, self._resolver)
        except TransportError as e:
            if e.code == 'not_found' and self.session_id:
                raise TransportError('session_expired', 'The server no longer knows this session. Start a new one.',
                                     status=404) from e
            raise
        try:
            sid = resp.getheader('Mcp-Session-Id')
            if sid and not self.session_id and len(sid) <= 256 and sid.isascii() and sid.isprintable():
                self.session_id = sid
            ctype = (resp.getheader('Content-Type') or '').split(';')[0].strip().lower()
            if resp.status == 202 or resp.status == 204:
                _read_limited(resp)
            elif ctype == 'application/json':
                _dispatch(_read_limited(resp), self._seen)
            elif ctype == 'text/event-stream':
                for event, data in iter_sse(resp):
                    if event in ('message', ''):
                        _dispatch(data, self._seen)
            else:
                _read_limited(resp)
                raise TransportError('bad_content_type', 'The server answered with something that is neither JSON nor an '
                                                         'event stream.', status=resp.status)
        finally:
            conn.close()

    def close(self) -> None:
        if not self.session_id:
            return
        try:
            conn, resp = _open(self.target, 'DELETE', self.target.path, self._request_headers('*/*'), None, 10.0,
                               self._resolver)
            resp.read(1024)
            conn.close()
        except Exception:                                # ending a session is a courtesy; the server may refuse it
            pass


class SseSession:
    """Legacy SSE: one long GET for the answers, a POST for each client message to the address the
    server's `endpoint` event names (which must be the approved origin)."""

    protocol = 'sse'

    def __init__(self, target: Target, headers: dict, on_message, *, timeout: float = IDLE_TIMEOUT_S, resolver=None,
                 on_close=None):
        self.target, self._headers, self._on_message = target, dict(headers), on_message
        self._timeout, self._resolver, self._on_close = timeout, resolver, on_close
        self.session_id = None
        self.protocol_version = None
        self._post_path: str | None = None
        self._ready = threading.Event()
        self._error: TransportError | None = None
        self._conn = None
        self._thread: threading.Thread | None = None
        self._closed = False

    def open(self) -> None:
        h = {'Accept': 'text/event-stream', 'Cache-Control': 'no-cache', 'User-Agent': CLIENT_NAME, **self._headers}
        conn, resp = _open(self.target, 'GET', self.target.path, h, None, self._timeout, self._resolver)
        ctype = (resp.getheader('Content-Type') or '').split(';')[0].strip().lower()
        if ctype != 'text/event-stream':
            conn.close()
            raise TransportError('bad_content_type', 'The server did not answer with an event stream.', status=resp.status)
        self._conn = conn
        self._thread = threading.Thread(target=self._read, args=(resp,), name='remote-mcp-sse', daemon=True)
        self._thread.start()
        if not self._ready.wait(ENDPOINT_WAIT_S):
            self.close()
            raise TransportError('no_endpoint', 'The server never said where to send messages.')
        if self._error is not None:
            err = self._error
            self.close()
            raise err

    def _read(self, resp) -> None:
        try:
            for event, data in iter_sse(resp):
                if event == 'endpoint' and self._post_path is None:
                    target = urljoin(self.target.url, data.strip())
                    if not self.target.same_origin(target):
                        raise TransportError('endpoint_other_origin', 'The server named a different address for messages. '
                                                                      'Nothing was sent there.')
                    p = urlsplit(target)
                    self._post_path = (p.path or '/') + (f'?{p.query}' if p.query else '')
                    self._ready.set()
                elif event == 'message':
                    _dispatch(data, self._seen)
        except TransportError as e:
            if not self._closed:
                self._error = e
        except Exception as e:                           # a listener bug must end the session, not hang it
            if not self._closed:
                self._error = TransportError('stream_failed', f'The event stream failed ({type(e).__name__}).')
        finally:
            if not self._closed and self._error is None and not self._ready.is_set():
                self._error = TransportError('stream_ended', 'The server closed the event stream before it was ready.')
            self._ready.set()
            if self._on_close and not self._closed:
                self._on_close(self._error)

    def _seen(self, m: dict) -> None:
        result = m.get('result')
        if isinstance(result, dict) and isinstance(result.get('protocolVersion'), str) and 'capabilities' in result:
            self.protocol_version = result['protocolVersion'][:32]
        self._on_message(m)

    def send(self, message: dict) -> None:
        if self._error is not None:
            raise self._error
        if self._post_path is None:
            raise TransportError('no_endpoint', 'The event stream is not open.')
        body = json.dumps(message, separators=(',', ':')).encode('utf-8')
        h = {'Content-Type': 'application/json', 'User-Agent': CLIENT_NAME, **self._headers}
        conn, resp = _open(self.target, 'POST', self._post_path, h, body, self._timeout, self._resolver)
        try:
            _read_limited(resp)
        finally:
            conn.close()

    def close(self) -> None:
        self._closed = True
        conn = self._conn
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def open_session(protocol: str, url: str, headers: dict, on_message, *, allow_private: bool = False,
                 timeout: float = IDLE_TIMEOUT_S, resolver=None, on_close=None):
    """A ready session for `protocol`. Raises TransportError (or ValueError for a bad header)."""
    if protocol not in PROTOCOLS:
        raise TransportError('bad_protocol', 'The protocol must be streamable_http or sse.')
    check_headers(headers)
    target = Target(url, allow_private)
    if protocol == 'sse':
        s = SseSession(target, headers, on_message, timeout=timeout, resolver=resolver, on_close=on_close)
    else:
        s = StreamableSession(target, headers, on_message, timeout=timeout, resolver=resolver)
    s.open()
    return s


def deadline_left(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise TransportError('timeout', 'The check ran out of time.')
    return left
