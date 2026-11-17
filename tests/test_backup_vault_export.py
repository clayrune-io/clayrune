"""P26 (MC-1179 pass 07): the vault half of a project export.

`export_all_for_backup` opens each entry with `_open` directly, which skips
`get_secret_value`'s `oauth.*` deny and its per-read audit line. The export
loop now refuses server-internal entries itself and writes one audit line per
entry it exports. The route-level passcode gate is in
tests/test_backup_unattended_gate.py.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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
    secrets_store._last_key_use = None
    return secrets_store


def _decrypt(vault, blob: bytes, passphrase: str) -> dict:
    outer = json.loads(blob.decode('utf-8'))
    key = vault._scrypt_key(passphrase, base64.b64decode(outer['salt']))
    return json.loads(vault._aesgcm(key).decrypt(
        base64.b64decode(outer['nonce']), base64.b64decode(outer['ciphertext']),
        vault._EXPORT_AAD))


def test_export_refuses_server_internal_entries(vault):
    vault.set_secret('proj.token', 'PLAIN-ONE', scope='proj_x')
    vault.set_secret('oauth.x', '{"access_token":"ACCESS-SENTINEL"}', scope='proj_x')
    blob = vault.export_all_for_backup('pw-1', consumer='backup-export', scope_filter='proj_x')
    names = [e['name'] for e in _decrypt(vault, blob, 'pw-1')['entries']]
    assert names == ['proj.token']
    assert b'ACCESS-SENTINEL' not in blob


def test_export_writes_one_audit_line_per_exported_entry(vault):
    vault.set_secret('proj.a', 'A-VALUE', scope='proj_x')
    vault.set_secret('proj.b', 'B-VALUE', scope='proj_x')
    vault.set_secret('other.c', 'C-VALUE', scope='someone_else')
    vault.export_all_for_backup('pw-1', consumer='backup-export', scope_filter='proj_x')
    rows = [r for r in vault.audit_tail(50) if r.get('event') == 'export_entry']
    assert sorted(r['name'] for r in rows) == ['proj.a', 'proj.b']
    assert all(r['consumer'] == 'backup-export' and r['scope_filter'] == 'proj_x' for r in rows)
    assert 'A-VALUE' not in json.dumps(rows) and 'B-VALUE' not in json.dumps(rows)
