"""Merge review gate (MC-1075): no agent branch lands on the base branch unless
an adversarial review of its CURRENT tip SHA is on record with verdict `pass`.

Why a gate and not a convention: a builder's own green tests are the author
vouching for the author. The review has to be a separate session's verdict, on
the exact commit that would land, or "reviewed" means nothing the moment the
branch gains one more commit.

Three pieces, all in this file:

  * the record store -- one JSON file per project under
    `<data root>/data/merge_reviews/`, OUTSIDE `data/projects/` (the DATA_DIR
    pollution rule in CLAUDE.md: anything else in there becomes a malformed
    "project" and 500s both restart endpoints). Keyed by branch + tip SHA, so a
    record for an older SHA never counts for a newer tip.
  * `record()` -- validates and writes one review. Refuses a self-review (the
    reviewer session is the branch owner) and a SHA that is not the branch tip.
  * `verdict_for()` / `enabled()` -- what `agent_worktree.merge_back` asks.

The gate is a PROJECT setting, `merge_requires_review`, and there is no code
default: absent means off, so a fresh install (which may have no reviewer
agent at all) behaves exactly as before. Turning it on is a write to that
project's own record.

Reading is fail-closed: a missing, unreadable or corrupt store reads as "no
review on record", which holds the merge. Writing never loses a corrupt store
silently -- it is moved aside as `.corrupt` first.

Stdlib + `mc.project_sync` + `mc.atomic_json` only, so `tools/merge-review-check.py`
can import it without starting anything.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import mc.project_sync as _sync
from mc.atomic_json import write_json_atomic

SETTING = 'merge_requires_review'
BRANCH_PREFIX = 'clayrune/agent/'
STORE_DIRNAME = 'merge_reviews'

PASS = 'pass'
CHANGES_REQUESTED = 'changes_requested'
VERDICTS = (PASS, CHANGES_REQUESTED)
NONE = 'none'   # verdict_for(): nothing on record for this exact SHA

_FULL_SHA = re.compile(r'^(?:[0-9a-f]{40}|[0-9a-f]{64})$')
_SAFE_PROJECT_ID = re.compile(r'^[A-Za-z0-9_-]{1,80}$')
_MAX_TEXT = 500

# Tests and tools/merge-review-check.py point the store somewhere explicit;
# None = derive from the server's data root (project_sync, wired at startup).
STORE_DIR: Path | None = None
_write_lock = threading.Lock()


class ReviewRefused(Exception):
    """A review record that must not be written. `status` is the HTTP code the
    route answers with; `message` is safe to show the caller."""

    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


def enabled(project: dict) -> bool:
    """Is the gate on for this project? Strict `is True`: a stray string or 1
    in a hand-edited record must not silently switch a safety gate on or off
    differently from how the setting reads in the UI."""
    return (project or {}).get(SETTING) is True


def store_dir() -> Path | None:
    if STORE_DIR is not None:
        return Path(STORE_DIR)
    root = getattr(_sync, '_data_root', None)
    return None if root is None else Path(root) / 'data' / STORE_DIRNAME


def store_path(project_id: str) -> Path | None:
    d = store_dir()
    if d is None or not _SAFE_PROJECT_ID.match(project_id or ''):
        return None
    return d / f'{project_id}.json'


def branch_tip(project: dict, branch: str) -> str:
    """Full SHA the agent branch points at, '' when it cannot be resolved.
    Read from the base checkout's refs, not the worktree's HEAD, so it is the
    same answer a manual `git merge <branch>` there would act on."""
    base = (project or {}).get('project_path') or ''
    if not base or not branch or branch.startswith('-'):
        return ''
    ok, out = _sync.git_run(base, ['rev-parse', '--verify', '--quiet',
                                   f'refs/heads/{branch}^{{commit}}'], timeout=10)
    out = out.strip().lower()
    return out if ok and _FULL_SHA.match(out) else ''


def _key(branch: str, sha: str) -> str:
    return f'{branch}@{sha}'


def _read(path: Path) -> dict:
    """The `records` map, or {} for anything unreadable. Never raises."""
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        recs = data.get('records') if isinstance(data, dict) else None
        return recs if isinstance(recs, dict) else {}
    except Exception:
        return {}


def find(project_id: str, branch: str, sha: str) -> dict | None:
    """The review on record for exactly this branch at exactly this SHA."""
    path = store_path(project_id)
    if path is None or not sha:
        return None
    rec = _read(path).get(_key(branch, sha.lower()))
    if not isinstance(rec, dict) or rec.get('verdict') not in VERDICTS:
        return None
    if rec.get('sha') != sha.lower() or rec.get('branch') != branch:
        return None
    return rec


def verdict_for(project_id: str, branch: str, sha: str) -> str:
    """PASS, CHANGES_REQUESTED, or NONE for this exact branch tip."""
    rec = find(project_id, branch, sha)
    return rec['verdict'] if rec else NONE


def rejected_by(project_id: str, sha: str) -> str:
    """The branch whose review of exactly `sha` requested changes, '' when no
    review of it did. Looked up across every branch name: a rejection is of
    the commit, so renaming or recreating the branch, or reviewing the same
    commit under a second branch, must not leave it behind."""
    path = store_path(project_id)
    if path is None or not sha:
        return ''
    sha = sha.lower()
    for rec in _read(path).values():
        if (isinstance(rec, dict) and rec.get('sha') == sha
                and rec.get('verdict') == CHANGES_REQUESTED):
            return str(rec.get('branch') or '?')
    return ''


def hold_reason(project_id: str, branch: str, tip: str) -> str:
    """Why the merge is held, '' when the tip has a pass on record and no
    review of that commit, under any branch name, requested changes. One place
    for the wording so merge_back, the CLI and the hook say the same thing."""
    if not tip:
        return f'cannot resolve the tip of {branch}'
    other = rejected_by(project_id, tip)
    if other and other != branch:
        return f'review of {other} at {tip[:8]} requested changes on this commit'
    v = verdict_for(project_id, branch, tip)
    if v == PASS:
        return ''
    if v == CHANGES_REQUESTED:
        return f'review of {branch} at {tip[:8]} requested changes'
    return f'no review on record for {branch} at {tip[:8]}'


def _text(value, limit: int = _MAX_TEXT) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ''


def record(project: dict, branch: str, *, sha, verdict, reviewer_session: str = '',
           reviewer_character: str = '', report_path: object = '', attribution: str = '') -> dict:
    """Write one review. Raises ReviewRefused; returns the stored record.

    `reviewer_session` is the session the CALLER was attributed to by the
    server (never a value the caller typed) -- '' means an unattributed caller
    (a human, or a process outside every managed session's tree). The latest
    review of a given branch+SHA replaces the earlier one, so a later
    `changes_requested` withdraws an earlier `pass` on the same commit."""
    pid = (project or {}).get('id') or ''
    if not branch.startswith(BRANCH_PREFIX) or len(branch) == len(BRANCH_PREFIX):
        raise ReviewRefused(400, f'not an agent branch: {branch!r}')
    if verdict not in VERDICTS:
        raise ReviewRefused(400, f'verdict must be one of {list(VERDICTS)}')
    if not isinstance(sha, str) or not _FULL_SHA.match(sha.strip().lower()):
        raise ReviewRefused(400, 'sha must be the full commit sha of the branch tip')
    sha = sha.strip().lower()
    owner = branch[len(BRANCH_PREFIX):]
    if reviewer_session and reviewer_session == owner:
        raise ReviewRefused(403, 'self-review refused: the reviewer session is the '
                                 'owner of this branch')
    tip = branch_tip(project, branch)
    if not tip:
        raise ReviewRefused(404, f'branch {branch} not found')
    if sha != tip:
        raise ReviewRefused(409, f'sha {sha[:8]} is not the tip of {branch} '
                                 f'(tip is {tip[:8]}); review the current tip', tip=tip)
    path = store_path(pid)
    if path is None:
        raise ReviewRefused(500, 'review store is not available for this project')
    rec = {
        'sha': sha, 'branch': branch, 'verdict': verdict,
        'reviewer_session': reviewer_session, 'reviewer_character': _text(reviewer_character, 120),
        'report_path': _text(report_path),
        'attribution': attribution or ('attributed' if reviewer_session else 'unattributed'),
        'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
    }
    with _write_lock:
        records = _read(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not records and path.exists():
            # `_read` found nothing in an existing file: empty store or an
            # unparseable one. Keep an unparseable one rather than overwrite it.
            try:
                json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                path.replace(path.with_name(path.name + '.corrupt'))
        records[_key(branch, sha)] = rec
        write_json_atomic(path, {'version': 1, 'records': records},
                          indent=2, ensure_ascii=False)
    return rec
