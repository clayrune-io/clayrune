"""An unreadable desk.json is set aside, never replaced (MC-1187 DK7).

`desk._read_store` used to read a half-written or non-object `desk.json` as an
empty store, and the next `_write_store` replaced the file with that empty
store: every voice, campaign, piece and ledger row gone, no trace. The model for
the fix is the channel-impact store in this package: say the record is bad, keep the bytes.

Two rules, both here so `desk.py` does not grow another concern:

* On a parse failure or a non-object, the file is MOVED to
  `desk.json.unreadable-<UTC stamp>` next to it and never touched again. The
  name ends in neither `.json` nor `.jsonl` and does not match the
  `.<name>.tmp<N>` shape the orphan-tmp sweeper deletes, so no glob that lists
  stores, project records, or backs up records picks it up.
* Writers refuse (`StoreUnreadable`) while `desk.json` is missing AND a set-aside
  file exists. That state survives a restart with no extra marker file, and ends
  the moment a readable `desk.json` is back or `start_empty_store` is called.
* The move can fail (on Windows, another process holds the file). The unreadable
  `desk.json` then stays where it is, and writers refuse the same way for as long
  as it is still unreadable: it is remembered in `_unmoved` for this process, and a
  restart reads it, fails, and tries the move again. A readable file there lifts it.

Reads keep working as empty meanwhile: a corrupt store degrades to "learned
nothing yet", and nothing is written until a person decides.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from mc.core import _log

ASIDE_MARKER = '.unreadable-'
_unmoved: set[Path] = set()     # stores that were unreadable and could not be moved aside


class StoreUnreadable(RuntimeError):
    """desk.json could not be read and has been set aside; writes are refused.

    `file_name` is the unreadable file's name, no directory: the set-aside copy, or
    `desk.json` itself when it could not be moved."""

    def __init__(self, message: str, file_name: str = ''):
        super().__init__(message)
        self.file_name = file_name


def _aside_files(path: Path) -> list[Path]:
    try:
        return sorted(path.parent.glob(path.name + ASIDE_MARKER + '*'))
    except OSError:
        return []


def set_aside(path: Path, reason: str) -> Path | None:
    """Move an unreadable store out of the way. Never overwrites an earlier one."""
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target = path.with_name(path.name + ASIDE_MARKER + stamp)
    n = 1
    while target.exists():
        n += 1
        target = path.with_name(f'{path.name}{ASIDE_MARKER}{stamp}-{n}')
    try:
        path.rename(target)
    except OSError as e:
        _unmoved.add(path)
        _log(f'[desk] store unreadable ({reason}) and could not be set aside: {e}. '
             f'Writes are refused while {path.name} stays unreadable.', flush=True)
        return None
    _unmoved.discard(path)
    _log(f'[desk] store unreadable ({reason}); set aside as {target.name}. '
         f'Writes are refused until a readable {path.name} is restored or an '
         f'explicit empty start.', flush=True)
    return target


def _still_unreadable(path: Path) -> bool:
    try:
        return not isinstance(json.loads(path.read_text(encoding='utf-8')), dict)
    except ValueError:
        return True
    except OSError:
        return True      # cannot even read it: not proof it is good, so keep refusing


def _unmoved_and_bad(path: Path) -> bool:
    """True while `path` is a remembered unmovable store that is still unreadable."""
    if path not in _unmoved:
        return False
    if path.exists() and _still_unreadable(path):
        return True
    _unmoved.discard(path)           # gone or readable again: the aside rule decides from here
    return False


def writers_refused(path: Path | None) -> bool:
    if path is None:
        return False
    return _unmoved_and_bad(path) or (not path.exists() and bool(_aside_files(path)))


def check_writable(path: Path | None) -> None:
    if path is None:
        return
    if _unmoved_and_bad(path):
        raise StoreUnreadable(
            f'{path.name} is unreadable and could not be moved aside (another '
            f'process may hold it); it has not been touched. Restore a readable '
            f'{path.name} before writing.', path.name)
    if writers_refused(path):
        aside = _aside_files(path)[-1].name
        raise StoreUnreadable(
            f'{path.name} was unreadable and has been set aside '
            f'({aside}); restore a readable {path.name} or '
            f'start an empty store explicitly before writing.', aside)
