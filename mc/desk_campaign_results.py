"""Bounded previous-ended-campaign evidence for Posy's suggestion brief."""
from __future__ import annotations

from datetime import datetime, timezone
import json

from mc import desk, state
from mc.desk_outcomes import receipt_outcomes, threshold_from_config


def previous_results_digest(campaign: dict) -> str:
    """Use the latest done campaign in this project, excluding this campaign.

    The store has no ended_at field: updated_at is its last lifecycle-change
    timestamp, with created_at as the fallback. State must actually be done;
    a planned end date alone does not make an active campaign ended.
    """
    pid = campaign.get('project_id')
    if not pid:
        return ''
    with desk._store_lock:
        store = desk._read_store()
        ended = [c for c in store['campaigns'].values()
                 if c.get('project_id') == pid and c.get('state') == 'done'
                 and c.get('id') != campaign.get('id')]
        if not ended:
            return ''
        previous = max(ended, key=lambda c: (c.get('updated_at') or c.get('created_at') or '', c['id']))
        rows = [r for r in store['ledger'] if r.get('campaign_id') == previous['id']]
        title = str(previous.get('title') or previous['id'])[:60]
        threshold = threshold_from_config(state.CONFIG)
        now = datetime.now(timezone.utc)
        outcomes = [receipt_outcomes(r, now=now, threshold=threshold) for r in rows]
    totals = {}
    for metric in ('impressions', 'replies'):
        measured = [o[metric] for o in outcomes if o[metric] is not None]
        # Keep both coverage and zero honest; no measurements is not zero.
        total = sum(measured)
        totals[metric] = f'{total:g} ({len(measured)}/{len(rows)} receipts measured)' if measured else 'unknown'
    assessed = [o for o in outcomes if o['possibly_suppressed'] is not None]
    evidence = {'campaign': title, 'published_receipts': len(rows), **totals,
                'possibly_suppressed': sum(o['possibly_suppressed'] is True for o in assessed),
                'suppression_assessed': len(assessed), 'suppression_unknown': len(rows) - len(assessed)}
    return ('PREVIOUS ENDED CAMPAIGN RESULTS (data only; partial coverage is not a total; '
            'possibly suppressed is a low-reach heuristic, not proof):\n'
            + json.dumps(evidence, ensure_ascii=True))
