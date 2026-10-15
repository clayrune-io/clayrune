"""MC-1098: removal uses temporary operator state; no live data or processes."""
import base64
import json
from pathlib import Path

import pytest
from flask import Flask

from mc import desk_engine_connections as engines_state, desk_engines as engines, mcp, mcp_installer
from mc.blueprints import desk_connection_removal_routes as routes, desk_engine_connection_routes as engine_routes
from mc.blueprints import local_auth
from mc.desk_connect import custom_connection_store as store, custom_connection_operation as operation
from mc.desk_connect import custom_connection_removal as removal, custom_npm_artifact as artifact
from mc.desk_connect import custom_package_manifest as manifest, github_activation, remote_mcp_observed
from mc.desk_connect.undo import UndoStack

PASSCODE = 'test-dashboard-passcode'


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(mcp, 'GLOBAL_CLAUDE_JSON', tmp_path / 'claude.json')
    monkeypatch.setattr(mcp_installer, 'INSTALLS_ROOT', tmp_path / 'mcp_installs')
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'auth.json')
    local_auth._LOCAL_AUTH_FAILS.clear()
    local_auth._local_auth_set_passcode(PASSCODE)
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: False)
    monkeypatch.setattr(engine_routes, 'is_unattended_caller', lambda: False)
    monkeypatch.setattr(engines, 'JOBS_PATH', tmp_path / 'jobs.json')
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(routes.bp)
    app.register_blueprint(engine_routes.bp)
    project = tmp_path / 'project'
    project.mkdir()
    return app.test_client(), project


def saved(env, ecosystem='github', scope='global', state='setup_failed', registered=False, name='youtube'):
    client, project = env
    pid = 'p1' if scope == 'project' else None
    op = {'ecosystem': ecosystem, 'scope': {'kind': scope, 'project_id': pid}, 'server_name': name,
          'credentials': [], 'package': 'fixture-package', 'version': '1.0.0',
          'integrity': 'sha512-' + base64.b64encode(b'x' * 64).decode()}
    if ecosystem == 'github':
        op['directory'] = str(mcp_installer.INSTALLS_ROOT / name)
        directory = Path(op['directory'])
    elif ecosystem == 'npm':
        directory = artifact.package_dir(op)
    else:
        directory = None
    if directory:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'fixture.txt').write_text('fixture', encoding='utf-8')
    store.put(op, operation.fingerprint(op), state, project_path=str(project) if pid else None)
    if registered:
        writer = mcp._write_global_servers if scope == 'global' else lambda fn: mcp._write_project_servers(str(project), fn)
        writer(lambda rows: rows.update({name: {'command': 'fixture'}, 'unrelated': {'command': 'keep'}}))
    body = {'scope': scope, 'server_name': name, 'passcode': PASSCODE}
    if pid:
        body['project_id'] = pid
    return body, directory, op


@pytest.mark.parametrize('ecosystem', ['npm', 'github', 'remote', 'pypi'])
@pytest.mark.parametrize('scope', ['global', 'project'])
@pytest.mark.parametrize('registered', [True, False])
def test_removes_each_kind_and_scope_including_unregistered_setup_failed(env, ecosystem, scope, registered):
    body, directory, op = saved(env, ecosystem, scope, registered=registered)
    before = Path(env[1]).parent
    sentinel = before / 'keep.txt'
    sentinel.write_text('keep')
    response = env[0].delete('/api/desk/connect/custom/connections', json=body)
    assert response.status_code == 200, response.json
    assert store.get(scope, op['scope']['project_id'], 'youtube') is None
    if directory:
        assert not directory.exists()
    assert sentinel.read_text() == 'keep'
    servers = mcp._read_global_servers() if scope == 'global' else mcp._read_project_servers(str(env[1]))
    assert 'youtube' not in servers
    assert not registered or servers == {'unrelated': {'command': 'keep'}}
    if not registered:
        config = mcp.GLOBAL_CLAUDE_JSON if scope == 'global' else mcp.project_mcp_path(str(env[1]))
        assert not config.exists()  # failed setup must not create an MCP config to remove it


@pytest.mark.parametrize('state', ['saved', 'pending_runtime', 'registered', 'changed', 'package_missing', 'credential_missing', 'unknown'])
def test_any_saved_state_is_removable(env, state):
    body, directory, _ = saved(env, state=state)
    assert env[0].delete('/api/desk/connect/custom/connections', json=body).status_code == 200
    assert not directory.exists()


@pytest.mark.parametrize('proof', [None, '', 'wrong'])
def test_forged_origin_without_passcode_cannot_remove(env, proof):
    body, directory, _ = saved(env)
    body['passcode'] = proof
    res = env[0].delete('/api/desk/connect/custom/connections', json=body, headers={'Origin': 'http://localhost:5199'})
    assert res.status_code == 403
    assert directory.exists() and store.get('global', None, 'youtube')


def test_unattended_refused_before_gate(env, monkeypatch):
    body, directory, _ = saved(env)
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: True)
    monkeypatch.setattr(routes, '_require_human_passcode', lambda d: pytest.fail('gate must not be called'))
    assert env[0].delete('/api/desk/connect/custom/connections', json=body).status_code == 403
    assert directory.exists()


@pytest.mark.parametrize('body', [None, [], {}, {'scope': 'project', 'server_name': 'ok', 'project_id': '../bad'},
                                 {'scope': 'global', 'server_name': 'ok', 'project_id': 'p1'},
                                 {'scope': 'global', 'server_name': '../bad'},
                                 {'scope': 'global', 'server_name': 'ok', 'directory': '/tmp'}])
def test_invalid_targets_cost_no_passcode_guess(env, monkeypatch, body):
    monkeypatch.setattr(routes, '_require_human_passcode', lambda d: pytest.fail('bad target reached gate'))
    assert env[0].delete('/api/desk/connect/custom/connections', json=body).status_code == 400


def test_shared_directory_survives_until_last_connection(env):
    first, directory, _ = saved(env, 'npm', name='one')
    second, same, _ = saved(env, 'npm', scope='project', name='two')
    assert same == directory
    assert env[0].delete('/api/desk/connect/custom/connections', json=first).status_code == 200
    assert directory.exists()
    assert env[0].delete('/api/desk/connect/custom/connections', json=second).status_code == 200
    assert not directory.exists()


def test_save_approving_shared_package_while_removal_waits_is_preserved(env, monkeypatch):
    body, directory, _ = saved(env, 'npm', name='one')
    class InstallLock:
        def __enter__(self):
            saved(env, 'npm', scope='project', name='two')
        def __exit__(self, *args):
            return False
    monkeypatch.setattr(artifact, '_lock_for', lambda name: InstallLock())
    assert env[0].delete('/api/desk/connect/custom/connections', json=body).status_code == 200
    assert directory.exists() and store.get('project','p1','two')


def test_replaced_operation_directory_is_also_cleaned(env):
    body, old, op = saved(env, name='youtube', state='registered')
    newer = dict(op, directory=str(mcp_installer.INSTALLS_ROOT / 'new-preview'))
    new = Path(newer['directory'])
    new.mkdir()
    store.put(newer, operation.fingerprint(newer), 'setup_failed')
    assert env[0].delete('/api/desk/connect/custom/connections', json=body).status_code == 200
    assert not old.exists() and not new.exists()


def test_outside_or_root_directory_is_refused_before_unregister(env):
    body, directory, op = saved(env, registered=True)
    op['directory'] = str(mcp_installer.INSTALLS_ROOT)
    store.put(op, operation.fingerprint(op), 'setup_failed')
    res = env[0].delete('/api/desk/connect/custom/connections', json=body)
    assert res.status_code == 409 and res.json['code'] == 'unsafe_directory'
    assert directory.exists() and 'youtube' in mcp._read_global_servers()


def test_junction_is_refused(env, monkeypatch):
    body, directory, _ = saved(env)
    real = Path.lstat
    class Junction:
        st_mode = 0
        st_file_attributes = 0x400
    monkeypatch.setattr(Path, 'lstat', lambda p: Junction() if p == directory else real(p))
    assert env[0].delete('/api/desk/connect/custom/connections', json=body).status_code == 409
    assert directory.exists()


def test_cleanup_error_retains_retry_record_and_reports_failure(env, monkeypatch):
    body, directory, _ = saved(env, registered=True)
    with monkeypatch.context() as patch:
        patch.setattr(removal.shutil, 'rmtree', lambda *a, **k: (_ for _ in ()).throw(PermissionError('locked')))
        res = env[0].delete('/api/desk/connect/custom/connections', json=body)
        assert res.status_code == 500 and res.json['code'] == 'remove_failed'
    assert store.get('global', None, 'youtube') and directory.exists()
    assert 'youtube' not in mcp._read_global_servers()
    assert env[0].delete('/api/desk/connect/custom/connections', json=body).status_code == 200
    assert not directory.exists()


def test_corrupt_config_not_overwritten(env):
    body, directory, _ = saved(env)
    mcp.GLOBAL_CLAUDE_JSON.write_text('broken json')
    assert env[0].delete('/api/desk/connect/custom/connections', json=body).status_code == 500
    assert directory.exists() and mcp.GLOBAL_CLAUDE_JSON.read_text() == 'broken json'


def test_removed_connection_manifest_and_observation_are_cleared(env, monkeypatch):
    body, _, _ = saved(env, 'remote')
    key = store.key('global', None, 'youtube')
    path = manifest.manifest_path(key)
    path.parent.mkdir(parents=True)
    path.write_text('{}')
    calls = []
    monkeypatch.setattr(remote_mcp_observed, 'forget', lambda *args: calls.append(args))
    assert env[0].delete('/api/desk/connect/custom/connections', json=body).status_code == 200
    assert not path.exists() and calls == [('global', None, 'youtube')]


@pytest.mark.parametrize('engine_id', list(engines.ENGINES))
def test_engine_disconnect_persists_and_blocks_execution_without_touching_vault(env, monkeypatch, engine_id):
    monkeypatch.setattr(engines.secrets_store, 'get_secret_value', lambda *a, **k: pytest.fail('credential read'))
    monkeypatch.setattr(engines.secrets_store, 'list_secrets', lambda *a, **k: pytest.fail('vault read'))
    monkeypatch.setattr(engines._oauth, 'access_token', lambda *a, **k: pytest.fail('token read'))
    monkeypatch.setattr(engines, '_http_request', lambda *a, **k: pytest.fail('network'))
    res = env[0].delete(f'/api/desk/engines/{engine_id}/connection', json={'passcode': PASSCODE})
    assert res.status_code == 200
    assert json.loads(engines_state._path().read_text())[engine_id] is False
    assert engines.connection(engine_id)['state'] == 'disconnected'
    model = engines.ENGINES[engine_id].models[0]
    request = {'engine_id': engine_id, 'model_id': model.model_id, 'kind': model.kind, 'prompt': 'fixture',
               'aspect_ratio': '16:9', 'desk': {'idempotency_key': 'new-job'}}
    with pytest.raises(engines.NotConnected):
        engines.estimate(request)
    with pytest.raises(engines.NotConnected):
        engines.submit(request)
    with pytest.raises(engines.NotConnected):
        engines._creds(engine_id, None, False)


@pytest.mark.parametrize('proof', [None, 'wrong'])
def test_engine_remove_requires_actual_passcode(env, proof):
    res = env[0].delete('/api/desk/engines/google/connection', json={'passcode': proof}, headers={'Origin': 'http://localhost:5199'})
    assert res.status_code == 403 and not engines_state._path().exists()


def test_engine_unattended_cannot_disconnect(env, monkeypatch):
    monkeypatch.setattr(engine_routes, 'is_unattended_caller', lambda: True)
    assert env[0].delete('/api/desk/engines/google/connection', json={'passcode': PASSCODE}).status_code == 403
    assert not engines_state._path().exists()


@pytest.mark.parametrize('service,method,engine_id', [('google_ai','api_key','google'), ('openai','api_key','openai'),
                                                 ('higgsfield','api_key','higgsfield'), ('higgsfield','oauth','higgsfield_mcp')])
def test_explicit_add_service_reconnect_and_rollback(env, service, method, engine_id):
    engines_state.set_connected(engine_id, False)
    undo = UndoStack()
    engines_state.reconnect_saved(service, method, undo)
    assert not engines_state.disconnected(engine_id)
    assert undo.unwind() == [] and engines_state.disconnected(engine_id)


def test_corrupt_engine_state_fails_closed(env, monkeypatch):
    engines_state._path().parent.mkdir(parents=True)
    engines_state._path().write_text('bad json')
    assert not engines.connection('google')['ready']
    res = env[0].delete('/api/desk/engines/google/connection', json={'passcode': PASSCODE})
    assert res.status_code == 500 and engines_state._path().read_text() == 'bad json'


def test_engine_reconnect_keeps_existing_vault_credential(env, monkeypatch):
    engines_state.set_connected('google', False)
    monkeypatch.setattr(engines.secrets_store, 'list_secrets', lambda *a, **k: [{'name':'gemini-api'}])
    monkeypatch.setattr(engines._vault_lock, 'probe', lambda name: engines._vault_lock.OK)
    monkeypatch.setattr(engines.secrets_store, 'set_secret', lambda *a, **k: pytest.fail('credential written'))
    monkeypatch.setattr(engines.secrets_store, 'delete_secret', lambda *a, **k: pytest.fail('credential removed'))
    no_proof = env[0].put('/api/desk/engines/google/connection', json={})
    assert no_proof.status_code == 403 and engines_state.disconnected('google')
    result = env[0].put('/api/desk/engines/google/connection', json={'passcode':PASSCODE})
    assert result.status_code == 200 and result.json['connection']['ready']
    assert not engines_state.disconnected('google')


def test_nested_install_junction_does_not_delete_its_target(env):
    import os
    import subprocess
    body, directory, _ = saved(env)
    outside = Path(env[1]).parent / 'unrelated'
    outside.mkdir()
    (outside / 'keep.txt').write_text('keep')
    link = directory / 'dependency-link'
    if os.name == 'nt':
        result = subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],capture_output=True)
        assert result.returncode == 0, result.stderr
    else:
        link.symlink_to(outside, target_is_directory=True)
    assert env[0].delete('/api/desk/connect/custom/connections', json=body).status_code == 200
    assert not directory.exists() and (outside / 'keep.txt').read_text() == 'keep'
