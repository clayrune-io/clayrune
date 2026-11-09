"""Review-only: no provider subprocess, vault lookup, or live state."""
from datetime import datetime, timezone
from types import SimpleNamespace
import threading

import pytest

from mc import api_credits_budget as budget
from mc import api_credits_session as billing
from mc import limit_resume as policy
from mc import limit_resume_dispatch as dispatch


def test_downgrade_oauth_survives_reset_wait_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, '_data_dir', tmp_path)
    monkeypatch.setattr(budget, '_now_fn', lambda: datetime(2026, 11, 9, tzinfo=timezone.utc))
    budget.save_settings({'mode': 'off'})
    session = dict(session_id='review', project_id='p', provider='claude',
                   trigger_type='schedule', billing='credits', status='idle',
                   log_lines=[], mode='B', model='claude-haiku-5-5',
                   _limit_resume_exhaustion={'resets_at': '2026-11-10T00:00:00+00:00'})
    scoped = {'ANTHROPIC_API_KEY': 'synthetic-configured-key'}
    assert billing.env_for_session(session, scoped) == {}
    assert session['billing_oauth'] is True
    store = policy.ResumeStore(tmp_path / 'reset-waits.json')
    assert store.park(session)
    monkeypatch.setattr(policy, '_store', policy.ResumeStore(store.path))
    manager = SimpleNamespace(lock=threading.RLock(), session_ids=set())
    routes = SimpleNamespace(agent_sessions={}, get_manager=lambda pid: manager,
                             load_project=lambda pid: {'id': pid})
    dispatch.restore_waits(routes)
    restored = routes.agent_sessions['review']
    child = billing.env_for_session(restored, scoped)
    assert 'ANTHROPIC_API_KEY' not in child, (
        f"reset restore lost billing_oauth={restored.get('billing_oauth')!r}; "
        f"billing={restored['billing']}; configured API key reintroduced")


@pytest.mark.parametrize('boundary', ['park', 'deferred_success', 'completion'])
def test_reset_snapshot_keeps_billing_and_auth_consistent(tmp_path, monkeypatch, boundary):
    monkeypatch.setattr(budget, '_data_dir', tmp_path)
    budget.save_settings({'mode': 'off'})
    session = dict(session_id='billing', project_id='p', provider='claude',
                   trigger_type='schedule', billing='credits', billing_preference='auto',
                   status='idle', log_lines=[], model='claude-haiku-5-5',
                   _limit_resume_exhaustion={'resets_at': '2026-11-10T00:00:00+00:00'})
    configured = {'ANTHROPIC_API_KEY': 'synthetic-key',
                  'ANTHROPIC_AUTH_TOKEN': 'synthetic-token', 'KEEP': 'yes'}
    assert billing.env_for_session(session, configured) == {'KEEP': 'yes'}
    expected = billing.snapshot(session)
    assert expected == dict(billing='subscription', billing_reason='mode_not_on',
                            billing_preference='auto', billing_oauth=True)
    store = policy.ResumeStore(tmp_path / 'waits.json')
    assert store.park(session)
    if boundary != 'park':
        claimed = store.claim('billing', datetime(2026, 11, 11, tzinfo=timezone.utc))
        assert claimed is not None
        session['_limit_resume_attempt'] = claimed['attempts']
        assert store.defer_success(session, 'turn') is not None
        if boundary == 'completion':
            assert store.completion_done(session, claimed['attempts'])
    persisted = policy.ResumeStore(store.path).get('billing')
    assert persisted is not None
    restored = persisted['snapshot']
    assert billing.snapshot(restored) == expected
    assert billing.env_for_session(restored, configured) == {'KEEP': 'yes'}
    assert configured['ANTHROPIC_API_KEY'] == 'synthetic-key'
    assert not billing.record_result(restored, {
        'type': 'result', 'is_error': False, 'total_cost_usd': 0.5, 'usage': {},
    }, {})
    assert budget.spent_this_cycle() == 0
    assert billing.snapshot(restored) == expected
