"""The stdio side of a user-chosen remote MCP server (docs/DESK_SERVICE_PROFILES_SPEC.md section 6.3,
slice U2d). An MCP client (an agent session) starts this program as a local stdio server; it
relays each JSON-RPC message to the approved remote address through `remote_mcp_transport`.

It exists so a token never has to be written into `.claude.json` / `.mcp.json`: the config holds the
launch line of `tools/with-secret.py`, which resolves the vault entry NAMES into this program's
environment, and `tools/remote-mcp-bridge.py` reads them from there. What is on disk is names only.

    python tools/with-secret.py --raw [--project ID] --env CLAYRUNE_REMOTE_CRED_0=<vault name> --
        python -I tools/remote-mcp-bridge.py --protocol P --url U [--allow-private]
        [--credential '{"header":"Authorization","env":"CLAYRUNE_REMOTE_CRED_0","prefix":"Bearer "}']

A server with no credential skips the wrapper and runs the same program directly, so the rules
that hold for a token (no redirect followed, no other origin, no private address that was not
approved) hold for every remote server Desk registers, not only the credentialed ones.

Messages are passed through untouched (server text is data; nothing here interprets it). A request
that fails gets a JSON-RPC error that names a code and a sentence of ours, never a header value or a
response body. A value read from the environment is removed from it at start and is replaced in
everything this program writes (stdout and stderr), so a server that echoes its own token back
cannot put it into an agent's transcript.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from mc.desk_connect import remote_mcp_transport as _t

WORKERS = 8
_ENV_NAME = re.compile(r'^[A-Z][A-Z0-9_]{0,63}$')


class Bridge:
    def __init__(self, protocol: str, url: str, headers: dict, secrets: list[str], *, allow_private: bool,
                 out=None, err=None, resolver=None, timeout: float = _t.IDLE_TIMEOUT_S):
        self._out = out or sys.stdout
        self._err = err or sys.stderr
        self._secrets = [s for s in secrets if len(s) >= 6]
        self._lock = threading.Lock()
        self._pending: set = set()
        self._ended = threading.Event()
        self.exit_code = 0
        self._session = _t.open_session(protocol, url, headers, self._from_server, allow_private=allow_private,
                                        timeout=timeout, resolver=resolver, on_close=self._stream_closed) \
            if protocol == 'sse' else None
        if self._session is None:
            self._session = _t.open_session(protocol, url, headers, self._from_server, allow_private=allow_private,
                                            timeout=timeout, resolver=resolver)

    # ── output ───────────────────────────────────────────────────────────
    def _scrub(self, text: str) -> str:
        for s in self._secrets:
            text = text.replace(s, '[redacted]')
        return text

    def _scrub_tree(self, node):
        """`node` with every string in it (keys too) scrubbed, in place. Scrubbing the text after
        `json.dumps` misses a token holding a quote, a backslash or a non-ASCII character, because
        serialising escapes it; so the strings are scrubbed as the server sent them. Iterative: a
        deeply nested message must not hit the recursion limit."""
        if isinstance(node, str):
            return self._scrub(node)
        stack = [node]
        while stack:
            cur = stack.pop()
            is_dict = isinstance(cur, dict)
            for k, v in (list(cur.items()) if is_dict else enumerate(cur)):
                if isinstance(v, str):
                    v = self._scrub(v)
                elif isinstance(v, (dict, list)):
                    stack.append(v)
                nk = self._scrub(k) if is_dict and isinstance(k, str) else k
                if nk != k:
                    cur.pop(k)
                cur[nk] = v
        return node

    def _emit(self, message: dict) -> None:
        line = json.dumps(self._scrub_tree(message), separators=(',', ':'), ensure_ascii=True)
        with self._lock:
            self._out.write(line + '\n')
            self._out.flush()

    def note(self, text: str) -> None:
        with self._lock:
            self._err.write(self._scrub(text)[:500] + '\n')
            self._err.flush()

    def _from_server(self, m: dict) -> None:
        if 'id' in m and 'method' not in m:
            with self._lock:
                self._pending.discard(_key(m['id']))
        self._emit(m)

    def _stream_closed(self, error) -> None:
        """The SSE stream ended: every request still waiting gets an error, then the program ends."""
        self.exit_code = 1
        self._fail_pending(error.code if error else 'stream_ended',
                           str(error) if error else 'The server closed the event stream.')
        self._ended.set()

    def _fail_pending(self, code: str, text: str) -> None:
        with self._lock:
            ids = list(self._pending)
            self._pending.clear()
        for k in ids:
            self._emit({'jsonrpc': '2.0', 'id': _unkey(k), 'error': {'code': -32000, 'message': text, 'data': {'code': code}}})

    # ── input ────────────────────────────────────────────────────────────
    def handle(self, message) -> None:
        if not isinstance(message, dict):
            return
        is_request = 'method' in message and 'id' in message
        if is_request:
            with self._lock:
                self._pending.add(_key(message['id']))
        try:
            self._session.send(message)
        except _t.TransportError as e:
            self.note(f'remote MCP: {e.code}: {e}')
            if is_request:
                with self._lock:
                    still = _key(message['id']) in self._pending
                    self._pending.discard(_key(message['id']))
                if still:
                    self._emit({'jsonrpc': '2.0', 'id': message['id'],
                                'error': {'code': -32000, 'message': str(e), 'data': {'code': e.code}}})
        except Exception as e:                           # a bug must answer the request, not hang the client
            self.note(f'remote MCP: failed ({type(e).__name__})')
            if is_request:
                with self._lock:
                    self._pending.discard(_key(message['id']))
                self._emit({'jsonrpc': '2.0', 'id': message['id'],
                            'error': {'code': -32603, 'message': 'The bridge failed; see its error output.'}})

    def run(self, stdin) -> int:
        pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix='remote-mcp-send')
        try:
            for raw in stdin:
                if self._ended.is_set():
                    break
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    doc = json.loads(raw)
                except ValueError:
                    self.note('remote MCP: ignored a line that is not JSON')
                    continue
                for m in (doc if isinstance(doc, list) else [doc]):
                    pool.submit(self.handle, m)
        finally:
            pool.shutdown(wait=True)
            try:
                self._session.close()
            except Exception:
                pass
        return self.exit_code


def _key(i) -> str:
    return json.dumps(i, sort_keys=True)


def _unkey(k: str):
    return json.loads(k)


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog='remote-mcp-bridge', description='Relay a local MCP stdio session to one approved '
                                                                        'remote MCP server.')
    ap.add_argument('--protocol', required=True, choices=_t.PROTOCOLS)
    ap.add_argument('--url', required=True)
    ap.add_argument('--allow-private', action='store_true')
    ap.add_argument('--credential', action='append', default=[], metavar='JSON')
    return ap.parse_args(argv)


def headers_from_env(credentials: list[str], environ=None) -> tuple[dict, list[str]]:
    """`(headers, secret values)` for `--credential` JSON specs, reading each value from the
    environment and removing it there. Raises ValueError with a message that holds no value."""
    env = os.environ if environ is None else environ
    headers: dict = {}
    secrets: list[str] = []
    for spec in credentials:
        try:
            c = json.loads(spec)
        except ValueError:
            raise ValueError('a --credential is not valid JSON') from None
        if not isinstance(c, dict) or set(c) != {'header', 'env', 'prefix'} \
                or not all(isinstance(c[k], str) for k in c) or not _ENV_NAME.match(c['env']):
            raise ValueError('a --credential must be {header, env, prefix}')
        value = env.pop(c['env'], None)
        if not value:
            raise ValueError(f'the credential variable {c["env"]} is not set (is the Secrets entry present and unlocked?)')
        secrets.append(value)
        headers[c['header']] = c['prefix'] + value
    return headers, secrets


def main(argv: list[str] | None = None, *, stdin=None, out=None, err=None) -> int:
    err = err or sys.stderr
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        headers, secrets = headers_from_env(args.credential)
        _t.check_headers(headers)
        bridge = Bridge(args.protocol, args.url, headers, secrets, allow_private=args.allow_private, out=out, err=err)
    except ValueError as e:
        err.write(f'remote-mcp-bridge: {e}\n')
        return 2
    except _t.TransportError as e:
        err.write(f'remote-mcp-bridge: {e.code}: {e}\n')
        return 1
    return bridge.run(stdin or sys.stdin)
