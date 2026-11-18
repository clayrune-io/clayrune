"""Who owns a session's `_stdin_write_uncertain` flag after an ack timeout.

`_bounded_stdin_write` (mc/blueprints/agent_routes.py) gives a durable caller
`wait_for_ack` seconds to learn whether its stdin write landed. When that wait
runs out the flag is set: the write's outcome is unknown, so no further durable
write may go into the same pipe until it is known.

MC-1216 (2026-11-17, measured): the flag was set on timeout and never cleared
when the SAME write then completed. A callback handed to Dave at 18:31:20
reached his transcript at 18:31:32, 12 s later, past the 10 s wait. The flag
stayed set, so every later durable follow-up returned "previous stdin write
outcome requires review" without writing anything, and the parent sat in
'running' with nothing driving it until the 18:52 restart; three child reports
waited behind it.

This object ties the flag to the one write whose ack timed out. If that write
later succeeds on the same process, the outcome is known ('written') and the
flag is cleared. A write that fails, a watchdog kill, or a flag another path
set (launch uncertainty, steward delivery) is never cleared here.
"""
from __future__ import annotations

import threading
import uuid
from typing import Any, Optional

FLAG = '_stdin_write_uncertain'
OWNER = '_stdin_write_uncertain_owner'


class AckOwnership:
    """One per write. Serializes 'ack timed out' against 'write finished'."""

    def __init__(self, session: Optional[dict[str, Any]], proc: Any):
        self._session = session
        self._proc = proc
        self._lock = threading.Lock()
        self._finished = False
        self._timed_out = False
        # A string, not this object: session dicts are copied and serialized.
        self._token = uuid.uuid4().hex

    def _same_proc(self) -> bool:
        s = self._session
        return s is not None and s.get('proc', self._proc) is self._proc

    def ack_timed_out(self) -> bool:
        """Called when the ack wait ran out. False if the write finished in
        the meantime (the caller should read the real result instead)."""
        with self._lock:
            if self._finished:
                return False
            self._timed_out = True
            if self._same_proc():
                assert self._session is not None
                # Take ownership only of a flag this timeout is what set. A
                # flag already up came from another path (launch uncertainty,
                # steward delivery) or another write's timeout; this write's
                # late success settles none of those, so it must not clear it.
                if not self._session.get(FLAG):
                    self._session[FLAG] = True
                    self._session[OWNER] = self._token
            return True

    def write_finished(self, ok: bool) -> None:
        """Called by the writer thread when the write attempt ends."""
        with self._lock:
            self._finished = True
            if (ok and self._timed_out and self._same_proc()
                    and self._session is not None
                    and self._session.get(OWNER) == self._token):
                self._session.pop(FLAG, None)
                self._session.pop(OWNER, None)

    def disown(self) -> None:
        """The flag now means something this write cannot settle (watchdog
        kill): a late success must not clear it."""
        with self._lock:
            if self._session is not None and self._session.get(OWNER) == self._token:
                self._session.pop(OWNER, None)
