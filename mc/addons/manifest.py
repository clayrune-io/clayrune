"""The add-on manifest and absolute-path resolution (MC-1022, spec §3).

`~/.clayrune/addons/installed.json` records every add-on the user approved:
its absolute binary paths and the SHA-256 of each binary. `resolve()` is the
ONLY way code gets a path to an add-on binary, and it re-hashes the file first,
so a binary replaced after approval (or an adopted system copy that a package
manager upgraded) is marked `broken` and nothing runs it.

No add-on directory is ever put on a PATH: callers run the absolute path this
module returns, so a file dropped under `~/.clayrune/addons/` cannot shadow
`git`, `python` or `claude` in agent CLIs or terminal pop-outs.

Threat model (journal 6f53f808, Dave 2026-10-02): the gate defends against
UNATTENDED agents and injected install requests. The hash lives beside the
entry it protects, so it is integrity against corruption and a swapped file,
not against an attended agent that can rewrite this JSON. The consent step is
the control, not a sandbox.

Everything here lives outside `DATA_DIR` (CLAUDE.md, DATA_DIR pollution rule).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from mc.atomic_json import write_json_atomic
from mc.core import _log
from mc.secrets_store import clayrune_home

_lock = threading.RLock()
_holds: dict[str, dict[str, int]] = {}      # addon_id -> {job label: count}

STATUSES = ('ok', 'missing', 'broken')


class AddonError(Exception):
    """An add-on operation failed; `str(e)` is a one-line reason for the card."""


class AddonMissing(AddonError):
    """The add-on cannot be used right now. `status` is `absent` (never
    installed or removed), `missing` (files gone, e.g. a volume not mounted) or
    `broken` (a binary no longer matches the hash recorded at approval)."""

    def __init__(self, addon_id: str, status: str = 'absent', detail: str = ''):
        self.addon_id = addon_id
        self.status = status
        self.detail = detail
        super().__init__(f'{addon_id} is not available ({status})' + (f': {detail}' if detail else ''))


class AddonInUse(AddonError):
    """A running job holds the add-on, so it cannot be removed."""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def addons_root() -> Path:
    override = os.environ.get('CLAYRUNE_ADDONS_DIR')
    return Path(override) if override else clayrune_home() / 'addons'


def manifest_path() -> Path:
    return addons_root() / 'installed.json'


def staging_dir() -> Path:
    return addons_root() / 'staging'


def install_dir(addon_id: str, version: str) -> Path:
    return addons_root() / addon_id / version


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# ── manifest file ───────────────────────────────────────────────────────────

def load_manifest() -> dict:
    p = manifest_path()
    try:
        raw = json.loads(p.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'schema': 1, 'addons': {}}
    except (OSError, ValueError) as e:
        # Fail closed: an unreadable manifest resolves nothing. The file is
        # left where it is so the user can look at it; the next save replaces it.
        _log(f'[addons] manifest unreadable, treating as empty: {e}', flush=True)
        return {'schema': 1, 'addons': {}}
    if not isinstance(raw, dict) or not isinstance(raw.get('addons'), dict):
        _log('[addons] manifest has an unexpected shape, treating as empty', flush=True)
        return {'schema': 1, 'addons': {}}
    return raw


def save_manifest(m: dict) -> None:
    manifest_path().parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(manifest_path(), m, indent=2)


def get_entry(addon_id: str) -> dict | None:
    return load_manifest()['addons'].get(addon_id)


def put_entry(entry: dict) -> None:
    with _lock:
        m = load_manifest()
        m['addons'][entry['id']] = entry
        save_manifest(m)


def drop_entry(addon_id: str) -> None:
    with _lock:
        m = load_manifest()
        if m['addons'].pop(addon_id, None) is not None:
            save_manifest(m)


def _set_status(addon_id: str, status: str, detail: str = '') -> None:
    with _lock:
        m = load_manifest()
        e = m['addons'].get(addon_id)
        if e is None or (e.get('status') == status and e.get('status_detail', '') == detail):
            return
        e['status'], e['status_detail'], e['status_at'] = status, detail, now_iso()
        save_manifest(m)


# ── verification ────────────────────────────────────────────────────────────

def _check_binary(info: dict) -> tuple[str, str]:
    """('ok'|'missing'|'broken', detail) for one recorded binary."""
    p = Path(info.get('path') or '')
    if not p.is_absolute() or not p.is_file():
        return 'missing', f'{p} is not there'
    try:
        got = sha256_file(p)
    except OSError as e:
        return 'missing', f'{p} cannot be read: {e}'
    if got != info.get('sha256'):
        return 'broken', f'{p.name} no longer matches the checksum recorded when you approved it'
    return 'ok', ''


def verify_entry(addon_id: str) -> str:
    """Re-hash every recorded binary of `addon_id`, record and return the
    status. `absent` when there is no such entry."""
    e = get_entry(addon_id)
    if e is None:
        return 'absent'
    worst, detail = 'ok', ''
    for info in (e.get('binaries') or {}).values():
        st, why = _check_binary(info)
        if st == 'broken' or (st == 'missing' and worst == 'ok'):
            worst, detail = st, why
        if st == 'broken':
            break
    _set_status(addon_id, worst, detail)
    return worst


def verify_all() -> dict[str, str]:
    """Startup pass: mark each entry `ok`, `missing` or `broken`."""
    return {aid: verify_entry(aid) for aid in list(load_manifest()['addons'])}


def _find_entry(name: str) -> tuple[dict, str] | None:
    """The manifest entry for an add-on id or one of its binary names."""
    addons = load_manifest()['addons']
    if name in addons:
        e = addons[name]
        bins = e.get('binaries') or {}
        return e, (name if name in bins else next(iter(bins), name))
    for e in addons.values():
        if name in (e.get('binaries') or {}):
            return e, name
    return None


def resolve(name: str) -> str:
    """The absolute path of an approved add-on binary, after re-hashing it.

    `name` is an add-on id (`ffmpeg`) or one of its binary names (`ffprobe`).
    Raises `AddonMissing` when it was never approved, its files are gone, or
    the binary no longer matches the hash recorded at approval (the entry is
    then marked `broken` and stays so until the user approves again)."""
    found = _find_entry(name)
    if found is None:
        raise AddonMissing(name, 'absent')
    entry, binary = found
    aid = entry['id']
    info = (entry.get('binaries') or {}).get(binary)
    if not info:
        raise AddonMissing(aid, 'absent', f'{binary} is not part of this add-on')
    status, detail = _check_binary(info)
    if status != 'ok':
        _set_status(aid, status, detail)
        raise AddonMissing(aid, status, detail)
    if entry.get('status') != 'ok':
        # The file now matches: only a full verify may restore the entry.
        if verify_entry(aid) != 'ok':
            e2 = get_entry(aid) or {}
            raise AddonMissing(aid, e2.get('status', 'broken'), e2.get('status_detail', ''))
    return str(Path(info['path']))


def is_usable(name: str) -> bool:
    try:
        resolve(name)
        return True
    except AddonMissing:
        return False


# ── in-use holds (remove refuses while a job holds the add-on) ──────────────

@contextlib.contextmanager
def hold(addon_id: str, label: str) -> Generator[None, None, None]:
    """Mark `addon_id` as in use by the job named `label` for the duration."""
    with _lock:
        d = _holds.setdefault(addon_id, {})
        d[label] = d.get(label, 0) + 1
    try:
        yield
    finally:
        with _lock:
            d = _holds.get(addon_id, {})
            d[label] = d.get(label, 1) - 1
            if d.get(label, 0) <= 0:
                d.pop(label, None)


def holders(addon_id: str) -> list[str]:
    with _lock:
        return sorted(_holds.get(addon_id, {}))


# ── removal ─────────────────────────────────────────────────────────────────

def remove(addon_id: str) -> dict:
    """Forget `addon_id`; delete its directory when Clayrune installed it.

    Refuses (`AddonInUse`) while a job holds it. An adopted system copy is only
    forgotten: Clayrune never deletes files it did not install."""
    with _lock:
        who = holders(addon_id)
        if who:
            raise AddonInUse(f'{addon_id} is in use by {", ".join(who)}; remove it when that finishes')
        e = get_entry(addon_id)
        if e is None:
            raise AddonMissing(addon_id, 'absent')
        deleted = False
        if e.get('source') == 'catalogue':
            root = (addons_root() / addon_id).resolve()
            if root.parent != addons_root().resolve():
                raise AddonError(f'refusing to delete {root}: it is not a direct child of the add-ons folder')
            if root.exists():
                try:
                    shutil.rmtree(root)
                except OSError as ex:
                    raise AddonError(f'could not delete {root} (is something still using it?): {ex}')
                deleted = True
        drop_entry(addon_id)
    return {'removed': addon_id, 'deleted_files': deleted, 'source': e.get('source')}


def startup_sweep() -> dict:
    """Delete leftovers in `staging/` (a crash mid-install) and mark every
    entry `ok`, `missing` or `broken`. Server startup only, never at import."""
    cleaned = 0
    sd = staging_dir()
    if sd.is_dir():
        for child in sd.iterdir():
            try:
                shutil.rmtree(child) if child.is_dir() else child.unlink()
                cleaned += 1
            except OSError as e:
                _log(f'[addons] could not clear staging leftover {child}: {e}', flush=True)
    return {'staging_cleared': cleaned, 'status': verify_all()}
