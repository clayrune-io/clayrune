"""Phase-4 HTTP boundary; real resolver and attribution algorithm, isolated OS table."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

from flask import Flask
import pytest

from mc import caller_attribution as ca, state, unattended
from mc.blueprints import settings_routes as settings, test_run_routes as routes

BASE = '/api/project/a/test-runs'
RUN = '0' * 32
SURFACE = [
    ('GET', ''), ('GET', '/' + RUN), ('GET', '/' + RUN + '/events'),
    ('POST', '/enroll'), ('POST', '/' + RUN + '/producer'),
    ('POST', '/' + RUN + '/submit'), ('POST', '/' + RUN + '/spool'),
    ('POST', '/' + RUN + '/spool/attach'), ('POST', '/' + RUN + '/spool/poll'),
    ('POST', '/' + RUN + '/revoke'),
]


class Proc:
    def __init__(self, pid):
        self.pid = pid

    def poll(self):
        return None

    def create_time(self):
        return 123.0


@pytest.fixture
def env(tmp_path, monkeypatch):
    routes.close()
    projects = {p: {'id': p, 'project_path': str(tmp_path / p)} for p in ('a', 'b')}
    sessions = {
        's1': dict(session_id='s1', project_id='a', proc=Proc(1111),
                   _agent_cwd=str(tmp_path / 'worktree'), trigger_type='dispatch', status='running'),
        's2': dict(session_id='s2', project_id='a', proc=Proc(2222)),
        's3': dict(session_id='s3', project_id='b', proc=Proc(3333)),
    }
    peer = {'parents': [1111]}
    class PeerProc(Proc):
        def parents(self):
            return [Proc(pid) for pid in peer['parents']]

    psutil = SimpleNamespace(
        Process=PeerProc,
        net_connections=lambda **kw: [SimpleNamespace(
            laddr=SimpleNamespace(port=45000), raddr=SimpleNamespace(port=5199), pid=4444)])
    monkeypatch.setattr(ca, '_load_psutil', lambda: psutil)
    monkeypatch.setattr(state, 'CONFIG', {routes.FLAG: True})
    monkeypatch.setattr(state, 'agent_sessions', sessions)
    monkeypatch.setattr(state, 'tracked_processes', {})
    monkeypatch.setattr(unattended, 'agent_sessions', sessions)
    archived = []
    routes.wire(data_root=tmp_path / 'data', load_project_fn=projects.get,
                load_agent_log_fn=lambda pid: archived if pid == 'a' else [],
                session_cwd_fn=lambda sess, pp: sess.get('_agent_cwd') or pp,
                proc_identity_fn=lambda pid: ('test', 123.0))
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config['SERVER_NAME'] = 'localhost:5199'
    app.register_blueprint(routes.bp)
    app.register_blueprint(settings.bp)
    client = app.test_client()
    client.environ_base.update(REMOTE_ADDR='127.0.0.1', REMOTE_PORT='45000', SERVER_PORT='5199')
    result = SimpleNamespace(client=client, app=app, tmp=tmp_path, peer=peer,
                             sessions=sessions, projects=projects, archived=archived)
    yield result
    routes.close()


def enroll(env, **extra):
    response = env.client.post(BASE + '/enroll', json=dict(metadata={'runner': 'pytest'}, **extra))
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()


def url(record, suffix=''):
    return BASE + '/' + record['run']['run_id'] + suffix


def auth(record):
    return {'Authorization': 'Bearer ' + record['receipt']['token']}


def event(seq=1, kind='heartbeat', **payload):
    return dict(schema=1, producer_seq=seq, type=kind, payload=payload)


def submit(env, record, events):
    return env.client.post(url(record, '/submit'), json={'events': events}, headers=auth(record))


@pytest.mark.parametrize('method,path', SURFACE)
@pytest.mark.parametrize('flag', [False, None, 'true', 1])
def test_off_all_routes_404_without_constructing_ingest_or_storage(env, monkeypatch, method, path, flag):
    state.CONFIG[routes.FLAG] = flag
    monkeypatch.setattr(routes, 'TestRunIngest', lambda *a, **kw: pytest.fail('dark ingest'))
    monkeypatch.setattr(routes, 'TestRunStore', lambda *a, **kw: pytest.fail('dark store'))
    response = env.client.open(BASE + path, method=method, data='not JSON')
    assert response.status_code == 404
    assert not (env.tmp / 'data').exists()
    assert routes._ingest is None


def test_flag_unset_is_dark_and_allowlisted(env):
    from mc.blueprints.settings_routes import _CONFIG_EDITABLE_KEYS
    state.CONFIG.pop(routes.FLAG)
    assert env.client.get(BASE).status_code == 404
    assert routes.FLAG in _CONFIG_EDITABLE_KEYS
    # Parse the actual server wiring/default without importing a second server.
    import ast
    server = Path(__file__).resolve().parents[1] / 'server.py'
    tree = ast.parse(server.read_text(encoding='utf-8'))
    assert any(isinstance(n, ast.Dict) and any(
        isinstance(k, ast.Constant) and k.value == routes.FLAG
        and isinstance(v, ast.Constant) and v.value is False
        for k, v in zip(n.keys, n.values)) for n in ast.walk(tree))
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == 'register_blueprint' and n.args
               and ast.unparse(n.args[0]) == '_bp_test_runs.bp' for n in ast.walk(tree))


def test_real_resolver_ignores_forged_body_owner_and_origin(env):
    record = enroll(env, owner_session_id='s2', session_id='s2', project_id='b',
                    owner={'session_id': 's2'}, cwd='private', source='job',
                    process_identity='fake', job_id='fake', incognito=False)
    run = record['run']
    assert run['owner_session_id'] == 's1'
    assert run['project_id'] == 'a'
    assert run['cwd'] == str(env.tmp / 'worktree')
    assert run['process_identity'] == 'pid:4444@123.0'
    assert run['job_id'] is run['owner_turn_id'] is None
    assert run['source'] == 'wrapper'
    response = env.client.post(BASE + '/enroll', json={'owner_session_id': 's2'},
                               headers={'Origin': 'http://localhost:5199'})
    assert response.status_code == 201
    assert response.get_json()['run']['owner_session_id'] == 's1'


@pytest.mark.parametrize('status', [ca.UNAVAILABLE, ca.UNATTRIBUTED])
@pytest.mark.parametrize('suffix', ['/enroll', '/' + RUN + '/producer', '/' + RUN + '/submit',
                                    '/' + RUN + '/spool', '/' + RUN + '/spool/attach',
                                    '/' + RUN + '/spool/poll', '/' + RUN + '/revoke'])
def test_no_attribution_never_becomes_project_only(env, monkeypatch, status, suffix):
    monkeypatch.setattr(ca, 'attribute_caller', lambda *a, **kw: ca.Attribution(status))
    response = env.client.post(BASE + suffix,
                               json={'owner_session_id': None, 'session_id': 's1', 'source': 'harness'},
                               headers={'Origin': 'http://localhost:5199'})
    assert response.status_code == 403
    assert routes._ingest is None
    assert not (env.tmp / 'data').exists()


def test_missing_peer_port_refuses_real_attribution(env):
    response = env.client.post(BASE + '/enroll', json={}, environ_overrides={'REMOTE_PORT': ''})
    assert response.status_code == 403


@pytest.mark.parametrize('change', ['missing', 'wrong_project', 'wrong_sid', 'dead_root'])
def test_server_records_must_still_match_attributed_owner(env, monkeypatch, change):
    att = ca.Attribution(ca.ATTRIBUTED, 's1', 1111)
    monkeypatch.setattr(ca, 'attribute_caller', lambda *a, **kw: att)
    if change == 'missing':
        env.sessions.pop('s1')
    elif change == 'wrong_project':
        env.sessions['s1']['project_id'] = 'b'
    elif change == 'wrong_sid':
        env.sessions['s1']['session_id'] = 's2'
    else:
        env.sessions['s1']['proc'] = None
    assert env.client.post(BASE + '/enroll', json={}).status_code == 403
    assert not (env.tmp / 'data').exists()


def test_tracked_job_root_resolves_live_session_without_active_cli(env):
    env.sessions['s1']['proc'] = None
    env.sessions['s1']['status'] = 'idle'
    state.tracked_processes[5555] = dict(proc=Proc(5555), session_id='s1', project_id='a', type='agent_job')
    env.peer['parents'] = [5555]
    assert enroll(env)['run']['owner_session_id'] == 's1'
    state.tracked_processes[5555]['project_id'] = 'b'
    assert env.client.post(BASE + '/enroll', json={}).status_code == 403


@pytest.mark.parametrize('flag', ['incognito', '_codex_unattended_sandbox'])
@pytest.mark.parametrize('method,suffix', SURFACE)
def test_restricted_callers_refused_on_entire_surface(env, flag, method, suffix):
    env.sessions['s1'][flag] = True
    response = env.client.open(BASE + suffix, method=method, json={})
    assert response.status_code == 403
    assert not (env.tmp / 'data').exists()


def test_policy_rechecked_after_receipt_and_spool_creation(env, monkeypatch):
    record = enroll(env)
    assert env.client.post(url(record, '/spool'), json={}, headers=auth(record)).status_code == 201
    env.sessions['s1']['_codex_unattended_sandbox'] = True
    monkeypatch.setattr(os, 'read', lambda *a: pytest.fail('restricted read'))
    assert env.client.post(url(record, '/spool/poll'), json={}, headers=auth(record)).status_code == 403
    assert submit(env, record, [event()]).status_code == 403


def test_project_and_session_scope_for_reads_and_ingestion(env):
    record = enroll(env)
    for path in [url(record), url(record, '/events')]:
        assert env.client.get(path + '?session=s2').status_code == 403
        assert env.client.get(path.replace('/project/a/', '/project/b/')).status_code == 403
    assert env.client.get(BASE + '?session=s3').status_code == 404
    assert env.client.post(BASE + '/enroll?session=s2', json={}).status_code == 403
    env.peer['parents'] = [2222]
    assert env.client.get(url(record)).status_code == 404
    assert env.client.get(BASE).get_json()['runs'] == []
    assert submit(env, record, [event()]).status_code == 403
    assert env.client.post(url(record, '/producer'), json={}).status_code == 403


def test_unattributed_read_matches_existing_job_status_and_historical_session(env):
    first = enroll(env)
    env.peer['parents'] = [2222]
    second = enroll(env)
    env.peer['parents'] = []  # UI/human has no managed ancestor.
    assert len(env.client.get(BASE).get_json()['runs']) == 2
    assert env.client.get(url(first)).get_json()['owner_session_id'] == 's1'
    assert env.client.get(url(second) + '?session=s1').status_code == 404
    assert env.client.get(BASE + '?session=s3').status_code == 404
    env.sessions.pop('s1')
    env.archived.append({'session_id': 's1'})
    assert env.client.get(url(first) + '?session=s1').status_code == 200
    assert env.client.get(BASE + '?session=s1').get_json()['runs'] == [first['run']]


def test_paged_canonical_cursor_survives_client_and_service_reconnect(env):
    record = enroll(env)
    assert submit(env, record, [event(i) for i in range(1, 6)]).status_code == 200
    page = env.client.get(url(record, '/events?limit=2')).get_json()
    assert [e['seq'] for e in page['events']] == [1, 2]
    assert page['has_more'] is True
    routes.close()
    routes._store = None  # Snapshot/events re-read persisted history, no in-memory cursor.
    another = env.app.test_client()
    another.environ_base.update(env.client.environ_base)
    page2 = another.get(url(record, '/events?limit=2&cursor=' + page['next_cursor'])).get_json()
    assert [e['seq'] for e in page2['events']] == [3, 4]
    page3 = another.get(url(record, '/events?cursor=' + page2['next_cursor'])).get_json()
    assert [e['seq'] for e in page3['events']] == [5]
    empty = another.get(url(record, '/events?cursor=' + page3['next_cursor'])).get_json()
    assert empty['events'] == [] and empty['next_cursor'] == page3['next_cursor']
    assert submit(env, record, [event(6)]).status_code == 403  # Old receipt dies with service.
    new = another.post(url(record, '/producer'), json={}).get_json()
    record['receipt'] = new['receipt']
    assert submit(env, record, [event(1)]).status_code == 200
    resumed = another.get(url(record, '/events?cursor=' + empty['next_cursor'])).get_json()
    assert [e['seq'] for e in resumed['events']] == [6]
    assert resumed['has_more'] is False


@pytest.mark.parametrize('query', ['limit=0', 'limit=101', 'limit=-1', 'limit=nan',
                                    'offset=-1', 'cursor=bad', 'cursor=' + RUN + ':0',
                                    'cursor={run}:999', 'cursor={run}:-1'])
def test_invalid_paging_is_refused(env, query):
    record = enroll(env)
    query = query.replace('{run}', record['run']['run_id'])
    path = BASE + '?' + query if query.startswith('offset=') else url(record, '/events?' + query)
    assert env.client.get(path).status_code == 400


def test_producer_submit_retry_revoke_and_no_process_verdict(env):
    record = enroll(env)
    headers = auth(record)
    assert env.client.post(url(record, '/submit'), json={'events': [event()]}).status_code == 403
    assert submit(env, record, [event()]).get_json()['seq'] == 1
    assert submit(env, record, [event()]).get_json()['seq'] == 1
    assert submit(env, record, [event(2, 'run_finished', state='passed', exit_code=0)]).get_json()['state'] == 'running'
    forged = dict(event(3, 'run_finished', state='passed', exit_code=0), producer_id='_store_process')
    assert submit(env, record, [forged]).status_code == 400
    assert env.client.post(url(record, '/revoke'), json={}, headers=headers).status_code == 200
    assert submit(env, record, [event(4)]).status_code == 403
    receipt = env.client.post(url(record, '/producer'), json={}).get_json()['receipt']
    assert receipt['producer_id'] != record['receipt']['producer_id']
    assert receipt['token'] not in routes._storage()._path('a', record['run']['run_id']).read_text()


def test_spool_create_attach_poll_uses_only_minted_path(env):
    record = enroll(env)
    response = env.client.post(url(record, '/spool'), json={'path': 'arbitrary'}, headers=auth(record))
    assert response.status_code == 201
    path = response.get_json()['path']
    for candidate in ['../private', str(env.tmp / 'private'), path + ':stream']:
        assert env.client.post(url(record, '/spool/attach'), json={'path': candidate},
                               headers=auth(record)).status_code == 403
    assert env.client.post(url(record, '/spool/attach'), json={'path': path},
                           headers=auth(record)).status_code == 200
    Path(path).write_text(json.dumps(event()) + '\n', encoding='utf-8')
    response = env.client.post(url(record, '/spool/poll'), json={}, headers=auth(record))
    assert response.get_json() == {'accepted': 1}
    assert env.client.get(url(record)).get_json()['seq'] == 1
    assert env.client.post(url(record, '/spool/poll'), json={}, headers=auth(record)).get_json() == {'accepted': 0}


def test_linked_data_root_is_supported_but_spool_folder_redirect_is_refused(env, monkeypatch):
    real = env.tmp / 'real-data'
    real.mkdir()
    link = env.tmp / 'linked-data'
    if os.name == 'nt':
        completed = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(real)],
                                   capture_output=True, check=True)
        assert completed.returncode == 0
    else:
        link.symlink_to(real, target_is_directory=True)
    try:
        routes._data_root = link
        record = enroll(env)
        response = env.client.post(url(record, '/spool'), json={}, headers=auth(record))
        assert response.status_code == 201
        assert Path(response.get_json()['path']).parent == real.resolve() / 'test_run_spools'
        assert routes._storage().root.parent == real.resolve()
        another = enroll(env)
        original = Path.resolve
        def redirected(path, *args, **kwargs):
            return path.parent / 'outside' if path.name == 'test_run_spools' else original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'resolve', redirected)
        monkeypatch.setattr(os, 'open', lambda *a, **kw: pytest.fail('redirected spool open'))
        assert env.client.post(url(another, '/spool'), json={}, headers=auth(another)).status_code == 403
    finally:
        routes.close()
        if os.name == 'nt':
            link.rmdir()  # Remove only the junction, never recursively traverse it.
        else:
            link.unlink()


@pytest.mark.parametrize('data,status', [('[1]', 400), ('{"metadata":NaN}', 400),
                                        ('{"metadata":{},"metadata":{}}', 400), ('{', 400),
                                        ('x' * (routes.MAX_REQUEST_BYTES + 1), 413)],
                         ids=['array', 'nan', 'duplicate', 'malformed', 'oversize'])
def test_bounded_strict_json(env, data, status):
    assert env.client.post(BASE + '/enroll', data=data, content_type='application/json').status_code == status
    assert not (env.tmp / 'data').exists()


def test_telemetry_cannot_create_execution_or_change_config(env):
    before = dict(state.CONFIG)
    # Real pre-existing unattended gate, then real passcode gate even with forged Origin.
    refusal = env.client.put('/api/config', json={routes.FLAG: True})
    assert refusal.status_code == 403 and 'unattended' in refusal.get_json()['error']
    assert env.client.put('/api/config', json={routes.FLAG: True},
                          headers={'Origin': 'http://localhost:5199'}).status_code == 403
    assert state.CONFIG == before
    record = enroll(env, command='must never run', enabled=True, run=True)
    assert env.client.post(url(record, '/finish'), json={'state': 'passed'}).status_code == 404
    assert env.client.post(url(record, '/cancel'), json={}).status_code == 404
    assert env.client.get(url(record)).get_json()['state'] == 'running'


def test_disable_closes_receipts_and_no_cache(env):
    record = enroll(env)
    response = env.client.get(url(record))
    assert response.headers['Cache-Control'] == 'no-store'
    assert routes._ingest is not None
    state.CONFIG[routes.FLAG] = False
    assert env.client.get(BASE).status_code == 404
    assert routes._ingest is None
    state.CONFIG[routes.FLAG] = True
    assert submit(env, record, [event()]).status_code == 403


def test_corrupt_store_is_500_and_no_telemetry_or_receipt_leaked(env):
    record = enroll(env)
    routes._storage()._path('a', record['run']['run_id']).write_text('broken SECRET', encoding='utf-8')
    response = env.client.get(url(record))
    assert response.status_code == 500
    assert 'SECRET' not in response.get_data(as_text=True)
    assert record['receipt']['token'] not in response.get_data(as_text=True)


def test_enabled_read_is_lazy_and_paging_filters_own_session(env, monkeypatch):
    monkeypatch.setattr(routes, 'TestRunIngest', lambda *a, **kw: pytest.fail('read-only ingest'))
    assert env.client.get(BASE).get_json() == {'runs': []}
    store = routes._storage()
    store.create('a', observed_at=1, owner_session_id='s1')
    later = store.create('a', observed_at=2, owner_session_id='s1')
    store.create('a', observed_at=3, owner_session_id='s2')
    response = env.client.get(BASE + '?limit=1&offset=0')
    assert response.get_json() == {'runs': [later]}
    assert env.client.get(BASE + '?limit=1&offset=1').get_json()['runs'][0]['created_at'] == 1
    assert routes._ingest is None


def test_project_missing_incognito_and_wrong_media_type(env):
    assert env.client.get('/api/project/unknown/test-runs').status_code == 404
    env.projects['_incognito'] = {'id': '_incognito'}
    assert env.client.post('/api/project/_incognito/test-runs/enroll', json={}).status_code == 403
    assert env.client.post(BASE + '/enroll', data='{}', content_type='text/plain').status_code == 415
    assert env.client.post(BASE + '/enroll', json={'metadata': {'owner_session_id': 's2'}}).status_code == 400
    assert not (env.tmp / 'data').exists()


def test_unavailable_reads_fail_closed_and_cross_project_run_cannot_be_read(env, monkeypatch):
    record = enroll(env)
    monkeypatch.setattr(ca, 'attribute_caller', lambda *a, **kw: ca.Attribution(ca.UNAVAILABLE))
    assert env.client.get(BASE).status_code == 403
    monkeypatch.setattr(ca, 'attribute_caller', lambda *a, **kw: ca.Attribution(ca.UNATTRIBUTED))
    assert env.client.get(url(record).replace('/project/a/', '/project/b/')).status_code == 404
