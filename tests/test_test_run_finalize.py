"""Phase 4b: real HTTP resolver with controlled OS table; isolated persistence."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest

from mc import caller_attribution as ca, state, test_run_store as runs
from mc import test_run_finalize as final
from mc.test_run_ingest import IngestRefused, OwnerBinding, TestRunIngest
from test_test_run_routes import env, enroll, url, auth, submit, event, BASE, RUN


def result(code=0, signal=None, termination='exited'):
    return dict(exit_code=code, signal=signal, termination=termination)


def finish(env, record, body=None, headers=None):
    return env.client.post(url(record, '/finalize'), json=result() if body is None else body,
                           headers=cap_auth(record) if headers is None else headers)


def cap_auth(record):
    return {'Authorization': 'Bearer ' + record['finalize_capability']['token']}


@pytest.fixture
def core(tmp_path):
    owners = {'alice': OwnerBinding('a', 's1', False, False, process_identity='pid:1@2.0'),
              'sibling': OwnerBinding('a', 's1', False, False, process_identity='pid:3@2.0'),
              'bob': OwnerBinding('a', 's2', False, False, process_identity='pid:4@2.0'),
              'other': OwnerBinding('b', 's1', False, False, process_identity='pid:5@2.0')}
    now = [100.0]
    store = runs.TestRunStore(tmp_path / 'data')
    ingest = TestRunIngest(store, resolve_owner=lambda p, c: owners.get(c), clock=lambda: now[0])
    service = final.TestRunFinalize(ingest)
    yield SimpleNamespace(owners=owners, now=now, store=store, ingest=ingest, service=service)
    service.close()
    ingest.close()


def test_launcher_evidence_persists_and_exact_retry_is_write_free(core, monkeypatch):
    run, reporter, capability = core.service.enroll('a', 'alice', {})
    assert reporter.token != capability.token
    snapshot = core.service.finalize('a', 'alice', run['run_id'], capability.token, result())
    assert snapshot['state'] == 'passed' and snapshot['exit_code'] == 0
    assert snapshot['process_evidence'] == dict(source='launcher_observed',
        process_identity='pid:1@2.0', signal=None, termination='exited')
    history = core.store.events('a', run['run_id'])
    assert len(history) == snapshot['seq'] == 1
    assert history[0]['payload']['evidence'] == snapshot['process_evidence']
    raw = core.store._path('a', run['run_id']).read_text()
    assert capability.token not in raw and reporter.token not in raw
    assert 'server_observed' not in raw
    assert runs.TestRunStore(core.store.root.parent).get('a', run['run_id']) == snapshot
    core.now[0] += 100000  # The live process can finish/retry past reporter expiry.
    monkeypatch.setattr(runs, 'write_json_atomic', lambda *a, **kw: pytest.fail('retry wrote'))
    assert core.service.finalize('a', 'alice', run['run_id'], capability.token, result()) == snapshot


@pytest.mark.parametrize('body', [result(1), result(-9, 9, 'signalled'), result(1, None, 'timeout'),
                                   result(1, None, 'cancelled')])
def test_conflicting_second_finalize_refused_without_change(core, body):
    run, _, cap = core.service.enroll('a', 'alice', {})
    first = core.service.finalize('a', 'alice', run['run_id'], cap.token, result())
    with pytest.raises(final.FinalizeConflict):
        core.service.finalize('a', 'alice', run['run_id'], cap.token, body)
    assert core.store.get('a', run['run_id']) == first
    assert len(core.store.events('a', run['run_id'])) == 1


@pytest.mark.parametrize('code,signal,termination,state_name', [
    (0, None, 'exited', 'passed'), (1, None, 'exited', 'failed'),
    (3221225477, None, 'exited', 'failed'), (-15, 15, 'signalled', 'failed'),
    (143, 15, 'signalled', 'failed'), (-9, 9, 'timeout', 'timeout'),
    (1, None, 'timeout', 'timeout'), (-15, 15, 'cancelled', 'cancelled')])
def test_observed_exit_outcomes(core, code, signal, termination, state_name):
    run, _, cap = core.service.enroll('a', 'alice', {})
    snapshot = core.service.finalize('a', 'alice', run['run_id'], cap.token,
                                     result(code, signal, termination))
    assert snapshot['state'] == state_name and snapshot['exit_code'] == code
    assert snapshot['process_evidence']['signal'] == signal
    assert snapshot['process_evidence']['termination'] == termination


@pytest.mark.parametrize('body', [
    {}, result(None), result(True), result(0.0), result(2**32), result(-(2**31)-1),
    result(0, 9), result(-9), result(0, 9, 'signalled'), result(1, None, 'signalled'),
    result(1, 0, 'signalled'), result(1, True, 'signalled'), result(1, 256, 'signalled'),
    result(0, None, 'timeout'), result(0, None, 'cancelled'), result(1, None, 'lost'),
    dict(result(), state='passed'), dict(result(), evidence_source='server_observed'),
    dict(result(), process_identity='forged'), dict(result(), owner_session_id='s2'),
    dict(result(), stdout='secret'), dict(result(), observed_at=1),
])
def test_invalid_results_never_mutate(core, body):
    run, _, cap = core.service.enroll('a', 'alice', {})
    with pytest.raises(ValueError):
        core.service.finalize('a', 'alice', run['run_id'], cap.token, body)
    assert core.store.get('a', run['run_id']) == run


@pytest.mark.parametrize('caller,project', [('sibling', 'a'), ('bob', 'a'), ('other', 'b')])
def test_capability_alone_does_not_authorize_other_identity(core, caller, project):
    run, _, cap = core.service.enroll('a', 'alice', {})
    with pytest.raises((IngestRefused, KeyError)):
        core.service.finalize(project, caller, run['run_id'], cap.token, result())
    assert core.store.get('a', run['run_id']) == run


def test_reporter_receipt_and_launcher_capability_are_not_interchangeable(core):
    run, reporter, cap = core.service.enroll('a', 'alice', {})
    with pytest.raises(IngestRefused):
        core.service.finalize('a', 'alice', run['run_id'], reporter.token, result())
    with pytest.raises(IngestRefused):
        core.ingest.submit('a', 'alice', run['run_id'], cap.token, [event()])
    finished = core.ingest.submit('a', 'alice', run['run_id'], reporter.token,
                                   [event(1, 'run_finished', state='passed', exit_code=0)])
    assert finished['state'] == 'running' and finished['reporter_state'] == 'passed'
    assert 'process_evidence' not in finished
    forged = event(2, 'run_finished', state='passed', exit_code=0,
                   evidence=dict(source='launcher_observed', process_identity='pid:1@2.0',
                                 signal=None, termination='exited'))
    with pytest.raises(ValueError):
        core.ingest.submit('a', 'alice', run['run_id'], reporter.token, [forged])
    assert core.store.get('a', run['run_id']) == finished


@pytest.mark.parametrize('token', ['', 'f' * 64, '\u2603' * 64, 'x' * 129])
def test_forged_tokens_refused(core, token):
    run, _, _ = core.service.enroll('a', 'alice', {})
    with pytest.raises(IngestRefused):
        core.service.finalize('a', 'alice', run['run_id'], token, result())


def test_capability_is_scoped_to_run_and_service_lifetime(core):
    first, _, cap = core.service.enroll('a', 'alice', {})
    second, _, _ = core.service.enroll('a', 'alice', {})
    with pytest.raises(IngestRefused):
        core.service.finalize('a', 'alice', second['run_id'], cap.token, result())
    core.service.close()
    with pytest.raises(IngestRefused):
        core.service.finalize('a', 'alice', first['run_id'], cap.token, result())
    with pytest.raises(IngestRefused):
        final.TestRunFinalize(core.ingest).finalize('a', 'alice', first['run_id'], cap.token, result())


@pytest.mark.parametrize('mode', ['incognito', 'sandboxed', 'no_session', 'no_identity', 'unknown'])
def test_current_policy_and_identity_rechecked_before_storage(core, monkeypatch, mode):
    run, _, cap = core.service.enroll('a', 'alice', {})
    if mode in ('incognito', 'sandboxed'):
        core.owners['alice'] = replace(core.owners['alice'], **{mode: True})
    elif mode == 'no_session':
        core.owners['alice'] = replace(core.owners['alice'], session_id=None)
    elif mode == 'no_identity':
        core.owners['alice'] = replace(core.owners['alice'], process_identity=None)
    else:
        core.owners.pop('alice')
    monkeypatch.setattr(core.store, '_read', lambda *a: pytest.fail('unauthorized storage read'))
    with pytest.raises(IngestRefused):
        core.service.finalize('a', 'alice', run['run_id'], cap.token, result())
    with pytest.raises(IngestRefused):
        core.service.enroll('a', 'alice', {})


def test_child_counts_are_never_added_to_parent_and_parent_exit_does_not_finish_children(core):
    parent, reporter, cap = core.service.enroll('a', 'alice', {'unit': 'script'})
    child, child_reporter, child_cap = core.service.enroll(
        'a', 'alice', {'unit': 'test'}, parent_run_id=parent['run_id'])
    assert child['parent_run_id'] == parent['run_id']
    core.ingest.submit('a', 'alice', child['run_id'], child_reporter.token,
        [event(1, 'collection', selected_total=100, revision=1),
         event(2, 'unit_report', unit_id='child-test', attempt=1, phase='call',
               outcome='pass', final=True, revision=1)])
    core.ingest.submit('a', 'alice', parent['run_id'], reporter.token,
        [event(1, 'collection', selected_total=1, revision=1),
         event(2, 'unit_report', unit_id='script', attempt=1, phase='call',
               outcome='pass', final=True, revision=1)])
    snapshot = core.service.finalize('a', 'alice', parent['run_id'], cap.token, result())
    assert snapshot['selected_total'] == snapshot['completed'] == 1
    assert snapshot['counts']['pass'] == 1 and snapshot['percent'] == 100
    assert core.store.get('a', child['run_id'])['state'] == 'running'
    core.service.finalize('a', 'alice', child['run_id'], child_cap.token, result())
    assert core.store.get('a', parent['run_id']) == snapshot


@pytest.mark.parametrize('caller,project', [('bob', 'a'), ('other', 'b')])
def test_foreign_parent_refused(core, caller, project):
    parent, _, _ = core.service.enroll('a', 'alice', {})
    before = core.store.list_runs(project)
    with pytest.raises((IngestRefused, KeyError)):
        core.service.enroll(project, caller, {}, parent_run_id=parent['run_id'])
    assert core.store.list_runs(project) == before


@pytest.mark.parametrize('terminal', sorted(runs.TERMINAL_STATES))
def test_all_terminal_parents_refused(core, terminal):
    parent, _, _ = core.service.enroll('a', 'alice', {})
    core.store.finish('a', parent['run_id'], state=terminal, observed_at=101, exit_code=0)
    with pytest.raises(IngestRefused):
        core.service.enroll('a', 'alice', {}, parent_run_id=parent['run_id'])
    assert len(core.store.list_runs('a')) == 1


def test_prior_capture_terminal_evidence_cannot_be_replaced(core):
    run, _, cap = core.service.enroll('a', 'alice', {})
    core.store.finish('a', run['run_id'], state='passed', exit_code=0, observed_at=101)
    with pytest.raises(final.FinalizeConflict):
        core.service.finalize('a', 'alice', run['run_id'], cap.token, result())


def test_reported_failure_still_outranks_launcher_exit_zero(core):
    run, reporter, cap = core.service.enroll('a', 'alice', {})
    core.ingest.submit('a', 'alice', run['run_id'], reporter.token,
        [event(1, 'unit_report', unit_id='failed-test', attempt=1, phase='call',
               outcome='fail', final=True, revision=1)])
    first = core.service.finalize('a', 'alice', run['run_id'], cap.token, result())
    assert first['state'] == 'failed' and first['exit_code'] == 0
    assert core.service.finalize('a', 'alice', run['run_id'], cap.token, result()) == first


def test_atomic_failure_can_be_retried_without_false_terminal_state(core, monkeypatch):
    run, _, cap = core.service.enroll('a', 'alice', {})
    original = runs.write_json_atomic
    def fail(*a, **kw):
        raise OSError('test storage failure')
    monkeypatch.setattr(runs, 'write_json_atomic', fail)
    with pytest.raises(OSError):
        core.service.finalize('a', 'alice', run['run_id'], cap.token, result())
    assert core.store.get('a', run['run_id']) == run
    monkeypatch.setattr(runs, 'write_json_atomic', original)
    assert core.service.finalize('a', 'alice', run['run_id'], cap.token, result())['state'] == 'passed'


def test_concurrent_conflicting_finalizers_commit_one_result(core):
    run, _, cap = core.service.enroll('a', 'alice', {})
    def attempt(code):
        try:
            return core.service.finalize('a', 'alice', run['run_id'], cap.token, result(code))
        except final.FinalizeConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [0, 1]))
    assert results.count('conflict') == 1
    assert len(core.store.events('a', run['run_id'])) == 1


def test_http_capability_and_conflict_contract(env):
    record = enroll(env)
    assert record['finalize_capability']['run_id'] == record['run']['run_id']
    assert finish(env, record, headers=auth(record)).status_code == 403
    assert submit(env, record, [event(1, 'run_finished', state='passed', exit_code=0)]).get_json()['state'] == 'running'
    first = finish(env, record)
    assert first.status_code == 200 and first.headers['Cache-Control'] == 'no-store'
    assert first.get_json()['process_evidence']['process_identity'] == 'pid:4444@123.0'
    assert finish(env, record).get_json() == first.get_json()
    conflict = finish(env, record, result(1))
    assert conflict.status_code == 409
    assert record['finalize_capability']['token'] not in conflict.get_data(as_text=True)
    assert env.client.get(url(record)).get_json() == first.get_json()


def test_http_sibling_socket_and_reused_pid_cannot_finalize(env, monkeypatch):
    record = enroll(env)
    monkeypatch.setattr(ca, '_owner_pid_system_wide', lambda *a: 5555)
    assert finish(env, record).status_code == 403  # Same managed ancestor/session.
    monkeypatch.setattr(ca, '_owner_pid_system_wide', lambda *a: 4444)
    from mc.blueprints import test_run_routes as routes
    monkeypatch.setattr(routes, '_proc_identity', lambda pid: ('test', 999.0))
    assert finish(env, record).status_code == 403  # Same PID, different creation time.
    assert env.client.get(url(record)).get_json()['state'] == 'running'


@pytest.mark.parametrize('created', [None, float('nan'), float('inf'), -1, True, '123.0'])
def test_missing_or_invalid_peer_identity_refuses_enrollment(env, monkeypatch, created):
    from mc.blueprints import test_run_routes as routes
    monkeypatch.setattr(routes, '_proc_identity', lambda pid: ('test', created))
    assert env.client.post(BASE + '/enroll', json={'process_identity': 'forged'}).status_code == 403
    assert not (env.tmp / 'data').exists()


def test_unprivileged_socket_tree_fallback_preserves_actual_peer(env, monkeypatch):
    psutil = ca._load_psutil()
    def denied(*a, **kw):
        raise PermissionError('test')
    monkeypatch.setattr(psutil, 'net_connections', denied)
    monkeypatch.setattr(ca, '_search_managed_trees', lambda *a: ((1111, 4444), False))
    record = enroll(env)
    assert record['run']['process_identity'] == 'pid:4444@123.0'
    assert finish(env, record).status_code == 200


def test_http_parent_scope_terminal_and_missing_validation(env):
    parent = enroll(env)
    child = enroll(env, parent_run_id=parent['run']['run_id'], owner_session_id='s2')
    assert child['run']['parent_run_id'] == parent['run']['run_id']
    assert child['run']['owner_session_id'] == 's1'
    env.peer['parents'] = [2222]
    assert env.client.post(BASE + '/enroll', json={'parent_run_id': parent['run']['run_id']}).status_code == 403
    env.peer['parents'] = [3333]
    assert env.client.post(BASE.replace('/a/', '/b/') + '/enroll',
                           json={'parent_run_id': parent['run']['run_id']}).status_code == 404
    env.peer['parents'] = [1111]
    assert finish(env, parent).status_code == 200
    assert env.client.post(BASE + '/enroll', json={'parent_run_id': parent['run']['run_id']}).status_code == 403
    assert env.client.post(BASE + '/enroll', json={'parent_run_id': RUN}).status_code == 404
    assert env.client.post(BASE + '/enroll', json={'parent_run_id': 42}).status_code == 400


@pytest.mark.parametrize('flag', [False, None, 1, 'true'])
def test_finalize_disabled_before_body_attribution_and_storage(env, monkeypatch, flag):
    from mc.blueprints import test_run_routes as routes
    state.CONFIG[routes.FLAG] = flag
    monkeypatch.setattr(routes, '_attribution', lambda: pytest.fail('dark attribution'))
    monkeypatch.setattr(routes, '_storage', lambda: pytest.fail('dark storage'))
    response = env.client.post(BASE + '/' + RUN + '/finalize', data='not json')
    assert response.status_code == 404 and routes._finalize is None


@pytest.mark.parametrize('mode', ['incognito', '_codex_unattended_sandbox', 'project', '_incognito',
                                   ca.UNAVAILABLE, ca.UNATTRIBUTED])
def test_finalize_restricted_or_unattributed_before_file_io(env, monkeypatch, mode):
    record = enroll(env)
    if mode in (ca.UNAVAILABLE, ca.UNATTRIBUTED):
        monkeypatch.setattr(ca, 'attribute_caller', lambda *a, **kw: ca.Attribution(mode))
    elif mode == 'project':
        env.projects['a']['_is_incognito_project'] = True
    elif mode == '_incognito':
        env.projects['_incognito'] = env.projects['a']
        record['run']['run_id'] = RUN
    else:
        env.sessions['s1'][mode] = True
    from mc.blueprints import test_run_routes as routes
    monkeypatch.setattr(routes, '_storage', lambda: pytest.fail('restricted storage'))
    if mode == '_incognito':
        response = env.client.post('/api/project/_incognito/test-runs/' + RUN + '/finalize',
                                   json=result(), headers=cap_auth(record))
    else:
        response = finish(env, record)
    assert response.status_code == 403


def test_disable_reenable_invalidates_capability_without_erasing_history(env):
    from mc.blueprints import test_run_routes as routes
    record = enroll(env)
    state.CONFIG[routes.FLAG] = False
    assert finish(env, record).status_code == 404
    state.CONFIG[routes.FLAG] = True
    assert finish(env, record).status_code == 403
    assert env.client.get(url(record)).get_json() == record['run']
