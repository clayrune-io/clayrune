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

import json
import math
from datetime import datetime, timezone
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
        # §10.2: "confirmed -> stale when a newer retro points the other way."
        # A confirmed finding on this exact project/dimension/arms whose
        # direction disagrees with what this retro just found is stale news —
        # mark it before proposing the new one, and link the new finding back
        # to it (`contradicts`) so the evidence trail survives to the UI.
        contradicts = None
        for existing in _desk.list_findings(project_id=project_id, state='confirmed'):
            if (existing.get('dimension') == dimension
                    and existing.get('arms') == arm_labels
                    and (existing.get('effect') or {}).get('direction') != direction):
                stale = _desk.mark_stale(existing['id'], reason=f'Contradicts new evidence ({direction})')
                if stale:
                    contradicts = existing['id']
        fid = _desk.propose_finding(
            project_id=project_id, dimension=dimension, arms=arm_labels,
            account=account, metric=metric, effect=verdict['effect'],
            evidence=evidence, n_total=verdict['n_total'],
            confidence=verdict['confidence'], contradicts=contradicts,
        )
        if fid:
            proposed.append(fid)
    return {'interim': interim, 'dimensions': dimensions, 'proposed': proposed}


# -- M12: the retro of one campaign term, assembled from the ledger -----------
#
# R1-W S8 (docs/desk_v1/R1W_WIRING_PLAN.md M12). `run_retro` above takes arms the
# caller already grouped; this is the caller for the campaign's Retro section. A
# post with no recorded number for the per-post metric is NOT in an arm, never a
# 0 in one (§10.7, "a dimension with no numbers never renders as zero"), so the
# "need 10 each" floor counts posts that have a number.

_SLOT_HOURS = 4          # a slot is a four-hour UTC window: `08-12 UTC`


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _post_value(row: dict, metric: str):
    """The newest outcome for `metric` on a ledger row, or None. Unreadable or
    non-numeric values are skipped, so a bad entry is "no number", not 0."""
    best = None
    for o in row.get('outcomes') or []:
        if not isinstance(o, dict) or str(o.get('metric') or '').strip().lower() != metric:
            continue
        v = _desk._as_number(o.get('value'))
        if v is None:
            continue
        if best is None or str(o.get('at') or '') >= str(best[0]):
            best = (o.get('at') or '', v)
    return best[1] if best else None


def _slot_label(published_at: str | None) -> str | None:
    try:
        h = _desk._parse_term_date(published_at).hour
    except (ValueError, TypeError, AttributeError):
        return None
    lo = h - h % _SLOT_HOURS
    return f'{lo:02d}-{lo + _SLOT_HOURS:02d} UTC'


def _two_arms(groups: dict[str, list], campaign_id: str, term: int) -> dict:
    """The two most numerous groups become arm a and arm b (ties by label, so
    the same ledger always gives the same arms). A dimension with fewer than two
    groups gets an empty arm b, which the verdict reports as "too few"."""
    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    (la, a), (lb, b) = (ranked + [(None, []), (None, [])])[:2]
    return {'a': a, 'b': b, 'a_label': la or 'a', 'b_label': lb or 'b',
            'evidence': [{'campaign_id': campaign_id, 'term': term, 'n_a': len(a), 'n_b': len(b)}]}


def _group(rows: list[dict], key, metric: str, campaign_id: str) -> dict[str, list]:
    out: dict[str, list] = {}
    for r in rows:
        v = _post_value(r, metric)
        k = key(r)
        if v is None or not k:
            continue
        out.setdefault(k, []).append({'value': v, 'id': r.get('id'), 'campaign_id': campaign_id})
    return out


def _sane(d):
    """A copy of a verdict/finding with an infinite ratio (one arm's mean is 0)
    made JSON-safe: `ratio: None, unbounded: True`. `Infinity` is not JSON and
    would make the browser's `res.json()` throw."""
    d = json.loads(json.dumps(d, default=str))
    eff = d.get('effect')
    if isinstance(eff, dict) and not _finite(eff.get('ratio')):
        eff['ratio'] = None
        eff['unbounded'] = True
    return d


def _retro_inputs(campaign_id: str, term: int | None, metric: str):
    """(campaign, term index, rows, closed, arms-by-dimension, slot_account) or
    None for an unknown campaign. Reads the store once, under its lock."""
    with _desk._store_lock:
        store = _desk._read_store()
        camp = store['campaigns'].get(campaign_id)
        if not camp:
            return None
        camp = json.loads(json.dumps(camp))
        ledger = [json.loads(json.dumps(r)) for r in store['ledger'] if r.get('campaign_id') == campaign_id]
    terms = camp['terms'] if isinstance(camp.get('terms'), list) and camp['terms'] \
        else ([camp['term']] if isinstance(camp.get('term'), dict) else [])
    idx = term if term is not None else ((camp.get('term') or {}).get('index') or 1)
    term_obj = next((t for t in terms if isinstance(t, dict) and t.get('index') == idx), None)
    window = _desk._term_window(term_obj)
    closed = camp.get('state') == 'done' or bool(window and datetime.now(timezone.utc) >= window[1])
    rows = [r for r in ledger if (r.get('term') or 1) == idx]
    cid = campaign_id
    arms = {
        'format': _two_arms(_group(rows, lambda r: r.get('format'), metric, cid), cid, idx),
        'platform_voice': _two_arms(
            _group(rows, lambda r: r.get('account') or r.get('platform'), metric, cid), cid, idx),
    }
    # Slots are compared within ONE account (§10.1): the one with the most
    # posts that have a number.
    by_account = _group(rows, lambda r: r.get('account') or r.get('platform'), metric, cid)
    slot_account = max(by_account, key=lambda a: (len(by_account[a]), a)) if by_account else None
    slot_rows = [r for r in rows if (r.get('account') or r.get('platform')) == slot_account]
    arms['slot'] = _two_arms(_group(slot_rows, lambda r: _slot_label(r.get('published_at')), metric, cid), cid, idx)
    # Angle and spend kind are judged per CAMPAIGN (§10.1); one campaign is one
    # unit, so these read "too few campaigns" until the project has three.
    numbers = [i['value'] for g in by_account.values() for i in g]
    own = [{'value': sum(numbers) / len(numbers), 'id': cid, 'campaign_id': cid}] if numbers else []
    how = camp.get('how') if isinstance(camp.get('how'), dict) else {}
    budget = how.get('budget')
    budget = budget if isinstance(budget, dict) else {}
    arms['angle'] = {'a': own, 'b': [], 'a_label': str(how.get('angle') or how.get('strategy') or 'this angle'),
                     'b_label': 'other angles', 'evidence': []}
    arms['spend_kind'] = {'a': own, 'b': [],
                          'a_label': 'own budget' if budget.get('source') not in (None, 'none') else 'no budget',
                          'b_label': 'other spend', 'evidence': []}
    return camp, idx, rows, closed, arms, slot_account


def _covers(finding: dict, campaign_id: str, term: int) -> bool:
    return any(isinstance(e, dict) and e.get('campaign_id') == campaign_id and e.get('term') == term
               for e in finding.get('evidence') or [])


def campaign_retro(campaign_id: str, term: int | None = None, *, metric: str = 'clicks') -> dict | None:
    """M12 (read). The retro of one campaign term: goal vs actual, spend, the
    five-row dimension table and, for a CLOSED term, the findings already
    proposed from it. None for an unknown campaign. Never writes: an interim
    retro never proposes (§10.1) and a closed one proposes only through
    `propose_campaign_retro`, so reading twice cannot add findings."""
    metric = (metric or 'clicks').strip().lower()
    inputs = _retro_inputs(campaign_id, term, metric)
    if inputs is None:
        return None
    camp, idx, rows, closed, arms, slot_account = inputs
    dimensions = []
    for dimension, a in arms.items():
        v = _sane(retro_verdict(dimension, a))
        if v.get('verdict') == 'finding':
            v['arms'] = {'a': a['a_label'], 'b': a['b_label']}
            v['evidence'] = a['evidence']
        if dimension == 'slot':
            v['account'] = slot_account
        dimensions.append(v)

    goal = camp.get('goal') if isinstance(camp.get('goal'), dict) else {}
    actual = _desk.goal_current(goal)
    target = _desk._as_number(goal.get('target'))
    how = camp.get('how') if isinstance(camp.get('how'), dict) else {}
    budget = how.get('budget')
    budget = budget if isinstance(budget, dict) else {}
    ceiling = _desk._as_number(budget.get('amount')) if budget.get('source') not in (None, 'none') else None
    publishing = round(sum(_desk._as_number(r.get('cost')) or 0 for r in rows), 4) if rows else None
    # Media-job cost has no per-campaign ledger yet, so it is None (not 0) and
    # the total is publishing alone.
    cost_per = round(publishing / actual, 4) if (publishing and actual and actual > 0) else None
    gm = goal.get('metric') or 'the goal'
    if actual is not None and target is not None:
        reading = f'{actual:g} of {target:g}'
    else:
        reading = f'{actual:g}' if actual is not None else 'no goal number recorded'
    parts = [f'Judged on {metric} per post, not on {gm}.',
             ('Term closed' if closed else 'Interim') + f': {reading}'
             + (f' {gm}' if goal.get('metric') and actual is not None else '')
             + (f', ${publishing:.2f} spent.' if publishing is not None else '.')]
    if not closed:
        parts.append('Not enough of the term has run to propose findings.')

    found = [_sane(f) for f in _desk.list_findings(project_id=camp.get('project_id'))
             if f.get('state') == 'proposed' and _covers(f, campaign_id, idx)] if closed else []
    return {
        'campaign_id': campaign_id, 'project_id': camp.get('project_id'), 'term': idx,
        'status': 'closed' if closed else 'interim', 'metric': metric,
        'computed_at': _desk.now_iso(),
        'goal': {'metric': goal.get('metric'), 'target': target, 'actual': actual,
                 'baseline': goal.get('baseline')},
        'spend': {'publishing': publishing, 'media_cost': None, 'total': publishing,
                  'ceiling': ceiling, 'cost_per_outcome': cost_per},
        'dimensions': dimensions,
        'summary': ' '.join(parts),
        'findings': [f['id'] for f in found],
        'finding_rows': found,
    }


def propose_campaign_retro(campaign_id: str, term: int | None = None, *, metric: str = 'clicks') -> dict | None:
    """M12 (propose). The closed-term retro run: proposes a finding for every
    dimension that clears the §10.1 bar and has none yet for this campaign term
    (any state: a rejected or confirmed one is not re-proposed), then returns
    the same payload `campaign_retro` does. Safe to call twice. Raises
    ValueError for an interim term (§10.1: never proposes) or a campaign with no
    project; None for an unknown campaign."""
    metric = (metric or 'clicks').strip().lower()
    inputs = _retro_inputs(campaign_id, term, metric)
    if inputs is None:
        return None
    camp, idx, _, closed, arms, slot_account = inputs
    if not closed:
        raise ValueError('this term has not closed: an interim retro shows numbers only and proposes nothing')
    project_id = camp.get('project_id')
    if not project_id:
        raise ValueError('this campaign has no project, so there is no playbook to propose into')
    done = {f.get('dimension') for f in _desk.list_findings(project_id=project_id) if _covers(f, campaign_id, idx)}
    fresh = {d: a for d, a in arms.items() if d not in done}
    run_retro(project_id, dimension_arms={d: a for d, a in fresh.items() if d != 'slot'}, metric=metric)
    if 'slot' in fresh:
        run_retro(project_id, dimension_arms={'slot': fresh['slot']}, account=slot_account, metric=metric)
    return campaign_retro(campaign_id, idx, metric=metric)
