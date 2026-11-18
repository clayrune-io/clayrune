"""The event id a delegation report carries in its header line (MC-1216).

A report's receipt is found by matching its first line against the parent's
transcript. That line used to be `[dispatched agent finished] <who> (session
<id12>) ended with status=<s>.` -- no turn number. One child finishing several
turns writes the same line for each, so turn:4's delivery (or turn:2's) made
turn:3 read as delivered although it was never written (8e65fec33d4e:turn:3,
2026-11-17). The line now ends with the event id, so the match is exact.

A stored header without the id (a row queued before this change) cannot be
told apart from its siblings: it is never taken as proof of delivery.

Leaf module: imports nothing from mc.
"""
from __future__ import annotations


def tag(event_id: str) -> str:
    return f'[event {event_id}]'


def with_event_id(header: str, event_id: str) -> str:
    """`header` ending in the id tag. Idempotent."""
    return header if header_carries(header, event_id) else f'{header} {tag(event_id)}'


def header_carries(header: str, event_id: str) -> bool:
    """True only when the header line ends with THIS event's id tag."""
    return bool(event_id) and isinstance(header, str) and header.endswith(' ' + tag(event_id))
