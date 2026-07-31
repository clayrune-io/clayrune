"""Per-agent git worktree isolation — Phase 2 of the coordination layer.

Design: docs/COORDINATION_LAYER_DESIGN.md §5d + §11. Backlog b264200a
(isolation) composed with 9518ec62 (awareness).

Today every agent on a project shares ONE working tree, so two concurrent
agents editing the same file silently clobber each other — the loser's write
is gone with no error. This module gives each concurrent agent its own git
worktree + branch, which converts that silent data loss into a normal,
recoverable merge (clean when they touched different regions; a loud, abortable
CONFLICT when they touched the same lines — nothing lost either way).

Companion to ``project_sync.py`` and deliberately built on its primitives
(``git_run``'s argv-guarded subprocess wrapper, the ``ensure_worktree``
pattern). project_sync's worktree is ONE hidden per-INSTALL checkout for
cross-machine sync; this one is per-AGENT-SESSION and short-lived.

TWO NON-OBVIOUS REQUIREMENTS — both found empirically, both load-bearing:

  1. **A worktree only receives TRACKED files.** Everything gitignored stays
     behind: on this repo that is `.venv` (117 MB), `node_modules` (16 MB),
     `data/projects/` (the project records + backlog, 100+ files) and
     `config.json`. An agent dropped into a bare worktree cannot run the test
     suite, cannot import deps, and cannot read the backlog. So we **link**
     the runtime dirs back in (directory junctions on Windows — no admin
     needed — symlinks elsewhere). These are *shared runtime state*, not the
     source files agents conflict over, so sharing them is correct.

  2. **`.mcp.json` pins absolute paths.** The per-project filesystem MCP server
     is configured with the MAIN tree's absolute path. Copied verbatim into a
     worktree, the agent would edit the worktree via Edit/Bash while reading
     and writing the MAIN tree via MCP — a split brain strictly worse than the
     bug we're fixing. So we rewrite those paths to the worktree on create.

Everything here is best-effort and gated: a failure to build a worktree falls
back to the shared tree (today's behavior) rather than failing the dispatch.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import threading
from pathlib import Path

import project_sync as _sync

# ── Injected helpers (set by register()) ────────────────────────────────────

_log_activity = None
_load_project = None
_log = None

# Runtime dirs that are gitignored but load-bearing for an agent. Linked into
# every worktree. Order matters only for readability. `data/projects` is nested
# (its parent `data/` IS tracked), which is why entries are relative paths, not
# bare names.
DEFAULT_SHARED_RUNTIME = (
    '.venv', 'venv', 'node_modules', 'data/projects', 'data/uploads',
    'config.json', 'data/settings.json',
    # `.mcp.json` is GITIGNORED, so it never reaches a worktree on its own —
    # without copying it the agent silently loses every per-project MCP server
    # (filesystem, browser). Copied (not linked) so retarget_mcp() can rewrite
    # its absolute paths without touching the main tree's file.
    '.mcp.json',
)

# Files git leaves in an otherwise-empty tracked directory. When a shared
# runtime path arrives as a real dir containing only these, it is a placeholder
# and we replace it with the junction; anything else we leave alone.
_PLACEHOLDERS = {'.gitkeep', '.gitignore'}

_IS_WINDOWS = platform.system() == 'Windows'
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

# A session id we mint worktree names from must never escape the worktree root.
_SAFE_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def register(log_activity, load_project, log):
    """Inject server helpers — called once at startup."""
    global _log_activity, _load_project, _log
    _log_activity = log_activity
    _load_project = load_project
    _log = log


def _plog(msg: str) -> None:
    if _log:
        _log(msg)


def _lock(project_id: str) -> threading.Lock:
    with _locks_guard:
        lk = _locks.get(project_id)
        if lk is None:
            lk = threading.Lock()
            _locks[project_id] = lk
        return lk


# ── Naming ──────────────────────────────────────────────────────────────────

def worktree_root(project: dict) -> Path | None:
    base = project.get('project_path') or ''
    if not base:
        return None
    return Path(base) / '.clayrune' / 'agents'


def worktree_path(project: dict, session_id: str) -> Path | None:
    root = worktree_root(project)
    if root is None or not _SAFE_ID.match(session_id or ''):
        return None
    return root / session_id


def branch_name(session_id: str) -> str:
    return f'clayrune/agent/{session_id}'


# ── Linking shared runtime dirs (requirement #1) ────────────────────────────

def _link_dir(link: Path, target: Path) -> bool:
    """Create a directory junction (Windows) or symlink (POSIX). Junctions are
    used on Windows deliberately: unlike symlinks they need no admin rights and
    no Developer Mode."""
    try:
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            return True
        if link.exists():
            # A TRACKED placeholder dir (e.g. data/projects shipping a single
            # .gitkeep) arrives from the checkout and would otherwise make us
            # skip the junction — leaving the agent with an empty data dir that
            # merely LOOKS present. Replace it only when it holds nothing but
            # placeholders; never clobber real content.
            try:
                entries = [e.name for e in link.iterdir()]
            except Exception:
                return True
            if entries and not set(entries) <= _PLACEHOLDERS:
                return True  # real content — leave it, don't destroy anything
            shutil.rmtree(str(link), ignore_errors=True)
            if link.exists():
                return True
        if _IS_WINDOWS:
            r = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                               capture_output=True, text=True,
                               creationflags=_sync._POPEN_FLAGS,
                               startupinfo=_sync._STARTUPINFO)
            return r.returncode == 0
        os.symlink(str(target), str(link), target_is_directory=True)
        return True
    except Exception as e:
        _plog(f"[worktree] link {link.name} failed: {e}")
        return False


def _link_file(link: Path, target: Path) -> bool:
    """Files (config.json, settings.json) are COPIED, not linked — an agent
    writing config in its worktree must not mutate the live install's config."""
    try:
        if link.exists():
            return True
        link.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(target), str(link))
        return True
    except Exception as e:
        _plog(f"[worktree] copy {link.name} failed: {e}")
        return False


def _shared_runtime(project: dict) -> tuple[str, ...]:
    custom = project.get('worktree_shared_runtime')
    if isinstance(custom, (list, tuple)) and custom:
        return tuple(str(c) for c in custom)
    return DEFAULT_SHARED_RUNTIME


def link_runtime(project: dict, wt: Path) -> list[str]:
    """Link/copy the gitignored-but-needed runtime paths into a worktree.
    Returns the list of entries actually materialized."""
    base = Path(project.get('project_path') or '')
    done = []
    for rel in _shared_runtime(project):
        target = base / rel
        if not target.exists():
            continue
        link = wt / rel
        ok = _link_file(link, target) if target.is_file() else _link_dir(link, target)
        if ok:
            done.append(rel)
    return done


def unlink_runtime(project: dict, wt: Path) -> None:
    """Remove junctions/symlinks BEFORE deleting a worktree.

    LOAD-BEARING: a recursive delete that follows a junction would delete the
    REAL .venv / data/projects behind it. Always call this first — `git
    worktree remove` and any rmtree must never walk into a link.
    """
    for rel in _shared_runtime(project):
        link = wt / rel
        try:
            if not (link.exists() or link.is_symlink()):
                continue
            if link.is_dir() and not link.is_symlink() and _IS_WINDOWS:
                # Windows junction: rmdir unlinks without touching the target.
                subprocess.run(['cmd', '/c', 'rmdir', str(link)],
                               capture_output=True, text=True,
                               creationflags=_sync._POPEN_FLAGS,
                               startupinfo=_sync._STARTUPINFO)
            elif link.is_symlink():
                link.unlink()
            elif link.is_file():
                link.unlink()
        except Exception as e:
            _plog(f"[worktree] unlink {rel} failed: {e}")


# ── Rewriting .mcp.json (requirement #2) ────────────────────────────────────

def retarget_mcp(project: dict, wt: Path) -> bool:
    """Rewrite absolute main-tree paths in the worktree's .mcp.json so MCP
    servers (notably filesystem) operate on the WORKTREE, not the main tree.

    Without this the agent edits the worktree through Edit/Bash but reads and
    writes the main tree through MCP — a split brain worse than the original
    clobbering bug.
    """
    p = wt / '.mcp.json'
    if not p.exists():
        return True  # nothing to retarget
    base = str(Path(project.get('project_path') or '')).replace('\\', '/')
    if not base:
        return False
    try:
        raw = p.read_text(encoding='utf-8')
        wt_s = str(wt).replace('\\', '/')
        # Match both slash conventions the file might use.
        out = raw.replace(base, wt_s).replace(base.replace('/', '\\\\'),
                                              wt_s.replace('/', '\\\\'))
        if out != raw:
            p.write_text(out, encoding='utf-8')
        return True
    except Exception as e:
        _plog(f"[worktree] mcp retarget failed: {e}")
        return False


# ── Lifecycle ───────────────────────────────────────────────────────────────

def create(project: dict, session_id: str, base_ref: str = 'HEAD'):
    """Create an isolated worktree for one agent session.

    Returns (ok, path_or_error). Best-effort: on ANY failure the caller should
    fall back to the shared project_path (today's behavior) rather than fail
    the dispatch.
    """
    pid = project.get('id', '')
    base = project.get('project_path') or ''
    if not _sync.is_git_repo(base):
        return False, 'not a git repository'
    wt = worktree_path(project, session_id)
    if wt is None:
        return False, 'bad session id or project_path'

    with _lock(pid):
        if wt.exists() and (wt / '.git').exists():
            return True, str(wt)
        if wt.exists():
            return False, f'{wt} exists but is not a worktree'
        wt.parent.mkdir(parents=True, exist_ok=True)
        branch = branch_name(session_id)
        # Reuse the branch if a prior run left it behind.
        ok, refs = _sync.git_run(base, ['branch', '--list', branch])
        have = ok and refs.strip() != ''
        args = (['worktree', 'add', str(wt), branch] if have
                else ['worktree', 'add', '-b', branch, str(wt), base_ref])
        ok, msg = _sync.git_run(base, args, timeout=120)
        if not ok:
            return False, f'git worktree add failed: {msg}'

        linked = link_runtime(project, wt)
        retarget_mcp(project, wt)
        _sync._ensure_gitignore_entry(base, '.clayrune/')
        _plog(f"[worktree] created {wt.name} on {branch} (linked: {', '.join(linked) or 'none'})")
        if _log_activity:
            _log_activity(pid, f'Agent worktree created: {session_id[:8]} on {branch}')
        return True, str(wt)


def remove(project: dict, session_id: str, delete_branch: bool = False):
    """Tear down an agent worktree. Unlinks runtime junctions FIRST so the
    delete can never follow one into the real .venv / data/projects."""
    pid = project.get('id', '')
    base = project.get('project_path') or ''
    wt = worktree_path(project, session_id)
    if wt is None or not wt.exists():
        return True, 'nothing to remove'
    with _lock(pid):
        unlink_runtime(project, wt)          # MUST precede any delete
        ok, msg = _sync.git_run(base, ['worktree', 'remove', '--force', str(wt)],
                                timeout=60)
        if not ok:
            # Fall back to a manual delete, but only after unlinking above.
            try:
                shutil.rmtree(str(wt), ignore_errors=True)
                _sync.git_run(base, ['worktree', 'prune'])
            except Exception as e:
                return False, f'remove failed: {e} / {msg}'
        if delete_branch:
            _sync.git_run(base, ['branch', '-D', branch_name(session_id)])
        _plog(f"[worktree] removed {session_id[:12]}")
        return True, 'removed'


def list_worktrees(project: dict) -> list[str]:
    """Session ids that currently have a worktree on disk."""
    root = worktree_root(project)
    if root is None or not root.exists():
        return []
    return [d.name for d in root.iterdir() if d.is_dir()]


def gc_stale(project: dict, live_session_ids) -> int:
    """Remove worktrees whose session is no longer live. Called at startup and
    periodically — a hard kill (or an MC crash) otherwise leaks them forever,
    the same failure class as the watermark-GC gap that truncated the memory
    index. NEVER removes a live session's tree."""
    live = set(live_session_ids or ())
    removed = 0
    for sid in list_worktrees(project):
        if sid in live:
            continue
        ok, _ = remove(project, sid, delete_branch=False)
        if ok:
            removed += 1
    if removed:
        _plog(f"[worktree] gc removed {removed} stale worktree(s) "
              f"for {project.get('id','')}")
    return removed


# ── Propagation (Phase 3 — turn-gated sync) ─────────────────────────────────

def has_uncommitted(project: dict, session_id: str) -> bool:
    wt = worktree_path(project, session_id)
    if wt is None or not wt.exists():
        return False
    return _sync._dirty(str(wt))


def sync_into(project: dict, session_id: str, source_ref: str = 'master'):
    """Merge `source_ref` into one agent's worktree — the "awareness becomes
    availability" step.

    Returns (status, detail) where status is one of:
      'skipped'  — nothing to do, or the tree is dirty (agent mid-edit)
      'clean'    — merged, the agent silently gained the sibling's work
      'conflict' — overlapping edits; the merge was ABORTED and the tree left
                   exactly as the agent had it. Escalate; never auto-resolve.

    CALLER CONTRACT: only invoke between an agent's turns, never while its
    session status is 'running'. Merging a tree mid-edit is precisely the
    clobbering this whole feature exists to prevent.
    """
    wt = worktree_path(project, session_id)
    if wt is None or not wt.exists():
        return 'skipped', 'no worktree'
    wts = str(wt)
    if _sync._dirty(wts):
        # Uncommitted work in flight — merging would fight the agent's edits.
        return 'skipped', 'worktree dirty'
    ok, _ = _sync.git_run(wts, ['rev-parse', '--verify', source_ref], timeout=10)
    if not ok:
        return 'skipped', f'unknown ref {source_ref}'
    behind_ahead = _sync._ahead_behind(wts, 'HEAD', source_ref)
    if behind_ahead[1] == 0:
        return 'skipped', 'already up to date'
    ok, msg = _sync.git_run(wts, ['merge', '--no-edit', source_ref], timeout=120)
    if ok:
        return 'clean', msg
    # Conflict (or any merge failure): restore the agent's tree exactly.
    _sync.git_run(wts, ['merge', '--abort'], timeout=30)
    return 'conflict', msg
