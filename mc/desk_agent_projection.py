"""Manual-channel projection for the Desk agent channel (MC-1151 P1, DARK).

Spec: `docs/desk_v1/AGENT_CHANNEL_SPEC.md` sections 6 and 7. A campaign's
agent-listings checklist is a MANUAL channel: it has a stable identity for
measurement and NO publishing capability. This module turns a stored
checklist document into the typed shape shared views (Goal, the channel
overview, MC-1152's adapter) may read, so none of them has to reach into the
store's layout.

What this projection must never become, and why:

  * It is NOT a `plan.accounts` row and has no `account_id`, `platform` or
    provider field. Campaign approval hashes `plan.accounts` as its publishing
    bound (`mc/desk.py` `_campaign_bounds`); a fake account would silently
    widen what an approval covers. `publish_capability` is always `'none'`.
  * It is NOT a composer or calendar destination and consumes no cadence
    slot -- the explicit `False`/`0` fields say so to any consumer.
  * Readiness is never "coverage": not-applicable entries are excluded from
    the counts, and no percentage is produced.

FAIL CLOSED ON READINESS. `Ready to copy` requires a `pass` lint result bound
to the entry's CURRENT revision bytes and evidence AND to the linter/rules/
template versions the caller says are current. P2 owns those versions; until
a caller supplies them (`current_versions=None`, every P1 caller) a pass is
reported as `unreviewed`, because a pass under unknown rules is not a pass.

Pure: reads the document it is given, never the store or the campaign.
"""
from __future__ import annotations

from typing import Any

from mc import desk_agent_schema as S
from mc.desk_agent_store import current_revision, revision_evidence_hash

CHANNEL_TYPE = 'agent_listings'

PREPARATION_STATES = ('needs_information', 'draft', 'needs_changes',
                      'unreviewed', 'ready_to_copy')
EXTERNAL_STATES = ('not_reported', 'submitted', 'listed', 'rejected', 'withdrawn')


def _lint_for(doc: dict, rev: dict) -> dict | None:
    """Newest lint result bound to this revision's exact bytes and evidence."""
    ev_hash = revision_evidence_hash(doc, rev)
    bound = [x for x in doc['lint_results'].values()
             if x['revision_id'] == rev['id']
             and x['content_hash'] == rev['content_hash']
             and x['evidence_hash'] == ev_hash
             and x['template_version'] == rev['template_version']]
    if not bound:
        return None
    return max(bound, key=lambda x: x['created_at'])


def preparation_state(doc: dict, entry: dict,
                      current_versions: dict | None = None) -> str | None:
    """Section 6's derived preparation status, or None when not applicable."""
    if entry['applicability'] == 'not_applicable':
        return None
    if entry['applicability'] == 'needs_information':
        return 'needs_information'
    rev = current_revision(doc, entry)
    if rev is None:
        return 'draft'
    lint = _lint_for(doc, rev)
    if lint is None:
        return 'draft'
    if lint['result'] == 'refuse':
        return 'needs_changes'
    if lint['result'] == 'unreviewed':
        return 'unreviewed'
    if not current_versions:
        return 'unreviewed'
    if (lint['linter_version'] != current_versions.get('linter_version')
            or lint['rules_version'] != current_versions.get('rules_version')):
        return 'unreviewed'
    want_template = (current_versions.get('template_versions') or {}).get(entry['surface_id'])
    if want_template != rev['template_version']:
        return 'unreviewed'
    return 'ready_to_copy'


def external_state(doc: dict, entry: dict) -> str:
    """Latest user-reported outcome not superseded by a later correction."""
    events = [e for e in doc['submission_events'] if e['entry_id'] == entry['id']]
    superseded = {e['supersedes'] for e in events if e['supersedes']}
    live = [e for e in events if e['id'] not in superseded]
    return live[-1]['outcome'] if live else 'not_reported'


def project_manual_channel(doc: dict, *, current_versions: dict | None = None) -> dict:
    ck = doc['checklist']
    entries: list[dict[str, Any]] = []
    applicable = ready = 0
    for eid in ck['entry_ids']:
        entry = doc['entries'][eid]
        prep = preparation_state(doc, entry, current_versions)
        entries.append({
            'entry_id': eid,
            'surface_id': entry['surface_id'],
            'surface_group': ('documentation' if entry['surface_id'] in S.DOCS_SURFACE_IDS
                              else 'listing'),
            'artifact_id': entry['artifact_id'],
            'lifecycle': entry['lifecycle'],
            'applicability': entry['applicability'],
            'preparation': prep,
            # User-reported, not independently verified (spec section 6).
            'external_outcome': external_state(doc, entry),
            'external_outcome_basis': 'user_reported',
        })
        if entry['lifecycle'] == 'active' and prep is not None:
            applicable += 1
            ready += prep == 'ready_to_copy'
    return {
        'kind': 'manual_channel',
        'channel_type': CHANNEL_TYPE,
        'channel_id': ck['channel_id'],
        'checklist_id': ck['id'],
        'project_id': ck['project_id'],
        'campaign_id': ck['campaign_id'],
        'publish_capability': 'none',
        'composer_destination': False,
        'calendar_destination': False,
        'cadence_slots': 0,
        'archived': bool(ck.get('archived_at')),
        'entries': entries,
        # Counts exclude not-applicable and removed entries. No percentage:
        # preparation is an operational state, not acquisition coverage.
        'readiness': {'applicable': applicable, 'ready_to_copy': ready},
    }
