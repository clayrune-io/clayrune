"""`tools/with-secret.py --unset VAR`: an opt-in removal of inherited environment variables from
the child, used by the curated MCP launch (mc/desk_connect/mcp_activation.py) for NODE_OPTIONS and
NODE_PATH. Without the flag nothing changes for any other caller. The server-exec fallback runs in
the server's own environment, so it cannot honour --unset and refuses instead.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    from mc import secrets_store
    secrets_store._dispensed.clear()
    secrets_store._unlocked_key = None
    secrets_store._lock_notified = False
    secrets_store._key_mismatch = False
    secrets_store._exec_token = None
    secrets_store.set_secret('svc.token', 'tok-value-123', description='', scope='global')
    spec = importlib.util.spec_from_file_location('with_secret_unset', REPO / 'tools' / 'with-secret.py')
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, tmp_path


def _child_env(mod, tmp_path, extra):
    out = tmp_path / 'env.json'
    code = f'import os, json; json.dump(dict(os.environ), open({str(out)!r}, "w"))'
    rc = mod.main([*extra, '--env', 'TOKEN=svc.token', '--', sys.executable, '-c', code])
    assert rc == 0
    return json.loads(out.read_text(encoding='utf-8'))


def test_unset_removes_the_named_variables_and_keeps_the_rest(ws, monkeypatch):
    mod, tmp_path = ws
    monkeypatch.setenv('NODE_OPTIONS', '--require C:/evil.js')
    monkeypatch.setenv('NODE_PATH', 'C:/evil')
    monkeypatch.setenv('KEEP_ME', 'yes')
    env = _child_env(mod, tmp_path, ['--raw', '--unset', 'NODE_OPTIONS', '--unset', 'NODE_PATH'])
    assert 'NODE_OPTIONS' not in env and 'NODE_PATH' not in env
    assert env['KEEP_ME'] == 'yes' and env['TOKEN'] == 'tok-value-123'


def test_without_the_flag_the_environment_is_passed_through_unchanged(ws, monkeypatch):
    mod, tmp_path = ws
    monkeypatch.setenv('NODE_OPTIONS', '--max-old-space-size=512')
    for extra in (['--raw'],):
        env = _child_env(mod, tmp_path, extra)
        assert env['NODE_OPTIONS'] == '--max-old-space-size=512'


def test_unset_does_not_remove_a_secret_injected_under_the_same_name(ws, monkeypatch):
    mod, tmp_path = ws
    monkeypatch.setenv('TOKEN', 'inherited-value')
    env = _child_env(mod, tmp_path, ['--raw', '--unset', 'TOKEN'])
    assert env['TOKEN'] == 'tok-value-123'                     # inherited removed first, then the secret injected


def test_the_server_exec_fallback_refuses_unset(ws, monkeypatch):
    mod, _ = ws
    called = []
    monkeypatch.setattr(mod, '_run_via_server_exec', lambda *a, **k: called.append(a) or 0)

    def locked(*a, **k):
        raise mod.vault.VaultLocked('the vault is locked')

    monkeypatch.setattr(mod.vault, 'env_for', locked)
    rc = mod.main(['--unset', 'NODE_OPTIONS', '--env', 'TOKEN=svc.token', '--', sys.executable, '-c', 'pass'])
    assert rc == 2 and called == []
    rc = mod.main(['--env', 'TOKEN=svc.token', '--', sys.executable, '-c', 'pass'])
    assert rc == 0 and len(called) == 1                         # without --unset the fallback is used as before
