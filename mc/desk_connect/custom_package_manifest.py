"""Drift check for an approved npm package's files (docs/DESK_SERVICE_PROFILES_SPEC.md, section 6.2,
slice U2a; backlog f7332894, MC-1054 part 2).

The approved package is unpacked into a digest-addressed directory that Clayrune never rewrites,
but nothing stops an agent's file tools (or anyone) from editing a file under it afterwards, and
the server that agent session starts would run the edited file. The launch line (`matches`) and the
archive digest only cover what was written at install. This module records what the directory held
when a human approved it and says, later, whether it still does.

    record(op)         At approval/install: walk the package directory (never following a link) and
                       store a manifest, one entry per file: relative path, sha256, size and the
                       mtime it had. Bounded by file count and total bytes. Atomic write, in its own
                       file under `~/.clayrune/desk_custom_package_manifests/` (operator state
                       outside the repo and outside `data/projects/`, so `load_projects()` never reads
                       it). The approval record carries a `package_manifest` marker saying one was
                       made, which is what lets a deleted manifest read as a problem and not as
                       "never recorded".
    verify(rec)        Compare the directory with the manifest. Cheap: a file whose size and mtime are
                       what the manifest (or the last look) saw is not read again; a file whose size
                       or mtime moved is hashed in full. FAILS VISIBLE: a missing or unreadable
                       manifest, an unreadable directory or a package over the bounds is `changed`,
                       never `unchanged`.
    check(rec)         `verify` plus ONE `_log` line per distinct drift (not one per look).
    check_for_launch   What agent dispatch calls: `check` for every approved npm server the session
                       would start. Never raises.

DETECT, NOT BLOCK: nothing here refuses a launch. A re-approval with the passcode runs `provision`,
which records the manifest again. The shortcut is a stated limit: an edit that keeps both the size
and the mtime is not seen until something else moves them; this is drift detection for ordinary
file edits, not tamper-proofing against a caller that resets timestamps.

A result is `{status, reason, counts, changed, added, removed, more, checked}` with status
`unchanged`, `changed`, `not_recorded` (approved before this check existed) or `not_applicable` (a
remote server has no files). Listed paths come from the directory, so they are cleaned of control
characters and cut; they are text for a person, never for a model.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone

from mc import secrets_store as _vault
from mc.core import _log
from mc.desk_connect import custom_connection_store as _store
from mc.desk_connect import custom_npm_artifact as _artifact

DIR_NAME = 'desk_custom_package_manifests'
MAX_FILES = _artifact.MAX_TREE_MEMBERS + 64          # a package with its approved dependency tree (slice U2b)
MAX_BYTES = _artifact.MAX_TREE_UNPACKED + (1 << 20)
LIST_MAX = 10
_PATH_SHOWN = 120
_HASH_CHUNK = 1 << 20
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_REPARSE_POINT = 0x400

_lock = threading.Lock()
_seen: dict[str, dict[str, tuple[int, int, str]]] = {}     # manifest key -> rel -> (size, mtime_ns, sha256) last read
_logged: dict[str, tuple] = {}                              # manifest key -> signature of the drift already logged


class ManifestError(RuntimeError):
    """A manifest could not be made or read; `reason` is one of the codes `verify` reports."""

    def __init__(self, reason: str, message: str = ''):
        super().__init__(message or reason)
        self.reason = reason


def _forget_all_for_tests() -> None:
    with _lock:
        _seen.clear()
        _logged.clear()


def key_of(rec_or_op: dict) -> str:
    """The manifest's identity: the approval record's key (`scope:project:name`), so two servers that
    share one digest directory each keep their own manifest."""
    if 'operation' in rec_or_op:
        return _store.key(rec_or_op['scope'], rec_or_op['project_id'], rec_or_op['server_name'])
    return _store.key(rec_or_op['scope']['kind'], rec_or_op['scope']['project_id'], rec_or_op['server_name'])


def manifest_path(key: str):
    return _vault.clayrune_home() / DIR_NAME / f'{hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]}.json'


def _is_link(st) -> bool:
    import stat as _stat
    return _stat.S_ISLNK(st.st_mode) or bool(getattr(st, 'st_file_attributes', 0) & _REPARSE_POINT) \
        or bool(getattr(st, 'st_reparse_tag', 0))


def _walk(root) -> tuple[dict[str, dict], bool]:
    """Every entry under `root` as `{rel: {kind, size, mtime_ns, link?}}`, without following a link and
    without descending into one. The second value is True when the count or byte bound was hit (the
    walk stops there). Raises OSError when `root` itself cannot be read."""
    import stat as _stat
    out: dict[str, dict] = {}
    total, over = 0, False
    stack = [(str(root), '')]
    while stack and not over:
        base, prefix = stack.pop()
        with os.scandir(base) as it:
            for e in it:
                rel = prefix + e.name
                st = e.stat(follow_symlinks=False)
                if _is_link(st):
                    try:
                        target = os.readlink(e.path)
                    except OSError:
                        target = ''
                    out[rel] = {'kind': 'link', 'link': target}
                elif _stat.S_ISDIR(st.st_mode):
                    stack.append((e.path, rel + '/'))
                    continue
                elif _stat.S_ISREG(st.st_mode):
                    total += st.st_size
                    out[rel] = {'kind': 'file', 'size': st.st_size, 'mtime_ns': st.st_mtime_ns}
                else:
                    out[rel] = {'kind': 'other'}
                if len(out) > MAX_FILES or total > MAX_BYTES:
                    over = True
                    break
    return out, over


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(_HASH_CHUNK)
            if not chunk:
                return h.hexdigest()
            h.update(chunk)


def _entry_of(root, rel: str, meta: dict) -> dict:
    if meta['kind'] == 'file':
        return {'sha256': _sha256(os.path.join(str(root), *rel.split('/'))), 'size': meta['size'], 'mtime_ns': meta['mtime_ns']}
    if meta['kind'] == 'link':
        return {'link': meta['link']}
    return {'other': True}


# ── record ───────────────────────────────────────────────────────────────────

def record(op: dict) -> dict:
    """Walk the package directory of `op` and store its manifest, then mark the approval record.
    Returns `{files, bytes}`. Raises ManifestError (nothing marked) when the directory cannot be
    read or is over the bounds."""
    key = key_of(op)
    root = _artifact.package_dir(op)
    try:
        walked, over = _walk(root)
    except OSError as e:
        raise ManifestError('package_unreadable', f'the package directory could not be read: {type(e).__name__}') from e
    if over:
        raise ManifestError('too_large', 'the package directory is over the file or size bound')
    try:
        files = {rel: _entry_of(root, rel, m) for rel, m in walked.items()}
    except OSError as e:
        raise ManifestError('package_unreadable', f'a package file could not be read: {type(e).__name__}') from e
    total = sum(m.get('size', 0) for m in walked.values())
    doc = {'version': 1, 'key': key, 'integrity': op['integrity'],
           'recorded_at': datetime.now(timezone.utc).isoformat(), 'files': files}
    p = manifest_path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f'.{p.name}.{uuid.uuid4().hex[:8]}.tmp')
    try:
        tmp.write_text(json.dumps(doc, sort_keys=True), encoding='utf-8')
        os.replace(tmp, p)
    except OSError as e:
        raise ManifestError('manifest_unwritable', f'the manifest could not be written: {type(e).__name__}') from e
    finally:
        tmp.unlink(missing_ok=True)
    summary = {'recorded_at': doc['recorded_at'], 'files': len(files), 'bytes': total}
    _store.set_package_manifest(op['scope']['kind'], op['scope']['project_id'], op['server_name'], summary)
    with _lock:
        _seen.pop(key, None)
        _logged.pop(key, None)
    return summary


# ── verify ───────────────────────────────────────────────────────────────────

def _load(key: str, op: dict) -> dict:
    """The stored manifest's `files`, shape-checked. Raises ManifestError `manifest_missing`,
    `manifest_unreadable` or `manifest_mismatch` (a manifest of another key or another digest)."""
    try:
        doc = json.loads(manifest_path(key).read_text(encoding='utf-8'))
    except FileNotFoundError as e:
        raise ManifestError('manifest_missing') from e
    except (OSError, ValueError) as e:
        raise ManifestError('manifest_unreadable') from e
    files = doc.get('files') if isinstance(doc, dict) else None
    if not isinstance(files, dict) or doc.get('version') != 1:
        raise ManifestError('manifest_unreadable')
    for rel, ent in files.items():
        ok = isinstance(rel, str) and isinstance(ent, dict) and (
            (isinstance(ent.get('sha256'), str) and bool(_SHA256.match(ent['sha256']))
             and type(ent.get('size')) is int and type(ent.get('mtime_ns')) is int)
            or isinstance(ent.get('link'), str) or ent.get('other') is True)
        if not ok:
            raise ManifestError('manifest_unreadable')
    if doc.get('key') != key or doc.get('integrity') != op.get('integrity'):
        raise ManifestError('manifest_mismatch')
    return files


def _show(rel: str) -> str:
    s = ''.join(c if c.isprintable() else '?' for c in rel)
    return s if len(s) <= _PATH_SHOWN else s[:_PATH_SHOWN - 1] + '…'


def _result(status: str, reason: str = '', changed=(), added=(), removed=(), checked: int = 0) -> dict:
    lists = {'changed': sorted(changed), 'added': sorted(added), 'removed': sorted(removed)}
    counts = {k: len(v) for k, v in lists.items()}
    shown = {k: [_show(p) for p in v[:LIST_MAX]] for k, v in lists.items()}
    return {'status': status, 'reason': reason, 'counts': counts, **shown,
            'more': sum(max(0, n - LIST_MAX) for n in counts.values()), 'checked': checked}


def _differs(mine: dict, theirs: dict) -> bool:
    if 'sha256' in mine:
        return 'sha256' not in theirs or mine['sha256'] != theirs['sha256'] or mine['size'] != theirs['size']
    return mine != theirs


def verify(rec: dict) -> dict:
    """Compare the directory with the manifest recorded for `rec`. Never raises."""
    op = rec.get('operation') if isinstance(rec, dict) else None
    if not isinstance(op, dict) or op.get('ecosystem') != 'npm':
        return _result('not_applicable')
    if not isinstance(rec.get('package_manifest'), dict):
        return _result('not_recorded')
    try:
        key = key_of(rec)
        want = _load(key, op)
        root = _artifact.package_dir(op)
        have, over = _walk(root)
    except ManifestError as e:
        return _result('changed', e.reason)
    except (OSError, KeyError, TypeError) as e:
        _log(f'[desk_connect] custom package check could not read the package: {type(e).__name__}', flush=True)
        return _result('changed', 'package_unreadable')
    with _lock:
        seen = _seen.setdefault(key, {})
    changed, now = [], {}
    for rel, meta in have.items():
        if meta['kind'] != 'file':
            if rel in want and not _differs(_entry_of(root, rel, meta), want[rel]):
                continue
            if rel in want:
                changed.append(rel)
            continue
        base = want.get(rel)
        if base is None:
            continue
        sig = (meta['size'], meta['mtime_ns'])
        known = seen.get(rel)
        if known and known[:2] == sig:
            sha = known[2]
        elif 'sha256' in base and (base['size'], base['mtime_ns']) == sig:
            sha = base['sha256']
        else:
            try:
                sha = _sha256(os.path.join(str(root), *rel.split('/')))
            except OSError:
                changed.append(rel)
                continue
        now[rel] = (*sig, sha)
        if 'sha256' not in base or sha != base['sha256'] or meta['size'] != base['size']:
            changed.append(rel)
    added = [rel for rel in have if rel not in want]
    removed = [rel for rel in want if rel not in have]
    with _lock:
        seen.clear()
        seen.update(now)
    checked = len(have)
    if over:
        return _result('changed', 'too_large', changed, added, removed, checked)
    if changed or added or removed:
        return _result('changed', 'files_differ', changed, added, removed, checked)
    return _result('unchanged', checked=checked)


# ── report ───────────────────────────────────────────────────────────────────

def check(rec: dict) -> dict:
    """`verify`, and one `_log` line each time the drift is new (the same drift seen again, by a card
    load or a dispatch, logs nothing; a return to unchanged lets a later drift log again)."""
    res = verify(rec)
    try:
        key = key_of(rec)
    except (KeyError, TypeError):
        return res
    sig = (res['reason'], tuple(res['counts'].values()), tuple(res['changed'] + res['added'] + res['removed']))
    with _lock:
        if res['status'] != 'changed':
            _logged.pop(key, None)
            return res
        if _logged.get(key) == sig:
            return res
        _logged[key] = sig
    c = res['counts']
    detail = f'{c["changed"]} changed, {c["added"]} added, {c["removed"]} removed'
    paths = ', '.join((res['changed'] + res['added'] + res['removed'])[:3])
    _log(f'[desk_connect] custom MCP {rec.get("server_name")} package files no longer match the approved manifest '
         f'({res["reason"]}; {detail}{"; " + paths if paths else ""}). Launch is not blocked.', flush=True)
    return res


def check_for_launch(project) -> None:
    """Called when an agent session's MCP set is resolved: `check` every approved npm server this
    session would start (global ones, this project's own; only the selected ones when the project
    trims its servers). Detect only; never raises."""
    try:
        pid = (project or {}).get('id')
        selected = (project or {}).get('enabled_mcp_servers')
        for rec in _store.all_records():
            op = rec.get('operation')
            if not isinstance(op, dict) or op.get('ecosystem') != 'npm':
                continue
            if rec.get('scope') == 'project' and rec.get('project_id') != pid:
                continue
            if isinstance(selected, list) and rec.get('server_name') not in selected:
                continue
            check(rec)
    except _store.StoreUnreadable:
        pass                                            # already logged by the store; nothing to compare against
    except Exception as e:
        _log(f'[desk_connect] custom package check at launch failed: {type(e).__name__}', flush=True)
