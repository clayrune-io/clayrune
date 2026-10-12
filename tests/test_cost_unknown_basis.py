"""A model newer than the user's CLI must not inflate the cost gauge.

Claude Code 2.1.292 does not know claude-haiku-5-5. Its `result` event marks
that model's `modelUsage` entry `costBasis: 'unknown'` and carries a cost that
is ~39x too high; a model it knows reads `costBasis: 'list'`. The fixtures
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


def test_unknown_basis_without_price_row_keeps_the_cli_figure_flagged(prices):
    """model_prices.json is never shipped; booking 0 would be a silent $0 on
    every install. The CLI's (high) figure stays and the flag says it is not ours."""
    s = _book(H55)
    assert s['cost_usd'] == pytest.approx(H55['total_cost_usd'])
    assert s['cost_estimate'] is True
    assert s['cost_estimate_models'] == {'claude-haiku-5-5': 'unpriced'}


def test_unpriced_cumulative_totals_still_book_as_deltas(prices):
    second = copy.deepcopy(H55)
    second['total_cost_usd'] = 0.30
    second['modelUsage']['claude-haiku-5-5']['costUSD'] = 0.30
    session, proc = {}, {}
    ar.accumulate_result_cost(session, copy.deepcopy(H55), proc)
    ar.accumulate_result_cost(session, second, proc)
    assert session['cost_usd'] == pytest.approx(0.30)


def test_mixed_priced_and_unpriced_unknown_models(prices):
    mu.set_price('claude', 'claude-haiku-5-5', 0.10, 0.50)
    msg = copy.deepcopy(H55)
    other = copy.deepcopy(H55['modelUsage']['claude-haiku-5-5'])
    other.update(canonicalModel='claude-future-9', costUSD=0.5)
    msg['modelUsage']['claude-future-9'] = other
    msg['total_cost_usd'] = 0.1674652 + 0.5
    s = _book(msg)
    assert s['cost_usd'] == pytest.approx(H55_REPRICED + 0.5)
    assert s['cost_estimate_models'] == {'claude-haiku-5-5': 'repriced',
                                         'claude-future-9': 'unpriced'}


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


def _write_row(tmp_path, **row):
    """A price row with extra optional keys, written the way the store reads it."""
    mu.set_price('claude', 'claude-haiku-5-5', 0.10, 0.50)
    data = mu.load_prices()
    data['claude']['claude-haiku-5-5'].update(row)
    mu.save_prices(data)


def test_explicit_cache_rates_on_the_row_are_used(prices):
    _write_row(prices, cache_read=0.02, cache_write_1h=0.30)
    expected = (2 * 0.10 + 20045 * 0.30 + 30686 * 0.02 + 48 * 0.50) / 1e6
    assert _book(H55)['cost_usd'] == pytest.approx(expected)


def test_explicit_5m_write_rate_weighted_by_the_split(prices):
    _write_row(prices, cache_write_5m=0.20, cache_write_1h=0.40)
    msg = copy.deepcopy(H55)
    msg['usage']['cache_creation'] = {'ephemeral_5m_input_tokens': 5000,
                                      'ephemeral_1h_input_tokens': 15045}
    write = (5000 * 0.20 + 15045 * 0.40) / 20045
    expected = (2 * 0.10 + 20045 * write + 30686 * 0.10 * 0.1 + 48 * 0.50) / 1e6
    assert _book(msg)['cost_usd'] == pytest.approx(expected)


def test_rates_a_row_omits_fall_back_per_kind(prices):
    """Only cache_read is given: writes still use 1.25x / 2x of input."""
    _write_row(prices, cache_read=0.03)
    msg = copy.deepcopy(H55)
    msg['usage']['cache_creation'] = {'ephemeral_5m_input_tokens': 10000,
                                      'ephemeral_1h_input_tokens': 10045}
    write = (10000 * 0.10 * 1.25 + 10045 * 0.10 * 2.0) / 20045
    expected = (2 * 0.10 + 20045 * write + 30686 * 0.03 + 48 * 0.50) / 1e6
    assert _book(msg)['cost_usd'] == pytest.approx(expected)


def test_no_split_prices_writes_at_the_1h_rate(prices):
    """modelUsage carries one cache-write count; without usage.cache_creation
    the 1h rate (explicit or 2x) is used, so the guess errs high."""
    _write_row(prices, cache_write_5m=0.01, cache_write_1h=0.40)
    msg = copy.deepcopy(H55)
    del msg['usage']['cache_creation']
    expected = (2 * 0.10 + 20045 * 0.40 + 30686 * 0.10 * 0.1 + 48 * 0.50) / 1e6
    assert _book(msg)['cost_usd'] == pytest.approx(expected)


@pytest.mark.parametrize('bad', [-1, 'x', None, True, [0.1]])
def test_unusable_cache_rate_on_the_row_falls_back(prices, bad):
    _write_row(prices, cache_read=bad, cache_write_1h=bad)
    assert _book(H55)['cost_usd'] == pytest.approx(H55_REPRICED)


def test_row_is_one_rate_so_a_long_prompt_is_booked_at_the_row_rate(prices):
    """Known limit, pinned so nobody mistakes it for tier-aware pricing: Haiku
    5.5 lists 5x higher past 100K prompt tokens, but a row holds one rate and
    modelUsage is a per-process total, so 150K cache-read tokens book at 0.01
    per Mtok, not the 0.05 list rate."""
    mu.set_price('claude', 'claude-haiku-5-5', 0.10, 0.50)
    msg = copy.deepcopy(H55)
    msg['modelUsage']['claude-haiku-5-5'].update(
        cacheReadInputTokens=150_000, cacheCreationInputTokens=0, inputTokens=0,
        outputTokens=0)
    msg['usage']['cache_creation'] = {}
    assert _book(msg)['cost_usd'] == pytest.approx(150_000 * 0.01 / 1e6)


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
