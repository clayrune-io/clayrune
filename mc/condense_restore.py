"""Agent-mode condense: putting MEMORY.md back after a bad run (MC-1179 ML21).

An agent-mode condense is an external `claude -p` process that rewrites
MEMORY.md with its own Write tool. `memory._condense_integrity_check` compares
the file after the run with a pre-image taken before it, and asks for one of
two repairs: `restore` (write the pre-image back) or `heal` (re-inject the
watermark lines the agent dropped).

Both repairs used to be a plain `write_text`, outside the per-project memory
write lock, of text decided from a read taken a moment earlier. A session
commit that landed in that gap was overwritten, and a crash mid-write left a
truncated index.

What this module owns:

- `EndImage`: MEMORY.md is read ONCE, the moment the agent run ends. The
  integrity check judges that text (`replay`), and the repair is conditional
  on it.
- `restore` / `heal`: take the same per-project lock every other MEMORY.md
  writer takes, re-read the file, and write only if it still holds exactly
  what the end image held. If it does not, somebody else has committed since
  the run ended: the file is left as it is and the caller logs and counts it.
  Writes go through `core._atomic_write_text`.

What it does not close: a commit that lands DURING the agent run, after the
agent's own write, is part of the end image and is still replaced by a
restore. Closing that needs a record of what the agent itself wrote.

This module does not import `mc.memory`; the split/compose functions `heal`
needs are passed in.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from mc.core import _atomic_write_text
from mc.state import _get_mem_write_lock


def _read(mem_path: Path) -> str:
    """The file's text; '' when it does not exist (the integrity check's own
    convention). Raises on a read or decode error."""
    return mem_path.read_text(encoding='utf-8') if mem_path.exists() else ''


class EndImage:
    """MEMORY.md as it was the moment the agent run ended. Read once."""

    def __init__(self, mem_path: Path):
        self.text: Optional[str] = None
        self.error: Optional[Exception] = None
        try:
            self.text = _read(mem_path)
        except Exception as e:  # unreadable is a state of the file, kept
            self.error = e

    def replay(self) -> str:
        """Hand the integrity check the text this image holds, or raise the
        error the read raised, so the check judges the same read the repair
        is conditional on."""
        if self.error is not None:
            raise self.error
        return self.text or ''

    def still_holds(self, mem_path: Path) -> bool:
        """True when the file on disk is what this image read. An image that
        could not be read still holds only while the file still cannot be."""
        try:
            now = _read(mem_path)
        except Exception:
            return self.error is not None
        return self.error is None and now == self.text


def restore(project_id: str, mem_path: Path, pre_mem: str,
            end: EndImage) -> bool:
    """Write the pre-image back, under the memory write lock, only if the
    file is unchanged since the run ended. Returns True when it wrote, False
    when the file had changed and was left alone. Raises on a write error."""
    with _get_mem_write_lock(project_id):
        if not end.still_holds(mem_path):
            return False
        _atomic_write_text(mem_path, pre_mem)
        return True


def heal(project_id: str, mem_path: Path, pre_wm: list, end: EndImage,
         split: Callable, compose: Callable) -> bool:
    """Re-inject the watermark lines the agent dropped, keeping its curation.
    Same condition and same return as `restore`."""
    with _get_mem_write_lock(project_id):
        if not end.still_holds(mem_path):
            return False
        cur, ent, wm = split(end.replay())
        have = set(wm)
        for w in pre_wm:
            if w not in have:
                wm.append(w)
                have.add(w)
        _atomic_write_text(mem_path, compose(cur, ent, wm))
        return True
