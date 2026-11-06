"""Bounded per-client search leases: newest request cancels older work."""
from __future__ import annotations

import fnmatch
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable

from mc.desk_pieces import PieceError

MAX_SECONDS = 2.0
MAX_CLIENTS = 128
_guard = threading.Lock()


def matcher(query: str) -> Callable[[str], bool]:
    """Name predicate for a search box. Case-insensitive.

    No ``*``/``?`` in the query: plain substring ("mp4" finds "clip.mp4").
    With one: a whole-name glob ("*.mp4" matches names ending in .mp4). The
    pattern is translated to a regex here rather than handed to the OS-aware
    ``fnmatch.fnmatch`` (which would also fold case/separators by platform);
    ``[`` is escaped so a stray bracket in a filename search stays literal.
    """
    query = query.strip().casefold()
    if not query:
        return lambda _name: True
    if '*' not in query and '?' not in query:
        return lambda name: query in name.casefold()
    pattern = re.compile(fnmatch.translate(query.replace('[', '[[]')))
    return lambda name: pattern.match(name.casefold()) is not None


@dataclass
class Lease:
    request_id: str = ''
    deadline: float = field(default_factory=lambda: time.monotonic() + MAX_SECONDS)
    cancel: threading.Event = field(default_factory=threading.Event)
    acquired: bool = False

    def stopped(self) -> bool:
        return self.cancel.is_set() or time.monotonic() >= self.deadline


@dataclass
class Slot:
    lock: threading.Lock = field(default_factory=threading.Lock)
    latest: Lease | None = None
    users: int = 0


_slots: dict[str, Slot] = {}


def cancel(client: str, request_id: str | None = None) -> None:
    with _guard:
        slot = _slots.get(client)
        if slot and slot.latest and (request_id is None or slot.latest.request_id == request_id):
            slot.latest.cancel.set()


@contextmanager
def flight(client: str, request_id: str = ''):
    lease = Lease(request_id=request_id)
    with _guard:
        slot = _slots.get(client)
        if slot is None:
            if len(_slots) >= MAX_CLIENTS:
                raise PieceError('File search is busy. Try again shortly.', 429)
            slot = _slots[client] = Slot()
        if slot.latest:
            slot.latest.cancel.set()
        slot.latest = lease
        slot.users += 1
    try:
        while not lease.stopped():
            if slot.lock.acquire(timeout=0.025):
                lease.acquired = True
                break
        yield lease
    finally:
        if lease.acquired:
            slot.lock.release()
        with _guard:
            slot.users -= 1
            if not slot.users:
                del _slots[client]
