"""Definition-to-Goal binding for Desk channel impact (MC-1152 P1, DARK).

Spec: `docs/desk_v1/CHANNEL_IMPACT_SPEC.md` section 5, last paragraph, and
section 12 row P1. The impact definition is the source-of-truth MEASUREMENT
binding for a campaign; it is not a second goal. The campaign's own `goal`
(target, deadline, manual entries) stays where it is, in `data/desk.json`, and
this module only reads it.

What it guarantees, each pinned in tests/test_desk_impact_binding.py:

  * No definition is a valid state: the projection still returns the goal.
  * The goal is returned as stored. Attaching, changing or retiring a definition
    never writes the goal, so manual entries and `goal.source == 'manual'` keep
    their meaning (`mc.desk.goal_current` is untouched).
  * A new definition revision starts a distinct series; earlier revisions stay
    listed and are never summed into it.
  * Automatic goal progress is DARK: `auto_progress.state` is always `'dark'`
    and its value always None until a tested binding exists. This module does
    not compute a number from snapshots, because GitHub observations are
    repository context and cannot become campaign progress (spec section 2, D1).

Nothing imports this yet: no route, no UI. The Goal screen displaying and
editing the binding is P4.
"""
from __future__ import annotations

import copy
from typing import Any

from mc.desk_impact_store import ImpactStore

AUTO_PROGRESS_DARK = {
    'state': 'dark', 'value': None,
    'reason': 'Automatic goal progress is not enabled: no tested binding from a '
              'conversion source to this campaign goal exists yet.',
}


def goal_binding(store: ImpactStore, project_id: Any, campaign_id: Any) -> dict:
    """The campaign's goal beside its conversion definition, read-only.

    Returns `{goal, definition, revisions, auto_progress}`. `goal` is a deep
    copy of the stored goal (or `{}`), `definition` the active revision record
    or None, `revisions` every revision oldest first (inspectable history, one
    series each).
    """
    camp = store.get_campaign(project_id, campaign_id)
    defs = store.get_definitions(project_id, campaign_id)
    goal = camp.get('goal')
    return {
        'goal': copy.deepcopy(goal) if isinstance(goal, dict) else {},
        'definition': defs['active'],
        'revisions': defs['revisions'],
        'auto_progress': copy.deepcopy(AUTO_PROGRESS_DARK),
    }
