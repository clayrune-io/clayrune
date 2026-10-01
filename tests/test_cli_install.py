"""mc/cli_install.py + tools/cli-version-check.py: ONE install-method detector
decides the updater for both the in-app Update button and the daily tool.

Field case 2026-10-01: codex was OpenAI's standalone install
(~/.codex/packages/standalone/...), no npm on PATH. The tool's hardcoded
`npm install -g` could not have helped and, with npm present, would have left
a second shadowed copy. Nothing here runs a real installer.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from mc import cli_install


def _load_cvc():
    path = Path(__file__).resolve().parent.parent / 'tools' / 'cli-version-check.py'
    spec = importlib.util.spec_from_file_location('cli_version_check_inst', path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def cvc():
    return _load_cvc()


STANDALONE = '/Users/k/.codex/packages/standalone/current/bin/codex'
NPM = '/Users/k/.nvm/versions/node/v22/bin/codex'
BREW = '/opt/homebrew/Caskroom/codex/0.160.0/codex'


@pytest.mark.parametrize('path,method', [
    (STANDALONE, 'standalone'), (NPM, 'npm'), (BREW, 'brew'),
    ('/usr/local/bin/codex', ''), (None, ''),
])
def test_update_command_follows_install_method(path, method):
    got = cli_install.update_command('codex', path, 'darwin')
    assert got == (cli_install.CODEX_UPDATE_COMMANDS[method] if method else None)


def test_update_command_claude_is_flavour_independent_and_unknown_cli_is_none():
    assert cli_install.update_command('claude', '/anything') == 'claude update'
    assert cli_install.update_command('gemini', '/anything') is None


@pytest.fixture(autouse=True)
def _posix_detection(cvc, monkeypatch):
    # The fixture paths are POSIX; pin the platform so a Windows runner does
    # not read 'standalone' as the Windows installer flavour.
    monkeypatch.setattr(cvc, '_install_method',
                        lambda p: cli_install.install_method(p, 'darwin'))


def test_tool_standalone_gets_install_script_never_npm(cvc):
    argv, method = cvc.update_argv(cvc.CLIS[2], STANDALONE)
    assert method == 'standalone'
    assert argv[:2] == ['sh', '-c'] and 'chatgpt.com/codex/install.sh' in argv[2]
    assert 'npm' not in argv


def test_tool_npm_keeps_npm_and_brew_gets_brew(cvc):
    codex = cvc.CLIS[2]
    assert codex.name == 'codex'
    assert cvc.update_argv(codex, NPM) == (codex.update_cmd, 'npm')
    assert cvc.update_argv(codex, BREW) == (['brew', 'upgrade', '--cask', 'codex'], 'brew')


def test_tool_unknown_codex_install_is_not_guessed(cvc):
    argv, method = cvc.update_argv(cvc.CLIS[2], '/usr/local/bin/codex')
    assert argv is None and method == ''


def test_tool_other_clis_keep_their_fixed_update_cmd(cvc):
    gemini = cvc.CLIS[1]
    assert cvc.update_argv(gemini, '/x/gemini') == (gemini.update_cmd, None)


def _stub(cvc, monkeypatch, path):
    monkeypatch.setattr(cvc, 'installed_version', lambda name: ('0.153.0', path))
    monkeypatch.setattr(cvc, 'latest_version', lambda pkg: '0.160.0')
    monkeypatch.setattr(cvc, 'shadow_check', lambda name, p: [])
    monkeypatch.setattr(cvc, 'list_processes', lambda: [])
    calls = []
    monkeypatch.setattr(cvc, '_run', lambda cmd, timeout=120: (calls.append(cmd), (0, 'ok'))[1])
    monkeypatch.setattr(cvc.shutil, 'which', lambda x: x)
    return calls


def test_check_one_standalone_apply_runs_install_script_not_npm(cvc, monkeypatch):
    calls = _stub(cvc, monkeypatch, STANDALONE)
    row = cvc.check_one(cvc.CLIS[2], apply_updates=True)
    assert row['install_method'] == 'standalone'
    assert len(calls) == 1 and calls[0][0] == 'sh' and 'install.sh' in calls[0][2]


def test_check_one_unknown_method_skips_update_and_says_so(cvc, monkeypatch):
    calls = _stub(cvc, monkeypatch, '/usr/local/bin/codex')
    row = cvc.check_one(cvc.CLIS[2], apply_updates=True)
    assert calls == [] and row['updated'] is False
    assert row['install_method'] == 'unknown'
    assert 'not guessing' in row['update_skipped']
    assert row['status'] == 'outdated'
