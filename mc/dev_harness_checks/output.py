"""Bounded ephemeral stdout capture; stderr remains DEVNULL, disk logs stay empty."""
from __future__ import annotations

import threading
from typing import Any

from mc.dev_harness_checks.reports import MAX_REPORT_BYTES


class Capture:
    def __init__(self, stream: Any):
        self.stream = stream
        self.data = bytearray()
        self.invalid = False
        self.thread = threading.Thread(target=self._read, daemon=True, name='harness-report')
        self.thread.start()

    def _read(self) -> None:
        try:
            while chunk := self.stream.read(65536):
                if len(self.data) + len(chunk) <= MAX_REPORT_BYTES and not self.invalid:
                    self.data.extend(chunk)
                else:
                    self.invalid = True
                    self.data.clear()
        except (OSError, ValueError):
            self.invalid = True
            self.data.clear()
        finally:
            self.stream.close()

    def take(self) -> bytes:
        self.thread.join(timeout=2)
        if self.thread.is_alive() or self.invalid:
            self.data.clear()
            raise ValueError('native_output_limit')
        result = bytes(self.data)
        self.data.clear()
        return result
