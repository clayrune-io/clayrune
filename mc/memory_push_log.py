"""Size-bounded append for the mid-task memory push log (ML24, MC-1179).

`mc.memory_push` appends one JSON row per decision to
`data/memory_push_log/<project_id>.jsonl`. Nothing bounded that file: on the
reviewed install it reached 129.6 MB in two months, read by one hand-run tool.

THE BOUND. Before a row is appended, a file already at `MAX_BYTES` is renamed
to `<name>.1` (replacing the previous `.1`) and a new file is started. A
project's log is therefore at most two generations: about `2 * MAX_BYTES`.

A FILE FROM BEFORE THE BOUND IS NOT ROTATED AWAY. A log more than twice
`MAX_BYTES` can only have been written before this module existed. It is
renamed once to `<stem>.pre-cap.jsonl` (or `.pre-cap-2.jsonl`, ...), a name
rotation never writes to, so months of collected rows are not deleted by the
second rotation after an upgrade. Removing it is the operator's call.

Never raises: a failed rotation (the file is open in another program on
Windows, a read-only directory) is logged once per file and the row is
appended anyway. Losing the bound for a while is the cheaper failure; the rows
are the deliverable.

Leaf module: imports nothing from `mc`.
"""
from __future__ import annotations

import json as _json
import os as _os
import threading as _threading
from pathlib import Path as _Path

# One generation. 8 MB is about 17,000 rows at the measured 470 bytes a row.
MAX_BYTES = 8 * 1024 * 1024

# Two stream-reader threads of one project append to the same file. Without
# this, both can see the file at the cap and the second rename would move the
# just-started file over `.1`, deleting a full generation.
_lock = _threading.Lock()
_rotate_warned: set = set()


def _pre_cap_target(path: _Path) -> _Path:
    base = path.with_name(f'{path.stem}.pre-cap{path.suffix}')
    n = 2
    while base.exists():
        base = path.with_name(f'{path.stem}.pre-cap-{n}{path.suffix}')
        n += 1
    return base


def _rotate_if_full(path: _Path, max_bytes: int, log) -> None:
    try:
        size = _os.path.getsize(path)
    except OSError:
        return  # no file yet
    if size < max_bytes:
        return
    try:
        if size > 2 * max_bytes:
            target = _pre_cap_target(path)
            _os.rename(path, target)
            if log is not None:
                log(f"[memory-push] {path.name} was {size // (1024 * 1024)} MB, "
                    f"written before the size bound; kept as {target.name} "
                    f"(not deleted) and a new log started")
        else:
            _os.replace(path, path.with_name(path.name + '.1'))
    except OSError as e:
        if log is not None and str(path) not in _rotate_warned:
            _rotate_warned.add(str(path))
            log(f"[memory-push] could not rotate {path.name} at "
                f"{size // 1024} KB ({e}); appending past the bound")


def append_row(path, row: dict, log=None, max_bytes=None) -> None:
    """Append `row` as one JSON line to `path`, rotating first when the file
    is at the bound. Never raises."""
    p = _Path(path)
    try:
        with _lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            _rotate_if_full(p, MAX_BYTES if max_bytes is None else max_bytes, log)
            with open(p, 'a', encoding='utf-8') as f:
                f.write(_json.dumps(row, ensure_ascii=False) + '\n')
    except Exception as e:
        if log is not None:
            log(f"[memory-push] log write failed for {p.name}: {e}")
