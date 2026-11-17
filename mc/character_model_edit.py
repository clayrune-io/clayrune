"""Change the one `model:` value of a character file, in place (MC-1179 OS45).

The unattended model auto-upgrade used to apply a change through
`characters.write_character(overwrite=True)`. That rebuilds the frontmatter
from a fixed list of keys and writes the file whole, so every key Clayrune
does not model was dropped: a subagent `tools:` line (which limits what the
subagent may call — losing it widens the agent), `color:`, anything else. It
also left a built-in character's bytes different from its hash marker, which
`characters.install_builtin_characters` reads as "the user edited this" and
never updates again.

This module reads the text, replaces the value on the frontmatter's `model:`
line, and writes the file back atomically. Every other byte is kept — line
endings, key order, quoting, the body. `write_character` and the human editor
are deliberately not touched.

A built-in is tracked by `<name>.md.mc-builtin-hash` beside the file in the
global agents dir. If the file matched that marker BEFORE the change, the
marker is rewritten to the new hash so the built-in keeps receiving updates;
if it was already user-edited (or has no marker) the marker is left alone.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from mc import characters as _chars
from mc.atomic_json import replace_with_retry

# Same fence the frontmatter parser uses (mc/skills.py `_FENCE_RE`), widened to
# accept CRLF so a Windows-edited file is found the way the parser finds it.
_FENCE_RE = re.compile(r'\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)', re.DOTALL)
_KEY_RE = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_-]*)[ \t]*:[ \t]*(.*?)(\r?)$')
# Characters the frontmatter dumper (mc/skills.py `dump_skill_md`) quotes for.
_NEEDS_QUOTES = set(':#&*!|>%@`')


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f'.{path.name}.',
                               suffix=f'.tmp{os.getpid()}')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        replace_with_retry(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _replace_model_value(text: str, new_model: str, label: str) -> str:
    m = _FENCE_RE.match(text)
    if not m:
        raise RuntimeError(f'character {label} has no frontmatter; no model line to change')
    start = m.start(1)
    lines = m.group(1).split('\n')
    hit: Optional[int] = None
    offsets: list[int] = []
    pos = start
    for i, line in enumerate(lines):
        offsets.append(pos)
        pos += len(line) + 1
        km = _KEY_RE.match(line)
        if km and km.group(1) == 'model':
            hit = i            # the parser keeps the LAST duplicate; so do we
    if hit is None:
        raise RuntimeError(
            f'character {label} has no model line; left untouched (the upgrade '
            f'only changes an existing pin, it does not add one)')
    km = _KEY_RE.match(lines[hit])
    assert km is not None
    value, cr = km.group(2), km.group(3)
    if value in ('|', '>') or value == '':
        raise RuntimeError(
            f'character {label} model line is a block or empty value; left untouched')
    nxt = lines[hit + 1] if hit + 1 < len(lines) else ''
    if nxt.startswith(' ') or nxt.startswith('\t'):
        raise RuntimeError(
            f'character {label} model value continues on indented lines; left untouched')
    quote = value[0] if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'' else ''
    if quote:
        rendered = f'{quote}{new_model}{quote}'
    elif any(c in _NEEDS_QUOTES for c in new_model):
        rendered = f'"{new_model}"'
    else:
        rendered = new_model
    # Keep the key text and the spacing around the colon exactly as written.
    prefix_len = len(lines[hit]) - len(cr) - len(value)
    new_line = lines[hit][:prefix_len] + rendered + cr
    line_start = offsets[hit]
    return text[:line_start] + new_line + text[line_start + len(lines[hit]):]


def set_character_model(scope: str, name: str, project_path: Optional[str],
                        new_model: str) -> None:
    """Set the `model:` value of a character file in place. Raises
    RuntimeError if the character or its model line is missing or unusable,
    ValueError if `new_model` is not a safe single-line value; in every raising
    case the file is untouched."""
    new_model = (new_model or '').strip()
    if not new_model or '\n' in new_model or '\r' in new_model \
            or '"' in new_model or "'" in new_model or '\\' in new_model:
        raise ValueError(f'unsafe model value {new_model!r}')
    path = _chars._find_file(scope, name, project_path)
    if path is None:
        raise RuntimeError(f'character {name!r} ({scope}) vanished before the upgrade applied')
    before = path.read_bytes()
    try:
        text = before.decode('utf-8')
    except UnicodeDecodeError as e:
        raise RuntimeError(f'character {name!r} is not valid UTF-8; left untouched: {e}')
    after = _replace_model_value(text, new_model, f'{name!r} ({scope})').encode('utf-8')

    marker: Optional[Path] = None
    in_sync = False
    if scope == 'global' and path.parent == _chars.GLOBAL_AGENTS_DIR:
        marker = _chars.GLOBAL_AGENTS_DIR / f'{path.name}{_chars._BUILTIN_MARKER_SUFFIX}'
        try:
            in_sync = marker.read_text(encoding='utf-8').strip() == \
                hashlib.sha256(before).hexdigest()
        except OSError:
            in_sync = False    # no marker (user-owned) or unreadable: leave alone

    _atomic_write_bytes(path, after)
    if marker is not None and in_sync:
        _atomic_write_bytes(marker, hashlib.sha256(after).hexdigest().encode('utf-8'))
