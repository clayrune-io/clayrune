"""Passive vendor evidence, isolated storage and actual reader integration."""
from __future__ import annotations

import importlib
import io
import json
import threading
from types import SimpleNamespace
from typing import Any, cast

import pytest

from mc import state, test_run_detection as detection
from test_test_run_routes import env as route_env


def use(call='call-1', command='python -m pytest tests/test_one.py', name: Any = 'Bash'):
    return {'type': 'tool_use', 'id': call, 'name': name, 'input': {'command': command}}


def result(call='call-1', **fields):
    return {'type': 'tool_result', 'tool_use_id': call, **fields}


def envelope(*blocks, kind='assistant'):
    return {'type': kind, 'message': {'content': list(blocks)}}


@pytest.fixture
def observer(tmp_path, monkeypatch):
    importlib.import_module('server')  # Complete real server wiring before isolation.
    service = detection.DetectionObserver(tmp_path, lambda _: {'project_path': str(tmp_path)})
    monkeypatch.setattr(detection, '_observer', service)
    monkeypatch.setitem(state.CONFIG, detection.FLAG, True)
    session = {'project_id': 'p', 'session_id': 's', 'proc': SimpleNamespace(), 'log_lines': []}
    monkeypatch.setitem(state.agent_sessions, 's', session)
    yield service, session
    service.close()


def emit(observer, provider, raw):
    service, session = observer
    detection.observe(session, provider, raw)
    service.pending.join()
    return service.store.list_runs('p')


@pytest.mark.parametrize('command', [
    'pytest tests/a.py', 'python -m pytest -q', 'python3.12 -m pytest',
    '"C:\\venv path\\python.exe" -m pytest tests/a.py',
    ['C:\\venv path\\python.exe', '-m', 'pytest', 'space in test.py'],
    'node tools/smoke/boot.mjs', 'node foo.test.cjs', 'node --test',
    'npm test', 'npm run test:unit', 'npx playwright test', 'pnpm test',
])
def test_direct_candidates(command):
    assert detection.candidate('shell', command)


@pytest.mark.parametrize('command', [
    'echo pytest', 'rg pytest tests', 'node ordinary.mjs', 'python nested.py',
    'cd repo && pytest', 'pytest; echo done', 'pytest | cat', 'pytest > out',
    'bash -c "pytest"', 'python tools/test-run.py -- pytest',
    ['python', 'C:\\install\\tools\\test-run.py', '--', 'pytest'],
    'curl -X POST localhost:5199/api/project/p/agent/s/job -d "pytest"',
    'npm run build', 'pytest $(echo tests)', 'pytest\npytest', None, {},
])
def test_uncertain_wrapper_job_commands_are_excluded(command):
    assert not detection.candidate('shell', command)


def test_all_blocks_dedupe_and_independent_identical_attempts(observer):
    raw = envelope({'type': 'text', 'text': 'pytest passed'}, use('a'), 'bad', use('b'))
    for _ in range(3):
        rows = emit(observer, 'claude', raw)
    assert len(rows) == 2
    rows = emit(observer, 'claude', envelope(result('a', exit_code=0), result('b', exit_code=7), kind='user'))
    assert {row['state'] for row in rows} == {'passed', 'failed'}
    for row in rows:
        assert row['source'] == 'generic-fallback'
        assert row['selected_total'] is None and row['eta_seconds'] is None
        assert row['completed'] == 0 and row['percent'] is None
        assert not any(row['counts'].values())
        assert row['capture'] == 'partial' and row['process_identity'] is None
    service, _ = observer
    encoded = json.dumps([service.store.events('p', row['run_id']) for row in rows])
    assert 'python -m pytest' not in encoded
    assert len(emit(observer, 'claude', raw)) == 2


@pytest.mark.parametrize('fields', [
    {}, {'is_error': False}, {'is_error': True}, {'exit_code': True},
    {'exit_code': '0'}, {'content': 'Process exited with code 0\n100 passed'},
    {'content': '{"exit_code":0}'}, {'status': 'success'},
])
def test_missing_or_noninteger_exit_is_unknown_never_pass(observer, fields):
    emit(observer, 'claude', envelope(use()))
    row = emit(observer, 'claude', envelope(result(**fields), kind='user'))[0]
    assert row['state'] == 'lost' and row['exit_code'] is None
    assert any(d['code'] == 'exit_code_unavailable' for d in row['diagnostics'])


def test_exit_is_not_taken_from_command_inputs(observer):
    block = use()
    block['input']['exit_code'] = 0
    row = emit(observer, 'claude', envelope(block))[0]
    assert row['state'] == 'running' and row['exit_code'] is None


def test_qwen_raw_result_suppressed_by_parser_still_delivers(observer):
    from mc.agent_runtime import QwenRuntime
    emit(observer, 'qwen', envelope(use()))
    raw = envelope(result(metadata={'exit_code': 3}, content='FAIL secret'), kind='user')
    assert QwenRuntime().parse_event(json.dumps(raw)) is None
    row = emit(observer, 'qwen', raw)[0]
    assert row['state'] == 'failed' and row['exit_code'] == 3
    assert 'secret' not in json.dumps(observer[0].store.events('p', row['run_id']))


@pytest.mark.parametrize('code,expected', [(0, 'passed'), (19, 'failed'), (None, 'lost')])
def test_codex_new_start_update_complete_and_completed_only(observer, code, expected):
    item = {'type': 'command_execution', 'id': 'item_0', 'command': 'pytest tests/a.py'}
    for kind in ['item.started', 'item.updated', 'item.completed', 'item.completed']:
        rows = emit(observer, 'codex', {'type': kind, 'item': {**item, 'exit_code': code,
                                                            'aggregated_output': 'ALL PASS'}})
    assert len(rows) == 1 and rows[0]['state'] == expected
    assert len(emit(observer, 'codex', {'type': 'item.completed', 'item': {**item, 'id': 'other',
                                                                                 'exit_code': 0}})) == 2


def test_codex_old_all_message_blocks_and_separate_function_output(observer):
    raw = {'type': 'item.completed', 'item': {'type': 'message', 'content': [
        {'type': 'output_text', 'text': 'hello'}, use('old-a'), use('old-b')]}}
    assert len(emit(observer, 'codex', raw)) == 2
    emit(observer, 'codex', {'type': 'item.completed', 'item': {
        'type': 'function_call', 'id': 'declaration-item', 'call_id': 'f', 'name': 'exec_command',
        'arguments': json.dumps({'cmd': 'pytest tests/a.py'})}})
    rows = emit(observer, 'codex', {'type': 'item.completed', 'item': {
        'type': 'function_call_output', 'call_id': 'f', 'exit_code': 5}})
    assert len(rows) == 3 and any(r['exit_code'] == 5 for r in rows)


def test_gemini_status_does_not_prove_exit(observer):
    emit(observer, 'gemini', {'type': 'tool_use', 'tool_id': 'g', 'tool_name': 'run_shell_command',
                             'parameters': {'command': 'node tools/smoke/boot.mjs'}})
    row = emit(observer, 'gemini', {'type': 'tool_result', 'tool_id': 'g', 'status': 'success'})[0]
    assert row['state'] == 'lost'


def test_wrapper_and_job_tool_calls_never_deliver_or_join(observer):
    emit(observer, 'claude', envelope(use(command='python tools/test-run.py -- pytest'),
                                    use('job', 'pytest', name='clayrune_start_job')))
    assert emit(observer, 'claude', envelope(result(), result('job', exit_code=0), kind='user')) == []


def test_reused_codex_item_id_new_process_or_owner_is_new_attempt(observer, monkeypatch):
    _, session = observer
    raw = {'type': 'item.completed', 'item': {'type': 'command_execution', 'id': 'item_0',
                                            'command': 'pytest', 'exit_code': 0}}
    assert len(emit(observer, 'codex', raw)) == 1
    session['proc'] = SimpleNamespace()
    assert len(emit(observer, 'codex', raw)) == 2
    other = {**session, 'session_id': 'other'}
    monkeypatch.setitem(state.agent_sessions, 'other', other)
    detection.observe(other, 'codex', raw)
    observer[0].pending.join()
    assert len(observer[0].store.list_runs('p')) == 3


@pytest.mark.parametrize('policy', ['off', 'nonboolean', 'incognito', 'sandbox', 'project', 'owner'])
def test_policy_refusal_no_storage(observer, monkeypatch, policy):
    service, session = observer
    if policy == 'off':
        monkeypatch.setitem(state.CONFIG, detection.FLAG, False)
    elif policy == 'nonboolean':
        monkeypatch.setitem(state.CONFIG, detection.FLAG, 1)
    elif policy == 'incognito':
        session['incognito'] = True
    elif policy == 'sandbox':
        session['_codex_unattended_sandbox'] = True
    elif policy == 'project':
        service.load_project = lambda _: {'_is_incognito_project': True}
    else:
        monkeypatch.delitem(state.agent_sessions, 's')
    detection.observe(session, 'claude', envelope(use()))
    service.pending.join()
    assert service._store is None
    assert not (service.data_root / 'test_runs').exists()


def test_slow_store_does_not_block_reader_and_capacity_is_observable(observer, monkeypatch):
    service, session = observer
    entered, release = threading.Event(), threading.Event()
    original = service._apply
    def slow(*args):
        entered.set()
        assert release.wait(5)
        original(*args)
    monkeypatch.setattr(service, '_apply', slow)
    logs = []
    monkeypatch.setattr(detection, '_log', lambda msg, **_: logs.append(msg))
    try:
        detection.observe(session, 'claude', envelope(use()))
        assert entered.wait(2)
        for i in range(detection.MAX_PENDING + 5):
            detection.observe(session, 'claude', envelope(use(str(i))))
        assert service.pending.qsize() == detection.MAX_PENDING
        assert any('queue full' in msg for msg in logs)
    finally:
        # Avoid hundreds of writes; this check covers bounded handoff only.
        monkeypatch.setitem(state.CONFIG, detection.FLAG, False)
        release.set()
        service.pending.join()


def test_observer_and_storage_errors_are_logged_not_propagated(observer, monkeypatch):
    service, _ = observer
    logs = []
    monkeypatch.setattr(detection, '_log', lambda msg, **_: logs.append(msg))
    def broken(*args, **kwargs):
        raise OSError('secret command output')
    monkeypatch.setattr(service.store, 'create', broken)
    assert emit(observer, 'claude', envelope(use())) == []
    assert any('delivery failed: OSError' in msg for msg in logs)
    monkeypatch.setattr(service, 'observe', broken)
    detection.observe(observer[1], 'claude', envelope(use()))
    assert any('observation failed: OSError' in msg for msg in logs)
    assert not any('secret' in msg for msg in logs)


@pytest.mark.parametrize('raw', ['bad', '[]', 'null', {}, {'message': 'bad'},
                               envelope(None, 'bad', {'type': 'tool_use', 'input': 'bad'})])
def test_malformed_observation_is_harmless(observer, raw):
    assert emit(observer, 'qwen', raw) == []


def test_malformed_tool_name_does_not_hide_later_blocks(observer):
    assert len(emit(observer, 'claude', envelope(use('bad', name=['bad']), use('good')))) == 1


def test_identity_capacity_refuses_new_calls_without_recreating_old_calls(observer, monkeypatch):
    monkeypatch.setattr(detection, 'MAX_IDENTITIES', 1)
    logs = []
    monkeypatch.setattr(detection, '_log', lambda msg, **_: logs.append(msg))
    emit(observer, 'claude', envelope(use('one'), use('two')))
    rows = emit(observer, 'claude', envelope(result('one', exit_code=0), kind='user'))
    assert len(rows) == 1 and rows[0]['state'] == 'passed'
    assert len(emit(observer, 'claude', envelope(use('one')))) == 1
    assert any('identity capacity' in msg for msg in logs)


def test_terminal_eviction_frees_capacity_without_recreating_completed_call(observer, monkeypatch):
    monkeypatch.setattr(detection, 'MAX_IDENTITIES', 1)
    service, _ = observer
    emit(observer, 'claude', envelope(use('one')))
    emit(observer, 'claude', envelope(result('one', exit_code=0), kind='user'))
    old_key = next(iter(service.runs))
    rows = emit(observer, 'claude', envelope(use('two')))
    assert len(rows) == 2 and old_key not in service.runs
    assert len(service.runs) == 1 and not next(iter(service.runs.values())).terminal
    assert len(service._tombstones) == 1
    snapshot = [(row['run_id'], row['seq'], row['state']) for row in rows]
    # Replayed declarations and results must leave stored history unchanged.
    emit(observer, 'claude', envelope(use('one')))
    rows = emit(observer, 'claude', envelope(use('one'), result('one', exit_code=9), kind='user'))
    assert [(row['run_id'], row['seq'], row['state']) for row in rows] == snapshot
    assert old_key not in service.runs


def test_eviction_uses_terminal_transition_order_and_never_evicts_active(observer, monkeypatch):
    monkeypatch.setattr(detection, 'MAX_IDENTITIES', 3)
    service, _ = observer
    emit(observer, 'claude', envelope(use('first'), use('second'), use('active')))
    first, second, active = service.runs
    emit(observer, 'claude', envelope(result('second', exit_code=1), kind='user'))
    emit(observer, 'claude', envelope(result('first'), kind='user'))
    # A repeated result must not refresh a terminal run's age.
    emit(observer, 'claude', envelope(result('second', exit_code=1), kind='user'))
    assert len(emit(observer, 'claude', envelope(use('new')))) == 4
    assert second not in service.runs
    assert first in service.runs and active in service.runs
    assert not service.runs[active].terminal


def test_all_active_identity_capacity_logs_and_skips(observer, monkeypatch):
    monkeypatch.setattr(detection, 'MAX_IDENTITIES', 2)
    service, _ = observer
    logs = []
    monkeypatch.setattr(detection, '_log', lambda msg, **_: logs.append(msg))
    emit(observer, 'claude', envelope(use('one'), use('two')))
    original = dict(service.runs)
    assert len(emit(observer, 'claude', envelope(use('three')))) == 2
    assert service.runs == original and all(not run.terminal for run in service.runs.values())
    assert not service._terminal and not service._tombstones
    assert any('identity capacity reached' in msg for msg in logs)


def test_tombstones_are_bounded_hash_only_fifo_without_replay_refresh(observer, monkeypatch):
    monkeypatch.setattr(detection, 'MAX_IDENTITIES', 1)
    monkeypatch.setattr(detection, 'MAX_TOMBSTONES', 2)
    service, _ = observer
    def complete(call):
        return emit(observer, 'codex', {'type': 'item.completed', 'item': {
            'type': 'command_execution', 'id': call, 'command': 'pytest', 'exit_code': 0}})
    for call in ['one', 'two', 'three']:
        complete(call)
    oldest, newest = service._tombstones
    assert all(len(key) == 64 and set(key) <= set('0123456789abcdef')
               for key in service._tombstones)
    assert all(value is None for value in service._tombstones.values())
    assert len(complete('one')) == 3
    assert list(service._tombstones) == [oldest, newest]
    assert len(complete('four')) == 4
    assert len(service._tombstones) == 2 and oldest not in service._tombstones
    assert next(iter(service._tombstones)) == newest
    assert len(complete('two')) == 4


def test_tombstone_does_not_suppress_same_call_in_another_session(observer, monkeypatch):
    monkeypatch.setattr(detection, 'MAX_IDENTITIES', 1)
    service, session = observer
    emit(observer, 'claude', envelope(use('one'), result('one', exit_code=0)))
    emit(observer, 'claude', envelope(use('two'), result('two', exit_code=0)))
    other = {**session, 'session_id': 'other'}
    monkeypatch.setitem(state.agent_sessions, 'other', other)
    detection.observe(other, 'claude', envelope(use('one')))
    service.pending.join()
    assert len(service.store.list_runs('p')) == 3
    assert next(iter(service.runs))[1] == 'other'


def test_flag_off_does_not_evict_terminal_runs_or_change_tombstones(observer, monkeypatch):
    monkeypatch.setattr(detection, 'MAX_IDENTITIES', 1)
    service, session = observer
    for call in ['one', 'two']:
        emit(observer, 'claude', envelope(use(call), result(call, exit_code=0)))
    original_runs = dict(service.runs)
    original_terminal = list(service._terminal)
    original_tombstones = list(service._tombstones)
    monkeypatch.setitem(state.CONFIG, detection.FLAG, False)
    assert len(emit(observer, 'claude', envelope(use('three')))) == 2
    # The queued worker policy check must also precede eviction.
    service._apply(session, 'launch', 'claude', detection.ToolObservation('four', 'candidate'), 1.0)
    assert service.runs == original_runs
    assert list(service._terminal) == original_terminal
    assert list(service._tombstones) == original_tombstones


def test_failed_startup_event_keeps_later_process_result_partial(observer, monkeypatch):
    service, _ = observer
    apply = service.store.apply
    def failing_start(pid, rid, event):
        if event['type'] == 'run_started':
            raise OSError('private')
        return apply(pid, rid, event)
    monkeypatch.setattr(service.store, 'apply', failing_start)
    emit(observer, 'claude', envelope(use()))
    row = emit(observer, 'claude', envelope(result(exit_code=0), kind='user'))[0]
    assert row['state'] == 'passed' and row['capture'] == 'partial'


def test_observed_output_time_coalesces_updates_and_flushes_completion(observer, monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(detection.time, 'time', lambda: clock[0])
    item = {'type': 'command_execution', 'id': 'i', 'command': 'pytest', 'aggregated_output': 'text'}
    emit(observer, 'codex', {'type': 'item.started', 'item': item})
    clock[0] = 10.1
    assert emit(observer, 'codex', {'type': 'item.updated', 'item': item})[0]['last_output_at'] == 10.0
    clock[0] = 11.0
    assert emit(observer, 'codex', {'type': 'item.updated', 'item': item})[0]['last_output_at'] == 11.0
    clock[0] = 11.1
    assert emit(observer, 'codex', {'type': 'item.completed', 'item': {**item, 'exit_code': 0}})[0]['last_output_at'] == 11.1


def test_disabled_composition_preserves_callback_identity_and_no_observer_work(observer, monkeypatch):
    monkeypatch.setitem(state.CONFIG, detection.FLAG, False)
    callback = lambda *args: None
    assert detection.compose_raw(callback, 'codex') is callback
    assert detection.compose_raw(None, 'qwen') is None
    detection.observe(observer[1], 'claude', envelope(use()))
    assert observer[0]._thread is None and observer[0]._store is None


def test_unsupported_provider_composition_is_unchanged(observer):
    assert detection.compose_raw(None, 'opencode') is None


def test_codex_banner_is_removed_only_for_observation(observer):
    raw = {'type': 'item.completed', 'item': {'type': 'command_execution', 'id': 'item_0',
                                            'command': 'pytest', 'exit_code': 0}}
    row = emit(observer, 'codex', 'Reading prompt from stdin...' + json.dumps(raw))[0]
    assert row['state'] == 'passed'


def test_queued_observations_recheck_restricted_policy(observer, monkeypatch):
    service, session = observer
    entered, release = threading.Event(), threading.Event()
    apply = service._apply
    def hold(*args):
        entered.set()
        assert release.wait(5)
        apply(*args)
    monkeypatch.setattr(service, '_apply', hold)
    try:
        detection.observe(session, 'claude', envelope(use()))
        assert entered.wait(2)
        session['incognito'] = True
    finally:
        release.set()
        service.pending.join()
    assert service._store is None


def test_fallback_visible_through_existing_scoped_read_routes(route_env):
    service = detection._observer
    assert service is not None
    try:
        session = route_env.sessions['s1']
        detection.observe(session, 'claude', envelope(use()))
        detection.observe(session, 'claude', envelope(result(exit_code=9), kind='user'))
        service.pending.join()
        response = route_env.client.get('/api/project/a/test-runs?session=s1')
        assert response.status_code == 200
        rows = response.get_json()['runs']
        assert len(rows) == 1 and rows[0]['state'] == 'failed'
        assert rows[0]['source'] == 'generic-fallback'
        response = route_env.client.get('/api/project/a/test-runs/' + rows[0]['run_id'] + '?session=s1')
        assert response.status_code == 200 and response.get_json()['exit_code'] == 9
    finally:
        service.close()


class FakeProc:
    def __init__(self, records):
        self.stdout = io.StringIO('\n'.join(json.dumps(r) for r in records) + '\n')
        self.pid = -1
    def wait(self):
        return 0
    def poll(self):
        return 0


@pytest.mark.parametrize('reader', ['claude-a', 'claude-b', 'codex', 'qwen', 'gemini'])
@pytest.mark.parametrize('broken_observer', [False, True])
def test_real_readers_keep_agent_verdict_and_existing_raw_callback(observer, monkeypatch,
                                                                 reader, broken_observer):
    from mc import agent_runtime as runtime
    service, session = observer
    if reader.startswith('claude') or reader == 'qwen':
        records = [envelope({'type': 'text', 'text': 'hello'}, use()),
                   envelope(result(content='ALL PASS'), kind='user'),
                   {'type': 'result', 'session_id': 'vendor', 'num_turns': 1}]
    elif reader == 'codex':
        records = [{'type': 'item.completed', 'item': {'type': 'command_execution',
                    'id': 'i', 'command': 'pytest', 'exit_code': 4}},
                   {'type': 'turn.completed', 'usage': {}}]
    else:
        records = [{'type': 'tool_use', 'tool_id': 'g', 'tool_name': 'run_shell_command',
                    'parameters': {'command': 'pytest'}},
                   {'type': 'tool_result', 'tool_id': 'g', 'exit_code': 0},
                   {'type': 'result', 'status': 'success'}]
    logs = []
    monkeypatch.setattr(detection, '_log', lambda msg, **_: logs.append(msg))
    if broken_observer:
        def broken(*args, **kwargs):
            raise OSError('private')
        monkeypatch.setattr(service, 'observe', broken)
    proc = FakeProc(records)
    session.update(proc=proc, status='running', provider=reader.split('-')[0],
                   last_output_time=0.0, last_status_change_time=0.0)
    called = []
    if reader.startswith('claude'):
        server = importlib.import_module('server')
        # Server wiring establishes its own service; restore the isolated fixture.
        monkeypatch.setattr(detection, '_observer', service)
        getattr(server, '_read_agent_stream_b' if reader.endswith('b') else '_read_agent_stream')(proc, session)
    else:
        handle = runtime.SessionHandle(mc_session_id='s', provider=reader, mode='A',
            project_path='/p', project_id='p', session_dict=session,
            meta={'callbacks': {'on_raw_record': lambda *args: called.append(args)}})
        if reader == 'gemini':
            runtime.GeminiRuntime()._read_stream(cast(Any, proc), handle)
        else:
            engine = runtime.CodexRuntime() if reader == 'codex' else runtime.QwenRuntime()
            runtime._mode_a_reader(cast(Any, proc), handle, engine)
            assert len(called) == len(records)
            assert json.loads(called[0][0]) == records[0]
    service.pending.join()
    assert not session.get('_capture_error')
    assert not any('[stream error:' in line for line in session['log_lines'])
    assert session.get('status') != 'blocked'
    if broken_observer:
        assert any('observation failed' in msg for msg in logs)
        assert service.store.list_runs('p') == []
    else:
        rows = service.store.list_runs('p')
        assert len(rows) == 1
        assert rows[0]['state'] == ('failed' if reader == 'codex' else
                                    'passed' if reader == 'gemini' else 'lost')


def test_composed_capture_error_keeps_its_existing_semantics(observer):
    def previous(*args):
        raise RuntimeError('existing capture failure')
    callback = detection.compose_raw(previous, 'qwen')
    assert callback is not None
    with pytest.raises(RuntimeError, match='existing capture failure'):
        callback(json.dumps(envelope(use())), 0, 's', observer[1])
    observer[0].pending.join()
    assert len(observer[0].store.list_runs('p')) == 1
