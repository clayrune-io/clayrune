"""API-credits offload, phase 1 (docs/API_CREDITS_BUILD_SPEC.md).

Plan detection, cycle math, price lookup, the stop gate, the Messages transport
(mocked: no test makes a paid call), the offload fallbacks, the oneshot hook, and
the routes' human-only gates.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mc.agent_runtime as ar
from mc import api_credits_budget as budget
from mc import api_credits_offload as offload
from mc import api_credits_plan as plan
from mc import api_credits_transport as transport

KEY = 'sk-ant-api03-SECRETSECRETSECRET'


def utc(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Own data dir, fixed clock, captured notices, no real vault, no cached key."""
    monkeypatch.setattr(budget, '_data_dir', tmp_path)
    monkeypatch.setattr(budget, '_now_fn', lambda: utc(2026, 10, 15))
    monkeypatch.setattr(budget, '_cap_logged', set())
    notices = []
    budget.set_notifier(lambda t, b: notices.append((t, b)))
    offload.forget_key()
    vault = MagicMock()
    vault.is_locked.return_value = False
    vault.get_secret_value.return_value = KEY
    monkeypatch.setitem(sys.modules, 'mc.secrets_store', vault)
    monkeypatch.setattr('mc.secrets_store', vault, raising=False)
    yield {'notices': notices, 'vault': vault}
    budget.set_notifier(None)
    offload.forget_key()


def turn_on(**over):
    s = {'mode': 'on', 'secret_name': 'anthropic-api-key', 'budget_usd': 100.0,
         'stop_pct': 90, 'reset_day': 1}
    s.update(over)
    budget.save_settings(s)


class Resp:
    def __init__(self, status=200, body=None, text=''):
        self.status_code = status
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError('no json')
        return self._body


def ok_body(text='hello', inp=1000, out=200, **extra):
    usage = {'input_tokens': inp, 'output_tokens': out, **extra}
    return {'content': [{'type': 'text', 'text': text}], 'usage': usage,
            'stop_reason': 'end_turn', 'model': 'claude-haiku-5-5'}


def err_body(msg, typ='invalid_request_error'):
    return {'type': 'error', 'error': {'type': typ, 'message': msg}}


def poster(resp=None, exc=None):
    calls = []

    def post(url, **kw):
        calls.append({'url': url, **kw})
        if exc:
            raise exc
        return resp
    post.calls = calls
    return post


# ── plan detection ───────────────────────────────────────────────────────────

def creds(tmp_path, sub, tier, extra=True):
    oauth = {'subscriptionType': sub, 'rateLimitTier': tier}
    if extra:
        oauth.update(accessToken='AT-SECRET', refreshToken='RT-SECRET')
    p = tmp_path / 'c.json'
    p.write_text(json.dumps({'claudeAiOauth': oauth}), encoding='utf-8')
    return p


@pytest.mark.parametrize('sub,tier,want,credit', [
    ('max', 'default_claude_max_20x', 'ultra', 200.0),   # this box today
    ('max', 'default_claude_max_5x', 'max', 100.0),
    ('ultra', 'default_claude_ultra', 'ultra', 200.0),   # new label
    ('max', 'default_claude_max', 'max', 100.0),         # new label for $100
    ('team', 'default_claude_max_5x', 'team', None),
    ('Team', '', 'team', None),
])
def test_plan_mapping(tmp_path, sub, tier, want, credit):
    out = plan.detect(creds(tmp_path, sub, tier))
    assert out['status'] == 'detected' and out['plan'] == want
    assert out['credit_usd'] == credit


def test_plan_not_eligible_and_no_tokens_leak(tmp_path):
    out = plan.detect(creds(tmp_path, 'pro', 'default_claude_pro'))
    assert out['status'] == 'not_eligible' and out['plan'] is None
    assert 'SECRET' not in json.dumps(plan.detect(creds(tmp_path, 'max', 'default_claude_max_5x')))


def test_plan_missing_or_broken_file_is_unknown(tmp_path):
    assert plan.detect(tmp_path / 'nope.json')['status'] == 'unknown'
    bad = tmp_path / 'bad.json'
    bad.write_text('{not json', encoding='utf-8')
    assert plan.detect(bad)['status'] == 'unknown'
    bad.write_text(json.dumps({'other': 1}), encoding='utf-8')
    assert plan.detect(bad)['status'] == 'unknown'


# ── cycle math ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize('now,day,start,end', [
    (utc(2026, 2, 15), 31, (2026, 1, 31), (2026, 2, 28)),   # reset day 31 in February
    (utc(2026, 2, 28), 31, (2026, 2, 28), (2026, 3, 31)),
    (utc(2026, 3, 1), 31, (2026, 2, 28), (2026, 3, 31)),
    (utc(2028, 2, 20), 31, (2028, 1, 31), (2028, 2, 29)),   # leap year
    (utc(2026, 10, 15), 1, (2026, 10, 1), (2026, 11, 1)),
    (utc(2026, 10, 15), 20, (2026, 9, 20), (2026, 10, 20)),
    (utc(2026, 12, 25), 20, (2026, 12, 20), (2027, 1, 20)),  # year rollover
    (utc(2027, 1, 5), 20, (2026, 12, 20), (2027, 1, 20)),
])
def test_cycle_bounds(now, day, start, end):
    s, e = budget.cycle_bounds(now, day)
    assert (s.year, s.month, s.day) == start and (e.year, e.month, e.day) == end


# ── prices ───────────────────────────────────────────────────────────────────

def test_cost_uses_spec_table():
    u = {'input_tokens': 1_000_000, 'output_tokens': 1_000_000,
         'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}
    assert budget.cost_usd('claude-sonnet-5-5', {**u, 'input_tokens': 100_000,
                                                 'output_tokens': 100_000}, {}) == pytest.approx(1.2)
    full = {'input_tokens': 1_000_000, 'output_tokens': 1_000_000,
            'cache_read_input_tokens': 1_000_000, 'cache_creation_input_tokens': 1_000_000}
    # haiku, prompt > 100K -> 0.50 + 2.50 + 0.05 + 0.625
    assert budget.cost_usd('claude-haiku-5-5', full, {}) == pytest.approx(3.675)
    # fable-5 cache read is 1.00, fable-5-1 is 0.25 (spec rows differ)
    assert budget.price_for('claude-fable-5', 1, {})['cache_read'] == 1.00
    assert budget.price_for('claude-fable-5-1', 1, {})['cache_read'] == 0.25


def test_haiku_rate_card_switches_on_total_prompt_tokens():
    assert budget.price_for('claude-haiku-5-5', 100_000, {})['input'] == 0.10
    assert budget.price_for('claude-haiku-5-5', 100_001, {})['input'] == 0.50
    # cache tokens count toward the prompt size
    u = {'input_tokens': 60_000, 'cache_read_input_tokens': 50_000, 'output_tokens': 0,
         'cache_creation_input_tokens': 0}
    assert budget.cost_usd('claude-haiku-5-5', u, {}) == pytest.approx(
        (60_000 * 0.50 + 50_000 * 0.05) / 1e6)


@pytest.mark.parametrize('model', ['claude-opus-5', 'claude-sonnet-4', 'gpt-6', '', 'haiku'])
def test_unknown_model_price_is_none(model):
    assert budget.price_for(model, 10, {}) is None
    assert budget.cost_usd(model, {'input_tokens': 5}, {}) is None


def test_price_override_from_settings():
    turn_on(prices={'claude-opus-5': {'input': 5, 'output': 25, 'cache_read': 0.5,
                                      'cache_write': 6.25}})
    s = budget.load_settings()
    assert budget.price_for('claude-opus-5', 1, s['prices'])['output'] == 25.0
    assert budget.can_spend('claude-opus-5', s)[0] is True
    assert 'claude-opus-5' in budget.status()['known_models']
    assert 'overrides' in budget.status()['cost_basis']


def test_bad_price_override_rejected():
    new, why = budget.validate_settings({'prices': {'claude-x': {'input': 1}}})
    assert new is None and 'prices' in why


# ── gate ─────────────────────────────────────────────────────────────────────

def test_gate_reasons():
    m = 'claude-haiku-5-5'
    assert budget.can_spend(m) == (False, 'mode_not_on')
    budget.save_settings({'mode': 'on'})
    assert budget.can_spend(m) == (False, 'no_key_selected')
    budget.save_settings({'mode': 'on', 'secret_name': 'k'})
    assert budget.can_spend(m) == (False, 'no_budget')
    turn_on()
    assert budget.can_spend(m) == (True, '')
    assert budget.can_spend('claude-opus-5') == (False, 'price_unknown')
    assert budget.can_spend('mystery') == (False, 'price_unknown')


def test_stop_point_blocks_and_notifies_once(isolated):
    turn_on(budget_usd=10.0, stop_pct=50)
    usage = {'input_tokens': 0, 'output_tokens': 0}
    budget.record(caller='t', model='claude-haiku-5-5', usage=usage, cost=4.99, estimated=False)
    assert budget.can_spend('claude-haiku-5-5')[0] is True
    budget.record(caller='t', model='claude-haiku-5-5', usage=usage, cost=0.02, estimated=False)
    assert budget.can_spend('claude-haiku-5-5') == (False, 'stop_point_reached')
    budget.record(caller='t', model='claude-haiku-5-5', usage=usage, cost=1.0, estimated=False)
    assert len(isolated['notices']) == 1
    st = budget.status()
    assert st['state'] == 'stopped' and st['cycle']['stop_usd'] == 5.0


def test_new_cycle_reopens_gate(monkeypatch):
    turn_on(budget_usd=10.0, stop_pct=50)
    budget.record(caller='t', model='claude-haiku-5-5', usage={}, cost=6.0, estimated=False)
    assert budget.can_spend('claude-haiku-5-5')[0] is False
    monkeypatch.setattr(budget, '_now_fn', lambda: utc(2026, 11, 2))
    assert budget.can_spend('claude-haiku-5-5')[0] is True
    assert budget.status()['cycle']['spent_usd'] == 0


def test_ledger_keeps_current_and_previous_cycle_only(monkeypatch):
    turn_on()
    for month in (8, 9, 10):
        monkeypatch.setattr(budget, '_now_fn', lambda m=month: utc(2026, m, 15))
        budget.record(caller='t', model='claude-haiku-5-5', usage={}, cost=1.0, estimated=False)
    cycles = json.loads(budget.ledger_path().read_text(encoding='utf-8'))['cycles']
    assert sorted(cycles) == ['2026-09-01', '2026-10-01']


def test_measure_estimates_never_count_toward_spend():
    turn_on(mode='measure', budget_usd=1.0)
    budget.record(caller='t', model='claude-haiku-5-5', usage={'input_tokens': 4}, cost=50.0,
                  estimated=True)
    st = budget.status()
    assert st['cycle']['spent_usd'] == 0 and st['cycle']['estimated_usd'] == 50.0
    assert st['state'] == 'measuring' and 'not billed' in st['reason']


def test_entry_cap_logs_and_keeps_totals(monkeypatch):
    turn_on()
    monkeypatch.setattr(budget, 'ENTRIES_PER_CYCLE_CAP', 3)
    logged = []
    monkeypatch.setattr(budget, '_log', lambda *a, **k: logged.append(a[0]))
    for _ in range(5):
        budget.record(caller='t', model='claude-haiku-5-5', usage={}, cost=1.0, estimated=False)
    cyc = json.loads(budget.ledger_path().read_text(encoding='utf-8'))['cycles']['2026-10-01']
    assert len(cyc['entries']) == 3 and cyc['spent_usd'] == 5.0 and cyc['calls'] == 5
    assert len(logged) == 1 and 'capped' in logged[0]


def test_settings_files_live_outside_projects_dir():
    assert budget.settings_path().parent == budget._data_dir
    assert budget.settings_path().name == 'api_credits.json'
    assert budget.ledger_path().name == 'api_credits_ledger.json'


# ── transport ────────────────────────────────────────────────────────────────

def send(post, **kw):
    return transport.send(api_key=KEY, model='claude-haiku-5-5', user_text='hi', post=post, **kw)


def test_transport_success_shape():
    post = poster(Resp(200, ok_body('abc', cache_read_input_tokens=7)))
    r = send(post, system_text='sys', timeout=12)
    assert r.ok and r.text == 'abc' and r.usage['cache_read_input_tokens'] == 7
    c = post.calls[0]
    assert c['url'] == 'https://api.anthropic.com/v1/messages'
    assert c['headers']['x-api-key'] == KEY and 'anthropic-version' in c['headers']
    assert c['json']['system'] == 'sys' and c['json']['messages'] == [{'role': 'user', 'content': 'hi'}]
    assert c['timeout'] == 12 and 'tools' not in c['json']


@pytest.mark.parametrize('resp,kind', [
    (Resp(400, err_body('Your credit balance is too low to access the Anthropic API.')), 'credit_low'),
    (Resp(401, err_body('invalid x-api-key', 'authentication_error')), 'auth'),
    (Resp(403, err_body('forbidden')), 'auth'),
    (Resp(429, err_body('slow down', 'rate_limit_error')), 'rate_limit'),
    (Resp(500, err_body('boom')), 'server'),
    (Resp(529, err_body('overloaded')), 'server'),
    (Resp(400, err_body('max_tokens too big')), 'bad_request'),
    (Resp(200, {'unexpected': 1}), 'bad_response'),
])
def test_transport_error_kinds(resp, kind):
    r = send(poster(resp))
    assert not r.ok and r.error_kind == kind


def test_transport_timeout_network_and_truncation():
    import requests
    assert send(poster(exc=requests.Timeout())).error_kind == 'timeout'
    assert send(poster(exc=ConnectionError('down'))).error_kind == 'network'
    body = ok_body('cut')
    body['stop_reason'] = 'max_tokens'
    r = send(poster(Resp(200, body)))
    assert not r.ok and r.error_kind == 'truncated' and r.usage['output_tokens'] == 200


def test_key_is_scrubbed_from_errors():
    r = send(poster(exc=RuntimeError(f'proxy rejected {KEY}')))
    assert KEY not in r.error_message and '[key]' in r.error_message
    r = send(poster(Resp(401, err_body(f'bad key {KEY} given'))))
    assert KEY not in r.error_message
    r = send(poster(Resp(502, text=f'<html>{KEY} sk-ant-api03-ANOTHERKEY123</html>')))
    assert 'sk-ant' not in r.error_message
    assert 'sk-ant' not in transport.scrub('x sk-ant-api03-abcdefgh y')


# ── offload fallbacks ────────────────────────────────────────────────────────

def test_offload_success_records_cost_and_returns_result():
    turn_on()
    post = poster(Resp(200, ok_body('  done  ', inp=50_000, out=0)))
    res = offload.try_offload(user_text='u', model='haiku', timeout=5, post=post)
    assert res is not None and res.text == 'done' and res.cost_usd == pytest.approx(0.005)
    assert post.calls[0]['json']['model'] == 'claude-haiku-5-5'
    st = budget.status()
    assert st['cycle']['spent_usd'] == pytest.approx(0.005) and st['cycle']['calls'] == 1


def test_offload_empty_model_uses_default_model():
    turn_on(default_model='claude-sonnet-5-5')
    post = poster(Resp(200, ok_body()))
    assert offload.try_offload(user_text='u', model='', timeout=5, post=post) is not None
    assert post.calls[0]['json']['model'] == 'claude-sonnet-5-5'


@pytest.mark.parametrize('mode', ['off', 'measure'])
def test_offload_inactive_modes_do_not_call_api(mode):
    turn_on(mode=mode)
    post = poster(Resp(200, ok_body()))
    assert offload.try_offload(user_text='u', model='haiku', timeout=5, post=post) is None
    assert post.calls == []


@pytest.mark.parametrize('resp,exc', [
    (Resp(400, err_body('Your credit balance is too low')), None),
    (Resp(401, err_body('invalid x-api-key')), None),
    (Resp(429, err_body('limit')), None),
    (Resp(500, err_body('boom')), None),
    (Resp(200, {'junk': 1}), None),
    (None, ConnectionError('down')),
])
def test_every_error_falls_back_to_subscription(resp, exc):
    turn_on()
    assert offload.try_offload(user_text='u', model='haiku', timeout=5,
                               post=poster(resp, exc)) is None


def test_timeout_falls_back():
    import requests
    turn_on()
    assert offload.try_offload(user_text='u', model='haiku', timeout=5,
                               post=poster(exc=requests.Timeout())) is None


def test_truncated_reply_falls_back_but_is_still_billed():
    turn_on()
    body = ok_body('cut', inp=50_000, out=0)
    body['stop_reason'] = 'max_tokens'
    assert offload.try_offload(user_text='u', model='haiku', timeout=5,
                               post=poster(Resp(200, body))) is None
    assert budget.status()['cycle']['spent_usd'] == pytest.approx(0.005)


def test_credit_low_stops_once_per_cycle(isolated):
    turn_on()
    post = poster(Resp(400, err_body('Your credit balance is too low')))
    assert offload.try_offload(user_text='u', model='haiku', timeout=5, post=post) is None
    st = budget.status()
    assert st['state'] == 'stopped' and 'Credit balance too low' in st['reason']
    # stopped: no further API traffic, no second notice
    assert offload.try_offload(user_text='u', model='haiku', timeout=5, post=post) is None
    assert len(post.calls) == 1 and len(isolated['notices']) == 1
    budget.halt('again', 'credit_low')
    assert len(isolated['notices']) == 1


def test_401_stops(isolated):
    turn_on()
    offload.try_offload(user_text='u', model='haiku', timeout=5,
                        post=poster(Resp(401, err_body('invalid x-api-key'))))
    assert budget.status()['state'] == 'stopped'
    assert len(isolated['notices']) == 1


def test_rate_limit_does_not_stop():
    turn_on()
    offload.try_offload(user_text='u', model='haiku', timeout=5,
                        post=poster(Resp(429, err_body('slow'))))
    st = budget.status()
    assert st['state'] == 'active' and '429' in st['last_error']


def test_halt_ends_with_new_cycle_and_with_settings_save(monkeypatch):
    turn_on()
    budget.halt('Credit balance too low', 'credit_low')
    assert budget.can_spend('claude-haiku-5-5') == (False, 'halted')
    monkeypatch.setattr(budget, '_now_fn', lambda: utc(2026, 11, 3))
    assert budget.can_spend('claude-haiku-5-5')[0] is True


def test_vault_failures_fall_back(isolated):
    turn_on()
    post = poster(Resp(200, ok_body()))
    isolated['vault'].is_locked.return_value = True
    assert offload.try_offload(user_text='u', model='haiku', timeout=5, post=post) is None
    assert 'locked' in budget.status()['last_error']
    isolated['vault'].is_locked.return_value = False
    isolated['vault'].get_secret_value.side_effect = KeyError('gone')
    assert offload.try_offload(user_text='u', model='haiku', timeout=5, post=post) is None
    assert post.calls == []
    assert KEY not in budget.ledger_path().read_text(encoding='utf-8')


def test_unknown_model_never_reaches_the_api():
    turn_on()
    post = poster(Resp(200, ok_body()))
    assert offload.try_offload(user_text='u', model='claude-opus-5', timeout=5, post=post) is None
    assert offload.try_offload(user_text='u', model='some-new-model', timeout=5, post=post) is None
    assert post.calls == []


def test_key_read_is_audited_consumer_and_never_persisted(isolated, tmp_path):
    turn_on()
    offload.try_offload(user_text='u', model='haiku', timeout=5, post=poster(Resp(200, ok_body())))
    args, kw = isolated['vault'].get_secret_value.call_args
    assert args == ('anthropic-api-key',)
    assert kw == {'consumer': 'api-credits-offload', 'unattended': True}
    for f in tmp_path.iterdir():
        assert KEY not in f.read_text(encoding='utf-8')


def test_measure_mode_records_estimate_after_subscription_call():
    turn_on(mode='measure')
    offload.note_subscription_call(user_text='x' * 4000, output_text='y' * 400, model='haiku')
    st = budget.status()
    assert st['cycle']['estimated_calls'] == 1 and st['cycle']['spent_usd'] == 0
    assert st['cycle']['estimated_usd'] == pytest.approx((1000 * 0.10 + 100 * 0.50) / 1e6)
    turn_on(mode='off')
    offload.note_subscription_call(user_text='x', output_text='y', model='haiku')
    assert budget.status()['cycle']['estimated_calls'] == 1


def test_key_test_outcomes():
    turn_on()
    assert offload.run_key_test(post=poster(Resp(200, ok_body())))['result'] == 'ok'
    trunc = ok_body()
    trunc['stop_reason'] = 'max_tokens'
    post = poster(Resp(200, trunc))
    assert offload.run_key_test(post=post)['result'] == 'ok'
    assert post.calls[0]['json']['max_tokens'] == 1
    assert post.calls[0]['json']['model'] == 'claude-haiku-5-5'
    assert offload.run_key_test(post=poster(Resp(401, err_body('x'))))['result'] == 'invalid_key'
    assert offload.run_key_test(post=poster(
        Resp(400, err_body('credit balance is too low'))))['result'] == 'credit_too_low'
    budget.save_settings({})
    assert offload.run_key_test(post=poster(Resp(200, ok_body())))['result'] == 'no_key'


def test_key_test_success_clears_a_sticky_stop():
    turn_on()
    budget.halt('Credit balance too low', 'credit_low')
    offload.run_key_test(post=poster(Resp(200, ok_body())))
    assert budget.can_spend('claude-haiku-5-5')[0] is True


# ── the oneshot hook ─────────────────────────────────────────────────────────

def fake_run(calls, stdout='cli says hi', rc=0):
    def run(cmd, **kw):
        calls.append({'cmd': cmd, **kw})
        r = MagicMock()
        r.returncode, r.stdout, r.stderr = rc, stdout, ''
        return r
    return run


def test_hook_mode_off_runs_the_original_path_untouched():
    rt = ar.ClaudeRuntime()
    calls = []
    with patch('subprocess.run', side_effect=fake_run(calls)), \
            patch.object(transport, 'send', side_effect=AssertionError('API touched')):
        res = rt.oneshot(prompt='INSTR', system_prompt='SYS', stdin_text='body', model='haiku')
    assert res.text == 'cli says hi' and res.usage is None and res.cost_usd is None
    assert len(calls) == 1
    assert calls[0]['cmd'] == rt._oneshot_argv(model='haiku', max_turns=1, effort='')
    assert calls[0]['input'].startswith('SYS\n\nINSTR') and 'body' in calls[0]['input']
    assert not budget.ledger_path().exists()


def test_hook_on_success_skips_the_subprocess_entirely():
    turn_on()
    calls = []
    post = poster(Resp(200, ok_body('from api')))
    with patch('subprocess.run', side_effect=fake_run(calls)), \
            patch('requests.post', post):
        res = ar.ClaudeRuntime().oneshot(prompt='INSTR', stdin_text='body', model='haiku')
    assert res.text == 'from api' and res.usage['input_tokens'] == 1000
    assert calls == []
    assert 'body' in post.calls[0]['json']['messages'][0]['content']


def test_hook_on_failure_runs_subscription_path_for_same_call():
    turn_on()
    calls = []
    with patch('subprocess.run', side_effect=fake_run(calls)), \
            patch('requests.post', poster(Resp(500, err_body('boom')))):
        res = ar.ClaudeRuntime().oneshot(prompt='INSTR', stdin_text='body', model='haiku')
    assert res.text == 'cli says hi' and len(calls) == 1


def test_hook_measure_records_estimate_and_still_uses_subscription():
    turn_on(mode='measure')
    calls = []
    with patch('subprocess.run', side_effect=fake_run(calls, stdout='y' * 400)), \
            patch.object(transport, 'send', side_effect=AssertionError('API touched')):
        res = ar.ClaudeRuntime().oneshot(prompt='INSTR', stdin_text='x' * 4000, model='haiku')
    assert res.text == 'y' * 400 and len(calls) == 1
    st = budget.status()
    assert st['cycle']['estimated_calls'] == 1 and st['cycle']['spent_usd'] == 0


def test_hook_failed_subscription_call_records_no_estimate():
    turn_on(mode='measure')
    with patch('subprocess.run', side_effect=fake_run([], rc=1)):
        assert ar.ClaudeRuntime().oneshot(prompt='p', stdin_text='b') is None
    assert budget.status()['cycle']['estimated_calls'] == 0


def test_caller_label_is_the_module_that_called_oneshot():
    turn_on()
    seen = {}
    real = budget.record
    with patch.object(budget, 'record', side_effect=lambda **kw: seen.update(kw) or real(**kw)), \
            patch('requests.post', poster(Resp(200, ok_body()))):
        ar.ClaudeRuntime().oneshot(prompt='p', stdin_text='b', model='haiku')
    assert seen['caller'] == __name__


# ── routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path):
    from flask import Flask
    from mc.blueprints import api_credits_routes as routes
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    return app.test_client(), routes


def test_status_route_has_plan_settings_and_no_secret_values(client, tmp_path, monkeypatch):
    c, _ = client
    monkeypatch.setattr(plan, 'credentials_path', lambda: creds(tmp_path, 'max', 'default_claude_max_20x'))
    turn_on()
    body = c.get('/api/api-credits/status').get_json()
    assert body['plan']['plan'] == 'ultra' and body['plan']['credit_usd'] == 200.0
    assert body['settings']['secret_name'] == 'anthropic-api-key'
    assert body['state'] == 'active' and body['cycle']['resets_on'] == '2026-11-01'
    text = json.dumps(body)
    assert KEY not in text and 'SECRET' not in text


def test_put_settings_off_to_measure_needs_no_passcode(client):
    c, _ = client
    with patch('mc.blueprints.api_credits_routes.is_unattended_caller', return_value=False):
        r = c.put('/api/api-credits/settings', json={'mode': 'measure', 'reset_day': 20})
    assert r.status_code == 200 and budget.load_settings()['mode'] == 'measure'
    assert budget.load_settings()['reset_day'] == 20


def test_put_settings_turning_on_requires_passcode(client):
    c, _ = client
    body = {'mode': 'on', 'secret_name': 'k', 'budget_usd': 50}
    with patch('mc.blueprints.api_credits_routes.is_unattended_caller', return_value=False):
        with patch('mc.blueprints.api_credits_routes._require_human_passcode',
                   side_effect=lambda data: jsonify_403()) as gate:
            r = c.put('/api/api-credits/settings', json=body)
            assert r.status_code == 403 and gate.called
            assert budget.load_settings()['mode'] == 'off'
        with patch('mc.blueprints.api_credits_routes._require_human_passcode', return_value=None):
            r = c.put('/api/api-credits/settings', json={**body, 'passcode': '1234'})
        assert r.status_code == 200 and budget.load_settings()['mode'] == 'on'
        assert 'passcode' not in budget.settings_path().read_text(encoding='utf-8')
        # already on, nothing spend-widening changed -> no passcode needed
        with patch('mc.blueprints.api_credits_routes._require_human_passcode',
                   side_effect=AssertionError('asked')):
            r = c.put('/api/api-credits/settings', json={'reset_day': 5})
        assert r.status_code == 200


def jsonify_403():
    from flask import jsonify
    return jsonify({'error': 'bad_passcode'}), 403


def test_put_settings_validation(client):
    c, _ = client
    with patch('mc.blueprints.api_credits_routes.is_unattended_caller', return_value=False):
        for bad in ({'mode': 'maybe'}, {'reset_day': 32}, {'stop_pct': 0}, {'budget_usd': -1},
                    {'reset_day': 2.5}, {'secret_name': 5}):
            assert c.put('/api/api-credits/settings', json=bad).status_code == 400, bad


def test_unattended_agent_cannot_change_settings_or_run_the_test(client):
    c, _ = client
    with patch('mc.blueprints.api_credits_routes.is_unattended_caller', return_value=True), \
            patch.object(offload, 'run_key_test', side_effect=AssertionError('spent')):
        assert c.put('/api/api-credits/settings', json={'mode': 'measure'}).status_code == 403
        assert c.post('/api/api-credits/test').status_code == 403
        assert c.get('/api/api-credits/status').status_code == 200
    assert budget.load_settings()['mode'] == 'off'


def test_test_route_reports_result(client):
    c, _ = client
    turn_on()
    with patch('mc.blueprints.api_credits_routes.is_unattended_caller', return_value=False), \
            patch('requests.post', poster(Resp(401, err_body('x')))):
        assert c.post('/api/api-credits/test').get_json()['result'] == 'invalid_key'
