"""Is a note slug safe to turn into a filename in the memory directory?

A slug is the one caller-supplied string the memory writers put into a file
path: `write_position` builds `position_<slug>.md` and `write_topic_note`
builds `<slug>.md`. The positions route passes the request body's `slug`
straight through, so an unchecked slug is a write anywhere the server can
reach (MC-1179 pass 3, ML14: on Windows `/../../../victim/SKILL` landed three
levels above the memory directory).

The rule is the one `delete_position` and `resolve_mint` already apply to a
FILENAME, applied here to the slug before a file exists: one path component,
and the path it builds must sit directly in the memory directory.
"""
from pathlib import Path

__all__ = ['is_safe_note_slug']


def is_safe_note_slug(mem_dir, slug, prefix=''):
    """True when `<mem_dir>/<prefix><slug>.md` is a file directly in `mem_dir`.

    Never raises: anything that cannot be checked is not safe.
    """
    if not isinstance(slug, str) or not slug.strip():
        return False
    if '/' in slug or '\\' in slug or '\x00' in slug or slug.strip() in ('.', '..'):
        return False
    try:
        mem_dir = Path(mem_dir)
        path = mem_dir / f'{prefix}{slug}.md'
        return path.resolve().parent == mem_dir.resolve()
    except (OSError, ValueError):
        return False
