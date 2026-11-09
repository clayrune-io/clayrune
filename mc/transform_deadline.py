"""Optional total monotonic deadline for multi-leg text transforms."""
from __future__ import annotations

import time
from typing import Optional


def check(deadline: Optional[float]) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError('text transform deadline exceeded')


def remaining(timeout: float, deadline: Optional[float], *, fallback: bool = False) -> float:
    """Legacy callers retain their full timeout on each leg."""
    if deadline is None:
        return timeout
    seconds = deadline - time.monotonic()
    # The API transport floors its timeout at one second; do not round up a
    # smaller remaining budget. The CLI fallback requires a three-second floor.
    if seconds < (3 if fallback else 1):
        raise TimeoutError('text transform deadline exhausted')
    return min(timeout, seconds)
