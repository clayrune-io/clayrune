"""Cold-review probes: real billing code, temporary ledger, fake vault only."""
import ast
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mc import api_credits_budget as budget
from mc import api_credits_session as billing
from mc import launch_marker, provider_env_scope, secrets_store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, '_data_dir', tmp_path)
    monkeypatch.setattr(budget, '_now_fn', lambda: datetime(2026, 11, 9, tzinfo=timezone.utc))
    monkeypatch.setattr(budget, '_notifier', lambda *args: None)
    monkeypatch.setattr(secrets_store, 'get_secret_value', MagicMock(return_value='synthetic-review-value'))
    budget.save_settings({'mode': 'on', 'budget_usd': 10, 'stop_pct': 90, 'secret_name': 'fake'})


def scheduled(**over):
    return dict(project_id='p', session_id='s', trigger_type='schedule',
                model='claude-haiku-5-5', **over)


def result(cost):
    return {'type': 'result', 'is_error': False, 'total_cost_usd': cost, 'usage': {}}


def test_mode_off_preserves_explicit_claude_auth(monkeypatch):
    budget.save_settings({**budget.load_settings(), 'mode': 'off'})
    monkeypatch.setattr(provider_env_scope, '_saved', {'claude': {'ANTHROPIC_API_KEY': 'synthetic-review-value'}})
    monkeypatch.setattr(provider_env_scope, '_ORIGINAL_ENV', {})
    base = {'ANTHROPIC_API_KEY': 'synthetic-review-value'}
    old_path = launch_marker.launch_env('manual', base=base)
    new_path = launch_marker.launch_env('manual', base=base,
        session={'provider': 'claude', 'trigger_type': 'manual'})
    assert 'ANTHROPIC_API_KEY' in old_path
    assert 'ANTHROPIC_API_KEY' in new_path, 'Off now deletes the legitimate Claude auth that the original launch path preserves'


def test_failed_ledger_write_cannot_reopen_spent_budget(monkeypatch):
    s = scheduled(billing='credits')
    counters = {}
    with monkeypatch.context() as failure:
        failure.setattr(budget, '_save_ledger', MagicMock(side_effect=OSError('temporary write failure')))
        billing.record_result(s, result(9), counters)
    billing.record_result(s, result(9.5), counters)
    spent = budget.spent_this_cycle()
    allowed = budget.can_spend(s['model'])[0]
    assert spent == pytest.approx(9.5) or not allowed, (
        f'CLI billed 9.5, ledger retained {spent}, can_spend={allowed}; failed 9-dollar delta was discarded')


def test_cold_dispatch_uses_latest_sticky_choice():
    # Execute the exact selection statements from the dispatch function, without
    # its real process/context side effects. The log is newest first, as written
    # by _log_agent_completion_body.complete's insert(0, entry).
    source = Path('mc/blueprints/agent_routes.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    fun = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_dispatch_agent_internal')
    nodes = sorted(ast.walk(fun), key=lambda n: getattr(n, 'lineno', 0))
    start = next(n for n in nodes if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == '_billing_prior' for t in n.targets))
    choose = next(n for n in nodes if isinstance(n, ast.If) and n.lineno == start.end_lineno + 1)
    latest = {'session_id': 's', 'billing': 'subscription', 'billing_reason': 'mode_not_on'}
    older = {'session_id': 's', 'billing': 'credits', 'billing_reason': ''}
    scope = {'session_dict_override': None, 'agent_sessions': {}, 'reuse_session_id': 's',
             'project_id': 'p', '_load_agent_log': lambda pid: [latest, older]}
    exec(compile(ast.Module(body=[start, choose], type_ignores=[]), '<actual-dispatch-selection>', 'exec'), scope)
    prior = scope['_billing_prior']
    s = {**scheduled(), **billing.snapshot(prior)}
    child = billing.env_for_session(s, {})
    assert s['billing'] == 'subscription' and 'ANTHROPIC_API_KEY' not in child, (
        f'Newest choice was subscription; cold dispatch chose {s["billing"]}')
