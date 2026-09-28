"""Regression tests for the config-toggle + endpoint wiring around MC-991
Phase 2's orphan CLI process sweep (mc/blueprints/system_routes.py's
`run_process_sweep` / `_process_sweep_root_pids` / `/api/system/process-sweep`).

mc/process_sweep.py's own criteria (image match, dead ancestor chain,
registry protection, age, CPU idleness) are covered in isolation by
tests/test_process_sweep.py with no server import at all. This file covers
what only exists once the module is wired into the running server: the
`process_sweep_enabled`/`process_sweep_dry_run` config toggle, root_pids
built from the live registry UNION the on-disk child-PID ledger (the
restart re-adoption window), and the durable log file.

Reloads `server` under an isolated MC_DATA_DIR (tmp_data_dir, conftest.py)
per the mc/process_ledger.py test precedent (tests/test_pid_reaper.py's
`srv` fixture) — never touches the real ./data tree. `process_sweep.run_sweep`
itself is stubbed in every test here; no real process enumeration or kill
ever happens.
"""
from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture
def srv(tmp_data_dir):
    import server
    importlib.reload(server)
    from mc.blueprints import system_routes as sr
    from mc import state
    return server, sr, state


def _stub_report(**overrides):
    base = {'ok': True, 'dry_run': False, 'candidates': [], 'protected_skipped': [],
            'not_idle_skipped': [], 'killed': []}
    base.update(overrides)
    return base


# ── config toggle: process_sweep_enabled ────────────────────────────────────

def test_toggle_off_skips_the_sweep_entirely(srv, monkeypatch):
    """process_sweep_enabled=False must short-circuit BEFORE process_sweep.run_sweep
    is even called -- no per-call override, no way to force a kill while off."""
    _server, sr, state = srv
    monkeypatch.setitem(state.CONFIG, 'process_sweep_enabled', False)
    called = []
    monkeypatch.setattr(sr.process_sweep, 'run_sweep', lambda **kw: called.append(kw) or _stub_report())

    report = sr.run_process_sweep()
    assert report['ok'] is True
    assert report.get('skipped') is True
    assert called == []


def test_toggle_default_on_when_unset(srv, monkeypatch):
    """Absence of the config key must behave as enabled (default ON per the
    brief) -- not as disabled."""
    _server, sr, state = srv
    state.CONFIG.pop('process_sweep_enabled', None)
    called = []
    monkeypatch.setattr(sr.process_sweep, 'run_sweep', lambda **kw: called.append(kw) or _stub_report())
    monkeypatch.setattr(sr, '_kill_pid', lambda pid, tree=True: True)

    sr.run_process_sweep()
    assert len(called) == 1


# ── dry_run: config default vs explicit override ────────────────────────────

def test_dry_run_config_default_used_when_no_explicit_arg(srv, monkeypatch):
    _server, sr, state = srv
    monkeypatch.setitem(state.CONFIG, 'process_sweep_enabled', True)
    monkeypatch.setitem(state.CONFIG, 'process_sweep_dry_run', True)
    captured = {}
    monkeypatch.setattr(sr.process_sweep, 'run_sweep',
                        lambda **kw: captured.update(kw) or _stub_report(dry_run=kw['dry_run']))
    monkeypatch.setattr(sr, '_kill_pid', lambda pid, tree=True: True)

    sr.run_process_sweep()
    assert captured['dry_run'] is True


def test_dry_run_explicit_arg_overrides_config(srv, monkeypatch):
    _server, sr, state = srv
    monkeypatch.setitem(state.CONFIG, 'process_sweep_enabled', True)
    monkeypatch.setitem(state.CONFIG, 'process_sweep_dry_run', False)
    captured = {}
    monkeypatch.setattr(sr.process_sweep, 'run_sweep',
                        lambda **kw: captured.update(kw) or _stub_report(dry_run=kw['dry_run']))
    monkeypatch.setattr(sr, '_kill_pid', lambda pid, tree=True: True)

    sr.run_process_sweep(dry_run=True)
    assert captured['dry_run'] is True


# ── root_pids: restart re-adoption from the on-disk ledger ──────────────────

def test_root_pids_includes_live_registry(srv):
    _server, sr, state = srv
    with state.process_tracker_lock:
        state.tracked_processes[777] = {'pid': 777}
    try:
        pids = sr._process_sweep_root_pids()
        assert 777 in pids
    finally:
        with state.process_tracker_lock:
            state.tracked_processes.pop(777, None)


def test_root_pids_includes_on_disk_ledger_after_restart(srv, tmp_data_dir):
    """CRITICAL case from the brief: a server restart clears the in-memory
    registry (tracked_processes) before session revival has re-populated
    it, but the previous instance's live children are still recorded in
    mc_child_pids.json. Those PIDs must be protected too, or the sweep
    would kill its own just-restarted server's re-adopted children."""
    _server, sr, state = srv
    with state.process_tracker_lock:
        state.tracked_processes.clear()  # simulate the post-restart gap
    ledger_path = tmp_data_dir / 'data' / 'mc_child_pids.json'
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps({'children': [{'pid': 888}, {'pid': 889}]}), encoding='utf-8')

    pids = sr._process_sweep_root_pids()
    assert {888, 889}.issubset(pids)


def test_root_pids_survives_missing_ledger_file(srv):
    """No mc_child_pids.json yet (fresh install) must not raise -- just
    fall back to whatever the live registry has."""
    _server, sr, state = srv
    pids = sr._process_sweep_root_pids()
    assert isinstance(pids, set)


def test_run_process_sweep_protects_readopted_pid_end_to_end(srv, tmp_data_dir, monkeypatch):
    """End-to-end: an orphan-shaped candidate whose PID is ONLY in the
    on-disk ledger (the restart window) must come back as protected, not
    killed, when run_process_sweep feeds real root_pids into process_sweep.run_sweep."""
    _server, sr, state = srv
    with state.process_tracker_lock:
        state.tracked_processes.clear()
    ledger_path = tmp_data_dir / 'data' / 'mc_child_pids.json'
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps({'children': [{'pid': 45812}]}), encoding='utf-8')
    monkeypatch.setitem(state.CONFIG, 'process_sweep_enabled', True)
    monkeypatch.setitem(state.CONFIG, 'process_sweep_dry_run', False)

    captured = {}

    def fake_run_sweep(**kw):
        captured.update(kw)
        assert 45812 in kw['root_pids']
        return _stub_report(candidates=[{'pid': 45812}], protected_skipped=[{'pid': 45812}])

    monkeypatch.setattr(sr.process_sweep, 'run_sweep', fake_run_sweep)
    report = sr.run_process_sweep()
    assert report['protected_skipped'] == [{'pid': 45812}]


# ── /api/system/process-sweep endpoint ──────────────────────────────────────

def test_endpoint_returns_sweep_report(srv, monkeypatch):
    _server, sr, state = srv
    monkeypatch.setitem(state.CONFIG, 'process_sweep_enabled', True)
    monkeypatch.setattr(sr.process_sweep, 'run_sweep', lambda **kw: _stub_report(dry_run=kw['dry_run']))
    monkeypatch.setattr(sr, '_kill_pid', lambda pid, tree=True: True)
    server, _sr2, _state2 = srv
    server.app.config['TESTING'] = True
    client = server.app.test_client()

    resp = client.post('/api/system/process-sweep', json={'dry_run': True})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is True
    assert body['dry_run'] is True


def test_endpoint_honors_toggle_off(srv, monkeypatch):
    _server, sr, state = srv
    monkeypatch.setitem(state.CONFIG, 'process_sweep_enabled', False)
    monkeypatch.setattr(sr.process_sweep, 'run_sweep',
                        lambda **kw: pytest.fail('run_sweep must not be called when disabled'))
    server, _sr2, _state2 = srv
    server.app.config['TESTING'] = True
    client = server.app.test_client()

    resp = client.post('/api/system/process-sweep', json={})
    assert resp.status_code == 200
    assert resp.get_json().get('skipped') is True


# ── durable log surfacing ────────────────────────────────────────────────────

def test_kill_report_appended_to_log_file(srv, monkeypatch):
    _server, sr, state = srv
    monkeypatch.setitem(state.CONFIG, 'process_sweep_enabled', True)
    entry = {'pid': 45812, 'action': 'killed', 'cli_name': 'codex',
             'start_epoch': 0, 'age_hours': 30, 'exe': 'codex.exe'}
    monkeypatch.setattr(sr.process_sweep, 'run_sweep',
                        lambda **kw: _stub_report(killed=[entry]))
    monkeypatch.setattr(sr, '_kill_pid', lambda pid, tree=True: True)

    sr.run_process_sweep()
    assert sr.PROCESS_SWEEP_LOG_PATH.exists()
    log = json.loads(sr.PROCESS_SWEEP_LOG_PATH.read_text(encoding='utf-8'))
    assert log[-1]['killed'] == [entry]
    assert 'ts' in log[-1]


def test_no_op_report_not_appended_to_log(srv, monkeypatch):
    """A clean sweep (nothing found, nothing killed) must not grow the log
    file forever -- only kills and errors are durable-logged (clayrune.log
    still gets a heartbeat via obs.heartbeat in the periodic loop)."""
    _server, sr, state = srv
    monkeypatch.setitem(state.CONFIG, 'process_sweep_enabled', True)
    monkeypatch.setattr(sr.process_sweep, 'run_sweep', lambda **kw: _stub_report())

    sr.run_process_sweep()
    assert not sr.PROCESS_SWEEP_LOG_PATH.exists()
