"""Start an approved repository once, through its registered gate, and ask it for its tools.

Saving used to end at "Registered; not checked": a server that exits on its first line (a missing
API key, a missing build) read as healthy until an agent tried it (MC-1095). This runs the exact
command written to the MCP config (`github_activation.launch_config`: the gate, which checks the
recorded files and hands the approved Vault entries to the program), sends `initialize`, the
`initialized` notification and `tools/list` (paged), and never calls a tool. Then it stops the
process tree it started, by the PID it got back.

Credentials are resolved by the gate in the child process, as for an agent; no secret value
passes through this module. The server's stderr is text a stranger's program wrote: it is reduced
to bounded printable characters and shown as data, never interpreted.

The result lives on the connection record (`custom_connection_store.set_start_check`), bound to
the approval's fingerprint, so a new approval starts unchecked. The store state stays
`registered`: the gate refuses to start anything else.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
from datetime import datetime, timezone

from mc.core import _log
from mc.desk_connect import custom_connection_store as store
from mc.desk_connect.mcp_errors import ActivationError

PROTOCOL_VERSION = '2025-06-18'
DEADLINE_S = 25.0
MAX_PAGES = 10
_STDERR_KEEP = 8192
_REASON_CHARS = 300
_lock = threading.Lock()


def _printable(text: str, limit: int) -> str:
    return ' '.join(''.join(c if c.isprintable() else ' ' for c in text).split())[:limit]


def _reason(stderr: str) -> str:
    """The server's own last words, the lines most likely to say why it stopped."""
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    lines = [ln.replace('[desk-connect] repository start refused:', '').strip() for ln in lines]
    tail = ' '.join(lines[-3:])
    return _printable(tail, _REASON_CHARS)


def scrubber(op: dict):
    """`text -> text` that removes secrets from what a started server wrote. The wrapper that starts it
    runs `--raw` (no scrubbing), so this is the only place it happens: the approved credentials' values
    are read once (audited, consumer `desk-start-check`) and registered with the vault's own redactor
    (`secrets_store.redact`), then any word that still looks like a token (`parameter_schema.credential_like`)
    is replaced too. A value that cannot be read adds no redaction and is logged."""
    from mc import secrets_store as vault
    from mc.desk_connect import parameter_schema as ps
    for c in op.get('credentials') or []:
        try:
            vault.get_secret_value(c['vault'], consumer='desk-start-check', project_id=op['scope']['project_id'])
        except Exception as e:
            _log(f'[desk-connect] start check could not load a credential to redact: {type(e).__name__}', flush=True)

    def scrub(text: str) -> str:
        text = vault.redact(text)
        return re.sub(r'\S+', lambda m: '[redacted]' if ps.credential_like(m.group()) else m.group(), text)
    return scrub


def _stop(proc: subprocess.Popen) -> None:
    """Stop the process tree this module started. Closing stdin asks an MCP server to exit; the
    gate's child (node) does not die with the gate on Windows, so the tree goes by our own PID."""
    try:
        if proc.stdin:
            proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)], capture_output=True, timeout=10)
        else:
            os.killpg(proc.pid, 9)
    except Exception as e:
        _log(f'[desk-connect] start check could not stop its server tree: {type(e).__name__}', flush=True)
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _log(f'[desk-connect] start check server {proc.pid} did not exit', flush=True)


def handshake(argv: list[str], cwd: str | None, deadline_s: float = DEADLINE_S, scrub=lambda text: text) -> dict:
    """Run `argv` as an MCP stdio server: `{'ok': True, 'tools': n, 'server': name}` or
    `{'ok': False, 'code', 'reason'}`; `reason` is the server's stderr tail, possibly empty. Every
    piece of text the server wrote goes through `scrub` BEFORE it is cut to length, so a secret
    split by the cut cannot slip past."""
    flags = 0
    if os.name == 'nt':
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, creationflags=flags,
                                start_new_session=os.name != 'nt')
    except OSError as e:
        _log(f'[desk-connect] start check could not launch: {type(e).__name__}', flush=True)
        return {'ok': False, 'code': 'launch_failed', 'reason': ''}
    lines: queue.Queue = queue.Queue()
    err = bytearray()

    def read_out():
        assert proc.stdout is not None
        for raw in proc.stdout:
            lines.put(raw)
        lines.put(None)

    def read_err():
        assert proc.stderr is not None
        for raw in proc.stderr:
            err.extend(raw)
            del err[:-_STDERR_KEEP]

    threading.Thread(target=read_out, daemon=True).start()
    err_reader = threading.Thread(target=read_err, daemon=True)
    err_reader.start()
    end = time.monotonic() + deadline_s
    closed = threading.Event()

    def send(msg: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write((json.dumps(msg) + '\n').encode('utf-8'))
        proc.stdin.flush()

    def answer(msg_id: int):
        """The response to `msg_id`; None at exit or deadline. Other lines are skipped."""
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return None
            try:
                raw = lines.get(timeout=left)
            except queue.Empty:
                return None
            if raw is None:
                closed.set()                              # stdout ended: the program exited
                return None
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get('id') == msg_id:
                return msg

    def failed(code: str) -> dict:
        exited = closed.is_set() or proc.poll() is not None
        if exited:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            err_reader.join(1)                            # the last line is usually the reason
        return {'ok': False, 'code': 'exited' if exited and code == 'no_answer' else code,
                'reason': _reason(scrub(err.decode('utf-8', 'replace')))}

    try:
        try:
            send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                  'params': {'protocolVersion': PROTOCOL_VERSION, 'capabilities': {},
                             'clientInfo': {'name': 'clayrune-desk-check', 'version': '1'}}})
        except OSError:
            closed.set()                                  # stdin already closed: it exited at once
            return failed('no_answer')
        init = answer(1)
        if init is None:
            return failed('no_answer')
        if 'result' not in init:
            return {'ok': False, 'code': 'refused', 'reason': _printable(scrub(str((init.get('error') or {}).get('message', ''))), _REASON_CHARS)}
        server = _printable(scrub(str(((init['result'] or {}).get('serverInfo') or {}).get('name', ''))), 80)
        send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        count, cursor = 0, None
        for page in range(MAX_PAGES):
            send({'jsonrpc': '2.0', 'id': 2 + page, 'method': 'tools/list', 'params': {'cursor': cursor} if cursor else {}})
            reply = answer(2 + page)
            if reply is None:
                return failed('no_tools')
            result = reply.get('result') if isinstance(reply.get('result'), dict) else None
            if result is None or not isinstance(result.get('tools'), list):
                return {'ok': False, 'code': 'no_tools', 'reason': _printable(scrub(str((reply.get('error') or {}).get('message', ''))), _REASON_CHARS)}
            count += len(result['tools'])
            cursor = result.get('nextCursor')
            if not isinstance(cursor, str) or not cursor:
                break
        return {'ok': True, 'tools': count, 'server': server}
    finally:
        _stop(proc)


_FAILED = {
    'exited': 'Not working: the server stopped before it answered.',
    'no_answer': 'Not working: the server did not answer within {s} seconds.',
    'no_tools': 'Not working: the server started but did not list its tools.',
    'refused': 'Not working: the server refused to start a session.',
    'launch_failed': 'Not working: the server could not be started on this computer.',
    'not_registered': 'Not checked: this connection is not registered.',
}


def summary(result: dict) -> dict:
    """The plain status line for a check result: `{'status', 'tools'?, 'message', 'reason'?}`."""
    if result.get('ok'):
        n = result['tools']
        return {'status': 'working', 'tools': n, 'message': f'Working: {n} tool{"" if n == 1 else "s"}.'}
    reason = result.get('reason') or ''
    message = _FAILED.get(result.get('code', ''), _FAILED['exited']).format(s=int(DEADLINE_S))
    if reason:
        message += f' It said: "{reason}"'
    return {'status': 'failed', 'code': result.get('code', 'exited'), 'message': message, 'reason': reason}


def check(op: dict) -> dict:
    """Run the registered start command once and record what it showed. Returns the summary plus
    `checked_at`. One check at a time: each starts a real program."""
    from mc.desk_connect import github_activation as activation
    try:
        cfg = activation.launch_config(op)
    except ActivationError as e:
        out = {'status': 'failed', 'code': e.code, 'message': f'Not checked: {e}', 'reason': ''}
    else:
        with _lock:
            out = summary(handshake([cfg['command'], *cfg['args']], op.get('directory'), scrub=scrubber(op)))
    out['checked_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    from mc.desk_connect import custom_connection_operation as operation
    store.set_start_check(op['scope']['kind'], op['scope']['project_id'], op['server_name'],
                          {**out, 'fingerprint': operation.fingerprint(op)})
    _log(f'[desk-connect] start check {op["server_name"]}: {out["status"]}'
         f'{" " + str(out.get("tools")) + " tools" if out["status"] == "working" else " " + out.get("code", "")}', flush=True)
    return out


def recorded(rec: dict) -> dict | None:
    """The last check of this exact approval, or None."""
    c = rec.get('start_check')
    if isinstance(c, dict) and c.get('fingerprint') == rec.get('fingerprint'):
        return {k: v for k, v in c.items() if k != 'fingerprint'}
    return None

