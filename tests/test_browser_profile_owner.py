"""Sandbox-only storage and read-only profile-owner refusal regressions (MC-1172)."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from mc import browser_profile_owner as owner
from mc.blueprints import browser_routes as br


@pytest.mark.parametrize('root', ['browser_profiles', 'browser_profiles_named', 'browser_downloads'])
def test_browser_storage_uses_instance_home(root, tmp_path, monkeypatch):
    sandbox = tmp_path / 'sandbox with spaces'
    monkeypatch.setenv('MC_DATA_DIR', str(sandbox))
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    resolver = {'browser_profiles': br._profiles_root,
                'browser_profiles_named': br._named_profiles_root,
                'browser_downloads': br._downloads_root}[root]
    assert resolver() == os.path.join(str(sandbox), '.clayrune', root)
    assert not sandbox.exists(), 'resolving paths must not create or sweep anything'


@pytest.mark.parametrize('override', [None, ''])
@pytest.mark.parametrize('frozen', [False, True])
def test_default_browser_storage_paths_are_unchanged(override, frozen, monkeypatch):
    monkeypatch.setattr(sys, 'frozen', frozen, raising=False)
    if override is None:
        monkeypatch.delenv('MC_DATA_DIR', raising=False)
    else:
        monkeypatch.setenv('MC_DATA_DIR', override)
    home = os.path.expanduser('~')
    assert br._profiles_root() == os.path.join(home, '.clayrune', 'browser_profiles')
    assert br._named_profiles_root() == os.path.join(home, '.clayrune', 'browser_profiles_named')
    assert br._downloads_root() == os.path.join(home, '.clayrune', 'browser_downloads')


def test_packaged_browser_storage_ignores_instance_data_root(tmp_path, monkeypatch):
    data_root = tmp_path / 'packaged app data'
    monkeypatch.setenv('MC_DATA_DIR', str(data_root))
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    home = os.path.expanduser('~')
    assert br._profiles_root() == os.path.join(home, '.clayrune', 'browser_profiles')
    assert br._named_profiles_root() == os.path.join(home, '.clayrune', 'browser_profiles_named')
    assert br._downloads_root() == os.path.join(home, '.clayrune', 'browser_downloads')
    assert not data_root.exists(), 'resolving paths must not create or migrate anything'


def test_import_keeps_sweep_disabled():
    assert br.SWEEP_ENABLED is False


@pytest.fixture
def processes(monkeypatch):
    parent = SimpleNamespace(
        cmdline=lambda: ['python', str(Path(__file__).resolve().parents[1] / 'server.py'), '--port', '5200'],
        net_connections=lambda **kw: [
            SimpleNamespace(status='LISTEN', laddr=SimpleNamespace(port=5200)),
            SimpleNamespace(status='LISTEN', laddr=SimpleNamespace(port=5200)),
            SimpleNamespace(status='ESTABLISHED', laddr=SimpleNamespace(port=55555)),
        ],
    )
    holder = SimpleNamespace(ppid=lambda: 1234)
    calls = []

    def process(pid):
        calls.append(pid)
        return {4242: holder, 1234: parent}[pid]

    monkeypatch.setitem(sys.modules, 'psutil', SimpleNamespace(Process=process, CONN_LISTEN='LISTEN'))
    return holder, parent, calls


def test_owner_names_chrome_parent_and_listening_server_port(processes):
    assert owner.profile_owner(4242) == 'pid 4242, parent pid 1234, Clayrune server port 5200'


def test_owner_does_not_call_a_foreign_listener_clayrune(processes):
    _, parent, _ = processes
    parent.cmdline = lambda: ['unrelated-program']
    assert owner.profile_owner(4242) == 'pid 4242, parent pid 1234'


def test_owner_does_not_misidentify_an_unrelated_server_py(processes, tmp_path):
    _, parent, _ = processes
    parent.cmdline = lambda: ['python', str(tmp_path / 'server.py')]
    assert owner.profile_owner(4242) == 'pid 4242, parent pid 1234'


def test_owner_recognizes_relative_clayrune_server_launch(processes):
    _, parent, _ = processes
    parent.cmdline = lambda: ['python', 'server.py']
    parent.cwd = lambda: str(Path(__file__).resolve().parents[1])
    assert 'Clayrune server port 5200' in owner.profile_owner(4242)


def test_owner_recognizes_packaged_clayrune(processes):
    _, parent, _ = processes
    parent.cmdline = lambda: [r'C:\Apps\Clayrune.exe']
    assert 'Clayrune server port 5200' in owner.profile_owner(4242)


def test_owner_survives_parent_lookup_failure(processes):
    holder, _, _ = processes

    def denied():
        raise PermissionError('parent unavailable')

    holder.ppid = denied
    assert owner.profile_owner(4242) == 'pid 4242, parent unknown'


@pytest.mark.parametrize('operation', ['cmdline', 'net_connections'])
def test_owner_keeps_known_parent_when_inspection_fails(processes, operation):
    _, parent, _ = processes

    def denied(**kwargs):
        raise PermissionError('inspection denied')

    setattr(parent, operation, denied)
    assert owner.profile_owner(4242) == 'pid 4242, parent pid 1234'


def test_owner_survives_missing_psutil(monkeypatch):
    monkeypatch.setitem(sys.modules, 'psutil', None)
    assert owner.profile_owner(4242) == 'pid 4242, parent unknown'


@pytest.mark.parametrize('transport', ['--remote-debugging-pipe', '--remote-debugging-port=9222', ''])
def test_refusal_names_owner_and_never_closes_untracked_holder(transport, processes, monkeypatch):
    monkeypatch.setattr(br, 'browser_sessions', {})
    monkeypatch.setattr(br, '_profile_dir_locked', lambda udd: True)
    monkeypatch.setattr(br, '_profile_holder', lambda udd: (4242, f'chrome --headless {transport}'))

    def no_close(*args, **kwargs):
        pytest.fail('untracked Chromium must never be closed')

    monkeypatch.setattr(br, '_cdp_browser_close', no_close)
    monkeypatch.setattr(br, '_kill_browser_session', no_close)
    error = br._release_held_profile('main', '/unused/main')
    assert "profile 'main' is in use by another browser process" in error
    assert 'pid 4242, parent pid 1234, Clayrune server port 5200' in error


def test_refusal_still_works_when_owner_parent_lookup_fails(processes, monkeypatch):
    holder, _, _ = processes

    def gone():
        raise ProcessLookupError('exited')

    holder.ppid = gone
    monkeypatch.setattr(br, 'browser_sessions', {})
    monkeypatch.setattr(br, '_profile_dir_locked', lambda udd: True)
    monkeypatch.setattr(br, '_profile_holder', lambda udd: (4242, 'chrome --remote-debugging-pipe'))
    assert 'pid 4242, parent unknown' in br._release_held_profile('main', '/unused/main')


def test_refusal_survives_unidentified_holder(monkeypatch):
    monkeypatch.setattr(br, 'browser_sessions', {})
    monkeypatch.setattr(br, '_profile_dir_locked', lambda udd: True)
    monkeypatch.setattr(br, '_profile_holder', lambda udd: None)
    assert 'could not identify it' in br._release_held_profile('main', '/unused/main')


def test_release_still_cleans_up_this_instances_stale_session(monkeypatch):
    stale = {'profile': 'main', 'status': 'error', 'proc': SimpleNamespace(poll=lambda: None)}
    monkeypatch.setattr(br, 'browser_sessions', {'ours': stale})
    closed = []
    monkeypatch.setattr(br, '_kill_browser_session', closed.append)
    monkeypatch.setattr(br, '_profile_dir_locked', lambda udd: False)
    assert br._release_held_profile('main', '/unused/main') is None
    assert closed == [stale]
