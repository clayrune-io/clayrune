"""Hivemind integration — land finished workstreams on ONE branch, serially.

Hivemind workers each run in their own git worktree (`hm_<id>` sessions, see
`hivemind_routes._hm_spawn_worker_session`). When every workstream has
completed, `integrate()` merges their branches one at a time into
`hivemind/<hivemind_id>`, created off the commit the workers branched from, and
runs the smokes each workstream names after each merge.

HARD RULES (Backlog e3c0824e):
  * NEVER touches master (or any branch but `hivemind/<id>`) and never pushes.
    A human, or Dave, lands the integration branch.
  * The merge happens in a DEDICATED worktree under
    `<project>/.clayrune/hivemind-integration/<id>` — the user's own checkout,
    its HEAD and its dirty state are never touched.
  * Stops at the FIRST conflict, dirty worker tree, missing branch or failing
    smoke. Remaining workstreams stay unmerged. The integration branch only ever
    holds smoke-green merges: a merge whose smoke fails is rolled back.
  * A workstream that names no smokes runs none, and that is recorded.

State is returned (and streamed through `on_update`) as a plain dict; the route
layer persists it and serves it through the hivemind status API.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import mc.agent_worktree as _awt
import mc.project_sync as _sync
from mc.core import _log, now_iso

SMOKE_TIMEOUT_S = 240
_SMOKE_NAME = re.compile(r'^[A-Za-z0-9_.-]+\.mjs$')
_SMOKE_NODE_MODULES = 'tools/smoke/node_modules'


def clean_smokes(raw) -> list[str]:
    """Filenames only — a workstream record is user/agent-writable, and these
    end up as argv to `node`. Anything that isn't a bare `*.mjs` name is
    dropped."""
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for n in raw:
        n = str(n).strip()
        if _SMOKE_NAME.match(n) and n not in out:
            out.append(n)
    return out


def integration_branch(hivemind_id: str) -> str:
    return f'hivemind/{hivemind_id}'


def integration_path(project: dict, hivemind_id: str) -> Path | None:
    base = project.get('project_path') or ''
    if not base or not re.match(r'^[A-Za-z0-9_-]{1,64}$', hivemind_id or ''):
        return None
    return Path(base) / '.clayrune' / 'hivemind-integration' / hivemind_id


def _git(cwd, args, timeout=120):
    return _sync.git_run(str(cwd), args, timeout=timeout)


def _run_smoke(wt: Path, name: str) -> tuple[bool, str]:
    """Run one tools/smoke/<name> with node inside the integration worktree."""
    script = wt / 'tools' / 'smoke' / name
    if not script.is_file():
        return False, f'smoke file not found in the merged tree: tools/smoke/{name}'
    node = shutil.which('node')
    if not node:
        return False, 'node not found on PATH'
    try:
        r = subprocess.run(
            [node, str(Path('tools') / 'smoke' / name)], cwd=str(wt),
            capture_output=True, stdin=subprocess.DEVNULL,
            text=True, encoding='utf-8', errors='replace',
            timeout=SMOKE_TIMEOUT_S,
            creationflags=_sync._POPEN_FLAGS, startupinfo=_sync._STARTUPINFO)
    except subprocess.TimeoutExpired:
        return False, f'timed out after {SMOKE_TIMEOUT_S}s'
    except Exception as e:
        return False, f'could not run: {e}'
    if r.returncode == 0:
        return True, ''
    tail = ((r.stdout or '') + (r.stderr or '')).strip()[-600:]
    return False, f'exit {r.returncode}: {tail}'


def _order(workstreams: list[dict]) -> list[dict]:
    """Completion order: a dependency always completes before its dependent, so
    this is dependency-respecting without a separate topological sort."""
    return sorted(workstreams, key=lambda w: (w.get('completed_at') or '', w.get('id') or ''))


def integrate(project: dict, hivemind_id: str, workstreams: list[dict],
              base_commit: str,
              on_update: Callable[[dict], None] | None = None) -> dict:
    """Merge completed workstreams' worktree branches into hivemind/<id>, one
    at a time. Returns the final state dict (never raises)."""
    branch = integration_branch(hivemind_id)
    st: dict = {
        'status': 'running', 'branch': branch, 'base_commit': base_commit or '',
        'merged': [], 'skipped': [], 'failed': None, 'unmerged': [],
        'started_at': now_iso(), 'finished_at': None,
    }

    def push():
        if on_update:
            try:
                on_update(st)
            except Exception as e:
                _log(f"[hivemind-integration] state write failed: {e}")

    def finish(status, reason=''):
        st['status'] = status
        if reason:
            st['reason'] = reason
        st['finished_at'] = now_iso()
        push()
        return st

    try:
        cands = []
        for ws in _order([w for w in workstreams if w.get('status') == 'completed']):
            if ws.get('worktree_session_id'):
                cands.append(ws)
            else:
                st['skipped'].append({'ws_id': ws.get('id'), 'reason': 'no isolated worktree'})
        if not cands:
            return finish('skipped', 'no workstream ran in an isolated worktree')
        base = project.get('project_path') or ''
        ok, sha = _git(base, ['rev-parse', '--verify', f'{base_commit}^{{commit}}'] if base_commit
                       else ['rev-parse', '--verify', 'HEAD'], 15)
        if not ok or not sha:
            return finish('error', f'base commit {base_commit!r} not found')
        st['base_commit'] = sha
        wt = integration_path(project, hivemind_id)
        if wt is None:
            return finish('error', 'bad hivemind id or project_path')
        if wt.exists():
            return finish('error', f'integration worktree already exists: {wt}')
        ok, refs = _git(base, ['branch', '--list', branch], 15)
        if ok and refs.strip():
            return finish('error', f'branch {branch} already exists; not reusing it')
        wt.parent.mkdir(parents=True, exist_ok=True)
        ok, msg = _git(base, ['worktree', 'add', '-b', branch, str(wt), sha], 120)
        if not ok:
            return finish('error', f'git worktree add failed: {msg}')
        st['worktree'] = str(wt)
        push()

        # Smokes import playwright from the MAIN checkout's tools/smoke
        # node_modules (gitignored, so absent from a fresh worktree). Junction it
        # in — and MUST unlink before any delete (see _awt.unlink_runtime).
        link_proj = dict(project, worktree_shared_runtime=[_SMOKE_NODE_MODULES])
        _awt.link_runtime(link_proj, wt)
        try:
            for i, ws in enumerate(cands):
                ws_id = ws.get('id')
                ws_branch = ws.get('worktree_branch') or _awt.branch_name(ws['worktree_session_id'])
                fail = _merge_one(project, wt, ws, ws_branch, sha, st)
                if fail == 'skip':
                    continue
                if fail:
                    st['failed'] = dict(fail, ws_id=ws_id, branch=ws_branch)
                    st['unmerged'] = [c.get('id') for c in cands[i:]]
                    break
                push()
        finally:
            _awt.unlink_runtime(link_proj, wt)
            if not (wt / _SMOKE_NODE_MODULES).exists():
                _git(base, ['worktree', 'remove', '--force', str(wt)], 60)
                st['worktree_removed'] = not wt.exists()
            else:
                # Never delete a tree that still holds a live junction into the
                # main checkout's node_modules.
                st['worktree_removed'] = False
        return finish('stopped' if st['failed'] else 'completed')
    except Exception as e:
        _log(f"[hivemind-integration] {hivemind_id} crashed: {e}")
        return finish('error', f'integration crashed: {e}')


def _merge_one(project, wt, ws, ws_branch, base_sha, st):
    """Merge one workstream. Returns None (merged), 'skip' (nothing to merge),
    or a failure dict {kind, detail, ...}."""
    ws_id = ws.get('id')
    sess_wt = _awt.worktree_path(project, ws['worktree_session_id'])
    if sess_wt is not None and sess_wt.exists() and _awt.dirty_outside_runtime(project, sess_wt):
        return {'kind': 'dirty',
                'detail': 'uncommitted changes in the worker worktree; its work is not on the branch'}
    ok, _ = _git(wt, ['rev-parse', '--verify', f'{ws_branch}^{{commit}}'], 15)
    if not ok:
        return {'kind': 'missing_branch', 'detail': f'branch {ws_branch} not found'}
    ok, n = _git(wt, ['rev-list', '--count', f'{base_sha}..{ws_branch}'], 30)
    if ok and n.strip() == '0':
        st['skipped'].append({'ws_id': ws_id, 'reason': 'no commits beyond the base'})
        return 'skip'
    ok, pre = _git(wt, ['rev-parse', 'HEAD'], 15)
    if not ok:
        return {'kind': 'git', 'detail': f'rev-parse HEAD failed: {pre}'}
    ok, msg = _git(wt, ['merge', '--no-edit', ws_branch], 120)
    if not ok:
        conflicted = _git(wt, ['rev-parse', '-q', '--verify', 'MERGE_HEAD'], 15)[0]
        _git(wt, ['merge', '--abort'], 30)
        return {'kind': 'conflict' if conflicted else 'merge_error', 'detail': msg[-600:]}
    ok, head = _git(wt, ['rev-parse', 'HEAD'], 15)
    smokes = clean_smokes(ws.get('smokes'))
    results = []
    for name in smokes:
        passed, detail = _run_smoke(wt, name)
        results.append({'name': name, 'ok': passed})
        if not passed:
            # Keep the integration branch smoke-green: roll this merge back.
            _git(wt, ['reset', '--hard', pre], 60)
            return {'kind': 'smoke', 'smoke': name, 'detail': detail,
                    'rolled_back_to': pre, 'smokes': results}
    st['merged'].append({
        'ws_id': ws_id, 'branch': ws_branch, 'commit': head if ok else '',
        'smokes_run': bool(smokes), 'smokes': results,
        'note': '' if smokes else 'no smokes named; none run',
    })
    return None
