"""Bound MCP response reads even when an SSE peer keeps sending heartbeats."""
from __future__ import annotations

import time
from typing import Iterator


def chunks(response, *, deadline: float, maximum: int) -> Iterator[bytes]:
    stream = response
    sock = None
    # urllib: HTTPResponse.fp -> BufferedReader.raw -> SocketIO._sock;
    # HTTPError adds one fp wrapper. No new connection or endpoint is created.
    for _ in range(4):
        sock = getattr(stream, '_sock', None)
        if sock is not None:
            break
        stream = getattr(stream, 'raw', None) or getattr(stream, 'fp', None)
        if stream is None:
            break
    reader = (getattr(response, 'read1', None)
              or getattr(getattr(response, 'fp', None), 'read1', None) or response.read)
    seen = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('MCP response deadline exceeded')
        if sock is not None:
            sock.settimeout(remaining)
        # read1 performs at most one underlying read, so heartbeats cannot reset
        # the overall deadline. BytesIO/test transports also support read1.
        part = reader(min(64 * 1024, maximum + 1 - seen))
        if time.monotonic() >= deadline:
            raise TimeoutError('MCP response deadline exceeded')
        if not part:
            return
        seen += len(part)
        if seen > maximum:
            raise ValueError('response too large')
        yield part


def lines(response, *, deadline: float, maximum: int) -> Iterator[bytes]:
    pending = b''
    for part in chunks(response, deadline=deadline, maximum=maximum):
        pending += part
        while b'\n' in pending:
            line, pending = pending.split(b'\n', 1)
            yield line
    if pending:
        yield pending
