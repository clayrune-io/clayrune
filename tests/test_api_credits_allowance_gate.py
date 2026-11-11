"""An exhausted Claude SUBSCRIPTION must not refuse a run that bills API credits.

_allowance_refusal() knew nothing about api_credits_session, so once the
subscription's weekly limit wrote an exhaustion record every dispatch/schedule/
workflow/followup that env_for_session would have put on the API key was
refused. The gate now asks api_credits_session.would_bill_credits() first.
Manual/attended runs, a stopped budget and non-Claude vendors keep refusing.

The gate is exercised for real; the code after it is cut off with a sentinel
exception so nothing spawns.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from mc import allowance_state as al
from mc import api_credits_budget as budget
from mc import api_credits_session as billing
from tests.test_revive_notify_carry import ar, _project  # noqa: F401


class PastGate(Exception):
    """Raised by a stub placed just after the allowance gate."""


SETTINGS = {'budget_usd': 100.0, 'stop_pct': 90, 'session_reserve_usd': 1.0,
            'default_model': 'claude-sonnet-4-5', 'mode': 'on'}


@pytest.fixture(autouse=True)
def _gate_env(tmp_path, monkeypatch):
    # server's first import wires the real state file; import it BEFORE wiring
    # ours or the first test of a cold run records into (and reads) that file.
    import server  # noqa: F401
    al.wire(tmp_path / 'allowance_state.json')
    from mc import agent_runtime
    for rt in agent_runtime._RUNTIMES.values():
        monkeypatch.setattr(rt, 'probe_allowance', lambda: None)
    al._LAST_PROBE.clear()
    monkeypatch.setattr(budget, 'load_settings', lambda: dict(SETTINGS))
    monkeypatch.setattr(budget, 'spent_this_cycle', lambda s: 0.0)
    monkeypatch.setattr(budget, 'can_spend', lambda model, settings=None: (True, ''))
    # The operator's own fallback order must not swap the vendor under test.
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'engine_fallback_order', [])
    # A record WITH a reset time: one with none goes 'unverified' and the gate
    # grants it a trial run, which would mask the refusal under test.
    resets = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    al.record_exhaustion('claude', limit_kind='weekly', resets_at=resets,
                         resets_at_display='Nov 14, 2026 7:00 PM')
    yield
    al._STATE = {}


def _dispatch(ar, tmp_path, monkeypatch, **kw):
    monkeypatch.setattr(ar, 'load_project', lambda _: _project(tmp_path))
    monkeypatch.setattr(ar, '_model_provider_mismatch',
                        lambda *a, **k: (_ for _ in ()).throw(PastGate()))
    return ar._dispatch_agent_internal(
        'p1', 'task', provider_override='claude',
        model_override='claude-sonnet-4-5', **kw)


def test_a_dispatch_trigger_with_budget_is_not_refused(ar, tmp_path, monkeypatch):
    with pytest.raises(PastGate):
        _dispatch(ar, tmp_path, monkeypatch, trigger_type='dispatch')


def test_b_manual_dispatch_is_still_refused(ar, tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='no fallback'):
        _dispatch(ar, tmp_path, monkeypatch, trigger_type='manual')


def test_c_budget_stopped_dispatch_is_still_refused(ar, tmp_path, monkeypatch):
    monkeypatch.setattr(budget, 'can_spend',
                        lambda model, settings=None: (False, 'stop_point_reached'))
    with pytest.raises(ValueError, match='no fallback'):
        _dispatch(ar, tmp_path, monkeypatch, trigger_type='dispatch')


def test_c2_session_reserve_short_is_still_refused(ar, tmp_path, monkeypatch):
    monkeypatch.setattr(budget, 'spent_this_cycle', lambda s: 89.5)  # stop 90, reserve 1
    with pytest.raises(ValueError, match='no fallback'):
        _dispatch(ar, tmp_path, monkeypatch, trigger_type='dispatch')


def test_c3_subscription_only_schedule_is_still_refused(ar, tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='no fallback'):
        _dispatch(ar, tmp_path, monkeypatch, trigger_type='schedule',
                  session_metadata={'billing_preference': 'subscription'})


def test_non_claude_vendor_is_unchanged(ar, tmp_path, monkeypatch):
    al.record_exhaustion('codex', limit_kind='usage_limit',
                         resets_at_display='Nov 14, 2026 7:00 PM')
    monkeypatch.setattr(ar, 'load_project', lambda _: _project(tmp_path))
    with pytest.raises(ValueError, match='no fallback'):
        ar._dispatch_agent_internal('p1', 'task', provider_override='codex',
                                    trigger_type='dispatch')


def _followup(ar, monkeypatch, **session_fields):
    import server
    from pathlib import Path
    session = {'project_id': 'p1', 'session_id': 's1', 'provider': 'claude',
               'mode': 'B', 'process_alive': True, 'claude_session_id': 'native',
               'proc': MagicMock(pid=-1, **{'poll.return_value': None}),
               'log_lines': [], 'status': 'idle', **session_fields}
    ar.agent_sessions['s1'] = session
    monkeypatch.setattr(ar, 'load_project',
                        lambda _: {'id': 'p1', 'project_path': '/tmp/proj'})
    monkeypatch.setattr(Path, 'is_dir', lambda self: True)
    monkeypatch.setattr(ar, '_pid_is_alive', lambda pid: True)
    monkeypatch.setattr(ar, '_auto_fresh_trigger',
                        lambda *a, **k: (_ for _ in ()).throw(PastGate()))
    monkeypatch.setitem(server.app.config, 'PROPAGATE_EXCEPTIONS', True)
    with server.app.test_client() as c:
        return c.post('/api/project/p1/agent/followup',
                      json={'message': 'again', 'session_id': 's1'})


def test_d_followup_of_credits_session_is_not_refused(ar, monkeypatch):
    with pytest.raises(PastGate):
        _followup(ar, monkeypatch, trigger_type='dispatch', _spawned_by='parent-1')


def test_d2_followup_of_manual_session_is_still_refused(ar, monkeypatch):
    resp = _followup(ar, monkeypatch, trigger_type='manual')
    assert resp.status_code == 409
    assert resp.get_json()['allowance_exhausted'] is True


def test_d3_followup_of_sticky_subscription_session_is_still_refused(ar, monkeypatch):
    resp = _followup(ar, monkeypatch, trigger_type='dispatch', billing='subscription')
    assert resp.status_code == 409


def test_would_bill_credits_shares_eligible_rule():
    for trig, spawned, notify in [('manual', 'x', 'y'), ('dispatch', '', ''),
                                  ('', 'x', ''), ('', '', 'y'), ('', '', ''),
                                  ('schedule', '', ''), (None, None, None)]:
        s = {'trigger_type': trig, '_spawned_by': spawned, '_notify_session': notify}
        assert billing.would_bill_credits(trig, spawned, notify, 'm') == billing.eligible(s)
