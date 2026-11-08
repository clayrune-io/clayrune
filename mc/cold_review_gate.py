"""MC-1165: what `merge_back` asks when a project demands a cold review.

Two jobs, both small:

  * `hold_reason()` -- the existing review gate's answer with `require_cold` set,
    so only a pass written by the server-side cold reviewer counts; a pass
    recorded by hand or by a reviewer session does not. When the review has
    failed it leads with the attempt count and last error, so the merge state a
    human reads (`awaiting_review` detail -> activity feed) says what happened,
    not just a log line.
  * attempt state -- how many cold reviews have been tried for a tip SHA and how
    the last one failed. Stored in the SAME review store (`mc.merge_review_gate`,
    side section `cold_attempts`), not a second file.

The attempt cap is per tip SHA: a new commit is a new SHA and starts at zero. A
tip that has used its attempts without a verdict stays held until a human acts
(merge by hand after their own review, or commit a change). There is no HTTP
route that resets it.
"""
from __future__ import annotations

from datetime import datetime, timezone

import mc.merge_review_gate as _gate

SECTION = 'cold_attempts'
MAX_ATTEMPTS = 2
_ERROR_CHARS = 120


def _enabled(project: dict) -> bool:
    # Imported here, not at module top: `mc.cold_review` pulls in
    # `mc.dev_harness_diff`, which imports `mc.agent_worktree`, which imports this
    # module. A top-level import closed that loop and broke every importer of
    # `agent_worktree` that started from `dev_harness_diff`.
    from mc import cold_review
    return cold_review.enabled(project)


def required(project: dict) -> bool:
    return _enabled(project)


def gated(project: dict) -> bool:
    """Does `merge_back` run the review hold for this project at all?"""
    return _gate.enabled(project) or _enabled(project)


def _key(branch: str, sha: str) -> str:
    return f'{branch}@{sha.lower()}'


def attempts(project_id: str, branch: str, sha: str) -> dict:
    """`{'count': int, 'last_error': str, 'at': str}`; zeros when none."""
    raw = _gate.read_section(project_id, SECTION).get(_key(branch, sha))
    if not isinstance(raw, dict):
        return {'count': 0, 'last_error': '', 'at': ''}
    count = raw.get('count')
    return {'count': count if isinstance(count, int) and count > 0 else 0,
            'last_error': str(raw.get('last_error') or '')[:_ERROR_CHARS],
            'at': str(raw.get('at') or '')}


def begin_attempt(project_id: str, branch: str, sha: str) -> int:
    """Count an attempt BEFORE the model call, so a crash mid-call still spends
    it. Returns the new count."""
    out = [0]

    def apply(sec):
        cur = sec.get(_key(branch, sha))
        n = cur.get('count') if isinstance(cur, dict) else 0
        n = n if isinstance(n, int) and n > 0 else 0
        out[0] = n + 1
        sec[_key(branch, sha)] = {'count': out[0], 'last_error': '', 'at': _now()}
    _gate.update_section(project_id, SECTION, apply)
    return out[0]


def note_error(project_id: str, branch: str, sha: str, error: str) -> None:
    def apply(sec):
        cur = sec.get(_key(branch, sha))
        entry: dict[str, object] = dict(cur) if isinstance(cur, dict) else {'count': 1}
        entry['last_error'] = str(error)[:_ERROR_CHARS]
        entry['at'] = _now()
        sec[_key(branch, sha)] = entry
    _gate.update_section(project_id, SECTION, apply)


def exhausted(project_id: str, branch: str, sha: str) -> bool:
    return attempts(project_id, branch, sha)['count'] >= MAX_ATTEMPTS


def hold_reason(project: dict, branch: str, tip: str) -> str:
    """'' when the tip may land under this project's review settings."""
    pid = (project or {}).get('id', '')
    base = _gate.hold_reason(pid, branch, tip, require_cold=required(project))
    if not base or not required(project) or not tip:
        return base
    a = attempts(pid, branch, tip)
    if not a['count']:
        return base
    state = f"cold review attempt {a['count']}/{MAX_ATTEMPTS}"
    if a['last_error']:
        state += f", last error: {a['last_error']}"
        if a['count'] >= MAX_ATTEMPTS:
            state += '; out of attempts, needs a human'
    return f'{state} - {base}'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')
