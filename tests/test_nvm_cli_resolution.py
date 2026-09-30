"""nvm-installed provider CLIs must resolve on Linux/macOS (backlog b4b2e068).

A CLI under ~/.nvm/versions/node/<ver>/bin showed "not installed"
(cli_not_found) forever: installer/start.sh runs server.py, which never
augmented PATH, and no runtime's Unix fallback list knew about nvm. Its npm
shim is `#!/usr/bin/env node`, so node had to be on PATH as well.

These run on every platform: sys.platform is patched to 'linux' and HOME /
USERPROFILE point at a fake home.
"""
import os
import shutil
import sys
from pathlib import Path

import pytest

from mc import agent_runtime
from mc import unix_path
from mc.unix_path import augment_unix_path, nvm_bin_dirs


def _make_nvm(home: Path, versions=('v9.0.0', 'v20.20.2'), names=('claude', 'node')):
    bins = {}
    for v in versions:
        b = home / '.nvm' / 'versions' / 'node' / v / 'bin'
        b.mkdir(parents=True)
        for n in names:
            f = b / n
            f.write_text('#!/bin/sh\n')
            f.chmod(0o755)
        bins[v] = b
    return bins


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.delenv('NVM_BIN', raising=False)
    monkeypatch.setattr(sys, 'platform', 'linux')
    return home


# ── nvm_bin_dirs ────────────────────────────────────────────────────────────

def test_picks_newest_by_semver_not_lexical(fake_home):
    bins = _make_nvm(fake_home)
    # lexically 'v9.0.0' > 'v20.20.2'; semver says v20 wins
    assert nvm_bin_dirs() == [bins['v20.20.2']]


def test_nvm_bin_env_wins_when_set(fake_home, monkeypatch):
    bins = _make_nvm(fake_home)
    monkeypatch.setenv('NVM_BIN', str(bins['v9.0.0']))
    assert nvm_bin_dirs() == [bins['v9.0.0']]


def test_nvm_bin_env_ignored_when_dir_missing(fake_home, monkeypatch):
    bins = _make_nvm(fake_home)
    monkeypatch.setenv('NVM_BIN', str(fake_home / 'gone' / 'bin'))
    assert nvm_bin_dirs() == [bins['v20.20.2']]


def test_no_nvm_dir_is_empty(fake_home):
    assert nvm_bin_dirs() == []


# ── augment_unix_path ───────────────────────────────────────────────────────

def test_augment_adds_nvm_dir_and_is_idempotent(fake_home, monkeypatch):
    bins = _make_nvm(fake_home)
    monkeypatch.setenv('PATH', os.pathsep.join(['/usr/bin', '/bin']))
    assert augment_unix_path() is True
    parts = os.environ['PATH'].split(os.pathsep)
    assert str(bins['v20.20.2']) in parts
    assert str(bins['v9.0.0']) not in parts
    # nvm is prepended: the CLI's `#!/usr/bin/env node` shim must get the nvm
    # node it was installed with, not an older distro node in /usr/bin
    assert parts.index(str(bins['v20.20.2'])) < parts.index('/usr/bin')
    before = os.environ['PATH']
    assert augment_unix_path() is False
    assert os.environ['PATH'] == before
    assert os.environ['PATH'].split(os.pathsep).count(str(bins['v20.20.2'])) == 1


def test_augment_without_nvm_leaves_path_unchanged(fake_home, monkeypatch):
    # isolate from real /opt/homebrew, /usr/local/bin on a dev box
    monkeypatch.setattr(unix_path.os.path, 'isdir', lambda d: False)
    monkeypatch.setenv('PATH', '/usr/bin:/bin')
    assert augment_unix_path() is False
    assert os.environ['PATH'] == '/usr/bin:/bin'


def test_augment_does_not_add_nonexistent_dirs(fake_home, monkeypatch):
    monkeypatch.setenv('PATH', '/usr/bin')
    augment_unix_path()
    for p in os.environ['PATH'].split(os.pathsep):
        assert p == '/usr/bin' or os.path.isdir(p)


def test_augment_is_noop_on_windows(fake_home, monkeypatch):
    _make_nvm(fake_home)
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setenv('PATH', 'C:\\Windows')
    assert augment_unix_path() is False
    assert os.environ['PATH'] == 'C:\\Windows'


# ── runtime resolve_binary fallbacks ────────────────────────────────────────

_RUNTIMES = [
    ('claude', agent_runtime.ClaudeRuntime),
    ('gemini', agent_runtime.GeminiRuntime),
    ('qwen', agent_runtime.QwenRuntime),
    ('codex', agent_runtime.CodexRuntime),
    ('opencode', agent_runtime.OpenCodeRuntime),
]


def _isolated(monkeypatch, name):
    monkeypatch.setattr(shutil, 'which', lambda *_a, **_k: None)
    monkeypatch.setattr(agent_runtime, '_npm_global_bin_dirs', lambda: [])
    for fixed in (f'/usr/local/bin/{name}', f'/opt/homebrew/bin/{name}'):
        if Path(fixed).exists():
            pytest.skip(f'real {fixed} on this machine would shadow the fake nvm one')


@pytest.mark.parametrize('name,cls', _RUNTIMES)
def test_runtime_resolves_newest_nvm_binary(fake_home, monkeypatch, name, cls):
    _isolated(monkeypatch, name)
    bins = _make_nvm(fake_home, names=(name, 'node'))
    rt = cls()
    rt._bin_cache = None
    got = rt.resolve_binary()
    assert got is not None
    assert Path(got) == bins['v20.20.2'] / name


@pytest.mark.parametrize('name,cls', _RUNTIMES)
def test_runtime_nvm_bin_env_wins(fake_home, monkeypatch, name, cls):
    _isolated(monkeypatch, name)
    bins = _make_nvm(fake_home, names=(name, 'node'))
    monkeypatch.setenv('NVM_BIN', str(bins['v9.0.0']))
    rt = cls()
    rt._bin_cache = None
    assert Path(rt.resolve_binary()) == bins['v9.0.0'] / name


def test_claude_without_nvm_is_unchanged(fake_home, monkeypatch):
    _isolated(monkeypatch, 'claude')
    # a ~/.local/bin hit still resolves, exactly as before
    lb = fake_home / '.local' / 'bin'
    lb.mkdir(parents=True)
    (lb / 'claude').write_text('')
    assert Path(agent_runtime.ClaudeRuntime().resolve_binary()) == lb / 'claude'
    (lb / 'claude').unlink()
    # nothing anywhere -> bare last resort, as before
    assert Path(agent_runtime.ClaudeRuntime().resolve_binary()) == Path('claude')


def test_merge_registry_path_reaugments_after_install(fake_home, monkeypatch):
    """Providers-panel install path: a CLI (and node) that appears after boot
    resolves on the next _merge_registry_path() with no restart."""
    from mc.blueprints import agent_routes
    _isolated(monkeypatch, 'claude')
    monkeypatch.setenv('PATH', '/usr/bin:/bin')
    assert Path(agent_runtime.ClaudeRuntime().resolve_binary()) == Path('claude')
    bins = _make_nvm(fake_home)  # "install" happens now
    agent_routes._merge_registry_path()
    assert str(bins['v20.20.2']) in os.environ['PATH'].split(os.pathsep)
    assert agent_runtime.claude_installed() is True
