"""Serialize terminal events behind a process's initial stdin acknowledgment.

Pending events never block the stdout reader. The thread that accepts delivery
or submits the next accepted event drains the queue; callbacks run without the
queue lock, so they can take the project's lifecycle lock safely.
"""
from collections import deque
import threading
from typing import Callable


class TerminalDeliveryGate:
    def __init__(self, on_error: Callable[[Exception], None]):
        self._lock = threading.Lock()
        self._accepted: bool | None = None
        self._events: deque[Callable[[], None]] = deque()
        self._draining = False
        self._on_error = on_error

    def submit(self, event: Callable[[], None]) -> None:
        with self._lock:
            if self._accepted is False:
                return
            self._events.append(event)
        self._drain()

    def accept(self) -> None:
        with self._lock:
            if self._accepted is not None:
                return
            self._accepted = True
        self._drain()

    def reject(self) -> None:
        with self._lock:
            if self._accepted is not None:
                return
            self._accepted = False
            self._events.clear()

    def _drain(self) -> None:
        with self._lock:
            if self._accepted is not True or self._draining:
                return
            self._draining = True
        while True:
            with self._lock:
                if not self._events:
                    self._draining = False
                    return
                event = self._events.popleft()
            try:
                event()
            except Exception as exc:
                # An accepted write stays accepted even if its terminal handler
                # fails. Report it and continue, including queued EOF cleanup.
                self._on_error(exc)
