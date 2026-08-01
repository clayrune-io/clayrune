"""Inventory of the Claude CLI's built-in slash commands.

WHY THIS EXISTS
---------------
Typing `/` in a composer should offer the commands that will actually run.
Two facts make that non-trivial:

1. **Only some commands work headless.** Mission Control always spawns the CLI
   with `--print` (see `ClaudeRuntime.build_command`). The CLI marks each
   command with `supportsNonInteractive`; of ~74 built-ins only ~23 carry it.
   Offering the rest would suggest commands that silently do nothing — the
   exact failure this feature exists to prevent.
2. **The list is version-specific.** It ships inside the user's own `claude`
   binary and changes across releases, so a list baked into MC would be wrong
   for anyone on a different Claude Code version.

So we read the registry out of the installed binary. The binary is ~265 MB, so
the scan is cached and only repeats when the binary itself changes
(path + size + mtime fingerprint) — a cold read costs a few seconds, every
later boot costs a stat().

The registry is bundled as object literals, e.g.

    {type:"local",name:"compact",description:"Free up context by …",
     supportsNonInteractive:!0,argumentHint:"<optional instructions>"}

WHAT THIS IS NOT
----------------
Skills are *also* invoked as `/name`, but they are a separate surface with
their own endpoint (`/api/skills`) and are deliberately not merged here.

Failure is never fatal: any error falls back to `_FALLBACK`, a small
hand-verified set, and the caller still gets a usable list.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Anchors the start of a command literal. `prompt`-type entries are skills, not
# built-ins, so they are deliberately not matched.
_ANCHOR = re.compile(rb'type:"(local|local-jsx)"')
_NAME = re.compile(rb'name:"([A-Za-z0-9_:.-]{1,40})"')
_DESC = re.compile(rb'description:"((?:[^"\\]|\\.){0,400})"')
_HINT = re.compile(rb'argumentHint:"((?:[^"\\]|\\.){0,160})"')
_NONINT = re.compile(rb'supportsNonInteractive:!0')

_CHUNK = 8 << 20          # 8 MB reads
_WINDOW = 1500            # a single command literal is far shorter than this
_OVERLAP = 4096           # > _WINDOW, so no literal is split across chunks

# Hand-verified against the 2026-07 CLI. Used only when extraction fails, so a
# broken/renamed bundle format degrades to something usable instead of nothing.
_FALLBACK: List[Dict[str, Any]] = [
    {'name': 'clear', 'description': 'Start a new session with empty context',
     'argumentHint': '[name]'},
    {'name': 'compact', 'description': 'Free up context by summarizing the conversation so far',
     'argumentHint': '<optional custom summarization instructions>'},
    {'name': 'goal', 'description': 'Set a goal Claude checks before stopping',
     'argumentHint': '[<condition> | clear]'},
    {'name': 'recap', 'description': 'Generate a one-line session recap now'},
    {'name': 'review', 'description': 'Review a GitHub pull request',
     'argumentHint': '[pr number]'},
    {'name': 'version', 'description': 'Print the version this session is running'},
]


def _unescape(raw: bytes) -> str:
    """Decode a JS string body (handles \\uXXXX, \\", \\\\) without eval."""
    txt = raw.decode('utf-8', 'replace')
    try:
        return json.loads('"' + txt + '"')
    except Exception:
        return txt.replace('\\"', '"').replace('\\\\', '\\')


def _fingerprint(binary: Path) -> Optional[Dict[str, Any]]:
    """Identity of the binary we extracted from — cheap to recompute."""
    try:
        st = binary.stat()
    except OSError:
        return None
    return {'path': str(binary), 'size': st.st_size, 'mtime': int(st.st_mtime)}


def extract_commands(binary: Path) -> List[Dict[str, Any]]:
    """Scan the CLI binary and return every built-in slash command found.

    Each entry: {name, description, argumentHint?, nonInteractive}. Duplicates
    are expected — a command often ships an interactive (`local-jsx`) variant
    AND a headless (`local`) one; the headless record wins so its
    `supportsNonInteractive` flag isn't lost.
    """
    found: Dict[str, Dict[str, Any]] = {}
    with binary.open('rb') as fh:
        tail = b''
        while True:
            buf = fh.read(_CHUNK)
            if not buf:
                break
            data = tail + buf
            for m in _ANCHOR.finditer(data):
                win = data[m.start():m.start() + _WINDOW]
                # Never let a sibling literal donate fields to this one.
                nxt = _ANCHOR.search(win, 1)
                if nxt:
                    win = win[:nxt.start()]
                nm = _NAME.search(win)
                if not nm:
                    continue
                name = nm.group(1).decode('utf-8', 'replace')
                desc = _DESC.search(win)
                rec: Dict[str, Any] = {
                    'name': name,
                    'description': _unescape(desc.group(1)) if desc else '',
                    'nonInteractive': bool(_NONINT.search(win)),
                }
                hint = _HINT.search(win)
                if hint:
                    rec['argumentHint'] = _unescape(hint.group(1))
                prev = found.get(name)
                if prev is None:
                    found[name] = rec
                    continue
                # Prefer the headless variant; otherwise prefer a real description.
                if rec['nonInteractive'] and not prev['nonInteractive']:
                    rec.setdefault('argumentHint', prev.get('argumentHint', ''))
                    if not rec['description']:
                        rec['description'] = prev['description']
                    found[name] = rec
                elif rec['nonInteractive'] == prev['nonInteractive']:
                    if not prev.get('description') and rec['description']:
                        prev['description'] = rec['description']
                    if not prev.get('argumentHint') and rec.get('argumentHint'):
                        prev['argumentHint'] = rec['argumentHint']
            tail = data[-_OVERLAP:]
    return sorted(found.values(), key=lambda r: r['name'])


def _read_cache(cache_path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(cache_path.read_text(encoding='utf-8'))
    except Exception:
        return None


def _write_cache(cache_path: Path, payload: Dict[str, Any]) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(payload, indent=1), encoding='utf-8')
        tmp.replace(cache_path)
    except Exception:
        pass  # best-effort: a missing cache only costs a re-scan


def load_inventory(binary: Optional[Path], cache_path: Path,
                   force: bool = False, log=None) -> Dict[str, Any]:
    """Return the cached inventory, re-extracting only when the binary changed.

    `force=True` re-scans regardless (the manual refresh path). Returns
    {commands, source, cli, extracted_at} where source is
    'cache' | 'extracted' | 'fallback'.
    """
    fp = _fingerprint(binary) if binary else None
    cached = _read_cache(cache_path)
    if not force and cached and fp and cached.get('cli') == fp:
        cached['source'] = 'cache'
        return cached

    if fp and binary:
        try:
            t0 = time.time()
            cmds = extract_commands(binary)
            if cmds:
                payload = {
                    'commands': cmds,
                    'cli': fp,
                    'extracted_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                    'extract_seconds': round(time.time() - t0, 2),
                    'source': 'extracted',
                }
                _write_cache(cache_path, payload)
                return payload
            if log:
                log('[slash-commands] extraction found 0 commands — using fallback')
        except Exception as e:
            if log:
                log(f'[slash-commands] extraction failed: {e}')

    # Never leave the composer with nothing to offer.
    if cached and cached.get('commands'):
        cached['source'] = 'cache'
        return cached
    return {'commands': [dict(c, nonInteractive=True) for c in _FALLBACK],
            'cli': fp, 'extracted_at': '', 'source': 'fallback'}


# Headless-capable but not things a person types. `__`-prefixed entries and the
# workflow handoff are internal dispatch plumbing the CLI invokes on itself;
# heapdump is a debug tool that writes to ~/Desktop. Kept out of the picker, not
# out of the inventory — `?all=1` still shows them.
_INTERNAL = {'__remote-workflow', 'workflow-launch-exec', 'heapdump'}


def headless_commands(inventory: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The subset safe to offer in MC — see module docstring, point 1.

    Drops interactive-only commands, internal plumbing, and anything the CLI
    itself marks as removed or renamed (their descriptions say so, and picking
    one would just waste a turn).
    """
    out = []
    for c in inventory.get('commands', []):
        if not c.get('nonInteractive'):
            continue
        name = c.get('name', '')
        if name in _INTERNAL or name.startswith('__'):
            continue
        desc = (c.get('description') or '').strip()
        if desc.startswith('(removed)') or desc.startswith('Renamed to'):
            continue
        out.append(c)
    return out
