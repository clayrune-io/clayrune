"""IMAP string quoting for the mail tools — one place, so the two callers
(server.py `tool_search_email`, read_digest.py `fetch_messages`) cannot drift.

imaplib does NOT quote str arguments: `_command` joins them into the command
line as-is. A query like `subject:"Developer ID certificate"` interpolated
into `f'"{query}"'` therefore closed the quoted string early and Gmail
answered `BAD Could not parse command` (backlog 0a20a80c).

RFC 3501 quoted-string: backslash and double quote are backslash-escaped, and
the text must be 7-bit with no CR/LF/NUL. Anything else has to travel as a
literal (`{n}` + CRLF + raw bytes); imaplib supports exactly one literal, via
`M.literal`, which it appends after the LAST argument — so the query must be
the last argument of the command.
"""
from __future__ import annotations

from typing import Optional, Tuple

_NEEDS_LITERAL = ("\r", "\n", "\x00")


def quote(value: str) -> str:
    """Return `value` as an IMAP quoted-string. Caller must have checked
    `needs_literal(value)` is False."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def needs_literal(value: str) -> bool:
    return any(c in value for c in _NEEDS_LITERAL) or not value.isascii()


def search_args(key: str, query: str) -> Tuple[tuple, Optional[bytes]]:
    """Build `(uid_args, literal)` for `M.uid("SEARCH", *uid_args)` of
    `<key> <query>`. If `literal` is not None the caller must set
    `M.literal = literal` before the call; the query is the last argument and
    imaplib appends ` {n}` for it. Non-ASCII queries are sent UTF-8 with an
    explicit `CHARSET UTF-8`."""
    if not needs_literal(query):
        return (None, key, quote(query)), None
    charset = ("CHARSET", "UTF-8") if not query.isascii() else ()
    return (None, *charset, key), query.encode("utf-8")


def uid_search(M, key: str, query: str):
    """`M.uid("SEARCH", ...)` of `<key> <query>`, correctly quoted."""
    args, literal = search_args(key, query)
    if literal is not None:
        M.literal = literal
    return M.uid("SEARCH", *args)


def gmail_search(M, query: str):
    """Gmail `X-GM-RAW` search with the existing plain-`TEXT` fallback when
    the server answers NO. Both forms are quoted by the same rules."""
    typ, data = uid_search(M, "X-GM-RAW", query)
    if typ != "OK":
        typ, data = uid_search(M, "TEXT", query)
    return typ, data


def safe_uid(uid) -> bytes:
    """A message uid is digits only. imaplib joins arguments into the command
    line unquoted, so a uid with CRLF in it is several IMAP commands (MC-1179
    TL19); anything but one run of digits is refused."""
    text = str(uid).strip()
    if not (text.isascii() and text.isdigit()):
        raise ValueError("uid must be a number")
    return text.encode()


def safe_mailbox(name: str) -> str:
    """A mailbox name as an IMAP quoted-string. imaplib sends a bare name as
    separate atoms, so `[Gmail]/All Mail` was BAD and CRLF in a name was an
    injection (MC-1179 TL19). A name that cannot travel quoted is refused."""
    name = str(name)
    if not name or needs_literal(name):
        raise ValueError("mailbox name must be non-empty printable ASCII")
    return quote(name)
