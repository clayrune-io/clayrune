"""Reading a `<project>_skill_stats.json` sidecar without ever mistaking
"could not read it" for "it is empty" (ML8, rail 3: "no" must be durable).

The sidecar holds the Distiller's recorded rejections (`suppressions`), its
proposal outbox and its signal window. Every writer is a read-modify-write
under the per-project lock, so a reader that answers the empty schema for an
unparseable file hands the next writer an empty store to save over it. That
was `distiller._read_skill_stats` until this module: the next counter bump,
which runs on every dispatch, replaced the file and every recorded "no" was
gone with nothing kept.

Two different failures, kept apart because the right response differs:

* `corrupt=True`: the bytes were read and are not a JSON object (a torn or
  zero-filled write, a list, `null`, bad UTF-8). A WRITER moves the file aside
  to `<name>.corrupt-<utc stamp>`, byte for byte, and starts a fresh store.
  The kept copy is for a human; nothing reads it back.
* `corrupt=False`: the read itself failed (a lock, a permission). The file may
  be perfectly good, so nobody moves it and nobody writes over it.

A read-only caller gets the exception either way and decides its own
fail-closed answer; it never moves or writes anything.

The kept copy's name does not end in `.json`, so `load_projects()`'s
`DATA_DIR.glob('*.json')` cannot take it for a project record (the DATA_DIR
rule in CLAUDE.md).

Leaf module: standard library only.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, Optional

Log = Optional[Callable[[str], object]]


class SkillStatsUnreadable(Exception):
    """The sidecar exists and its contents could not be used."""

    def __init__(self, path: Path, reason: str, *, corrupt: bool):
        super().__init__(f'{path.name}: {reason}')
        self.path = path
        self.reason = reason
        self.corrupt = corrupt


# path -> the reason last logged for it. A store that stays unreadable is read
# on every dispatch; one line per distinct failure is the signal, not a flood.
_logged: dict[str, str] = {}


def _log_once(log: Log, path: Path, reason: str, what: str) -> None:
    if _logged.get(str(path)) == reason:
        return
    _logged[str(path)] = reason
    if log:
        log(f"[distiller] skill_stats unreadable ({what}): {path.name}: {reason}")


def load(path: Path, *, log: Log = None) -> Optional[dict]:
    """The parsed sidecar, or None when there is no file.

    Raises SkillStatsUnreadable (logged once per distinct failure) for
    anything else. An empty file reads as `{}`, as it always has.
    """
    try:
        raw = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return None
    except UnicodeDecodeError as e:
        _log_once(log, path, f'not UTF-8 ({e.reason})', 'corrupt')
        raise SkillStatsUnreadable(path, f'not UTF-8 ({e.reason})', corrupt=True) from e
    except OSError as e:
        reason = f'read failed ({e.__class__.__name__}: {e.strerror or e})'
        _log_once(log, path, reason, 'left in place, not written over')
        raise SkillStatsUnreadable(path, reason, corrupt=False) from e
    try:
        data = json.loads(raw or '{}')
    except ValueError as e:
        _log_once(log, path, f'not JSON ({e})', 'corrupt')
        raise SkillStatsUnreadable(path, f'not JSON ({e})', corrupt=True) from e
    if not isinstance(data, dict):
        reason = f'JSON {type(data).__name__}, not an object'
        _log_once(log, path, reason, 'corrupt')
        raise SkillStatsUnreadable(path, reason, corrupt=True)
    _logged.pop(str(path), None)
    return data


def quarantine(path: Path, *, log: Log = None) -> Path:
    """Move a corrupt sidecar aside, byte for byte, and return where it went.

    Never replaces an earlier kept copy. Raises OSError if the move fails; the
    caller must then NOT write, or the only copy of the old bytes is lost.
    """
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    target = path.with_name(f'{path.name}.corrupt-{stamp}')
    n = 1
    while target.exists():
        n += 1
        target = path.with_name(f'{path.name}.corrupt-{stamp}-{n}')
    os.rename(path, target)
    _logged.pop(str(path), None)
    if log:
        log(f"[distiller] skill_stats corrupt: kept {path.name} as {target.name} "
            f"and started a fresh store. Recorded suppressions, the outbox and "
            f"the signal window are in the kept file, not in the new one.")
    return target


def start_fresh_after(err: SkillStatsUnreadable, *, log: Log = None) -> None:
    """For a read-modify-write caller that holds the store's lock and just
    caught `err` from its read.

    Returns only when the corrupt file has been moved aside, so the caller may
    go on with the empty schema. Re-raises when the file merely could not be
    read, and raises when it could not be moved: in both cases the caller's
    write must not happen.
    """
    if not err.corrupt:
        raise err
    try:
        quarantine(err.path, log=log)
    except OSError as move_err:
        raise SkillStatsUnreadable(
            err.path, f'{err.reason}; could not be moved aside ({move_err})',
            corrupt=False) from move_err
