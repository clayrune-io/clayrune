"""Secrets vault tests — mc/secrets_store.py.

The invariants worth pinning are the ones that would leak a credential:
values never appear in metadata, scope/attendedness denials actually deny,
and nothing is ever written inside the repo.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    """A vault rooted in tmp_path, on the file key backend so the test never
    touches (or prompts) the real OS keyring."""
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
    # Hermetic: whoever runs this suite might genuinely be a Claude Code
    # session (this one included), which would otherwise leak a real
    # CLAUDE_CODE_SESSION_ID in and make the MC-923 unattended-context
    # detection hit the *real* local server. Tests that want to exercise
    # detection mock it explicitly instead (see the MC-923 section below).
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    from mc import secrets_store
    # Module-level caches that must not bleed between tests.
    secrets_store._dispensed.clear()
    return secrets_store


# ── Round trip ───────────────────────────────────────────────────────────────

def test_set_and_get_round_trip(vault):
    vault.set_secret('reddit.password', 'hunter2-correct-horse',
                     description='throwaway')
    assert vault.get_secret_value('reddit.password',
                                  consumer='test') == 'hunter2-correct-horse'


def test_rotate_replaces_value_and_keeps_created_at(vault):
    first = vault.set_secret('api.key', 'old-value-aaaa')
    second = vault.set_secret('api.key', 'new-value-bbbb')
    assert second['created_at'] == first['created_at']
    assert second['updated_at'] >= first['updated_at']
    assert vault.get_secret_value('api.key', consumer='test') == 'new-value-bbbb'


def test_unknown_secret_raises(vault):
    with pytest.raises(vault.SecretNotFound):
        vault.get_secret_value('nope.nothing', consumer='test')


@pytest.mark.parametrize('bad', ['', 'UPPER', 'has space', 'a/b', '.leading',
                                 'x' * 65])
def test_invalid_names_rejected(vault, bad):
    with pytest.raises(vault.SecretsError):
        vault.set_secret(bad, 'value-value')


def test_empty_value_rejected(vault):
    with pytest.raises(vault.SecretsError):
        vault.set_secret('a.b', '')


# ── The value must never leak through metadata ───────────────────────────────

def test_listing_never_carries_the_value(vault):
    vault.set_secret('social.token', 'SUPERSECRET-VALUE-123', hint='the X token')
    blob = json.dumps(vault.list_secrets())
    assert 'SUPERSECRET-VALUE-123' not in blob
    assert 'ciphertext' not in blob
    assert 'the X token' in blob  # the hint is user-authored, and is fine


def test_store_file_holds_no_plaintext(vault):
    vault.set_secret('social.token', 'SUPERSECRET-VALUE-123')
    raw = vault.store_path().read_text(encoding='utf-8')
    assert 'SUPERSECRET-VALUE-123' not in raw


def test_audit_log_holds_no_plaintext(vault):
    vault.set_secret('social.token', 'SUPERSECRET-VALUE-123')
    vault.get_secret_value('social.token', consumer='test')
    raw = vault.audit_path().read_text(encoding='utf-8')
    assert 'SUPERSECRET-VALUE-123' not in raw
    assert 'social.token' in raw


# ── Policy enforcement ───────────────────────────────────────────────────────

def test_project_scope_denies_other_projects(vault):
    vault.set_secret('proj.key', 'value-for-alpha', scope='alpha')
    assert vault.get_secret_value('proj.key', consumer='t',
                                  project_id='alpha') == 'value-for-alpha'
    with pytest.raises(vault.SecretDenied):
        vault.get_secret_value('proj.key', consumer='t', project_id='beta')
    with pytest.raises(vault.SecretDenied):
        vault.get_secret_value('proj.key', consumer='t', project_id=None)


def test_global_scope_available_everywhere(vault):
    vault.set_secret('global.key', 'value-global')
    assert vault.get_secret_value('global.key', consumer='t',
                                  project_id='anything') == 'value-global'


def test_attended_only_blocks_unattended_cycles(vault):
    vault.set_secret('bank.password', 'value-danger', allow_unattended=False)
    with pytest.raises(vault.SecretDenied):
        vault.get_secret_value('bank.password', consumer='steward',
                               unattended=True)
    # …but an attended session may still use it.
    assert vault.get_secret_value('bank.password', consumer='chat',
                                  unattended=False) == 'value-danger'


# ── Server-side unattended detection (MC-923) ───────────────────────────────
# `tools/with-secret.py --unattended` used to be trusted purely as whatever
# the caller passed, so an unattended cycle that simply omitted the flag
# silently dodged `allow_unattended=False`. `detect_unattended_context` /
# `detect_effective_unattended` give a CLI-spawned caller a way to derive that
# flag from something it doesn't control instead — server-side ground truth
# (trigger_type MC recorded at dispatch, looked up via the session id the
# Claude Code CLI itself sets), fail-CLOSED whenever it can't be determined.
#
# These are deliberately NOT wired into `get_secret_value` itself — see the
# module comment in mc/secrets_store.py above `detect_unattended_context` for
# why (mc.blueprints.secrets_routes calls it directly from inside the Flask
# server process for the human-facing Secrets panel, where there is no
# CLAUDE_CODE_SESSION_ID at all, and auto-detecting there would refuse a real
# human clicking in the browser).

def test_omitting_the_flag_no_longer_grants_attended_treatment(vault, monkeypatch):
    """The exact MC-923 bug: an unattended-context caller that forgets
    --unattended must still be DETECTED as unattended."""
    monkeypatch.setattr(vault, '_session_id_from_env', lambda: 'fake-sid')
    monkeypatch.setattr(vault, '_lookup_trigger_type', lambda sid: 'schedule')
    effective, reason = vault.detect_effective_unattended(False)  # flag NOT passed
    assert effective is True
    assert 'schedule' in reason


def test_no_session_id_fails_closed_to_unattended(vault, monkeypatch):
    """Undetermined context (no CLAUDE_CODE_SESSION_ID at all) must be
    detected as unattended, never as a free pass."""
    monkeypatch.setattr(vault, '_session_id_from_env', lambda: '')
    is_unattended, reason = vault.detect_unattended_context()
    assert is_unattended is True
    assert 'fail-closed' in reason


def test_session_unknown_to_server_fails_closed_to_unattended(vault, monkeypatch):
    """The session id is real but MC's server can't be reached or has never
    heard of it — still undetermined, still fails closed."""
    monkeypatch.setattr(vault, '_session_id_from_env', lambda: 'fake-sid')
    monkeypatch.setattr(vault, '_lookup_trigger_type', lambda sid: None)
    is_unattended, reason = vault.detect_unattended_context()
    assert is_unattended is True
    assert 'fail-closed' in reason


def test_manual_trigger_type_is_detected_as_attended(vault, monkeypatch):
    monkeypatch.setattr(vault, '_session_id_from_env', lambda: 'fake-sid')
    monkeypatch.setattr(vault, '_lookup_trigger_type', lambda sid: 'manual')
    is_unattended, reason = vault.detect_unattended_context()
    assert is_unattended is False
    assert reason == 'trigger_type=manual'


def test_explicit_flag_still_forces_unattended_even_if_detection_says_manual(vault, monkeypatch):
    """The flag can only ADD strictness, never remove what detection found —
    but it must still work as an explicit opt-in even when detection alone
    would have said attended (e.g. a human deliberately running a task as if
    it were a steward cycle, to test the gate)."""
    monkeypatch.setattr(vault, '_session_id_from_env', lambda: 'fake-sid')
    monkeypatch.setattr(vault, '_lookup_trigger_type', lambda sid: 'manual')
    effective, reason = vault.detect_effective_unattended(True)
    assert effective is True
    assert reason == 'flag'
    # Detection is never even consulted once the flag alone settles it.


def test_end_to_end_with_secret_cli_blocks_an_omitted_flag(vault, monkeypatch):
    """Exercises the actual with-secret.py entrypoint (not just the vault
    helpers it calls) for the exact MC-923 scenario: an unattended-context
    caller that forgets --unattended must still be refused the secret."""
    import importlib.util
    import sys as _sys

    vault.set_secret('bank.password', 'value-danger', allow_unattended=False)
    monkeypatch.setattr(vault, '_session_id_from_env', lambda: 'fake-sid')
    monkeypatch.setattr(vault, '_lookup_trigger_type', lambda sid: 'schedule')

    spec = importlib.util.spec_from_file_location(
        'with_secret_mc923', Path(__file__).resolve().parent.parent / 'tools' / 'with-secret.py')
    assert spec is not None and spec.loader is not None
    with_secret = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(with_secret)
    monkeypatch.setattr(with_secret, 'vault', vault)

    rc = with_secret.main([
        '--env', 'X=bank.password', '--',
        _sys.executable, '-c', 'print("SHOULD NOT RUN")',
    ])
    assert rc == 2


def test_lookup_trigger_type_over_a_real_http_call(vault, monkeypatch):
    """Exercises `_lookup_trigger_type` itself (not a mock of it) against a
    real HTTP server on loopback, so the urllib call + JSON parsing that
    `_detect_unattended_context` depends on is actually proven, not assumed."""
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen['path'] = self.path
            body = _json.dumps({'found': True, 'trigger_type': 'schedule'}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass  # keep test output quiet

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(vault, '_TRIGGER_TYPE_URL',
                            f'http://127.0.0.1:{server.server_port}/api/session/trigger-type')
        assert vault._lookup_trigger_type('some-real-sid') == 'schedule'
        assert seen['path'] == '/api/session/trigger-type?claude_session_id=some-real-sid'
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_lookup_trigger_type_returns_none_when_server_unreachable(vault, monkeypatch):
    """A closed port (nothing listening) must fail closed via None, not raise
    out of `get_secret_value` — an unreachable MC server must never itself
    become the reason a legitimate credential use crashes instead of denies."""
    monkeypatch.setattr(vault, '_TRIGGER_TYPE_URL', 'http://127.0.0.1:1/nope')
    assert vault._lookup_trigger_type('any-sid') is None


def test_scoped_secret_hidden_from_other_projects_listing(vault):
    vault.set_secret('proj.key', 'value-for-alpha', scope='alpha')
    vault.set_secret('open.key', 'value-open')
    names = [s['name'] for s in vault.list_secrets(project_id='beta')]
    assert names == ['open.key']
    assert 'proj.key' in [s['name'] for s in vault.list_secrets(project_id='alpha')]
    assert 'proj.key' in [s['name'] for s in vault.list_secrets()]


def test_denials_are_audited(vault):
    vault.set_secret('proj.key', 'value-for-alpha', scope='alpha')
    with pytest.raises(vault.SecretDenied):
        vault.get_secret_value('proj.key', consumer='t', project_id='beta')
    reasons = [r.get('reason') for r in vault.audit_tail(10)]
    assert 'out_of_scope' in reasons


def test_use_count_and_last_used_advance(vault):
    vault.set_secret('a.b', 'value-value')
    assert vault.list_secrets()[0]['use_count'] == 0
    vault.get_secret_value('a.b', consumer='t')
    vault.get_secret_value('a.b', consumer='t')
    meta = vault.list_secrets()[0]
    assert meta['use_count'] == 2
    assert meta['last_used_at']


# ── Placeholders ─────────────────────────────────────────────────────────────

def test_resolve_placeholders(vault):
    vault.set_secret('reddit.user', 'ron-the-user')
    vault.set_secret('reddit.password', 'p4ssw0rd-long')
    out, used = vault.resolve_placeholders(
        'login {{secret:reddit.user}} / {{ secret:reddit.password }}',
        consumer='test')
    assert out == 'login ron-the-user / p4ssw0rd-long'
    assert used == ['reddit.user', 'reddit.password']


def test_resolve_raises_rather_than_leaving_a_live_placeholder(vault):
    with pytest.raises(vault.SecretNotFound):
        vault.resolve_placeholders('use {{secret:missing.one}}', consumer='t')


def test_text_without_placeholders_is_untouched(vault):
    out, used = vault.resolve_placeholders('nothing here', consumer='t')
    assert out == 'nothing here' and used == []


def test_resolve_user_placeholder(vault):
    vault.set_secret('reddit.password', 'p4ssw0rd-long', username='u/ron')
    out, used = vault.resolve_placeholders(
        '{{user:reddit.password}}:{{secret:reddit.password}}', consumer='test')
    assert out == 'u/ron:p4ssw0rd-long'
    assert used == ['reddit.password']


def test_username_is_metadata_not_ciphertext(vault):
    """It rides in the listing on purpose — it's what tells two accounts on the
    same site apart. The value still must not."""
    vault.set_secret('site.password', 'p4ssw0rd-long', username='ron@example.com')
    entry = vault.list_secrets()[0]
    assert entry['username'] == 'ron@example.com'
    assert 'p4ssw0rd-long' not in json.dumps(entry)


def test_user_placeholder_without_a_username_raises(vault):
    """Better a refused command than a login attempt with an empty user field."""
    vault.set_secret('site.password', 'p4ssw0rd-long')
    with pytest.raises(vault.SecretsError):
        vault.resolve_placeholders('{{user:site.password}}', consumer='t')


def test_username_respects_project_scope(vault):
    vault.set_secret('scoped.password', 'p4ssw0rd-long', username='ron',
                     scope='proj-a')
    assert vault.get_username('scoped.password', project_id='proj-a') == 'ron'
    with pytest.raises(vault.SecretDenied):
        vault.get_username('scoped.password', project_id='proj-b')


def test_username_survives_a_metadata_only_reseal(vault):
    vault.set_secret('site.password', 'p4ssw0rd-long', username='ron')
    again = vault.set_secret('site.password', 'p4ssw0rd-long', username='ron',
                             description='edited')
    assert again['username'] == 'ron'


def test_env_for(vault):
    vault.set_secret('gh.token', 'ghp-token-value')
    env = vault.env_for([('GH_TOKEN', 'gh.token')], consumer='t')
    assert env == {'GH_TOKEN': 'ghp-token-value'}


# ── Redaction ────────────────────────────────────────────────────────────────

def test_redaction_only_covers_dispensed_values(vault):
    vault.set_secret('a.b', 'never-handed-out')
    vault.set_secret('c.d', 'handed-out-value')
    # Not dispensed yet → not scrubbed (and never fingerprintable this way).
    assert vault.redact('never-handed-out') == 'never-handed-out'
    vault.get_secret_value('c.d', consumer='t')
    assert vault.redact('leak: handed-out-value!') == 'leak: [redacted:c.d]!'


def test_short_values_are_not_registered_for_redaction(vault):
    # Scrubbing a 3-char string out of output would corrupt unrelated text.
    vault.set_secret('tiny.pin', 'abc')
    vault.get_secret_value('tiny.pin', consumer='t')
    assert vault.redact('abcdef') == 'abcdef'


def test_deleting_a_secret_stops_redacting_its_old_value(vault):
    vault.set_secret('c.d', 'handed-out-value')
    vault.get_secret_value('c.d', consumer='t')
    vault.delete_secret('c.d')
    assert vault.redact('handed-out-value') == 'handed-out-value'


# ── Storage location — the repo must stay clean ──────────────────────────────

def test_nothing_is_written_inside_the_repo(vault, tmp_path):
    vault.set_secret('a.b', 'value-value')
    vault.get_secret_value('a.b', consumer='t')
    home = tmp_path / '.clayrune'
    for p in (vault.store_path(), vault.key_file_path(), vault.audit_path()):
        assert p.is_file()
        assert home in p.parents
        assert REPO not in p.parents


def test_ciphertext_is_bound_to_its_name(vault):
    """Swapping two ciphertexts between entries must fail, not silently hand
    back the wrong credential — that's what the AAD is for."""
    vault.set_secret('low.risk', 'low-value-xx')
    vault.set_secret('high.risk', 'high-value-xx')
    store = json.loads(vault.store_path().read_text(encoding='utf-8'))
    store['secrets']['low.risk']['ciphertext'] = \
        store['secrets']['high.risk']['ciphertext']
    store['secrets']['low.risk']['nonce'] = store['secrets']['high.risk']['nonce']
    vault.store_path().write_text(json.dumps(store), encoding='utf-8')
    with pytest.raises(vault.SecretsError):
        vault.get_secret_value('low.risk', consumer='t')


def test_delete(vault):
    vault.set_secret('a.b', 'value-value')
    assert vault.delete_secret('a.b') is True
    assert vault.delete_secret('a.b') is False
    assert vault.list_secrets() == []


# ── The agent-facing runner ──────────────────────────────────────────────────

def test_with_secret_injects_env_and_scrubs_output(vault, tmp_path):
    vault.set_secret('demo.token', 'TOKEN-VALUE-XYZ')
    env = dict(os.environ)
    env['CLAYRUNE_HOME'] = str(tmp_path / '.clayrune')
    env['CLAYRUNE_SECRETS_KEY_BACKEND'] = 'file'
    # The child deliberately prints the secret — the runner must scrub it.
    res = subprocess.run(
        [sys.executable, str(REPO / 'tools' / 'with-secret.py'),
         '--env', 'DEMO_TOKEN=demo.token', '--',
         sys.executable, '-c',
         'import os; print("got", os.environ["DEMO_TOKEN"])'],
        capture_output=True, text=True, env=env)
    assert res.returncode == 0, res.stderr
    assert 'TOKEN-VALUE-XYZ' not in res.stdout
    assert '[redacted:demo.token]' in res.stdout


def test_with_secret_refuses_to_run_on_a_missing_secret(vault, tmp_path):
    env = dict(os.environ)
    env['CLAYRUNE_HOME'] = str(tmp_path / '.clayrune')
    env['CLAYRUNE_SECRETS_KEY_BACKEND'] = 'file'
    res = subprocess.run(
        [sys.executable, str(REPO / 'tools' / 'with-secret.py'),
         '--env', 'X=absent.secret', '--',
         sys.executable, '-c', 'print("SHOULD NOT RUN")'],
        capture_output=True, text=True, env=env)
    assert res.returncode == 2
    assert 'SHOULD NOT RUN' not in res.stdout
