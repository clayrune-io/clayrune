"""``POST /api/secrets/exec`` and ``POST /api/secrets/notify-vault-locked`` —
mc/blueprints/secrets_routes.py (MC-979).

These are the one deliberate exception to "no route returns a plaintext
value" (the child's OWN output, not a secret, comes back), so they carry
their own gate stack instead of the human-passcode gate the other write
routes use: loopback only, no Cloudflare/tunnel header (tunnel traffic also
looks like loopback), and a per-boot random token. All three must hold.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
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
    # MC-994 re-review N3, second pass: vault-lock throttling now shares
    # local_auth's ONE per-IP budget instead of its own _VAULT_LOCK_FAILS.
    local_auth._LOCAL_AUTH_FAILS.clear()
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


def test_omitted_claude_session_id_fails_closed_even_if_server_env_looks_attended(
        client, monkeypatch):
    """MC-979 audit finding (confirmed by PoC 2026-09-26): the route used to
    resolve a missing `claude_session_id` by falling back to
    `detect_unattended_context`'s "not given" branch, which reads THIS
    process's own `CLAUDE_CODE_SESSION_ID` — but THIS process is the SERVER,
    not the caller, so that env var (if the server itself happens to have
    inherited one, e.g. started from inside a Claude Code Bash/terminal call)
    has nothing to do with who is actually calling the route. That let ANY
    caller who simply omits `claude_session_id` (and doesn't set
    `unattended=True`) get treated as attended whenever the server process's
    own environment resolved to trigger_type=manual — bypassing
    `allow_unattended=False` for a caller that proved nothing. Omitting the
    field must fail closed regardless of what the server's own env holds.
    """
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'server-boot-session-attended')
    from mc import secrets_store as vault
    monkeypatch.setattr(vault, '_lookup_trigger_type', lambda _sid: 'manual')

    _create(client, allow_unattended=False)
    body = {'env': [['X', 'demo.token']], 'command': PRINT_ENV_CMD}
    # Deliberately omit both `claude_session_id` and `unattended`.
    res = _exec(client, **body)
    assert res.status_code == 400, res.get_data(as_text=True)
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


def test_secret_straddling_the_truncation_boundary_is_not_leaked(client, monkeypatch):
    """MC-979 audit finding (confirmed 2026-09-26): `_decode_and_scrub` used
    to truncate the raw bytes to the output cap BEFORE redacting. A secret
    value that straddled the cut point was reduced to a prefix that
    `vault.redact`'s full-string match couldn't scrub, so that prefix reached
    the caller in cleartext — with a 130-byte secret and a 110-byte cap, the
    first 110 bytes leaked unredacted. Redaction now runs on the full text
    before the cap is applied.
    """
    from mc.blueprints import secrets_routes as sr
    # 100 'A's + the secret (would straddle a byte-110 cap pre-redaction) +
    # 50 'B's after it, so the cap still has to truncate something even once
    # the secret is safely replaced by its (fixed-length) marker first.
    monkeypatch.setattr(sr, '_EXEC_MAX_OUTPUT_BYTES', 130)
    straddling_secret = 'S' * 130
    _create(client, name='big.token', value=straddling_secret)
    filler_then_secret_cmd = [
        sys.executable, '-c',
        'import os, sys; sys.stdout.write("A" * 100 + os.environ["X"] + "B" * 50)']
    res = _exec(client, env=[['X', 'big.token']], command=filler_then_secret_cmd)
    assert res.status_code == 200
    data = res.get_json()
    body_text = res.get_data(as_text=True)
    assert straddling_secret not in data['stdout']
    assert straddling_secret not in body_text
    # No recognizable fragment of the secret (the old bug's leaked prefix)
    # may survive either.
    assert 'S' * 20 not in data['stdout']
    assert 'S' * 20 not in body_text
    assert '[redacted:big.token]' in data['stdout']
    assert '...[truncated]' in data['stdout']
    assert 'B' * 50 not in data['stdout']


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


def _pid_alive(pid: int) -> bool:
    if sys.platform == 'win32':
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_timeout_kills_the_grandchild_too(client, tmp_path):
    """MC-981 follow-up: a bare ``subprocess.run(..., timeout=)`` (what this
    route used before) only kills the direct child it started via
    ``Popen.kill()`` — a resolved command that itself forks a longer-lived
    process (a daemon, a sleeper) used to survive the route's timeout
    entirely, orphaned under whatever PID happened to inherit it. The command
    below spawns its own grandchild, writes the grandchild's pid out, then
    sleeps well past the 1s timeout; once the route reports the timeout the
    grandchild must be gone too, not just the direct child."""
    _create(client)
    pid_file = tmp_path / 'grandchild.pid'
    parent_script = (
        "import subprocess, sys, time\n"
        "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open(r'{pid_file}', 'w').write(str(gc.pid))\n"
        "time.sleep(60)\n"
    )
    parent_cmd = [sys.executable, '-c', parent_script]
    res = _exec(client, env=[['X', 'demo.token']], command=parent_cmd, timeout=1)
    assert res.status_code == 504, res.get_data(as_text=True)
    assert res.get_json()['error'] == 'timeout'

    deadline = time.time() + 5
    while time.time() < deadline and not pid_file.exists():
        time.sleep(0.25)
    assert pid_file.exists(), "grandchild never wrote its pid before the timeout fired"
    grandchild_pid = int(pid_file.read_text().strip())

    deadline = time.time() + 5
    while time.time() < deadline and _pid_alive(grandchild_pid):
        time.sleep(0.25)
    assert not _pid_alive(grandchild_pid), (
        f"grandchild pid {grandchild_pid} survived the route's timeout")


def test_timeout_kills_grandchild_that_outlives_its_exited_parent(client, tmp_path):
    """MC-981 review finding: ``test_timeout_kills_the_grandchild_too`` above
    keeps the direct child alive (it sleeps 60s too) for the whole test, so
    it never exercises the shape that actually broke a bare
    ``taskkill /T /PID <child>`` — the common fork-and-exit daemon pattern,
    where the CHILD SPAWNS A GRANDCHILD AND THEN EXITS ITSELF. Reproduced
    with ``_scratch/mc981_probe.py`` before this fix: once the child has
    already exited, ``taskkill /T`` has nothing to walk the tree from, so the
    grandchild — which is what's actually keeping ``communicate()`` from
    returning, since it inherited the write end of the child's stdout pipe —
    survives forever. The job object this fix wires in
    (``_win_job_object_for``) doesn't have that hole: the grandchild joins
    the job when the child spawns it, and stays a member independent of
    whether the child that created it is still alive."""
    _create(client)
    pid_file = tmp_path / 'grandchild3.pid'
    parent_script = (
        "import subprocess, sys\n"
        "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open(r'{pid_file}', 'w').write(str(gc.pid))\n"
        # No further sleep — this process exits immediately, leaving the
        # grandchild as the only thing still holding the pipe open.
    )
    parent_cmd = [sys.executable, '-c', parent_script]
    res = _exec(client, env=[['X', 'demo.token']], command=parent_cmd, timeout=1)
    assert res.status_code == 504, res.get_data(as_text=True)
    assert res.get_json()['error'] == 'timeout'

    deadline = time.time() + 5
    while time.time() < deadline and not pid_file.exists():
        time.sleep(0.25)
    assert pid_file.exists(), "grandchild never wrote its pid before the timeout fired"
    grandchild_pid = int(pid_file.read_text().strip())

    deadline = time.time() + 5
    while time.time() < deadline and _pid_alive(grandchild_pid):
        time.sleep(0.25)
    assert not _pid_alive(grandchild_pid), (
        f"grandchild pid {grandchild_pid} survived the timeout after its "
        f"parent had already exited")


def test_server_import_does_not_touch_exec_token_path(tmp_path):
    """MC-981 follow-up: ``ensure_exec_token()`` used to run at server.py
    MODULE-import time (right after registering ``secrets_routes.bp``), so
    ANY ``import server`` — a stray script, pytest collecting this very
    module, another agent's one-off ``python -c 'import server'`` — minted
    and persisted a FRESH token to ``exec_token_path()``, silently
    overwriting whatever a REAL running server had already written there. A
    same-box ``with-secret.py`` fallback call made after that read the new
    (wrong) token off disk and got ``bad_exec_token`` from the real server,
    which still held the OLD one in memory, until that server restarted.
    Regression-tested at the process boundary (a real subprocess, not an
    in-process import) because ``sys.modules`` caching would hide the bug for
    any import after the first one in a shared pytest session."""
    repo = Path(__file__).resolve().parent.parent
    home = tmp_path / '.clayrune'
    data_root = tmp_path / 'mcdata'
    (data_root / 'data').mkdir(parents=True)
    env = dict(os.environ)
    env['CLAYRUNE_HOME'] = str(home)
    env['MC_DATA_DIR'] = str(data_root)
    env['CLAYRUNE_SECRETS_KEY_BACKEND'] = 'file'
    env['MC_REMOTE_ENABLED'] = '0'
    env.pop('MC_RESTART_FROM_PID', None)

    result = subprocess.run(
        [sys.executable, '-c', 'import server'], stdin=subprocess.DEVNULL,
        cwd=str(repo), env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (home / 'secrets_exec_token').exists()
