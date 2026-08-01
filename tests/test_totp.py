"""TOTP — mc/totp.py, and its integration with the vault.

The correctness anchor is the RFC 6238 reference vectors: if we agree with
those, we agree with Google Authenticator, because both are implementations of
the same spec. Nothing here talks to Google.
"""

import base64
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mc import totp  # noqa: E402

# RFC 6238 Appendix B — seed "12345678901234567890", SHA1, 8 digits.
RFC_SEED = base64.b32encode(b'12345678901234567890').decode('ascii')
RFC_VECTORS = [
    (59, '94287082'),
    (1111111109, '07081804'),
    (1111111111, '14050471'),
    (1234567890, '89005924'),
    (2000000000, '69279037'),
    (20000000000, '65353130'),
]


@pytest.mark.parametrize('at,expected', RFC_VECTORS)
def test_rfc6238_reference_vectors(at, expected):
    assert totp.generate(RFC_SEED, digits=8, algorithm='SHA1', at=at) == expected


def test_six_digit_codes_are_the_last_six_of_the_eight(at=1111111109):
    eight = totp.generate(RFC_SEED, digits=8, at=at)
    six = totp.generate(RFC_SEED, digits=6, at=at)
    assert six == eight[-6:]


def test_code_is_stable_within_a_window_and_changes_across_it():
    # t=1000 sits in window 33, which spans 990–1019 inclusive.
    a = totp.generate(RFC_SEED, at=1000)
    assert totp.generate(RFC_SEED, at=1019) == a
    assert totp.generate(RFC_SEED, at=1020) != a


def test_seconds_remaining():
    assert totp.seconds_remaining(30, at=1000) == 20
    assert totp.seconds_remaining(30, at=1019) == 1
    # Sub-second remainders round UP: a code with 0.3s left is still live, and
    # reporting 0 would read as "expired".
    assert totp.seconds_remaining(30, at=1019.7) == 1
    # Exactly on a boundary, the whole window is ahead.
    assert totp.seconds_remaining(30, at=1020) == 30


# ── Seed handling ────────────────────────────────────────────────────────────

def test_seeds_are_accepted_the_way_sites_print_them():
    # Grouped, lowercase, padded — all the same secret.
    canonical = totp.normalize_secret('JBSWY3DPEHPK3PXP')
    for variant in ('jbsw y3dp ehpk 3pxp', 'JBSWY3DP-EHPK3PXP', 'JBSWY3DPEHPK3PXP='):
        assert totp.normalize_secret(variant) == canonical


@pytest.mark.parametrize('bad', ['', '   ', 'not-base32!', 'ABC189'])
def test_invalid_seeds_rejected(bad):
    with pytest.raises(totp.TotpError):
        totp.normalize_secret(bad)


# ── otpauth:// URIs ──────────────────────────────────────────────────────────

def test_parse_otpauth_uri():
    got = totp.parse_otpauth_uri(
        'otpauth://totp/GitHub:ron%40example.com?secret=JBSWY3DPEHPK3PXP'
        '&issuer=GitHub&digits=6&period=30&algorithm=SHA1')
    assert got['secret'] == 'JBSWY3DPEHPK3PXP'
    assert got['issuer'] == 'GitHub'
    assert got['account'] == 'ron@example.com'
    assert got['digits'] == 6 and got['period'] == 30


def test_parse_otpauth_uri_infers_issuer_from_the_label():
    got = totp.parse_otpauth_uri('otpauth://totp/Reddit:ron?secret=JBSWY3DPEHPK3PXP')
    assert got['issuer'] == 'Reddit' and got['account'] == 'ron'


def test_hotp_uri_rejected():
    # Counter-based: handing out codes without tracking the counter would
    # desynchronise the account.
    with pytest.raises(totp.TotpError):
        totp.parse_otpauth_uri('otpauth://hotp/x?secret=JBSWY3DPEHPK3PXP&counter=1')


def test_otpauth_without_secret_rejected():
    with pytest.raises(totp.TotpError):
        totp.parse_otpauth_uri('otpauth://totp/GitHub:ron?issuer=GitHub')


# ── Google Authenticator export ──────────────────────────────────────────────

def _protobuf_field(num, wire, payload: bytes) -> bytes:
    return bytes([(num << 3) | wire]) + payload


def _len_delim(num, raw: bytes) -> bytes:
    return _protobuf_field(num, 2, bytes([len(raw)]) + raw)


def _varint_field(num, value: int) -> bytes:
    return _protobuf_field(num, 0, bytes([value]))


def _make_migration_uri(accounts) -> str:
    """Build a payload in Google Authenticator's own export format."""
    blob = b''
    for secret_bytes, name, issuer, otp_type in accounts:
        entry = (_len_delim(1, secret_bytes)
                 + _len_delim(2, name.encode())
                 + _len_delim(3, issuer.encode())
                 + _varint_field(4, 1)          # SHA1
                 + _varint_field(5, 1)          # six digits
                 + _varint_field(6, otp_type))  # 1=HOTP 2=TOTP
        blob += _len_delim(1, entry)
    blob += _varint_field(2, 1)                 # version
    return 'otpauth-migration://offline?data=' + quote(
        base64.b64encode(blob).decode('ascii'))


def test_parse_migration_uri_round_trip():
    uri = _make_migration_uri([
        (b'12345678901234567890', 'ron@example.com', 'GitHub', 2),
        (b'09876543210987654321', 'ron', 'Reddit', 2),
    ])
    entries = totp.parse_migration_uri(uri)
    assert [e['issuer'] for e in entries] == ['GitHub', 'Reddit']
    assert [e['account'] for e in entries] == ['ron@example.com', 'ron']
    # The decoded seed must actually produce the RFC vectors.
    assert totp.generate(entries[0]['secret'], digits=8, at=59) == '94287082'


def test_migration_skips_hotp_entries():
    uri = _make_migration_uri([
        (b'12345678901234567890', 'ron', 'GitHub', 2),
        (b'12345678901234567890', 'ron', 'Counter', 1),   # HOTP
    ])
    entries = totp.parse_migration_uri(uri)
    assert [e['issuer'] for e in entries] == ['GitHub']


def test_migration_with_no_totp_accounts_raises():
    uri = _make_migration_uri([(b'12345678901234567890', 'ron', 'Counter', 1)])
    with pytest.raises(totp.TotpError):
        totp.parse_migration_uri(uri)


@pytest.mark.parametrize('bad', [
    'otpauth://totp/x?secret=JBSWY3DPEHPK3PXP',       # not a migration URI
    'otpauth-migration://offline',                     # no data
    'otpauth-migration://offline?data=!!!notbase64!!',
])
def test_bad_migration_uris_raise(bad):
    with pytest.raises(totp.TotpError):
        totp.parse_migration_uri(bad)


def test_suggested_name():
    assert totp.suggested_name({'issuer': 'GitHub', 'account': 'ron@example.com'}) \
        == 'github.ron-example-com.totp'


# ── Vault integration ────────────────────────────────────────────────────────

@pytest.fixture()
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
    from mc import secrets_store
    secrets_store._dispensed.clear()
    return secrets_store


def test_pasting_an_otpauth_uri_creates_a_totp_secret(vault):
    rec = vault.set_secret(
        'github.totp',
        'otpauth://totp/GitHub:ron?secret=JBSWY3DPEHPK3PXP&issuer=GitHub')
    assert rec['kind'] == 'totp'
    assert rec['issuer'] == 'GitHub' and rec['account'] == 'ron'
    assert rec['placeholder'] == '{{totp:github.totp}}'
    code, remaining = vault.generate_totp_code('github.totp', consumer='test')
    assert len(code) == 6 and code.isdigit()
    assert 1 <= remaining <= 30


def test_bare_base32_seed_with_explicit_kind(vault):
    vault.set_secret('x.totp', 'jbsw y3dp ehpk 3pxp', kind='totp')
    code, _ = vault.generate_totp_code('x.totp', consumer='test')
    assert len(code) == 6


def test_bad_seed_is_rejected_at_save_not_at_login(vault):
    with pytest.raises(vault.SecretsError):
        vault.set_secret('x.totp', 'not base32 at all!', kind='totp')


def test_totp_placeholder_resolves_to_a_code_not_the_seed(vault):
    vault.set_secret('gh.totp', 'otpauth://totp/GitHub:ron?secret=' + RFC_SEED)
    out, used = vault.resolve_placeholders('--otp {{totp:gh.totp}}', consumer='t')
    assert used == ['gh.totp']
    code = out.split()[-1]
    assert len(code) == 6 and code.isdigit()
    assert RFC_SEED not in out


def test_repeated_totp_placeholder_yields_one_consistent_code(vault):
    vault.set_secret('gh.totp', RFC_SEED, kind='totp')
    out, _ = vault.resolve_placeholders('{{totp:gh.totp}} {{totp:gh.totp}}',
                                        consumer='t')
    a, b = out.split()
    assert a == b
    # …and it counted as a single use, not one per occurrence.
    assert vault.list_secrets()[0]['use_count'] == 1


def test_the_seed_is_never_left_in_the_redaction_table(vault):
    """A TOTP seed must not be registered as a dispensed value — it should
    never appear in output at all, and holding it there is a needless copy."""
    vault.set_secret('gh.totp', RFC_SEED, kind='totp')
    vault.generate_totp_code('gh.totp', consumer='t')
    assert vault.redact('seed is ' + RFC_SEED) == 'seed is ' + RFC_SEED


def test_secret_placeholder_on_a_totp_entry_still_returns_the_seed(vault):
    """{{secret:x}} is the escape hatch for tooling that wants the seed
    itself (e.g. re-enrolling elsewhere) — it must not silently become a code."""
    vault.set_secret('gh.totp', RFC_SEED, kind='totp')
    out, _ = vault.resolve_placeholders('{{secret:gh.totp}}', consumer='t')
    assert out == RFC_SEED


def test_totp_placeholder_on_a_password_rejects(vault):
    vault.set_secret('a.password', 'plain-value')
    with pytest.raises(vault.SecretsError):
        vault.generate_totp_code('a.password', consumer='t')


def test_totp_respects_unattended_policy(vault):
    vault.set_secret('bank.totp', RFC_SEED, kind='totp', allow_unattended=False)
    with pytest.raises(vault.SecretDenied):
        vault.generate_totp_code('bank.totp', consumer='steward', unattended=True)


def test_metadata_edit_preserves_totp_params(vault):
    """Re-saving with different digits/period defaults must not silently start
    producing wrong codes."""
    vault.set_secret(
        'x.totp',
        'otpauth://totp/Acme:ron?secret=' + RFC_SEED + '&digits=8&period=60')
    before = vault.list_secrets()[0]
    assert before['digits'] == 8 and before['period'] == 60
    vault.set_secret('x.totp', vault.get_secret_value('x.totp', consumer='t'),
                     kind='totp', description='renamed')
    after = vault.list_secrets()[0]
    assert after['digits'] == 8 and after['period'] == 60
