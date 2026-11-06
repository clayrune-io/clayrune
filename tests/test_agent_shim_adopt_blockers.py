"""MC-1150 P4: regression tests for Fenn's five merge blockers on 55083ab1
(docs/_journal/ebbb15a7-fenn-p4-review.md). Every test here fails on 55083ab1.

  1  PID reuse put an unrelated process into the kill ledger
  2  a crash after the forced checkpoint lost undelivered completion work
  3  an older checkpoint could overwrite the completion checkpoint
  4  a missing reader state turned output into "no reply"  (Dave: unsupported
     reader paths are not adopted and stay restart blockers)
  5  Power off spared shim agents

Run:  .venv/Scripts/python.exe -m pytest tests/test_agent_shim_adopt_blockers.py -q
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mc import agent_shim as shim  # noqa: E402
from mc import agent_shim_adopt as A  # noqa: E402
from mc import agent_shim_client as C  # noqa: E402
from mc.core import TimestampedLines  # noqa: E402

from tests.test_agent_shim import wait_for  # noqa: E402
from tests.test_agent_shim_adopt import (  # noqa: E402,F401
    LIVE, ON, OFF, _launch, _man, _write_ck, tracker)


@pytest.fixture(autouse=True)
def _clean_registry():
    A.reset_for_tests()
    yield
    A.reset_for_tests()


@pytest.fixture
def restorable(monkeypatch):
    """Declare the stand-in's reader path restorable, for the tests that exercise
    the adoption machinery itself (the production registry is empty)."""
    monkeypatch.setattr(A, 'RESTORABLE_READER_PATHS', frozenset({'qwen:A'}))


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(A, 'enabled_now', lambda: True)


def _shim_session(tracker, root, sid, *flags, **over):
    sdir = _launch(tracker, root, sid, *flags)
    proc = C.ShimProc(sdir, client=None, start_offset=0, manifest=_man(sdir))
    s = {'session_id': sid, 'status': 'running', 'project_id': 'p', 'task': 't', 'proc': proc,
         'log_lines': TimestampedLines(), 'mode': 'A'}
    s.update(over)
    return sdir, s


def _ck_of(sdir):
    return json.loads((Path(sdir) / A.CHECKPOINT_NAME).read_text(encoding='utf-8'))


# ═══ 1. PID reuse must not enter the kill ledger ═══════════════════════════════
class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, proc, name, ptype, sid, pid_, preview='', identity=None):
        self.calls.append({'pid': proc.pid, 'identity': identity})


def _deps(ident, rec):
    return A.Deps(agent_sessions={}, get_manager=lambda p: None, register_process=rec,
                  start_reader=lambda e, p, s: None, session_provider=lambda s, spec: 'qwen',
                  proc_identity=ident)


def test_reused_pid_is_not_registered_into_the_kill_ledger(tracker, tmp_path, restorable):
    sdir = _launch(tracker, tmp_path, 'b1_reuse')
    _write_ck(sdir)
    claim = A.claim_all(tmp_path, cfg=ON)
    assert claim.claimed == ['b1_reuse']
    rec = _Recorder()
    # the pid now belongs to a stranger: same pid, a different create-time
    A.start_all(_deps(lambda pid: ('unrelated.exe', 999.0), rec))
    assert rec.calls == [], rec.calls


def test_a_cli_that_finished_during_downtime_is_replayed_but_never_registered(tracker, tmp_path, restorable):
    sdir = _launch(tracker, tmp_path, 'b1_done', '--lines', '3', '--interval', '0.01')
    assert wait_for(lambda: (sdir / 'exit.json').exists(), 20)
    wait_for(lambda: shim.process_state(_man(sdir)['cli_pid'], _man(sdir)['cli_ct']) == 'gone', 20)
    _write_ck(sdir)
    claim = A.claim_all(tmp_path, cfg=ON)
    assert claim.claimed == ['b1_done']
    m = _man(sdir)
    assert m['cli_pid'] not in A.protected_pids()
    rec = _Recorder()
    A.start_all(_deps(lambda pid: (None, None), rec))
    assert rec.calls == []


def test_a_positively_owned_cli_is_registered_with_the_manifest_identity(tracker, tmp_path, restorable):
    sdir = _launch(tracker, tmp_path, 'b1_live')
    _write_ck(sdir)
    A.claim_all(tmp_path, cfg=ON)
    m = _man(sdir)
    rec = _Recorder()
    # the live holder's create-time is a hair off the manifest's (sampling jitter)
    jitter = shim.IDENTITY_TOLERANCE_S / 2
    A.start_all(_deps(lambda pid: ('qwen.exe', float(m['cli_ct']) + jitter), rec))
    assert len(rec.calls) == 1
    # the entry keeps the manifest's create-time, not the one just sampled
    assert rec.calls[0]['identity'] == ('qwen.exe', float(m['cli_ct']))


def test_protected_pids_are_rechecked_when_the_reaper_asks(tracker, tmp_path, restorable, monkeypatch):
    sdir = _launch(tracker, tmp_path, 'b1_prot')
    _write_ck(sdir)
    A.claim_all(tmp_path, cfg=ON)
    m = _man(sdir)
    assert {m['cli_pid'], m['shim_pid']} <= A.protected_pids()
    # between assessment and the reaper, the CLI's pid is reused by a stranger
    real = shim.process_state
    monkeypatch.setattr(A._shim, 'process_state',
                        lambda pid, ct: 'stale' if pid == m['cli_pid'] else real(pid, ct))
    got = A.protected_pids()
    assert m['cli_pid'] not in got and m['shim_pid'] in got


# ═══ 2. crash after the forced checkpoint: completion work survives ═══════════
class Crash(BaseException):
    """A hard stop: not caught by the `except Exception` blocks on the path."""


@pytest.fixture
def cenv(tracker, tmp_path, monkeypatch, restorable, flag_on):
    import server  # noqa: F401  (wires the blueprints)
    from mc import agent_spawn
    from mc.blueprints import agent_routes as ar
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data_dir)
    project = {'id': 'proj1', 'project_path': str(tmp_path)}
    monkeypatch.setattr(ar, 'load_project', lambda pid: project if pid == 'proj1' else None)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_write_session_memory', lambda *a, **k: True)
    wakes = []

    def _wake(project_id, notify_sid, session, summary):
        wakes.append((session.get('session_id'), session.get('_delegation_turn', 1)))
        return True
    monkeypatch.setattr(ar, '_notify_agent_spawner', _wake)
    agent_spawn.set_root(tmp_path)
    sdir, s = _shim_session(tracker, tmp_path, 'cr1', '--lines', '3', '--interval', '0.01',
                            status='completed', project_id='proj1', provider='qwen',
                            started_at='2026-11-05T10:00:00', _notify_session='parent1',
                            _delegation_turn=1)
    s['log_lines'].append('the answer')
    assert wait_for(lambda: (sdir / 'exit.json').exists(), 20)
    yield {'ar': ar, 'sdir': sdir, 'session': s, 'wakes': wakes, 'root': tmp_path,
           'rows': lambda: json.loads((data_dir / 'proj1_agent_log.json').read_text(encoding='utf-8'))
           if (data_dir / 'proj1_agent_log.json').exists() else []}


def _replay(cenv):
    from mc import agent_shim_completion as comp
    return comp.replay_pending(cenv['root'])


def test_completion_record_is_on_disk_before_the_first_side_effect(cenv, monkeypatch):
    ar = cenv['ar']
    seen = {}

    def _spy(session):
        seen['ck'] = _ck_of(cenv['sdir'])
        raise Crash()
    monkeypatch.setattr(ar, '_log_agent_completion_body', _spy)
    with pytest.raises(Crash):
        ar._log_agent_completion(cenv['session'])
    rec = seen['ck']['session'].get('_pending_completion')
    assert rec and rec['kind'] == 'exit' and rec['id'], seen['ck']['session'].keys()
    assert seen['ck']['inflight_included'] is True


@pytest.mark.parametrize('point', ['after_stamp', 'after_row', 'after_wake', 'after_ack'])
def test_crash_on_either_side_of_each_completion_boundary_yields_exactly_one_row(cenv, monkeypatch, point):
    from mc import agent_shim_completion as comp
    ar = cenv['ar']
    real_body, real_notify, real_ack = (ar._log_agent_completion_body,
                                        ar._notify_spawner_unless_jobs_open, comp.ack)
    if point == 'after_stamp':
        monkeypatch.setattr(ar, '_log_agent_completion_body', lambda s: (_ for _ in ()).throw(Crash()))
    elif point == 'after_row':
        def _body_then_crash(s):
            real_body(s)
            raise Crash()
        monkeypatch.setattr(ar, '_log_agent_completion_body', _body_then_crash)
        monkeypatch.setattr(ar, '_notify_spawner_unless_jobs_open', lambda s, r: (_ for _ in ()).throw(Crash()))
    elif point == 'after_wake':
        monkeypatch.setattr(comp, 'ack', lambda s, **k: (_ for _ in ()).throw(Crash()))
    try:
        ar._log_agent_completion(cenv['session'])
    except Crash:
        pass
    monkeypatch.setattr(ar, '_log_agent_completion_body', real_body)
    monkeypatch.setattr(ar, '_notify_spawner_unless_jobs_open', real_notify)
    monkeypatch.setattr(comp, 'ack', real_ack)
    ck_before = _ck_of(cenv['sdir'])['session']
    if point == 'after_ack':
        assert '_pending_completion' not in ck_before          # acknowledged: nothing owed
    else:
        assert ck_before.get('_pending_completion'), 'the owed completion must be on disk'

    rep = _replay(cenv)                                         # the next boot
    rows = cenv['rows']()
    assert [r['session_id'] for r in rows] == ['cr1'], rows     # one row, never two, never none
    assert rows[0]['status'] == 'completed'
    assert cenv['wakes'], 'the spawner was never woken'
    assert '_pending_completion' not in _ck_of(cenv['sdir'])['session']
    if point == 'after_ack':
        assert rep.replayed == []
    # a second boot owes nothing and changes nothing
    again = _replay(cenv)
    assert again.replayed == [] and len(cenv['rows']()) == 1


def test_replay_that_fails_keeps_the_record_for_the_next_boot(cenv, monkeypatch):
    from mc import agent_shim_completion as comp
    ar = cenv['ar']
    comp.begin(cenv['session'], 'exit')
    monkeypatch.setattr(ar, '_replay_exit_completion', lambda s: False)
    rep = comp.replay_pending(cenv['root'], runners={'exit': ar._replay_exit_completion})
    assert 'cr1' in rep.retained and rep.replayed == []
    assert _ck_of(cenv['sdir'])['session'].get('_pending_completion')
    monkeypatch.setattr(ar, '_replay_exit_completion', lambda s: (_ for _ in ()).throw(RuntimeError('x')))
    rep = comp.replay_pending(cenv['root'], runners={'exit': ar._replay_exit_completion})
    assert 'cr1' in rep.retained
    assert _ck_of(cenv['sdir'])['session'].get('_pending_completion')


def test_a_wake_that_did_not_commit_is_not_acknowledged(cenv, monkeypatch):
    ar = cenv['ar']
    monkeypatch.setattr(ar, '_notify_agent_spawner', lambda *a, **k: False)   # outbox refused
    ar._log_agent_completion(cenv['session'])
    assert _ck_of(cenv['sdir'])['session'].get('_pending_completion'), 'wake still owed'


def test_a_pending_completion_session_is_not_adopted(cenv):
    from mc import agent_shim_completion as comp
    comp.begin(cenv['session'], 'exit')
    a = A.assess(cenv['sdir'])
    assert not a.ok and 'pending' in a.reason, a.reason


def test_completion_records_are_inert_with_the_flag_off(cenv, monkeypatch):
    from mc import agent_shim_completion as comp
    monkeypatch.setattr(A, 'enabled_now', lambda: False)
    assert comp.begin(cenv['session'], 'exit') is None
    assert '_pending_completion' not in cenv['session']


# ═══ 3. an older checkpoint never replaces a later one ═════════════════════════
def _advance(session):
    it = iter(session['proc'].stdout)
    next(it)
    return it


def test_older_snapshot_never_replaces_the_completion_checkpoint(tracker, tmp_path, flag_on):
    sdir, s = _shim_session(tracker, tmp_path, 'b3_order', '--lines', '3', '--interval', '0.01')
    assert wait_for(lambda: (sdir / 'exit.json').exists(), 20)
    it = _advance(s)                                      # a line is in flight: committed 0, offset > 0
    s['status'] = 'completed'
    assert A.force_checkpoint(s)                          # the completion checkpoint (offset past the line)
    newer = _ck_of(sdir)
    assert newer['consumed_offset'] > 0
    s['status'] = 'running'
    assert A.write_checkpoint(s, inflight=False) is False   # the older view (committed 0) is dropped
    now = _ck_of(sdir)
    assert now['consumed_offset'] == newer['consumed_offset'] and now['session']['status'] == 'completed'
    del it


def test_concurrent_old_writer_cannot_land_after_the_new_one(tracker, tmp_path, monkeypatch, flag_on):
    sdir, s = _shim_session(tracker, tmp_path, 'b3_race', '--lines', '3', '--interval', '0.01')
    assert wait_for(lambda: (sdir / 'exit.json').exists(), 20)
    s['proc'].stdout.offset = 120                         # a line in flight, no boundary held
    in_old_write, release = threading.Event(), threading.Event()
    real = A.write_json_atomic
    calls = []

    def _paused(path, obj, *a, **k):
        calls.append(obj['consumed_offset'])
        if len(calls) == 1:                               # the OLD writer, mid-replace
            in_old_write.set()
            release.wait(10)
        return real(path, obj, *a, **k)
    monkeypatch.setattr(A, 'write_json_atomic', _paused)

    old = threading.Thread(target=lambda: A.write_checkpoint(s, inflight=False))
    old.start()
    assert in_old_write.wait(10)
    new_result = []
    s_new = dict(s, status='completed')
    new = threading.Thread(target=lambda: new_result.append(A.write_checkpoint(s_new, inflight=True)))
    new.start()
    time.sleep(0.3)                                       # the new writer must be waiting, not racing
    release.set()
    old.join(10)
    new.join(10)
    final = _ck_of(sdir)
    assert final['session']['status'] == 'completed' and final['consumed_offset'] == 120, final


def test_checkpoint_waits_for_the_reader_boundary(tracker, tmp_path, monkeypatch):
    sdir, s = _shim_session(tracker, tmp_path, 'b3_bound', '--lines', '3', '--interval', '0.01')
    assert wait_for(lambda: (sdir / 'exit.json').exists(), 20)
    monkeypatch.setattr(A, 'BOUNDARY_WAIT_S', 0.2)
    it = _advance(s)                                      # the reader holds the boundary mid-line
    out = []
    t = threading.Thread(target=lambda: out.append(A.write_checkpoint(s, inflight=False)))
    t.start()
    t.join(5)
    assert out == [False], 'a checkpoint must not snapshot state while a line is being applied'
    list(it)                                              # all lines applied: boundary released
    t = threading.Thread(target=lambda: out.append(A.write_checkpoint(s, inflight=False)))
    t.start()
    t.join(5)
    assert out == [False, True]


# ═══ 4. unsupported reader paths are not adopted and stay blockers ═════════════
def test_the_production_registry_is_empty():
    assert A.RESTORABLE_READER_PATHS == frozenset()


def _mid_block_session(extra=None):
    s = {'status': 'running', 'session_id': 'x', 'project_id': 'p', 'mode': 'A',
         'log_lines': {'lines': ['```mc:question', '{"questions": [{"header": "Pick"'], 'ts': [1, 2],
                       'dropped': 0}}
    s.update(extra or {})
    return s


def test_protocol_output_checkpoint_is_not_adopted(tracker, tmp_path):
    # a checkpoint taken in the middle of an mc:question block: the reader's
    # half-read block lives in a local, so a fresh reader would lose the question
    sdir = _launch(tracker, tmp_path, 'b4_proto')
    _write_ck(sdir, consumed_offset=0, session=_mid_block_session({'session_id': 'b4_proto'}))
    a = A.assess(sdir)
    assert not a.ok and 'does not checkpoint' in a.reason, a.reason
    rep = A.claim_all(tmp_path, cfg=ON)
    assert rep.claimed == [] and A.protected_pids() == frozenset()


def test_end_of_output_restart_is_not_adopted(tracker, tmp_path):
    # the answer was read, the CLI exited 0 during downtime: an adopted reader
    # would see EOF with an empty turn buffer and report "ended with no reply"
    sdir = _launch(tracker, tmp_path, 'b4_eof', '--lines', '3', '--interval', '0.01')
    assert wait_for(lambda: (sdir / 'exit.json').exists(), 20)
    size = (sdir / 'out.log').stat().st_size
    _write_ck(sdir, consumed_offset=size, session={
        'status': 'running', 'session_id': 'b4_eof', 'project_id': 'p', 'mode': 'A',
        'log_lines': {'lines': ['answer'], 'ts': [1], 'dropped': 0}})
    a = A.assess(sdir)
    assert not a.ok and 'does not checkpoint' in a.reason, a.reason
    assert A.claim_all(tmp_path, cfg=ON).claimed == []


@pytest.mark.parametrize('engine,mode', [('qwen', 'A'), ('claude', 'B'), ('gemini', 'A'), ('claude', 'A')])
def test_every_reader_path_is_refused_until_it_is_registered(tracker, tmp_path, engine, mode):
    sdir = _launch(tracker, tmp_path, f'b4_{engine}{mode}', engine=engine)
    ck = _write_ck(sdir)
    ck['session']['mode'] = mode
    (sdir / A.CHECKPOINT_NAME).write_text(json.dumps(ck), encoding='utf-8')
    a = A.assess(sdir)
    assert not a.ok and f'{engine}:{mode}' in a.reason, a.reason


def test_unsupported_reader_path_stays_a_restart_blocker(tracker, tmp_path):
    sdir, s = _shim_session(tracker, tmp_path, 'b4_block')
    assert A.write_checkpoint(s)                          # fresh checkpoint, live shim, flag on...
    assert A.is_adoptable_session(s, cfg=ON) is False     # ...but its reader path is unsupported
    assert A.should_spare(s, cfg=ON) is False
    blockers = {'active_sessions': [{'session_id': 'b4_block', 'project_id': 'p'}],
                'active_hiveminds': []}
    out = A.partition_blockers(blockers, {'b4_block': s}, cfg=ON)
    assert [r['session_id'] for r in out['active_sessions']] == ['b4_block']
    assert out['adoptable_sessions'] == []


def test_a_registered_reader_path_is_adoptable_again(tracker, tmp_path, restorable):
    sdir, s = _shim_session(tracker, tmp_path, 'b4_ok')
    assert A.write_checkpoint(s)
    assert A.is_adoptable_session(s, cfg=ON) is True


# ═══ 5. Power off stops every session; only a restart may spare ════════════════
@pytest.fixture
def stopper(monkeypatch):
    import server  # noqa: F401
    import mc.blueprints.system_routes as sr
    stopped = []
    sessions = {'adopt1': {'session_id': 'adopt1', 'status': 'running', 'project_id': 'p'},
                'plain1': {'session_id': 'plain1', 'status': 'running', 'project_id': 'p'}}
    monkeypatch.setattr(sr, 'agent_sessions', sessions)
    monkeypatch.setattr(sr, '_adoptable_sessions_to_spare', lambda: {'adopt1'})
    monkeypatch.setattr(sr, 'get_manager_for_session', lambda sid: None)
    monkeypatch.setattr(sr, 'get_manager', lambda pid: None)
    monkeypatch.setattr(sr, '_stop_session', lambda sess, sid: stopped.append(sid))
    monkeypatch.setattr(sr, '_kill_proc_background', lambda p: None)
    monkeypatch.setattr(A, 'write_checkpoint', lambda *a, **k: True)
    return sr, stopped


def test_power_off_helper_stops_adoptable_agents_too(stopper):
    sr, stopped = stopper
    sr._stop_all_sessions_for_restart(grace_seconds=0)
    assert sorted(stopped) == ['adopt1', 'plain1']


def test_a_restart_with_a_replacement_server_spares_only_adoptable_agents(stopper):
    sr, stopped = stopper
    sr._stop_all_sessions_for_restart(grace_seconds=0, spare_adoptable=True)
    assert stopped == ['plain1']


def test_the_power_off_caller_does_not_ask_to_spare():
    import inspect
    import mc.blueprints.system_routes as sr
    src = inspect.getsource(sr._perform_server_shutdown_async)
    assert '_stop_all_sessions_for_restart()' in src and 'spare_adoptable' not in src
