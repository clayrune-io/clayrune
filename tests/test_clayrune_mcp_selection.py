"""Injection / opt-out of the `clayrune` MCP server into per-launch selections (BUILTINS-7a).

Pins the rule: the server is added where the user has made no MCP choice, and
never overrides one. An `enabled_mcp_servers` list (empty or not) is honoured
exactly; the config kill switch, incognito projects and bundled builds get none.
"""
from __future__ import annotations

import importlib
import json
import sys

import pytest

from mc import session_mcp_config as smc

PROJECT = {'id': 'p1', 'project_path': ''}


@pytest.fixture
def srv(tmp_data_dir):
    import server
    importlib.reload(server)
    return server


@pytest.fixture
def routes(srv):
    from mc.blueprints import agent_routes
    from mc import state
    return agent_routes, state



# ── pure module ──────────────────────────────────────────────────────────────

def test_spec_launches_the_server_by_path_with_project_only():
    spec = smc.server_spec(PROJECT, {'port': 5199})
    assert spec is not None
    assert spec['command'] == sys.executable
    assert spec['args'][0].endswith('server.py') and 'clayrune_mcp' in spec['args'][0]
    assert spec['args'][1:] == ['--project', 'p1', '--base-url', 'http://127.0.0.1:5199']
    assert 'session' not in json.dumps(spec).lower(), 'identity is never launch-time data'
    assert 'env' not in spec


def test_spec_is_withheld_when_disabled_incognito_frozen_or_projectless(monkeypatch):
    assert smc.server_spec(PROJECT, {'clayrune_mcp_enabled': False}) is None
    assert smc.server_spec({'id': 'p', '_is_incognito_project': True}, {}) is None
    assert smc.server_spec({}, {}) is None
    assert smc.server_spec(None, {}) is None
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert smc.server_spec(PROJECT, {}) is None


def test_default_is_enabled():
    assert smc.is_enabled({}) is True


def test_catalog_setdefault_never_replaces_a_user_server_of_the_same_name():
    mine = {'command': 'mine'}
    cat = smc.add_to_catalog({'clayrune': mine}, PROJECT, {})
    assert cat['clayrune'] is mine
    assert 'clayrune' in smc.add_to_catalog({}, PROJECT, {})
    assert smc.add_to_catalog({}, PROJECT, {'clayrune_mcp_enabled': False}) == {}


def test_claude_flags_additive_only_when_no_selection_was_made():
    flags = smc.claude_additive_flags(PROJECT, None, {})
    assert flags[0] == '--mcp-config' and len(flags) == 2
    assert '--strict-mcp-config' not in flags
    assert list(json.loads(flags[1])['mcpServers']) == ['clayrune']
    # An opted-in selection (any non-empty resolved JSON, including the empty trim):
    assert smc.claude_additive_flags(PROJECT, '{"mcpServers": {}}', {}) == []
    assert smc.claude_additive_flags(PROJECT, '{"mcpServers": {"a": {}}}', {}) == []
    # Resolution failed open, but the project did choose a list: do not second-guess it.
    assert smc.claude_additive_flags({**PROJECT, 'enabled_mcp_servers': []}, None, {}) == []
    assert smc.claude_additive_flags(PROJECT, None, {'clayrune_mcp_enabled': False}) == []


# ── wiring: Claude ───────────────────────────────────────────────────────────

def test_claude_untrimmed_project_gets_additive_flag_and_nothing_else_changes(routes, monkeypatch):
    ar, state = routes
    monkeypatch.setitem(state.CONFIG, 'clayrune_mcp_enabled', False)
    off = ar._build_claude_flags(dict(PROJECT))
    monkeypatch.setitem(state.CONFIG, 'clayrune_mcp_enabled', True)
    on = ar._build_claude_flags(dict(PROJECT))
    assert on[2:] == off, 'the only difference is the leading additive --mcp-config pair'
    assert on[0] == '--mcp-config' and 'clayrune' in json.loads(on[1])['mcpServers']
    assert '--strict-mcp-config' not in on and '--strict-mcp-config' not in off


def test_claude_flags_start_with_a_flag_after_the_variadic_option(routes, monkeypatch):
    """`--mcp-config <configs...>` is variadic; the next token must be a flag."""
    ar, state = routes
    monkeypatch.setitem(state.CONFIG, 'clayrune_mcp_enabled', True)
    flags = ar._build_claude_flags(dict(PROJECT))
    assert flags[2].startswith('--')


def test_claude_empty_selection_stays_empty(routes, monkeypatch):
    ar, state = routes
    monkeypatch.setitem(state.CONFIG, 'clayrune_mcp_enabled', True)
    flags = ar._build_claude_flags({**PROJECT, 'enabled_mcp_servers': []})
    assert '--strict-mcp-config' in flags
    cfg = json.loads(flags[flags.index('--mcp-config') + 1])
    assert cfg == {'mcpServers': {}}
    assert flags.count('--mcp-config') == 1


def test_claude_explicit_selection_excluding_it_does_not_load_it(routes, monkeypatch):
    ar, state = routes
    monkeypatch.setitem(state.CONFIG, 'clayrune_mcp_enabled', True)
    flags = ar._build_claude_flags({**PROJECT, 'enabled_mcp_servers': ['engram']})
    cfg = json.loads(flags[flags.index('--mcp-config') + 1])
    assert list(cfg['mcpServers']) == ['engram']
    assert flags.count('--mcp-config') == 1


def test_claude_explicit_selection_naming_it_loads_it_strictly(routes, monkeypatch):
    ar, state = routes
    monkeypatch.setitem(state.CONFIG, 'clayrune_mcp_enabled', True)
    flags = ar._build_claude_flags({**PROJECT, 'enabled_mcp_servers': ['engram', 'clayrune']})
    assert '--strict-mcp-config' in flags and flags.count('--mcp-config') == 1
    cfg = json.loads(flags[flags.index('--mcp-config') + 1])
    assert set(cfg['mcpServers']) == {'engram', 'clayrune'}
    assert cfg['mcpServers']['clayrune']['args'][2] == 'p1'


def test_claude_kill_switch_removes_it_from_catalog_and_flags(routes, monkeypatch):
    ar, state = routes
    monkeypatch.setitem(state.CONFIG, 'clayrune_mcp_enabled', False)
    assert 'clayrune' not in ar._mcp_server_catalog(dict(PROJECT))
    flags = ar._build_claude_flags({**PROJECT, 'enabled_mcp_servers': ['engram', 'clayrune']})
    assert 'clayrune' not in json.loads(flags[flags.index('--mcp-config') + 1])['mcpServers']


# ── wiring: Gemini / Qwen ────────────────────────────────────────────────────

@pytest.mark.parametrize('provider', ['gemini', 'qwen', 'codex', 'opencode', 'goose', 'kiro'])
def test_gemini_qwen_untrimmed_set_includes_it_alongside_user_servers(routes, monkeypatch, provider):
    ar, state = routes
    from mc import mcp as mcp_mod
    monkeypatch.setitem(state.CONFIG, 'clayrune_mcp_enabled', True)
    monkeypatch.setattr(mcp_mod, '_read_global_servers', lambda: {'mine': {'command': 'x'}})
    monkeypatch.setattr(mcp_mod, '_read_project_servers', lambda p: {})
    servers = json.loads(ar._runtime_mcp_config_json(dict(PROJECT), provider))['mcpServers']
    assert set(servers) == {'mine', 'clayrune'}
    assert servers['clayrune']['command'] == sys.executable
    assert servers['mine'] == {'command': 'x'}


@pytest.mark.parametrize('provider', ['gemini', 'qwen', 'codex', 'opencode', 'goose', 'kiro'])
def test_gemini_qwen_empty_and_excluding_selections_are_honoured(routes, monkeypatch, provider):
    ar, state = routes
    monkeypatch.setitem(state.CONFIG, 'clayrune_mcp_enabled', True)
    empty = json.loads(ar._runtime_mcp_config_json({**PROJECT, 'enabled_mcp_servers': []}, provider))
    assert empty == {'mcpServers': {}}
    only = json.loads(ar._runtime_mcp_config_json({**PROJECT, 'enabled_mcp_servers': ['engram']}, provider))
    assert set(only['mcpServers']) == {'engram'}
    named = json.loads(ar._runtime_mcp_config_json({**PROJECT, 'enabled_mcp_servers': ['clayrune']}, provider))
    assert set(named['mcpServers']) == {'clayrune'}


@pytest.mark.parametrize('provider', ['gemini', 'qwen', 'codex', 'opencode', 'goose', 'kiro'])
def test_gemini_qwen_kill_switch(routes, monkeypatch, provider):
    ar, state = routes
    from mc import mcp as mcp_mod
    monkeypatch.setitem(state.CONFIG, 'clayrune_mcp_enabled', False)
    monkeypatch.setattr(mcp_mod, '_read_global_servers', lambda: {})
    monkeypatch.setattr(mcp_mod, '_read_project_servers', lambda p: {})
    assert json.loads(ar._runtime_mcp_config_json(dict(PROJECT), provider)) == {'mcpServers': {}}


def test_aider_is_out_of_scope(routes, monkeypatch):
    ar, state = routes
    monkeypatch.setitem(state.CONFIG, 'clayrune_mcp_enabled', True)
    assert ar._runtime_mcp_config_json(dict(PROJECT), 'aider') == ''
