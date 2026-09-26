"""``POST /api/secrets/exec`` and ``POST /api/secrets/notify-vault-locked`` —
mc/blueprints/secrets_routes.py (MC-979).

These are the one deliberate exception to "no route returns a plaintext
value" (the child's OWN output, not a secret, comes back), so they carry
their own gate stack instead of the human-passcode gate the other write
routes use: loopback only, no Cloudflare/tunnel header (tunnel traffic also
looks like loopback), and a per-boot random token. All three must hold.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from flask import Flask

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SECRET = 'PLAINTEXT-VALUE-SHOULD-NEVER-APPEAR'


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
    # Hermetic: this suite may itself be running inside a real Claude Code
    # session, which would otherwise leak a real CLAUDE_CODE_SESSION_ID in
    # and make the MC-923 unattended-context detection hit the *real* local
    # server. Tests that want unattended behavior set it explicitly.
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    from mc import secrets_store
    from mc.blueprints import local_auth, secrets_routes
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    secrets_routes._VAULT_LOCK_FAILS.clear()
    secrets_store._dispensed.clear()
    secrets_store._unlocked_key = None
    secrets_store._lock_notified = False
    secrets_store._key_mismatch = False
    # The per-boot exec token is cached in a module global — a stale token
    # from a previous test's (different) CLAYRUNE_HOME must not leak in.
    secrets_store._exec_token = None
    app = Flask(__name__)
    app.register_blueprint(secrets_routes.bp)
    return app.test_client()


def _create(client, name='demo.token', value=SECRET, **over):
    body = {'name': name, 'value': value}
    body.update(over)
    return client.post('/api/secrets', json=body)


def _token():
    from mc import secrets_store as vault
    return vault.ensure_exec_token()


def _exec(client, **body):
    headers = {'X-Clayrune-Exec-Token': _token()}
    if 'headers' in body:
        headers.update(body.pop('headers'))
    return client.post('/api/secrets/exec', json=body, headers=headers)


PRINT_ENV_CMD = [sys.executable, '-c',
                 'import os, sys; sys.stdout.write(os.environ["X"])']


def test_loopback_with_good_token_runs_the_command(client):
    _create(client)
    res = _exec(client, env=[['X', 'demo.token']], command=PRINT_ENV_CMD)
    assert res.status_code == 200, res.get_data(as_text=True)
    data = res.get_json()
    assert data['exit_code'] == 0
    assert data['stdout'] == '[redacted:demo.token]'


def test_cf_ray_header_is_refused_even_though_loopback(client):
    """Tunnel traffic terminates at cloudflared on THIS host and is forwarded
    to the origin over loopback, so `_is_loopback_request()` alone can't
    distinguish a phone reaching in over the tunnel from a genuine local
    CLI call. Any Cloudflare header must refuse outright."""
    _create(client)
    res = _exec(client, env=[['X', 'demo.token']], command=PRINT_ENV_CMD,
               headers={'Cf-Ray': '1234-ABC'})
    assert res.status_code == 403
    assert res.get_json()['error'] == 'tunnel_refused'


def test_cf_access_header_variants_are_also_refused(client):
    _create(client)
    for header in ('Cf-Connecting-Ip', 'Cf-Access-Jwt-Assertion', 'CF-RAY'):
        res = _exec(client, env=[['X', 'demo.token']], command=PRINT_ENV_CMD,
                   headers={header: 'x'})
        assert res.status_code == 403, header
        assert res.get_json()['error'] == 'tunnel_refused', header


def test_missing_token_is_refused(client):
    _create(client)
    res = client.post('/api/secrets/exec',
                      json={'env': [['X', 'demo.token']], 'command': PRINT_ENV_CMD})
    assert res.status_code == 403
    assert res.get_json()['error'] == 'bad_exec_token'


def test_wrong_token_is_refused(client):
    _create(client)
    res = client.post('/api/secrets/exec',
                      json={'env': [['X', 'demo.token']], 'command': PRINT_ENV_CMD},
                      headers={'X-Clayrune-Exec-Token': 'not-the-real-token'})
    assert res.status_code == 403
    assert res.get_json()['error'] == 'bad_exec_token'


def test_non_loopback_caller_is_refused(client, monkeypatch):
    from mc.blueprints import secrets_routes
    monkeypatch.setattr(secrets_routes, '_is_loopback_request', lambda: False)
    _create(client)
    res = _exec(client, env=[['X', 'demo.token']], command=PRINT_ENV_CMD)
    assert res.status_code == 403
    assert res.get_json()['error'] == 'loopback_required'


def test_locked_vault_gives_a_clear_error(client):
    from mc import secrets_store as vault
    _create(client)
    from mc.blueprints import local_auth
    local_auth._local_auth_set_passcode('unlock1234')
    set_res = client.post('/api/secrets/vault-lock/set',
                          json={'passphrase': 'a real passphrase',
                                'passcode': 'unlock1234'})
    assert set_res.status_code == 200
    vault._unlocked_key = None  # simulate a restart: locked again
    # This bare test app never calls push_mobile.wire(), so
    # `_notify_vault_locked()` would otherwise take its out-of-process
    # relay branch and reach for a REAL port 5199 — not what this test is
    # about (the relay itself is covered by the notify-vault-locked route
    # tests below). Short-circuit it the same way it short-circuits itself
    # after the first real lock notification.
    vault._lock_notified = True

    res = _exec(client, env=[['X', 'demo.token']], command=PRINT_ENV_CMD)
    assert res.status_code == 423
    data = res.get_json()
    assert data['error'] == 'vault_locked'
    assert SECRET not in res.get_data(as_text=True)


def test_unattended_and_allow_unattended_false_is_refused(client):
    _create(client, allow_unattended=False)
    res = _exec(client, env=[['X', 'demo.token']], command=PRINT_ENV_CMD,
               unattended=True)
    assert res.status_code == 400
    assert 'attended-only' in res.get_json()['error']


def test_output_is_scrubbed_of_the_secret_value(client):
    _create(client, value=SECRET)
    print_secret_cmd = [sys.executable, '-c',
                        'import os, sys; sys.stdout.write(os.environ["X"]); '
                        'sys.stderr.write(os.environ["X"])']
    res = _exec(client, env=[['X', 'demo.token']], command=print_secret_cmd)
    assert res.status_code == 200
    data = res.get_json()
    body_text = res.get_data(as_text=True)
    assert SECRET not in data['stdout']
    assert SECRET not in data['stderr']
    assert SECRET not in body_text
    assert '[redacted:demo.token]' in data['stdout']
    assert '[redacted:demo.token]' in data['stderr']


def test_raw_flag_is_rejected(client):
    _create(client)
    res = _exec(client, env=[['X', 'demo.token']], command=PRINT_ENV_CMD, raw=True)
    assert res.status_code == 400


def test_unknown_secret_is_refused_and_nothing_runs(client):
    res = _exec(client, env=[['X', 'nope.secret']], command=PRINT_ENV_CMD)
    assert res.status_code == 400


def test_exit_code_and_stderr_pass_through(client):
    _create(client)
    fail_cmd = [sys.executable, '-c', 'import sys; sys.exit(7)']
    res = _exec(client, env=[['X', 'demo.token']], command=fail_cmd)
    assert res.status_code == 200
    assert res.get_json()['exit_code'] == 7


def test_notify_vault_locked_requires_the_same_gates(client):
    res = client.post('/api/secrets/notify-vault-locked', json={})
    assert res.status_code == 403
    assert res.get_json()['error'] == 'bad_exec_token'

    res = client.post('/api/secrets/notify-vault-locked', json={},
                      headers={'X-Clayrune-Exec-Token': _token(),
                               'Cf-Ray': 'x'})
    assert res.status_code == 403
    assert res.get_json()['error'] == 'tunnel_refused'


def test_notify_vault_locked_succeeds_with_good_token(client, monkeypatch):
    calls = []
    from mc.blueprints import push_mobile as _bp_push_mobile
    monkeypatch.setattr(_bp_push_mobile, '_notify_push',
                        lambda title, body: calls.append((title, body)))
    res = client.post('/api/secrets/notify-vault-locked', json={},
                      headers={'X-Clayrune-Exec-Token': _token()})
    assert res.status_code == 200
    assert res.get_json() == {'ok': True}
    assert calls == [('Vault locked',
                      'A job needs the secrets vault unlocked — open the '
                      'dashboard to unlock it.')]
