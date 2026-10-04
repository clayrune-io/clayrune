"""A LIFO undo stack for the local writes of one Save (docs/DESK_CONNECT_BY_URL_SPEC.md,
Dave's 2026-10-03 ruling: compensating rollback, no transaction coordinator).

Each write pushes the call that undoes it; if a later write fails the caller
`unwind()`s and the writes come back out in reverse order. An undo that itself
fails is logged (the exception CLASS only, never its text, which could echo a
value) and reported by name so the human is told what to remove by hand.
"""
from __future__ import annotations

from typing import Callable

from mc.core import _log


class UndoStack:
    def __init__(self) -> None:
        self._steps: list[tuple[str, Callable[[], object]]] = []

    def push(self, what: str, undo: Callable[[], object]) -> None:
        """`what` is a short non-secret label ("vault entry gemini-api")."""
        self._steps.append((what, undo))

    def __len__(self) -> int:
        return len(self._steps)

    def unwind(self) -> list[str]:
        """Undo everything, newest first. Returns the labels of undos that failed
        (so something is left behind); empty when everything came out clean."""
        left = []
        while self._steps:
            what, undo = self._steps.pop()
            try:
                undone = undo()
            except Exception as e:
                _log(f'[desk_connect] undo of {what} failed: {type(e).__name__}', flush=True)
                left.append(what)
                continue
            if undone is False:
                left.append(what)
        return left

    def commit(self) -> None:
        """Everything held; forget the undos."""
        self._steps.clear()
