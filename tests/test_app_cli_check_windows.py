"""P70 (MC-1179): app.py's CLI checks must resolve a `.cmd` shim on Windows.

An npm global install provides `claude.cmd` and `npm.cmd`. `CreateProcess`
does not resolve a bare name to a `.cmd`, so `subprocess.run(['npm', ...])`
raised FileNotFoundError, `_check_npm` and `_check_claude_cli` returned False,
and every launch ran a winget Node install and ended on a false "Claude CLI not
found". These tests stand in for that resolution rule: `_run_silent` raises
FileNotFoundError for a bare name and succeeds for the resolved path, and
`shutil.which` finds the shims. No real CLI, npm or winget is run.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

NPM = r'C:\fake\nodejs\npm.CMD'
CLAUDE = r'C:\fake\npm-global\claude.CMD'
SHIMS = {'npm': NPM, 'claude': CLAUDE}


@pytest.fixture
def win(monkeypatch):
    """app module with a Windows-like launcher: bare `npm`/`claude` do not
    resolve, the full shim path does. Returns (app, commands_run)."""
    import app as desktop_app

    commands: list[list[str]] = []
    state = {'claude_installed': False}

    def fake_run(cmd, **kwargs):
        commands.append(list(cmd))
        exe = cmd[0]
        if exe in SHIMS:                        # bare name: CreateProcess cannot find a .cmd
            raise FileNotFoundError(exe)
        if exe == NPM and cmd[1:2] == ['install']:
            state['claude_installed'] = True
            return subprocess.CompletedProcess(cmd, 0, '', '')
        if exe == NPM:
            return subprocess.CompletedProcess(cmd, 0, '10.0.0', '')
        if exe == CLAUDE:
            return subprocess.CompletedProcess(
                cmd, 0 if state['claude_installed'] else 1, '', '')
        if exe == 'winget':
            return subprocess.CompletedProcess(cmd, 0, '', '')
        raise FileNotFoundError(exe)

    monkeypatch.setattr(desktop_app, '_run_silent', fake_run)
    monkeypatch.setattr(shutil, 'which', lambda name, *a, **k: SHIMS.get(name))
    monkeypatch.setattr(desktop_app, '_refresh_path', lambda: None)
    desktop_app._state = state
    return desktop_app, commands


def test_check_npm_finds_a_cmd_shim(win):
    app_mod, _ = win
    assert app_mod._check_npm() is True


def test_check_claude_cli_finds_a_cmd_shim(win):
    app_mod, _ = win
    app_mod._state['claude_installed'] = True
    assert app_mod._check_claude_cli() is True


def test_windows_install_with_npm_present_never_runs_winget(win):
    """The launch path: CLI missing, npm installed. It must npm-install the
    CLI through the resolved shim, not install Node through winget."""
    app_mod, commands = win
    ok, msg = app_mod._install_claude_cli_windows()
    assert ok, msg
    assert not any(c[0] == 'winget' for c in commands), commands
    assert [NPM, 'install', '-g', '@anthropic-ai/claude-code'] in commands


def test_a_tool_that_is_not_installed_still_reads_as_missing(win, monkeypatch):
    app_mod, _ = win
    monkeypatch.setattr(shutil, 'which', lambda name, *a, **k: None)
    assert app_mod._check_npm() is False
    assert app_mod._check_claude_cli() is False


def test_refresh_path_keeps_existing_precedence_and_expands_registry(monkeypatch):
    import app as desktop_app

    fake_registry = SimpleNamespace(
        HKEY_LOCAL_MACHINE='system',
        HKEY_CURRENT_USER='user',
        OpenKey=lambda root, subkey: nullcontext(root),
        QueryValueEx=lambda root, name: ({
            'system': r'%MC_TEST_PROGRAMS%\Node;C:\Windows',
            'user': r'C:\Agent\venv;C:\UserTools',
        }[root], 2),
    )
    monkeypatch.setitem(sys.modules, 'winreg', fake_registry)
    monkeypatch.setattr(desktop_app.sys, 'platform', 'win32')
    monkeypatch.setenv('MC_TEST_PROGRAMS', r'C:\Program Files')
    monkeypatch.setenv('PATH', r'C:\Agent\venv;C:\Windows')

    desktop_app._refresh_path()

    assert desktop_app.os.environ['PATH'].split(';') == [
        r'C:\Agent\venv', r'C:\Windows', r'C:\Program Files\Node', r'C:\UserTools',
    ]
