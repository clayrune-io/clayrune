#!/usr/bin/env python3
"""Agent-mail first: route one agent-sent email through the local Clayrune server.

Called from send_mail.py before it touches Gmail. If the caller is an agent
session (CLAYRUNE_PROJECT_ID + CLAYRUNE_SESSION_ID in the environment, set for
every agent child by mc/agent_spawn.py, or --project/--session flags), the note
is POSTed to the local agent-mail route:

    POST http://127.0.0.1:<port>/api/project/<pid>/agent/mail/note
         {session_id, request_id, subject, body}      (mc/blueprints/agent_mail_note_routes.py)

The recipient is always the install's verified owner; replies land back in the
sending chat, not in the Gmail inbox. Stdlib only.

Contract of `attempt()`:
  * accepted           -> prints one stdout line naming the transport, returns True.
  * no session context -> silent, returns False (server-side alerts have none).
  * ANY other outcome  -> one stderr line "agent-mail unavailable (<reason>)", returns
                          False; the caller then sends via Gmail unchanged. A mail is
                          never dropped on this path. Never raises.

request_id is a hash of session + subject + body, so re-running the same send is
idempotent on the server (same intent, not a second mail).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping, Optional, Tuple
from urllib.parse import quote

DEFAULT_PORT = 5199
TIMEOUT_S = 20
# Mirrors mc/agent_mail_outbound.validate_request: subject <= 200 bytes, body <= 16384.
# The server wraps the body with a project/conversation header (task text is cut at
# 300 chars, so up to ~1.2 KB) and a footer, then re-checks 16384. Keep the margin
# here; an oversized body goes by Gmail whole rather than being truncated.
SUBJECT_MAX_BYTES = 200
BODY_MAX_BYTES = 14000
HINT_MAX_CHARS = 300

Post = Callable[[str, dict, float], Tuple[int, Any]]


def request_id(session: str, subject: str, body: str) -> str:
    """Deterministic, matches the server's [A-Za-z0-9_-]{1,128}."""
    digest = hashlib.sha256('\0'.join((session, subject, body)).encode('utf-8')).hexdigest()
    return 'sm_' + digest[:40]


def _http_post(url: str, payload: dict, timeout: float) -> Tuple[int, Any]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode('utf-8'), method='POST',
        headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    try:
        return status, json.loads(raw.decode('utf-8'))
    except Exception:
        return status, None


_REAL_POST = _http_post


def _call(post: Post, url: str, payload: dict) -> Tuple[int, Any]:
    """Run `post` with a hard TIMEOUT_S wall-clock bound (urlopen's own timeout is
    per socket operation). A daemon thread, so a stuck call cannot hold the exit."""
    box: dict = {}

    def run() -> None:
        try:
            box['r'] = post(url, payload, TIMEOUT_S)
        except BaseException as e:  # noqa: BLE001 - reported as the reason, never raised
            box['e'] = e

    t = threading.Thread(target=run, daemon=True, name='agent-mail-route')
    t.start()
    t.join(TIMEOUT_S)
    if t.is_alive():
        raise TimeoutError(f'no answer within {TIMEOUT_S}s')
    if 'e' in box:
        raise box['e']
    return box['r']


def _unavailable(reason: str, err: Any) -> bool:
    print(f'[send_mail] agent-mail unavailable ({reason}); sending via Gmail',
          file=err, flush=True)
    return False


def _reason_from(status: int, data: Any) -> str:
    # The route's error vocabulary and hints are fixed, code-owned strings (never
    # remote text). Still bounded and kept to one printable line here.
    fields = data if isinstance(data, dict) else {}
    code = fields.get('error')
    code = code[:60] if isinstance(code, str) else ''
    reason = f'HTTP {status} {code}'.strip()
    hint = fields.get('hint')
    hint = ' '.join(hint.split()) if isinstance(hint, str) else ''
    hint = ''.join(c for c in hint if c.isprintable())[:HINT_MAX_CHARS]
    return f'{reason}: {hint}' if hint else reason


def attempt(subject: str, body: str, *, project: Optional[str] = None,
            session: Optional[str] = None, verify: bool = False,
            env: Optional[Mapping[str, str]] = None, post: Optional[Post] = None,
            out: Any = None, err: Any = None) -> bool:
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    env = os.environ if env is None else env
    project = (project or env.get('CLAYRUNE_PROJECT_ID') or '').strip()
    session = (session or env.get('CLAYRUNE_SESSION_ID') or '').strip()
    if not project or not session:
        return False  # no session context: SMTP as today, silently
    post = post or _http_post
    if post is _REAL_POST and os.environ.get('PYTEST_CURRENT_TEST'):
        return False  # tests never reach a live server; inject `post` to exercise this path
    try:
        if not subject.strip() or len(subject.encode('utf-8')) > SUBJECT_MAX_BYTES \
                or any(ord(c) < 32 or ord(c) == 127 for c in subject):
            return _unavailable('subject not acceptable to agent-mail', err)
        if not body.strip() or '\0' in body:
            return _unavailable('body not acceptable to agent-mail', err)
        size = len(body.encode('utf-8'))
        if size > BODY_MAX_BYTES:
            return _unavailable(f'body is {size} bytes, over the {BODY_MAX_BYTES}-byte limit', err)
        port = (env.get('CLAYRUNE_PORT') or '').strip() or str(DEFAULT_PORT)
        url = f'http://127.0.0.1:{int(port)}/api/project/{quote(project, safe="")}/agent/mail/note'
        payload = {'session_id': session, 'request_id': request_id(session, subject, body),
                   'subject': subject, 'body': body}
        status, data = _call(post, url, payload)
    except TimeoutError as e:
        return _unavailable(f'timeout: {e}', err)
    except Exception as e:  # noqa: BLE001 - connection refused, bad port, anything: fall back
        return _unavailable(f'{type(e).__name__}: {str(e)[:120]}', err)
    if status != 200 or not isinstance(data, dict):
        return _unavailable(_reason_from(status, data), err)
    intent = data.get('intent_id')
    if data.get('state') == 'accepted' and isinstance(intent, str) and intent:
        line = f'[send_mail] sent via agent-mail, intent {intent}'
        if verify:
            line += ' (--verify: accepted by agent-mail counts as verified; no IMAP check)'
        print(line, file=out, flush=True)
        return True
    state, reason = data.get('state'), data.get('reason')
    state = state[:30] if isinstance(state, str) else '?'
    reason = reason[:40] if isinstance(reason, str) else ''
    return _unavailable(f'not accepted: state {state} {reason}'.strip(), err)
