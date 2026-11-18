"""Accept / Dismiss for an agent-proposed backlog item (Ron, 2026-11-17).

An item an agent adds is stored `status: 'proposed'` (mc/backlog_proposals.py)
and waits for a person. These two routes are the only way out of that state,
and both are human-only -- agent and unattended callers are refused by
attribution before anything is read or saved, the same gate as
`hivemind_synthesize_routes`.

  accept   proposed -> open. `proposed_by` stays, so the item still says who
           suggested it; `accepted_at` / `accepted_by` record the decision.
  dismiss  proposed -> done with `done_reason: 'dismissed'`. Nothing is deleted;
           the text, the proposer and the history stay on the record.

404 for an unknown item; 409 when the item is not (or no longer) proposed.
"""
from __future__ import annotations

from flask import Blueprint, jsonify

from mc.human_only_gate import human_only

bp = Blueprint('backlog_proposal_routes', __name__)


def _decide(project_id, item_id, verb):
    from mc import backlog_proposals as bp_mod
    from mc.blueprints import project_routes as pr
    from mc.core import now_iso, record_backlog_status_change

    p = pr.load_project(project_id)
    if p is None:
        return jsonify({'error': 'project not found'}), 404
    item = next((i for i in p.get('backlog', []) if i.get('id') == item_id), None)
    if item is None:
        return jsonify({'error': 'item not found'}), 404
    if not bp_mod.is_proposed(item):
        return jsonify({'error': f'item is not a proposal (status {item.get("status")!r})'}), 409

    now = now_iso()
    if verb == 'accept':
        record_backlog_status_change(item, 'open', by='dashboard', ts=now)
        item['status'] = 'open'
        item['accepted_at'] = now
        item['accepted_by'] = 'dashboard'
    else:
        record_backlog_status_change(item, 'done', by='dashboard', ts=now)
        item['status'] = 'done'
        item['done_at'] = now
        item['done_reason'] = bp_mod.DISMISSED
    item['updated_at'] = now
    p['last_updated'] = now
    pr.save_project(project_id, p)
    return jsonify({'ok': True, 'item': item})


@bp.route('/api/project/<project_id>/backlog/<item_id>/accept', methods=['POST'])
@human_only('accept an agent-proposed backlog item')
def accept_proposal(project_id, item_id):
    return _decide(project_id, item_id, 'accept')


@bp.route('/api/project/<project_id>/backlog/<item_id>/dismiss', methods=['POST'])
@human_only('dismiss an agent-proposed backlog item')
def dismiss_proposal(project_id, item_id):
    return _decide(project_id, item_id, 'dismiss')
