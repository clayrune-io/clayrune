"""Real anonymous-pipe transfer; never starts/restarts a live server."""
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mc import secrets_store as store, state
from mc import vault_restart_handover as handover


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
    monkeypatch.delenv(handover.CHANNEL_ENV, raising=False)
    monkeypatch.setattr(state, 'CONFIG', {'vault_keep_unlocked_on_restart': True,
                                        'vault_idle_lock_minutes': 120})
    monkeypatch.setattr(store, '_unlocked_key', None)
    monkeypatch.setattr(store, '_last_key_use', None)
    monkeypatch.setattr(store, '_lock_notified', False)
    monkeypatch.setattr(store, '_key_mismatch', False)
    monkeypatch.setattr(store, '_retry_quarantine_if_needed', lambda: None)
    monkeypatch.setattr(handover, '_pending_fd', None)
    monkeypatch.setattr(handover, '_capture_error', '')
    monkeypatch.setattr(handover, '_handover_seen', False)
    # Only a synthetic key/fingerprint: no OS keyring, human login or real vault.
    key = os.urandom(32)
    store.wrapped_key_path().write_text(json.dumps({'fingerprint': store._key_fingerprint(key)}))
    return key


CHILD = '''
import base64, json, os, sys
from mc import secrets_store as store, state
from mc import vault_restart_handover as h
h.capture_inherited_channel()
metadata_removed = h.CHANNEL_ENV not in os.environ
noninheritable = h._pending_fd is None or not os.get_inheritable(h._pending_fd)
store._retry_quarantine_if_needed = lambda: None
state.CONFIG = {'vault_keep_unlocked_on_restart': '--disabled' not in sys.argv}
ok = h.consume_inherited_key()
key = store._unlocked_key
sources = json.dumps(dict(os.environ)) + json.dumps(sys.argv)
encodings = [] if key is None else [key.hex(), base64.b64encode(key).decode(), repr(key)]
print('RESULT:' + json.dumps(dict(ok=ok, locked=store.is_locked(),
    metadata_removed=metadata_removed, noninheritable=noninheritable,
    leaked=any(k in sources for k in encodings) or (key is not None and key in sources.encode()),
    idle_reset=store._last_key_use is not None,
    one_use=not h.consume_inherited_key())))
'''


def run_child(*extra):
    proc = handover.spawn_replacement(
        [sys.executable, '-c', CHILD, *extra],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = proc.communicate(timeout=20)
    assert proc.returncode == 0, err
    line = next(line for line in out.splitlines() if line.startswith('RESULT:'))
    return json.loads(line[7:]), out + err


def test_handover_success_and_no_key_in_environ_argv_or_logs(vault):
    store._unlocked_key = vault
    result, logs = run_child()
    assert result == dict(ok=True, locked=False, metadata_removed=True,
                          noninheritable=True, leaked=False, idle_reset=True, one_use=True)
    for encoded in (vault.hex(), base64.b64encode(vault).decode(), repr(vault)):
        assert encoded not in logs
        assert encoded not in store.audit_path().read_text()
    events = [json.loads(line) for line in store.audit_path().read_text().splitlines()]
    assert any(e['event'] == 'vault_unlocked' and e['reason'] == 'restart_handover'
               for e in events)


def test_wrong_fingerprint_stays_locked(vault):
    store._unlocked_key = os.urandom(32)
    result, logs = run_child()
    assert not result['ok'] and result['locked'] and not result['idle_reset']
    assert 'SecretDenied' in logs
    assert not store.audit_path().exists()


@pytest.mark.parametrize('mode', ['parent_locked', 'flag_off', 'child_flag_off', 'idle_expired'])
def test_restart_without_permission_or_unlocked_parent_stays_locked(vault, mode, monkeypatch):
    if mode != 'parent_locked':
        store._unlocked_key = vault
    if mode == 'flag_off':
        state.CONFIG['vault_keep_unlocked_on_restart'] = False
    if mode == 'idle_expired':
        monkeypatch.setattr(store, '_last_key_use', time.monotonic() - 121 * 60)
    result, _ = run_child(*(['--disabled'] if mode == 'child_flag_off' else []))
    assert not result['ok'] and result['locked'] and not result['idle_reset']


def test_fresh_start_has_no_pipe_even_if_parent_unlocked(vault):
    store._unlocked_key = vault
    proc = subprocess.run([sys.executable, '-c', CHILD], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
    assert '"ok": false, "locked": true' in proc.stdout
    assert 'no inherited pipe' in proc.stdout


def test_channel_setup_failure_still_spawns_locked(vault, monkeypatch):
    store._unlocked_key = vault
    original = os.pipe
    def fail_once():
        monkeypatch.setattr(handover.os, 'pipe', original)
        raise OSError('test')
    monkeypatch.setattr(handover.os, 'pipe', fail_once)
    result, _ = run_child()
    assert not result['ok'] and result['locked']


def test_unrelated_inheritable_pipe_does_not_reach_child(vault):
    store._unlocked_key = vault
    read_fd, write_fd = os.pipe()
    try:
        os.set_inheritable(read_fd, True)
        if sys.platform == 'win32':
            import msvcrt
            token = msvcrt.get_osfhandle(read_fd)
            check = '''
import ctypes
from ctypes import wintypes
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.GetFileType.argtypes = [wintypes.HANDLE]
kernel.GetFileType.restype = wintypes.DWORD
assert kernel.GetFileType(int(os.environ['CHECK_UNRELATED_PIPE'])) == 0
'''
        else:
            token = read_fd
            check = '''
try:
    os.fstat(int(os.environ['CHECK_UNRELATED_PIPE']))
except OSError:
    pass
else:
    raise AssertionError('unrelated pipe inherited')
'''
        env = dict(os.environ, CHECK_UNRELATED_PIPE=str(token))
        proc = handover.spawn_replacement([sys.executable, '-c', CHILD + check],
                                         env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = proc.communicate(timeout=20)
        assert proc.returncode == 0, err
        assert '"ok": true' in out
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_spawn_failure_closes_handover_pipe(vault, monkeypatch):
    store._unlocked_key = vault
    original = os.pipe
    descriptors = []
    def capture_pipe():
        pair = original()
        descriptors.extend(pair)
        return pair
    def failed_spawn(*args, **kwargs):
        raise OSError('synthetic spawn failure')
    monkeypatch.setattr(handover.os, 'pipe', capture_pipe)
    monkeypatch.setattr(handover.subprocess, 'Popen', failed_spawn)
    with pytest.raises(OSError, match='synthetic spawn failure'):
        handover.spawn_replacement([sys.executable])
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize('wrapped', [None, '{broken json', '{}'])
def test_missing_or_corrupt_wrapped_file_cannot_unlock(vault, wrapped):
    if wrapped is None:
        store.wrapped_key_path().unlink()
    else:
        store.wrapped_key_path().write_text(wrapped)
    read_fd, write_fd = os.pipe()
    os.write(write_fd, vault)
    os.close(write_fd)
    handover._pending_fd = read_fd
    assert not handover.consume_inherited_key()
    assert store._unlocked_key is None
    assert store.is_locked()
    store._lock_notified = True  # no live push/loopback notification in this probe
    with pytest.raises(store.VaultLocked):
        store.load_master_key()
    assert not store.key_file_path().exists()


@pytest.mark.parametrize('frame', [b'', b'too short', b'x' * 33])
def test_incomplete_or_oversize_frame_fails_closed(vault, monkeypatch, frame):
    read_fd, write_fd = os.pipe()
    os.write(write_fd, frame)
    os.close(write_fd)
    monkeypatch.setattr(handover, '_pending_fd', read_fd)
    assert not handover.consume_inherited_key()
    assert store.is_locked()
    with pytest.raises(OSError):
        os.fstat(read_fd)


def test_stalled_pipe_is_bounded_and_closed(vault, monkeypatch):
    read_fd, write_fd = os.pipe()
    monkeypatch.setattr(handover, '_pending_fd', read_fd)
    monkeypatch.setattr(handover, 'TIMEOUT_SECONDS', 0.03)
    start = time.monotonic()
    try:
        assert not handover.consume_inherited_key()
        assert time.monotonic() - start < 1
        assert store.is_locked()
        with pytest.raises(OSError):
            os.fstat(read_fd)
    finally:
        os.close(write_fd)


@pytest.mark.parametrize('token', ['untrusted-test-token', '-1', '0', '999999999999'])
def test_invalid_metadata_is_removed_and_never_logged(vault, monkeypatch, token):
    logs = []
    monkeypatch.setattr(handover, '_log', logs.append)
    monkeypatch.setenv(handover.CHANNEL_ENV, token)
    assert not handover.consume_inherited_key()
    assert handover.CHANNEL_ENV not in os.environ
    assert all(token not in line for line in logs)
    assert store.is_locked()


def test_only_pipe_is_accepted(vault, monkeypatch, tmp_path):
    with (tmp_path / 'ordinary').open('wb') as file:
        if sys.platform == 'win32':
            import msvcrt
            token = msvcrt.get_osfhandle(file.fileno())
        else:
            token = file.fileno()
        monkeypatch.setenv(handover.CHANNEL_ENV, str(token))
        assert not handover.consume_inherited_key()
        assert store.is_locked()


def test_adopted_unlock_resets_idle_and_notifications(vault, monkeypatch):
    monkeypatch.setattr(store, '_monotonic', lambda: 1234)
    store._last_key_use = 1
    store._lock_notified = store._key_mismatch = True
    monkeypatch.setattr(store, '_retry_quarantine_if_needed',
                        lambda: pytest.fail('restart boot must not access the OS keyring'))
    store._unlock_from_restart(vault)
    assert store._last_key_use == 1234
    assert not store._lock_notified and not store._key_mismatch


@pytest.mark.skipif(sys.platform == 'win32', reason='macOS swap uses a POSIX descriptor')
def test_mac_swap_hands_pipe_to_verified_executable(vault, tmp_path):
    from test_mac_self_update import TestSwapHelper
    harness = TestSwapHelper()
    layout = harness.make_layout(tmp_path / 'swap')
    executable = layout['new'] / 'Contents' / 'MacOS' / 'Clayrune'
    executable.parent.mkdir(parents=True)
    repo = str(Path(__file__).resolve().parents[1])
    executable.write_text('#!' + sys.executable + '\nimport sys\nsys.path.insert(0, '
                          + repr(repo) + ')\n' + CHILD)
    executable.chmod(0o755)
    store._unlocked_key = vault
    result, status = harness.run(layout, pipe_spawn=True)
    assert result.returncode == 0, result.stderr
    assert status['status'] == 'updated'
    # The helper may finish its health stub before Python finishes importing.
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and 'RESULT:' not in layout['log'].read_text():
        time.sleep(0.05)
    logs = layout['log'].read_text()
    assert '"ok": true, "locked": false' in logs
    assert not layout['opened'].exists()  # LaunchServices would discard the pipe.


def test_setting_is_default_on_and_editable():
    import server
    from mc.blueprints.settings_routes import _CONFIG_EDITABLE_KEYS
    assert server._load_config()['vault_keep_unlocked_on_restart'] is True
    assert 'vault_keep_unlocked_on_restart' in _CONFIG_EDITABLE_KEYS
