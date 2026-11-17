"""tools/with-secret.py: a multi-line secret is scrubbed line by line, and a child that
cannot start is exit 2, not a traceback (MC-1179 pass 12 TL5, TL7)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

PEM = ("-----BEGIN PRIVATE KEY-----\n"
       "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7\n"
       "q8Zx0Yt3Rk9m2LpQvW5nE1aB4cD6fG8hJ0kL2mN4pR6sT8uV0wX2yZ4\n"
       "-----END PRIVATE KEY-----")


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
    secrets_store.set_secret('svc.pem', PEM, description='', scope='global')
    spec = importlib.util.spec_from_file_location('with_secret_scrub', REPO / 'tools' / 'with-secret.py')
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_multiline_secret_never_reaches_stdout(ws, capfd):
    code = "import os; print(os.environ['PEM'])"
    rc = ws.main(['--env', 'PEM=svc.pem', '--', sys.executable, '-c', code])
    out = capfd.readouterr().out
    assert rc == 0
    assert 'MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7' not in out
    assert 'q8Zx0Yt3Rk9m2LpQvW5nE1aB4cD6fG8hJ0kL2mN4pR6sT8uV0wX2yZ4' not in out
    assert '[redacted:svc.pem]' in out


def test_short_lines_of_a_multiline_value_are_not_needles(ws):
    ws.vault.register_dispensed('svc.pem', PEM)
    # "-----END PRIVATE KEY-----" is 25 chars and is a needle; a 4-char line would not be
    assert ws._scrub('keep } and x\n') == 'keep } and x\n'


def test_single_line_secret_still_scrubbed_whole(ws):
    ws.vault.register_dispensed('svc.tok', 'tok-value-123456')
    assert ws._scrub('a tok-value-123456 b') == 'a [redacted:svc.tok] b'


def test_child_that_cannot_start_is_exit_2(ws, capsys):
    rc = ws.main(['--env', 'PEM=svc.pem', '--', 'definitely-not-a-program-xyz'])
    assert rc == 2
    assert 'cannot start' in capsys.readouterr().err


def test_raw_child_that_cannot_start_is_exit_2(ws, capsys):
    rc = ws.main(['--raw', '--env', 'PEM=svc.pem', '--', 'definitely-not-a-program-xyz'])
    assert rc == 2
