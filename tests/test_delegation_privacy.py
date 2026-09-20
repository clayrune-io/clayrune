"""Deletion/revocation integration tests for durable delegation records."""
from flask import Flask, jsonify

from mc.delegation_delivery import DeliveryStore, DeliveryUncertain
from mc.blueprints import agent_routes as ar
from mc.blueprints import project_routes as pr


def _payload(child='child', summary='private full result'):
    return {'event_id': 'e1', 'child_session_id': child, 'who': 'worker',
            'status': 'completed', 'task': 'secret task', 'summary': summary,
            'provider': 'codex', 'model': 'gpt-5.6-luna', 'message': summary}


def _app(monkeypatch, store):
    app = Flask(__name__)
    app.register_blueprint(ar.bp)
    monkeypatch.setattr(ar, '_delivery_store', store)
    return app


def test_deleted_child_purges_all_payloads_and_late_http_replay_is_blocked(tmp_path, monkeypatch):
    store = DeliveryStore(tmp_path / 'delivery.sqlite3')
    p = _payload()
    store.enqueue('e1', 'project', 'parent', p)
    store.record_completion_source('e1', 'project', 'parent', p)
    store.accept('e1', 'project', 'parent', p)
    store.revoke_session('project', 'child')
    reopened = DeliveryStore(tmp_path / 'delivery.sqlite3')
    assert reopened.status('outbox', 'e1', 'project') is None
    assert reopened.recover_sources() == 0
    assert reopened.enqueue('e1', 'project', 'parent', p) is False
    response = _app(monkeypatch, reopened).test_client().post(
        '/api/project/project/agent/delegation/inbox',
        json={'event_id': 'e1', 'parent_session_id': 'parent', 'payload': p})
    assert response.status_code == 410
    assert 'secret task' not in response.get_data(as_text=True)


def test_deleted_parent_and_project_block_reopen_recovery_without_retaining_payload(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.sqlite3')
    parent_payload = _payload('child-a')
    store.enqueue('ea', 'p', 'parent-a', parent_payload)
    store.record_completion_source('ea', 'p', 'parent-a', parent_payload)
    store.revoke_session('p', 'parent-a')
    assert store.status('outbox', 'ea', 'p') is None
    assert store.recover_sources() == 0
    assert store.enqueue('ea', 'p', 'parent-a', parent_payload) is False

    other = _payload('child-b')
    store.enqueue('eb', 'deleted-project', 'parent-b', other)
    store.revoke_project('deleted-project')
    reopened = DeliveryStore(tmp_path / 'delivery.sqlite3')
    assert reopened.status('outbox', 'eb', 'deleted-project') is None
    assert reopened.enqueue('eb', 'deleted-project', 'parent-b', other) is False
    assert reopened.enqueue('live', 'live-project', 'parent', _payload('live')) is True


def test_explicit_project_recreation_allows_new_events_but_not_old_replay(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.sqlite3')
    old = _payload('old-child')
    store.enqueue('old-event', 'p', 'parent', old)
    store.revoke_project('p')
    store.recreate_project('p')
    assert store.enqueue('old-event', 'p', 'parent', old) is False
    new_generation = store.project_generation('p')
    assert store.enqueue('new-event', 'p', 'new-parent',
                         dict(_payload('new-child'), delivery_generation=new_generation)) is True


def test_failed_project_creation_does_not_reopen_revoked_generation(tmp_path, monkeypatch):
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    store = DeliveryStore(tmp_path / 'delegation_delivery.sqlite3')
    old_generation = store.project_generation('p')
    store.revoke_project('p')
    revoked_generation = store.project_generation('p')
    with store._db() as db:
        tombstones_before = [tuple(row) for row in db.execute(
            'SELECT identity, project_id, session_id, event_id, scope '
            'FROM revocations ORDER BY identity')]
    monkeypatch.setattr(pr, 'DATA_DIR', data_dir)
    monkeypatch.setattr(pr, 'get_manager', lambda _pid: type('M', (), {'lock': __import__('threading').RLock()})())
    # `import server` (test_agent_routes et al.) wires pr._APP_DIR to the checkout
    # and it outlives that test; a tmp workspace under the checkout then trips the
    # install-dir guard (400) before the failure this test injects (500).
    monkeypatch.setattr(pr, '_APP_DIR', None)
    monkeypatch.setitem(pr.state.CONFIG, 'auto_workspace_base', str(tmp_path / 'auto'))
    app = Flask('failed-recreate')
    app.register_blueprint(pr.bp)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    response = app.test_client().post('/api/project/p', json={
        'project_path': str(workspace), 'default_character': 'bad'})
    assert response.status_code == 400
    assert store.project_is_revoked('p') is True
    assert store.project_generation('p') == revoked_generation
    with store._db() as db:
        assert [tuple(row) for row in db.execute(
            'SELECT identity, project_id, session_id, event_id, scope '
            'FROM revocations ORDER BY identity')] == tombstones_before
    monkeypatch.setattr(pr, 'save_project', lambda *_args: (_ for _ in ()).throw(OSError('save failed')))
    response = app.test_client().post('/api/project/p', json={'project_path': str(workspace)})
    assert response.status_code == 500
    assert store.project_is_revoked('p') is True
    assert store.project_generation('p') == revoked_generation
    with store._db() as db:
        assert [tuple(row) for row in db.execute(
            'SELECT identity, project_id, session_id, event_id, scope '
            'FROM revocations ORDER BY identity')] == tombstones_before
    assert revoked_generation == old_generation + 1


def test_real_route_recreation_preserves_old_replay_block_and_allows_new_generation(tmp_path, monkeypatch):
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    (data_dir / 'p.json').write_text('{"id":"p"}', encoding='utf-8')
    store = DeliveryStore(tmp_path / 'delegation_delivery.sqlite3')
    old_generation = store.project_generation('p')
    old = dict(_payload('old-child'), delivery_generation=old_generation)
    store.enqueue('old-event', 'p', 'parent', old)
    store.revoke_project('p')
    (data_dir / 'p.json').unlink()
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    monkeypatch.setattr(pr, 'DATA_DIR', data_dir)
    monkeypatch.setattr(pr, 'get_manager', lambda _pid: type('M', (), {'lock': __import__('threading').RLock()})())
    monkeypatch.setattr(pr, '_APP_DIR', None)
    monkeypatch.setattr(pr, '_steward_core', type('S', (), {'install_fence_to_project': lambda *_a: None})())
    app = Flask('route-recreate')
    app.register_blueprint(pr.bp)
    response = app.test_client().post('/api/project/p', json={'project_path': str(workspace)})
    assert response.status_code == 200
    reopened = DeliveryStore(tmp_path / 'delegation_delivery.sqlite3')
    new_generation = reopened.project_generation('p')
    assert new_generation != old_generation
    assert reopened.enqueue('old-unseen', 'p', 'parent', old) is False
    assert reopened.enqueue('new-event', 'p', 'new-parent',
                            dict(_payload('new-child'), delivery_generation=new_generation)) is True


def test_concurrent_create_rechecks_missing_state_under_manager_guard(tmp_path, monkeypatch):
    import threading
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    manager = type('M', (), {'lock': threading.RLock()})()
    barrier = threading.Barrier(2)
    monkeypatch.setattr(pr, 'DATA_DIR', data_dir)
    monkeypatch.setattr(pr, 'get_manager', lambda _pid: manager)
    monkeypatch.setattr(pr, '_APP_DIR', None)
    monkeypatch.setattr(pr, '_steward_core', type('S', (), {'install_fence_to_project': lambda *_a: None})())
    original_refusal = pr._refuse_project_path_in_install_dir
    def synchronize(*args):
        barrier.wait(timeout=5)
        return original_refusal(*args)
    monkeypatch.setattr(pr, '_refuse_project_path_in_install_dir', synchronize)
    app = Flask('concurrent-create')
    app.register_blueprint(pr.bp)
    responses = []
    def create():
        responses.append(app.test_client().post('/api/project/p', json={'project_path': str(workspace)}))
    threads = [threading.Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert all(not thread.is_alive() for thread in threads)
    assert [response.status_code for response in responses] == [200, 200]
    assert DeliveryStore(tmp_path / 'delegation_delivery.sqlite3').project_generation('p') == 1


def test_never_recorded_old_generation_completion_is_refused_after_recreate(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.sqlite3')
    old_generation = store.project_generation('p')
    store.allocate_turn('old-child')
    store.revoke_project('p')
    store.recreate_project('p')
    old_completion = dict(_payload('old-child'), delivery_generation=old_generation)
    assert store.enqueue('first-ever-old-event', 'p', 'parent', old_completion) is False
    new_generation = store.project_generation('p')
    store.allocate_turn('new-child')
    new_completion = dict(_payload('new-child'), delivery_generation=new_generation)
    assert store.enqueue('new-event', 'p', 'new-parent', new_completion) is True


def test_revoke_event_removes_only_that_event_and_rejects_empty_identity(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.sqlite3')
    store.enqueue('e1', 'p', 'parent', _payload('child-1'))
    store.enqueue('e2', 'p', 'parent', _payload('child-2'))
    store.revoke_event('p', 'e1')
    assert store.status('outbox', 'e1', 'p') is None
    assert store.status('outbox', 'e2', 'p') is not None
    assert store.enqueue('e1', 'p', 'parent', _payload('child-1')) is False
    for method, args in ((store.revoke_session, ('p', '')),
                         (store.revoke_event, ('p', ''))):
        try:
            method(*args)
        except ValueError:
            pass
        else:
            raise AssertionError('empty identity became a project revocation')


def test_wrong_project_status_and_retry_do_not_observe_or_mutate_revocation(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.sqlite3')
    p = _payload()
    store.enqueue('e1', 'p1', 'parent', p)
    store.revoke_project('p1')
    assert store.status('outbox', 'e1', 'p2') is None
    store.retry('outbox', 'e1', 'p2')
    assert store.status('outbox', 'e1', 'p1') is None


def test_delete_racing_claim_fences_late_sender_and_source_recovery(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.sqlite3')
    p = _payload()
    store.enqueue('e1', 'p', 'parent', p)
    claimed = store.claim_outbox()
    assert claimed is not None
    store.revoke_session('p', 'child')
    store.ack_outbox('e1', claimed['fence_token'])
    reopened = DeliveryStore(tmp_path / 'delivery.sqlite3')
    assert reopened.status('outbox', 'e1', 'p') is None
    assert reopened.recover_sources() == 0


def test_process_inbox_rechecks_fence_after_delete_before_parent_handoff(tmp_path, monkeypatch):
    store = DeliveryStore(tmp_path / 'delivery.sqlite3')
    p = _payload()
    store.enqueue('e1', 'p', 'parent', p)
    store.accept('e1', 'p', 'parent', p)
    row = store.claim_inbox()
    assert row is not None
    handoffs = []
    manager = type('M', (), {'lock': __import__('threading').RLock()})()
    monkeypatch.setattr(ar, '_delivery_store', store)
    monkeypatch.setattr(ar, '_delegation_app', Flask('delegation-race'))
    monkeypatch.setattr(ar, 'get_manager', lambda _pid: manager)
    monkeypatch.setattr(ar, 'agent_sessions', {'parent': {
        'project_id': 'p', 'status': 'completed', 'provider': 'codex',
        'agent_model': 'gpt-5.6-luna', 'incognito': False}})
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *a: '')
    monkeypatch.setattr(ar, 'agent_followup', lambda _pid: (handoffs.append(True), jsonify({'session_id': 'parent'}))[1])
    original = store.revalidate_claim
    checks = [0]
    def delete_before_handoff(table, event_id, token):
        checks[0] += 1
        if checks[0] == 2:
            store.revoke_session('p', 'parent')
        return original(table, event_id, token)
    monkeypatch.setattr(store, 'revalidate_claim', delete_before_handoff)
    try:
        ar._process_inbox(row)
    except DeliveryUncertain:
        pass
    else:
        raise AssertionError('stale inbox action was allowed after deletion')
    assert handoffs == []
    assert DeliveryStore(tmp_path / 'delivery.sqlite3').status('inbox', 'e1', 'p') is None


def test_incognito_completion_keeps_delivery_store_empty(tmp_path, monkeypatch):
    store = DeliveryStore(tmp_path / 'delivery.sqlite3')
    monkeypatch.setattr(ar, '_delivery_store', store)
    monkeypatch.setattr(ar, '_write_session_memory', lambda *a, **k: True)
    monkeypatch.setattr(ar, '_extract_transcript_telemetry', lambda *a: {})
    ar._log_agent_completion({'project_id': 'p', 'session_id': 'child',
                              '_notify_session': 'parent', 'incognito': True,
                              'status': 'completed', 'log_lines': ['secret']})
    with store._db() as db:
        assert db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM completion_sources').fetchone()[0] == 0


def test_rejected_conversation_deletes_leave_rows_and_tombstones_unchanged(tmp_path, monkeypatch):
    store = DeliveryStore(tmp_path / 'delegation.sqlite3')
    p = _payload('child')
    store.enqueue('e1', 'p', 'parent', p)
    before = store.status('outbox', 'e1', 'p')
    app = Flask('conversation-refusals')
    app.register_blueprint(ar.bp)
    monkeypatch.setattr(ar, '_delivery_store', store)
    monkeypatch.setattr(ar, 'load_project', lambda _pid: {'project_path': str(tmp_path)})
    monkeypatch.setattr(ar, 'get_manager', lambda _pid: type('M', (), {'lock': __import__('threading').RLock()})())
    monkeypatch.setattr(ar, 'agent_sessions', {'mc-parent': {
        'project_id': 'p', 'claude_session_id': 'native-live'}})
    monkeypatch.setattr(ar, '_find_transcript_file', lambda *_args: tmp_path / 'missing.jsonl')
    assert app.test_client().delete('/api/project/p/conversation/native-live').status_code == 409
    assert store.status('outbox', 'e1', 'p') == before
    assert store.project_is_revoked('p') is False

    monkeypatch.setattr(ar, 'agent_sessions', {})
    monkeypatch.setattr(ar, '_find_transcript_file', lambda *_args: None)
    assert app.test_client().delete('/api/project/p/conversation/native-missing').status_code == 404
    assert store.status('outbox', 'e1', 'p') == before
    assert store.project_is_revoked('p') is False


def test_real_project_delete_route_revokes_delivery_before_record_removal(tmp_path, monkeypatch):
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    project_id = 'gone'
    (data_dir / f'{project_id}.json').write_text('{"id":"gone"}', encoding='utf-8')
    store = DeliveryStore(tmp_path / 'delegation_delivery.sqlite3')
    p = _payload()
    store.enqueue('e1', project_id, 'parent', p)
    monkeypatch.setattr(pr, 'DATA_DIR', data_dir)
    monkeypatch.setattr(pr, 'UPLOADS_DIR', tmp_path / 'uploads')
    monkeypatch.setattr(pr, 'load_project', lambda _pid: {'backlog': []})
    monkeypatch.setattr(pr, 'agent_sessions', {})
    monkeypatch.setattr(pr, 'terminal_sessions', {})
    monkeypatch.setattr(pr, 'get_manager', lambda _pid: type('M', (), {'lock': __import__('threading').RLock()})())
    monkeypatch.setattr(pr, '_unregister_process', lambda _pid: None)
    app = Flask(__name__)
    app.register_blueprint(pr.bp)
    response = app.test_client().delete(f'/api/project/{project_id}')
    assert response.status_code == 200
    assert not (data_dir / f'{project_id}.json').exists()
    reopened = DeliveryStore(tmp_path / 'delegation_delivery.sqlite3')
    assert reopened.status('outbox', 'e1', project_id) is None
    assert reopened.enqueue('e1', project_id, 'parent', p) is False


def test_real_conversation_delete_revokes_native_and_mc_aliases(tmp_path, monkeypatch):
    project_id, mc_id, native_id = 'p', 'mc-child-7', 'claude-native-7'
    transcript = tmp_path / 'native.jsonl'
    transcript.write_text('{"type":"user"}\n', encoding='utf-8')
    store = DeliveryStore(tmp_path / 'delegation.sqlite3')
    p = _payload(mc_id)
    store.enqueue('e1', project_id, 'parent', p)
    monkeypatch.setattr(ar, '_delivery_store', store)
    monkeypatch.setattr(ar, 'load_project', lambda _pid: {'project_path': str(tmp_path)})
    monkeypatch.setattr(ar, '_find_transcript_file', lambda _path, _sid: str(transcript))
    monkeypatch.setattr(ar, 'agent_sessions', {})
    monkeypatch.setattr(ar, 'get_manager', lambda _pid: type('M', (), {'lock': __import__('threading').RLock()})())
    monkeypatch.setattr(ar, '_load_agent_log', lambda _pid: [
        {'session_id': mc_id, 'claude_session_id': native_id}])
    lifecycle_calls = []
    monkeypatch.setattr(ar, '_runtime_lifecycle_service', type('Lifecycle', (), {
        'revoke_conversations': lambda _self, pid, aliases:
            lifecycle_calls.append((pid, set(aliases))) or tuple(sorted(aliases))})())
    app = Flask('conversation-delete')
    app.register_blueprint(ar.bp)
    response = app.test_client().delete(f'/api/project/{project_id}/conversation/{native_id}')
    assert response.status_code == 200
    assert not transcript.exists()
    reopened = DeliveryStore(tmp_path / 'delegation.sqlite3')
    assert reopened.status('outbox', 'e1', project_id) is None
    assert reopened.enqueue('e1', project_id, 'parent', p) is False
    assert reopened.enqueue('native-replay', project_id, native_id,
                            _payload('other')) is False
    assert lifecycle_calls == [(project_id, {mc_id, native_id})]


def test_conversation_rename_failure_reports_partial_and_keeps_delivery_revoked(tmp_path, monkeypatch):
    project_id, native_id = 'p', 'native-failure'
    transcript = tmp_path / 'native.jsonl'
    transcript.write_text('{"type":"user"}\n', encoding='utf-8')
    store = DeliveryStore(tmp_path / 'delegation.sqlite3')
    p = _payload('mc-failure')
    store.enqueue('e1', project_id, 'parent', p)
    monkeypatch.setattr(ar, '_delivery_store', store)
    monkeypatch.setattr(ar, 'load_project', lambda _pid: {'project_path': str(tmp_path)})
    monkeypatch.setattr(ar, '_find_transcript_file', lambda *_args: str(transcript))
    monkeypatch.setattr(ar, 'agent_sessions', {})
    monkeypatch.setattr(ar, 'get_manager', lambda _pid: type('M', (), {'lock': __import__('threading').RLock()})())
    monkeypatch.setattr(ar, '_load_agent_log', lambda _pid: [
        {'session_id': 'mc-failure', 'claude_session_id': native_id}])
    monkeypatch.setattr(ar, '_runtime_lifecycle_service', type('Lifecycle', (), {
        'revoke_conversations': lambda _self, _pid, _aliases: ('mc-failure',)})())
    def fail_rename(self, _dst):
        raise OSError('simulated rename failure')
    monkeypatch.setattr(__import__('pathlib').Path, 'rename', fail_rename)
    response = Flask('rename-failure')
    response.register_blueprint(ar.bp)
    result = response.test_client().delete(f'/api/project/{project_id}/conversation/{native_id}')
    assert result.status_code == 500
    assert result.get_json()['partial'] is True
    assert result.get_json()['delivery_revoked'] is True
    assert result.get_json()['lifecycle_revoked'] is True
    reopened = DeliveryStore(tmp_path / 'delegation.sqlite3')
    assert reopened.status('outbox', 'e1', project_id) is None
    assert reopened.project_is_revoked(project_id) is False
    assert transcript.exists()
