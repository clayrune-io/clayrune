"""mc/desk_retro.py — the §10.1 sample-size table (MC-977 R1-L).

`retro_verdict` / `RETRO_DIMENSIONS` are a byte-for-byte port of
`static/js/desk-v1-kit.js`'s `retroVerdict()` / `RETRO_DIMENSIONS` (R2-14).
The three cases below are the exact fixtures `tools/smoke/desk-v1-kit.mjs`
runs through the frontend kit — the R1-L ticket requires the two reproduce
each other's verdicts exactly, so these numbers must not drift from that file.
"""
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk_retro as retro  # noqa: E402


def test_too_few_posts_matches_kit_fixture_byte_for_byte():
    # 6 vs 4 posts, both under the 10-per-arm floor.
    v = retro.retro_verdict('format', {'a': [10] * 6, 'b': [10] * 4})
    assert v['verdict'] == 'too_few_posts'
    assert v['text'] == 'Too few posts to tell (6 and 4; need 10 each)'


def test_no_clear_difference_matches_kit_fixture_byte_for_byte():
    # 12 vs 12, means 1.2x apart (< 30% gap) -> No clear difference.
    v = retro.retro_verdict('format', {'a': [10] * 12, 'b': [12] * 12})
    assert v['verdict'] == 'no_clear_difference'
    assert v['text'] == 'No clear difference'


def test_one_post_drives_matches_kit_fixture_byte_for_byte():
    # One post = 60% of a 10-post arm's total; other arm clearly lower.
    dominated = [60, 4, 4, 4, 4, 4, 4, 4, 4, 4]
    other = [2] * 10
    v = retro.retro_verdict('format', {'a': dominated, 'b': other})
    assert v['verdict'] == 'one_post_drives'
    assert v['text'] == 'One post drives this, not a pattern'
    assert v['post_id'] == 'item-0'


def test_platform_voice_dimension_label_and_cant_separate_note():
    meta = retro.RETRO_DIMENSIONS['platform_voice']
    assert meta['label'] == 'Platform + voice'
    assert re.search(r"can.t separate", meta['note'], re.I)


def test_campaign_unit_dimension_needs_three_campaigns_each():
    v = retro.retro_verdict('angle', {
        'a': [{'value': 10, 'campaign_id': 'c1'}],
        'b': [{'value': 10, 'campaign_id': 'c2'}, {'value': 10, 'campaign_id': 'c3'},
              {'value': 10, 'campaign_id': 'c4'}],
    })
    assert v['verdict'] == 'too_few_campaigns'
    assert v['text'] == 'Too few campaigns to tell (1 and 3; need 3 each)'


def test_a_real_finding_gets_a_confidence_tier():
    # >=3 campaigns, pooled n>=30/arm, clear ratio and same mean/median direction -> high.
    a = [{'value': 30, 'campaign_id': f'c{i}'} for i in range(30)]
    b = [{'value': 10, 'campaign_id': f'd{i}'} for i in range(30)]
    v = retro.retro_verdict('slot', {'a': a, 'b': b})
    assert v['verdict'] == 'finding'
    assert v['confidence'] == 'high'
    assert v['effect']['direction'] == 'a>b'
    assert v['n_total'] == 60


# -- run_retro: the higher-level loop that turns a verdict into a proposal ----

@pytest.fixture
def store(tmp_path, monkeypatch):
    from mc import desk
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(desk, 'SIGNALS_PATH', tmp_path / 'desk_signals.jsonl')
    return desk


def test_run_retro_proposes_only_dimensions_that_clear_the_bar(store):
    hi = [{'value': 30, 'campaign_id': f'c{i}'} for i in range(30)]
    lo = [{'value': 10, 'campaign_id': f'd{i}'} for i in range(30)]
    result = retro.run_retro('mc', dimension_arms={
        'format': {'a': [10] * 6, 'b': [10] * 4},   # too few posts -> no proposal
        'slot': {'a': hi, 'b': lo, 'a_label': 'morning', 'b_label': 'evening',
                'evidence': [{'campaign_id': f'c{i}', 'term': 't1'} for i in range(30)]},
    })
    assert result['dimensions']['format']['verdict'] == 'too_few_posts'
    assert result['dimensions']['slot']['verdict'] == 'finding'
    assert len(result['proposed']) == 1
    f = store.get_finding(result['proposed'][0])
    assert f['origin'] == 'unattended' and f['state'] == 'proposed'


def test_run_retro_interim_never_proposes(store):
    hi = [{'value': 30, 'campaign_id': f'c{i}'} for i in range(30)]
    lo = [{'value': 10, 'campaign_id': f'd{i}'} for i in range(30)]
    result = retro.run_retro('mc', interim=True, dimension_arms={
        'slot': {'a': hi, 'b': lo,
                'evidence': [{'campaign_id': f'c{i}', 'term': 't1'} for i in range(30)]},
    })
    assert result['interim'] is True
    assert result['proposed'] == []
    assert store.list_findings(project_id='mc') == []


def test_run_retro_does_not_repropose_rejected_evidence(store):
    evidence = [{'campaign_id': f'c{i}', 'term': 't1'} for i in range(30)]
    hi = [{'value': 30, 'campaign_id': f'c{i}'} for i in range(30)]
    lo = [{'value': 10, 'campaign_id': f'd{i}'} for i in range(30)]
    arms = {'a': hi, 'b': lo, 'a_label': 'morning', 'b_label': 'evening', 'evidence': evidence}

    r1 = retro.run_retro('mc', dimension_arms={'slot': arms})
    assert len(r1['proposed']) == 1
    store.reject_finding(r1['proposed'][0], decided_by='ron')

    # same evidence -> proposes nothing again.
    r2 = retro.run_retro('mc', dimension_arms={'slot': arms})
    assert r2['proposed'] == []

    # +10 posts from a new campaign/term -> new evidence key -> re-proposes.
    more_hi = hi + [{'value': 30, 'campaign_id': f'e{i}'} for i in range(10)]
    more_lo = lo + [{'value': 10, 'campaign_id': f'f{i}'} for i in range(10)]
    more_evidence = evidence + [{'campaign_id': f'e{i}', 'term': 't2'} for i in range(10)]
    r3 = retro.run_retro('mc', dimension_arms={'slot': {
        'a': more_hi, 'b': more_lo, 'a_label': 'morning', 'b_label': 'evening',
        'evidence': more_evidence}})
    assert len(r3['proposed']) == 1


def test_run_retro_marks_a_contradicted_confirmed_finding_stale(store):
    """§10.2: 'confirmed -> stale when a newer retro points the other way
    (proposed as Contradicts F3)' (MC-977 R2-16)."""
    hi = [{'value': 30, 'campaign_id': f'c{i}'} for i in range(30)]
    lo = [{'value': 10, 'campaign_id': f'd{i}'} for i in range(30)]
    r1 = retro.run_retro('mc', dimension_arms={'slot': {
        'a': hi, 'b': lo, 'a_label': 'morning', 'b_label': 'evening',
        'evidence': [{'campaign_id': f'c{i}', 'term': 't1'} for i in range(30)]}})
    old_fid = r1['proposed'][0]
    confirmed = store.confirm_finding(old_fid, decided_by='ron')
    assert confirmed['effect']['direction'] == 'a>b'

    # A later retro on the SAME dimension/arms with the OPPOSITE direction —
    # evening now wins, from different campaigns so it isn't suppressed as
    # the same rejected evidence.
    hi2 = [{'value': 30, 'campaign_id': f'g{i}'} for i in range(30)]
    lo2 = [{'value': 10, 'campaign_id': f'h{i}'} for i in range(30)]
    r2 = retro.run_retro('mc', dimension_arms={'slot': {
        'a': lo2, 'b': hi2, 'a_label': 'morning', 'b_label': 'evening',
        'evidence': [{'campaign_id': f'g{i}', 'term': 't2'} for i in range(30)]}})
    new_fid = r2['proposed'][0]

    assert store.get_finding(old_fid)['state'] == 'stale'
    assert store.get_finding(old_fid)['stale_reason'].startswith('Contradicts')
    assert store.get_finding(new_fid)['contradicts'] == old_fid
    assert old_fid not in store.playbook_brief('mc')


def test_run_retro_does_not_mark_stale_when_direction_agrees(store):
    """The contradiction check must not fire on a finding that simply agrees
    again — only a genuine direction flip marks a confirmed finding stale."""
    hi = [{'value': 30, 'campaign_id': f'c{i}'} for i in range(30)]
    lo = [{'value': 10, 'campaign_id': f'd{i}'} for i in range(30)]
    r1 = retro.run_retro('mc', dimension_arms={'slot': {
        'a': hi, 'b': lo, 'a_label': 'morning', 'b_label': 'evening',
        'evidence': [{'campaign_id': f'c{i}', 'term': 't1'} for i in range(30)]}})
    old_fid = r1['proposed'][0]
    store.confirm_finding(old_fid, decided_by='ron')

    hi2 = [{'value': 30, 'campaign_id': f'g{i}'} for i in range(30)]
    lo2 = [{'value': 10, 'campaign_id': f'h{i}'} for i in range(30)]
    r2 = retro.run_retro('mc', dimension_arms={'slot': {
        'a': hi2, 'b': lo2, 'a_label': 'morning', 'b_label': 'evening',
        'evidence': [{'campaign_id': f'g{i}', 'term': 't2'} for i in range(30)]}})
    new_fid = r2['proposed'][0]

    assert store.get_finding(old_fid)['state'] == 'confirmed'
    assert store.get_finding(new_fid)['contradicts'] is None
