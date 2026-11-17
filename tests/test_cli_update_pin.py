"""MC-1179 AP58: the daily updater leaves Aider alone while the restricted adapter is on."""
from __future__ import annotations

import pytest

from mc import aider_launch, cli_update, cli_update_pin, state
from tests.test_cli_update import Box, FakeRT, NOW, state_file  # noqa: F401  (fixture)


def _aider(tmp_path):
    binary = tmp_path / 'pipx' / 'venvs' / 'aider-chat' / 'bin' / 'aider'
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/sh\n')
    return binary


def _pass(binary, box):
    return cli_update.run_once(
        enabled_fn=lambda: True, runtimes_fn=lambda: [FakeRT('aider', binary)],
        in_use_fn=lambda n: [], now_fn=lambda: NOW, run_fn=box.run,
        http_fn=lambda url, timeout=15: {'info': {'version': '0.99.0'}},
        list_fn=lambda: [], force=True, platform='linux')


def test_flag_on_aider_is_pinned_and_no_updater_runs(tmp_path, state_file, monkeypatch):
    monkeypatch.setitem(state.CONFIG, aider_launch.FLAG, True)
    box = Box('0.86.2', '0.99.0', bumps_to='0.99.0')
    out = _pass(_aider(tmp_path), box)
    assert out['checked'] == {'aider': 'pinned'}
    assert box.updater_calls() == []
    assert box.version == '0.86.2'
    assert 'audited' in out['records']['aider']['detail']


def test_flag_off_aider_is_updated_as_before(tmp_path, state_file, monkeypatch):
    monkeypatch.setitem(state.CONFIG, aider_launch.FLAG, False)
    monkeypatch.setattr(cli_update.shutil, 'which', lambda n: n)
    box = Box('0.86.2', '0.99.0', bumps_to='0.99.0')
    out = _pass(_aider(tmp_path), box)
    assert out['checked']['aider'] != 'pinned'


@pytest.mark.parametrize('name', ['claude', 'codex', 'gemini', 'qwen', 'opencode'])
def test_the_pin_never_reaches_another_cli(name, monkeypatch):
    monkeypatch.setitem(state.CONFIG, aider_launch.FLAG, True)
    assert cli_update_pin.pinned_reason(name) is None


@pytest.mark.parametrize('value', [False, 'true', 1, None])
def test_only_the_exact_flag_value_pins(value, monkeypatch):
    monkeypatch.setitem(state.CONFIG, aider_launch.FLAG, value)
    assert cli_update_pin.pinned_reason('aider') is None
