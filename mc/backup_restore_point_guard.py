"""Which restore-point directory a project id and snap id may name (MC-1179
pass 07, P28).

``project_id`` and ``snap_id`` arrive as URL segments and used to go straight
into ``restore_points_dir / project_id / snap_id``. On Windows a segment can
hold backslashes, and ``..`` works on every platform, so the path could land
anywhere, and DELETE ``rmtree``d it if it held a ``manifest.json``.

The rule, in two independent halves (either alone would have been enough for
the measured attack; both together mean a new caller cannot reopen it):

* ``snap_id`` must match the stamp-and-slug shape ``mc.backup`` writes
  (``%Y%m%dT%H%M%S%f-<slug>[-n]``). No dots, separators or drive letters can
  match it.
* The resolved directory must be a DIRECT child of the directory it is meant
  to sit in (restore-points root for a project, project dir for a snap), so a
  junction or odd segment that still resolves elsewhere is refused too.

A project id is accepted when it is a registered project, or when a
directory for it already exists under the restore-points root: rollback
exists partly to recover a project whose record was deleted, and that
project's points must stay reachable. Nothing outside the restore-points
root is ever accepted either way.

Pure path logic, no ``mc.backup`` import (``mc.backup`` imports this).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable, Iterable, Optional

# 8-digit date, 'T', 12 digits (HHMMSS + microseconds), '-', slug. The slug is
# mc.backup._slugify output (<=40 of alnum / '-' / '_'), optionally followed by
# the '-<n>' same-microsecond suffix, so allow a little headroom. \w is
# str.isalnum() plus '_', which is exactly what _slugify keeps.
_SNAP_ID = re.compile(r'^\d{8}T\d{12}-[\w-]{1,64}$')


def snap_id_refusal(snap_id: object) -> Optional[str]:
    """Reason ``snap_id`` is not a name this module could have written."""
    if not isinstance(snap_id, str) or not _SNAP_ID.match(snap_id):
        return f"'{snap_id}' is not a restore point id"
    return None


def _direct_child(parent: Path, name: str) -> Optional[Path]:
    """``parent / name`` if it resolves to a direct child of ``parent``."""
    try:
        base = parent.resolve()
        cand = (parent / name).resolve()
    except (OSError, ValueError):
        return None
    if os.path.normcase(str(cand.parent)) != os.path.normcase(str(base)):
        return None
    # The segment must BE the directory name: 'a\..\b' resolves to a
    # direct child 'b' but is not 'b'.
    if os.path.normcase(cand.name) != os.path.normcase(name):
        return None
    return cand


def project_dir(root: Path, project_id: object,
                registered_ids: Callable[[], Iterable[str]], *,
                require_known: bool = True) -> Path:
    """The per-project restore-point directory, or ``ValueError`` (message is
    the reason). ``root`` is the restore-points directory. ``registered_ids``
    is only called when the directory does not exist yet (it reads every
    project record, and delete loops call this once per point)."""
    if not isinstance(project_id, str) or not project_id:
        raise ValueError('project id is empty or not a string')
    cand = _direct_child(root, project_id)
    if cand is None:
        raise ValueError(f"'{project_id}' is not a project restore-point directory")
    # require_known=False is for LIST only: an unknown but well-formed id has
    # nothing on disk, so the listing is empty rather than an error.
    if require_known and not cand.is_dir() and project_id not in set(registered_ids()):
        raise ValueError(f"no registered project '{project_id}' and no restore points for it")
    return cand


def snap_dir(root: Path, project_id: object, snap_id: object,
             registered_ids: Callable[[], Iterable[str]]) -> Path:
    """The directory of one restore point, or ``ValueError``."""
    refusal = snap_id_refusal(snap_id)
    if refusal:
        raise ValueError(refusal)
    pdir = project_dir(root, project_id, registered_ids)
    cand = _direct_child(pdir, str(snap_id))
    if cand is None:
        raise ValueError(f"'{snap_id}' is not a restore point directory")
    return cand
