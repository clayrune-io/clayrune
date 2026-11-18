"""Bounded plumbing for a streaming CLI transform (MC-1179 A2).

`ClaudeRuntime.stream_text` read the child's stdout with no limit, left its
stderr pipe undrained (a CLI that fills it blocks itself and the reader), and
ended the CLI with a bare `proc.kill()` that, behind the Windows `.cmd` shim,
stops only the shim. This module owns the two pieces that fix that: a thread
that drains stderr into a bounded tail, and a watchdog that ends the child's
whole tree through the caller's kill function when a total time is exceeded.
"""
from __future__ import annotations

import subprocess
import threading
from typing import Callable

# Total wall-clock for one streamed transform. The only caller today is the
# in-app guide chat; its answers take seconds, so this is a stall bound, not a
# budget. A caller that needs longer passes `timeout=`.
DEFAULT_TOTAL_SECONDS = 600.0

_STDERR_TAIL_CHARS = 4000


class StreamGuard:
    """Drains a child's stderr and ends the child if it runs past a deadline."""

    def __init__(self, proc: subprocess.Popen, kill: Callable[[int], None],
                 seconds: float) -> None:
        self._proc = proc
        self._kill = kill
        self._tail = ''
        self._lock = threading.Lock()
        self.timed_out = False
        self._timer = threading.Timer(max(0.0, seconds), self._expire)
        self._timer.daemon = True
        self._drain = threading.Thread(target=self._drain_stderr,
                                       name='stream-text-stderr', daemon=True)
        self._drain.start()
        self._timer.start()

    def _drain_stderr(self) -> None:
        stream = self._proc.stderr
        if stream is None:
            return
        try:
            # readline, not read(n): a text-mode read(n) waits for n characters,
            # which would hold back a short error line from a child still running.
            for chunk in iter(stream.readline, ''):
                with self._lock:
                    self._tail = (self._tail + chunk)[-_STDERR_TAIL_CHARS:]
        except (OSError, ValueError):
            # The pipe was closed under us during teardown; the tail is kept.
            pass

    def _expire(self) -> None:
        if self._proc.poll() is None:
            self.timed_out = True
            self._kill(self._proc.pid)

    def stderr_tail(self) -> str:
        # Let the drain finish what the child already wrote before reading.
        self._drain.join(timeout=1.0)
        with self._lock:
            return self._tail

    def close(self) -> None:
        self._timer.cancel()
