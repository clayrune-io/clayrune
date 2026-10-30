"""Frozen integration identities and immutable dirty-preview manifests (DH-M3)."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from mc import agent_worktree, project_sync
from mc.dev_harness_catalogue import digest
from mc.dev_harness_store import _within

MAX_FILES = 20000
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_GIT_BYTES = 32 * 1024 * 1024
ERROR_CODES = frozenset({
    'git_unavailable', 'git_failed', 'git_output_limit', 'undecodable_path', 'source_unavailable',
    'unsafe_path', 'escaping_path', 'invalid_diff', 'unmerged_or_submodule',
    'file_count_limit', 'preview_changed', 'file_size_limit', 'unsupported_file_type',
    'total_size_limit', 'unresolvable_head', 'preview_has_no_checkoutable_head',
    'invalid_ref', 'invalid_object', 'dirty_integration_input', 'dirty_incoming_checkout',
    'ambiguous_merge_base', 'invalid_tree', 'worktree_create_failed',
    'candidate_tree_mismatch', 'worktree_cleanup_failed', 'binary_source_requires_adapter',
    'inputs_changed', 'plan_inputs_changed', 'ambiguous_pending_parents', 'pending_conflicts',
    'pending_worktree_differs', 'pending_resolution_unsupported',
})


class FreezeError(ValueError):
    """Fixed diagnostic codes only: never include raw Git output or filenames."""


def git_bytes(repo: Path, args: list[str]) -> bytes:
    """NUL-safe supplement to the existing text Git wrapper; no shell/logging."""
    try:
        result = subprocess.run(['git', *args], cwd=repo, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=30,
                                creationflags=project_sync._POPEN_FLAGS,
                                startupinfo=project_sync._STARTUPINFO)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FreezeError('git_unavailable') from exc
    if result.returncode:
        raise FreezeError('git_failed')
    if len(result.stdout) > MAX_GIT_BYTES:
        raise FreezeError('git_output_limit')
    return result.stdout


def _path(raw: bytes) -> str:
    try:
        name = raw.decode('utf-8', errors='strict')
    except UnicodeError as exc:
        raise FreezeError('undecodable_path') from exc
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or '..' in path.parts or '\\' in name
            or re.match(r'^[A-Za-z]:', name)):
        raise FreezeError('unsafe_path')
    return name


def safe_path(root: Path, name: str) -> Path:
    _path(name.encode('utf-8'))
    target = root / name
    if not _within(target.resolve(), root.resolve()):
        raise FreezeError('escaping_path')
    return target


def changes(repo: Path, base: str, tip: str | None = None) -> list[dict[str, str]]:
    """Preserve old/new names, statuses and modes, including newline filenames."""
    args = ['diff', '--raw', '-z', '--no-abbrev', '--find-renames', base]
    if tip is not None:
        args.append(tip)
    args.append('--')
    fields = iter(git_bytes(repo, args).split(b'\0'))
    found = []
    try:
        for header in fields:
            if not header:
                continue
            old_mode, new_mode, old_oid, new_oid, status_code = header.decode('ascii')[1:].split()
            old_name = _path(next(fields))
            new_name = _path(next(fields)) if status_code[0] in 'RC' else old_name
            found.append({'status': status_code, 'old_path': old_name, 'new_path': new_name,
                          'old_mode': old_mode, 'new_mode': new_mode,
                          'old_oid': old_oid, 'new_oid': new_oid})
    except (StopIteration, UnicodeError, ValueError) as exc:
        raise FreezeError('invalid_diff') from exc
    return found


def manifest(root: Path) -> dict[str, Any]:
    """Hash tracked working files, full index identity and nonignored new files.

    No source contents are persisted. Binary files remain explicitly labelled;
    they are never interpreted as text or silently dropped from the identity.
    """
    root = root.resolve()
    if not root.is_dir():
        raise FreezeError('source_unavailable')
    is_git = project_sync.is_git_repo(str(root))
    indexed = git_bytes(root, ['ls-files', '--stage', '-z']).split(b'\0') if is_git else []
    index: dict[str, Any] = {}
    for item in filter(None, indexed):
        head, raw_name = item.split(b'\t', 1)
        mode, oid, stage = head.decode('ascii').split()
        if stage != '0' or mode == '160000':
            raise FreezeError('unmerged_or_submodule')
        index[_path(raw_name)] = {'mode': mode, 'oid': oid}
    if is_git:
        untracked = [_path(p) for p in git_bytes(root, ['ls-files', '--others', '--exclude-standard', '-z']).split(b'\0') if p]
    else:
        untracked = []
        observed_count = 0
        for directory, dirs, paths in os.walk(root, followlinks=False):
            for name in [*dirs, *paths]:
                observed_count += 1
                path = Path(directory) / name
                safe_path(root, path.relative_to(root).as_posix())
                if name in paths or path.is_symlink():
                    untracked.append(path.relative_to(root).as_posix())
                if observed_count > MAX_FILES:
                    raise FreezeError('file_count_limit')
    names = sorted(set(index) | set(untracked))
    if len(names) > MAX_FILES:
        raise FreezeError('file_count_limit')
    files: dict[str, Any] = {}
    total = 0
    for name in names:
        path = safe_path(root, name)
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            if name not in index:
                raise FreezeError('preview_changed')
            files[name] = {'kind': 'deleted'}
            continue
        if stat.S_ISLNK(mode):
            data = os.readlink(path).encode('utf-8')
            kind, file_mode = 'symlink', '120000'
        elif stat.S_ISREG(mode):
            if path.stat().st_size > MAX_FILE_BYTES:
                raise FreezeError('file_size_limit')
            with path.open('rb') as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise FreezeError('file_size_limit')
            try:
                data.decode('utf-8', errors='strict')
                kind = 'binary' if b'\0' in data else 'text'
            except UnicodeError:
                kind = 'binary'
            # Index execute bits survive Windows' lack of executable permissions.
            file_mode = index.get(name, {}).get('mode', '100755' if mode & 0o111 else '100644')
            if os.name != 'nt':
                file_mode = '100755' if mode & 0o111 else '100644'
        else:
            raise FreezeError('unsupported_file_type')
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise FreezeError('total_size_limit')
        files[name] = {'kind': kind, 'mode': file_mode, 'bytes': len(data),
                       'sha256': hashlib.sha256(data).hexdigest()}
    value = {'version': 1, 'index': index, 'files': files}
    return {**value, 'digest': digest(value)}


def preview(repo: Path) -> dict[str, Any]:
    """Two consecutive observations must agree; dirty previews grant no authority."""
    first = manifest(repo)
    head = None
    if project_sync.is_git_repo(str(repo)):
        try:
            head = resolve_commit(repo, 'HEAD')
        except FreezeError:
            if git_bytes(repo, ['rev-list', '--all']):
                raise FreezeError('unresolvable_head')
    changed = changes(repo, head) if head else []
    if manifest(repo) != first:
        raise FreezeError('preview_changed')
    return {'kind': 'preview', 'head': head, 'manifest': first, 'changes': changed,
            'authorizes_merge': False}


@contextmanager
def preview_worktree(project: dict[str, Any], frozen: dict[str, Any]) -> Iterator[Path]:
    """Overlay dirty working bytes on a disposable HEAD, then verify their hashes."""
    source = Path(project['project_path']).resolve()
    if not frozen['head']:
        raise FreezeError('preview_has_no_checkoutable_head')
    head = frozen['head']
    tree = git_bytes(source, ['rev-parse', head + '^{tree}']).decode('ascii').strip()
    with candidate_worktree(project, {'B': head, 'H': head, 'T': tree}) as root:
        entries = frozen['manifest']['files']
        # Staged deletions/renames are absent from the source index. Remove the
        # HEAD version too, without ever touching the source checkout.
        for name in manifest(root)['files']:
            if name not in entries or entries[name]['kind'] == 'deleted':
                safe_path(root, name).unlink(missing_ok=True)
        for name, entry in entries.items():
            if entry['kind'] == 'deleted':
                continue
            src, dst = safe_path(source, name), safe_path(root, name)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() or dst.is_symlink():
                dst.unlink()
            if entry['kind'] == 'symlink':
                dst.symlink_to(os.readlink(src))
                safe_path(root, name)
            else:
                shutil.copyfile(src, dst)
                if os.name != 'nt':
                    dst.chmod(0o755 if entry['mode'] == '100755' else 0o644)
        observed = {k: v for k, v in manifest(root)['files'].items() if v['kind'] != 'deleted'}
        expected = {k: v for k, v in entries.items() if v['kind'] != 'deleted'}
        # An untracked preview path loses its index execute bit on Windows;
        # bytes/kind/mode must otherwise agree with the frozen input.
        if observed != expected or manifest(source) != frozen['manifest']:
            raise FreezeError('preview_changed')
        yield root


def resolve_commit(repo: Path, ref: str) -> str:
    if not isinstance(ref, str) or not ref or ref.startswith('-') or '\0' in ref:
        raise FreezeError('invalid_ref')
    oid = git_bytes(repo, ['rev-parse', '--verify', '--end-of-options', ref + '^{commit}']).decode('ascii').strip()
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', oid):
        raise FreezeError('invalid_object')
    return oid


def freeze_candidate(repo: Path, target: str, incoming: str) -> dict[str, Any]:
    if git_bytes(repo, ['status', '--porcelain', '-z', '--untracked-files=all']):
        raise FreezeError('dirty_integration_input')
    b, h = resolve_commit(repo, target), resolve_commit(repo, incoming)
    # Incoming dirty work from another registered checkout must not be silently
    # substituted with its last committed tip.
    checkout: Path | None = None
    for field in git_bytes(repo, ['worktree', 'list', '--porcelain', '-z']).split(b'\0'):
        if field.startswith(b'worktree '):
            try:
                checkout = Path(field[9:].decode('utf-8', errors='strict'))
            except UnicodeError as exc:
                raise FreezeError('undecodable_path') from exc
        elif field == b'HEAD ' + h.encode('ascii') and checkout is not None:
            if git_bytes(checkout, ['status', '--porcelain', '-z', '--untracked-files=all']):
                raise FreezeError('dirty_incoming_checkout')
    bases = git_bytes(repo, ['merge-base', '--all', b, h]).decode('ascii').split()
    if len(bases) != 1:
        raise FreezeError('ambiguous_merge_base')
    # merge-tree writes objects only, never checks out or mutates the user's tree.
    tree = git_bytes(repo, ['merge-tree', '--write-tree', b, h]).decode('ascii').splitlines()[0]
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', tree):
        raise FreezeError('invalid_tree')
    return {'kind': 'integration', 'B': b, 'H': h, 'M': bases[0], 'T': tree,
            'branch_changes': changes(repo, bases[0], h),
            'integration_changes': changes(repo, b, tree)}


@contextmanager
def candidate_worktree(project: dict[str, Any], frozen: dict[str, Any]) -> Iterator[Path]:
    """Reuse create/remove and link-safe cleanup, with no shared runtime links."""
    repo = Path(project['project_path']).resolve()
    git_bytes(repo, ['check-ignore', '-q', '.clayrune/agents/dh_probe'])
    session = 'dh_' + uuid.uuid4().hex
    # A synthetic local commit makes the merged tree checkoutable; no target ref moves.
    commit = git_bytes(repo, ['-c', 'user.name=Dev Harness', '-c', 'user.email=dev-harness@localhost',
                             'commit-tree', frozen['T'], '-p', frozen['B'], '-p', frozen['H'],
                             '-m', 'Disposable frozen verification candidate']).decode('ascii').strip()
    ok, value = agent_worktree.create(project, session, commit, isolated=True)
    if not ok:
        raise FreezeError('worktree_create_failed')
    path = Path(value)
    try:
        if git_bytes(path, ['rev-parse', 'HEAD^{tree}']).decode('ascii').strip() != frozen['T']:
            raise FreezeError('candidate_tree_mismatch')
        yield path
    finally:
        ok, _ = agent_worktree.remove(project, session, delete_branch=True, force=True)
        if not ok:
            raise FreezeError('worktree_cleanup_failed')
