"""``tools/with-secret.py``'s MC-979 fallback: when the vault is locked in
THIS process (the CLI) but was unlocked in the SERVER's — the unwrapped
master key never crosses processes — the CLI must ask the server's
``POST /api/secrets/exec`` to run the command instead of just failing.

Exercises the real ``with-secret.py`` entrypoint (not just the helper it
calls), same pattern as ``test_end_to_end_with_secret_cli_blocks_an_omitted_
flag`` in test_secrets_store.py, against a real local HTTP server standing in
for the exec route — no real network call, no `claude auth login`.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    from mc import secrets_store
    secrets_store._dispensed.clear()
    secrets_store._unlocked_key = None
    secrets_store._lock_notified = False
    secrets_store._key_mismatch = False
    secrets_store._exec_token = None
    return secrets_store


@pytest.fixture()
def with_secret(vault, monkeypatch):
    """A fresh module object for ``tools/with-secret.py``, wired to the same
    (test-isolated) vault module the ``vault`` fixture manipulates — mirrors
    test_secrets_store.py's MC-923 end-to-end test."""
    spec = importlib.util.spec_from_file_location(
        'with_secret_mc979', REPO / 'tools' / 'with-secret.py')
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, 'vault', vault)
    return mod


def _lock_the_vault(vault):
    """Create the secret these tests reference, configure the passphrase
    lock (which auto-unlocks), then simulate a fresh CLI process (this one)
    that never unlocked it — `wrapped_key_path()` exists but `_unlocked_key`
    is None, exactly MC-979's scenario. The secret must exist BEFORE the
    lock so the record is in the store; locking only blocks decrypting it."""
    vault.set_secret('demo.token', 'TOKEN-VALUE-XYZ')
    vault.set_passphrase('a real passphrase')
    vault._unlocked_key = None
    vault._lock_notified = True  # skip the out-of-process push relay — not under test here


class _ExecHandler(BaseHTTPRequestHandler):
    """Stands in for `POST /api/secrets/exec` — records the request body and
    the auth header it received, and returns a scripted response."""
    response_body: dict = {}
    response_status: int = 200
    seen: list = []

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length).decode('utf-8'))
        type(self).seen.append({
            'path': self.path,
            'token': self.headers.get('X-Clayrune-Exec-Token'),
            'body': body,
        })
        payload = json.dumps(type(self).response_body).encode('utf-8')
        self.send_response(type(self).response_status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


@pytest.fixture()
def exec_server():
    _ExecHandler.seen = []
    _ExecHandler.response_body = {}
    _ExecHandler.response_status = 200
    server = HTTPServer(('127.0.0.1', 0), _ExecHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, _ExecHandler
    server.shutdown()
    thread.join(timeout=2)


def _point_at_server(with_secret, monkeypatch, server):
    monkeypatch.setattr(with_secret.vault, 'exec_route_port',
                        lambda: server.server_port)


def test_falls_back_to_server_and_prints_its_output(vault, with_secret, monkeypatch, exec_server):
    server, handler = exec_server
    _lock_the_vault(vault)
    vault.ensure_exec_token()  # writes the token file the fallback reads
    _point_at_server(with_secret, monkeypatch, server)
    handler.response_body = {'exit_code': 0, 'stdout': 'ok from server\n', 'stderr': ''}

    rc = with_secret.main([
        '--env', 'X=demo.token', '--',
        sys.executable, '-c', 'print("SHOULD NOT RUN LOCALLY")',
    ])

    assert rc == 0
    assert handler.seen, "the fallback never called the server"
    req = handler.seen[0]
    assert req['token'] == vault.ensure_exec_token()
    assert req['body']['env'] == [['X', 'demo.token']]
    assert req['body']['command'] == [sys.executable, '-c', 'print("SHOULD NOT RUN LOCALLY")']


def test_stdout_and_exit_code_pass_through_from_the_server(vault, with_secret, monkeypatch,
                                                            exec_server, capsys):
    server, handler = exec_server
    _lock_the_vault(vault)
    vault.ensure_exec_token()
    _point_at_server(with_secret, monkeypatch, server)
    handler.response_body = {'exit_code': 3, 'stdout': 'from the server\n', 'stderr': 'oops\n'}

    rc = with_secret.main(['--env', 'X=demo.token', '--', 'irrelevant'])

    assert rc == 3
    out = capsys.readouterr()
    assert 'from the server' in out.out
    assert 'oops' in out.err


def test_server_reachable_but_also_locked_gives_the_clear_message(vault, with_secret, monkeypatch,
                                                                   exec_server, capsys):
    server, handler = exec_server
    _lock_the_vault(vault)
    vault.ensure_exec_token()
    _point_at_server(with_secret, monkeypatch, server)
    handler.response_status = 423
    handler.response_body = {'error': 'vault_locked', 'message': 'locked'}

    rc = with_secret.main(['--env', 'X=demo.token', '--', 'irrelevant'])

    assert rc == 2
    assert 'vault is locked' in capsys.readouterr().err


def test_server_unreachable_gives_the_clear_message_not_a_crash(vault, with_secret, monkeypatch,
                                                                 capsys):
    _lock_the_vault(vault)
    vault.ensure_exec_token()
    # Port 1 is a privileged, always-refused port — nothing will ever answer.
    monkeypatch.setattr(with_secret.vault, 'exec_route_port', lambda: 1)

    rc = with_secret.main(['--env', 'X=demo.token', '--', 'irrelevant'])

    assert rc == 2
    assert 'vault is locked' in capsys.readouterr().err


def test_no_token_file_gives_the_clear_message_without_any_network_call(vault, with_secret,
                                                                        monkeypatch, capsys):
    """No token minted yet (e.g. server never started this boot) — the
    fallback must recognize it can't authenticate and give up cleanly rather
    than firing a request with an empty/garbage header."""
    _lock_the_vault(vault)
    # Deliberately do NOT call vault.ensure_exec_token() — no token file exists.
    monkeypatch.setattr(with_secret.vault, 'exec_route_port',
                        lambda: (_ for _ in ()).throw(AssertionError(
                            'should never look up a port with no token')))

    rc = with_secret.main(['--env', 'X=demo.token', '--', 'irrelevant'])

    assert rc == 2
    assert 'vault is locked' in capsys.readouterr().err


def test_raw_flag_does_not_fall_back_and_says_why(vault, with_secret, monkeypatch, capsys,
                                                   exec_server):
    server, handler = exec_server
    _lock_the_vault(vault)
    vault.ensure_exec_token()
    _point_at_server(with_secret, monkeypatch, server)

    rc = with_secret.main([
        '--raw', '--env', 'X=demo.token', '--',
        sys.executable, '-c', 'print("SHOULD NOT RUN")',
    ])

    assert rc == 2
    assert not handler.seen, "--raw must never use the server-exec fallback"
    err = capsys.readouterr().err
    assert 'vault is locked' in err
    assert '--raw' in err


def test_unattended_and_allow_unattended_false_never_reaches_the_server(
        vault, with_secret, monkeypatch, exec_server):
    """A per-secret policy gate is enforced by whichever process actually
    resolves the secret (in-process here, since the vault ends up NOT
    locked) — this must refuse before ever touching the fallback path."""
    vault.set_secret('bank.password', 'value-danger', allow_unattended=False)
    monkeypatch.setattr(vault, '_session_id_from_env', lambda: 'fake-sid')
    monkeypatch.setattr(vault, '_lookup_trigger_type', lambda _sid: 'schedule')

    rc = with_secret.main([
        '--env', 'X=bank.password', '--',
        sys.executable, '-c', 'print("SHOULD NOT RUN")',
    ])

    assert rc == 2
    _server, handler = exec_server
    assert not handler.seen
