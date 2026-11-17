"""tools/with-secret.py keeps the child's stderr apart from its stdout and scrubs
both (MC-1179 pass 12 second wave, TL6)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

TOKEN = 'tok-value-1234567890'


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
    secrets_store.set_secret('svc.tok', TOKEN, description='', scope='global')
    spec = importlib.util.spec_from_file_location('with_secret_streams', REPO / 'tools' / 'with-secret.py')
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(ws, code):
    return ws.main(['--env', 'T=svc.tok', '--', sys.executable, '-c', code])


def test_stderr_goes_to_stderr_not_stdout(ws, capfd):
    rc = _run(ws, "import sys; print('out-line'); print('warn-line', file=sys.stderr)")
    cap = capfd.readouterr()
    assert rc == 0
    assert cap.out.strip() == 'out-line'
    assert cap.err.strip() == 'warn-line'


def test_secret_is_redacted_on_both_streams(ws, capfd):
    code = ("import os, sys; t = os.environ['T']; "
            "print('out ' + t); print('err ' + t, file=sys.stderr)")
    rc = _run(ws, code)
    cap = capfd.readouterr()
    assert rc == 0
    assert TOKEN not in cap.out and TOKEN not in cap.err
    assert 'out [redacted:svc.tok]' in cap.out
    assert 'err [redacted:svc.tok]' in cap.err


def test_exit_code_is_the_childs_with_output_on_both_streams(ws, capfd):
    rc = _run(ws, "import sys; print('a'); print('b', file=sys.stderr); sys.exit(3)")
    cap = capfd.readouterr()
    assert rc == 3
    assert 'a' in cap.out and 'b' in cap.err


def test_a_chatty_stderr_does_not_deadlock_the_child(ws, capfd):
    code = ("import sys\n"
            "for i in range(20000): print('e' * 60, file=sys.stderr)\n"
            "print('done')")
    assert _run(ws, code) == 0
    cap = capfd.readouterr()
    assert cap.out.strip() == 'done'
    assert cap.err.count('\n') == 20000
