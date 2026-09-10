#!/usr/bin/env python3
"""Safe path for an unattended cycle to read Ron's reply — laundered, not raw.

AGENT_RULES.md's "Read his reply" section used to point unattended cycles at
`search_email`/`read_email` on the mail MCP, called directly from inside their
own fully-tooled, `--dangerously-skip-permissions` session. That put raw,
sender-unauthenticated inbox text straight into a session with Bash, Write,
and every other MCP server still live — see docs/UNTRUSTED_INPUT_SURFACE.md
finding #2.

This script is what a cycle should run instead (via Bash, NOT via the mail
MCP tools): it does the IMAP fetch itself, hands the raw messages to
`mc.mail_launder.launder_mail_digest` for a TOOLLESS laundering pass, and
prints ONLY the resulting structured, labelled digest to stdout as JSON. Raw
message bodies never reach the calling session — only this process (and the
toolless oneshot() subprocess it spawns) ever sees them.

Fails closed: any laundering failure prints a structured `{"ok": false, ...}`
error with a `guidance` field and exits 1. It never falls back to printing
raw fetched text.

Usage:
    python read_digest.py --query "[Clayrune steward]" [--count 5] [--model haiku]

Credentials: same as tools/mail-mcp/server.py (NIGHT_MAIL_USER /
NIGHT_MAIL_APP_PASSWORD, or ~/.clayrune/night-mail.json).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_mail_server():
    """Reuse tools/mail-mcp/server.py's IMAP connect/fetch helpers rather than
    re-implementing them — same credentials, same behavior as the raw
    `list_recent`/`search_email`/`read_email` tools this script replaces for
    the reply-reading use case."""
    return _load_module('mail_mcp_server', _HERE / 'server.py')


def _load_mail_launder():
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    import mc.mail_launder as _launder  # noqa: E402
    return _launder


def fetch_messages(mail_server, query: str, count: int, mailbox: str) -> List[Dict[str, Any]]:
    """Search + fetch full bodies for up to `count` matching messages. Returns
    plain dicts (from/date/subject/body) — the only place in this script that
    touches raw IMAP content."""
    M = mail_server._connect()
    try:
        M.select(mailbox, readonly=True)
        typ, data = M.uid("SEARCH", None, "X-GM-RAW", f'"{query}"')
        if typ != "OK":
            typ, data = M.uid("SEARCH", None, "TEXT", query)
        ids = data[0].split() if data and data[0] else []
        uids = ids[-count:][::-1]
        messages = []
        for uid in uids:
            typ, d = M.uid("FETCH", uid, "(RFC822)")
            if typ != "OK" or not d or not d[0]:
                continue
            msg = mail_server.message_from_bytes(d[0][1])
            messages.append({
                'from': mail_server._dec(msg.get('From')),
                'date': mail_server._dec(msg.get('Date')),
                'subject': mail_server._dec(msg.get('Subject')),
                'body': mail_server._body_text(msg),
            })
        return messages
    finally:
        try:
            M.logout()
        except Exception:
            pass


def run(query: str, count: int, mailbox: str, model: str, timeout: int,
        *, fetch_fn=None, launder_fn=None) -> tuple[Dict[str, Any], int]:
    """Orchestration, factored out so tests can inject `fetch_fn`/`launder_fn`
    and never need a real IMAP connection or a real oneshot() subprocess.
    Returns (result_dict, exit_code)."""
    if fetch_fn is None:
        mail_server = _load_mail_server()
        fetch_fn = lambda: fetch_messages(mail_server, query, count, mailbox)  # noqa: E731
    if launder_fn is None:
        _launder = _load_mail_launder()
        launder_fn = _launder.launder_mail_digest

    try:
        messages = fetch_fn()
    except Exception as e:
        return ({'ok': False, 'error': 'imap_fetch_failed', 'detail': repr(e),
                  'guidance': ('The IMAP fetch itself failed before laundering ran. '
                               'Do not fall back to a different mail-reading path — '
                               'report the failure.')}, 1)

    result = launder_fn(messages, query=query, model=model, timeout=timeout)
    return (result, 0 if result.get('ok') else 1)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--query', required=True, help='Gmail search query, e.g. "[Clayrune steward]"')
    ap.add_argument('--count', type=int, default=5, help='max messages to consider (default 5)')
    ap.add_argument('--mailbox', default='INBOX')
    ap.add_argument('--model', default='haiku')
    ap.add_argument('--timeout', type=int, default=60)
    args = ap.parse_args(argv)

    result, code = run(args.query, args.count, args.mailbox, args.model, args.timeout)
    print(json.dumps(result, indent=2))
    return code


if __name__ == '__main__':
    sys.exit(main())
