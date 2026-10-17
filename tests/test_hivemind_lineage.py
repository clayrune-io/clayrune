"""MC-1107: creator attribution survives worker scheduling and runtime adapters."""
import pytest

from mc import caller_attribution as ca, hivemind_lineage as hl, review_lineage as rl
from mc.blueprints import agent_routes as ar
from mc.merge_review_gate import ReviewRefused
from tests.test_hivemind_routes import client, _create  # noqa: F401
from tests.test_hivemind_worktree_dispatch_hook import env as hook_env  # noqa: F401
from tests.test_hivemind_worktree_dispatch_hook import repo  # noqa: F401
import tests.test_hivemind_worktree_dispatch_hook as hook_tests


def _attribute(monkeypatch, sid='', status=ca.ATTRIBUTED):
    monkeypatch.setattr(ca, 'attribute_caller',
                        lambda *a, **k: ca.Attribution(status, sid))


def test_creation_records_os_caller_and_update_cannot_replace_it(client, monkeypatch):
    _attribute(monkeypatch, 'owner')
    body = _create(client, created_by_session_id='decoy', _spawned_by='decoy',
                   notify_session='decoy', dispatched_by_session_id='decoy')
    manifest = body['hivemind']
    assert manifest['created_by_session_id'] == 'owner'
    hid = manifest['id']
    r = client.put(f'/api/hivemind/{hid}', json={
        'created_by_session_id': 'decoy',
        'config': {'created_by_session_id': 'decoy', '_spawned_by': 'decoy'}})
    assert r.status_code == 200
    assert client.hm._hm_load_manifest(hid)['created_by_session_id'] == 'owner'


@pytest.mark.parametrize('status', [ca.UNATTRIBUTED, ca.UNAVAILABLE])
def test_unattributed_creation_never_trusts_named_identity(client, monkeypatch, status):
    _attribute(monkeypatch, 'decoy', status)
    manifest = _create(client, created_by_session_id='owner', notify_session='owner')['hivemind']
    assert manifest['created_by_session_id'] == ''


@pytest.mark.parametrize('automatic', [False, True])
@pytest.mark.parametrize('provider', ['claude', 'fakeprov'])
def test_worker_keeps_creator_live_and_durable_after_spawn_caller_changes(
        client, monkeypatch, tmp_path, automatic, provider):
    monkeypatch.setattr(ar, 'DATA_DIR', tmp_path)
    client.projects['thm']['provider'] = provider
    # The owner dispatched an intermediate session that creates the Hivemind.
    client.state.agent_sessions['creator'] = {'_spawned_by': 'owner'}
    _attribute(monkeypatch, 'creator')
    hid = _create(client)['hivemind']['id']
    _attribute(monkeypatch, 'unrelated-spawn-caller')
    if automatic:
        client.hm._hm_auto_spawn_workers(hid)
        ws = client.hm._hm_load_workstream(hid, 'ws_001')
        sid = ws['current_agent_session_id']
    else:
        response = client.post(f'/api/hivemind/{hid}/workstreams/ws_001/spawn', json={
            'created_by_session_id': 'decoy', '_spawned_by': 'decoy'})
        assert response.status_code == 200, response.get_json()
        sid = response.get_json()['session_id']
    assert sid.startswith('hm_')
    worker = client.state.agent_sessions[sid]
    assert worker['_spawned_by'] == 'creator'
    assert not worker.get('_notify_session')
    runtime = client.claude_rt if provider == 'claude' else client.fake_rt
    assert runtime.dispatch_calls[-1]['session_metadata']['_spawned_by'] == 'creator'
    rows = ar._load_agent_log('thm')
    assert rows[0]['dispatched_by_session_id'] == 'creator'
    assert rows[0]['spawned_by_session_id'] == ''
    assert rows[0]['project_generation'] == 7
    assert ar._row_spawned_by(rows[0], {}) == 'creator'  # completed/history banner reader
    with pytest.raises(ReviewRefused, match='descends from the branch owner'):
        rl.check(sid, 'owner')
    client.state.agent_sessions.pop(sid)
    with pytest.raises(ReviewRefused, match='descends from the branch owner'):
        rl.check(sid, 'owner')


def test_pending_lineage_write_failure_prevents_runtime_launch(client, monkeypatch):
    _attribute(monkeypatch, 'owner')
    hid = _create(client)['hivemind']['id']

    def fail(*args):
        raise OSError('lineage storage unavailable')

    monkeypatch.setattr(ar, '_update_agent_log', fail)
    response = client.post(f'/api/hivemind/{hid}/workstreams/ws_001/spawn')
    assert response.status_code == 500
    assert 'lineage storage unavailable' in response.get_json()['error']
    assert client.claude_rt.dispatch_calls == []
    assert not client.state.agent_sessions


@pytest.mark.parametrize('creator', ['', None])
def test_legacy_or_human_hivemind_does_not_adopt_later_spawn_caller(
        client, monkeypatch, creator):
    _attribute(monkeypatch, '', ca.UNATTRIBUTED)
    manifest = _create(client)['hivemind']
    if creator is None:
        manifest.pop('created_by_session_id')
    client.hm._hm_save_manifest(manifest['id'], manifest)
    _attribute(monkeypatch, 'owner')
    response = client.post(f"/api/hivemind/{manifest['id']}/workstreams/ws_001/spawn")
    assert response.status_code == 200
    worker = client.state.agent_sessions[response.get_json()['session_id']]
    assert worker['_spawned_by'] == ''


def test_real_claude_bridge_preserves_lineage(hook_env, monkeypatch):
    original_manifest = hook_tests._manifest
    monkeypatch.setattr(hook_tests, '_manifest', lambda hid: {
        **original_manifest(hid), 'created_by_session_id': 'owner'})
    rows = []
    monkeypatch.setattr(ar, '_update_agent_log', lambda pid, mutate: mutate(rows))
    sid, _ = hook_tests._spawn_worker(hook_env)
    assert len(hook_env['spawned']) == 1
    worker = hook_tests.state.agent_sessions[sid]
    assert worker['_spawned_by'] == 'owner'
    assert worker['_notify_session'] == ''
    assert rows[0]['dispatched_by_session_id'] == 'owner'


def test_repeated_recording_does_not_reset_a_completed_row(monkeypatch):
    rows = [{'session_id': 'hm_worker', 'status': 'completed',
             'dispatched_by_session_id': 'owner', 'summary': 'finished'}]
    monkeypatch.setattr(ar, '_update_agent_log', lambda pid, mutate: mutate(rows))
    hl.record_worker({'session_id': 'hm_worker', 'project_id': 'p',
                      '_spawned_by': 'owner'}, 7)
    assert rows == [{'session_id': 'hm_worker', 'status': 'completed',
                     'dispatched_by_session_id': 'owner', 'summary': 'finished'}]
