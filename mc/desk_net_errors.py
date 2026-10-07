"""Network failures against an engine: what they mean and when to retry (MC-1066).

A transport failure says very different things depending on how far the request
got. If it never left this machine (DNS miss, refused, network unreachable) the
vendor cannot have acted, so retrying is safe even for a paid submit and the
user can be told nothing was sent. If it failed after the bytes may have gone
out (read timeout, reset mid-stream) the vendor may have acted: retrying is
safe only for a call that is free, and a paid submit must report the result as
unknown.

`run()` owns the retry loop; `desk_engines` passes `free` explicitly from the
call site, never inferred from the payload. This module imports no engine code.
"""
from __future__ import annotations

import errno
import socket
import time
import urllib.error
import urllib.parse
from typing import Callable, TypeVar

NOT_SENT = 'not_sent'
MAYBE_SENT = 'maybe_sent'

BACKOFF = (0.5, 1.5)        # seconds before retry 1 and retry 2: two retries, three tries in all

# Winsock codes for "network down / unreachable / host unreachable".
_UNREACHABLE = {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN, 10050, 10051, 10065}

# What a transport attempt may raise; anything else is a bug and propagates.
TRANSPORT_ERRORS = (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError, ValueError)

_T = TypeVar('_T')


class AfterSend(Exception):
    """Wraps a failure raised while reading the response: the request was
    already sent, whatever the wrapped exception's type says."""

    def __init__(self, original: BaseException):
        super().__init__(str(original))
        self.original = original


class NetFailure(Exception):
    """A transport failure that retries could not clear. `kind` is NOT_SENT or
    MAYBE_SENT; `original` is the raw exception (log it, never show it)."""

    def __init__(self, kind: str, original: BaseException, attempts: int):
        super().__init__(str(original))
        self.kind = kind
        self.original = original
        self.attempts = attempts


def _root(exc: BaseException) -> BaseException:
    while isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, BaseException):
        exc = exc.reason
    return exc


def _unreachable(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and (
        exc.errno in _UNREACHABLE or getattr(exc, 'winerror', None) in _UNREACHABLE)


def classify(exc: BaseException) -> str:
    """NOT_SENT when the request never left this machine, otherwise MAYBE_SENT."""
    if isinstance(exc, AfterSend):
        return MAYBE_SENT
    root = _root(exc)
    if isinstance(root, (socket.gaierror, ConnectionRefusedError)) or _unreachable(root):
        return NOT_SENT
    return MAYBE_SENT


def cause(exc: BaseException) -> str:
    """A few plain words for why, for the user-facing message."""
    root = _root(exc.original if isinstance(exc, AfterSend) else exc)
    if isinstance(root, socket.gaierror):
        return 'network lookup failed'
    if isinstance(root, ConnectionRefusedError):
        return 'connection refused'
    if _unreachable(root):
        return 'network unreachable'
    if isinstance(root, (TimeoutError, socket.timeout)):
        return 'timed out'
    if isinstance(root, ConnectionError):
        return 'connection dropped'
    return 'connection failed'


def service_name(url: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or '').lower() if url else ''
    for suffix, name in (('higgsfield.ai', 'Higgsfield'), ('googleapis.com', 'Google'),
                         ('openai.com', 'OpenAI')):
        if host == suffix or host.endswith('.' + suffix):
            return name
    return 'the engine'


def message(fail: NetFailure, service: str, *, free: bool) -> str:
    """The sentence the user sees when retries ran out. No raw exception text."""
    why = cause(fail.original)
    if fail.kind == NOT_SENT:
        return f'Could not reach {service} ({why}). Nothing was sent or charged.'
    if free:
        return f'Could not get a reply from {service} ({why}). That was a read-only check, so it cost nothing.'
    return f'No answer from {service} after the request was sent ({why}); whether it went through is unknown.'


def _retryable(kind: str, exc: BaseException, free: bool) -> bool:
    if kind == NOT_SENT:
        return True
    # A paid call is never repeated once it may have been sent. ValueError is a
    # deterministic refusal (response too large), not a transient fault.
    return free and not isinstance(_root(exc.original if isinstance(exc, AfterSend) else exc), ValueError)


def run(do: Callable[[], _T], *, free: bool, deadline: float | None = None,
        log: Callable[[str], None] | None = None, label: str = '') -> _T:
    """Call `do()`, retrying transport failures per the class above. `do` raises
    the raw exception for one attempt. Raises NetFailure when retries run out or
    the failure may not be repeated. `deadline` (a `time.monotonic()` value)
    stops retrying so a caller's overall time budget is never exceeded."""
    tries = 1 + len(BACKOFF)
    for n in range(tries):
        try:
            return do()
        except TRANSPORT_ERRORS + (AfterSend,) as e:
            kind = classify(e)
            if log:
                log(f'[desk_net] {label} attempt {n + 1}/{tries} failed ({kind}): {type(_root(e)).__name__}: {e}')
            last = n == tries - 1
            if last or not _retryable(kind, e, free):
                raise NetFailure(kind, e.original if isinstance(e, AfterSend) else e, n + 1) from e
            delay = BACKOFF[n]
            if deadline is not None and time.monotonic() + delay >= deadline:
                raise NetFailure(kind, e.original if isinstance(e, AfterSend) else e, n + 1) from e
            time.sleep(delay)
    raise AssertionError('unreachable')     # pragma: no cover
