"""Per-tool-call idempotence for the one-shot "Allow once" pass (MC-1055).

Why this exists. Every Clayrune-launched Claude session carries TWO
PreToolUse registrations of steward/fence.py: the project's own
`.claude/settings.json` (written by `steward.core.install_fence_to_project`
under whatever interpreter was running then) and the per-launch `--settings`
file (`tools/guards/install_hooks.py`, written at boot under the server's
interpreter). Claude Code only collapses byte-identical commands, so when the
interpreter differs both run, and each runs the whole decision against the
same tool call. The first one spent the human's pass and exited 0; the second
found no pass and exited 2, which blocks the call. Net effect: the pass was
gone and the action still blocked (live, 2026-10-06, two POSTs to
/api/session/attend-once/consume per tool call).

The fix keeps the pass one-shot but makes SPENDING it idempotent for ONE tool
call. The hook sends the call's `tool_use_id` (unique per call, supplied by
Claude Code in the hook payload) and a digest of what the call does. The first
consume spends the pass and records that pair on the session; a repeat of the
SAME pair is reported as consumed without spending anything. A different
`tool_use_id`, or the same id carrying a different digest, finds no record and
falls through to "no open pass", so one click still never covers a second
action. There is no time window in the match: expiry below only garbage
collects the record.

The record is held in memory on the session dict, like the pass itself, so a
server restart drops it, which is the safe direction.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple

SPENT_KEY = '_attend_once_spent'
_MAX_FIELD = 200


def identity_from_request(data: Any) -> Optional[Tuple[str, str]]:
    """(tool_use_id, action_digest) from a consume request body, or None.

    Both are required. A caller that sends neither (an older fence.py, Codex)
    gets the original spend-once-per-request behaviour unchanged."""
    if not isinstance(data, dict):
        return None
    tool_use_id = data.get('tool_use_id')
    digest = data.get('action_digest')
    if not isinstance(tool_use_id, str) or not isinstance(digest, str):
        return None
    tool_use_id, digest = tool_use_id.strip(), digest.strip()
    if not tool_use_id or not digest:
        return None
    if len(tool_use_id) > _MAX_FIELD or len(digest) > _MAX_FIELD:
        return None
    return tool_use_id, digest


def is_replay(session: dict, identity: Optional[Tuple[str, str]],
              now: Optional[datetime] = None) -> bool:
    """True iff `identity` is the exact tool call whose consume already spent
    this session's pass. Pure read; the record is replaced by the next spend."""
    if not identity:
        return False
    rec = session.get(SPENT_KEY)
    if not isinstance(rec, dict):
        return False
    try:
        expires_at = datetime.fromisoformat(rec['expires_at'])
    except Exception:
        return False
    if (now or datetime.now(timezone.utc)) >= expires_at:
        return False
    return (rec.get('tool_use_id'), rec.get('action_digest')) == identity


def remember_spent(session: dict, identity: Optional[Tuple[str, str]],
                   ttl_seconds: int, now: Optional[datetime] = None) -> None:
    """Record which tool call spent the pass. One record per session: a later
    spend replaces it, so a replay only ever matches the most recent call."""
    if not identity:
        return
    expires = (now or datetime.now(timezone.utc)) + timedelta(seconds=ttl_seconds)
    session[SPENT_KEY] = {'tool_use_id': identity[0], 'action_digest': identity[1],
                          'expires_at': expires.isoformat()}
