"""MC-1025: the built-in daily agent-CLI update (mc/cli_update.py + the loop and
in-use probe in mc/blueprints/system_routes.py).

The decision tree runs with every outward dependency stubbed (subprocess, HTTP,
process list, clock): no test here touches the real machine, installs anything
or reaches the network. The server-wiring tests reload `server` under an
isolated MC_DATA_DIR (tmp_data_dir, conftest.py) like test_pid_reaper.py.
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest

from mc import cli_install, cli_update

NOW = 1_790_000_000.0


class FakeRT:
    def __init__(self, name, path):
        self.name = name
        self._path = path

    def resolve_binary(self):
        return self._path


class Box:
    """Stands in for the machine: a version per binary, a registry of what
    was run, and an `npm view` answer. A run of the updater bumps the version."""

    def __init__(self, version, latest, *, update_rc=0, bumps_to=None):
        self.version, self.latest = version, latest
        self.update_rc, self.bumps_to = update_rc, bumps_to
        self.calls = []

    def run(self, argv, timeout=60, env=None):
        self.calls.append(list(argv))
        if argv[-1] == '--version':
            return 0, 'tool %s' % self.version
        if 'view' in argv:
            return (0, self.latest) if self.latest else (1, 'E404')
        # anything else is the updater
        if self.update_rc == 0 and self.bumps_to:
            self.version = self.bumps_to
        return self.update_rc, 'updater output'

    def updater_calls(self):
        return [c for c in self.calls if c[-1] != '--version' and 'view' not in c]


@pytest.fixture
def npm_gemini(tmp_path):
    """A gemini under an npm prefix (so install_method reads it as npm) with
    an npm beside it."""
    bindir = tmp_path / 'home' / '.npm-global' / 'bin'
    bindir.mkdir(parents=True)
    gem = bindir / 'gemini'
    gem.write_text('#!/bin/sh\n')
    (bindir / 'npm').write_text('#!/bin/sh\n')
    return gem


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    p = tmp_path / 'data' / 'cli_update_state.json'
    p.parent.mkdir()
    monkeypatch.setattr(cli_update, 'STATE_PATH', p)
    monkeypatch.setattr(cli_update, '_STATE', {'clis': {}})
    return p


def _run(rts, box, *, in_use=lambda n: [], enabled=True, listing=lambda: [],
         http=lambda url, timeout=15: {}, force=False, now=NOW, platform=None):
    return cli_update.run_once(
        enabled_fn=lambda: enabled, runtimes_fn=lambda: rts, in_use_fn=in_use,
        now_fn=lambda: now, run_fn=box.run, http_fn=http, list_fn=listing, force=force, platform=platform)


# ── behind + known install method -> updated ────────────────────────────────

def test_behind_with_known_method_updates_and_records_it(npm_gemini, state_file):
    box = Box('0.50.0', '0.60.0', bumps_to='0.60.0')
    out = _run([FakeRT('gemini', npm_gemini)], box)
    assert out['checked'] == {'gemini': 'updated'}
    # the updater is npm install -g, run with the npm that sits BESIDE the binary
    (call,) = box.updater_calls()
    assert call[0] == str(npm_gemini.parent / 'npm')
    assert call[1:] == ['install', '-g', '@google/gemini-cli@latest']
    rec = json.loads(state_file.read_text(encoding='utf-8'))['clis']['gemini']
    assert rec['installed'] == '0.50.0' and rec['installed_after'] == '0.60.0'
    assert rec['last_update']['from'] == '0.50.0' and rec['last_update']['to'] == '0.60.0'
    summ = cli_update.provider_summary('gemini')
    assert 'Auto-updated 0.50.0' in summ['text'] and '0.60.0' in summ['text']


def test_updated_record_outlives_the_next_up_to_date_check(npm_gemini, state_file):
    box = Box('0.50.0', '0.60.0', bumps_to='0.60.0')
    _run([FakeRT('gemini', npm_gemini)], box)
    _run([FakeRT('gemini', npm_gemini)], box, now=NOW + 25 * 3600)
    summ = cli_update.provider_summary('gemini')
    assert summ['status'] == 'up_to_date'
    assert 'last auto-update 0.50.0' in summ['text']


def test_codex_standalone_uses_the_vendor_installer_never_npm(tmp_path, state_file, monkeypatch):
    tree = tmp_path / '.codex' / 'packages' / 'standalone' / 'current'
    tree.mkdir(parents=True)
    exe = tree / 'codex'
    exe.write_text('x')
    box = Box('0.153.0', '0.159.3', bumps_to='0.159.3')
    monkeypatch.setattr(cli_update.shutil, 'which',
                        lambda n: {'sh': '/usr/bin/sh', 'npm': '/usr/bin/npm'}.get(n))
    out = _run([FakeRT('codex', exe)], box, platform='darwin')
    assert out['checked'] == {'codex': 'updated'}
    (call,) = box.updater_calls()
    assert call == ['/usr/bin/sh', '-c', cli_install.CODEX_UPDATE_COMMANDS['standalone']]
    # npm was only ever asked for the version, never to install over the standalone copy
    assert not any('install' in c for c in box.calls if 'npm' in c[0])


def test_codex_latest_falls_back_to_github_when_npm_is_missing(tmp_path, state_file, monkeypatch):
    tree = tmp_path / '.codex' / 'packages' / 'standalone' / 'current'
    tree.mkdir(parents=True)
    exe = tree / 'codex'
    exe.write_text('x')
    box = Box('0.153.0', None, bumps_to='0.159.3')
    monkeypatch.setattr(cli_update.shutil, 'which', lambda n: '/usr/bin/sh' if n == 'sh' else None)
    asked = []

    def http(url, timeout=15):
        asked.append(url)
        return {'tag_name': 'rust-v0.159.3'}
    out = _run([FakeRT('codex', exe)], box, http=http, platform='darwin')
    assert out['checked'] == {'codex': 'updated'}
    assert asked == ['https://api.github.com/repos/openai/codex/releases/latest']


# ── unknown method / unknown latest: report, do not act ─────────────────────

def test_unknown_install_method_reports_and_never_runs_an_updater(tmp_path, state_file):
    odd = tmp_path / 'opt' / 'weird' / 'gemini'
    odd.parent.mkdir(parents=True)
    odd.write_text('x')
    box = Box('0.50.0', '0.60.0', bumps_to='0.60.0')
    out = _run([FakeRT('gemini', odd)], box)
    assert out['checked'] == {'gemini': 'unknown_install_method'}
    assert box.updater_calls() == []
    summ = cli_update.provider_summary('gemini')
    assert 'Not auto-updated' in summ['text'] and 'cannot tell how it was installed' in summ['text']


def test_latest_unknown_when_no_source_answers_updates_nothing(npm_gemini, state_file):
    box = Box('0.50.0', None)          # npm view fails; gemini has no vendor feed
    out = _run([FakeRT('gemini', npm_gemini)], box)
    assert out['checked'] == {'gemini': 'latest_unknown'}
    assert box.updater_calls() == []
    assert 'Latest version unknown' in cli_update.provider_summary('gemini')['text']


def test_claude_without_npm_is_latest_unknown_not_guessed(tmp_path, state_file, monkeypatch):
    exe = tmp_path / 'home' / '.local' / 'bin' / 'claude'
    exe.parent.mkdir(parents=True)
    exe.write_text('x')
    monkeypatch.setattr(cli_update.shutil, 'which', lambda n: None)
    box = Box('2.1.0', None)
    out = _run([FakeRT('claude', exe)], box)
    assert out['checked'] == {'claude': 'latest_unknown'}
    assert box.updater_calls() == []


def test_claude_behind_runs_its_own_update_command(tmp_path, state_file):
    bindir = tmp_path / 'home' / '.local' / 'bin'
    bindir.mkdir(parents=True)
    exe = bindir / 'claude'
    exe.write_text('x')
    (bindir / 'npm').write_text('x')   # an npm for the oracle only
    box = Box('2.1.0', '2.2.0', bumps_to='2.2.0')
    out = _run([FakeRT('claude', exe)], box)
    assert out['checked'] == {'claude': 'updated'}
    assert box.updater_calls() == [[str(exe), 'update']]


def test_runtime_with_no_table_entry_is_left_alone(tmp_path, state_file):
    exe = tmp_path / 'goose'
    exe.write_text('x')
    box = Box('1.0.0', '2.0.0')
    out = _run([FakeRT('goose', exe)], box)
    assert out['checked'] == {} and box.calls == []


# ── in use -> skipped, retried hourly ───────────────────────────────────────

def test_cli_a_live_session_uses_is_skipped_not_updated(npm_gemini, state_file):
    box = Box('0.50.0', '0.60.0', bumps_to='0.60.0')
    out = _run([FakeRT('gemini', npm_gemini)], box,
               in_use=lambda n: ['session abc12345 (running)'] if n == 'gemini' else [])
    assert out['checked'] == {'gemini': 'skipped_in_use'}
    assert box.updater_calls() == []
    assert box.version == '0.50.0'
    assert 'abc12345' in cli_update.provider_summary('gemini')['text']


def test_failed_process_enumeration_counts_as_in_use(npm_gemini, state_file):
    box = Box('0.50.0', '0.60.0', bumps_to='0.60.0')
    out = _run([FakeRT('gemini', npm_gemini)], box, listing=lambda: None)
    assert out['checked'] == {'gemini': 'skipped_in_use'}
    assert box.updater_calls() == []


def test_os_process_running_out_of_the_package_dir_blocks(tmp_path, state_file):
    pkg = tmp_path / 'npm' / 'node_modules' / '@google' / 'gemini-cli' / 'bin'
    pkg.mkdir(parents=True)
    exe = pkg / 'gemini'
    exe.write_text('x')
    box = Box('0.50.0', '0.60.0', bumps_to='0.60.0')
    live = [{'pid': 77, 'name': 'node', 'exe': str(pkg / 'gemini.exe')}]
    out = _run([FakeRT('gemini', exe)], box, listing=lambda: live)
    assert out['checked'] == {'gemini': 'skipped_in_use'}
    assert box.updater_calls() == []


def test_skipped_in_use_retries_after_an_hour_others_wait_a_day(npm_gemini, state_file):
    box = Box('0.50.0', '0.60.0', bumps_to='0.60.0')
    rts = [FakeRT('gemini', npm_gemini)]
    _run(rts, box, in_use=lambda n: ['session s (running)'])
    # 30 min later: not due
    assert _run(rts, box, now=NOW + 1800)['checked'] == {}
    # 61 min later: due again, now free -> updates
    assert _run(rts, box, now=NOW + 3700)['checked'] == {'gemini': 'updated'}
    # an up-to-date result is not rechecked for a day
    assert _run(rts, box, now=NOW + 3700 + 3600)['checked'] == {}
    assert _run(rts, box, now=NOW + 3700 + 24 * 3600)['checked'] == {'gemini': 'up_to_date'}


# ── toggle off ──────────────────────────────────────────────────────────────

def test_toggle_off_does_nothing_at_all(npm_gemini, state_file):
    class Boom(FakeRT):
        def resolve_binary(self):
            raise AssertionError('toggle OFF must not even look for the binary')

    def no_run(*a, **k):
        raise AssertionError('toggle OFF must not run a subprocess')

    def no_http(*a, **k):
        raise AssertionError('toggle OFF must not touch the network')
    out = cli_update.run_once(enabled_fn=lambda: False, runtimes_fn=lambda: [Boom('gemini', npm_gemini)],
                              in_use_fn=lambda n: [], now_fn=lambda: NOW,
                              run_fn=no_run, http_fn=no_http, list_fn=lambda: [])
    assert out['skipped'] is True
    assert not state_file.exists()


# ── failure handling ────────────────────────────────────────────────────────

def test_failed_update_is_recorded_with_the_reason(npm_gemini, state_file):
    box = Box('0.50.0', '0.60.0', update_rc=1)
    out = _run([FakeRT('gemini', npm_gemini)], box)
    assert out['checked'] == {'gemini': 'update_failed'}
    assert 'Auto-update failed' in cli_update.provider_summary('gemini')['text']
    assert 'updater output' in cli_update.provider_summary('gemini')['text']


def test_downgrade_by_a_failed_update_is_named(npm_gemini, state_file):
    box = Box('0.50.0', '0.60.0', update_rc=1)
    real_run = box.run

    def run(argv, timeout=60, env=None):
        rc, out = real_run(argv, timeout, env)
        if argv[-1] != '--version' and 'view' not in argv:
            box.version = '0.49.0'
        return rc, out
    box.run = run
    _run([FakeRT('gemini', npm_gemini)], box)
    assert 'DOWNGRADED 0.50.0 -> 0.49.0' in cli_update.provider_summary('gemini')['text']


def test_updater_that_changes_nothing_is_not_reported_as_updated(npm_gemini, state_file):
    box = Box('0.50.0', '0.60.0', update_rc=0, bumps_to=None)
    out = _run([FakeRT('gemini', npm_gemini)], box)
    assert out['checked'] == {'gemini': 'update_no_change'}


def test_npm_not_found_is_reported_not_guessed(tmp_path, state_file, monkeypatch):
    bindir = tmp_path / 'h' / '.npm-global' / 'bin'
    bindir.mkdir(parents=True)
    gem = bindir / 'gemini'
    gem.write_text('x')                         # no npm beside it
    monkeypatch.setattr(cli_update.shutil, 'which', lambda n: None)
    box = Box('0.50.0', '0.60.0')
    box_http = {'info': {'version': '0.60.0'}}
    # the oracle needs npm too, so latest is unknown first -- either way no updater runs
    out = _run([FakeRT('gemini', gem)], box, http=lambda u, timeout=15: box_http)
    assert out['checked']['gemini'] in ('latest_unknown', 'updater_unavailable')
    assert box.updater_calls() == []


def test_uninstalled_cli_is_recorded_and_hidden_from_the_row(tmp_path, state_file):
    out = _run([FakeRT('gemini', tmp_path / 'nope')], Box('1.0.0', '2.0.0'))
    assert out['checked'] == {'gemini': 'not_installed'}
    assert cli_update.provider_summary('gemini') is None


# ── aider: pip family ───────────────────────────────────────────────────────

def test_aider_pipx_and_pip_pick_their_own_updater(tmp_path):
    pipx = tmp_path / 'pipx' / 'venvs' / 'aider-chat' / 'bin' / 'aider'
    pipx.parent.mkdir(parents=True)
    pipx.write_text('x')
    assert cli_install.update_command('aider', pipx) == 'pipx upgrade aider-chat'
    venv = tmp_path / 'venv' / 'bin'
    venv.mkdir(parents=True)
    (venv / 'aider').write_text('x')
    (venv / 'python').write_text('x')
    assert cli_install.update_command('aider', venv / 'aider') == 'python -m pip install -U aider-chat'
    argv, _env, why = cli_update.build_argv('aider', venv / 'aider',
                                            'python -m pip install -U aider-chat')
    assert argv == [str(venv / 'python'), '-m', 'pip', 'install', '-U', 'aider-chat'], why
    lone = tmp_path / 'lone' / 'aider'
    lone.parent.mkdir()
    lone.write_text('x')
    assert cli_install.update_command('aider', lone) is None      # no interpreter beside it: report


def test_npm_clis_are_updated_by_npm_only_when_npm_installed_them(tmp_path):
    other = tmp_path / 'opt' / 'qwen'
    other.parent.mkdir(parents=True)
    other.write_text('x')
    assert cli_install.update_command('qwen', other) is None
    shim = tmp_path / 'AppData' / 'Roaming' / 'npm' / 'opencode.cmd'
    shim.parent.mkdir(parents=True)
    shim.write_text('x')
    assert cli_install.update_command('opencode', shim, 'win32') == 'npm install -g opencode-ai@latest'


# ── server wiring ───────────────────────────────────────────────────────────

@pytest.fixture
def srv(tmp_data_dir):
    import server
    importlib.reload(server)
    from mc.blueprints import system_routes as sr
    from mc import state
    return server, sr, state


def test_sidecar_lives_beside_data_not_inside_data_projects(srv):
    server, _sr, _state = srv
    path = Path(cli_update.STATE_PATH)
    assert path.name == 'cli_update_state.json'
    assert path.parent.name == 'data'
    assert 'projects' not in path.parts[-3:]
    assert path.parent != Path(server.DATA_DIR)


def test_toggle_defaults_on_and_is_a_saveable_setting(srv):
    from mc.blueprints import settings_routes
    _server, _sr, state = srv
    assert state.CONFIG.get('cli_auto_update_enabled') is True
    assert 'cli_auto_update_enabled' in settings_routes._CONFIG_EDITABLE_KEYS


def test_run_cli_update_reads_the_toggle_from_live_config(srv, monkeypatch):
    _server, sr, state = srv
    seen = []
    monkeypatch.setattr(sr.cli_update, 'run_once', lambda **kw: seen.append(kw['enabled_fn']()) or {})
    monkeypatch.setitem(state.CONFIG, 'cli_auto_update_enabled', False)
    sr._run_cli_update()
    monkeypatch.setitem(state.CONFIG, 'cli_auto_update_enabled', True)
    sr._run_cli_update()
    monkeypatch.delitem(state.CONFIG, 'cli_auto_update_enabled')
    sr._run_cli_update()
    assert seen == [False, True, True]          # absent key = ON


def test_loop_is_started_by_server_and_heartbeats(srv):
    server, sr, _state = srv
    assert server._cli_update_loop is sr._cli_update_loop
    assert 'obs.heartbeat(\'cli-update\')' in Path(server.__file__).parent.joinpath(
        'mc', 'blueprints', 'system_routes.py').read_text(encoding='utf-8')
    assert "target=_cli_update_loop" in Path(server.__file__).read_text(encoding='utf-8')


class _Proc:
    def __init__(self, alive):
        self._alive = alive

    def poll(self):
        return None if self._alive else 0


def test_in_use_probe_sees_sessions_and_registered_processes(srv, monkeypatch):
    _server, sr, _state = srv
    monkeypatch.setattr(sr, 'agent_sessions', {
        's1111111': {'provider': 'gemini', 'status': 'idle', 'proc': _Proc(True)},   # idle Mode B, proc alive
        's2222222': {'provider': 'gemini', 'status': 'completed', 'proc': _Proc(False)},
        's3333333': {'provider': 'codex', 'status': 'running'},
        's4444444': {'status': 'running'},                                            # default provider = claude
    })
    monkeypatch.setattr(sr, 'tracked_processes', {
        10: {'pid': 10, 'name': 'gemini-watch', 'command_preview': 'gemini -p x', 'proc': _Proc(True)},
        11: {'pid': 11, 'name': 'build', 'command_preview': 'gemini-cli-tests', 'proc': _Proc(True)},  # different word
        12: {'pid': 12, 'name': 'gemini', 'command_preview': '', 'proc': _Proc(False)},               # dead
    })
    got = sr._cli_in_use_reasons('gemini')
    assert got == ['session s1111111 (idle)', 'registered process pid 10']
    assert sr._cli_in_use_reasons('codex') == ['session s3333333 (running)']
    assert sr._cli_in_use_reasons('claude') == ['session s4444444 (running)']
    assert sr._cli_in_use_reasons('qwen') == []


def test_provider_list_carries_the_cli_update_summary(srv, monkeypatch):
    server, _sr, _state = srv
    from mc.blueprints import agent_routes
    monkeypatch.setattr(cli_update, '_STATE', {'clis': {'gemini': {
        'status': 'up_to_date', 'installed': '0.6.0', 'latest': '0.6.0',
        'checked_at': '2026-10-02T03:00:00+00:00', 'checked_ts': NOW}}})
    client = server.app.test_client()
    body = client.get('/api/agent/providers').get_json()
    rows = body['providers'] if isinstance(body, dict) else body
    by = {r['name']: r for r in rows}
    assert by['gemini']['cli_update']['text'] == 'Up to date (checked 2026-10-02)'
    assert all('cli_update' in r for r in rows)
    assert agent_routes._cli_update is cli_update
