"""Offline integration seams for durable child completion delivery."""
import json
import threading
import time
from unittest.mock import patch
import pytest
from flask import Flask
from types import SimpleNamespace

from mc.delegation_delivery import (DeliveryBlocked, DeliveryDeferred,
                                    DeliveryStore, callback_payload, drain_once,
                                    event_id_for_turn)


def payload(summary='full result ' * 500):
    return {'event_id': 'child:turn:1', 'child_session_id': 'child',
            'who': 'worker', 'status': 'completed', 'task': 'task',
            'summary': summary, 'provider': 'codex', 'model': 'gpt-5.6-luna',
            'message': summary}


def test_conflicting_duplicate_is_rejected_and_exact_duplicate_is_idempotent(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.db')
    store.enqueue('e1', 'p', 'parent', payload())
    assert store.accept('e1', 'p', 'parent', payload()) is True
    assert store.accept('e1', 'p', 'parent', payload()) is False
    altered = dict(payload(), summary='tampered')
    try:
        store.accept('e1', 'p', 'parent', altered)
    except ValueError:
        pass
    else:
        raise AssertionError('conflicting event was accepted')
    assert store.status('outbox', 'e1', 'other') is None
    store.retry('outbox', 'e1', 'other')
    try:
        store.accept('e1', 'other', 'parent', payload())
    except ValueError:
        pass
    else:
        raise AssertionError('cross-project event was accepted')


def test_fenced_lease_cannot_ack_after_new_claim(tmp_path):
    now = [100.0]
    store = DeliveryStore(tmp_path / 'delivery.db', clock=lambda: now[0])
    store.enqueue('e1', 'p', 'parent', payload())
    first = store.claim_outbox()
    assert first
    now[0] += 31
    second = store.claim_outbox()
    assert second and second['fence_token'] != first['fence_token']
    store.ack_outbox('e1', first['fence_token'])
    assert store.status('outbox', 'e1', 'p')['state'] == 'pending'
    store.ack_outbox('e1', second['fence_token'])
    assert store.status('outbox', 'e1', 'p')['state'] == 'delivered'


def test_transport_timeout_retries_receipt_but_not_parent_action(tmp_path):
    now = [100.0]
    store = DeliveryStore(tmp_path / 'delivery.db', clock=lambda: now[0])
    store.enqueue('e1', 'p', 'parent', payload())
    calls = []

    def send(row):
        calls.append(row['event_id'])
        if len(calls) == 1:
            raise TimeoutError('receiver response lost')

    assert drain_once(store, send_outbox=send,
                      process_inbox=lambda row: None) == 0
    assert store.status('outbox', 'e1', 'p')['state'] == 'pending'
    now[0] += 2
    assert drain_once(store, send_outbox=send,
                      process_inbox=lambda row: None) == 1
    assert calls == ['e1', 'e1']


def test_receiver_crash_reconciles_to_uncertain_without_replay(tmp_path):
    now = [100.0]
    store = DeliveryStore(tmp_path / 'delivery.db', clock=lambda: now[0])
    store.enqueue('e1', 'p', 'parent', payload())
    store.accept('e1', 'p', 'parent', payload())
    claimed = store.claim_inbox()
    assert claimed and claimed['state'] == 'dispatch_intent'
    now[0] += 31
    reopened = DeliveryStore(tmp_path / 'delivery.db', clock=lambda: now[0])
    assert reopened.reconcile() == 1
    assert reopened.status('inbox', 'e1', 'p')['state'] == 'uncertain'
    assert reopened.claim_inbox() is None


def test_submitted_ack_requires_real_write_and_is_idempotent(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.db')
    store.enqueue('e-ack', 'p', 'parent', payload())
    store.accept('e-ack', 'p', 'parent', payload())
    row = store.claim_inbox()
    with pytest.raises(ValueError, match='stdin write acknowledgment'):
        store.submit_inbox('e-ack', row['fence_token'], {'stdin_write_ack': 'failed'})
    assert store.status('inbox', 'e-ack', 'p')['state'] == 'dispatch_intent'
    store.submit_inbox('e-ack', row['fence_token'], {'stdin_write_ack': 'written'})
    store.submit_inbox('e-ack', row['fence_token'], {'stdin_write_ack': 'written'})
    assert store.status('inbox', 'e-ack', 'p')['state'] == 'submitted'


def test_blocked_and_busy_parent_are_distinct_and_recoverable(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.db')
    store.enqueue('e1', 'p', 'parent', payload())
    store.accept('e1', 'p', 'parent', payload())
    row = store.claim_inbox()
    store.finish_inbox('e1', row['fence_token'], 'parent missing', state='blocked')
    assert store.status('inbox', 'e1', 'p')['state'] == 'blocked'
    store.retry('inbox', 'e1', 'p')
    row = store.claim_inbox()
    store.finish_inbox('e1', row['fence_token'], 'parent busy', state='pending')
    assert store.status('inbox', 'e1', 'p')['state'] == 'pending'


def test_drain_is_idempotent_concurrent_and_preserves_full_provider_payload(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.db')
    store.enqueue(event_id_for_turn('child', 2), 'p', 'parent', payload())
    sent = []
    lock = threading.Lock()

    def send(row):
        with lock:
            sent.append(json.loads(row['payload'])['payload'])

    def process(row):
        raise DeliveryBlocked('missing parent')

    threads = [threading.Thread(target=drain_once, kwargs={
        'store': store, 'send_outbox': send, 'process_inbox': process}) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(sent) == 1
    assert sent[0]['provider'] == 'codex'
    assert len(sent[0]['summary']) > 1500
    assert store.status('outbox', 'child:turn:2', 'p')['state'] == 'delivered'


def test_enqueue_failure_keeps_completion_recoverable_in_agent_log(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.db')
    exact = dict(payload('exact result ' * 500), event_id=event_id_for_turn('child', 3))
    source = [{'session_id': 'child', 'spawned_by_session_id': 'parent',
               'status': 'completed', 'delegation_turn': 3,
               'delegation_completion': exact}]
    assert store.recover_outbox(source, 'p') == 1
    reopened = DeliveryStore(tmp_path / 'delivery.db')
    row = reopened.status('outbox', 'child:turn:3', 'p')
    assert row is not None
    assert json.loads(row['payload'])['payload'] == exact
    assert len(json.loads(row['payload'])['payload']['summary']) > 1500


def test_recovery_ignores_pending_or_legacy_truncated_rows(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.db')
    assert store.recover_outbox([{
        'session_id': 'child', 'spawned_by_session_id': 'parent',
        'status': 'in_progress', 'summary': 'not a result'}], 'p') == 0
    assert store.recover_outbox([{
        'session_id': 'child', 'spawned_by_session_id': 'parent',
        'status': 'completed', 'summary': 'truncated'}], 'p') == 0


def test_real_completion_sources_survive_enqueue_fault_and_next_turn(monkeypatch, tmp_path):
    from mc.blueprints import agent_routes as ar
    store = DeliveryStore(tmp_path / 'delivery.db')
    monkeypatch.setattr(ar, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(ar, '_delivery_store', store)
    monkeypatch.setattr(ar, '_write_session_memory', lambda *args, **kwargs: True)
    monkeypatch.setattr(ar, '_extract_transcript_telemetry', lambda *args: {})
    monkeypatch.setattr(store, 'enqueue', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
    for turn in (1, 2):
        ar._log_agent_completion({
            'project_id': 'p', 'session_id': 'child',
            '_notify_session': 'parent', '_delegation_turn': turn,
            'status': 'completed', 'task': f'task {turn}',
            'provider': 'codex', 'agent_model': 'gpt-5.6-luna',
            'log_lines': [f'exact result {turn}'],
        })
    reopened = DeliveryStore(tmp_path / 'delivery.db')
    assert reopened.recover_sources() == 2
    for turn in (1, 2):
        row = reopened.status('outbox', f'child:turn:{turn}', 'p')
        assert row is not None
        data = json.loads(row['payload'])['payload']
        assert data['summary'] == f'exact result {turn}'


def test_missing_parent_never_calls_send_or_fresh_dispatch(tmp_path, monkeypatch):
    from mc.blueprints import agent_routes as ar
    store = DeliveryStore(tmp_path / 'delivery.db')
    monkeypatch.setattr(ar, '_delivery_store', store)
    store.enqueue('child:turn:1', 'p', 'missing', payload())
    store.accept('child:turn:1', 'p', 'missing', payload())
    claimed = store.claim_inbox()
    assert claimed
    before = dict(ar.agent_sessions)
    ar.agent_sessions.clear()
    row = claimed
    with patch('urllib.request.urlopen') as transport:
        try:
            ar._process_inbox(row)
        except DeliveryBlocked:
            pass
        else:
            raise AssertionError('missing parent was not blocked')
        transport.assert_not_called()
    ar.agent_sessions.clear()
    ar.agent_sessions.update(before)


def test_busy_parent_defers_and_completed_mode_a_parent_is_submitted(monkeypatch, tmp_path):
    from flask import Flask
    from mc.blueprints import agent_routes as ar
    from types import SimpleNamespace
    import tempfile
    from pathlib import Path
    ar._delegation_app = Flask('test-delegation')
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *args: '')
    monkeypatch.setattr(ar, 'load_project', lambda project: {'id': project,
                      'project_path': tempfile.gettempdir()})
    monkeypatch.setattr(ar, 'get_manager', lambda project: SimpleNamespace(lock=threading.RLock()))
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *args: None)
    class Runtime:
        def write_followup(self, handle, message):
            handle.session_dict['provider_session_id'] = 'parent-provider-thread'
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda provider: Runtime())
    store = DeliveryStore(tmp_path / 'delivery.db')
    monkeypatch.setattr(ar, '_delivery_store', store)
    store.enqueue('child:turn:1', 'p', 'busy', payload())
    store.accept('child:turn:1', 'p', 'busy', payload())
    claimed = store.claim_inbox()
    assert claimed
    before = dict(ar.agent_sessions)
    ar.agent_sessions.clear()
    ar.agent_sessions['busy'] = {'project_id': 'p', 'status': 'running'}
    row = claimed
    try:
        ar._process_inbox(row)
    except DeliveryDeferred:
        pass
    else:
        raise AssertionError('busy parent was submitted')
    ar.agent_sessions['done'] = {'project_id': 'p', 'status': 'completed',
                                 'mode': 'A', 'provider': 'codex',
                                 'agent_model': 'gpt-5.6-luna', 'incognito': False,
                                 'log_lines': []}
    store.enqueue('child:turn:2', 'p', 'done', dict(payload(), event_id='child:turn:2'))
    store.accept('child:turn:2', 'p', 'done', dict(payload(), event_id='child:turn:2'))
    row = store.claim_inbox()
    assert row
    ar._process_inbox(row)
    assert ar.agent_sessions['done']['status'] == 'running'
    ar.agent_sessions.clear()
    ar.agent_sessions.update(before)


@pytest.mark.parametrize('write_mode', ['success', 'broken_pipe', 'flush_broken_pipe', 'delayed'])
def test_real_mode_b_followup_ack_waits_for_stdin_write(monkeypatch, tmp_path, write_mode):
    """The durable handoff acknowledges the real writer, not thread creation."""
    from mc.blueprints import agent_routes as ar

    class FakeStdin:
        def __init__(self):
            self.writes = []

        def write(self, value):
            if write_mode == 'delayed':
                time.sleep(0.03)
            if write_mode == 'broken_pipe':
                raise BrokenPipeError('pipe closed')
            self.writes.append(value)

        def flush(self):
            if write_mode in ('broken_pipe', 'flush_broken_pipe'):
                raise BrokenPipeError('pipe closed')

    stdin = FakeStdin()
    proc = SimpleNamespace(stdin=stdin, pid=12345, poll=lambda: None)
    parent = {'project_id': 'p', 'status': 'completed', 'mode': 'B',
              'provider': 'claude', 'agent_model': 'sonnet', 'incognito': False,
              'process_alive': True, 'proc': proc, 'stdin_lock': threading.Lock(),
              'log_lines': []}
    monkeypatch.setattr(ar, 'agent_sessions', {'parent': parent})
    monkeypatch.setattr(ar, 'load_project', lambda _: {'id': 'p', 'project_path': str(tmp_path)})
    monkeypatch.setattr(ar, 'get_manager', lambda _: SimpleNamespace(lock=threading.RLock()))
    monkeypatch.setattr(ar, '_pid_is_alive', lambda _: True)
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *args: '')
    monkeypatch.setattr(ar, '_memory_turn', SimpleNamespace(refresh_for_turn=lambda *args: {'block': ''}))
    monkeypatch.setattr(ar, '_behavior_tail', SimpleNamespace(render=lambda: ''))
    monkeypatch.setattr(ar, '_apply_mobile_brief', lambda message, data: message)
    monkeypatch.setattr(ar, '_rearm_notify_for_new_turn', lambda _: None)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *args: None)

    app = Flask('real-followup-ack')
    app.register_blueprint(ar.bp)
    with app.test_client() as client:
        response = client.post('/api/project/p/agent/followup', json={
            'message': 'delegated completion', 'session_id': 'parent',
            '_durable_delivery_ack': True,
        })

    body = response.get_json()
    if write_mode == 'broken_pipe':
        assert response.status_code == 504
        assert body['stdin_write_ack'] == 'unknown'
        assert parent['process_alive'] is False
        assert stdin.writes == []
    elif write_mode == 'flush_broken_pipe':
        assert response.status_code == 504
        assert body['stdin_write_ack'] == 'unknown'
        assert parent['_stdin_write_uncertain'] is True
        assert len(stdin.writes) == 1
    else:
        assert response.status_code == 200
        assert body['stdin_write_ack'] == 'written'
        assert len(stdin.writes) == 1
        assert stdin.writes[0].endswith('\n')
        assert json.loads(stdin.writes[0])['message']['content'] == 'delegated completion'


def test_real_process_inbox_acknowledges_only_after_mode_b_write(monkeypatch, tmp_path):
    from mc.blueprints import agent_routes as ar

    class FakeStdin:
        def __init__(self):
            self.writes = []
        def write(self, value):
            self.writes.append(value)
        def flush(self):
            pass

    stdin = FakeStdin()
    parent = {'project_id': 'p', 'status': 'completed', 'mode': 'B',
              'provider': 'claude', 'agent_model': 'sonnet', 'incognito': False,
              'process_alive': True,
              'proc': SimpleNamespace(stdin=stdin, pid=12345, poll=lambda: None),
              'stdin_lock': threading.Lock(), 'log_lines': []}
    monkeypatch.setattr(ar, 'agent_sessions', {'parent': parent})
    monkeypatch.setattr(ar, 'load_project', lambda _: {'id': 'p', 'project_path': str(tmp_path)})
    monkeypatch.setattr(ar, 'get_manager', lambda _: SimpleNamespace(lock=threading.RLock()))
    monkeypatch.setattr(ar, '_pid_is_alive', lambda _: True)
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *args: '')
    monkeypatch.setattr(ar, '_memory_turn', SimpleNamespace(refresh_for_turn=lambda *args: {'block': ''}))
    monkeypatch.setattr(ar, '_behavior_tail', SimpleNamespace(render=lambda: ''))
    monkeypatch.setattr(ar, '_apply_mobile_brief', lambda message, data: message)
    monkeypatch.setattr(ar, '_rearm_notify_for_new_turn', lambda _: None)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *args: None)

    app = Flask('real-process-inbox')
    app.register_blueprint(ar.bp)
    monkeypatch.setattr(ar, '_delegation_app', app)
    store = DeliveryStore(tmp_path / 'delivery.db')
    monkeypatch.setattr(ar, '_delivery_store', store)
    event = 'child:turn:real'
    event_payload = dict(payload(), event_id=event, message='delegated completion')
    store.enqueue(event, 'p', 'parent', event_payload)
    store.accept(event, 'p', 'parent', event_payload)
    row = store.claim_inbox()
    evidence = ar._process_inbox(row)
    assert evidence['stdin_write_ack'] == 'written'
    assert json.loads(stdin.writes[0])['message']['content'] == 'delegated completion'
    store.submit_inbox(event, row['fence_token'], evidence)
    assert store.claim_inbox() is None


@pytest.mark.parametrize('requested_model', ['gpt-5.6-luna', ''])
def test_sqlite_flask_bridge_cold_codex_revival_launches_once(monkeypatch, tmp_path, requested_model):
    """Exercise the production chain without a real CLI or network."""
    from flask import Flask
    from types import SimpleNamespace
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(ar, 'agent_sessions', {})

    store = DeliveryStore(tmp_path / 'delivery.db')
    # Exercise a recreated project so a hard-coded first-generation value
    # cannot satisfy the invocation-time safety check.
    store.project_generation('p')
    store.revoke_project('p')
    store.recreate_project('p')
    expected_generation = store.project_generation('p')
    monkeypatch.setattr(ar, '_delivery_store', store)
    monkeypatch.setattr(ar, 'DATA_DIR', tmp_path)
    project = {'id': 'p', 'project_path': str(tmp_path),
               'provider': 'codex', 'agent_model': 'project-default-wrong'}
    monkeypatch.setattr(ar, 'load_project', lambda pid: project if pid == 'p' else None)
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *args: '')
    monkeypatch.setattr(ar, '_build_agent_context', lambda *args, **kwargs: '')
    monkeypatch.setattr(ar, '_prior_character', lambda *args: '')
    monkeypatch.setattr(ar, '_refuse_duplicate_spawn', lambda *args: False)
    monkeypatch.setattr(ar, '_maybe_isolate_worktree', lambda *args: (str(tmp_path), False))
    monkeypatch.setattr(ar, '_log_agent_dispatch_pending', lambda *args, **kwargs: None)
    monkeypatch.setattr(ar, '_router_stat', lambda *args, **kwargs: None)
    monkeypatch.setattr(ar, '_register_process', lambda *args, **kwargs: None)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *args: None)
    monkeypatch.setattr(ar, 'get_manager', lambda pid: SimpleNamespace(
        lock=threading.RLock(), session_ids=set(), ensure_guardian=lambda: None))

    launches = []
    class Runtime:
        name = 'codex'
        def model_supported(self, model):
            return True
        def build_command(self, **kwargs):
            return ['fake-codex', '--model', kwargs.get('model', ''),
                    '--resume', kwargs.get('resume_id', '')]
        def dispatch(self, **kwargs):
            assert kwargs['session_dict']['_delivery_generation'] == expected_generation
            launches.append({k: kwargs[k] for k in
                             ('model', 'resume_id', 'mc_session_id', 'session_dict')})
            kwargs['session_dict']['provider_session_id'] = kwargs['resume_id']
            kwargs['session_dict']['status'] = 'completed'
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: Runtime())

    parent_sid = 'parent-cold'
    (tmp_path / 'p_agent_log.json').write_text(json.dumps([{
        'session_id': parent_sid, 'status': 'completed', 'provider': 'codex',
            'provider_session_id': 'native-parent-7', 'agent_model': requested_model,
        'model': 'telemetry-wrong', 'pinned_model': '',
        'requested_effort': 'high', 'source': 'agent',
        'spawned_by_session_id': 'grandparent', 'trigger_type': 'workflow',
        'trigger_id': 'run-7:step-2', 'incognito': False, 'ts': '2'
    }]), encoding='utf-8')
    child = dict(payload(), event_id='child:cold:1', message='deliver this')
    child['delivery_generation'] = expected_generation
    store.enqueue('child:cold:1', 'p', parent_sid, child)
    app = Flask('bridge')
    app.register_blueprint(ar.bp)
    client = app.test_client()
    sent = []
    def bridge(row):
        sent.append(client.post('/api/project/p/agent/delegation/inbox',
                                json={'event_id': row['event_id'],
                                      'parent_session_id': row['parent_session_id'],
                                      'payload': json.loads(row['payload'])['payload']}))
    first = drain_once(store, send_outbox=bridge, process_inbox=ar._process_inbox)
    assert first == 2, store.status('inbox', 'child:cold:1', 'p')
    assert sent[0].status_code == 202
    second = drain_once(store, send_outbox=bridge, process_inbox=ar._process_inbox)
    assert second == 0, store.status('inbox', 'child:cold:1', 'p')
    assert len(launches) == 1, store.status('inbox', 'child:cold:1', 'p')
    assert launches[0]['model'] == requested_model
    assert launches[0]['resume_id'] == 'native-parent-7'
    assert launches[0]['mc_session_id'] == parent_sid
    assert launches[0]['session_dict']['requested_effort'] == 'high'
    assert launches[0]['session_dict']['_notify_session'] == 'grandparent'
    assert launches[0]['session_dict']['_notify_workflow'] == {
        'run_id': 'run-7', 'step': 'step-2'}
    assert launches[0]['session_dict']['trigger_type'] == 'workflow'
    assert launches[0]['session_dict']['trigger_id'] == 'run-7:step-2'
    assert launches[0]['session_dict']['source'] == 'agent'
    assert launches[0]['session_dict']['_delivery_generation'] == expected_generation
    completion = callback_payload(launches[0]['session_dict'], 'full completion', 'source-after')
    store.record_completion_source('source-after', 'p', 'grandparent', completion)
    reopened = DeliveryStore(tmp_path / 'delivery.db')
    assert reopened.recover_sources() == 1
    assert reopened.status('outbox', 'child:cold:1', 'p')['state'] == 'delivered'
    assert reopened.status('inbox', 'child:cold:1', 'p')['state'] == 'submitted'
    assert drain_once(reopened, send_outbox=bridge, process_inbox=ar._process_inbox) == 1
    assert len(launches) == 1
    reopened.revoke_project('p')
    reopened.recreate_project('p')
    assert reopened.enqueue('old-unseen', 'p', 'grandparent', completion) is False


def test_claude_cold_revival_uses_saved_native_identity_and_lineage(monkeypatch, tmp_path):
    """Real helper and Claude revival; only process/context plumbing is fake."""
    from mc.blueprints import agent_routes as ar
    from flask import Flask
    from types import SimpleNamespace
    monkeypatch.setattr(ar, 'agent_sessions', {})
    entry = {'session_id': 'p-claude', 'status': 'completed', 'provider': 'claude',
             'claude_session_id': 'claude-native-9', 'agent_model': 'claude-model',
             'requested_effort': 'high', 'incognito': False, 'source': 'agent',
             'spawned_by_session_id': 'grandparent', 'trigger_type': 'workflow',
             'trigger_id': 'run:step', 'ts': '1'}
    monkeypatch.setattr(ar, '_delivery_store', DeliveryStore(tmp_path / 'delivery.db'))
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [entry])
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *args: '')
    monkeypatch.setattr(ar, '_build_agent_context', lambda *a, **k: 'CTX')
    monkeypatch.setattr(ar, '_session_too_large', lambda *a, **k: (False, 0))
    monkeypatch.setattr(ar, '_prior_character', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_resume_cwd_for', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_revive_history_lines', lambda *a, **k: [])
    monkeypatch.setattr(ar, '_refuse_duplicate_spawn', lambda *a, **k: False)
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'fake-claude')
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *a, **k: [
        '--model', k.get('model_override', ''), '--effort', k.get('effort_override', '')])
    monkeypatch.setattr(ar, '_sysprompt_file_args', lambda ctx: ([], None))
    monkeypatch.setattr(ar, '_sysprompt_cleanup', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_register_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_hide_windows_delayed', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_read_agent_stream', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_memory_turn', SimpleNamespace(seed_delivered=lambda *a: None))
    class Proc:
        pid = 999
        stdout = iter([])
    argv = []
    def fake_popen(cmd, **kwargs):
        argv.append(cmd)
        return Proc()
    monkeypatch.setattr(ar.subprocess, 'Popen', fake_popen)
    monkeypatch.setattr(ar, 'get_manager', lambda pid: SimpleNamespace(
        lock=threading.RLock(), session_ids=set(), ensure_guardian=lambda: None))
    project = {'id': 'p', 'project_path': str(tmp_path)}
    result = ar._revive_parent_for_delegation('p', 'p-claude', 'message', project)
    assert result['revived'] is True
    assert argv and 'claude-native-9' in argv[0]
    assert '--model' in argv[0] and 'claude-model' in argv[0]
    assert '--effort' in argv[0] and 'high' in argv[0]
    session = ar.agent_sessions['p-claude']
    assert session['_notify_session'] == 'grandparent'
    assert session['_notify_workflow'] == {'run_id': 'run', 'step': 'step'}
    assert session['trigger_type'] == 'workflow' and session['source'] == 'agent'


def test_cold_revival_unknown_provider_or_missing_native_id_blocks(monkeypatch):
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *args: '')
    for provider, native_key in [('unknown', 'provider_session_id'), ('codex', 'provider_session_id')]:
        entry = {'session_id': 'p', 'status': 'completed', 'provider': provider,
                 native_key: '' if provider == 'codex' else 'native',
                 'agent_model': 'model', 'incognito': False, 'ts': '1'}
        monkeypatch.setattr(ar, '_load_agent_log', lambda pid, entry=entry: [entry])
        try:
            ar._revive_parent_for_delegation('p', 'p', 'message', {})
        except DeliveryBlocked:
            pass
        else:
            raise AssertionError('unsafe parent revival was allowed')
