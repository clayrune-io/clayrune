"""Paid-result durability, failed writes and gate recovery without paid calls."""
import importlib
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from mc import api_credits_budget as budget
from mc import api_credits_pending as pending
from mc import api_credits_session as billing
from test_api_credits_session import isolated, session, event


def test_failure_blocks_every_credit_gate_and_does_not_replay(isolated, monkeypatch):
    s, costs = session(billing='credits'), {}
    with monkeypatch.context() as fail:
        fail.setattr(budget, '_save_ledger', MagicMock(side_effect=OSError('disk')))
        assert not billing.record_result(s, event(9), costs)
        assert not s.get('_api_credits_retry_pending')
        assert len(json.loads(pending.path().read_text())) == 1
        assert budget.can_spend(s['model']) == (False, 'accounting_pending')
        assert budget.status()['state'] == 'stopped'
        assert billing.env_for_session(session(), {}) == {}
        assert billing.needs_respawn(s)
    assert budget.can_spend(s['model'])[0]
    assert budget.spent_this_cycle() == 9
    # Accounting failures never replay completed user work.
    assert not s.get('_api_credits_retry_pending')


def test_journal_survives_module_restart_and_retains_full_delta(isolated, monkeypatch):
    s, costs = session(billing='credits'), {}
    with monkeypatch.context() as fail:
        fail.setattr(budget, '_save_ledger', MagicMock(side_effect=OSError('disk')))
        billing.record_result(s, event(9), costs)
        billing.record_result(s, event(9.5), costs)
    importlib.reload(pending)  # no live session/counter/in-memory queue required
    assert budget.can_spend(s['model'])[0]
    assert budget.spent_this_cycle() == pytest.approx(9.5)
    assert json.loads(pending.path().read_text()) == {}
    assert budget.can_spend(s['model'])[0]
    assert budget.spent_this_cycle() == pytest.approx(9.5)


def test_post_commit_journal_clear_failure_cannot_double_charge(isolated, monkeypatch):
    real_save = pending._save
    def save(rows):
        if not rows:
            raise OSError('journal clear failed')
        real_save(rows)
    with monkeypatch.context() as fail:
        fail.setattr(pending, '_save', save)
        billing.record_result(session(billing='credits'), event(4), {})
        assert budget.spent_this_cycle() == 4
        assert not budget.can_spend('claude-haiku-5-5')[0]
    importlib.reload(pending)
    assert budget.can_spend('claude-haiku-5-5')[0]
    assert budget.spent_this_cycle() == 4
    assert not budget._load_ledger().get('pending_receipts')


def test_queue_write_failure_retains_in_memory_and_closes_gate(isolated, monkeypatch):
    with monkeypatch.context() as fail:
        fail.setattr(pending, '_save', MagicMock(side_effect=OSError('journal unavailable')))
        billing.record_result(session(billing='credits'), event(4), {})
        assert not budget.can_spend('claude-haiku-5-5')[0]
    assert budget.can_spend('claude-haiku-5-5')[0]
    assert budget.spent_this_cycle() == 4


def test_old_pending_event_keeps_original_cycle_and_timestamp(isolated, monkeypatch):
    with monkeypatch.context() as fail:
        fail.setattr(budget, '_save_ledger', MagicMock(side_effect=OSError('disk')))
        billing.record_result(session(billing='credits'), event(4), {})
    monkeypatch.setattr(budget, '_now_fn', lambda: datetime(2026, 12, 2, tzinfo=timezone.utc))
    assert budget.can_spend('claude-haiku-5-5')[0]
    assert budget.spent_this_cycle() == 0
    old = budget._load_ledger()['cycles']['2026-11-01']
    assert old['spent_usd'] == 4
    assert old['entries'][0]['ts'].startswith('2026-11-09')


@pytest.mark.parametrize('target', ['pending', 'ledger'])
def test_corrupt_accounting_state_fails_closed(isolated, target):
    (pending.path() if target == 'pending' else budget.ledger_path()).write_text('broken')
    assert budget.can_spend('claude-haiku-5-5') == (False, 'accounting_pending')


def test_retries_preserve_multiple_children_and_capped_totals(isolated, monkeypatch):
    monkeypatch.setattr(budget, 'ENTRIES_PER_CYCLE_CAP', 1)
    with monkeypatch.context() as fail:
        fail.setattr(budget, '_save_ledger', MagicMock(side_effect=OSError('disk')))
        for cost in (4, 6, 8):
            billing.record_result(session(billing='credits'), event(cost), {})
    assert budget.can_spend('claude-haiku-5-5')[0]
    assert budget.status()['cycle']['session_spent_usd'] == 18
    assert budget.status()['cycle']['calls'] == 3


@pytest.mark.parametrize('over', [
    {'trigger_type': 'manual'}, {'provider': 'codex'},
    {'billing_preference': 'subscription'}, {'billing': 'subscription'}])
def test_untouched_scoped_auth_is_preserved(isolated, over):
    base = {'ANTHROPIC_API_KEY': 'configured-key', 'anthropic_auth_token': 'configured-token', 'X': '1'}
    assert billing.env_for_session(session(**over), base) == base


def test_actual_downgrade_stays_oauth_through_snapshot(isolated):
    s = session(billing='credits')
    budget.save_settings({**budget.load_settings(), 'mode': 'off'})
    base = {'ANTHROPIC_API_KEY': 'configured-key', 'anthropic_auth_token': 'configured-token', 'X': '1'}
    assert billing.env_for_session(s, base) == {'X': '1'}
    restored = {**session(), **billing.snapshot(s)}
    assert restored['billing_oauth'] is True
    assert billing.env_for_session(restored, base) == {'X': '1'}
