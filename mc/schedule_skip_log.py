"""Per-schedule log of ticks that were skipped, and why.

A skipped tick has no agent_log row (no agent ever started), so the Runs panel
would show nothing and a condition that never fires would be indistinguishable
from a broken schedule. This is the missing half of the run history.

Lives next to ``schedules.json`` (OUTSIDE ``DATA_DIR``, so the project-records
loader never sees it). Bounded per schedule; newest first. Never raises: a
history write must not take down the scheduler tick that produced it.
"""

from __future__ import annotations

from pathlib import Path

from mc.atomic_json import write_json_atomic
from mc.core import _log, now_iso

FILENAME = 'schedule_skips.json'
MAX_PER_SCHEDULE = 50


def path_for(schedules_path: Path) -> Path:
    return Path(schedules_path).parent / FILENAME


def _load(path: Path) -> dict:
    import json
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        _log(f'[scheduler] skip log unreadable ({e}); starting empty')
        return {}


def record(schedules_path: Path, schedule_id: str, reason: str, kind: str) -> None:
    try:
        path = path_for(schedules_path)
        data = _load(path)
        rows = data.get(schedule_id, [])
        rows.insert(0, {'ts': now_iso(), 'kind': kind, 'reason': reason})
        data[schedule_id] = rows[:MAX_PER_SCHEDULE]
        write_json_atomic(path, data, indent=2, ensure_ascii=False)
    except Exception as e:
        _log(f'[scheduler] could not record skip for {schedule_id}: {e}')


def list_for(schedules_path: Path, schedule_id: str, limit: int = MAX_PER_SCHEDULE) -> list:
    return _load(path_for(schedules_path)).get(schedule_id, [])[:limit]


def forget(schedules_path: Path, schedule_id: str) -> None:
    try:
        path = path_for(schedules_path)
        data = _load(path)
        if data.pop(schedule_id, None) is not None:
            write_json_atomic(path, data, indent=2, ensure_ascii=False)
    except Exception as e:
        _log(f'[scheduler] could not clear skips for {schedule_id}: {e}')
