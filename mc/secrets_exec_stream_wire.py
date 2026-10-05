"""Wire format shared by the streaming server-exec route and its client (MC-1047).

``tools/with-secret.py --raw`` on a passphrase-locked vault cannot read the vault in
its own process, so it asks the server to parent the child and relays stdin/stdout/
stderr over loopback HTTP. The server's half is ``mc/secrets_exec_stream.py`` and
``mc/blueprints/secrets_exec_stream_routes.py``; the CLI's half is
``mc/secrets_exec_stream_client.py``. This file is only what both must agree on.

Output comes back on one long response as frames: ``channel`` (1 byte), ``length``
(4 bytes, big-endian), ``payload``. stdin goes the other way as separate POSTs, so
neither direction waits on the other.
"""
from __future__ import annotations

import struct
from typing import Callable

PATH = '/api/secrets/exec-stream'

CH_HEARTBEAT = 0   # empty; lets the server notice a client that has gone away
CH_STDOUT = 1
CH_STDERR = 2
CH_EXIT = 3        # payload: signed 32-bit exit code; always the last frame
CH_ERROR = 4       # payload: utf-8 text; the server ended the session itself
CH_SESSION = 5     # payload: ascii session id; always the first frame

MAX_FRAME = 1 << 20   # a frame larger than this is a corrupt stream, not data
_HEAD = struct.Struct('>BI')


def pack(channel: int, payload: bytes = b'') -> bytes:
    return _HEAD.pack(channel, len(payload)) + payload


def pack_exit(code: int) -> bytes:
    return pack(CH_EXIT, struct.pack('>i', code))


def unpack_exit(payload: bytes) -> int:
    return struct.unpack('>i', payload)[0]


def read_frame(read: Callable[[int], bytes]) -> tuple[int, bytes] | None:
    """One frame from ``read(n)`` (which returns exactly ``n`` bytes, or fewer at
    end of stream). None at a clean end of stream; ``ValueError`` if the stream ends
    mid-frame or announces an impossible length."""
    head = read(_HEAD.size)
    if not head:
        return None
    if len(head) < _HEAD.size:
        raise ValueError('stream ended inside a frame header')
    channel, length = _HEAD.unpack(head)
    if length > MAX_FRAME:
        raise ValueError('frame too large')
    payload = read(length) if length else b''
    if len(payload) < length:
        raise ValueError('stream ended inside a frame')
    return channel, payload
