"""SR-3 uses disposable projects/patch copies and never launches an engine."""
import copy
from dataclasses import asdict, replace
import importlib.util
import json
from pathlib import Path
import threading
from types import SimpleNamespace

from flask import Flask
import pytest

from mc import steward_role_dispatch as d, steward_role_task as task
from mc.steward_role_model import RoleError
from test_steward_role_task import staged  # protected patches, temporary tree only
from test_context_profile_by_job_shape import env as context_env
from test_builtins_resume_context import spawns
from test_revive_notify_carry import ar as revive_env


@pytest.fixture
def integrated(staged, monkeypatch):
    from steward import core
    spec = importlib.util.spec_from_file_location('steward.sr3_core_copy', staged / 'steward/core.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(core, 'build_cycle_task', module.build_cycle_task)
    return staged


class FakeServices:
    def __init__(self, tmp_path):
        self.project = {'id': 'project', 'project_path': str(tmp_path / 'canonical'),
                        'steward_mode': 'on', 'steward_assignment': {'schedule_id': 'wake'},
                        'roster': [{'character': 'global:owner'}],
                        'backlog': [{'id': 'charter', 'text': 'Current mandate', 'status': 'open'}]}
        self.row = {'id': 'wake', 'project_id': 'project', 'character': 'global:owner',
                    'enabled': True, 'continue_session': True,
                    'steward_role': {'version': 1, 'charter_item_id': 'charter',
                                     'journal_path': 'docs/_journal/charter.md'}}
        self.selection = d.Selection('global:owner', 'codex', 'chosen', 'high')
        self.sessions = {}
        self.deliveries = []
        self.refreshes = []
        self.events = []
        self.guard_error = ''
        self.unknown = False
        self.children = False
        self.changing = False

    def load(self, schedule_id):
        assert schedule_id == self.row['id']
        return copy.deepcopy(self.project), copy.deepcopy(self.row), [copy.deepcopy(self.row)]

    def resolve(self, project, row):
        return self.selection, {'name': 'owner', 'agent_name': 'Example Owner'}, 'CURRENT VOICE'

    def observe(self, pid, sid):
        return self.sessions.get(sid)

    def active(self, pid):
        return False  # claim/observation checks also independently cover active cycles

    def guard(self, project, selection, session):
        if self.guard_error:
            raise RoleError(self.guard_error)

    def refresh(self, project, text, meta, body, selection, since):
        self.refreshes.append(since)
        assert text.startswith('[Steward cycle]') and 'CURRENT VOICE' in text
        if self.changing:
            self.project['backlog'][0]['text'] = 'changed during preparation'
        return 'CURRENT RULES; FILTERED MEMORY; INTERACTIVE EXPLORATION'

    def lifecycle(self, project, row, event, detail):
        self.events.append((event, detail))
        if event == 'successor' and self.children:
            raise RoleError('outstanding child callbacks must drain before successor')
        return 'Handoff journal and child state: ' + str(detail)

    def deliver(self, project, row, text, prepared, previous):
        sid = previous['session_id'] if previous else f'session{len(self.deliveries)}'
        outcome = 'revived' if previous else 'dispatched'
        self.deliveries.append((text, prepared, previous))
        self.sessions[sid] = {'session_id': sid, 'status': 'running',
                              'provider': prepared.assignment['provider'],
                              'trigger_type': 'schedule', 'trigger_id': row['id']}
        if self.unknown:
            raise OSError('launch happened but acknowledgment was lost')
        return sid, outcome

    def complete(self):
        for session in self.sessions.values():
            session['status'] = 'idle'


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(d.characters, 'read_character', lambda *a, **k: {'name': 'owner'})
    return FakeServices(tmp_path), d.RoleStateStore(tmp_path / 'data')


def run(env):
    return d.prepare_and_fire('wake', *env)


def test_first_fire_marker_voice_provenance_and_acceptance(integrated, env):
    services, store = env
    result = run(env)
    text, prepared, previous = services.deliveries[0]
    assert text.startswith('[Steward cycle]\n')
    assert all(s in text for s in ('Example Owner', 'CURRENT VOICE', 'Current mandate',
                                   'CURRENT RULES', task.load_directive()))
    assert previous is None
    assert result['trigger_type'] == 'schedule' and result['origin'] == 'unattended'
    assert result['trigger_id'] == 'wake'
    assert prepared.assignment == asdict(services.selection)
    record = store.read('project', 'wake')
    assert record['claim']['state'] == 'accepted'
    assert record['refresh_watermark'] and record['last_completed_at'] is None
    assert not (store.data_root / 'projects').exists()


def test_unchanged_selection_revives_same_thread_with_new_charter_rules(integrated, env):
    services, store = env
    first = run(env)
    services.complete()
    services.project['backlog'][0]['text'] = 'New charter wording'
    watermark = store.read('project', 'wake')['refresh_watermark']
    second = run(env)
    assert second['session_id'] == first['session_id']
    assert services.refreshes == ['', watermark]
    assert 'New charter wording' in services.deliveries[-1][0]
    assert services.deliveries[-1][2] is not None
    assert store.read('project', 'wake')['last_completed_at']


@pytest.mark.parametrize('field,value', [('character', 'global:other'), ('provider', 'qwen'),
                                        ('model', 'other'), ('effort', 'low')])
def test_selection_change_uses_explicit_successor(integrated, env, field, value):
    services, store = env
    first = run(env)
    services.complete()
    services.selection = replace(services.selection, **{field: value})
    second = run(env)
    assert first['session_id'] != second['session_id']
    assert second['predecessor_session_id'] == first['session_id']
    assert services.deliveries[-1][2] is None
    assert 'successor' in [e[0] for e in services.events]
    assert first['session_id'] in services.deliveries[-1][0]


def test_successor_holds_for_undrained_children(integrated, env):
    services, store = env
    run(env)
    services.complete()
    services.children = True
    services.selection = replace(services.selection, model='changed')
    before = store.read('project', 'wake')
    with pytest.raises(RoleError, match='callbacks'):
        run(env)
    assert len(services.deliveries) == 1 and store.read('project', 'wake') == before


@pytest.mark.parametrize('flag', ['running', 'waiting_for_question', 'waiting_for_plan_approval',
                                  'waiting_on_children', '_stdin_write_uncertain', 'missing'])
def test_active_or_unknown_cycle_does_not_dispatch(integrated, env, flag):
    services, store = env
    run(env)
    services.complete()
    prior = next(iter(services.sessions.values()))
    if flag == 'running':
        prior['status'] = 'running'
    elif flag == 'missing':
        services.sessions.clear()
    else:
        prior[flag] = True
    with pytest.raises(d.RoleBusy):
        run(env)
    assert len(services.deliveries) == 1


def test_lost_launch_ack_stays_unknown_across_new_store_instance(integrated, env):
    services, store = env
    services.unknown = True
    with pytest.raises(OSError, match='acknowledgment'):
        run(env)
    assert store.read('project', 'wake')['refresh_watermark'] is None
    assert store.read('project', 'wake')['claim']['state'] == 'delivery_unknown'
    services.complete()
    with pytest.raises(d.RoleBusy, match='unknown'):
        d.prepare_and_fire('wake', services, d.RoleStateStore(store.data_root))
    assert len(services.deliveries) == 1


def test_disabled_guard_missing_charter_and_owner_fail_before_delivery(integrated, env):
    services, store = env
    services.project['steward_mode'] = 'off'
    with pytest.raises(RoleError, match='disabled'):
        run(env)
    services.project['steward_mode'] = 'on'
    services.row['enabled'] = False
    with pytest.raises(RoleError, match='disabled'):
        run(env)
    services.row['enabled'] = True
    services.guard_error = 'guard could not demonstrate refusal'
    with pytest.raises(RoleError, match='guard'):
        run(env)
    services.guard_error = ''
    services.project['backlog'].clear()
    with pytest.raises(RoleError, match='charter'):
        run(env)
    assert not services.deliveries and store.read('project', 'wake')['claim'] is None


def test_unapplied_core_patch_and_missing_directive_are_explicit(env, monkeypatch):
    from steward import core
    monkeypatch.setattr(core, 'build_cycle_task', lambda project, charter, refresh='': 'old')
    with pytest.raises(RoleError, match='core adapter patch is unapplied'):
        run(env)
    monkeypatch.setattr(core, 'build_cycle_task', task.build_cycle_task)
    monkeypatch.setattr(task, 'DIRECTIVE_PATH', Path('/nonexistent/sr3-directive'))
    with pytest.raises(RoleError, match='directive unavailable'):
        run(env)
    assert not env[0].deliveries


def test_configuration_change_before_launch_is_not_delivered(integrated, env):
    env[0].changing = True
    with pytest.raises(RoleError, match='configuration changed'):
        run(env)
    assert not env[0].deliveries


def test_concurrent_timer_and_run_now_have_one_durable_claim(integrated, env):
    services, store = env
    entered, release = threading.Event(), threading.Event()
    original = services.deliver

    def deliver(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    services.deliver = deliver
    errors = []
    def first():
        try:
            run(env)
        except Exception as exc:
            errors.append(exc)
    worker = threading.Thread(target=first)
    worker.start()
    try:
        assert entered.wait(5)
        with pytest.raises(d.RoleBusy, match='claim'):
            d.prepare_and_fire('wake', services, d.RoleStateStore(store.data_root))
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and not errors and len(services.deliveries) == 1


def test_crashed_mutex_and_corrupt_checkpoint_never_fail_open(env):
    services, store = env
    claim = store.data_root / 'steward/dispatch-locks/project.claim'
    claim.parent.mkdir(parents=True)
    claim.write_text('old owner, unknown outcome')
    with pytest.raises(d.RoleBusy):
        run(env)
    claim.unlink()
    path = store.path('project', 'wake')
    path.parent.mkdir(parents=True)
    path.write_text('{broken')
    with pytest.raises(RoleError, match='not valid JSON'):
        run(env)
    assert not services.deliveries


def test_append_requires_ack_and_does_not_kill_process():
    flushed = []
    proc = SimpleNamespace(stdin=SimpleNamespace(write=lambda text: flushed.append(text),
                                                 flush=lambda: flushed.append('flush')))
    session = {'proc': proc, 'stdin_lock': threading.Lock()}
    d._append_ack(session, '[Steward cycle] go')
    assert json.loads(flushed[0])['message']['content'].startswith('[Steward cycle]')
    assert flushed[1] == 'flush'
    block = threading.Event()
    proc.stdin.write = lambda text: block.wait(5)
    try:
        with pytest.raises(RoleError, match='uncertain'):
            d._append_ack(session, 'next', timeout=0.01)
        assert session['_stdin_write_uncertain']
    finally:
        block.set()


def test_run_now_seam_persists_blocker_and_leaves_ordinary_path(tmp_path, monkeypatch):
    from mc.blueprints import scheduler_routes as sr
    app = Flask(__name__)
    app.register_blueprint(sr.bp)
    path = tmp_path / 'schedules.json'
    rows = [{'id': 'role', 'project_id': 'project', 'enabled': True, 'steward_role': {}},
            {'id': 'ordinary', 'project_id': 'project', 'task': 'unchanged',
             'continue_session': False}]
    path.write_text(json.dumps(rows))
    monkeypatch.setattr(sr, 'SCHEDULES_PATH', path)
    monkeypatch.setattr(d, 'fire', lambda *args: (_ for _ in ()).throw(RoleError('guard refused')))
    ordinary = []
    monkeypatch.setattr(sr, '_dispatch_agent_internal', lambda *a, **k: ordinary.append((a, k)) or 'sid')
    client = app.test_client()
    result = client.post('/api/schedule/role/run-now')
    assert result.status_code == 409 and 'guard refused' in result.json['error']
    assert json.loads(path.read_text())[0]['last_error'] == 'guard refused'
    result = client.post('/api/schedule/ordinary/run-now')
    assert result.status_code == 200 and ordinary[0][0] == ('project', 'unchanged')
    assert ordinary[0][1]['trigger_type'] == 'schedule'


@pytest.mark.parametrize('provider', ['aider', 'opencode', 'goose', 'kiro', 'unknown'])
def test_undemonstrated_guard_refuses_before_binary_lookup(provider):
    services = d.SchedulerServices(None, lambda *a: '')
    with pytest.raises(RoleError, match='no demonstrated'):
        services.guard({}, d.Selection('global:owner', provider, '', ''), None)


@pytest.mark.parametrize('wake_task', ['[Steward cycle] go', 'recovery task with trusted role provenance'])
def test_filtered_context_refresh_reaches_memory_explorations_and_ledger(context_env, monkeypatch, wake_task):
    from mc import distiller, memory, state
    ar, project = context_env['ar'], context_env['project']
    calls = []
    def memory_floor(*args, **kwargs):
        calls.append(('memory', kwargs))
        return [{'file': 'permitted.md', 'snippet': 'permitted interactive memory'}]
    def exploration_floor(*args, **kwargs):
        calls.append(('exploration', kwargs))
        return []
    def ledger(*args, **kwargs):
        calls.append(('ledger', kwargs))
        return 'CURRENT FILTERED LEDGER'
    monkeypatch.setitem(state.CONFIG, 'exploration_readback_enabled', True)
    monkeypatch.setitem(state.CONFIG, 'negation_ledger_enabled', True)
    monkeypatch.setattr(ar, '_memory_search', memory_floor)
    monkeypatch.setattr(distiller, 'exploration_read_floor', exploration_floor)
    monkeypatch.setattr(memory, 'render_negation_ledger', ledger)
    services = d.SchedulerServices(None, lambda *a: '')
    output = services.refresh(project, wake_task, {'agent_name': 'Owner'},
                              'FRESH CHARACTER BODY', d.Selection('global:owner', 'claude', '', ''), '')
    assert {'memory', 'exploration', 'ledger'} <= {name for name, _ in calls}
    assert all(kwargs['consumer_unattended'] is True for _, kwargs in calls)
    assert 'FRESH CHARACTER BODY' in output and 'CURRENT FILTERED LEDGER' in output
    assert 'permitted interactive memory' in output
    assert next(kwargs for name, kwargs in calls if name == 'memory')['expand'] >= 0


def test_timer_and_recovery_use_same_adapter_and_preserve_binding(tmp_path, monkeypatch):
    from mc.blueprints import scheduler_routes as sr
    path = tmp_path / 'schedules.json'
    row = {'id': 'role', 'project_id': 'project', 'enabled': True, 'schedule_type': 'interval',
           'interval_minutes': 60, 'steward_role': {'charter_item_id': 'current'},
           'next_run': '2000-01-01T00:00:00Z'}
    path.write_text(json.dumps([row]))
    monkeypatch.setattr(sr, 'SCHEDULES_PATH', path)
    calls = []
    def fire(scheduler, sid):
        calls.append(sid)
        # A human save during dispatch must survive runtime finalization.
        updated = json.loads(path.read_text())
        updated[0]['description'] = 'human edit'
        path.write_text(json.dumps(updated))
        return {'ok': True, 'session_id': 'sid'}
    monkeypatch.setattr(d, 'fire', fire)
    class StopAfterOne:
        checks = 0
        def is_set(self):
            self.checks += 1
            return self.checks > 1
        def wait(self, timeout):
            return True
    monkeypatch.setattr(sr, '_scheduler_stop', StopAfterOne())
    monkeypatch.setattr(sr, 'all_managers', lambda: [])
    monkeypatch.setattr(sr._wf, 'resume_due_waits', lambda: None)
    monkeypatch.setattr(sr, '_claim_slot', lambda *a: True)
    sr._scheduler_loop()
    saved = json.loads(path.read_text())[0]
    assert calls == ['role'] and saved['last_run']
    assert saved['description'] == 'human edit' and saved['steward_role'] == row['steward_role']
    assert saved['next_run'] > row['next_run']


@pytest.mark.parametrize('provider', ['claude', 'codex', 'qwen', 'gemini'])
def test_production_revive_keeps_id_and_never_falls_back(provider, monkeypatch):
    calls = []
    scheduler = SimpleNamespace(get_manager=lambda pid: SimpleNamespace(lock=threading.RLock()),
                               _revive_from_agent_log=lambda *a, **k: calls.append((a, k)) or True,
                               _revive_non_claude_from_agent_log=lambda *a, **k: calls.append((a, k)) or True)
    services = d.SchedulerServices(scheduler, lambda *a: '')
    monkeypatch.setattr(d, 'wait_native_delivery', lambda s: None)
    project, row = {'id': 'p'}, {'id': 'wake'}
    prepared = d.Prepared({'name': 'owner'}, {'provider': provider}, 'current context')
    previous = {'session_id': 'old-sid', 'status': 'completed'}
    assert services.deliver(project, row, '[Steward cycle] current refresh', prepared,
                            previous) == ('old-sid', 'revived')
    assert calls[0][0] == ('p', 'old-sid', '[Steward cycle] current refresh', project)
    assert calls[0][1]['role_context'] is prepared
    scheduler._revive_from_agent_log = scheduler._revive_non_claude_from_agent_log = lambda *a, **k: False
    with pytest.raises(RoleError, match='no fresh fallback'):
        services.deliver(project, row, '[Steward cycle] go', prepared, previous)


def test_production_append_and_fresh_dispatch_provenance(monkeypatch):
    from mc import state
    sent = []
    live = {'session_id': 'old', 'project_id': 'p', 'status': 'idle', 'mode': 'B',
            'process_alive': True, 'stdin_lock': threading.Lock(),
            'proc': SimpleNamespace(poll=lambda: None, stdin=SimpleNamespace(
                write=lambda message: sent.append(json.loads(message)), flush=lambda: None))}
    monkeypatch.setitem(state.agent_sessions, 'old', live)
    dispatched = []
    scheduler = SimpleNamespace(get_manager=lambda pid: SimpleNamespace(lock=threading.RLock()),
                               _record_usage_breakdown_turn_started=None,
                               _dispatch_agent_internal=lambda *a, **k: dispatched.append((a, k)) or 'new')
    services = d.SchedulerServices(scheduler, lambda *a: '')
    prepared = d.Prepared({'name': 'owner'}, asdict(d.Selection('global:owner', 'claude', 'model', 'high')), 'rules')
    project, row = {'id': 'p'}, {'id': 'wake', 'character': 'global:owner'}
    assert services.deliver(project, row, '[Steward cycle] current', prepared, live) == ('old', 'appended')
    assert sent[0]['message']['content'].startswith('[Steward cycle]')
    assert live['status'] == 'running' and live['trigger_type'] == 'schedule'
    assert services.deliver(project, row, '[Steward cycle] successor', prepared, None) == ('new', 'dispatched')
    kwargs = dispatched[0][1]
    assert kwargs['trigger_id'] == 'wake' and kwargs['trigger_type'] == 'schedule'
    assert kwargs['prepared_context'] is prepared
    assert kwargs['provider_override'] == 'claude' and kwargs['model_override'] == 'model'
    assert kwargs['character'] == 'global:owner' and kwargs['strict_character'] is True
    assert 'resume_id' not in kwargs


def test_role_engine_resolution_reuses_precedence_and_opt_in_fallback(context_env, monkeypatch):
    from mc import engine_fallback
    ar, project = context_env['ar'], context_env['project']
    monkeypatch.setattr(ar, '_resolve_character', lambda *a, **k: (
        {'name': 'owner', 'engine': {'provider': 'claude', 'model': 'sonnet', 'effort': 'low'}}, 'voice'))
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *a: '')
    services = d.SchedulerServices(None, lambda *a: '')
    row = {'character': 'global:owner', 'model': 'opus', 'effort': 'high'}
    selection, meta, body = services.resolve(project, row)
    assert selection == d.Selection('global:owner', 'claude', 'opus', 'high') and body == 'voice'
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **k: 'quota blocked')
    monkeypatch.setattr(engine_fallback, 'resolve_fallback', lambda *a: None)
    with pytest.raises(engine_fallback.EngineFallbackBlocked):
        services.resolve(project, row)
    monkeypatch.setattr(engine_fallback, 'resolve_fallback',
                        lambda *a: {'provider': 'codex', 'model': ''})
    selection, meta, body = services.resolve(project, row)
    assert selection.provider == 'codex' and selection.effort == 'high'
    assert meta['_role_requested_selection']['provider'] == 'claude'
    assert meta['_role_engine_fallback'] == 'quota blocked'


def test_claim_write_failure_before_or_after_launch_holds_recovery(integrated, env, monkeypatch):
    services, store = env
    def fail_accept(*args):
        raise OSError('accept checkpoint unavailable')
    monkeypatch.setattr(store, 'accept_delivery', fail_accept)
    with pytest.raises(OSError, match='checkpoint'):
        run(env)
    assert store.read('project', 'wake')['claim']['state'] == 'delivery_unknown'
    assert store.read('project', 'wake')['refresh_watermark'] is None
    services.complete()
    with pytest.raises(d.RoleBusy):
        run(env)
    assert len(services.deliveries) == 1


def test_guard_is_rechecked_before_continuation(integrated, env):
    services, store = env
    run(env)
    services.complete()
    services.guard_error = 'guard was removed between cycles'
    watermark = store.read('project', 'wake')['refresh_watermark']
    with pytest.raises(RoleError, match='guard was removed'):
        run(env)
    assert len(services.deliveries) == 1 and store.read('project', 'wake')['refresh_watermark'] == watermark


@pytest.mark.parametrize('provider', ['gemini', 'qwen'])
def test_native_guard_uses_existing_loader_preflight(provider, monkeypatch, tmp_path):
    from mc import agent_runtime, vendor_fence, state
    monkeypatch.setitem(state.CONFIG, 'fence_unattended_enabled', True)
    monkeypatch.setattr(agent_runtime, 'get_runtime', lambda p: SimpleNamespace(resolve_binary=lambda: tmp_path / p))
    monkeypatch.setattr(agent_runtime, '_inject_guardrail_env', lambda p, e: e)
    checks = []
    monkeypatch.setattr(vendor_fence, 'prepare_env', lambda *a, **k: checks.append((a, k)))
    d.SchedulerServices(None, lambda *a: '').guard(
        {'project_path': str(tmp_path)}, d.Selection('global:owner', provider, '', ''), None)
    assert checks[0][0][0] == provider and checks[0][0][1]['trigger_type'] == 'schedule'
    assert checks[0][1]['binary'] == tmp_path / provider


@pytest.mark.parametrize('provider', ['claude', 'codex'])
def test_shared_guard_self_test_failure_blocks_role(provider, monkeypatch, tmp_path):
    from mc import agent_runtime, guardrail_hooks, fence_fail_closed, state
    monkeypatch.setitem(state.CONFIG, 'fence_unattended_enabled', True)
    monkeypatch.setattr(agent_runtime, 'get_runtime', lambda p: SimpleNamespace(resolve_binary=lambda: tmp_path / p))
    monkeypatch.setattr(guardrail_hooks, 'launch_file_if_exists', lambda p: tmp_path / 'settings.json')
    monkeypatch.setattr(fence_fail_closed, 'fail_closed_settings_path', lambda p: p)
    monkeypatch.setattr(fence_fail_closed, 'claude_fence_self_test', lambda: (False, 'missing fence token'))
    monkeypatch.setattr(guardrail_hooks, 'codex_fence_self_test', lambda: (False, 'missing fence token'))
    with pytest.raises(RoleError, match='missing fence token'):
        d.SchedulerServices(None, lambda *a: '').guard(
            {'project_path': str(tmp_path)}, d.Selection('global:owner', provider, '', ''), None)


@pytest.mark.parametrize('case', ['missing_wake', 'paused', 'condition'])
def test_timer_bookkeeping_is_persisted_without_firing(case, monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from mc.blueprints import scheduler_routes as sr
    path = tmp_path / 'schedules.json'
    row = {'id': 'role', 'project_id': 'p', 'enabled': True, 'schedule_type': 'interval',
           'interval_minutes': 60, 'steward_role': {}, 'last_run': None,
           'next_run': None if case == 'missing_wake' else '2000-01-01T00:00:00Z'}
    path.write_text(json.dumps([row]))
    monkeypatch.setattr(sr, 'SCHEDULES_PATH', path)
    monkeypatch.setattr(d, 'scheduler_fire', lambda *a, **k: pytest.fail('should not fire'))
    def condition(check_row, now):
        check_row['next_run'] = '2099-01-01T00:00:00Z'
        return case == 'condition'
    monkeypatch.setattr(sr, '_skipped_by_condition', condition)
    d.timer_tick(sr, row, datetime.now(timezone.utc), case == 'paused')
    saved = json.loads(path.read_text())[0]
    assert saved['next_run'] > '2000-01-01T00:00:00Z'
    assert saved['last_run'] is None and saved['enabled'] is True


@pytest.mark.parametrize('provider', ['codex', 'qwen', 'gemini'])
@pytest.mark.parametrize('native_id', ['', 'native-thread'])
def test_actual_native_prompt_marker_and_ack(provider, native_id, spawns, monkeypatch):
    from mc import agent_runtime
    from mc.resume_context import strip_resume_context, context_sha
    runtime = agent_runtime.get_runtime(provider)
    monkeypatch.setattr(runtime, 'resolve_binary', lambda: provider)
    # Guard capability is tested separately; this fixture launches no CLI.
    session = {'trigger_type': 'manual', 'task': '[Steward cycle] CURRENT TASK'}
    d.begin_native_delivery(session)
    runtime.dispatch(project_path='.', project_id='p', task=session['task'],
                     system_prompt='CURRENT PERSONA AND RULES', resume_id=native_id,
                     session_dict=session, mc_session_id='test')
    d.wait_native_delivery(session, timeout=0.1)
    prompt = spawns[-1][1].stdin.getvalue()
    assert prompt.startswith('[Steward cycle]\n\n')
    assert 'CURRENT PERSONA AND RULES' in prompt and prompt.endswith(session['task'])
    if native_id:
        assert strip_resume_context(prompt) == session['task']
    assert session['_ctx_delivered_sha'] == context_sha(session['_system_prompt'])


def test_actual_claude_auto_fresh_revival_marker_and_ack(revive_env, monkeypatch, tmp_path):
    ar = revive_env
    from mc import launch_marker
    import io
    class Pipe(io.StringIO):
        def close(self):
            pass
    pipe = Pipe()
    proc = SimpleNamespace(pid=555, stdin=pipe, stdout=iter([]), poll=lambda: None)
    monkeypatch.setattr(ar.subprocess, 'Popen', lambda *a, **k: proc)
    monkeypatch.setattr(ar, '_auto_fresh_trigger', lambda *a, **k: ('bytes', 100))
    monkeypatch.setattr(ar, '_auto_fresh_handoff', lambda *a, **k: ('CURRENT HANDOFF', '', 'handoff'))
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *a, **k: [])
    monkeypatch.setattr(launch_marker, 'launch_cmd', lambda cmd, *a: cmd)
    monkeypatch.setattr(ar._memory_turn, 'seed_delivered', lambda *a: None)
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid, **_kw: [{
        'session_id': 'prior', 'claude_session_id': 'aaaaaaaa-1111-2222-3333-444444444444',
        'trigger_type': 'schedule', 'trigger_id': 'wake', 'provider': 'claude'}])
    monkeypatch.setattr(ar, 'get_manager', lambda pid: SimpleNamespace(
        lock=threading.RLock(), session_ids=set(), ensure_guardian=lambda: None))
    prepared = d.Prepared({'name': 'owner'}, {'provider': 'claude'}, 'RULES')
    result = ar._revive_from_agent_log('p', 'prior', '[Steward cycle] CURRENT TASK',
        {'id': 'p', 'project_path': str(tmp_path), 'use_streaming_agent': True}, role_context=prepared)
    assert result
    delivered = json.loads(pipe.getvalue())['message']['content']
    assert delivered.startswith('[Steward cycle]\n\nCURRENT HANDOFF')
    assert delivered.endswith('CURRENT TASK')


@pytest.mark.parametrize('damage', ['lost_claim', 'lost_session', 'wrong_session', 'unknown_field'])
def test_partial_checkpoint_cannot_become_a_first_fire(integrated, env, damage):
    services, store = env
    run(env)
    services.complete()
    path = store.path('project', 'wake')
    record = json.loads(path.read_text())
    if damage == 'lost_claim':
        record.pop('claim')
    elif damage == 'lost_session':
        record.pop('current_session_id')
    elif damage == 'wrong_session':
        record['claim']['unknown_field'] = 'foreign'
    else:
        record['unknown'] = 'field'
    path.write_text(json.dumps(record))
    with pytest.raises(RoleError, match='state|claim'):
        run(env)
    assert len(services.deliveries) == 1


def test_legacy_cycle_with_old_timestamp_prefix_still_blocks_role(monkeypatch):
    from mc import state, steward_role_writes as writes
    monkeypatch.setattr(writes, '_schedules', lambda: [
        {'id': 'legacy', 'project_id': 'p', 'steward': True, 'enabled': False}])
    session = {'project_id': 'p', 'trigger_type': 'schedule', 'trigger_id': 'legacy',
               'task': '[Scheduled run]\n[Steward cycle] old task', 'status': 'running'}
    monkeypatch.setitem(state.agent_sessions, 'old-legacy', session)
    services = d.SchedulerServices(None, lambda *a: '')
    assert services.active('p')
    session['status'] = 'idle'
    assert not services.active('p')


@pytest.mark.parametrize('legacy_file', ['project.json', 'project.claim'])
def test_legacy_sr3_state_cannot_be_silently_forgotten(integrated, env, legacy_file):
    services, store = env
    old = store.data_root / 'steward/dispatch' / legacy_file
    old.parent.mkdir(parents=True)
    old.write_text('ambiguous prior dispatch')
    with pytest.raises(d.RoleBusy, match='legacy SR-3 checkpoint'):
        run(env)
    assert old.read_text() == 'ambiguous prior dispatch'
    assert not services.deliveries


def test_rebound_assignment_cannot_bypass_prior_claim(integrated, env):
    services, store = env
    assert store.acquire_claim('project', 'prior-wake', 'held', 'schedule')[0]
    with pytest.raises(d.RoleBusy, match='another assignment'):
        run(env)
    assert not services.deliveries
    assert store.read('project', 'prior-wake')['claim']['id'] == 'held'
    assert store.read('project', 'wake')['refresh_watermark'] is None


@pytest.mark.parametrize('method', ['record_attempt', 'record_session', 'accept_delivery'])
def test_sr4_persistence_failure_never_consumes_watermark(integrated, env, monkeypatch, method):
    services, store = env
    def fail(*args, **kwargs):
        raise RoleError('state disk unavailable')
    monkeypatch.setattr(store, method, fail)
    with pytest.raises(RoleError, match='disk unavailable'):
        run(env)
    expected = 0 if method == 'record_attempt' else 1
    assert len(services.deliveries) == expected
    record = store.read('project', 'wake')
    assert record['refresh_watermark'] is None
    assert record['claim']['state'] == ('preparing' if expected == 0 else 'delivery_unknown')
    services.complete()
    with pytest.raises(d.RoleBusy):
        d.prepare_and_fire('wake', services, d.RoleStateStore(store.data_root))
    assert len(services.deliveries) == expected


@pytest.mark.parametrize('operation', ['journal', 'notifier'])
def test_sr4_journal_or_notifier_failure_blocks_delivery(integrated, env, monkeypatch, operation):
    services, store = env
    if operation == 'journal':
        def fail(*args, **kwargs):
            raise OSError('journal disk unavailable')
        monkeypatch.setattr(d.comms, 'append_journal', fail)
    else:
        monkeypatch.setattr(d.comms, 'record_lifecycle', lambda *a, **k: {
            'ok': False, 'error': 'notifier disk unavailable'})
    with pytest.raises((RoleError, OSError), match='disk unavailable'):
        run(env)
    assert not services.deliveries
    assert store.read('project', 'wake')['refresh_watermark'] is None


def test_pending_reply_error_blocks_before_handoff(integrated, env, monkeypatch):
    services, store = env
    monkeypatch.setattr(d.comms, 'check_pending_replies', lambda *a, **k: [
        d.comms.ReplyCheck('decision', 'error', detail='digest offline')])
    with pytest.raises(RoleError, match='reply check failed'):
        run(env)
    assert not services.deliveries and not services.events
    assert store.read('project', 'wake')['claim'] is None


def test_journal_and_notifier_use_sr4_canonical_assignment(integrated, env):
    services, store = env
    first = run(env)
    journal = Path(services.project['project_path']) / services.row['steward_role']['journal_path']
    text = journal.read_text()
    assert all(f'| {event} | schedule wake' in text for event in ('first_fire', 'started', 'accepted'))
    assert json.dumps(asdict(services.selection), sort_keys=True) in text
    assert store.read('project', 'wake')['current_session_id'] == first['session_id']
    assert set(store.read('project', 'wake')['snapshot_hashes']) == {'selection', 'context', 'task', 'character'}
    assert not (store.data_root / 'steward/dispatch').exists()
    services.complete()
    run(env)
    text = journal.read_text()
    assert '| completed | schedule wake' in text and '| continue | schedule wake' in text
    assert store.read('project', 'wake')['last_completed_at']


def test_failure_notifier_does_not_hide_original_error(env, monkeypatch):
    services, store = env
    scheduler = SimpleNamespace(load_project=lambda pid: services.project,
                                SCHEDULES_PATH=store.data_root / 'schedules.json')
    result = {'ok': False, 'busy': False, 'error': 'guard refused'}
    d._failure_notification(scheduler, services.row, result)
    assert store.read('project', 'wake')['last_result']['event'] == 'blocked_guard'
    monkeypatch.setattr(d.comms, 'record_lifecycle', lambda *a, **k: {'ok': False, 'error': 'disk'})
    d._failure_notification(scheduler, services.row, result)
    assert result['error'] == 'guard refused' and result['lifecycle_error'] == 'disk'


@pytest.mark.parametrize('decision,mixed', [('approved', False), ('approved', True), ('no_reply_found', False)])
def test_cycle_reads_actual_sr4_pending_ask_without_granting_authority(integrated, env, monkeypatch, decision, mixed):
    from test_steward_role_comms import FakeMailer, FakeDigest
    services, store = env
    outcome = d.comms.raise_decision(services.project, services.row,
        d.comms.DecisionRequest('change', 'Change needed', 'Approve the change?'),
        d.comms.RouteContext(), store=store, mailer=FakeMailer(), digest_reader=FakeDigest())
    assert outcome.status == 'sent'
    reader = FakeDigest(decision=decision, mixed=mixed, text='DO NOT PERSIST THIS BODY')
    monkeypatch.setattr(d.comms, 'default_digest_reader', reader)
    run(env)
    assert len(reader.queries) == 1 and outcome.decision_id in reader.queries[0]
    check = services.events[0][1]['pending_replies'][0]
    assert check['grants_authority'] is False
    assert check['status'] == ('answered' if decision == 'approved' and not mixed else
                               'unclear' if mixed else 'no_reply')
    assert 'DO NOT PERSIST THIS BODY' not in store.path('project', 'wake').read_text()
    journal = Path(services.project['project_path']) / services.row['steward_role']['journal_path']
    assert 'DO NOT PERSIST THIS BODY' not in journal.read_text()


def test_failure_notification_does_not_hold_schedule_lock_while_waiting_for_state(env, monkeypatch):
    from mc import steward_role_writes as writes
    services, store = env
    scheduler = SimpleNamespace(SCHEDULES_PATH=store.data_root / 'schedules.json',
        load_project=lambda pid: services.project, _save_schedules=lambda rows: None)
    monkeypatch.setattr(writes, '_schedules', lambda: [copy.deepcopy(services.row)])
    monkeypatch.setattr(d, 'fire', lambda *a: (_ for _ in ()).throw(d.RoleBusy('busy')))
    notified = threading.Event()
    original = d._failure_notification
    def notification(*args):
        notified.set()
        return original(*args)
    monkeypatch.setattr(d, '_failure_notification', notification)
    results = []
    with store.lock('project', 'wake'):
        worker = threading.Thread(target=lambda: results.append(d.scheduler_fire(scheduler, 'wake')))
        worker.start()
        assert notified.wait(5)
        # The notifier is blocked on this thread's assignment lock. Preparing a
        # cycle must still be able to re-read its schedule without deadlocking.
        acquired = writes.WRITE_LOCK.acquire(timeout=1)
        if acquired:
            writes.WRITE_LOCK.release()
    worker.join(5)
    assert acquired and not worker.is_alive()
    assert results[0]['busy'] and results[0]['error'] == 'busy'
