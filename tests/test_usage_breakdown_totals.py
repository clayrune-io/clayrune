"""MC-998 Phase 1: `_mc_usage_from_agent_logs` (mc/blueprints/system_routes.py)
must not skip providers whose entries carry nested `usage` but no
transcript-derived `model_tokens` -- Codex being the measured case
(docs/USAGE_BREAKDOWN_SPEC.md's baseline audit: 119 retained Codex rows,
0 with model_tokens, 61 with positive nested usage).

Before this fix the function's first-pass loop does
`if not mt or not isinstance(mt, dict): continue` on `model_tokens` alone,
so a Codex entry contributes zero to every bucket even though its own
`usage.input_tokens`/`usage.output_tokens` are populated. `/api/usage`
(agent_routes.api_usage) already falls back to nested usage via
`_entry_usage`; this file is scoped to the sibling aggregator that powers
`/api/system/usage`'s today/week/month/all_time buckets, which had no such
fallback.
"""
from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture
def sr(tmp_data_dir):
    import server
    importlib.reload(server)
    from mc.blueprints import system_routes as _sr
    return _sr


def _write_log(sr, name, entries):
    path = sr.DATA_DIR / name
    path.write_text(json.dumps(entries), encoding='utf-8')
    return path


def test_codex_entry_with_nested_usage_and_no_model_tokens_counts_today(sr):
    today = sr.datetime.now().strftime('%Y-%m-%d')
    _write_log(sr, 'proj_agent_log.json', [{
        'ts': f'{today}T12:00:00Z',
        'provider': 'codex',
        'claude_session_id': 'codex-sess-1',
        'observed_model': 'gpt-5-codex',
        'model_tokens': {},
        'input_tokens': 0,
        'output_tokens': 0,
        'usage': {
            'input_tokens': 500,
            'output_tokens': 200,
            'cached_input_tokens': 300,
        },
    }])

    out = sr._mc_usage_from_agent_logs()

    assert out['today'].get('gpt-5-codex') == 700, out
    assert out['all_time'].get('gpt-5-codex') == 700, out
    assert out['last_data_date'] == today


def test_cached_input_tokens_not_double_counted(sr):
    """Codex's `cached_input_tokens` is a subset of `input_tokens` per the
    spec's metrics table -- must not be summed a second time into the total."""
    today = sr.datetime.now().strftime('%Y-%m-%d')
    _write_log(sr, 'proj_agent_log.json', [{
        'ts': f'{today}T12:00:00Z',
        'provider': 'codex',
        'claude_session_id': 'codex-sess-2',
        'observed_model': 'gpt-5-codex',
        'model_tokens': {},
        'usage': {'input_tokens': 1000, 'output_tokens': 50, 'cached_input_tokens': 900},
    }])

    out = sr._mc_usage_from_agent_logs()

    assert out['today'].get('gpt-5-codex') == 1050, out


def test_entry_with_no_usage_at_all_still_skipped(sr):
    """An entry with neither model_tokens nor a positive nested usage carries
    no evidence and must remain excluded -- this is the "missing data isn't a
    zero" contract, not a regression target."""
    today = sr.datetime.now().strftime('%Y-%m-%d')
    _write_log(sr, 'proj_agent_log.json', [{
        'ts': f'{today}T12:00:00Z',
        'provider': 'codex',
        'claude_session_id': 'codex-sess-3',
        'model_tokens': {},
        'usage': {},
    }])

    out = sr._mc_usage_from_agent_logs()

    assert out == {'today': {}, 'week': {}, 'month': {}, 'all_time': {},
                    'last_data_date': ''}


def test_claude_model_tokens_path_unaffected(sr):
    """Existing Claude rows (real model_tokens) must keep working unchanged --
    the fallback must only engage when model_tokens is absent/empty."""
    today = sr.datetime.now().strftime('%Y-%m-%d')
    _write_log(sr, 'proj_agent_log.json', [{
        'ts': f'{today}T12:00:00Z',
        'provider': 'claude',
        'claude_session_id': 'claude-sess-1',
        'model_tokens': {'claude-sonnet-5': 1234},
        'usage': {'input_tokens': 999999, 'output_tokens': 999999},
    }])

    out = sr._mc_usage_from_agent_logs()

    assert out['today'] == {'claude-sonnet-5': 1234}
