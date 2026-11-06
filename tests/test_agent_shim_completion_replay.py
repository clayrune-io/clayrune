"""MC-1150 P4: regression tests for the two defects Fenn found re-reviewing blocker 2
on 81841916 (docs/_journal/ebbb15a7-fenn-p4-rereview.md). Both fail on 81841916.

  P1  boot replay acknowledged a workflow completion once its callback THREAD had
      started; workflow startup adoption then interrupted the run and the late
      success was refused -- the result was lost for good.
  P2  an outbox event that was already committed (identical) made replay treat
      the wake as unsent and keep the pending record forever.

Nothing here stubs the thing under test: replay runs the real `agent_routes`
runners, the notifier writes to a real SQLite `DeliveryStore`, and the workflow
side is the real `mc.workflows` over a file-backed run store (the same `wf`
fixture tests/test_workflows.py uses; only its agent *dispatch* is a recorder).

Run:  .venv/Scripts/python.exe -m pytest tests/test_agent_shim_completion_replay.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mc import agent_shim_adopt as A  # noqa: E402
from mc import agent_shim_client as C  # noqa: E402
from mc import agent_shim_completion as comp  # noqa: E402
from mc.blueprints import agent_routes as ar  # noqa: E402
from mc.delegation_delivery import DeliveryStore, drain_once, event_id_for_turn  # noqa: E402

from tests.test_workflows import _agent, _doc, _edge, wf  # noqa: E402,F401

PID = 'p1'


@pytest.fixture(autouse=True)
def _clean_registry():
    A.reset_for_tests()
    yield
    A.reset_for_tests()


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / 'shims'
    sdir = root / 'probe'
    sdir.mkdir(parents=True)
    (sdir / 'out.log').write_bytes(b'answer\n')
    manifest = dict(schema=1, launch_id='test-launch', cli_pid=99999990,
                    shim_pid=99999991, cli_ct=1, shim_ct=1)
    proc = C.ShimProc(sdir, manifest=manifest)
    proc.stdout.offset = proc.stdout.committed = 7
    s = dict(session_id='probe', project_id=PID, status='completed', mode='A',
             proc=proc, log_lines=['answer'], task='replay regression', provider='qwen',
             started_at='2026-11-05T10:00:00', _notify_session='parent', _delegation_turn=1)
    monkeypatch.setattr(A, 'enabled_now', lambda: True)
    (tmp_path / 'projects').mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', tmp_path / 'projects')
    monkeypatch.setattr(ar, 'load_project', lambda pid: {'id': PID, 'project_path': str(tmp_path)})
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_write_session_memory', lambda *a, **k: True)
    monkeypatch.setattr(ar, '_compute_code_delta',
                        lambda s: {'status': 'ok', 'added': 1, 'deleted': 0, 'base_commit': 'b'})
    store = DeliveryStore(tmp_path / 'delivery.sqlite')
    monkeypatch.setattr(ar, '_delivery_store', store)
    return SimpleNamespace(root=root, sdir=sdir, s=s, store=store,
                           ck=lambda: A.read_checkpoint(sdir))


def _two_step_run(wf_):
    doc = wf_.m.create_workflow(_doc('replay', [_agent('first'), _agent('second')],
                                     edges=[_edge('first', 'second')]))
    run = wf_.m.start_run(doc['id'])
    return run, run['steps']['first']['session_id']


def _drain(env_, sent):
    drain_once(env_.store, send_outbox=lambda row: sent.append(row['event_id']),
               process_inbox=lambda row: {})


# ── P1: a workflow result must be durable before the pending record is cleared ──

@pytest.mark.parametrize('kind,mode,status', [('turn', 'B', 'idle'), ('exit', 'A', 'completed')])
def test_boot_replay_records_the_workflow_step_before_adoption_can_interrupt_it(
        env, wf, monkeypatch, kind, mode, status):
    run, sid = _two_step_run(wf)
    s = env.s
    s.update(session_id=sid, mode=mode, status=status, provider='claude', _notify_session='',
             _notify_workflow={'run_id': run['id'], 'step': 'first'})
    comp.begin(s, kind)                       # the server dies here: record on disk, no wake yet
    # What boot's `reconcile pending agent_log` leaves for a Mode B turn / a crashed exit:
    wf.agent_logs[PID] = [{'session_id': sid, 'status': 'interrupted', 'summary': 'server restarted'}]

    # A thread-started callback may not have run by the time adoption starts. If replay
    # still uses one it queues here, and the late run is what the old code lost.
    late = []

    class DelayedThread:
        def __init__(self, target, **kw):
            self.target = target

        def start(self):
            late.append(self.target)

    monkeypatch.setattr(ar.threading, 'Thread', DelayedThread)

    rep = comp.replay_pending(env.root)                 # server.py boot order: replay ...
    wf.m.adopt_on_startup()                             # ... then workflow adoption
    for cb in late:
        cb()

    after = wf.m.get_run(run['id'])
    assert after['status'] == 'running', after.get('error')
    assert after['steps']['first']['status'] == 'completed'
    assert after['steps']['second']['status'] == 'running'          # adoption advanced it
    assert len(wf.dispatch.calls) == 2
    assert rep.replayed == ['probe'] and comp.KEY not in env.ck()['session']


def test_a_workflow_step_that_cannot_be_recorded_keeps_the_pending_record(env, wf, monkeypatch):
    run, sid = _two_step_run(wf)
    env.s.update(session_id=sid, mode='B', status='idle', provider='claude', _notify_session='',
                 _notify_workflow={'run_id': run['id'], 'step': 'first'})
    comp.begin(env.s, 'turn')
    real_write = wf.m._write_run
    monkeypatch.setattr(wf.m, '_write_run', lambda r: (_ for _ in ()).throw(OSError('disk full')))

    rep = comp.replay_pending(env.root)
    assert rep.replayed == [] and 'probe' in rep.retained
    assert env.ck()['session'].get(comp.KEY)                         # still owed
    assert wf.m.get_run(run['id'])['steps']['first']['status'] == 'running'

    monkeypatch.setattr(wf.m, '_write_run', real_write)              # next boot: disk is back
    rep = comp.replay_pending(env.root)
    assert rep.replayed == ['probe'] and comp.KEY not in env.ck()['session']
    assert wf.m.get_run(run['id'])['steps']['first']['status'] == 'completed'
    assert comp.replay_pending(env.root).replayed == []               # nothing left owed


def test_replay_of_a_step_already_recorded_is_acknowledged_without_advancing_twice(env, wf):
    run, sid = _two_step_run(wf)
    env.s.update(session_id=sid, mode='B', status='idle', provider='claude', _notify_session='',
                 _notify_workflow={'run_id': run['id'], 'step': 'first'})
    comp.begin(env.s, 'turn')
    # The live callback landed (and advanced) before the crash; only the ack was lost.
    wf.m.on_agent_step_complete(run['id'], 'first', PID, sid, 'idle', 'LIVE')
    assert len(wf.dispatch.calls) == 2

    rep = comp.replay_pending(env.root)
    assert rep.replayed == ['probe']
    after = wf.m.get_run(run['id'])
    assert after['steps']['first']['output'] == 'LIVE' and len(wf.dispatch.calls) == 2


def test_on_agent_step_complete_reports_what_became_of_the_completion(wf):
    run, sid = _two_step_run(wf)
    done = lambda rid, **kw: wf.m.on_agent_step_complete(rid, 'first', PID, sid, 'idle', 'x', **kw)  # noqa: E731
    assert done('wf-no-such-run') == 'ignored'
    assert done(run['id'], advance=False) == 'recorded'
    assert len(wf.dispatch.calls) == 1                                  # advance=False starts nothing
    assert done(run['id']) == 'ignored'                                 # already past this step
    other = wf.m.start_run(wf.m.create_workflow(_doc('b', [_agent('first')]))['id'])
    assert wf.m.on_agent_step_complete(other['id'], 'first', PID, 'sess-other', 'error', 'boom') == 'ignored' \
        or wf.m.get_run(other['id'])['status'] == 'failed'


# ── P2: an identical outbox event already committed IS the wake ──────────────────

def test_an_already_committed_wake_lets_replay_acknowledge(env):
    comp.begin(env.s, 'exit')
    ar._log_agent_completion_body(env.s)         # crash before the ack: wake enqueued, row written
    event_id = event_id_for_turn('probe', 1)
    assert env.store.status('outbox', event_id, PID) is not None
    sent = []
    _drain(env, sent)                            # ... and the wake was even delivered

    first = comp.replay_pending(env.root)
    _drain(env, sent)
    assert first.replayed == ['probe'] and not first.retained
    assert comp.KEY not in env.ck()['session']   # acknowledged on the first replay
    assert comp.replay_pending(env.root).replayed == []
    _drain(env, sent)
    assert sent == [event_id]                    # still exactly one wake
    assert len(ar._load_agent_log(PID)) == 1     # still exactly one agent-log row


def test_a_refused_wake_is_not_a_wake(env):
    comp.begin(env.s, 'exit')
    env.store.revoke_session(PID, 'parent')      # the parent is gone: enqueue is refused
    rep = comp.replay_pending(env.root)
    assert rep.replayed == [] and 'probe' in rep.retained
    assert env.ck()['session'].get(comp.KEY)
    assert env.store.status('outbox', event_id_for_turn('probe', 1), PID) is None


def test_a_conflicting_event_identity_is_not_a_wake(env):
    comp.begin(env.s, 'exit')
    other = dict(env.s, task='a different task')           # same child + turn, other payload
    ar._notify_agent_spawner(PID, 'parent', other, 'someone else')
    rep = comp.replay_pending(env.root)
    assert rep.replayed == [] and 'probe' in rep.retained
    assert env.ck()['session'].get(comp.KEY)


def test_notifier_result_tells_committed_duplicate_and_refused_apart(env):
    child = dict(env.s)
    assert ar._notify_agent_spawner(PID, 'parent', child, 'r') is True          # new
    assert ar._notify_agent_spawner(PID, 'parent', child, 'r') is True          # identical, present
    env.store.revoke_session(PID, 'parent')
    assert ar._notify_agent_spawner(PID, 'parent', dict(child, session_id='c2'), 'r') is False
    assert ar._notify_agent_spawner(PID, '', child, 'r') is False               # no parent


def test_store_enqueue_keeps_its_bool_contract_and_adds_the_outcome(env):
    pl = {'message': 'm'}
    assert env.store.enqueue_outcome('e1', PID, 'parent', pl) == 'committed'
    assert env.store.enqueue_outcome('e1', PID, 'parent', pl) == 'duplicate'
    assert env.store.enqueue_outcome('', PID, 'parent', pl) == 'refused'
    with pytest.raises(ValueError):
        env.store.enqueue_outcome('e1', PID, 'parent', {'message': 'other'})
    assert env.store.enqueue('e2', PID, 'parent', pl) is True
    assert env.store.enqueue('e2', PID, 'parent', pl) is False                  # unchanged
    env.store.revoke_event(PID, 'e3')
    assert env.store.enqueue_outcome('e3', PID, 'parent', pl) == 'refused'


# ── OFF semantics (Dave decided A): replay still honours what is already owed ────

def test_flag_off_still_replays_a_completion_recorded_while_on(env, monkeypatch):
    comp.begin(env.s, 'exit')
    monkeypatch.setattr(A, 'enabled_now', lambda: False)
    rep = comp.replay_pending(env.root)
    assert rep.replayed == ['probe'] and not rep.retained
    assert len(ar._load_agent_log(PID)) == 1
    assert env.store.status('outbox', event_id_for_turn('probe', 1), PID) is not None
    assert comp.KEY not in env.ck()['session']
