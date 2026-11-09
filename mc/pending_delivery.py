"""Carry stdin acknowledgments past the outermost guarded publication window.

Nested launch helpers join their caller's boundary. Only its owner awaits,
after all nested project/lifecycle locks have exited, including error paths.
The context is reset before settling so an accepted terminal retry has its own
delivery outcome rather than silently joining the already-returned launch.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Callable, Iterator


_pending: ContextVar[list[Callable[[], None]] | None] = ContextVar('pending_delivery', default=None)


def complete_or_defer(acknowledge: Callable[[], None]) -> None:
    pending = _pending.get()
    if pending is None:
        acknowledge()
    else:
        pending.append(acknowledge)


@contextmanager
def publication_boundary() -> Iterator[None]:
    if _pending.get() is not None:
        yield
        return
    pending: list[Callable[[], None]] = []
    token = _pending.set(pending)
    try:
        yield
    finally:
        _pending.reset(token)
        first_error = None
        for acknowledge in pending:
            try:
                acknowledge()
            except Exception as exc:
                # Settle every launched process even when an earlier one fails.
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error


def after_publication(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        with publication_boundary():
            return fn(*args, **kwargs)
    return run
