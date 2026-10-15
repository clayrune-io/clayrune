"""MC-964 T4 — an exhaustion record with no reset time cannot deadlock a vendor.

Acceptance (journal b2d85e51, Dave's DECIDED WITHOUT RON #3): 'codex exhausted,
no reset, 25h old' yields ONE trial run, not a refusal; the refusal that
follows while the trial is in flight names the record's age; TTL 0/none turns
the whole thing off.
"""
import threading
from datetime import datetime, timedelta, timezone

import pytest

from mc import allowance_state as al
from mc import allowance_unverified as unv
from mc import state
from tests.test_revive_notify_carry import ar  # noqa: F401 — reuse its fixture


@pytest.fixture(autouse=True)
def _isolated_allowance_state(tmp_path, monkeypatch):
    al.wire(tmp_path / 'allowance_state.json')
    al._LAST_PROBE.clear()
    monkeypatch.delitem(state.CONFIG, unv.CONFIG_KEY, raising=False)
    from mc import agent_runtime
    for rt in agent_runtime._RUNTIMES.values():
        monkeypatch.setattr(rt, 'probe_allowance', lambda: None)
    yield
    al._STATE = {}
    al._LAST_PROBE.clear()


def _record_aged(vendor, hours, **kw):
    al.record_exhaustion(vendor, limit_kind='usage_limit', **kw)
    al._STATE[vendor]['recorded_at'] = (
        datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


# ── the acceptance fixture ─────────────────────────────────────────────────

def test_codex_no_reset_25h_old_gives_one_trial_run_not_a_refusal(ar):  # noqa: F811
    _record_aged('codex', 25)

    first = ar._allowance_refusal('codex', user_initiated=True)
    second = ar._allowance_refusal('codex', user_initiated=True)

    assert first == '', 'the first dispatch is the trial run'
    assert second != '', 'exactly one trial: the next dispatch is refused'
    assert 'codex' in second and 'unverified' in second
    assert '25h' in second, 'the refusal names the record age'
    assert al.get('codex')['status'] == 'unverified'


def test_same_for_a_non_user_initiated_dispatch(ar):  # noqa: F811
    _record_aged('gemini', 30)
    assert ar._allowance_refusal('gemini', user_initiated=False) == ''
    assert ar._allowance_refusal('gemini', user_initiated=False) != ''


# ── what must NOT change ───────────────────────────────────────────────────

def test_a_record_younger_than_the_ttl_still_refuses(ar):  # noqa: F811
    _record_aged('gemini', 23)
    msg = ar._allowance_refusal('gemini', user_initiated=True)
    assert 'out of allowance' in msg
    assert 'status' not in al.get('gemini')


def test_a_record_with_a_reset_time_is_never_unverified(ar):  # noqa: F811
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    _record_aged('claude', 100, resets_at=future)
    assert 'out of allowance' in ar._allowance_refusal('claude', user_initiated=True)
    assert 'status' not in al.get('claude')


@pytest.mark.parametrize('off', [0, 0.0, None, False, 'none', 'None', 'off', ''])
def test_ttl_zero_or_none_disables_it(ar, off):  # noqa: F811
    state.CONFIG[unv.CONFIG_KEY] = off
    _record_aged('gemini', 500)
    assert 'out of allowance' in ar._allowance_refusal('gemini', user_initiated=True)
    assert 'out of allowance' in ar._allowance_refusal('gemini', user_initiated=True)


def test_ttl_is_configurable(ar):  # noqa: F811
    state.CONFIG[unv.CONFIG_KEY] = 2
    _record_aged('gemini', 3)
    assert ar._allowance_refusal('gemini', user_initiated=True) == ''


def test_garbage_ttl_falls_back_to_the_default_not_to_off():
    assert unv.ttl_hours({unv.CONFIG_KEY: 'soon'}) == unv.DEFAULT_TTL_HOURS
    assert unv.ttl_hours({}) == unv.DEFAULT_TTL_HOURS
    assert unv.ttl_hours({unv.CONFIG_KEY: '6'}) == 6.0


def test_readers_that_only_look_never_consume_the_trial(ar):  # noqa: F811
    _record_aged('gemini', 26)
    assert al.is_exhausted('gemini') is True
    assert 'unverified' in al.display_text('gemini').lower()
    assert 'gemini' in al.all_states()
    assert ar._allowance_refusal('gemini', user_initiated=True) == '', \
        'looking at the record must not have spent the trial'


# ── the trial's outcome ────────────────────────────────────────────────────

def test_a_successful_trial_clears_the_record(ar):  # noqa: F811
    _record_aged('gemini', 26)
    assert ar._allowance_refusal('gemini', user_initiated=True) == ''
    al.clear_exhaustion('gemini')  # what agent_runtime does on a successful run
    assert al.get('gemini') is None
    assert ar._allowance_refusal('gemini', user_initiated=True) == ''


def test_a_failed_trial_renews_the_record_and_the_next_trial_is_a_ttl_away(ar):  # noqa: F811
    _record_aged('gemini', 26)
    assert ar._allowance_refusal('gemini', user_initiated=True) == ''
    # the trial hit the limit again: the stream reader re-records it
    al.record_exhaustion('gemini', limit_kind='unknown')
    entry = al.get('gemini')
    assert 'status' not in entry and 'trial_started_at' not in entry
    assert 'out of allowance' in ar._allowance_refusal('gemini', user_initiated=True)


def test_a_lost_trial_lapses_so_it_cannot_become_the_next_deadlock(ar):  # noqa: F811
    _record_aged('gemini', 26)
    assert ar._allowance_refusal('gemini', user_initiated=True) == ''
    assert ar._allowance_refusal('gemini', user_initiated=True) != ''
    al._STATE['gemini']['trial_started_at'] = (
        datetime.now(timezone.utc)
        - timedelta(seconds=unv.TRIAL_LAPSE_S + 5)).isoformat()
    assert ar._allowance_refusal('gemini', user_initiated=True) == ''


def test_concurrent_dispatches_cannot_both_win_the_trial():
    _record_aged('qwen', 48)
    wins = []
    barrier = threading.Barrier(8)

    def _go():
        barrier.wait()
        wins.append(al.claim_trial('qwen'))

    threads = [threading.Thread(target=_go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert wins.count(True) == 1


def test_state_survives_a_restart(tmp_path):
    _record_aged('gemini', 26)
    assert al.claim_trial('gemini') is True
    al.wire(tmp_path / 'allowance_state.json')  # reload from disk
    assert al.get('gemini')['status'] == 'unverified'
    assert al.claim_trial('gemini') is False, 'the grant was persisted'
