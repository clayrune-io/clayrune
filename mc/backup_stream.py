"""Streaming reads and writes of archive members (MC-1179 P29).

``mc.backup`` used to hold a whole member in memory on every path: restore and
import staged every selected member as bytes before the first write (a full
restore of a large install needed as much memory as the archive), and create
read each file whole, so a file too large to hold raised ``MemoryError`` and
was recorded as a skipped file in a backup that otherwise looked complete.

Everything here moves ``CHUNK`` bytes at a time. Nothing in this module
decides what may be written or where; callers keep their own path guards and
hash comparisons, and only swap ``zf.read`` / ``write_bytes`` for these.
"""
from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

CHUNK = 1024 * 1024

# A project JSON file is parsed and re-serialised on create so a half-written
# file is caught rather than archived (spec §6); that needs the whole file in
# memory. Above this size the file is streamed as it is instead and the caller
# records a warning.
MAX_JSON_HOLD_BYTES = 256 * 1024 * 1024


def sha256_member(zf: zipfile.ZipFile, arcname: str) -> str:
    """sha256 of a member, read in chunks. A bad zip CRC raises
    ``zipfile.BadZipFile`` at the end of the read, as ``zf.read`` does."""
    h = hashlib.sha256()
    with zf.open(arcname) as src:
        while True:
            chunk = src.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def copy_member(zf: zipfile.ZipFile, arcname: str, dest: Path) -> None:
    """Write a member to ``dest`` in chunks (parent directory must exist)."""
    with zf.open(arcname) as src, open(dest, 'wb') as out:
        while True:
            chunk = src.read(CHUNK)
            if not chunk:
                break
            out.write(chunk)


def member_equals_file(zf: zipfile.ZipFile, arcname: str, path: Path) -> bool:
    """True if ``path`` holds exactly the member's bytes. Compared in chunks."""
    with zf.open(arcname) as src, open(path, 'rb') as cur:
        while True:
            a = src.read(CHUNK)
            b = cur.read(len(a)) if a else cur.read(1)
            if a != b:
                return False
            if not a:
                return True


def add_file(zf: zipfile.ZipFile, arcname: str, src_path: Path) -> tuple[str, int]:
    """Stream ``src_path`` into the open archive as ``arcname``; return its
    (sha256, byte count). The source is opened before the archive is touched,
    so a missing or locked file raises with nothing written. A read that fails
    part-way leaves a truncated member the caller's manifest never lists."""
    h = hashlib.sha256()
    n = 0
    with open(src_path, 'rb') as src, zf.open(arcname, 'w', force_zip64=True) as out:
        while True:
            chunk = src.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
            out.write(chunk)
            n += len(chunk)
    return h.hexdigest(), n
