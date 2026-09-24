"""Secrets vault tests — mc/secrets_store.py.

The invariants worth pinning are the ones that would leak a credential:
values never appear in metadata, scope/attendedness denials actually deny,
and nothing is ever written inside the repo.
"""

import base64
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
    secrets_store._unlocked_key = None
    secrets_store._lock_notified = False
    secrets_store._key_mismatch = False
    secrets_store._last_key_use = None
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


# ── Master key durability (2026-09-14 silent-remint incident) ───────────────
#
# Root cause: a wiped OS keyring makes keyring.get_password() return None —
# the same shape as "never had a key" — so load_master_key() minted a fresh
# key over old ciphertext with no log line, orphaning 8 of 10 saved logins.
# These tests pin the fail-closed check before minting, and the file-mirror
# self-heal, using CLAYRUNE_SECRETS_KEY_BACKEND=file or a fake keyring —
# never the real OS keyring.

class _FakeKeyringBackend:
    """In-memory stand-in for the OS credential store, same shape as
    test_identity_mirror.py's fixture: `wipe()` simulates the incident
    (entry gone, no exception), and a raising get_password simulates a
    merely-locked store (different failure, must not be conflated)."""

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}
        self.raise_on_get: Exception | None = None

    def get_password(self, service, account):
        if self.raise_on_get is not None:
            raise self.raise_on_get
        return self.store.get((service, account))

    def set_password(self, service, account, value):
        self.store[(service, account)] = value

    def delete_password(self, service, account):
        if (service, account) not in self.store:
            raise KeyError(f'no entry for {(service, account)}')
        del self.store[(service, account)]

    def wipe(self):
        self.store.clear()


class _FakeDpapi:
    """In-memory stand-in for Windows DPAPI — a reversible transform, never
    the real `crypt32` call. A blob missing the marker prefix can't be
    unsealed, which is how tests simulate a corrupted or foreign-profile
    seal without needing a second machine or real CryptUnprotectData."""

    _PREFIX = b'FAKE-DPAPI-SEAL:'

    def protect(self, data: bytes) -> bytes:
        return self._PREFIX + bytes(reversed(data))

    def unprotect(self, blob: bytes) -> bytes:
        if not blob.startswith(self._PREFIX):
            raise ValueError('not a recognized (fake) DPAPI seal')
        return bytes(reversed(blob[len(self._PREFIX):]))


@pytest.fixture()
def fake_keyring_vault(tmp_path, monkeypatch):
    """A vault where the *keyring* backend is live (backed by an in-memory
    fake, never the real OS keyring) so self-heal/reseed behavior can be
    exercised. Distinct from the `vault` fixture, which forces the file
    backend and never touches keyring code at all.

    DPAPI is also faked here (never the real Windows API — this suite runs
    on a real Windows box, and calling the actual `crypt32` would violate
    "never touch real DPAPI state" even though it only touches bytes we
    control). This represents the Windows-with-a-sealed-mirror shape; see
    `fake_keyring_vault_no_dpapi` for the macOS/Linux shape.
    """
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    # A1 (2026-09-15): _keyring_disabled() now forces the file backend under
    # any overridden CLAYRUNE_HOME unless this is explicit — these tests
    # exist specifically to exercise keyring self-heal behavior, and never
    # touch the real OS credential store (keyring.get_password/set_password
    # are monkeypatched below), so opting back in here is exactly the
    # legitimate case that override exists for.
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'keyring')
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    import keyring as keyring_pkg
    fake = _FakeKeyringBackend()
    monkeypatch.setattr(keyring_pkg, 'get_password', fake.get_password)
    monkeypatch.setattr(keyring_pkg, 'set_password', fake.set_password)
    monkeypatch.setattr(keyring_pkg, 'delete_password', fake.delete_password)
    from mc import secrets_store
    secrets_store._dispensed.clear()
    fake_dpapi = _FakeDpapi()
    monkeypatch.setattr(secrets_store, '_dpapi_available', lambda: True)
    monkeypatch.setattr(secrets_store, '_dpapi_protect', fake_dpapi.protect)
    monkeypatch.setattr(secrets_store, '_dpapi_unprotect', fake_dpapi.unprotect)
    return secrets_store, fake


@pytest.fixture()
def fake_keyring_vault_no_dpapi(fake_keyring_vault, monkeypatch):
    """Same in-memory fake keyring, but DPAPI is unavailable — simulating
    macOS/Linux, where there is no OS primitive to seal a local mirror to
    this user (Keychain/SecretService already *are* the keyring backend).
    Per the module docstring, the keyring is the sole copy on this OS: a
    wipe fails closed instead of self-healing."""
    vault, fake = fake_keyring_vault
    monkeypatch.setattr(vault, '_dpapi_available', lambda: False)
    return vault, fake


def test_empty_store_mints_via_keyring_and_mirrors_via_dpapi(fake_keyring_vault):
    vault, fake = fake_keyring_vault
    backend = vault.load_master_key()[1]
    assert backend == 'keyring'
    assert fake.store  # keyring actually got the key
    assert vault.dpapi_mirror_path().is_file()  # sealed mirror, not just fallback
    # a7e4ebb wrote a plaintext mirror unconditionally; this fix restricts
    # that to platforms with no sealed alternative — Windows never needs it.
    assert not vault.key_file_path().is_file()


def test_wiped_keyring_with_existing_secrets_and_no_mirror_raises(vault):
    """`vault` fixture forces the file backend, so this simulates total loss:
    both the keyring (disabled) and the file mirror (deleted) are gone while
    sealed ciphertext still exists — must raise, never mint a replacement."""
    vault.set_secret('reddit.password', 'orphan-me-not')
    vault.key_file_path().unlink()
    with pytest.raises(vault.SecretsUnavailable):
        vault.get_secret_value('reddit.password', consumer='t')
    # No new key was minted in the process of failing.
    assert not vault.key_file_path().is_file()


def test_wiped_keyring_self_heals_from_dpapi_mirror(fake_keyring_vault):
    vault, fake = fake_keyring_vault
    vault.set_secret('reddit.password', 'still-here')
    fake.wipe()  # THE INCIDENT: keyring returns None, not an exception

    # Self-healed via the sealed mirror, not a freshly minted (wrong) key.
    assert vault.get_secret_value('reddit.password', consumer='t') == 'still-here'
    # And reseeded the keyring for next time.
    assert fake.store, 'self-heal did not restore the keyring'


def test_dpapi_mirror_that_fails_to_unseal_is_treated_as_unavailable_and_does_not_mint(fake_keyring_vault):
    """A DPAPI blob that can no longer be opened (wrong user profile, disk
    copy from a different machine, corruption) must be treated exactly like
    'no mirror' — fail closed, never minted over."""
    vault, fake = fake_keyring_vault
    vault.set_secret('reddit.password', 'orphan-me-not')
    fake.wipe()
    vault.dpapi_mirror_path().write_bytes(b'not-a-real-seal')

    with pytest.raises(vault.SecretsUnavailable):
        vault.get_secret_value('reddit.password', consumer='t')
    # No replacement key was minted into the keyring during the failed attempt.
    assert not fake.store


def test_migration_removes_legacy_plaintext_mirror_once_dpapi_verified(fake_keyring_vault):
    """A pre-existing a7e4ebb-era plaintext `secrets.key` must not survive
    next to a verified DPAPI-sealed mirror — once the sealed replacement is
    proven to hold the same key, the plaintext copy is pure exposure."""
    vault, fake = fake_keyring_vault
    vault.load_master_key()  # keyring now holds a key; DPAPI mirror written too
    assert vault.dpapi_mirror_path().is_file()

    # Simulate a leftover a7e4ebb mirror: the same key, in plaintext.
    vault.key_file_path().parent.mkdir(parents=True, exist_ok=True)
    vault.key_file_path().write_text(vault._keyring_get(), encoding='utf-8')

    vault.load_master_key()  # keyring read succeeds again -> migration runs

    assert not vault.key_file_path().is_file()
    assert vault.dpapi_mirror_path().is_file()


def test_migration_keeps_plaintext_file_holding_a_different_key(fake_keyring_vault):
    """A secrets.key that does NOT match the keyring may be the only copy of
    an older key that sealed existing entries; deleting it would make the
    orphaning permanent. Must be left in place on both OS shapes."""
    vault, fake = fake_keyring_vault
    vault.load_master_key()
    vault.key_file_path().parent.mkdir(parents=True, exist_ok=True)
    vault.key_file_path().write_text('a-different-older-key', encoding='utf-8')

    vault.load_master_key()

    assert vault.key_file_path().read_text(encoding='utf-8') == 'a-different-older-key'


def test_migration_leaves_plaintext_mirror_if_dpapi_mirror_cannot_be_verified(fake_keyring_vault, monkeypatch):
    """If writing the sealed mirror doesn't actually take (silently-failed
    disk write, or anything that makes the round-trip check fail), the
    plaintext copy must NOT be deleted — losing both would be worse than the
    at-rest downgrade this migration exists to fix."""
    vault, fake = fake_keyring_vault
    vault.load_master_key()  # keyring now holds a key (and a real DPAPI mirror)
    vault.key_file_path().parent.mkdir(parents=True, exist_ok=True)
    vault.key_file_path().write_text(vault._keyring_get(), encoding='utf-8')
    # Force the post-write verification read to look like a failed round-trip,
    # regardless of what the (unpatched) write actually did.
    monkeypatch.setattr(vault, '_read_dpapi_mirror', lambda: None)

    vault.load_master_key()

    assert vault.key_file_path().is_file()  # NOT removed — verification failed


def test_no_dpapi_healthy_keyring_writes_no_mirror_at_all(fake_keyring_vault_no_dpapi):
    vault, fake = fake_keyring_vault_no_dpapi
    vault.load_master_key()
    assert not vault.key_file_path().is_file()
    assert not vault.dpapi_mirror_path().is_file()


def test_no_dpapi_migration_removes_legacy_plaintext_mirror_immediately(fake_keyring_vault_no_dpapi):
    """No sealed alternative exists on this OS, so once the keyring is
    proven healthy the plaintext copy is pure exposure with nothing to
    verify against — removed on sight, per the module docstring."""
    vault, fake = fake_keyring_vault_no_dpapi
    vault.load_master_key()  # keyring now holds a key
    vault.key_file_path().parent.mkdir(parents=True, exist_ok=True)
    vault.key_file_path().write_text(vault._keyring_get(), encoding='utf-8')

    vault.load_master_key()  # keyring read succeeds again -> migration runs

    assert not vault.key_file_path().is_file()


def test_no_dpapi_keeps_plaintext_file_holding_a_different_key(fake_keyring_vault_no_dpapi):
    vault, fake = fake_keyring_vault_no_dpapi
    vault.load_master_key()
    vault.key_file_path().parent.mkdir(parents=True, exist_ok=True)
    vault.key_file_path().write_text('a-different-older-key', encoding='utf-8')

    vault.load_master_key()

    assert vault.key_file_path().is_file()


def test_no_dpapi_wiped_keyring_fails_closed_instead_of_self_healing(fake_keyring_vault_no_dpapi):
    """The point-2 protection: no mirror exists on this OS, so a wipe must
    refuse rather than silently mint or magically recover."""
    vault, fake = fake_keyring_vault_no_dpapi
    vault.set_secret('reddit.password', 'no-mirror-on-this-os')
    fake.wipe()
    with pytest.raises(vault.SecretsUnavailable):
        vault.get_secret_value('reddit.password', consumer='t')


@pytest.mark.skipif(sys.platform != 'win32', reason='DPAPI is Windows-only')
def test_real_dpapi_protect_unprotect_round_trip():
    """Exercises the ACTUAL Windows DPAPI call once, on plain bytes with no
    file path involved — never touches CLAYRUNE_HOME, ~/.clayrune, or the
    real keyring. Every other DPAPI-shaped test above uses `_FakeDpapi`."""
    from mc import secrets_store
    data = b'32-bytes-of-fake-master-key-mat'
    sealed = secrets_store._dpapi_protect(data)
    assert sealed != data
    assert secrets_store._dpapi_unprotect(sealed) == data


def test_locked_keyring_falls_back_to_mirror_without_minting(fake_keyring_vault):
    """A merely-locked keystore (raises, doesn't return None) must not be
    treated as 'lost' either — the file mirror already covers it, so the
    read succeeds transparently instead of orphaning anything."""
    vault, fake = fake_keyring_vault
    vault.set_secret('reddit.password', 'still-here-too')
    fake.raise_on_get = RuntimeError('keyring locked')

    assert vault.get_secret_value('reddit.password', consumer='t') == 'still-here-too'


def test_second_secret_after_wipe_does_not_orphan_the_first(fake_keyring_vault):
    """The exact 2026-09-14 shape: an entry stored AFTER a keyring wipe must
    not silently mint a key that makes an earlier entry unreadable."""
    vault, fake = fake_keyring_vault
    vault.set_secret('linkedin.password', 'pre-wipe-value')
    fake.wipe()
    vault.set_secret('github.password', 'post-wipe-value')  # self-heals first

    assert vault.get_secret_value('linkedin.password', consumer='t') == 'pre-wipe-value'
    assert vault.get_secret_value('github.password', consumer='t') == 'post-wipe-value'


# ── 2026-09-15 test-probe-mint incident hardening (A1-A3) ───────────────────
#
# Root cause (docs/_journal/vault-keyring-remint-2026-09-15.md): a redaction
# probe ran with CLAYRUNE_HOME pointed at a mkdtemp'd throwaway directory,
# expecting isolation. But the OS keyring is one GLOBAL entry
# (clayrune/secrets-master-key) shared by every process on the box regardless
# of which store it's paired with — the probe's temp store was (correctly)
# empty, so it minted a fresh key straight into the REAL keyring entry. The
# real server then loaded that key and could decrypt 0 of its 10 real
# secrets. Only S4U's own DPAPI failure kept the same mint from also
# overwriting the last surviving copy of the key that protected them.

def test_temp_home_never_touches_the_real_keyring_by_default(tmp_path, monkeypatch):
    """A1: the exact gap the incident fell through — CLAYRUNE_HOME overridden,
    CLAYRUNE_SECRETS_KEY_BACKEND left unset. keyring.set_password must never
    be called; the vault must fall back to the file backend instead."""
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.delenv('CLAYRUNE_SECRETS_KEY_BACKEND', raising=False)
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    import keyring as keyring_pkg
    from mc import secrets_store
    secrets_store._dispensed.clear()

    set_calls = []
    monkeypatch.setattr(keyring_pkg, 'set_password',
                         lambda *a, **k: set_calls.append((a, k)))
    monkeypatch.setattr(keyring_pkg, 'get_password', lambda *a, **k: None)

    secrets_store.set_secret('a.b', 'value-value')

    assert set_calls == [], ('keyring.set_password must never be called '
                              'under an overridden CLAYRUNE_HOME')
    assert secrets_store.key_file_path().is_file()
    assert secrets_store.get_secret_value('a.b', consumer='t') == 'value-value'


def test_explicit_keyring_backend_opts_back_in_under_temp_home(tmp_path, monkeypatch):
    """The escape hatch A1 leaves open: an operator who really means to
    exercise the real keyring under a temp home can still say so explicitly.

    Uses `_FakeKeyringBackend` (an in-memory stand-in, same as
    `fake_keyring_vault`) rather than a stateless lambda — `set_secret` reads
    the key back via `key_backend()` in the same call, so a mock that always
    answers "no key" would re-mint on every read and make a real reseed
    indistinguishable from this bug. DPAPI is disabled: this box's session
    cannot do real DPAPI (`test_real_dpapi_protect_unprotect_round_trip`
    fails the same way on an unmodified checkout), and the mirror isn't what
    this test is about.
    """
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'keyring')
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    import keyring as keyring_pkg
    from mc import secrets_store
    secrets_store._dispensed.clear()

    fake = _FakeKeyringBackend()
    monkeypatch.setattr(keyring_pkg, 'get_password', fake.get_password)
    monkeypatch.setattr(keyring_pkg, 'set_password', fake.set_password)
    monkeypatch.setattr(secrets_store, '_dpapi_available', lambda: False)

    secrets_store.set_secret('a.b', 'value-value')

    assert fake.store, 'the explicit opt-in must still reach the keyring'
    assert secrets_store.get_secret_value('a.b', consumer='t') == 'value-value'


def test_keyring_key_mismatch_falls_back_to_mirror(fake_keyring_vault):
    """A2: the keyring answers, but with a key that decrypts none of the
    store's secrets (the exact incident shape). Must not be trusted as-is —
    fall back to the DPAPI mirror, use it, and reseed the keyring."""
    vault, fake = fake_keyring_vault
    vault.set_secret('reddit.password', 'sealed-under-real-key')
    # Simulate a foreign mint landing in the (global) keyring entry, as the
    # 2026-09-15 probe did — the DPAPI mirror still holds the real key.
    foreign_key = base64.b64encode(os.urandom(32)).decode('ascii')
    fake.store[(vault.KEYRING_SERVICE, vault.KEYRING_ACCOUNT)] = foreign_key

    assert vault.get_secret_value('reddit.password', consumer='t') == \
        'sealed-under-real-key'
    assert vault.key_mismatch() is True
    # Reseeded with the recovered (real) key, not left pointing at the foreign one.
    assert fake.store[(vault.KEYRING_SERVICE, vault.KEYRING_ACCOUNT)] != foreign_key


def test_keyring_key_mismatch_with_no_working_mirror_raises(fake_keyring_vault):
    """A2: a mismatched keyring key with no fallback that opens anything must
    raise, never silently hand back a key that decrypts nothing."""
    vault, fake = fake_keyring_vault
    vault.set_secret('reddit.password', 'sealed-under-real-key')
    foreign_key = base64.b64encode(os.urandom(32)).decode('ascii')
    fake.store[(vault.KEYRING_SERVICE, vault.KEYRING_ACCOUNT)] = foreign_key
    # Corrupt the DPAPI mirror too, so there is no working fallback either.
    vault.dpapi_mirror_path().write_bytes(b'not-a-real-seal')

    with pytest.raises(vault.SecretsUnavailable, match='key mismatch'):
        vault.get_secret_value('reddit.password', consumer='t')
    assert vault.key_mismatch() is True


def test_mirror_not_overwritten_by_a_key_that_opens_nothing(fake_keyring_vault):
    """A3: _maintain_key_mirror must not reseal a mirror that already
    protects a real record with a new key that decrypts nothing in the
    store — the exact way the 2026-09-15 incident would have destroyed the
    last surviving copy of an older key on a live server, had S4U's DPAPI
    write not (accidentally) failed."""
    vault, fake = fake_keyring_vault
    vault.set_secret('reddit.password', 'protected-by-real-key')
    assert vault.dpapi_mirror_path().is_file()  # mirror now holds the real key

    store = vault._load_store()
    foreign_key = base64.b64encode(os.urandom(32)).decode('ascii')  # opens nothing
    vault._maintain_key_mirror(foreign_key, store)

    recovered = vault._read_dpapi_mirror()
    assert recovered != foreign_key
    assert vault._key_opens_any(base64.b64decode(recovered), store)


def test_mirror_still_reseals_for_a_key_that_does_open_something(fake_keyring_vault):
    """A3's gate is about protecting a mirror that already guards real
    ciphertext — it must not block the ordinary case where the new key is
    legitimate and simply hasn't been mirrored yet."""
    vault, fake = fake_keyring_vault
    vault.set_secret('reddit.password', 'sealed-under-real-key')
    store = vault._load_store()
    real_key = vault._keyring_get()

    # Blank the mirror, simulating a first sync that hasn't happened yet.
    vault.dpapi_mirror_path().unlink()
    vault._maintain_key_mirror(real_key, store)

    assert vault._read_dpapi_mirror() == real_key


# ── Health check: is_readable / list_secrets(check_readable=True) ──────────

def test_is_readable_true_for_a_healthy_entry(vault):
    vault.set_secret('a.b', 'value-value')
    assert vault.is_readable('a.b') is True


def test_is_readable_false_for_unknown_name(vault):
    assert vault.is_readable('nope.nothing') is False


def test_is_readable_false_after_master_key_replaced(vault):
    """Simulates the incident's end state directly: ciphertext sealed under
    one key, a different key now in the file. Must report unreadable, not
    raise, and must never touch/return the value."""
    vault.set_secret('linkedin.password', 'orphaned-value')
    # Swap the key file's contents for a fresh, unrelated key — same effect
    # as a silent remint, without going through load_master_key() to get
    # there (that path is now guarded; this proves is_readable independently).
    vault.key_file_path().write_text(
        base64.b64encode(os.urandom(32)).decode('ascii'), encoding='utf-8')
    assert vault.is_readable('linkedin.password') is False


def test_list_secrets_check_readable_reports_per_name(vault):
    vault.set_secret('good.one', 'fine-value')
    vault.set_secret('bad.one', 'about-to-be-orphaned')
    vault.key_file_path().write_text(
        base64.b64encode(os.urandom(32)).decode('ascii'), encoding='utf-8')
    # good.one and bad.one are BOTH now unreadable (same key rotated under
    # both) — this asserts the report shape, not selective breakage.
    items = {s['name']: s for s in vault.list_secrets(check_readable=True)}
    assert items['good.one']['readable'] is False
    assert items['bad.one']['readable'] is False
    assert 'fine-value' not in json.dumps(items)
    assert 'about-to-be-orphaned' not in json.dumps(items)


def test_list_secrets_without_check_readable_omits_the_field(vault):
    vault.set_secret('a.b', 'value-value')
    items = vault.list_secrets()
    assert 'readable' not in items[0]


# ── Passphrase lock (MC backlog 503edfe4) ────────────────────────────────────

def _relock(vault):
    """Simulate a server restart: the in-memory key is gone, only the
    wrapped file on disk remains."""
    vault._unlocked_key = None
    vault._lock_notified = False


def test_lock_state_starts_unconfigured(vault):
    assert vault.lock_state() == 'unconfigured'
    assert vault.is_locked() is False


def test_set_passphrase_migrates_existing_secret_and_auto_unlocks(vault):
    vault.set_secret('reddit.password', 'pre-lock-value')
    recovery_key = vault.set_passphrase('correct horse battery staple')
    assert vault.lock_state() == 'unlocked'
    assert vault.wrapped_key_path().is_file()
    assert isinstance(recovery_key, str) and len(recovery_key) > 10
    # The live plaintext key file is gone — leaving it live would make the
    # lock cosmetic (Dave's review, MC 503edfe4) — but it was MOVED, not
    # destroyed: a copy survives under the quarantine dir.
    assert not vault.key_file_path().is_file()
    quarantined = list(vault.legacy_key_quarantine_dir().rglob('secrets.key'))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding='utf-8').strip()
    # The record sealed before the lock existed must still read back clean.
    assert vault.get_secret_value('reddit.password', consumer='test') == 'pre-lock-value'


def test_set_passphrase_quarantine_survives_a_restart(vault):
    """The quarantined file must not be something load_master_key() (or any
    other code path) ever reads again — simulate a restart (in-memory key
    gone) and confirm the vault still unlocks and reads purely off the
    wrapped file, never falling back to the quarantined copy."""
    vault.set_secret('reddit.password', 'pre-lock-value')
    vault.set_passphrase('correct horse battery staple')
    _relock(vault)
    vault.unlock_with_passphrase('correct horse battery staple')
    assert vault.get_secret_value('reddit.password', consumer='test') == 'pre-lock-value'


def test_set_passphrase_quarantines_the_keyring_entry(fake_keyring_vault):
    """Keyring backend (not the file fallback): the live keyring entry must
    be gone after set_passphrase, its value preserved in quarantine."""
    vault, fake = fake_keyring_vault
    vault.set_secret('reddit.password', 'pre-lock-value')
    assert fake.get_password(vault.KEYRING_SERVICE, vault.KEYRING_ACCOUNT) is not None
    vault.set_passphrase('correct horse battery staple')
    assert fake.get_password(vault.KEYRING_SERVICE, vault.KEYRING_ACCOUNT) is None
    quarantined = list(vault.legacy_key_quarantine_dir().rglob('keyring_secrets-master-key.b64'))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding='utf-8').strip()
    assert vault.get_secret_value('reddit.password', consumer='test') == 'pre-lock-value'


def test_set_passphrase_quarantine_failure_does_not_block_the_lock(vault, monkeypatch, tmp_path):
    """A quarantine step failing (e.g. an unwritable quarantine dir) must not
    unwind the already-durable wrapped-key write — the lock still takes
    effect even if a legacy copy could not be moved this time."""
    from mc import secrets_store
    # A plain FILE sitting where the quarantine dir wants to be: mkdir(...)
    # then reliably raises FileExistsError, cross-platform, no permissions
    # hackery needed.
    blocker = tmp_path / 'quarantine_blocker'
    blocker.write_text('in the way')
    monkeypatch.setattr(secrets_store, 'legacy_key_quarantine_dir', lambda: blocker)
    vault.set_secret('reddit.password', 'pre-lock-value')
    recovery_key = vault.set_passphrase('correct horse battery staple')
    assert isinstance(recovery_key, str) and len(recovery_key) > 10
    assert vault.lock_state() == 'unlocked'
    # The legacy file is still live in this failure case — quarantining it
    # is best-effort, but the lock itself must never depend on it succeeding.
    assert vault.key_file_path().is_file()


def test_set_passphrase_refuses_short_passphrase(vault):
    with pytest.raises(vault.SecretsError):
        vault.set_passphrase('short')
    assert vault.lock_state() == 'unconfigured'


def test_set_passphrase_refuses_when_already_configured(vault):
    vault.set_passphrase('correct horse battery staple')
    with pytest.raises(vault.SecretsError):
        vault.set_passphrase('a different passphrase entirely')


def test_load_master_key_raises_vault_locked_after_relock(vault):
    vault.set_secret('reddit.password', 'pre-lock-value')
    vault.set_passphrase('correct horse battery staple')
    _relock(vault)
    assert vault.lock_state() == 'locked'
    with pytest.raises(vault.VaultLocked):
        vault.load_master_key()


def test_locked_read_fails_closed(vault):
    """A locked vault must refuse get_secret_value outright, not return a
    stale or empty value — the fail-closed posture the read-path rule
    requires for every read, not just the key-mismatch case."""
    vault.set_secret('reddit.password', 'pre-lock-value')
    vault.set_passphrase('correct horse battery staple')
    _relock(vault)
    with pytest.raises(vault.SecretsUnavailable):
        vault.get_secret_value('reddit.password', consumer='test')


def test_unlock_with_correct_passphrase_restores_read_access(vault):
    vault.set_secret('reddit.password', 'pre-lock-value')
    vault.set_passphrase('correct horse battery staple')
    _relock(vault)
    vault.unlock_with_passphrase('correct horse battery staple')
    assert vault.lock_state() == 'unlocked'
    assert vault.get_secret_value('reddit.password', consumer='test') == 'pre-lock-value'


def test_unlock_with_wrong_passphrase_rejected_via_fingerprint(vault):
    """The fingerprint check is what turns 'garbage bytes that happen to
    pass AES-GCM's tag check under a coincidentally-matching KEK' (astronomically
    unlikely, but so is every other crypto failure mode this module guards)
    into a deterministic reject — the wrap/unwrap round trip alone isn't
    what's being asserted here, the fingerprint compare is."""
    vault.set_secret('reddit.password', 'pre-lock-value')
    vault.set_passphrase('correct horse battery staple')
    _relock(vault)
    with pytest.raises(vault.SecretDenied):
        vault.unlock_with_passphrase('wrong passphrase entirely')
    assert vault.lock_state() == 'locked'


def test_unlock_with_recovery_key_works(vault):
    vault.set_secret('reddit.password', 'pre-lock-value')
    recovery_key = vault.set_passphrase('correct horse battery staple')
    _relock(vault)
    vault.unlock_with_recovery_key(recovery_key)
    assert vault.lock_state() == 'unlocked'
    assert vault.get_secret_value('reddit.password', consumer='test') == 'pre-lock-value'


def test_unlock_with_recovery_key_tolerates_dashes_case_and_whitespace(vault):
    vault.set_secret('reddit.password', 'pre-lock-value')
    recovery_key = vault.set_passphrase('correct horse battery staple')
    _relock(vault)
    messy = '  ' + recovery_key.lower().replace('-', ' ') + '  '
    vault.unlock_with_recovery_key(messy)
    assert vault.lock_state() == 'unlocked'


def test_unlock_with_wrong_recovery_key_rejected(vault):
    vault.set_secret('reddit.password', 'pre-lock-value')
    vault.set_passphrase('correct horse battery staple')
    _relock(vault)
    with pytest.raises(vault.SecretDenied):
        vault.unlock_with_recovery_key('AAAA-BBBB-CCCC-DDDD-EEEE')
    assert vault.lock_state() == 'locked'


def test_change_passphrase_requires_correct_old_passphrase(vault):
    vault.set_passphrase('correct horse battery staple')
    with pytest.raises(vault.SecretDenied):
        vault.change_passphrase('wrong old passphrase', 'a brand new passphrase')


def test_change_passphrase_rotates_without_touching_recovery_leg(vault):
    vault.set_secret('reddit.password', 'pre-lock-value')
    recovery_key = vault.set_passphrase('correct horse battery staple')
    vault.change_passphrase('correct horse battery staple', 'a brand new passphrase')
    _relock(vault)
    # Old passphrase no longer works.
    with pytest.raises(vault.SecretDenied):
        vault.unlock_with_passphrase('correct horse battery staple')
    # New passphrase does.
    vault.unlock_with_passphrase('a brand new passphrase')
    assert vault.get_secret_value('reddit.password', consumer='test') == 'pre-lock-value'
    _relock(vault)
    # The recovery key from set_passphrase-time is untouched by the rotation.
    vault.unlock_with_recovery_key(recovery_key)
    assert vault.lock_state() == 'unlocked'


def test_notification_fires_once_per_lock_period(vault, monkeypatch):
    """A burst of jobs hitting a locked vault must produce ONE notification,
    not one per job — see `_lock_notified`."""
    vault.set_passphrase('correct horse battery staple')
    _relock(vault)
    calls = []
    monkeypatch.setattr(
        'mc.blueprints.push_mobile._notify_push',
        lambda title, body, **kw: calls.append((title, body)))
    for _ in range(3):
        with pytest.raises(vault.VaultLocked):
            vault.load_master_key()
    assert len(calls) == 1

    # A fresh lock period (after an unlock) can notify again.
    vault.unlock_with_passphrase('correct horse battery staple')
    _relock(vault)
    with pytest.raises(vault.VaultLocked):
        vault.load_master_key()
    assert len(calls) == 2


# ── Idle auto-lock + manual 'Lock now' (MC-949 follow-up, Ron 2026-09-24) ────
#
# `_monotonic()` is the injectable-clock seam (mc.secrets_store._monotonic) —
# every test here patches it to a plain list-backed counter instead of
# sleeping real seconds.

def test_idle_expiry_relocks_and_raises_vault_locked(vault, monkeypatch):
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'vault_idle_lock_minutes', 10)
    clock = [0.0]
    monkeypatch.setattr(vault, '_monotonic', lambda: clock[0])
    vault.set_secret('reddit.password', 'pre-lock-value')
    vault.set_passphrase('correct horse battery staple')
    assert vault.lock_state() == 'unlocked'

    clock[0] += 10 * 60  # exactly at the configured threshold
    with pytest.raises(vault.VaultLocked):
        vault.load_master_key()
    assert vault.lock_state() == 'locked'
    with pytest.raises(vault.SecretsUnavailable):
        vault.get_secret_value('reddit.password', consumer='test')


def test_read_inside_the_idle_window_extends_it(vault, monkeypatch):
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'vault_idle_lock_minutes', 10)
    clock = [0.0]
    monkeypatch.setattr(vault, '_monotonic', lambda: clock[0])
    vault.set_secret('reddit.password', 'pre-lock-value')
    vault.set_passphrase('correct horse battery staple')

    clock[0] += 9 * 60  # inside the window — a read here restarts the clock
    assert vault.get_secret_value('reddit.password', consumer='test') == 'pre-lock-value'
    clock[0] += 9 * 60  # 18 min since unlock, but only 9 since the last read
    assert vault.get_secret_value('reddit.password', consumer='test') == 'pre-lock-value'
    assert vault.lock_state() == 'unlocked'


def test_idle_lock_minutes_zero_disables_auto_lock(vault, monkeypatch):
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'vault_idle_lock_minutes', 0)
    clock = [0.0]
    monkeypatch.setattr(vault, '_monotonic', lambda: clock[0])
    vault.set_passphrase('correct horse battery staple')

    clock[0] += 10_000 * 60  # absurdly idle
    assert vault.lock_state() == 'unlocked'
    vault.load_master_key()  # must not raise


def test_check_idle_lock_relocks_even_without_a_read(vault, monkeypatch):
    """The sweeper calls check_idle_lock() directly, not load_master_key() —
    it must clear the key on its own, since its whole point is catching the
    case where nothing ever reads again to trigger the lazy check."""
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'vault_idle_lock_minutes', 5)
    clock = [0.0]
    monkeypatch.setattr(vault, '_monotonic', lambda: clock[0])
    vault.set_passphrase('correct horse battery staple')

    clock[0] += 5 * 60
    assert vault.check_idle_lock() is True
    assert vault.lock_state() == 'locked'
    # A second sweep with nothing unlocked is a no-op, not an error.
    assert vault.check_idle_lock() is False


def test_lock_now_clears_the_key_immediately(vault):
    vault.set_secret('reddit.password', 'pre-lock-value')
    vault.set_passphrase('correct horse battery staple')
    assert vault.lock_state() == 'unlocked'
    vault.lock_now()
    assert vault.lock_state() == 'locked'
    with pytest.raises(vault.VaultLocked):
        vault.load_master_key()


def test_lock_now_is_a_no_op_when_already_locked(vault):
    assert vault.lock_state() == 'unconfigured'
    vault.lock_now()  # must not raise
    assert vault.lock_state() == 'unconfigured'


def test_notification_fires_once_after_idle_relock(vault, monkeypatch):
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'vault_idle_lock_minutes', 10)
    clock = [0.0]
    monkeypatch.setattr(vault, '_monotonic', lambda: clock[0])
    vault.set_passphrase('correct horse battery staple')
    calls = []
    monkeypatch.setattr(
        'mc.blueprints.push_mobile._notify_push',
        lambda title, body, **kw: calls.append((title, body)))

    clock[0] += 10 * 60
    for _ in range(3):
        with pytest.raises(vault.VaultLocked):
            vault.load_master_key()
    assert len(calls) == 1

    # A fresh unlock + a fresh idle period can notify again.
    vault.unlock_with_passphrase('correct horse battery staple')
    clock[0] += 10 * 60
    with pytest.raises(vault.VaultLocked):
        vault.load_master_key()
    assert len(calls) == 2


class TestTamperEvidence:
    """Wren's finding on MC 503edfe4: a hijacker who steals the dashboard
    passcode (see the local-auth fix above) and reaches vault-lock/set or
    /change would otherwise own the vault silently. Every set / change /
    recovery-key-unlock must be visible to Ron even if he never opens the
    dashboard — unlike `_notify_vault_locked`'s once-per-period throttle,
    this one is NOT deduped: each of the three events fires its own
    notification and audit line, every time."""

    def test_set_passphrase_notifies_and_audits_caller_addr(self, vault, monkeypatch):
        calls = []
        monkeypatch.setattr(
            'mc.blueprints.push_mobile._notify_push',
            lambda title, body, **kw: calls.append((title, body)))
        vault.set_passphrase('correct horse battery staple', caller_addr='10.0.0.7')
        assert len(calls) == 1
        title, body = calls[0]
        assert 'passphrase' in title.lower()
        assert '10.0.0.7' in body
        rec = vault.audit_tail(1)[0]
        assert rec['event'] == 'vault_passphrase_set'
        assert rec['caller_addr'] == '10.0.0.7'

    def test_change_passphrase_notifies_and_audits_caller_addr(self, vault, monkeypatch):
        vault.set_passphrase('correct horse battery staple')
        calls = []
        monkeypatch.setattr(
            'mc.blueprints.push_mobile._notify_push',
            lambda title, body, **kw: calls.append((title, body)))
        vault.change_passphrase('correct horse battery staple', 'a brand new passphrase',
                                caller_addr='10.0.0.7')
        assert len(calls) == 1
        assert '10.0.0.7' in calls[0][1]
        rec = vault.audit_tail(1)[0]
        assert rec['event'] == 'vault_passphrase_changed'
        assert rec['caller_addr'] == '10.0.0.7'

    def test_unlock_with_recovery_key_notifies_and_audits_caller_addr(self, vault, monkeypatch):
        recovery_key = vault.set_passphrase('correct horse battery staple')
        _relock(vault)
        calls = []
        monkeypatch.setattr(
            'mc.blueprints.push_mobile._notify_push',
            lambda title, body, **kw: calls.append((title, body)))
        vault.unlock_with_recovery_key(recovery_key, caller_addr='10.0.0.7')
        assert len(calls) == 1
        assert '10.0.0.7' in calls[0][1]
        rec = vault.audit_tail(1)[0]
        assert rec['event'] == 'vault_unlocked'
        assert rec['method'] == 'recovery_key'
        assert rec['caller_addr'] == '10.0.0.7'

    def test_routine_passphrase_unlock_does_not_notify(self, vault, monkeypatch):
        """A normal daily unlock is not tamper evidence — must not spam Ron
        on every restart-then-unlock cycle."""
        vault.set_passphrase('correct horse battery staple')
        _relock(vault)
        calls = []
        monkeypatch.setattr(
            'mc.blueprints.push_mobile._notify_push',
            lambda title, body, **kw: calls.append((title, body)))
        vault.unlock_with_passphrase('correct horse battery staple')
        assert calls == []

    def test_notification_failure_does_not_break_the_underlying_action(self, vault, monkeypatch):
        """Best-effort: a broken notifier must not turn a legitimate
        set/change/unlock into an error for the human doing it."""
        monkeypatch.setattr(
            'mc.blueprints.push_mobile._notify_push',
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('boom')))
        recovery_key = vault.set_passphrase('correct horse battery staple',
                                            caller_addr='10.0.0.7')
        assert vault.lock_state() == 'unlocked'
        assert recovery_key


def test_unconfigured_vault_behaves_exactly_as_before(vault):
    """The whole point of gating on `wrapped_key_path().is_file()` is that a
    box which has never called set_passphrase must be unaffected — this is
    the regression guard for that promise."""
    vault.set_secret('reddit.password', 'never-locked')
    assert vault.lock_state() == 'unconfigured'
    assert vault.get_secret_value('reddit.password', consumer='test') == 'never-locked'


# ── icacls retry-once (Dave's review, MC 503edfe4, blocker 4) ──────────────
# A transient icacls hiccup during set_passphrase/change_passphrase used to
# strand Ron mid-setup with no recourse but to start over. One retry gives a
# transient failure a second chance without weakening the fail-closed
# guarantee on a REAL failure (both attempts denied/erroring).

def test_icacls_grant_and_verify_retries_once_after_a_transient_failure(monkeypatch, tmp_path):
    from mc import secrets_store
    calls = []

    def fake_once(path, sid, account):
        calls.append(1)
        if len(calls) == 1:
            return False, "icacls exited 1332: some element(s) could not be translated"
        return True, ''

    monkeypatch.setattr(secrets_store, '_icacls_grant_and_verify_once', fake_once)
    ok, detail = secrets_store._icacls_grant_and_verify(tmp_path / 'x', 'S-1-5-21-fake', 'user')
    assert ok is True
    assert detail == ''
    assert len(calls) == 2


def test_icacls_grant_and_verify_still_fails_closed_after_two_failures(monkeypatch, tmp_path):
    from mc import secrets_store
    calls = []

    def fake_once(path, sid, account):
        calls.append(1)
        return False, f"attempt {len(calls)} denied"

    monkeypatch.setattr(secrets_store, '_icacls_grant_and_verify_once', fake_once)
    ok, detail = secrets_store._icacls_grant_and_verify(tmp_path / 'x', 'S-1-5-21-fake', 'user')
    assert ok is False
    assert len(calls) == 2
    assert 'attempt 1 denied' in detail
    assert 'attempt 2 denied' in detail
