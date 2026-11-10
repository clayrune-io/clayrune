"""Shared agent-log transactions for the server and offline repair tools."""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Iterator

from mc.atomic_json import read_text_with_retry, write_json_atomic
from mc.delivery_attempt import DeliveryAttempt, pending_fields

_guard = threading.Lock()
_locks: dict[str, Any] = {}
_held = threading.local()


@contextmanager
def transaction(path: Path) -> Iterator[None]:
    """ReviewStore's OS byte-lock pattern; no transcript/model I/O here.

    The .lock sidecar is not JSON, so project discovery never treats it as a
    project. Closing releases the OS lock, including after a process crash.
    """
    key = os.path.normcase(str(path.resolve()))
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_held, 'paths', set())
        if key in held:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix('.lock').open('a+b') as stream:
            if stream.tell() == 0:
                stream.write(b'0')
                stream.flush()
            deadline = time.monotonic() + 10
            while True:
                try:
                    stream.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)
            _held.paths = held | {key}
            try:
                yield
            finally:
                _held.paths = held


def claim_rows(rows: list[dict]) -> list[dict]:
    for row in rows:
        if not row.get('delivery_attempt_id'):
            row.update(pending_fields({}))
    return deepcopy(rows)


def merge_rows(rows: list[dict], before: list[dict], after: list[dict]) -> int:
    original = {r['delivery_attempt_id']: r for r in before}
    inserted_any = False
    applied = 0
    for changed in after:
        token = changed.get('delivery_attempt_id')
        prior = original.get(token)
        if prior is None:
            csid = changed.get('claude_session_id')
            if (csid and changed.get('synthesized') and not changed.get('session_id')
                    and not any(r.get('claude_session_id') == csid for r in rows)):
                inserted = deepcopy(changed)
                inserted.update(pending_fields({}))
                rows.insert(0, inserted)
                inserted_any = True
                applied += 1
            continue
        if prior == changed or not isinstance(token, str) or not token:
            continue
        current = next((r for r in rows if DeliveryAttempt(token).owns_row(r)), None)
        if current is not None and current == prior:
            current.update(deepcopy(changed))
            applied += 1
    if inserted_any:
        rows.sort(key=lambda r: r.get('ts', ''), reverse=True)
    return applied


def snapshot(path: Path) -> list[dict]:
    """Offline --apply claim, before potentially slow transcript reads."""
    with transaction(path):
        rows = json.loads(read_text_with_retry(path))
        before = claim_rows(rows)
        write_json_atomic(path, rows, indent=2, ensure_ascii=False)
        return before


def commit(path: Path, before: list[dict], after: list[dict]) -> int:
    with transaction(path):
        rows = json.loads(read_text_with_retry(path))
        applied = merge_rows(rows, before, after)
        write_json_atomic(path, rows, indent=2, ensure_ascii=False)
        return applied
