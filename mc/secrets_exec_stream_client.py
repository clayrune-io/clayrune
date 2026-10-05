"""The CLI half of the server-parented streaming exec (MC-1047) — what
``tools/with-secret.py`` calls when the vault is locked in its own process.

It asks the server (``POST /api/secrets/exec-stream``, same loopback + per-boot token
gate as ``/api/secrets/exec``) to start the child, then does nothing but relay: the
frame stream becomes this process's stdout/stderr, this process's stdin is POSTed to the
child, and the child's exit code is this process's exit code. No secret value is ever
requested or received — the child's environment is built inside the server.

The child's life follows this process's. If this process exits or is killed, the
connection drops and the server kills the child; if this process is asked to stop
(Ctrl-C, SIGTERM) it tells the server to end the session first, so the child does not
linger until the next heartbeat.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import sys
import threading

from mc import secrets_exec_stream_wire as wire
from mc import secrets_store as vault

CONNECT_TIMEOUT_S = 10
# The server sends a heartbeat at least every 5s; a stream silent for this long is dead.
READ_TIMEOUT_S = 45
STDIN_CHUNK = 65536


def _post(conn: http.client.HTTPConnection, path: str, token: str,
          body: bytes = b'') -> int:
    conn.request('POST', path, body=body, headers={
        'X-Clayrune-Exec-Token': token,
        'Content-Type': 'application/octet-stream',
        'Content-Length': str(len(body))})
    resp = conn.getresponse()
    resp.read()
    return resp.status


def _forward_stdin(port: int, token: str, session_id: str) -> None:
    """Copy this process's stdin to the child until EOF, then close the child's stdin.
    Runs as a daemon thread: if the child is gone first, the 410 ends it."""
    base = f'{wire.PATH}/{session_id}/stdin'
    try:
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=CONNECT_TIMEOUT_S)
        fd = sys.stdin.fileno() if sys.stdin is not None else None
        while fd is not None:
            chunk = os.read(fd, STDIN_CHUNK)
            if not chunk:
                break
            if _post(conn, base, token, chunk) != 204:
                return
        _post(conn, base + '?eof=1', token)
        conn.close()
    except (OSError, ValueError, http.client.HTTPException):
        pass                              # stdin is gone or the child is: the main loop decides


def _end_session(port: int, token: str, session_id: str) -> None:
    try:
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=CONNECT_TIMEOUT_S)
        _post(conn, f'{wire.PATH}/{session_id}/end', token)
        conn.close()
    except (OSError, http.client.HTTPException):
        pass


def _refusal(resp: http.client.HTTPResponse, locked_message: str) -> int:
    try:
        result = json.loads(resp.read().decode('utf-8'))
    except (OSError, ValueError, http.client.HTTPException):
        print(f"with-secret: {locked_message}", file=sys.stderr)
        return 2
    if result.get('error') == 'vault_locked':
        print(f"with-secret: {locked_message}", file=sys.stderr)
    else:
        print(f"with-secret: server-exec refused: "
              f"{result.get('message') or result.get('error')}", file=sys.stderr)
    return 2


def run(args, cmd: list[str], project: str | None, unattended: bool,
        locked_message: str) -> int:
    """Run ``cmd`` through the server and relay it. ``args`` is with-secret's parsed
    namespace (``env``, ``user``, ``totp``, ``stdin``, ``unset``). Returns the child's
    exit code, or 2 for anything that stopped it starting or reaching its end."""
    try:
        token = vault.exec_token_path().read_text(encoding='utf-8').strip()
    except OSError:
        token = ''
    if not token:
        print(f"with-secret: {locked_message}", file=sys.stderr)
        return 2

    body = json.dumps({
        'env': [list(p) for p in args.env],
        'user': [list(p) for p in args.user],
        'totp': [list(p) for p in args.totp],
        'stdin': args.stdin,
        'unset': list(args.unset),
        'project_id': project,
        'unattended': unattended,
        'command': cmd,
        'cwd': os.getcwd(),
        # The child's base environment is the CALLER's, as it would be in-process; the
        # server's own environment (every provider key it hydrated) never reaches a child.
        'environ': dict(os.environ),
        'claude_session_id': os.environ.get('CLAUDE_CODE_SESSION_ID', ''),
    }).encode('utf-8')
    port = vault.exec_route_port()
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=CONNECT_TIMEOUT_S)
    try:
        conn.request('POST', wire.PATH, body=body, headers={
            'Content-Type': 'application/json', 'X-Clayrune-Exec-Token': token})
        resp = conn.getresponse()
    except (OSError, http.client.HTTPException):
        # Server unreachable: indistinguishable, to the caller, from "also locked".
        print(f"with-secret: {locked_message}", file=sys.stderr)
        return 2
    if resp.status != 200:
        return _refusal(resp, locked_message)
    if conn.sock is not None:
        conn.sock.settimeout(READ_TIMEOUT_S)

    out, err = sys.stdout.buffer, sys.stderr.buffer
    session_id = ''
    exit_code: int | None = None
    try:
        while True:
            try:
                frame = wire.read_frame(resp.read)
            except (OSError, ValueError, http.client.HTTPException, socket.timeout):
                frame = None
            if frame is None:
                break
            channel, payload = frame
            if channel == wire.CH_SESSION:
                session_id = payload.decode('ascii', 'replace')
                if not args.stdin:                  # --stdin already took the child's stdin
                    threading.Thread(target=_forward_stdin,
                                     args=(port, token, session_id), daemon=True).start()
            elif channel == wire.CH_STDOUT:
                out.write(payload)
                out.flush()
            elif channel == wire.CH_STDERR:
                err.write(payload)
                err.flush()
            elif channel == wire.CH_EXIT:
                exit_code = wire.unpack_exit(payload)
                break
            elif channel == wire.CH_ERROR:
                print(f"with-secret: server-exec: {payload.decode('utf-8', 'replace')}",
                      file=sys.stderr)
                break
    except BaseException:
        # Ctrl-C / SIGTERM / a write to a closed pipe: end the child now rather than
        # leaving it to the heartbeat.
        if session_id:
            _end_session(port, token, session_id)
        raise
    finally:
        conn.close()
    if exit_code is None:
        print("with-secret: the server-exec stream ended before the command did",
              file=sys.stderr)
        return 2
    return exit_code
