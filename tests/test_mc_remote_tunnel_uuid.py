"""MC-1166 7d-2e-3, decision E1: the attestation reply's non-secret tunnel_uuid is persisted next to hostname.

An in-memory stand-in replaces the OS keyring; nothing here touches a real keystore, the network or the vault.
"""
import pytest

from mc_remote import attestation, config, device_keys

TUNNEL = '12345678-1234-1234-1234-123456789abc'


@pytest.fixture
def keystore(monkeypatch):
    store = {}
    monkeypatch.setattr(device_keys.keyring, 'set_password', lambda svc, name, value: store.__setitem__(name, value))
    monkeypatch.setattr(device_keys.keyring, 'get_password', lambda svc, name: store.get(name))
    monkeypatch.setattr(device_keys.keyring, 'delete_password', lambda svc, name: store.pop(name, None))
    return store


def enrolled(store):
    for logical, value in (('device_pub', 'cHVi'), ('device_id', 'device1'), ('username', 'ron'),
                           ('hostname', 'ron.example.test'), ('enrollment_token', 'tok')):
        store[config.KEYSTORE_KEYS[logical]] = value


def test_the_tunnel_uuid_round_trips_beside_hostname(keystore):
    assert device_keys.load_tunnel_uuid() is None
    device_keys.store_tunnel_uuid(TUNNEL)
    assert device_keys.load_tunnel_uuid() == TUNNEL
    assert config.KEYSTORE_KEYS['tunnel_uuid'] == 'tunnel_uuid' and keystore['tunnel_uuid'] == TUNNEL


def test_an_install_enrolled_before_this_field_existed_still_loads_as_enrolled(keystore):
    enrolled(keystore)
    identity = device_keys.load_identity()
    assert identity is not None and identity.device_id == 'device1'
    assert device_keys.load_tunnel_uuid() is None          # absent until its next attestation


def test_clearing_the_identity_clears_the_tunnel_uuid_too(keystore):
    enrolled(keystore)
    device_keys.store_tunnel_uuid(TUNNEL)
    device_keys.clear_identity()
    assert 'tunnel_uuid' not in keystore and device_keys.load_tunnel_uuid() is None


@pytest.mark.parametrize('value', [None, '', 5, TUNNEL.upper(), TUNNEL + 'x', ' ' + TUNNEL, 'not-a-uuid',
                                   'PLACEHOLDER_TOKEN_x'])
def test_only_a_well_formed_tunnel_id_is_taken_from_the_reply(value):
    assert attestation._valid_tunnel_uuid(value) is None
    assert attestation._valid_tunnel_uuid(TUNNEL) == TUNNEL


def test_recording_writes_only_a_valid_value_and_never_raises(keystore, monkeypatch):
    attestation._record_tunnel_uuid(None)
    assert keystore == {}
    attestation._record_tunnel_uuid(TUNNEL)
    assert keystore['tunnel_uuid'] == TUNNEL

    def locked(value):
        raise device_keys.KeystoreUnavailable('locked')
    monkeypatch.setattr(device_keys, 'store_tunnel_uuid', locked)
    attestation._record_tunnel_uuid(TUNNEL)                 # a keystore failure must not fail the attestation


def test_the_ok_result_defaults_to_no_tunnel_id():
    fields = {name: None for name in attestation.AttestationOk.__dataclass_fields__ if name != 'tunnel_uuid'}
    assert attestation.AttestationOk(**fields).tunnel_uuid is None
