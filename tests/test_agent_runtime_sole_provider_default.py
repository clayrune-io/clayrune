"""MC-1010: server-side sole-installed-CLI auto-pick for `default_provider`.

Moves the auto-pick that used to live in static/js/project-actions.js's
`_maybeSetSoleProviderDefault` server-side (mc.agent_runtime.
maybe_set_sole_provider_default) — the client-side version called
saveSetting(), which since MC-995 goes through humanProofFetch and pops the
dashboard passcode modal on a routine page load, violating the "no dialog,
ever, for an existing install" rule the function's own comment promised
(Ron, 2026-09-14). See tools/smoke/provider-choice-no-popup.mjs for the DOM
side of this fix.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import agent_runtime


def _config_path(tmp_path, initial: dict) -> Path:
    p = tmp_path / 'config.json'
    p.write_text(json.dumps(initial), encoding='utf-8')
    return p


def _rt(name):
    return SimpleNamespace(name=name)


def test_empty_default_one_installed_sets_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_runtime, 'installed_runtimes', lambda: [_rt('codex')])
    config = {'default_provider': '', 'other_key': 'unchanged'}
    cfg_path = _config_path(tmp_path, config)

    result = agent_runtime.maybe_set_sole_provider_default(config, cfg_path)

    assert result is True
    assert config['default_provider'] == 'codex'
    on_disk = json.loads(cfg_path.read_text(encoding='utf-8'))
    assert on_disk['default_provider'] == 'codex'
    assert on_disk['other_key'] == 'unchanged'


def test_already_set_default_is_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_runtime, 'installed_runtimes', lambda: [_rt('codex')])
    config = {'default_provider': 'claude'}
    cfg_path = _config_path(tmp_path, config)

    result = agent_runtime.maybe_set_sole_provider_default(config, cfg_path)

    assert result is False
    assert config['default_provider'] == 'claude'
    on_disk = json.loads(cfg_path.read_text(encoding='utf-8'))
    assert on_disk['default_provider'] == 'claude'


def test_zero_installed_is_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_runtime, 'installed_runtimes', lambda: [])
    config = {'default_provider': ''}
    cfg_path = _config_path(tmp_path, config)

    result = agent_runtime.maybe_set_sole_provider_default(config, cfg_path)

    assert result is False
    assert config['default_provider'] == ''


def test_two_or_more_installed_is_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_runtime, 'installed_runtimes',
                         lambda: [_rt('claude'), _rt('codex')])
    config = {'default_provider': ''}
    cfg_path = _config_path(tmp_path, config)

    result = agent_runtime.maybe_set_sole_provider_default(config, cfg_path)

    assert result is False
    assert config['default_provider'] == ''


def test_logs_once_when_it_sets(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_runtime, 'installed_runtimes', lambda: [_rt('codex')])
    config = {'default_provider': ''}
    cfg_path = _config_path(tmp_path, config)

    calls = []
    monkeypatch.setattr('mc.core._log', lambda *a, **k: calls.append((a, k)))

    agent_runtime.maybe_set_sole_provider_default(config, cfg_path)

    assert len(calls) == 1
    assert 'codex' in calls[0][0][0]


def test_setup_pending_is_untouched_even_with_one_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_runtime, 'installed_runtimes', lambda: [_rt('codex')])
    config = {'default_provider': '', 'setup_completed': False}
    cfg_path = _config_path(tmp_path, config)

    result = agent_runtime.maybe_set_sole_provider_default(config, cfg_path)

    assert result is False
    assert config['default_provider'] == ''
    on_disk = json.loads(cfg_path.read_text(encoding='utf-8'))
    assert on_disk['default_provider'] == ''


def test_setup_completed_true_still_sets(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_runtime, 'installed_runtimes', lambda: [_rt('codex')])
    config = {'default_provider': '', 'setup_completed': True}
    cfg_path = _config_path(tmp_path, config)

    result = agent_runtime.maybe_set_sole_provider_default(config, cfg_path)

    assert result is True
    assert config['default_provider'] == 'codex'
    on_disk = json.loads(cfg_path.read_text(encoding='utf-8'))
    assert on_disk['default_provider'] == 'codex'


def test_setup_completed_key_absent_still_sets(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_runtime, 'installed_runtimes', lambda: [_rt('codex')])
    config = {'default_provider': ''}
    cfg_path = _config_path(tmp_path, config)

    result = agent_runtime.maybe_set_sole_provider_default(config, cfg_path)

    assert result is True
    assert config['default_provider'] == 'codex'


def test_no_log_when_it_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_runtime, 'installed_runtimes', lambda: [])
    config = {'default_provider': ''}
    cfg_path = _config_path(tmp_path, config)

    calls = []
    monkeypatch.setattr('mc.core._log', lambda *a, **k: calls.append((a, k)))

    agent_runtime.maybe_set_sole_provider_default(config, cfg_path)

    assert calls == []
