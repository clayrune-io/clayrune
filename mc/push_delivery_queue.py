"""Where push delivery runs, and what a failed delivery may log (MC-1179 SF15).

`_handle_push_signal` is called from the agent stream-reader threads, once per
`result` message. It used to call `webpush` inline for every subscribed device
with no timeout, so a push service that accepted the connection and never
answered froze the reader that read the CLI's output for good.

Delivery now goes through ONE background worker fed by a bounded queue:

- FIFO, so pushes arrive in the order the turns finished; a stalled endpoint
  delays only the pushes behind it, never a reader.
- At most `MAX_PENDING` pending pushes. When full the NEW push is dropped and
  one line says so. `submit` never blocks the caller and never raises.
- The worker is a daemon thread started on first use. Pending pushes do not
  survive process exit; a push is a nudge, not a record (the Inbox row is
  written by `_notify_push` when the job runs).

Failure logs name the subscriber by endpoint HOST only. A push endpoint's path
is the subscription's capability and is never logged; neither is any key.
"""
from __future__ import annotations

import queue
import threading
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlsplit

from mc.core import _log

MAX_PENDING = 200

_STOP = object()


class DeliveryQueue:
    def __init__(self, max_pending: int = MAX_PENDING):
        self.max_pending = max_pending
        self._q: "queue.Queue[Any]" = queue.Queue(maxsize=max_pending)
        self._start_lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None

    def submit(self, job: Callable[[], Any]) -> bool:
        """Queue `job` for the worker. True if queued, False if dropped
        because the queue was full. Never blocks, never raises."""
        try:
            self._ensure_worker()
            self._q.put_nowait(job)
            return True
        except queue.Full:
            _log(f"[push] delivery queue full ({self.max_pending} pending); "
                 "dropped one push", flush=True)
        except Exception as e:  # a failed thread start must not reach a reader
            _log(f"[push] could not queue a push: {type(e).__name__}: {e}", flush=True)
        return False

    def stop(self, timeout: float = 5.0) -> None:
        """Ask the worker to exit once it has drained what is queued (tests)."""
        t = self.thread
        if t is None or not t.is_alive():
            return
        try:
            self._q.put_nowait(_STOP)
        except queue.Full:
            return
        t.join(timeout)

    def _ensure_worker(self) -> None:
        t = self.thread
        if t is not None and t.is_alive():
            return
        with self._start_lock:
            t = self.thread
            if t is not None and t.is_alive():
                return
            t = threading.Thread(target=self._run, name='push-delivery', daemon=True)
            self.thread = t
            t.start()

    def _run(self) -> None:
        while True:
            job = self._q.get()
            if job is _STOP:
                return
            try:
                job()
            except Exception as e:  # one bad job must not end the worker
                _log(f"[push] delivery job failed: {type(e).__name__}: {e}", flush=True)


def endpoint_host(endpoint: Any) -> str:
    """The host of a push endpoint, or '?' when it has none."""
    try:
        return urlsplit(str(endpoint or '')).hostname or '?'
    except Exception:
        return '?'


def redact(text: Any, endpoint: Any, secrets: Iterable[Any] = ()) -> str:
    """`text` with the endpoint, its path/query and any `secrets` (key values)
    removed. Library errors echo the URL ('... with url: /wpush/v2/<token>'),
    and the path is the secret part."""
    out = str(text)
    ep = str(endpoint or '')
    needles = []
    if ep:
        needles.append(ep)
        try:
            parts = urlsplit(ep)
            tail = parts.path + (('?' + parts.query) if parts.query else '')
            if len(tail) > 1:
                needles.append(tail)
        except Exception:
            pass
    needles.extend(str(x) for x in secrets if isinstance(x, str) and len(x) >= 8)
    for n in needles:
        out = out.replace(n, '<redacted>')
    return out
