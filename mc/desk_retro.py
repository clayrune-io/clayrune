"""The Desk — retro computation. Spec: `docs/THE_DESK_V1_IA_REVISION_2.md` §10
(outcome learning loop), ticket R1-L.

This is the deterministic half of the loop: `retro_verdict` decides whether a
dimension's two arms show a real difference, by CODE, never a model — §10.1
is explicit that "the model only words the retro summary and an optional
one-line `maybe_why`; it never picks a winner the table did not already
produce." `run_retro` groups that per-dimension into candidate findings and
hands them to `mc/desk.py`'s playbook store, which owns state and the human
gate.

`retro_verdict` and `RETRO_DIMENSIONS` are a byte-for-byte port of
`static/js/desk-v1-kit.js`'s `retroVerdict()` / `RETRO_DIMENSIONS` (landed
under R2-14) — the frontend's Interim retro view and this module's term-end
retro must agree on the same evidence, and the R1-L ticket requires the two
reproduce each other's verdicts exactly. Port changes here must be ported
there too, and vice versa.
"""

from __future__ import annotations

import math
from typing import Any

from mc import desk as _desk

# -- §10.1 dimension table ----------------------------------------------------
# Keep in lockstep with static/js/desk-v1-kit.js RETRO_DIMENSIONS.
RETRO_DIMENSIONS: dict[str, dict[str, Any]] = {
    'format': {'label': 'Piece format', 'unit': 'post', 'note': ''},
    # §10.1 honesty rule: the voice split fixes Ron to \U0001D54F and Clayrune
    # to LinkedIn, so v1 cannot separate "LinkedIn worked" from "the Clayrune
    # voice worked" -- one dimension, not two.
    'platform_voice': {
        'label': 'Platform + voice', 'unit': 'post',
        'note': ('one dimension, not two: the voice split fixes Ron to \U0001D54F '
                 'and Clayrune to LinkedIn, so v1 can’t separate '
                 '“LinkedIn worked” from “the Clayrune voice worked”.'),
    },
    'slot': {
        'label': 'Posting day / time slot', 'unit': 'post',
        'note': ('compared within one account only (platforms have different '
                 'audiences at different hours)'),
    },
    'angle': {
        'label': 'Angle / strategy', 'unit': 'campaign',
        'note': ('n = campaigns, so almost always “Too few campaigns to '
                 'tell” in v1; shown anyway so the gap is visible'),
    },
    'spend_kind': {
        'label': 'Spend kind', 'unit': 'campaign',
        'note': 'cost per outcome, same n caveat',
    },
}


def _arm_stats(items: list) -> dict:
    """Port of desk-v1-kit.js `_armStats`. `items` is a list of plain numbers,
    or `{value, id?, campaign_id?}` dicts — a plain number gets a synthetic id
    so "one post drives this" can still link back to it."""
    norm = []
    for i, it in enumerate(items or []):
        if isinstance(it, dict):
            norm.append(dict(it))
        else:
            norm.append({'value': it, 'id': f'item-{i}'})
    n = len(norm)
    total = sum((it.get('value') or 0) for it in norm)
    mean = total / n if n else 0
    values_sorted = sorted((it.get('value') or 0) for it in norm)
    if n:
        if n % 2:
            median = values_sorted[(n - 1) // 2]
        else:
            median = (values_sorted[n // 2 - 1] + values_sorted[n // 2]) / 2
    else:
        median = 0
    dominant = None
    if total > 0:
        for it in norm:
            share = (it.get('value') or 0) / total
            if dominant is None or share > dominant['share']:
                dominant = {'id': it.get('id'), 'share': share}
    campaign_ids = {it.get('campaign_id') for it in norm if it.get('campaign_id')}
    campaign_count = len(campaign_ids) or n
    return {'n': n, 'mean': mean, 'median': median, 'dominant': dominant,
            'campaignCount': campaign_count}


def retro_verdict(dimension: str, arms: dict) -> dict:
    """Port of desk-v1-kit.js `retroVerdict()` — the §10.1 sample-size table,
    in order. Must reproduce the frontend's verdicts byte-for-byte (R1-L test
    requirement); do not "improve" the wording here without porting it there.
    """
    meta = RETRO_DIMENSIONS.get(dimension) or {'unit': 'post'}
    a = _arm_stats((arms or {}).get('a') or [])
    b = _arm_stats((arms or {}).get('b') or [])

    if meta.get('unit') == 'campaign':
        if a['campaignCount'] < 3 or b['campaignCount'] < 3:
            return {
                'verdict': 'too_few_campaigns', 'dimension': dimension,
                'text': (f"Too few campaigns to tell ({a['campaignCount']} and "
                         f"{b['campaignCount']}; need 3 each)"),
            }
    elif a['n'] < 10 or b['n'] < 10:
        return {
            'verdict': 'too_few_posts', 'dimension': dimension,
            'text': f"Too few posts to tell ({a['n']} and {b['n']}; need 10 each)",
        }

    mean_dir = 'a' if a['mean'] >= b['mean'] else 'b'
    median_dir = 'a' if a['median'] >= b['median'] else 'b'
    hi, lo = max(a['mean'], b['mean']), min(a['mean'], b['mean'])
    if lo > 0:
        ratio = hi / lo
    elif hi > 0:
        ratio = math.inf
    else:
        ratio = 1
    if ratio < 1.3 or mean_dir != median_dir:
        return {'verdict': 'no_clear_difference', 'dimension': dimension,
                'text': 'No clear difference'}

    best_a, best_b = a['dominant'], b['dominant']
    dominant = best_a if (best_a and (not best_b or best_a['share'] >= best_b['share'])) else best_b
    if dominant and dominant['share'] > 0.5:
        return {'verdict': 'one_post_drives', 'dimension': dimension,
                'post_id': dominant['id'], 'text': 'One post drives this, not a pattern'}

    # §10.2 confidence: low = one campaign/term; medium = same direction in
    # >=2 campaigns, pooled n>=20/arm; high = >=3 campaigns, pooled n>=30/arm.
    # ("no confirmed retro pointing the other way" is R1-L's own read of the
    # playbook store, applied in run_retro, not a kit-level concern.)
    min_campaigns = min(a['campaignCount'], b['campaignCount'])
    min_n = min(a['n'], b['n'])
    confidence = 'low'
    if min_campaigns >= 3 and min_n >= 30:
        confidence = 'high'
    elif min_campaigns >= 2 and min_n >= 20:
        confidence = 'medium'

    return {
        'verdict': 'finding', 'dimension': dimension, 'confidence': confidence,
        'effect': {'ratio': ratio, 'direction': f"{mean_dir}>{'b' if mean_dir == 'a' else 'a'}"},
        'n_total': a['n'] + b['n'],
    }


def run_retro(project_id: str, *, dimension_arms: dict[str, dict], account: str | None = None,
              metric: str = 'clicks', interim: bool = False) -> dict:
    """Compute the retro over one term/campaign's evidence and propose any
    dimension that clears the sample-size bar (§10.1) as a finding.

    `dimension_arms`: {dimension: {'a': [...], 'b': [...], 'a_label': str,
    'b_label': str, 'evidence': [{campaign_id, term, n_a, n_b}, ...]}} — the
    caller (the route, or a fixture in tests) already grouped ledger rows into
    the two arms being compared; grouping rules differ per dimension (§10.1's
    table) and are not this function's concern.

    `interim=True` (the "Run retro now" mid-term action, §10.1) computes and
    returns the same dimension table but NEVER proposes a finding — "a
    half-term's numbers are the noisiest there are."

    Every proposed finding is `origin: 'unattended'` regardless of who or what
    triggered this call (§10.5.2): the retro is code-computed, not a human
    judgement, so its output starts on the unattended side of the loop no
    matter the caller — it only crosses to `interactive` when Ron confirms it.
    """
    dimensions: dict[str, dict] = {}
    proposed: list[str] = []
    for dimension, arms in (dimension_arms or {}).items():
        verdict = retro_verdict(dimension, arms)
        dimensions[dimension] = verdict
        if interim or verdict.get('verdict') != 'finding':
            continue
        evidence = arms.get('evidence') or []
        arm_labels = {'a': arms.get('a_label', 'a'), 'b': arms.get('b_label', 'b')}
        direction = verdict['effect']['direction']
        evidence_key = _desk.evidence_key(evidence)
        if _desk.is_finding_suppressed(project_id, dimension, arm_labels, direction, evidence_key):
            continue
        fid = _desk.propose_finding(
            project_id=project_id, dimension=dimension, arms=arm_labels,
            account=account, metric=metric, effect=verdict['effect'],
            evidence=evidence, n_total=verdict['n_total'],
            confidence=verdict['confidence'],
        )
        if fid:
            proposed.append(fid)
    return {'interim': interim, 'dimensions': dimensions, 'proposed': proposed}
