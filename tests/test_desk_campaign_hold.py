"""A held post says the fix that works for the campaign's kind.

Short campaign (plan end passed): "extend its end date, then approve"; Renew
cannot help and is refused with the same words. Capped term (plan end still
ahead): Renew is the real fix and keeps its wording.
"""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from mc import desk
from mc import desk_campaign_hold as hold
from mc.desk_tick import _cadence_refusal, _refusal  # noqa: F401  (_refusal pins the import surface)

NOW = datetime(2026, 10, 29, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(desk, 'SIGNALS_PATH', tmp_path / 'signals.jsonl')


def _started(end_date, term_ends, *, horizon='short'):
    draft = desk.create_campaign(
        'Hold wording', 'A bounded campaign', voiceless_ok=True,
        project_id='alpha', state='draft',
        plan={'accounts': ['x:test'], 'cadence': {'per_week': 3},
              'end': {'date': end_date, 'post_cap': 10}},
        goal={'horizon': horizon},
        term={'index': 1, 'starts': '2026-10-15', 'ends': term_ends, 'post_cap': 10})
    return desk.start_campaign(draft['id'], today='2026-10-15')


def test_short_campaign_past_its_end_date_gets_the_dated_fix_and_a_code():
    camp = _started('2026-10-28', '2026-10-28')
    reason = _cadence_refusal(camp, [], NOW)
    assert reason == 'This campaign ended on 2026-10-28. Extend its end date, then approve to keep posting.'
    assert reason.code == 'campaign_ended'
    assert 'renew' not in reason.lower()


def test_the_end_day_itself_still_posts():
    camp = _started('2026-10-28', '2026-10-28')
    assert _cadence_refusal(camp, [], datetime(2026, 10, 28, 23, 0, tzinfo=timezone.utc)) is None


def test_capped_term_with_plan_end_ahead_still_says_renew_and_has_no_code():
    camp = _started('2027-02-28', '2027-01-13', horizon='long')
    reason = _cadence_refusal(camp, [], datetime(2027, 1, 14, tzinfo=timezone.utc))
    assert 'choose Renew term' in reason
    assert getattr(reason, 'code', None) is None


def test_a_renewed_short_campaign_stays_held_with_the_ended_message():
    # Term 1 ended but the plan end is still ahead, so Renew opens term 2. Once the
    # plan end then passes, the hold must name the end date, never "renew".
    camp = _started('2026-10-30', '2026-10-28')
    renewed = desk.renew_campaign(camp['id'], today='2026-10-29')
    assert renewed['term']['index'] == 2
    reason = _cadence_refusal(renewed, [], datetime(2026, 10, 31, tzinfo=timezone.utc))
    assert reason == hold.ended_message('2026-10-30') and reason.code == 'campaign_ended'


def test_renew_is_refused_once_the_plan_end_has_passed():
    camp = _started('2026-10-28', '2026-10-28')
    before = desk.STORE_PATH.read_bytes()
    with pytest.raises(desk.ApprovalRefused) as exc:
        desk.renew_campaign(camp['id'], today='2026-10-29')
    assert str(exc.value) == hold.ended_message('2026-10-28')
    assert desk.STORE_PATH.read_bytes() == before


def test_the_plan_end_day_itself_can_still_renew():
    camp = _started('2026-10-28', '2026-10-27')
    assert desk.renew_campaign(camp['id'], today='2026-10-28')['term']['index'] == 2


def test_renew_with_no_today_uses_the_real_clock_for_the_plan_end():
    camp = _started('2026-10-28', '2026-10-28')  # long past on any real clock
    with pytest.raises(desk.ApprovalRefused, match='ended on 2026-10-28'):
        desk.renew_campaign(camp['id'])


def test_renew_still_works_while_the_plan_end_is_ahead():
    camp = _started('2027-02-28', '2027-01-13', horizon='long')
    out = desk.renew_campaign(camp['id'], today='2027-01-14')
    assert out['term']['index'] == 2


def test_plan_end_passed_ignores_a_campaign_with_no_or_bad_end_date():
    assert hold.plan_end_passed({'plan': {'end': {'post_cap': 3}}}, NOW) is None
    assert hold.plan_end_passed({'plan': {'end': {'date': 'nope'}}}, NOW) is None
    assert hold.plan_end_passed({}, NOW) is None
