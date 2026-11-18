"""Agent-proposed backlog items (Ron, 2026-11-17).

An item an agent adds is stored LABELLED -- `status: 'proposed'` plus a
`proposed_by` the SERVER derives from who is calling -- and waits for a person.
Nothing here trusts the request body: the label comes from the same attribution
the human-only gate uses (`mc.human_only_gate`: browser Origin, then the OS
lookup that maps the loopback peer to a managed agent session), and the session
named in `proposed_by` comes from the same lookup `/agent/dispatch` records as
its spawner (`mc.dispatch_lineage.attributed_caller_session`). No second
attribution lives here.

`proposed` is deliberately NOT a status the PATCH route accepts
(`ui_field_validation._STATUSES`); the one door out of it into live work is the
human-only Accept route (`mc/blueprints/backlog_proposal_routes.py`). Every
reader that means "work to do" must skip it -- `is_proposed()` is the one test.
An item with no `proposed_by` is an ordinary item (no migration).
"""
from __future__ import annotations

from flask import jsonify, request

PROPOSED = 'proposed'
DISMISSED = 'dismissed'
SOURCE = 'agent'


def is_proposed(item) -> bool:
    """True while the item waits for a person: listed, but not work."""
    return isinstance(item, dict) and item.get('status') == PROPOSED


def is_dismissed(item) -> bool:
    """A closed proposal a person turned down (status done, done_reason dismissed)."""
    return isinstance(item, dict) and item.get('done_reason') == DISMISSED


def is_work(item) -> bool:
    """False for an item nobody has accepted, or one a person dismissed."""
    return not is_proposed(item) and not is_dismissed(item)


def _non_human_caller() -> bool:
    # Composed exactly as mc.human_only_gate.refuse_non_human composes it.
    from mc.project_safety_settings import is_agent_caller
    from mc.unattended import is_unattended_caller
    return bool(is_unattended_caller() or is_agent_caller())


def _caller_session() -> tuple[str, dict]:
    """(session_id, session record) the request descends from, or ('', {})."""
    from mc import dispatch_lineage, state
    sid = dispatch_lineage.attributed_caller_session(
        request, state.agent_sessions, state.tracked_processes)
    session = state.agent_sessions.get(sid) if sid else None
    return sid, (session if isinstance(session, dict) else {})


def proposer_from_request() -> dict | None:
    """`proposed_by` for the current request, or None when a person is calling.

    Fails closed: a caller with no Origin that the OS cannot name is still an
    agent (an unattributed curl), recorded with an empty session_id and
    attended False.
    """
    if not _non_human_caller():
        return None
    sid, session = _caller_session()
    return {
        'kind': 'agent',
        'session_id': sid,
        'agent_name': str((session.get('character') or {}).get('agent_name') or ''),
        'attended': session.get('trigger_type') == 'manual',
    }


def stamp_proposed(item: dict, proposer: dict) -> None:
    """Label a freshly built item as an agent's proposal (the server's, not the body's)."""
    item['status'] = PROPOSED
    item['source'] = SOURCE
    item['proposed_by'] = proposer


def refuse_proposal_patch(item: dict, data: dict):
    """A (response, status) to return from PATCH, or None to carry on.

    - nobody moves a proposal to live work through PATCH: 409, Accept is the door;
    - an agent may edit only a proposal it made itself, and the attribution that
      proves it is the server's: 403 for any other agent and for an unattributed
      caller (it cannot show the proposal is its own).
    A person at the dashboard may edit text or priority, or close it.
    """
    if not is_proposed(item):
        return None
    new_status = data.get('status')
    if new_status is not None and new_status not in ('done', 'wontdo'):
        return jsonify({'error': 'proposed item: a person must Accept it first',
                        'status': PROPOSED}), 409
    if not _non_human_caller():
        return None
    sid, _ = _caller_session()
    owner = ((item.get('proposed_by') or {}).get('session_id') or '')
    if not sid or sid != owner:
        return jsonify({'error': 'proposed item: only the agent that proposed it '
                                 'or a person can change it'}), 403
    return None
