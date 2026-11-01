"""HR-4a: exact candidates, fake provider launches and durable vetoes."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mc import agent_runtime, characters, hivemind_reviewer as reviewer, state
from mc import hivemind_worker as worker, review_lineage
from mc.blueprints import agent_routes as ar, hivemind_routes as hm
from mc.hivemind_review import Candidate, ReviewStore
from mc.merge_review_gate import ReviewRefused
from tests.test_hivemind_routes import client  # noqa: F401
from tests.test_hivemind_roster import agents_dir, PROVIDERS  # noqa: F401
from tests.test_hivemind_roster_worker import rig  # noqa: F401


class FakeTimer:
    created = []

    def __init__(self, interval, function, args):
        self.interval, self.function, self.args = interval, function, args
        self.daemon = False
        self.started = False
        self.created.append(self)

    def start(self):
        self.started = True


class FakeProc:
    pid = 990301

    def __init__(self):
        self.rc = None

    def poll(self):
        return self.rc


@pytest.fixture
def review_rig(rig, monkeypatch):
    rig.manifest['config'].update(reviewer_character='global:worker-codex')
    rig.manifest['status'] = 'active'
    hm._hm_save_manifest('roster1', rig.manifest)
    candidate = Candidate('thm', 7, 'roster1', 'ws1', 1, 1,
                          'a' * 40, 'clayrune/agent/worker1', 'b' * 40,
                          'c' * 40, 'd' * 40, ('worker1', 'earlier-worker'))
    rig.candidate = candidate
    rig.clock = [datetime.now(timezone.utc).timestamp()]
    monkeypatch.setattr(reviewer, 'time', SimpleNamespace(time=lambda: rig.clock[0]))
    monkeypatch.setattr(reviewer, 'threading', SimpleNamespace(Timer=FakeTimer))
    FakeTimer.created = []
    rig.lineage = {'attributed-parent': []}

    def ancestry(sid):
        if sid not in rig.lineage:
            raise review_lineage.LineageUnreadable(f'unknown {sid}')
        return rig.lineage[sid]

    monkeypatch.setattr(review_lineage, '_production_spawner_of', lambda: ancestry)
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *a, **k: '')
    rig.git_calls = []

    def git(cwd, args, **kwargs):
        rig.git_calls.append((cwd, args))
        return True, (candidate.merged_sha if args == ['rev-parse', 'HEAD'] else
                      candidate.merged_tree if args == ['rev-parse', 'HEAD^{tree}'] else '')

    monkeypatch.setattr(reviewer.project_sync, 'git_run', git)
    rig.stops, rig.registrations = [], []
    monkeypatch.setattr(hm, '_register_process', lambda *args: rig.registrations.append(args))
    for runtime in rig.runtimes.values():
        dispatch = runtime.dispatch

        def launch(*, _dispatch=dispatch, **kw):
            _dispatch(**kw)
            kw['session_dict']['proc'] = FakeProc()
            kw['register_process'](kw['session_dict']['proc'], 'review fixture', 'agent',
                                   kw['mc_session_id'], kw['project_id'])

        def stop(handle):
            rig.stops.append(handle)
            handle.session_dict['proc'].rc = 0
            handle.session_dict.update(status='stopped', process_alive=False)

        runtime.dispatch, runtime.stop = launch, stop
    return rig


def launch(rig, provider='codex', candidate=None):
    rig.manifest['config']['reviewer_character'] = f'global:worker-{provider}'
    hm._hm_save_manifest('roster1', rig.manifest)
    return reviewer.launch_reviewer(hm, rig.manifest, rig.project, candidate or rig.candidate,
                                    review_cwd=rig.project['project_path'],
                                    verdict_url='http://localhost:5377/api/review-fixture')


def submit(rig, verdict='pass', candidate=None):
    candidate = candidate or rig.candidate
    with reviewer.verdict_guard(hm, candidate) as status:
        sid = status['reviewer_session']
        # Attribution is covered by HR-3; replace only the real OS request
        # resolver, never the designated session or independence checks.
        from unittest.mock import patch
        with patch('mc.hivemind_lineage.creator_of_request', return_value=sid):
            return ReviewStore(hm._hm_dir('roster1')).record(
                candidate, object(), verdict=verdict, report_path='review.md',
                spawner_of=lambda s: ['attributed-parent'] if s == sid else [])


@pytest.mark.parametrize('provider', PROVIDERS)
def test_all_engines_get_exact_candidate_persona_rules_and_role(review_rig, provider):
    rig = review_rig
    result = launch(rig, provider)
    sid = result['reviewer_session']
    call, = rig.runtimes[provider].calls
    session = state.agent_sessions[sid]
    prompt = call['system_prompt']
    for text in (f'PERSONA-{provider}', f'Worker_{provider}', 'fixture-skill',
                 'PROJECT-RULES', 'SHARED-RULES', 'SKILL-CATALOG',
                 rig.candidate.merged_sha, rig.candidate.merged_tree,
                 rig.candidate.incoming_tip, rig.candidate.pre_merge_sha,
                 'worker1', 'earlier-worker', 'independent Hivemind', '/api/review-fixture'):
        assert text in prompt
    assert prompt.count('UNIVERSAL-ONCE') == prompt.count('POINTER-ONCE') == 1
    assert call['mc_session_id'] == sid and sid.startswith('hm_review_')
    assert call['session_dict'] is session
    assert call['project_path'] == rig.project['project_path']
    assert call['mode'] == 'A' and call['housekeeping'] is True
    assert call['effort'] == 'high' and call['model'] == ''
    assert call['register_process'] is hm._register_process
    assert len(rig.registrations) == 1
    assert hm.get_manager('thm').guardian_calls == 1
    for item in (session, call['session_metadata'], rig.rows[0]):
        assert item['hivemind_role'] == 'reviewer'
        assert item['trigger_type'] == 'hivemind_reviewer'
        assert not item.get('hivemind_ws_id')
        assert item['project_generation'] == 7
    assert session['_spawned_by'] == rig.rows[0]['dispatched_by_session_id'] == 'attributed-parent'
    assert rig.rows[0]['character'] == session['character']
    assert rig.rows[0]['resolved_assignment']['provider'] == provider
    assert worker.read_pinned_prompt(rig.rows[0]) == prompt
    assert '_system_prompt' not in rig.rows[0]
    assert ReviewStore(hm._hm_dir('roster1')).find(rig.candidate)['reviewer_session'] == sid
    assert result['state'] == 'pending' and result['result'] is None
    timer, = FakeTimer.created
    assert timer.interval == 2700 and timer.daemon and timer.started
    assert rig.memory_calls and rig.exploration_calls
    assert all(c['consumer_unattended'] for c in rig.memory_calls + rig.exploration_calls)


@pytest.mark.parametrize('provider', [p for p in PROVIDERS if p != 'claude'])
def test_completion_log_keeps_reviewer_role_and_identity(review_rig, monkeypatch, provider):
    rig = review_rig
    sid = launch(rig, provider)['reviewer_session']
    session = state.agent_sessions[sid]
    session.update(status='completed', process_alive=False, provider_session_id='native-review')
    session['log_lines'].append('Review completed; no pass inferred here.')
    monkeypatch.setattr(ar, 'get_manager', hm.get_manager)
    rig.runtimes[provider].calls[-1]['callbacks']['on_process_exit'](None, session)
    row, = rig.rows
    assert row['hivemind_role'] == 'reviewer' and row['trigger_type'] == 'hivemind_reviewer'
    assert row['dispatched_by_session_id'] == 'attributed-parent'
    assert row['project_generation'] == 7 and row['provider_session_id'] == 'native-review'
    assert row['character_ref'] == session['character_ref']
    assert row['pinned_prompt_sha256'] == session['pinned_prompt_sha256']


def test_designation_pending_log_and_intent_are_durable_before_dispatch(review_rig):
    rig = review_rig
    runtime = rig.runtimes['codex']
    original = runtime.dispatch

    def inspect(**kw):
        record = ReviewStore(hm._hm_dir('roster1')).find(rig.candidate)
        assert record['reviewer_session'] == kw['mc_session_id']
        assert reviewer._read(hm, rig.candidate)['phase'] == 'launching'
        assert rig.rows[0]['session_id'] == kw['mc_session_id']
        original(**kw)

    runtime.dispatch = inspect
    launch(rig)


@pytest.mark.parametrize('parent', ['worker1', 'child-of-worker', 'unknown'])
def test_worker_or_descendant_or_unknown_cannot_nominate_independent_reviewer(review_rig, parent):
    rig = review_rig
    rig.lineage.update({'worker1': [], 'child-of-worker': ['worker1']})
    rig.manifest['created_by_session_id'] = parent
    with pytest.raises(ReviewRefused) as refusal:
        launch(rig)
    assert refusal.value.status == 403
    assert not any(r.calls for r in rig.runtimes.values())
    assert not ReviewStore(hm._hm_dir('roster1')).path(rig.candidate).exists()


def test_human_created_run_has_no_invented_parent(review_rig):
    rig = review_rig
    rig.manifest.pop('created_by_session_id')
    rig.manifest.update(notify_session='worker1', created_by='worker1')
    sid = launch(rig)['reviewer_session']
    assert state.agent_sessions[sid]['_spawned_by'] == ''
    assert rig.rows[0]['dispatched_by_session_id'] == ''


def test_duplicate_and_restart_never_launch_replacement(review_rig, agents_dir):
    rig = review_rig
    first = launch(rig)
    (agents_dir / 'worker-codex.md').unlink()
    assert launch(rig)['reviewer_session'] == first['reviewer_session']
    state.agent_sessions.clear()
    status = launch(rig)
    assert status['state'] == 'held' and 'restart' in status['hold_reason']
    assert status['reviewer_session'] == first['reviewer_session']
    assert len(rig.runtimes['codex'].calls) == 1
    with pytest.raises(ReviewRefused):
        submit(rig)


def test_deleted_reviewer_holds_new_attempt_without_default_persona(review_rig, agents_dir):
    rig = review_rig
    (agents_dir / 'worker-codex.md').unlink()
    with pytest.raises(ReviewRefused, match='not found'):
        launch(rig)
    assert not any(rt.calls for rt in rig.runtimes.values())


@pytest.mark.parametrize('damage', ['generation', 'manifest', 'project', 'paused', 'legacy', 'head', 'tree', 'dirty', 'runtime', 'context', 'quota', 'allowance'])
def test_preflight_holds_without_launch(review_rig, monkeypatch, damage):
    rig = review_rig
    if damage == 'generation':
        monkeypatch.setattr(hm, '_assert_runtime_project_generation', lambda *a: (_ for _ in ()).throw(ReviewRefused(409, 'generation')))
    elif damage == 'manifest':
        rig.manifest['project_generation'] = 8
    elif damage == 'project':
        rig.project = dict(rig.project, id='other')
    elif damage == 'paused':
        rig.manifest['status'] = 'paused'
    elif damage == 'legacy':
        rig.manifest['config'].pop('roster_version')
    elif damage in ('head', 'tree', 'dirty'):
        prior = reviewer.project_sync.git_run
        bad = {'head': ['rev-parse', 'HEAD'], 'tree': ['rev-parse', 'HEAD^{tree}'],
               'dirty': ['status', '--porcelain', '--untracked-files=no']}[damage]
        monkeypatch.setattr(reviewer.project_sync, 'git_run',
                            lambda cwd, args, **kw: (True, 'wrong') if args == bad else prior(cwd, args, **kw))
    elif damage == 'runtime':
        monkeypatch.setattr(agent_runtime, 'get_runtime', lambda name: None)
    elif damage == 'context':
        monkeypatch.setattr(ar, '_build_agent_context', lambda *a, **kw: (_ for _ in ()).throw(OSError('context unavailable')))
    elif damage == 'quota':
        characters.write_character('global', 'worker-codex', 'd', 'b', engine={'provider': 'codex', 'model': 'gpt-6'}, overwrite=True)
        monkeypatch.setattr(ar, '_model_quota_blocked', lambda *a: 'model quota held')
    elif damage == 'allowance':
        monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **kw: 'vendor allowance held')
    with pytest.raises((ReviewRefused, ValueError)):
        launch(rig)
    assert not any(rt.calls for rt in rig.runtimes.values())


@pytest.mark.parametrize('failure', ['prompt', 'sync', 'designation', 'log', 'intent', 'dispatch', 'guard'])
def test_failure_never_becomes_pass_or_duplicate_launch(review_rig, monkeypatch, failure):
    rig = review_rig

    def fail(*a, **kw):
        raise OSError(f'{failure} failed')

    if failure == 'prompt':
        monkeypatch.setattr(reviewer, '_pin_prompt', fail)
    elif failure == 'sync':
        monkeypatch.setattr(reviewer.os, 'fsync', fail)
    elif failure == 'designation':
        monkeypatch.setattr(ReviewStore, 'designate', fail)
    elif failure == 'log':
        monkeypatch.setattr(ar, '_update_agent_log', fail)
    elif failure == 'intent':
        monkeypatch.setattr(reviewer, '_save', fail)
    else:
        rig.runtimes['codex'].dispatch = fail
    with pytest.raises((ReviewRefused, OSError)):
        launch(rig)
    with pytest.raises((ReviewRefused, OSError)):
        submit(rig)
    assert not rig.runtimes['codex'].calls
    if failure == 'designation':
        with pytest.raises(ReviewRefused):
            launch(rig)
    elif failure in ('log', 'dispatch', 'guard'):
        assert launch(rig)['state'] == 'held'


def test_uncertain_dispatch_keeps_owner_and_can_stop_only_that_process(review_rig):
    rig = review_rig
    original = rig.runtimes['codex'].dispatch

    def uncertain(**kw):
        original(**kw)
        raise RuntimeError('after process creation')

    rig.runtimes['codex'].dispatch = uncertain
    with pytest.raises(ReviewRefused, match='uncertain'):
        launch(rig)
    status = launch(rig)
    assert status['state'] == 'held' and len(rig.runtimes['codex'].calls) == 1
    with pytest.raises(ReviewRefused):
        submit(rig)
    assert reviewer.cancel_reviewer(hm, rig.candidate)['state'] == 'held'
    assert len(rig.stops) == 1
    assert rig.stops[0].mc_session_id == status['reviewer_session']


@pytest.mark.parametrize('outcome', ['pass', 'changes_requested'])
def test_structured_result_survives_restart_and_does_not_relaunch(review_rig, outcome):
    rig = review_rig
    launch(rig)
    record = submit(rig, outcome)
    state.agent_sessions.clear()
    status = reviewer.reviewer_status(hm, rig.candidate)
    assert status['state'] == outcome and status['result'] == record['result']
    assert launch(rig)['state'] == outcome
    assert len(rig.runtimes['codex'].calls) == 1


def test_process_exit_without_verdict_is_hold(review_rig):
    rig = review_rig
    sid = launch(rig)['reviewer_session']
    state.agent_sessions[sid].update(status='completed', process_alive=False)
    status = reviewer.reviewer_status(hm, rig.candidate)
    assert status['state'] == 'held' and status['result'] is None
    with pytest.raises(ReviewRefused):
        submit(rig)


def test_cancel_is_durable_before_stop_and_refuses_late_verdict(review_rig):
    rig = review_rig
    launch(rig)
    original = rig.runtimes['codex'].stop

    def inspect(handle):
        assert reviewer._read(hm, rig.candidate)['phase'] == 'cancelled'
        original(handle)

    rig.runtimes['codex'].stop = inspect
    assert reviewer.cancel_reviewer(hm, rig.candidate)['state'] == 'cancelled'
    state.agent_sessions.clear()
    assert reviewer.reviewer_status(hm, rig.candidate)['state'] == 'cancelled'
    with pytest.raises(ReviewRefused):
        submit(rig)
    assert launch(rig)['state'] == 'cancelled' and len(rig.runtimes['codex'].calls) == 1


@pytest.mark.parametrize('damage', ['pid', 'session', 'runtime', 'stop'])
def test_cancellation_never_kills_foreign_or_unknown_process(review_rig, monkeypatch, damage):
    rig = review_rig
    sid = launch(rig)['reviewer_session']
    if damage == 'pid':
        state.agent_sessions[sid]['proc'] = SimpleNamespace(pid=991999)
    elif damage == 'session':
        state.agent_sessions[sid]['project_generation'] = 8
    elif damage == 'runtime':
        monkeypatch.setattr(agent_runtime, 'get_runtime', lambda name: None)
    else:
        rig.runtimes['codex'].stop = lambda h: (_ for _ in ()).throw(OSError('cannot stop'))
    if damage == 'session':
        with pytest.raises(ReviewRefused):
            reviewer.cancel_reviewer(hm, rig.candidate)
    else:
        status = reviewer.cancel_reviewer(hm, rig.candidate)
        assert status['state'] == 'cancelled' and status['stop_error']
    assert not rig.stops
    with pytest.raises(ReviewRefused):
        submit(rig)


@pytest.mark.parametrize('elapsed', [2700, 2701, 9999])
def test_deadline_stops_owned_reviewer_and_never_passes(review_rig, elapsed):
    rig = review_rig
    launch(rig)
    rig.clock[0] += elapsed
    timer, = FakeTimer.created
    timer.function(*timer.args)
    status = reviewer.reviewer_status(hm, rig.candidate)
    assert status['state'] == 'timed_out' and status['result'] is None
    assert len(rig.stops) == 1
    with pytest.raises(ReviewRefused):
        submit(rig)
    assert len(rig.runtimes['codex'].calls) == 1


def test_timely_verdict_is_preserved_after_deadline(review_rig):
    rig = review_rig
    launch(rig)
    submit(rig)
    rig.clock[0] += 2800
    FakeTimer.created[0].function(*FakeTimer.created[0].args)
    assert reviewer.reviewer_status(hm, rig.candidate)['state'] == 'pass'
    assert not rig.stops


def test_timer_still_stops_owned_process_after_run_is_paused(review_rig):
    rig = review_rig
    launch(rig)
    rig.manifest['status'] = 'paused'
    hm._hm_save_manifest('roster1', rig.manifest)
    rig.clock[0] += 2701
    FakeTimer.created[0].function(*FakeTimer.created[0].args)
    assert len(rig.stops) == 1
    with pytest.raises(ReviewRefused):
        reviewer.reviewer_status(hm, rig.candidate)


def test_reviewer_does_not_consume_worker_capacity(review_rig, monkeypatch):
    rig = review_rig
    launch(rig)
    rig.manifest['config']['max_concurrent_workers'] = 1
    hm._hm_save_manifest('roster1', rig.manifest)
    workers = []

    def spawn(manifest, ws, project, hm_id, ws_id):
        workers.append(ws_id)
        return 'new-worker'

    monkeypatch.setattr(hm, '_hm_spawn_worker_session', spawn)
    hm._hm_auto_spawn_workers('roster1')
    assert workers == ['ws1']
    assert hm._hm_load_workstream('roster1', 'ws1')['current_agent_session_id'] == 'new-worker'


@pytest.mark.parametrize('damage', ['missing', 'json', 'digest', 'candidate', 'designation', 'duplicate', 'schema'])
def test_corrupt_missing_or_mismatched_state_is_hold(review_rig, damage):
    rig = review_rig
    launch(rig)
    path = reviewer._path(hm, rig.candidate)
    if damage == 'missing':
        path.unlink()
    elif damage == 'json':
        path.write_text('{broken', encoding='utf-8')
    elif damage == 'digest':
        value = json.loads(path.read_text())
        value['phase'] = 'pending-corrupt'
        path.write_text(json.dumps(value))
    elif damage == 'candidate':
        rig.candidate = replace(rig.candidate, merged_sha='e' * 40)
    elif damage == 'duplicate':
        original = path.read_text(encoding='utf-8')
        path.write_text(original.replace('{', '{"schema_version": 1,', 1), encoding='utf-8')
    elif damage == 'schema':
        record = reviewer._read(hm, rig.candidate)
        record['schema_version'] = True
        reviewer._save(hm, rig.candidate, record)
    else:
        record = reviewer._read(hm, rig.candidate)
        record['reviewer_session'] = 'hm_review_aaaaaaaaaaaa'
        record['assignment']['session_id'] = record['reviewer_session']
        reviewer._save(hm, rig.candidate, record)
    with pytest.raises(ReviewRefused):
        reviewer.reviewer_status(hm, rig.candidate)
    with pytest.raises(ReviewRefused):
        submit(rig)
    with pytest.raises(ReviewRefused):
        launch(rig)
    assert len(rig.runtimes['codex'].calls) == 1


def test_new_candidate_resolves_current_character_but_existing_attempt_stays_pinned(review_rig, agents_dir):
    rig = review_rig
    first = launch(rig)
    characters.write_character('global', 'worker-codex', 'changed', 'CHANGED-PERSONA',
                               engine={'provider': 'gemini'}, agent_name='Changed', overwrite=True)
    assert launch(rig)['resolved_assignment']['provider'] == 'codex'
    second = launch(rig, candidate=replace(rig.candidate, integration_attempt=2))
    assert first['reviewer_session'] != second['reviewer_session']
    assert second['resolved_assignment']['provider'] == 'gemini'
    assert 'CHANGED-PERSONA' in rig.runtimes['gemini'].calls[-1]['system_prompt']


def test_concurrent_duplicate_during_dispatch_reads_intent_without_second_launch(review_rig):
    rig = review_rig
    runtime = rig.runtimes['codex']
    original = runtime.dispatch
    duplicates = []

    def dispatch(**kw):
        duplicates.append(launch(rig))
        original(**kw)

    runtime.dispatch = dispatch
    status = launch(rig)
    duplicate, = duplicates
    assert duplicate['state'] == 'held'
    assert duplicate['reviewer_session'] == status['reviewer_session']
    assert len(runtime.calls) == 1


def test_cancel_during_dispatch_vetoes_then_stops_new_owned_process(review_rig):
    rig = review_rig
    runtime = rig.runtimes['codex']
    original = runtime.dispatch

    def dispatch(**kw):
        assert reviewer.cancel_reviewer(hm, rig.candidate)['state'] == 'cancelled'
        original(**kw)

    runtime.dispatch = dispatch
    assert launch(rig)['state'] == 'cancelled'
    assert len(rig.stops) == 1
    with pytest.raises(ReviewRefused):
        submit(rig)


def test_generation_drift_never_reports_recorded_pass(review_rig, monkeypatch):
    rig = review_rig
    launch(rig)
    submit(rig)
    monkeypatch.setattr(hm, '_assert_runtime_project_generation',
                        lambda *a: (_ for _ in ()).throw(ReviewRefused(409, 'deleted generation')))
    with pytest.raises(ReviewRefused):
        reviewer.reviewer_status(hm, rig.candidate)
    with pytest.raises(ReviewRefused):
        submit(rig)


def test_cancel_cleanup_is_available_when_run_stopped(review_rig):
    rig = review_rig
    launch(rig)
    rig.manifest['status'] = 'stopped'
    hm._hm_save_manifest('roster1', rig.manifest)
    assert reviewer.cancel_reviewer(hm, rig.candidate)['state'] == 'cancelled'
    assert len(rig.stops) == 1


def test_expired_verdict_timestamp_cannot_be_consumed_as_pass(review_rig, monkeypatch):
    rig = review_rig
    launch(rig)
    late = datetime.fromtimestamp(rig.clock[0] + 2701, timezone.utc).isoformat()
    monkeypatch.setattr('mc.hivemind_review.now_iso', lambda: late)
    submit(rig)
    status = reviewer.reviewer_status(hm, rig.candidate)
    assert status['state'] == 'timed_out' and status['result'] is None
    with pytest.raises(ReviewRefused):
        with reviewer.verdict_guard(hm, rig.candidate):
            ReviewStore(hm._hm_dir('roster1')).consume_pass(rig.candidate)


def test_prompt_exposes_all_exact_pins_and_rejects_missing_delivery_url(review_rig):
    candidate = review_rig.candidate
    with pytest.raises(ReviewRefused):
        reviewer.build_reviewer_prompt(candidate, verdict_url='')
    prompt = reviewer.build_reviewer_prompt(candidate, verdict_url='/trusted-route')
    assert f'merged_sha="{candidate.merged_sha}"' in prompt
    assert f'incoming_tip="{candidate.incoming_tip}"' in prompt
    assert f'project_generation={candidate.project_generation}' in prompt
    for key, value in candidate.pins().items():
        assert f'"{key}"' in prompt
        assert str(value) in prompt or isinstance(value, list)


def test_real_codex_fence_refusal_is_a_durable_hold(review_rig, monkeypatch):
    rig = review_rig
    runtime = agent_runtime.CodexRuntime()
    monkeypatch.setattr(runtime, 'resolve_binary', lambda: 'fixture-codex')
    monkeypatch.setattr(agent_runtime, '_codex_fence_self_test', lambda: (False, 'fixture refusal'))
    monkeypatch.setitem(agent_runtime._RUNTIMES, 'codex', runtime)
    with pytest.raises(ReviewRefused, match='self-test failed'):
        launch(rig)
    assert launch(rig)['state'] == 'held'
    assert not rig.registrations
    with pytest.raises(ReviewRefused):
        submit(rig)


def test_watchdog_start_failure_stops_and_holds_owned_review(review_rig, monkeypatch):
    rig = review_rig
    monkeypatch.setattr(FakeTimer, 'start', lambda timer: (_ for _ in ()).throw(RuntimeError('timer failed')))
    with pytest.raises(ReviewRefused, match='timer failed'):
        launch(rig)
    assert len(rig.stops) == 1
    assert launch(rig)['state'] == 'held'
    with pytest.raises(ReviewRefused):
        submit(rig)


def test_deadline_stops_owned_process_even_when_verdict_store_is_corrupt(review_rig):
    rig = review_rig
    launch(rig)
    ReviewStore(hm._hm_dir('roster1')).path(rig.candidate).write_text('{broken', encoding='utf-8')
    rig.clock[0] += 2701
    FakeTimer.created[0].function(*FakeTimer.created[0].args)
    assert len(rig.stops) == 1
    assert reviewer._read(hm, rig.candidate)['phase'] == 'held'
    with pytest.raises(ReviewRefused):
        submit(rig)


def test_postlaunch_disk_failure_stops_local_owned_handle_and_never_relaunches(review_rig, monkeypatch):
    rig = review_rig
    save = reviewer._save
    writes = []

    def fail_finalization(*args):
        writes.append(args)
        if len(writes) > 1:
            raise OSError('disk failed after launch')
        return save(*args)

    monkeypatch.setattr(reviewer, '_save', fail_finalization)
    with pytest.raises(ReviewRefused, match='finalization failed'):
        launch(rig)
    assert len(rig.stops) == 1
    assert launch(rig)['state'] == 'held'
    assert len(rig.runtimes['codex'].calls) == 1


def test_expired_uncertain_launch_intent_never_remains_eligible(review_rig):
    rig = review_rig
    launch(rig)
    record = reviewer._read(hm, rig.candidate)
    record.update(phase='launching', process_pid=None)
    reviewer._save(hm, rig.candidate, record)
    state.agent_sessions.clear()
    rig.clock[0] += 2701
    assert reviewer.reviewer_status(hm, rig.candidate)['state'] == 'timed_out'
    with pytest.raises(ReviewRefused):
        submit(rig)


# Real git + real Claude bridge, with only model process launch faked.
from tests.test_hivemind_worktree_dispatch_hook import env, repo, _git  # noqa: E402,F401


@pytest.mark.parametrize('quota_block', [False, True])
def test_real_claude_bridge_keeps_candidate_cwd_identity_and_guard_once(
        env, agents_dir, monkeypatch, quota_block):
    from tests.test_hivemind_worktree_dispatch_hook import _agent_trees

    project = env['project']
    tip = _git(env['repo'], 'rev-parse', 'HEAD')
    tree = _git(env['repo'], 'rev-parse', 'HEAD^{tree}')
    candidate = Candidate('hmhook', 1, 'hm1', 'ws1', 1, 1, tip,
                          'clayrune/agent/worker1', tip, tip, tree, ('worker1',))
    characters.write_character('global', 'reviewer', 'fixture', 'REVIEWER-PERSONA',
                               agent_name='Reviewer', engine={'provider': 'claude', 'effort': 'high'})
    manifest = {'id': 'hm1', 'project_id': 'hmhook', 'project_generation': 1, 'status': 'active',
                'config': {'roster_version': 1, 'reviewer_character': 'global:reviewer'}}
    hm._hm_ensure_dirs('hm1')
    hm._hm_save_manifest('hm1', manifest)
    rows, allowance, registrations, guards = [], [], [], []
    monkeypatch.setattr(ar, '_update_agent_log', lambda pid, fn: fn(rows))
    monkeypatch.setattr(ar, '_allowance_refusal',
                        lambda *a, **kw: allowance.append(a) or ('fixture quota exhausted' if quota_block else ''))
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *a: '')
    monkeypatch.setattr(ar, '_register_process', lambda *a, **kw: registrations.append(a))
    monkeypatch.setattr(ar._launch_marker, 'launch_cmd', lambda cmd, *a: guards.append(a) or cmd)
    monkeypatch.setattr(ar, '_build_agent_context', lambda p, **kw: kw['character_body'])
    monkeypatch.setattr(reviewer, 'threading', SimpleNamespace(Timer=FakeTimer))
    kwargs = dict(review_cwd=str(env['repo']), verdict_url='/trusted-review-fixture')
    if quota_block:
        with pytest.raises(ReviewRefused, match='quota exhausted'):
            reviewer.launch_reviewer(hm, manifest, project, candidate, **kwargs)
        assert not env['spawned']
        assert reviewer.reviewer_status(hm, candidate)['state'] == 'held'
    else:
        status = reviewer.launch_reviewer(hm, manifest, project, candidate, **kwargs)
        session = state.agent_sessions[status['reviewer_session']]
        assert status['state'] == 'pending'
        assert Path(env['spawned'][0].cwd) == env['repo']
        assert session['hivemind_role'] == 'reviewer' and session['character_ref'] == 'global:reviewer'
        assert session['requested_effort'] == 'high'
        assert 'REVIEWER-PERSONA' in session['_system_prompt']
        assert session['_system_prompt'].count(candidate.merged_sha) >= 1
        assert len(guards) == len(registrations) == 1
    assert len(allowance) == 1
    assert _agent_trees(env['repo']) == []
    assert _git(env['repo'], 'rev-parse', 'HEAD') == tip
    assert _git(env['repo'], 'rev-parse', 'HEAD^{tree}') == tree
