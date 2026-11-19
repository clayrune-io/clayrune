"""Durable transcript privacy marks with an in-process retry on read recovery."""

import atexit
import json
import threading
from pathlib import Path

from mc.core import _atomic_write_text, _log, now_iso


_lock = threading.RLock()
_pending: dict[Path, dict[str, str]] = {}


def load(path: Path) -> dict[str, str] | None:
    """Read the registry and retry pending marks before returning a snapshot.

    An unreadable file stays in place and returns None so callers fail closed.
    A failed write still returns a snapshot that includes the pending marks.
    """
    with _lock:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError, RecursionError):
            return None
        if not isinstance(data, dict):
            return None

        pending = _pending.get(path)
        if pending:
            missing = {sid: stamp for sid, stamp in pending.items() if sid not in data}
            data.update(missing)
            if missing:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    _atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
                except (OSError, ValueError, RecursionError) as exc:
                    _log(f'[incognito] failed to persist pending transcript marks: {exc}')
                    return data
            del _pending[path]
        return data


def mark(path: Path, session_id: str) -> None:
    with _lock:
        _pending.setdefault(path, {}).setdefault(session_id, now_iso())
        if load(path) is None:
            _log('[incognito] incognito registry is unreadable; leaving it in place')


@atexit.register
def report_unpersisted_marks() -> None:
    with _lock:
        session_ids = {sid for pending in _pending.values() for sid in pending}
    for sid in sorted(session_ids):
        _log(f'[incognito] pending transcript mark at shutdown: {sid}', level='error')
