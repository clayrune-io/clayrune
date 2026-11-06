"""MC-1150 P4 unit tests: projection, checkpoint, assessment, claim/reaper,
restart-blocker partition. Real shims (the stand-in child from
tests/agent_shim_standin.py) stand behind every assessment case; nothing here
touches :5199. The end-to-end restart tests are in test_agent_shim_adopt_e2e.py.

Run:  .venv/Scripts/python.exe -m pytest tests/test_agent_shim_adopt.py -q
"""
from __future__ import annotations

import importlib
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

from tests.test_agent_shim import Tracker, child_argv, wait_for  # noqa: E402

ON = {'agent_survive_restart_enabled': True}
OFF = {'agent_survive_restart_enabled': False}


@pytest.fixture
def tracker():
    t = Tracker()
    yield t
    t.reap()


@pytest.fixture(autouse=True)
def _clean_registry():
    A.reset_for_tests()
    yield
    A.reset_for_tests()


LIVE = ('--lines', '900', '--interval', '0.2')    # a CLI that is still working for ~3 minutes


def _man(sdir):
    return C.read_json(Path(sdir) / 'shim.json') or {}


def _launch(tracker, root, sid, *flags, engine='qwen'):
    flags = flags or LIVE
    res = C.launch(root, sid, argv=child_argv(*flags), cwd=None, mode='oneshot',
                   engine=engine, tick_s=0.1)
    assert res.ok, res.error
    sdir = C.session_dir(root, sid)
    tracker.add_manifest(sdir)
    return sdir


def _write_ck(sdir, **over):
    m = _man(sdir)
    ck = {'schema': A.SCHEMA, 'session_id': Path(sdir).name, 'launch_id': m.get('launch_id'),
          'written_epoch': time.time(), 'consumed_offset': 0, 'inflight_included': False,
          'session': {'status': 'running', 'session_id': Path(sdir).name, 'project_id': 'p',
                      'mode': 'A'}}
    ck.update(over)
    (Path(sdir) / A.CHECKPOINT_NAME).write_text(json.dumps(ck), encoding='utf-8')
    return ck


def _edit(path, **kv):
    d = json.loads(Path(path).read_text(encoding='utf-8'))
    d.update(kv)
    Path(path).write_text(json.dumps(d), encoding='utf-8')


# ───────────────────────────── projection ────────────────────────────────────
def _real_mode_a_dict(proc=None):
    """The key set of the Mode A session dict `_revive_*` builds
    (agent_routes.py, `session = {` after 'Agent revived (A)'), plus the three
    non-JSON values a live session carries: a Lock, a TimestampedLines, and
    midturn_rollover's `_mt_pending_tools` set."""
    return {
        'proc': proc, 'status': 'running', 'task': 'ship it',
        'log_lines': TimestampedLines(['a', 'b', 'c'], ts=[1.0, 2.0, 3.0]),
        'log_epoch': 1730000000.5, 'started_at': '2026-11-05T17:00:00', 'session_id': 'sid1',
        'project_id': 'mission_control', 'mode': 'A', 'last_output_time': 1730000001.0,
        'last_status_change_time': 1730000001.0, 'guardian_state': None, 'recovery_attempts': 0,
        'last_recovery_time': 0, 'pending_recovery_message': None, 'circuit_breaker_tripped': False,
        'claude_session_id': 'cc-123', '_resume_id': 'cc-123', '_agent_cwd': 'C:/x',
        '_dispatch_time': 1730000000.0, 'usage': {'input_tokens': 5, 'cache': {'read': 2}},
        'cost_usd': 0.42, 'num_turns': 3, '_system_prompt': 'be brief', 'character': 'builder',
        'incognito': False, 'source': 'dispatch', 'job_shape': None, 'trigger_type': 'manual',
        'trigger_id': '', 'human_joined': False, 'provider': 'claude', 'model': 'm',
        'agent_model': 'm', 'pinned_model': 'm', 'requested_effort': None,
        'model_auto_requested': False, '_notify_session': 'parent1', '_spawned_by': 'parent1',
        '_delegation_turn': 2, '_delivery_generation': 1, 'project_generation': 4,
        '_notify_workflow': None, 'stdin_lock': threading.Lock(),
        '_mt_pending_tools': {'toolu_2', 'toolu_1'},
        'on_done': (lambda: None),                       # a callable can never be projected
    }


def test_projection_round_trip_of_a_real_session_dict():
    s = _real_mode_a_dict(proc=object())
    p = A.project_session(s)
    wire = json.loads(json.dumps(p))                     # what checkpoint.json actually holds
    assert 'proc' not in wire and 'stdin_lock' not in wire
    assert wire['_skipped'] == ['on_done']                # lossy keys are named, not silent
    back = A.restore_session(wire)
    for k, v in s.items():
        if k in ('proc', 'stdin_lock', 'log_lines', 'on_done'):
            continue
        assert back[k] == v, f'{k}: {back.get(k)!r} != {v!r}'
    assert back['_mt_pending_tools'] == {'toolu_1', 'toolu_2'}
    assert isinstance(back['_mt_pending_tools'], set)
    assert list(back['log_lines']) == ['a', 'b', 'c']
    assert isinstance(back['log_lines'], TimestampedLines)
    assert list(back['log_lines'].ts) == [1.0, 2.0, 3.0]


def test_restore_recreates_the_mode_b_stdin_lock_and_keeps_the_log_tail_bounded():
    s = _real_mode_a_dict()
    s['mode'] = 'B'
    s['log_lines'] = TimestampedLines([f'l{i}' for i in range(50)])
    wire = json.loads(json.dumps(A.project_session(s, log_tail=10)))
    assert wire['log_lines']['dropped'] == 40 and len(wire['log_lines']['lines']) == 10
    back = A.restore_session(wire)
    assert hasattr(back['stdin_lock'], 'acquire')         # Mode B writes under it
    assert list(back['log_lines'])[0] == 'l40'


def test_projection_of_an_unserializable_value_names_the_key():
    s = {'status': 'running', 'weird': object(), 'nan': float('nan'), 'ok': 1}
    p = A.project_session(s)
    assert p['ok'] == 1 and set(p['_skipped']) == {'weird', 'nan'}


# ───────────────────────────── assessment ────────────────────────────────────
def test_assess_running_session_is_adoptable_and_names_both_pids(tracker, tmp_path):
    sdir = _launch(tracker, tmp_path, 's_run')
    _write_ck(sdir)
    a = A.assess(sdir)
    assert a.ok, a.reason
    m = _man(sdir)
    assert sorted(a.protected_pids) == sorted([m['cli_pid'], m['shim_pid']])
    assert a.status is not None and a.status.cli_alive is True


def test_assess_cli_that_finished_during_downtime_is_adoptable_with_its_exit_record(tracker, tmp_path):
    sdir = _launch(tracker, tmp_path, 's_done', '--lines', '3', '--interval', '0.01')
    assert wait_for(lambda: (sdir / 'exit.json').exists(), 20)
    _write_ck(sdir)
    a = A.assess(sdir)
    assert a.ok, a.reason
    assert a.status is not None and a.status.exit_info is not None
    assert a.status.cli_alive is False


def test_assess_dead_cli_without_an_exit_record_is_refused(tracker, tmp_path):
    sdir = _launch(tracker, tmp_path, 's_dead', '--lines', '1', '--interval', '0.01')
    assert wait_for(lambda: (sdir / 'exit.json').exists(), 20)
    wait_for(lambda: shim.process_state(*[_man(sdir)[k] for k in ('shim_pid', 'shim_ct')]) == 'gone', 20)
    (sdir / 'exit.json').unlink()                         # the shim died before writing it
    _write_ck(sdir)
    a = A.assess(sdir, sleep=lambda s: None)
    assert not a.ok and 'no exit record' in a.reason, a.reason


def test_assess_stale_pid_is_refused(tracker, tmp_path):
    sdir = _launch(tracker, tmp_path, 's_pid')
    _write_ck(sdir)
    m = _man(sdir)
    _edit(sdir / 'shim.json', cli_ct=m['cli_ct'] - 5000)    # pid now belongs to someone else
    a = A.assess(sdir, sleep=lambda s: None)
    assert not a.ok, 'a reused CLI pid must never be adopted'
    assert A.claim_all(tmp_path, cfg=ON).claimed == []


@pytest.mark.parametrize('where,edit,why', [
    ('spec.json', {'schema': 99}, 'spec schema'),
    ('shim.json', {'schema': 99}, 'shim.json'),
    ('spec.json', {'session_id': 'other'}, 'does not match its directory'),
    ('spec.json', {'engine': 'mystery'}, 'not supported'),
    ('spec.json', {'launch_id': 'different'}, 'different launch'),
])
def test_assess_schema_skew_and_mismatched_manifests_are_refused(tracker, tmp_path, where, edit, why):
    sdir = _launch(tracker, tmp_path, 's_skew')
    _write_ck(sdir)
    _edit(sdir / where, **edit)
    a = A.assess(sdir, sleep=lambda s: None)
    assert not a.ok and why in a.reason, a.reason


@pytest.mark.parametrize('over,why', [
    ({'schema': 7}, 'checkpoint schema'),
    ({'launch_id': 'earlier-turn'}, 'earlier turn'),
    ({'consumed_offset': 10 ** 9}, 'past the end'),
    ({'consumed_offset': -1}, 'non-negative'),
    ({'session': {'status': 'idle', 'session_id': 's_ck'}}, 'not in flight'),
    ({'session': 'nope'}, 'does not describe'),
])
def test_assess_bad_or_stale_checkpoint_is_refused(tracker, tmp_path, over, why):
    sdir = _launch(tracker, tmp_path, 's_ck')
    _write_ck(sdir, **over)
    a = A.assess(sdir, sleep=lambda s: None)
    assert not a.ok and why in a.reason, a.reason


def test_assess_without_a_checkpoint_or_with_a_torn_one_is_refused(tracker, tmp_path):
    sdir = _launch(tracker, tmp_path, 's_none')
    assert 'no readable checkpoint' in A.assess(sdir).reason
    (sdir / A.CHECKPOINT_NAME).write_text('{"schema": 1, "sess', encoding='utf-8')
    assert 'no readable checkpoint' in A.assess(sdir).reason


def test_assess_never_raises_on_garbage(tmp_path):
    d = tmp_path / 'junk'
    d.mkdir()
    (d / 'spec.json').write_bytes(b'\xff\xfe\x00')
    assert A.assess(d).ok is False
    assert A.assess(tmp_path / 'absent').ok is False


# ───────────────────────────── claim / flag off ──────────────────────────────
def test_claim_all_protects_both_pids_and_start_all_is_not_needed_for_protection(tracker, tmp_path):
    sdir = _launch(tracker, tmp_path, 's_claim')
    _write_ck(sdir)
    rep = A.claim_all(tmp_path, cfg=ON)
    assert rep.claimed == ['s_claim']
    m = _man(sdir)
    assert A.protected_pids() == frozenset({m['cli_pid'], m['shim_pid']})


def test_claim_all_flag_off_touches_nothing(tracker, tmp_path):
    sdir = _launch(tracker, tmp_path, 's_off')
    _write_ck(sdir)
    rep = A.claim_all(tmp_path, cfg=OFF)
    assert rep.claimed == [] and rep.note == 'flag off'
    assert A.protected_pids() == frozenset()


def test_claim_all_reports_why_a_directory_was_skipped(tracker, tmp_path):
    _launch(tracker, tmp_path, 's_skip')                  # no checkpoint
    rep = A.claim_all(tmp_path, cfg=ON)
    assert rep.claimed == [] and 'no readable checkpoint' in rep.skipped['s_skip']
    assert A.protected_pids() == frozenset()


# ─────────────────────────────── reaper ──────────────────────────────────────
@pytest.fixture
def pl(tmp_data_dir):
    import server
    importlib.reload(server)
    import mc.process_ledger as pl
    return pl


def _reaper_harness(pl, tmp_path, monkeypatch, pids):
    led = tmp_path / 'ledger.json'
    monkeypatch.setattr(pl, '_PID_LEDGER_PATH', led)
    killed = []
    monkeypatch.setattr(pl, '_pid_is_alive', lambda p: True)
    monkeypatch.setattr(pl, '_proc_identity', lambda p: ('claude.exe', 1000.0))
    monkeypatch.setattr(pl, '_kill_pid', lambda p, tree=False: (killed.append(p), True)[1])
    led.write_text(json.dumps({'mc_pid': 555, 'children': [
        {'pid': p, 'os_image': 'claude.exe', 'create_time': 1000.0} for p in pids]}))
    return led, killed


def test_reaper_skips_protected_pids_and_still_reaps_the_rest(pl, tmp_path, monkeypatch):
    led, killed = _reaper_harness(pl, tmp_path, monkeypatch, [101, 102, 103, 104])
    pl._reap_prior_instance_strays(protected=frozenset({101, 103}))
    assert sorted(killed) == [102, 104]                    # non-adoptable strays still reaped
    assert json.loads(led.read_text())['children'] == []


def test_reaper_without_protected_behaves_as_before(pl, tmp_path, monkeypatch):
    _led, killed = _reaper_harness(pl, tmp_path, monkeypatch, [101, 102])
    pl._reap_prior_instance_strays()
    assert sorted(killed) == [101, 102]


def test_reaper_end_to_end_with_a_real_claim_protects_the_shim_run_agent(tracker, tmp_path, pl,
                                                                         monkeypatch):
    sdir = _launch(tracker, tmp_path / 'shims', 's_reap')
    _write_ck(sdir)
    m = _man(sdir)
    A.claim_all(tmp_path / 'shims', cfg=ON)
    _led, killed = _reaper_harness(pl, tmp_path, monkeypatch, [m['cli_pid'], m['shim_pid'], 424242])
    pl._reap_prior_instance_strays(protected=A.protected_pids())
    assert killed == [424242]


# ───────────────────────────── blockers ──────────────────────────────────────
def _live_session(tracker, root, sid, *, status='running', ck_age=0.0):
    sdir = _launch(tracker, root, sid)
    proc = C.ShimProc(sdir, client=None, start_offset=0, manifest=_man(sdir))
    s = {'session_id': sid, 'status': status, 'project_id': 'p', 'task': 't', 'proc': proc,
         'log_lines': TimestampedLines(), 'mode': 'A'}
    assert A.write_checkpoint(s, now=time.time() - ck_age)
    return s


def test_blocker_partition_splits_adoptable_from_blocking(tracker, tmp_path):
    good = _live_session(tracker, tmp_path, 'g1')
    wedged = _live_session(tracker, tmp_path, 'w1', ck_age=120.0)     # stale checkpoint
    plain = {'session_id': 'plain1', 'status': 'running', 'project_id': 'p', 'proc': object()}
    sessions = {'g1': good, 'w1': wedged, 'plain1': plain}
    blockers = {'active_sessions': [{'session_id': k, 'project_id': 'p'} for k in sessions],
                'active_hiveminds': []}
    out = A.partition_blockers(blockers, sessions, cfg=ON)
    assert [r['session_id'] for r in out['adoptable_sessions']] == ['g1']
    assert sorted(r['session_id'] for r in out['active_sessions']) == ['plain1', 'w1']
    assert out['active_hiveminds'] == [] and out['adoptable_hiveminds'] == []


def test_blocker_partition_hivemind_blocks_unless_every_running_worker_is_adoptable(tracker, tmp_path):
    good = _live_session(tracker, tmp_path, 'hg')
    plain = {'session_id': 'hp', 'status': 'running', 'project_id': 'p', 'proc': object()}
    sessions = {'hg': good, 'hp': plain}
    blockers = {'active_sessions': [{'session_id': 'hg'}, {'session_id': 'hp'}],
                'active_hiveminds': [{'hivemind_id': 'h_ok'}, {'hivemind_id': 'h_mixed'}]}
    out = A.partition_blockers(blockers, sessions, cfg=ON,
                               hivemind_workers={'h_ok': ['hg'], 'h_mixed': ['hg', 'hp']})
    assert [r['hivemind_id'] for r in out['adoptable_hiveminds']] == ['h_ok']
    assert [r['hivemind_id'] for r in out['active_hiveminds']] == ['h_mixed']


def test_adoptable_needs_fresh_checkpoint_live_shim_and_flag(tracker, tmp_path):
    s = _live_session(tracker, tmp_path, 'fresh')
    assert A.is_adoptable_session(s, cfg=ON)
    assert not A.is_adoptable_session(s, cfg=OFF)
    assert not A.is_adoptable_session(s, cfg=ON, now=time.time() + 3600)   # checkpoint has gone stale
    A.forget('fresh')
    assert not A.is_adoptable_session(s, cfg=ON)                          # never checkpointed this boot
    assert not A.is_adoptable_session({'session_id': 'x', 'proc': object()}, cfg=ON)


def test_adoptable_is_false_once_the_shim_is_gone(tracker, tmp_path):
    s = _live_session(tracker, tmp_path, 'gone1')
    m = s['proc'].manifest
    shim.kill_tree(int(m['cli_pid']))
    assert wait_for(lambda: shim.process_state(m['shim_pid'], m['shim_ct']) == 'gone', 20)
    assert not A.is_adoptable_session(s, cfg=ON)


def test_stop_all_spares_only_the_adoptable_running_session(tracker, tmp_path):
    good = _live_session(tracker, tmp_path, 'sp1')
    idle = _live_session(tracker, tmp_path, 'sp2', status='idle')
    plain = {'session_id': 'sp3', 'status': 'running', 'proc': object()}
    assert A.should_spare(good, cfg=ON)
    assert not A.should_spare(idle, cfg=ON)
    assert not A.should_spare(plain, cfg=ON)
    assert not A.should_spare(good, cfg=OFF)


def test_restart_blockers_payload_is_unchanged_with_the_flag_off(tmp_path):
    # `_get_active_restart_blockers` returns the bare two-key payload when the
    # flag is off: nothing from this module is consulted.
    import mc.blueprints.system_routes as sr
    out = sr._get_active_restart_blockers()
    assert set(out) == {'active_sessions', 'active_hiveminds'}


def test_checkpoint_offset_is_the_committed_line_not_the_in_flight_one(tracker, tmp_path):
    sdir = _launch(tracker, tmp_path, 'off1', '--lines', '3', '--interval', '0.01')
    assert wait_for(lambda: (sdir / 'exit.json').exists(), 20)
    proc = C.ShimProc(sdir, client=None, start_offset=0, manifest=_man(sdir))
    s = {'session_id': 'off1', 'status': 'running', 'proc': proc, 'log_lines': TimestampedLines()}
    it = iter(proc.stdout)
    first = next(it)
    assert first
    held = A.build_checkpoint(s, inflight=False)
    forced = A.build_checkpoint(s, inflight=True)
    assert held is not None and forced is not None
    assert held['consumed_offset'] == 0                     # line 1 not yet fully processed
    assert forced['consumed_offset'] == proc.stdout.offset > 0
    next(it)
    after = A.build_checkpoint(s, inflight=False)
    assert after is not None and after['consumed_offset'] == forced['consumed_offset']


def test_reader_counters_only_publish_for_a_shim_run_session():
    plain = {'session_id': 'c1'}
    cost, turns = A.reader_counters(plain, object())
    assert cost == {} and turns == {} and '_survive_proc_cost' not in plain
    adopted = {'session_id': 'c2', '_adopt_proc_cost': {'in': 4}, '_adopt_proc_turns': {'t': 1}}
    proc = C.ShimProc.__new__(C.ShimProc)
    cost, turns = A.reader_counters(adopted, proc)
    assert cost == {'in': 4} and turns == {'t': 1}
    assert adopted['_survive_proc_cost'] is cost and '_adopt_proc_cost' not in adopted
    assert A.take_skip_drain({'_adopt_skip_drain': True}) is True
    assert A.take_skip_drain({}) is False
