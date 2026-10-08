"""A model newer than the user's CLI must not inflate the cost gauge.

Claude Code 2.1.292 does not know claude-haiku-5-5. Its `result` event marks
that model's `modelUsage` entry `costBasis: 'unknown'` and carries a cost that
is ~34x too high; a model it knows reads `costBasis: 'list'`. The fixtures
below are the real `usage` / `modelUsage` objects from those two probes
(2026-10-08, CLI 2.1.292); ids and timings are left out.
"""
from __future__ import annotations

import copy
import json

import pytest

from mc import agent_runtime as ar
from mc import model_upgrade as mu

H55 = {
    'total_cost_usd': 0.1674652,
    'usage': {
        'input_tokens': 2, 'cache_creation_input_tokens': 20045,
        'cache_read_input_tokens': 30686, 'output_tokens': 48,
        'output_tokens_details': {'thinking_tokens': 44},
        'cache_creation': {'ephemeral_1h_input_tokens': 20045,
                           'ephemeral_5m_input_tokens': 0},
    },
    'modelUsage': {'claude-haiku-5-5': {
        'inputTokens': 2, 'outputTokens': 48, 'cacheReadInputTokens': 30686,
        'cacheCreationInputTokens': 20045, 'webSearchRequests': 0,
        'costUSD': 0.1674652, 'contextWindow': 200000, 'maxOutputTokens': 128000,
        'thinkingTokens': 44, 'canonicalModel': 'claude-haiku-5-5',
        'provider': 'firstParty', 'costBasis': 'unknown'}},
    'num_turns': 1, 'subtype': 'success', 'type': 'result',
}
H45 = {
    'total_cost_usd': 0.0274595,
    'usage': {
        'input_tokens': 10, 'cache_creation_input_tokens': 12533,
        'cache_read_input_tokens': 18035, 'output_tokens': 116,
        'cache_creation': {'ephemeral_1h_input_tokens': 12533,
                           'ephemeral_5m_input_tokens': 0},
    },
    'modelUsage': {'claude-haiku-4-5-20251001': {
        'inputTokens': 10, 'outputTokens': 116, 'cacheReadInputTokens': 18035,
        'cacheCreationInputTokens': 12533, 'webSearchRequests': 0,
        'costUSD': 0.0274595, 'contextWindow': 200000, 'maxOutputTokens': 32000,
        'thinkingTokens': 109, 'canonicalModel': 'claude-haiku-4-5',
        'provider': 'firstParty', 'costBasis': 'list'}},
    'num_turns': 1, 'subtype': 'success', 'type': 'result',
}
# 0.10 in / 0.50 out per Mtok; 1h cache write 2x, cache read 0.1x.
H55_REPRICED = (2 * 0.10 + 20045 * 0.10 * 2.0 + 30686 * 0.10 * 0.1 + 48 * 0.50) / 1e6


@pytest.fixture
def prices(tmp_path, monkeypatch):
    monkeypatch.setattr(mu, 'clayrune_home', lambda: tmp_path)
    return tmp_path


def _book(msg, session=None, proc=None):
    session = {} if session is None else session
    ar.accumulate_result_cost(session, copy.deepcopy(msg), {} if proc is None else proc)
    return session


def test_unknown_basis_with_price_row_is_repriced(prices):
    mu.set_price('claude', 'claude-haiku-5-5', 0.10, 0.50)
    s = _book(H55)
    assert s['cost_usd'] == pytest.approx(H55_REPRICED)
    assert s['cost_usd'] < 0.005 < H55['total_cost_usd']
    assert s['cost_estimate'] is True
    assert s['cost_estimate_models'] == {'claude-haiku-5-5': 'repriced'}


def test_unknown_basis_without_price_row_is_flagged_not_the_cli_number(prices):
    s = _book(H55)
    assert s['cost_usd'] == 0.0
    assert s['cost_usd'] != H55['total_cost_usd']
    assert s['cost_estimate'] is True
    assert s['cost_estimate_models'] == {'claude-haiku-5-5': 'unpriced'}


def test_list_basis_is_untouched_even_with_a_price_row(prices):
    mu.set_price('claude', 'claude-haiku-4-5-20251001', 9.0, 9.0)
    s = _book(H45)
    assert s == {'cost_usd': 0.0274595}


def test_absent_cost_basis_is_untouched(prices):
    msg = copy.deepcopy(H55)
    del msg['modelUsage']['claude-haiku-5-5']['costBasis']
    assert _book(msg) == {'cost_usd': 0.1674652}


def test_absent_model_usage_is_untouched(prices):
    msg = copy.deepcopy(H55)
    del msg['modelUsage']
    assert _book(msg) == {'cost_usd': 0.1674652}


def test_formula_reproduces_the_cli_list_price(prices):
    """Calibration: force the known-model result to 'unknown' and price it at
    Haiku 4.5's 1/5; the formula must land on the CLI's own 'list' figure."""
    mu.set_price('claude', 'claude-haiku-4-5-20251001', 1.0, 5.0)
    msg = copy.deepcopy(H45)
    msg['modelUsage']['claude-haiku-4-5-20251001']['costBasis'] = 'unknown'
    assert _book(msg)['cost_usd'] == pytest.approx(0.0274595, abs=1e-9)


def test_cache_write_priced_by_the_5m_1h_split(prices):
    mu.set_price('claude', 'claude-haiku-5-5', 0.10, 0.50)
    msg = copy.deepcopy(H55)
    msg['usage']['cache_creation'] = {'ephemeral_5m_input_tokens': 20045,
                                      'ephemeral_1h_input_tokens': 0}
    expected = (2 * 0.10 + 20045 * 0.10 * 1.25 + 30686 * 0.10 * 0.1 + 48 * 0.50) / 1e6
    assert _book(msg)['cost_usd'] == pytest.approx(expected)


def test_cumulative_totals_still_book_as_deltas(prices):
    """Two results in one process: modelUsage and total_cost_usd are both
    cumulative, so the booked figure is the repriced cumulative, once."""
    mu.set_price('claude', 'claude-haiku-5-5', 0.10, 0.50)
    second = copy.deepcopy(H55)
    second['total_cost_usd'] = 0.30
    mu_ = second['modelUsage']['claude-haiku-5-5']
    mu_.update(inputTokens=4, outputTokens=60, costUSD=0.30)
    session, proc = {}, {}
    ar.accumulate_result_cost(session, copy.deepcopy(H55), proc)
    ar.accumulate_result_cost(session, second, proc)
    expected = (4 * 0.10 + 20045 * 0.10 * 2.0 + 30686 * 0.10 * 0.1 + 60 * 0.50) / 1e6
    assert session['cost_usd'] == pytest.approx(expected)


def test_mixed_models_only_the_unknown_entry_is_swapped(prices):
    mu.set_price('claude', 'claude-haiku-5-5', 0.10, 0.50)
    msg = copy.deepcopy(H55)
    msg['modelUsage'].update(copy.deepcopy(H45['modelUsage']))
    msg['total_cost_usd'] = 0.1674652 + 0.0274595
    s = _book(msg)
    assert s['cost_usd'] == pytest.approx(H55_REPRICED + 0.0274595)
    assert s['cost_estimate_models'] == {'claude-haiku-5-5': 'repriced'}


def test_claude_turn_end_payload_carries_model_usage_only_when_present():
    rt = ar.ClaudeRuntime()
    ev = rt.parse_event(json.dumps({'type': 'result', **H55}))
    assert ev.payload['modelUsage']['claude-haiku-5-5']['costBasis'] == 'unknown'
    older = {k: v for k, v in H55.items() if k != 'modelUsage'}
    assert 'modelUsage' not in rt.parse_event(json.dumps({'type': 'result', **older})).payload


def test_mode_a_payload_path_reprices_too(prices):
    mu.set_price('claude', 'claude-haiku-5-5', 0.10, 0.50)
    ev = ar.ClaudeRuntime().parse_event(json.dumps({'type': 'result', **H55}))
    session = {}
    ar.accumulate_result_cost(session, ev.payload, {})
    assert session['cost_usd'] == pytest.approx(H55_REPRICED)


def test_usage_payload_exposes_the_estimate_flag_only_when_set():
    from mc.blueprints import agent_routes as routes
    clean = routes._session_usage_payload({'provider': 'claude', 'cost_usd': 0.01})
    assert 'cost_estimate' not in clean
    flagged = routes._session_usage_payload({
        'provider': 'claude', 'cost_usd': 0.0, 'cost_estimate': True,
        'cost_estimate_models': {'claude-haiku-5-5': 'unpriced'}})
    assert flagged['cost_estimate'] is True
    assert flagged['cost_estimate_models'] == {'claude-haiku-5-5': 'unpriced'}
