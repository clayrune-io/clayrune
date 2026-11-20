"""The pre-generation "no" check and the 7-day dedupe use the ARTIFACT kind.

MC-1179 pass 03, ML10. A topic signal becomes a `skill` artifact, and every
writer (the outbox marker, `_suppress_artifact`, `record_push`) keys its entry
`<exact>:skill`. `_evaluate_candidate` looked the same pattern up under
`<exact>:topic`, a key nothing writes, so a rejected skill pattern was caught
only after a model call (the post-generation re-check) and the dedupe never
bound: each recurrence wrote another proposal into the human queue.
"""
from __future__ import annotations

from datetime import datetime, timezone

from mc import distiller

EXACT, COARSE = 'abc' + 'e' * 13, 'c' * 16


def _eval(kind, *, suppressions=None, outbox=None, count=3):
    sids = {f's{i}' for i in range(count)}
    base = {'kind': kind, 'exact': EXACT, 'coarse': COARSE,
            'scope_tag': 'project-specific', 'summary': 'x'}
    window = [dict(base, sid=s) for s in sids]
    return distiller._evaluate_candidate(
        kind=kind, exact=EXACT, coarse=COARSE,
        kind_exact={(kind, EXACT): sids}, kind_coarse={(kind, COARSE): sids},
        suppressions=suppressions or {}, outbox=outbox or {},
        min_rec=3, pref_min_rec=1, dedupe_days=7,
        window_signals=window, new_signals=window)


def _fresh():
    return datetime.now(timezone.utc).isoformat()


def test_control_topic_at_threshold_is_a_skill_candidate():
    cand = _eval('topic')
    assert cand is not None and cand['kind'] == 'skill'


def test_measured_case_suppression_and_fresh_outbox_under_skill_key():
    """The report's measured case: both entries under <exact>:skill."""
    key = f'{EXACT}:skill'
    cand = _eval('topic',
                 suppressions={key: {'decision': 'no', 'decided_at': _fresh()}},
                 outbox={key: {'last_proposed_at': _fresh()}})
    assert cand is None


def test_no_under_skill_key_alone_stops_a_topic_signal():
    key = f'{EXACT}:skill'
    assert _eval('topic', suppressions={key: {'decision': 'no'}}) is None


def test_fresh_outbox_under_skill_key_alone_stops_a_topic_signal():
    key = f'{EXACT}:skill'
    assert _eval('topic', outbox={key: {'last_proposed_at': _fresh()}}) is None


def test_old_outbox_entry_under_skill_key_does_not_bind():
    key = f'{EXACT}:skill'
    cand = _eval('topic', outbox={key: {'last_proposed_at': '2020-01-01T00:00:00+00:00'}})
    assert cand is not None


def test_preference_and_exploration_keys_are_unchanged():
    for kind in ('preference', 'exploration'):
        key = f'{EXACT}:{kind}'
        assert _eval(kind, suppressions={key: {'decision': 'no'}}) is None
        assert _eval(kind, outbox={key: {'last_proposed_at': _fresh()}}) is None
        assert _eval(kind) is not None
