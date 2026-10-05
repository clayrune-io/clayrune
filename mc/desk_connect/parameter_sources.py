"""Where connection evidence is read from, with bounds (spec §5.1: "fetching and parsing
are implemented services, not an agent with browsing tools").

Three readers, each separate from the others and none of them an agent:

  * `fetch_npm` / `fetch_pypi`: ONE metadata document from the package registry's fixed
    https host (`registry.npmjs.org`, `pypi.org`). Metadata only: no tarball, wheel or
    sdist is downloaded, no archive is opened, no package code is imported or run, no
    `npm`/`pip` command exists in this file.
  * `fetch_spec`: the OpenAPI document at an address the person gave explicitly. Public
    https only (the slice 3 rules: `url_check` for the address, `net_guard.resolve_public`
    for what it resolves to), GET, no redirect followed, no credential sent, 2 MB at
    most. `$ref`s inside it are never fetched. A private, local or plain-http target is
    refused with a pointer to pasting the document instead (spec §5.1: such targets use
    pasted parameters before Save).
  * `read_docs_page`: public HTML through the SAME guarded temporary pane discovery uses
    (`pane_reader` behind `guard_proxy`). A failed page read is returned as a failure; no
    curl, requests or archive fallback exists, and none may be added.

Each fetch connects to the ADDRESS it just vetted (`_PinnedHTTPS`), never resolving the
name a second time, so a record that changes between the check and the connect has
nothing to act on. Every failure has its own code and sentence (`SourceError`).
"""
from __future__ import annotations

import http.client
import socket
import ssl
import time
from urllib.parse import quote, urlsplit

from mc.core import _log
from mc.desk_connect import net_guard, parameter_parsers as pp, url_check
from mc.desk_connect.url_check import UrlError

FETCH_S = 10.0
MAX_BODY = 2_000_000
PANE_PROJECT = 'desk_connect'
NPM_HOST = 'registry.npmjs.org'
PYPI_HOST = 'pypi.org'

MESSAGES = {
    'source_timeout': 'The source did not answer in time.',
    'source_unreachable': 'The source could not be reached.',
    'source_not_found': 'The source says there is no such package or document.',
    'source_too_large': 'The source is larger than Clayrune reads (2 MB), so it was not used.',
    'source_blocked': 'Clayrune does not look up private, local or unencrypted addresses by itself.',
    'source_redirect': 'The address redirects somewhere else. Clayrune does not follow redirects; paste the final address.',
    'source_not_json': 'The source did not return a JSON document Clayrune can read.',
    'source_not_openapi': 'That document is not an OpenAPI or Swagger description.',
}


class SourceError(Exception):
    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or MESSAGES[code])
        self.code = code


class _PinnedHTTPS(http.client.HTTPSConnection):
    """HTTPS to a vetted address with the certificate checked against the NAME."""

    def __init__(self, host: str, addr: str, timeout: float):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self._addr = addr

    def connect(self):
        sock = socket.create_connection((self._addr, 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)   # type: ignore[attr-defined]


def http_get(url: str, timeout: float, max_bytes: int = MAX_BODY, *, resolve=None,
             connect=None, now=time.monotonic) -> bytes:
    """One GET to `url` (https, port 443): redirect refused, response capped, a total
    deadline of `timeout` seconds. `resolve` / `connect` / `now` are test seams."""
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    if parts.scheme != 'https' or not host or parts.port not in (None, 443):
        raise SourceError('source_blocked')
    try:
        addrs = (resolve or net_guard.resolve_public)(host)
    except net_guard.Blocked as e:
        if e.code == 'dns_failed':
            raise SourceError('source_unreachable', 'That host name does not resolve.') from e
        raise SourceError('source_blocked') from e
    deadline = now() + timeout
    conn = (connect or _PinnedHTTPS)(host, addrs[0], max(1.0, timeout))
    try:
        path = (parts.path or '/') + (f'?{parts.query}' if parts.query else '')
        conn.request('GET', path, headers={'Accept': 'application/json', 'Accept-Encoding': 'identity',
                                           'User-Agent': 'Clayrune-desk-connect'})
        resp = conn.getresponse()
        if 300 <= resp.status < 400:
            raise SourceError('source_redirect')
        if resp.status == 404:
            raise SourceError('source_not_found')
        if resp.status != 200:
            raise SourceError('source_unreachable')
        length = resp.getheader('Content-Length')
        if length and length.isdigit() and int(length) > max_bytes:
            raise SourceError('source_too_large')
        chunks, total = [], 0
        while True:
            if now() > deadline:
                raise SourceError('source_timeout')
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise SourceError('source_too_large')
            chunks.append(chunk)
        return b''.join(chunks)
    except SourceError:
        raise
    except (TimeoutError, socket.timeout) as e:
        raise SourceError('source_timeout') from e
    except (OSError, http.client.HTTPException, ssl.SSLError) as e:
        raise SourceError('source_unreachable') from e
    finally:
        try:
            conn.close()
        except Exception as e:                                   # noqa: BLE001
            _log(f'[desk_connect] detect connection close failed: {type(e).__name__}', flush=True)


def _json(raw: bytes):
    doc = pp.load_json(raw, allow_dupes=True)
    if not isinstance(doc, dict):
        raise SourceError('source_not_json')
    return doc


def npm_url(spec: dict) -> str:
    """Registry URL for one version document: `/<name>/<exact version or tag>`. The
    registry resolves a tag server-side; the answer names the exact version."""
    name = quote(spec['name'], safe='@').replace('/', '%2F')
    return f'https://{NPM_HOST}/{name}/{quote(spec["version"] or spec["tag"] or "latest", safe="")}'


def pypi_url(spec: dict) -> str:
    name = quote(spec['name'], safe='')
    if spec['version']:
        return f'https://{PYPI_HOST}/pypi/{name}/{quote(spec["version"], safe="")}/json'
    return f'https://{PYPI_HOST}/pypi/{name}/json'


def fetch_npm(spec: dict, *, fetch=None, timeout: float = FETCH_S) -> dict:
    """The npm version document (JSON object), or SourceError. `fetch(url, timeout,
    max_bytes) -> bytes` is the test seam."""
    return _json((fetch or http_get)(npm_url(spec), timeout, MAX_BODY))


def fetch_pypi(spec: dict, *, fetch=None, timeout: float = FETCH_S) -> dict:
    return _json((fetch or http_get)(pypi_url(spec), timeout, MAX_BODY))


def fetch_spec(url: str, own_hosts=(), *, fetch=None, timeout: float = FETCH_S) -> dict:
    """The JSON document at an explicitly given public https address, or SourceError.
    An address `url_check` refuses (plain http, private, a query, a port, userinfo) is
    `source_blocked`: paste the document instead."""
    try:
        clean = url_check.check_url(url, own_hosts=own_hosts)['url']
    except UrlError as e:
        raise SourceError('source_blocked', f'{e} Paste the document text instead.') from e
    return _json((fetch or http_get)(clean, timeout, MAX_BODY))


def read_docs_page(url: str, own_hosts=(), *, read_page=None, make_proxy=None, resolve=None) -> dict:
    """`{ok: True, url, title, text, truncated, hidden_flagged}` or `{ok: False, code,
    message}`. The page is opened only in the guarded, signed-out, throwaway pane; the
    proxy and the pane are closed in `finally`. Never raises."""
    holder: list = []
    proxy = None
    try:
        try:
            info = url_check.check_url(url, own_hosts=own_hosts)
        except UrlError as e:
            return {'ok': False, 'code': 'source_blocked', 'message': f'{e} Paste the page text instead.'}
        try:
            (resolve or net_guard.resolve_public)(info['host'], own_hosts)
        except net_guard.Blocked as e:
            return {'ok': False, 'code': 'source_blocked' if e.code != 'dns_failed' else 'page_unreachable', 'message': str(e)}
        if read_page is None or make_proxy is None:
            from mc.desk_connect import guard_proxy, pane_reader      # heavy imports (Flask blueprint, Chromium harness)
            read_page = read_page or pane_reader.read_page
            make_proxy = make_proxy or (lambda hosts: guard_proxy.GuardProxy(own_hosts=hosts))
        proxy = make_proxy(tuple(own_hosts))
        proxy.start()
        page = read_page(info['url'], proxy, project_id=PANE_PROJECT, holder=holder)
        return page if isinstance(page, dict) else {'ok': False, 'code': 'page_unreachable', 'message': 'The page could not be read.'}
    except Exception as e:                                       # noqa: BLE001
        _log(f'[desk_connect] docs page read raised {type(e).__name__}', flush=True)
        return {'ok': False, 'code': 'page_unreachable', 'message': 'The page could not be read.'}
    finally:
        for pane in list(holder):
            try:
                pane.abort()
            except Exception as e:                               # noqa: BLE001
                _log(f'[desk_connect] pane close failed: {type(e).__name__}', flush=True)
        if proxy is not None:
            try:
                proxy.close()
            except Exception as e:                               # noqa: BLE001
                _log(f'[desk_connect] proxy close failed: {type(e).__name__}', flush=True)
