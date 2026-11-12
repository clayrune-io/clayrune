"""Pull key custody seams only; all identities are temporary test fixtures.

The explicit SQLite commit adapter below models the later lifecycle unit's CAS;
it is not service authentication, enrollment, or a live owner recovery ceremony.
"""
from dataclasses import replace
import base64
import json
import sqlite3
from typing import Any, cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mc import agent_mail_enrollment_keys as keys
from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal, SealReference, b64


MASTER = b'M' * 32
SEED = b'S' * 32
SECRET = b'F' * 32
CURRENT = SealReference('a' * 32, 'b' * 32, 1, 'old-key')
CANDIDATE = SealReference('c' * 32, 'b' * 32, 2, 'new-key')
TRANSCRIPT = b'fixture pull request with exact origin and generation'


@pytest.fixture
def seal(tmp_path):
    return IdentitySeal(tmp_path / 'identity', key=lambda: MASTER)


def decoded(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def verify(public, signature):
    Ed25519PublicKey.from_public_bytes(decoded(public)).verify(decoded(signature), TRANSCRIPT)


def public(seal, ref):
    return seal.public(ref.flow, ref.install, ref.generation, ref.key_id)


def legacy_fixture(seal, *, version=1):
    """Independent pre-pull v1 envelope/AAD, not the production writer."""
    aad = json.dumps(['clayrune-mail-identity', '1', CURRENT.flow, CURRENT.install,
                      '1', CURRENT.key_id], separators=(',', ':')).encode('ascii')
    nonce = b'N' * 12
    value = {'version': version, 'nonce': b64(nonce),
             'ciphertext': b64(AESGCM(MASTER).encrypt(nonce, SEED + SECRET, aad))}
    seal.folder.mkdir()
    path = seal.folder / (CURRENT.flow + '.sealed')
    path.write_text(json.dumps(value))
    return path, value


@pytest.mark.parametrize('version', [1])
def test_supported_version_reads_legacy_and_round_trips(tmp_path, version):
    seal = IdentitySeal(tmp_path / 'identity', key=lambda: MASTER)
    path, _ = legacy_fixture(seal, version=version)
    original = path.read_bytes()
    expected = b64(Ed25519PrivateKey.from_private_bytes(SEED).public_key().public_bytes_raw())
    assert public(seal, CURRENT) == expected
    assert seal.flow_secret(CURRENT.flow, CURRENT.install, 1, CURRENT.key_id) == SECRET
    signature, secret = seal.prove(CURRENT.flow, CURRENT.install, 1, CURRENT.key_id, TRANSCRIPT)
    verify(expected, signature)
    assert secret == SECRET
    assert seal.status(CURRENT) == 'ready'
    verify(expected, seal.sign_pull(CURRENT, TRANSCRIPT))
    # Orphan reuse never rewrites or upgrades the old format.
    assert seal.generate_pull(CURRENT, authorize=lambda: True, expected_public_key=None) == expected
    assert path.read_bytes() == original
    other = replace(CANDIDATE, generation=1)
    new_public = seal.generate_pull(other, authorize=lambda: True, expected_public_key=None)
    reopened = IdentitySeal(seal.folder, key=lambda: MASTER)
    assert public(reopened, other) == new_public
    verify(new_public, reopened.sign_pull(other, TRANSCRIPT))
    assert json.loads((seal.folder / (other.flow + '.sealed')).read_text())['version'] == version


@pytest.mark.parametrize('version', [0, 2, 999])
def test_unknown_version_fails_closed_without_conversion(seal, version):
    path, _ = legacy_fixture(seal, version=version)
    original = path.read_bytes()
    assert seal.status(CURRENT) == 'version_unsupported'
    with pytest.raises(EnrollmentRefused, match='^version_unsupported$'):
        seal.generate_pull(CURRENT, authorize=lambda: True, expected_public_key=None)
    with pytest.raises(EnrollmentRefused, match='^identity_unavailable_reenrollment_required$'):
        public(seal, CURRENT)
    assert path.read_bytes() == original


@pytest.mark.parametrize('allowed', [False, None, 1, 'true'])
def test_generation_requires_literal_authorization_and_touches_nothing(seal, allowed):
    with pytest.raises(EnrollmentRefused, match='^human_required$'):
        seal.generate_pull(CURRENT, authorize=lambda: allowed, expected_public_key=None)
    assert not seal.folder.exists()


def test_authorization_exception_is_observable_and_redacted(seal):
    def fail():
        raise OSError('fixture private passcode or adapter detail')
    with pytest.raises(EnrollmentRefused, match='^human_required$'):
        seal.generate_pull(CURRENT, authorize=fail, expected_public_key=None)
    assert not seal.folder.exists()


def test_vault_lock_is_distinct_and_does_not_cache_signing_material(seal):
    value = seal.generate_pull(CURRENT, authorize=lambda: True, expected_public_key=None)
    vault: dict[str, bytes | None] = {'key': MASTER}
    reopened = IdentitySeal(seal.folder, key=lambda: vault['key'])
    verify(value, reopened.sign_pull(CURRENT, TRANSCRIPT))
    vault['key'] = None
    assert reopened.status(CURRENT) == 'vault_locked'
    for operation in (lambda: reopened.sign_pull(CURRENT, TRANSCRIPT),
                      lambda: reopened.generate_pull(CANDIDATE, authorize=lambda: True, expected_public_key=None),
                      lambda: reopened.prepare_rotation(CURRENT, CANDIDATE, authorize=lambda: True, expected_public_key=None)):
        with pytest.raises(EnrollmentRefused, match='^vault_locked$'):
            operation()
    assert len(list(seal.folder.glob('*.sealed'))) == 1
    vault['key'] = MASTER
    verify(value, reopened.sign_pull(CURRENT, TRANSCRIPT))


def test_initial_lock_no_material_no_folder(tmp_path, monkeypatch):
    seal = IdentitySeal(tmp_path / 'identity', key=lambda: None)
    def no_random(*args):
        pytest.fail('locked vault must not generate identity material')
    monkeypatch.setattr(keys.os, 'urandom', no_random)
    assert seal.status(CURRENT) == 'vault_locked'
    with pytest.raises(EnrollmentRefused, match='^vault_locked$'):
        seal.generate_pull(CURRENT, authorize=lambda: True, expected_public_key=None)
    assert not seal.folder.exists()


@pytest.mark.parametrize('damage', ['missing', 'empty', 'json', 'unicode', 'nonce', 'ciphertext',
                                    'tag', 'extra', 'duplicate', 'bool-version', 'padded', 'garbage'])
def test_lost_or_corrupt_seal_is_held_and_never_rekeyed(seal, damage):
    path, value = legacy_fixture(seal)
    if damage == 'missing':
        path.unlink()
    elif damage in ('empty', 'json', 'unicode'):
        path.write_bytes({'empty': b'', 'json': b'{', 'unicode': b'\xff'}[damage])
    elif damage == 'duplicate':
        path.write_text(json.dumps(value)[:-1] + ',"version":1}')
    else:
        if damage == 'tag':
            value['ciphertext'] = b64(b'!' * 80)
        elif damage == 'bool-version':
            value['version'] = True
        elif damage == 'extra':
            value['seed'] = 'forbidden'
        elif damage == 'padded':
            value['ciphertext'] += '='
        elif damage == 'garbage':
            value['nonce'] += '!!'
        else:
            value[damage] = b64(b'x')
        path.write_text(json.dumps(value))
    expected = 'seal_missing' if damage == 'missing' else 'identity_unavailable_reenrollment_required'
    assert seal.status(CURRENT) == expected
    for operation in (lambda: seal.sign_pull(CURRENT, TRANSCRIPT),
                      lambda: seal.prepare_rotation(CURRENT, CANDIDATE, authorize=lambda: True, expected_public_key=None)):
        with pytest.raises(EnrollmentRefused, match='^' + expected + '$'):
            operation()
    assert not (seal.folder / (CANDIDATE.flow + '.sealed')).exists()
    assert list(seal.folder.glob('*.sealed')) == ([] if damage == 'missing' else [path])


@pytest.mark.parametrize('binding', [replace(CURRENT, flow='d' * 32),
                                     replace(CURRENT, install='d' * 32),
                                     replace(CURRENT, generation=2),
                                     replace(CURRENT, key_id='different')])
def test_aad_is_not_relabelable(seal, binding):
    path, _ = legacy_fixture(seal)
    if binding.flow != CURRENT.flow:
        (seal.folder / (binding.flow + '.sealed')).write_bytes(path.read_bytes())
    assert seal.status(binding) == 'identity_unavailable_reenrollment_required'


def test_replaced_or_invalid_master_key_never_recovers_silently(seal):
    legacy_fixture(seal)
    assert IdentitySeal(seal.folder, key=lambda: b'Z' * 32).status(CURRENT) == 'identity_unavailable_reenrollment_required'
    assert IdentitySeal(seal.folder, key=lambda: b'short').status(CURRENT) == 'vault_key_invalid'


def test_storage_failure_is_distinct_and_redacted(seal, monkeypatch):
    def fail(*args, **kwargs):
        raise PermissionError('fixture private path')
    monkeypatch.setattr(keys, 'read_text_with_retry', fail)
    assert seal.status(CURRENT) == 'storage_unavailable'
    with pytest.raises(EnrollmentRefused, match='^storage_unavailable$'):
        seal.sign_pull(CURRENT, TRANSCRIPT)


def test_rotation_stages_then_explicit_commit_retires_old_authority(seal):
    original = seal.generate_pull(CURRENT, authorize=lambda: True, expected_public_key=None)
    rotation = seal.prepare_rotation(CURRENT, CANDIDATE, authorize=lambda: True, expected_public_key=None)
    assert rotation.current_public_key == original
    assert rotation.candidate_public_key != original
    # Later lifecycle unit owns this durable atomic current/fenced projection.
    db = sqlite3.connect(':memory:')
    try:
        db.executescript("CREATE TABLE generations(g INTEGER PRIMARY KEY, state TEXT);"
                         "INSERT INTO generations VALUES(1,'current');"
                         "INSERT INTO generations VALUES(2,'held');")
        assert db.execute("SELECT g FROM generations WHERE state='current'").fetchone() == (1,)
        verify(original, seal.sign_pull(CURRENT, TRANSCRIPT))
        verify(rotation.candidate_public_key, seal.sign_pull(CANDIDATE, TRANSCRIPT))
        def commit(candidate):
            assert candidate == rotation
            with db:
                assert db.execute("UPDATE generations SET state='fenced' WHERE g=1 AND state='current'").rowcount == 1
                db.execute("UPDATE generations SET state='current' WHERE g=2 AND state='held'")
            return True
        seal.commit_rotation(rotation, commit=commit)
        assert db.execute('SELECT * FROM generations ORDER BY g').fetchall() == [(1, 'fenced'), (2, 'current')]
        # Keep immutable old seal for bounded management-flow status proof (6.1).
        assert seal.status(CURRENT) == 'ready'
        assert len(list(seal.folder.glob('*.sealed'))) == 2
    finally:
        db.close()


def test_pending_rotation_survives_restart_and_explicit_failure(seal):
    seal.generate_pull(CURRENT, authorize=lambda: True, expected_public_key=None)
    rotation = seal.prepare_rotation(CURRENT, CANDIDATE, authorize=lambda: True, expected_public_key=None)
    snapshots = {p.name: p.read_bytes() for p in seal.folder.glob('*.sealed')}
    reopened = IdentitySeal(seal.folder, key=lambda: MASTER)
    assert reopened.prepare_rotation(CURRENT, CANDIDATE, authorize=lambda: True, expected_public_key=rotation.candidate_public_key) == rotation
    for result in (False, None, 1):
        with pytest.raises(EnrollmentRefused, match='^stale_generation$'):
            reopened.commit_rotation(rotation, commit=lambda _: cast(Any, result))
    def fail(_):
        raise sqlite3.OperationalError('fixture write failure')
    with pytest.raises(sqlite3.OperationalError, match='fixture write failure'):
        reopened.commit_rotation(rotation, commit=fail)
    assert {p.name: p.read_bytes() for p in seal.folder.glob('*.sealed')} == snapshots


@pytest.mark.parametrize('which', ['signup', 'rotation'])
def test_durable_public_key_prevents_silent_regeneration_after_loss(seal, which):
    original = seal.generate_pull(CURRENT, authorize=lambda: True, expected_public_key=None)
    if which == 'signup':
        (seal.folder / (CURRENT.flow + '.sealed')).unlink()
        operation = lambda: seal.generate_pull(CURRENT, authorize=lambda: True, expected_public_key=original)
    else:
        rotation = seal.prepare_rotation(CURRENT, CANDIDATE, authorize=lambda: True, expected_public_key=None)
        (seal.folder / (CANDIDATE.flow + '.sealed')).unlink()
        operation = lambda: seal.prepare_rotation(CURRENT, CANDIDATE, authorize=lambda: True,
                                                  expected_public_key=rotation.candidate_public_key)
    before = {p.name: p.read_bytes() for p in seal.folder.glob('*.sealed')}
    with pytest.raises(EnrollmentRefused, match='^seal_missing$'):
        operation()
    assert {p.name: p.read_bytes() for p in seal.folder.glob('*.sealed')} == before


def test_public_key_conflict_cannot_adopt_an_orphan(seal):
    seal.generate_pull(CURRENT, authorize=lambda: True, expected_public_key=None)
    with pytest.raises(EnrollmentRefused, match='^operation_conflict$'):
        seal.generate_pull(CURRENT, authorize=lambda: True, expected_public_key=b64(b'x' * 32))


def test_commit_transaction_failure_rolls_back_retirement(seal):
    seal.generate_pull(CURRENT, authorize=lambda: True, expected_public_key=None)
    rotation = seal.prepare_rotation(CURRENT, CANDIDATE, authorize=lambda: True, expected_public_key=None)
    db = sqlite3.connect(':memory:')
    try:
        db.executescript("CREATE TABLE generations(g INTEGER PRIMARY KEY, state TEXT);"
                         "INSERT INTO generations VALUES(1,'current');"
                         "INSERT INTO generations VALUES(2,'held');")
        def commit(_):
            with db:
                db.execute("UPDATE generations SET state='fenced' WHERE g=1")
                db.execute("INSERT INTO generations VALUES(2,'current')")  # Real constraint failure.
            return True
        with pytest.raises(sqlite3.IntegrityError):
            seal.commit_rotation(rotation, commit=commit)
        assert db.execute('SELECT * FROM generations ORDER BY g').fetchall() == [(1, 'current'), (2, 'held')]
        assert public(seal, CURRENT) == rotation.current_public_key
        assert public(seal, CANDIDATE) == rotation.candidate_public_key
    finally:
        db.close()


def test_rotation_refuses_same_key_material(seal):
    legacy_fixture(seal)
    seal.create(CANDIDATE.flow, CANDIDATE.install, CANDIDATE.generation, CANDIDATE.key_id,
                lambda: (SEED, b'n' * 32))
    with pytest.raises(EnrollmentRefused, match='^operation_conflict$'):
        seal.prepare_rotation(CURRENT, CANDIDATE, authorize=lambda: True, expected_public_key=None)


@pytest.mark.parametrize('reference', [replace(CURRENT, flow='../invalid'),
                                      replace(CURRENT, install='short'),
                                      replace(CURRENT, generation=True),
                                      replace(CURRENT, generation=0),
                                      replace(CURRENT, generation=1_000_000_000_000_000),
                                      replace(CURRENT, key_id='bad/key')])
def test_invalid_pull_reference_fails_without_creating_files(seal, reference):
    assert seal.status(reference) == 'invalid_request'
    with pytest.raises(EnrollmentRefused, match='^invalid_request$'):
        seal.generate_pull(reference, authorize=lambda: True, expected_public_key=None)
    assert not seal.folder.exists()


@pytest.mark.parametrize('damage', ['lost', 'corrupt', 'locked', 'different-public'])
def test_commit_rechecks_both_seals_and_vault_before_calling_adapter(seal, damage):
    seal.generate_pull(CURRENT, authorize=lambda: True, expected_public_key=None)
    rotation = seal.prepare_rotation(CURRENT, CANDIDATE, authorize=lambda: True, expected_public_key=None)
    if damage == 'lost':
        (seal.folder / (CANDIDATE.flow + '.sealed')).unlink()
    elif damage == 'corrupt':
        (seal.folder / (CURRENT.flow + '.sealed')).write_text('{}')
    elif damage == 'locked':
        seal = IdentitySeal(seal.folder, key=lambda: None)
    else:
        rotation = replace(rotation, candidate_public_key=rotation.current_public_key)
    called = []
    def commit(_):
        called.append(True)
        return True
    with pytest.raises(EnrollmentRefused):
        seal.commit_rotation(rotation, commit=commit)
    assert not called


@pytest.mark.parametrize('candidate', [replace(CANDIDATE, generation=1),
                                      replace(CANDIDATE, generation=3),
                                      replace(CANDIDATE, install='d' * 32),
                                      replace(CANDIDATE, flow=CURRENT.flow),
                                      replace(CANDIDATE, key_id=CURRENT.key_id)])
def test_rotation_cannot_reuse_binding_or_skip_generation(seal, candidate):
    seal.generate_pull(CURRENT, authorize=lambda: True, expected_public_key=None)
    with pytest.raises(EnrollmentRefused, match='^invalid_request$'):
        seal.prepare_rotation(CURRENT, candidate, authorize=lambda: True, expected_public_key=None)
    assert len(list(seal.folder.glob('*.sealed'))) == 1


def test_rotation_requires_human_gate_without_staging_candidate(seal):
    seal.generate_pull(CURRENT, authorize=lambda: True, expected_public_key=None)
    with pytest.raises(EnrollmentRefused, match='^human_required$'):
        seal.prepare_rotation(CURRENT, CANDIDATE, authorize=lambda: False, expected_public_key=None)
    assert len(list(seal.folder.glob('*.sealed'))) == 1


def test_no_seed_or_flow_secret_in_public_results_or_disk(seal):
    path, _ = legacy_fixture(seal)
    rotation = seal.prepare_rotation(CURRENT, CANDIDATE, authorize=lambda: True, expected_public_key=None)
    results = repr((rotation, seal.status(CURRENT), seal.sign_pull(CURRENT, TRANSCRIPT)))
    for secret in (SEED, SECRET, MASTER):
        assert secret.decode() not in results and b64(secret) not in results
        for stored in seal.folder.glob('*.sealed'):
            assert secret not in stored.read_bytes() and b64(secret).encode() not in stored.read_bytes()
    assert path.exists()
