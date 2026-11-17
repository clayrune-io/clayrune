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

Reads keep working as empty meanwhile: a corrupt store degrades to "learned
nothing yet", and nothing is written until a person decides.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from mc.core import _log

ASIDE_MARKER = '.unreadable-'


class StoreUnreadable(RuntimeError):
    """desk.json could not be read and has been set aside; writes are refused."""


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
        _log(f'[desk] store unreadable ({reason}) and could not be set aside: {e}',
             flush=True)
        return None
    _log(f'[desk] store unreadable ({reason}); set aside as {target.name}. '
         f'Writes are refused until a readable {path.name} is restored or an '
         f'explicit empty start.', flush=True)
    return target


def writers_refused(path: Path | None) -> bool:
    return path is not None and not path.exists() and bool(_aside_files(path))


def check_writable(path: Path | None) -> None:
    if writers_refused(path):
        raise StoreUnreadable(
            f'{path.name} was unreadable and has been set aside '
            f'({_aside_files(path)[-1].name}); restore a readable {path.name} or '
            f'start an empty store explicitly before writing.')
