"""HR-4b reviewer-verdict HTTP adapter: routes over the real HR-3 store."""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from types import SimpleNamespace
from dataclasses import replace

import pytest
from flask import Flask

from mc import caller_attribution as ca, hivemind_review as hr, review_lineage, state
from mc.blueprints import hivemind_review_routes as routes
from mc.merge_review_gate import ReviewRefused

HM = 'hm_run'
WS = 'ws_one'
URL = f'/api/hivemind/{HM}/review/{WS}/1'


@pytest.fixture
def candidate():
    return hr.Candidate(
        project_id='project', project_generation=7, hivemind_id=HM,
        workstream_id=WS, integration_attempt=1, workstream_attempt=2,
        pre_merge_sha='a' * 40, incoming_branch='clayrune/agent/hm_worker',
        incoming_tip='b' * 40, merged_sha='c' * 40, merged_tree='d' * 40,
        contributing_sessions=('earlier_worker', 'hm_worker'))


@pytest.fixture
def env(tmp_path, monkeypatch, candidate):
    hm_dir = tmp_path / 'hiveminds'
    (hm_dir / HM).mkdir(parents=True)
    (hm_dir / HM / 'manifest.json').write_text(
        json.dumps({'project_id': 'project', 'project_generation': 7}), encoding='utf-8')
    checked = []
    projects = {'project': {'id': 'project'}}

    def assert_generation(project_id, generation):
        checked.append((project_id, generation))

    life = {'veto': None, 'raise': None}

    @contextmanager
    def lifecycle_guard(candidate):
        if life['veto']:
            raise ReviewRefused(409, f"reviewer {life['veto']}; integration held")
        yield

    def lifecycle_status(candidate):
        if life['raise']:
            raise life['raise']
        rec = hr.ReviewStore(hm_dir / HM).find(candidate)
        if life['veto']:
            return {'state': life['veto'], 'hold_reason': f"reviewer {life['veto']}",
                    'result': None, 'can_record': False, 'deadline_at': 1.0, 'stop_error': ''}
        res = rec['result']
        return {'state': res['verdict'] if res else 'pending',
                'hold_reason': hr.ReviewStore(hm_dir / HM).hold_reason(candidate),
                'result': res, 'can_record': True, 'deadline_at': 1.0, 'stop_error': ''}

    routes.wire(hivemind_dir=hm_dir, load_project_fn=projects.get,
                assert_project_generation_fn=assert_generation,
                verdict_guard_fn=lifecycle_guard, reviewer_status_fn=lifecycle_status)
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    caller = {'sid': 'reviewer', 'status': ca.ATTRIBUTED}
    monkeypatch.setattr(ca, 'attribute_caller',
                        lambda *a: ca.Attribution(caller['status'], caller['sid']))
    monkeypatch.setattr(state, 'agent_sessions', {})
    monkeypatch.setattr(state, 'tracked_processes', {})
    parents = {'reviewer': ['coordinator'], 'other': ['coordinator'],
               'hm_worker': ['coordinator'], 'earlier_worker': ['coordinator'],
               'coordinator': []}

    def lookup(sid):
        if sid not in parents:
            raise review_lineage.LineageUnreadable(sid)
        return parents[sid]

    monkeypatch.setattr(review_lineage, '_production_spawner_of', lambda: lookup)
    store = hr.ReviewStore(hm_dir / HM)
    store.designate(candidate, reviewer_session='reviewer',
                    reviewer_character='global:code-reviewer')
    yield type('Env', (), dict(client=app.test_client(), store=store, caller=caller,
                               hm_dir=hm_dir, checked=checked, life=life, projects=projects,
                               candidate=candidate))
    routes.wire(hivemind_dir=hm_dir, load_project_fn=lambda _: None,
                assert_project_generation_fn=lambda *_: None,
                verdict_guard_fn=None, reviewer_status_fn=None)


def submit(env, body=None, url=URL, **fields):
    payload = {'verdict': 'pass', 'merged_sha': 'c' * 40, 'report_path': 'docs/r.md'}
    payload.update(body or {})
    payload.update(fields)
    return env.client.post(url, json=payload)


def test_designated_reviewer_records_pass_and_state_reads_back(env):
    pending = env.client.get(URL).get_json()['review']
    assert pending['hold_reason'] == 'awaiting designated reviewer verdict'
    assert pending['reviewer_session'] == 'reviewer'
    resp = submit(env)
    assert resp.status_code == 200
    review = resp.get_json()['review']
    assert review['result']['verdict'] == 'pass' and review['result']['attribution'] == 'attributed'
    assert review['hold_reason'] == ''
    assert env.client.get(URL).get_json()['review'] == review
    assert env.store.find(env.candidate)['result']['report_path'] == 'docs/r.md'
    assert ('project', 7) in env.checked


def test_identical_replay_is_idempotent_and_conflict_is_409(env):
    first = submit(env).get_json()['review']['result']
    again = submit(env)
    assert again.status_code == 200 and again.get_json()['review']['result'] == first
    conflict = submit(env, verdict='changes_requested')
    assert conflict.status_code == 409
    assert env.store.find(env.candidate)['result'] == first


@pytest.mark.parametrize('author', ['hm_worker', 'earlier_worker'])
def test_worker_cannot_submit_for_its_own_candidate(env, author):
    env.caller['sid'] = author
    resp = submit(env)
    assert resp.status_code == 403 and 'self-review' in resp.get_json()['error']
    assert env.store.find(env.candidate)['result'] is None


def test_independent_but_undesignated_session_refused(env):
    env.caller['sid'] = 'other'
    resp = submit(env)
    assert resp.status_code == 403 and 'designated' in resp.get_json()['error']
    assert env.store.find(env.candidate)['result'] is None


@pytest.mark.parametrize('status', [ca.UNATTRIBUTED, 'error'])
def test_unattributed_caller_refused(env, status):
    env.caller['status'] = status
    assert submit(env).status_code == 403
    assert env.store.find(env.candidate)['result'] is None


@pytest.mark.parametrize('key', ['reviewer_session', 'reviewer', 'session_id', 'run_dir', 'candidate'])
def test_body_cannot_name_reviewer_or_candidate(env, key):
    env.caller['sid'] = 'hm_worker'
    resp = submit(env, **{key: 'reviewer'})
    assert resp.status_code == 400 and key in resp.get_json()['error']
    assert env.store.find(env.candidate)['result'] is None


@pytest.mark.parametrize('body', [
    {'surprise': 1}, {'merged_sha': ''}, {'merged_sha': 5}, {'verdict': 'approve'},
    {'verdict': None}, {'incoming_tip': 5}, {'project_generation': True},
    {'project_generation': '7'}])
def test_malformed_body_is_400(env, body):
    resp = submit(env, body)
    assert resp.status_code == 400
    assert env.store.find(env.candidate)['result'] is None


def test_missing_merged_sha_and_non_object_body_are_400(env):
    assert env.client.post(URL, json={'verdict': 'pass'}).status_code == 400
    assert env.client.post(URL, json=['pass']).status_code == 400
    assert env.client.post(URL, data='nope', content_type='text/plain').status_code == 400


def test_wrong_sha_or_tip_or_generation_claim_refuses_409(env):
    for override in ({'merged_sha': 'e' * 40}, {'incoming_tip': 'e' * 40},
                     {'project_generation': 8}):
        resp = submit(env, override)
        assert resp.status_code == 409, override
        assert 'do not match' in resp.get_json()['error']
    assert env.store.find(env.candidate)['result'] is None


def test_matching_optional_pins_accepted(env):
    assert submit(env, incoming_tip='b' * 40, project_generation=7).status_code == 200


def test_not_found_cases(env):
    assert env.client.get('/api/hivemind/hm_missing/review/ws_one/1').status_code == 404
    assert submit(env, url='/api/hivemind/hm_missing/review/ws_one/1').status_code == 404
    assert submit(env, url=f'/api/hivemind/{HM}/review/ws_none/1').status_code == 404
    # A newer integration attempt has no designation: an old pass cannot apply.
    assert submit(env).status_code == 200
    assert submit(env, url=f'/api/hivemind/{HM}/review/{WS}/2').status_code == 404
    env.projects.clear()
    assert env.client.get(URL).status_code == 404
    assert submit(env).status_code == 404


def test_hivemind_id_cannot_escape_the_hivemind_directory(env):
    assert routes._manifest('..') is None
    assert routes._manifest('../x') is None
    assert routes._manifest('') is None


def test_generation_mismatch_with_run_or_lifecycle_is_409(env):
    manifest = env.hm_dir / HM / 'manifest.json'
    manifest.write_text(json.dumps({'project_id': 'project', 'project_generation': 8}))
    assert env.client.get(URL).status_code == 409
    manifest.write_text(json.dumps({'project_id': 'project', 'project_generation': 7}))

    def stale(project_id, generation):
        raise RuntimeError('Project generation is stale or deleted')

    routes._assert_project_generation = stale
    resp = submit(env)
    assert resp.status_code == 409 and 'generation' in resp.get_json()['error']
    assert env.store.find(env.candidate)['result'] is None


def test_unreadable_record_holds_instead_of_404(env):
    (env.store.directory / ('0' * 64 + '.json')).write_text('{not json', encoding='utf-8')
    # The designated record is still trusted and reachable...
    assert submit(env).status_code == 200
    # ...but a candidate whose record cannot be trusted is a 409 hold.
    path = env.store.path(env.candidate)
    record = json.loads(path.read_text(encoding='utf-8'))
    record['reviewer_session'] = 'other'          # tamper: digest no longer matches
    path.write_text(json.dumps(record), encoding='utf-8')
    for resp in (env.client.get(URL), submit(env, verdict='changes_requested')):
        assert resp.status_code == 409 and 'unreadable' in resp.get_json()['error']


def test_two_candidates_for_one_attempt_need_a_sha_to_select(env, candidate):
    second = replace(candidate, merged_sha='e' * 40, merged_tree='f' * 40)
    env.store.designate(second, reviewer_session='reviewer',
                        reviewer_character='global:code-reviewer')
    assert env.client.get(URL).status_code == 409
    assert env.client.get(URL + '?merged_sha=' + 'e' * 40).status_code == 200
    assert submit(env, merged_sha='e' * 40, verdict='changes_requested').status_code == 200
    assert env.store.find(candidate)['result'] is None
    assert env.store.find(second)['result']['verdict'] == 'changes_requested'
    assert 'held' in env.client.get(URL + '?merged_sha=' + 'e' * 40).get_json()['review']['hold_reason']


def test_verdict_guard_runs_around_record_and_its_refusal_blocks_it(env):
    seen = []

    @contextmanager
    def guard(candidate):
        seen.append(('enter', candidate.key))
        yield
        seen.append(('exit', candidate.key))

    routes._verdict_guard = guard
    assert submit(env).status_code == 200
    assert seen == [('enter', env.candidate.key), ('exit', env.candidate.key)]


def test_verdict_guard_refusal_keeps_status_and_records_nothing(env):
    @contextmanager
    def guard(candidate):
        raise ReviewRefused(409, 'reviewer deadline expired; integration held')
        yield

    routes._verdict_guard = guard
    resp = submit(env)
    assert resp.status_code == 409 and 'deadline' in resp.get_json()['error']
    assert env.store.find(env.candidate)['result'] is None


def test_unexpected_failure_is_500_without_detail_and_records_nothing(env):
    @contextmanager
    def guard(candidate):
        raise RuntimeError('secret internal path C:\\x')
        yield

    routes._verdict_guard = guard
    resp = submit(env)
    assert resp.status_code == 500 and 'secret' not in resp.get_data(as_text=True)
    assert env.store.find(env.candidate)['result'] is None


def test_consumed_pass_still_refuses_conflicting_replay(env):
    assert submit(env).status_code == 200
    assert env.store.consume_pass(env.candidate) is True
    assert submit(env, verdict='changes_requested').status_code == 409
    assert submit(env).status_code == 200
    assert env.store.find(env.candidate)['consumed_at']


def test_verdict_path_is_the_registered_post_route(env):
    path = routes.verdict_path(env.candidate)
    assert path == URL
    assert submit(env, url=path).status_code == 200


def test_wire_requires_the_lifecycle_callables(tmp_path):
    with pytest.raises(TypeError):
        routes.wire(hivemind_dir=tmp_path, load_project_fn=lambda _: None,
                    assert_project_generation_fn=lambda *_: None)


@pytest.mark.parametrize('veto', ['cancelled', 'timed_out', 'held'])
def test_lifecycle_veto_blocks_post_and_never_renders_a_result(env, veto):
    assert submit(env).status_code == 200
    env.life['veto'] = veto
    review = env.client.get(URL).get_json()['review']
    assert review['result'] is None and veto in review['hold_reason']
    assert review['lifecycle']['state'] == veto and review['lifecycle']['can_record'] is False
    resp = submit(env)
    assert resp.status_code == 409 and veto in resp.get_json()['error']


def test_post_response_is_green_when_lifecycle_agrees(env):
    resp = env.client.post(URL, json={'verdict': 'pass', 'merged_sha': 'c' * 40})
    assert resp.get_json()['review']['hold_reason'] == ''
    assert resp.get_json()['review']['result']['verdict'] == 'pass'


@pytest.mark.parametrize('failure', [RuntimeError('boom'), ReviewRefused(409, 'lifecycle disagrees')])
def test_status_failure_is_a_hold_not_a_pass(env, failure):
    assert submit(env).status_code == 200
    env.life['raise'] = failure
    review = env.client.get(URL).get_json()['review']
    assert review['result'] is None and review['hold_reason'] and review['lifecycle'] is None
    assert 'boom' not in json.dumps(review)


def test_runtime_sidecar_is_not_a_review_record_but_damaged_records_still_hold(env):
    sidecar = env.store.directory / (env.candidate.key + '.runtime.json')
    sidecar.write_text('{"phase": "pending"}', encoding='utf-8')
    assert env.client.get(URL).status_code == 200
    # Counted as untrusted, it would turn this 404 into a 409 hold.
    assert env.client.get(f'/api/hivemind/{HM}/review/ws_none/1').status_code == 404
    assert submit(env).status_code == 200
    # A damaged file that merely resembles a sidecar is still untrusted.
    (env.store.directory / (env.candidate.key + '.runtime.json.bak.json')).write_text('{x', encoding='utf-8')
    path = env.store.path(env.candidate)
    record = json.loads(path.read_text(encoding='utf-8'))
    record['designated_at'] = 'tampered'
    path.write_text(json.dumps(record), encoding='utf-8')
    assert env.client.get(URL).status_code == 409


def test_lazy_lifecycle_fails_closed_without_the_runtime_module(env, monkeypatch):
    guard, status = routes.lifecycle_for(SimpleNamespace(name='api'))
    routes._verdict_guard, routes._reviewer_status = guard, status
    monkeypatch.setitem(sys.modules, 'mc.hivemind_reviewer', None)
    resp = submit(env)
    assert resp.status_code == 409 and 'lifecycle unavailable' in resp.get_json()['error']
    assert env.store.find(env.candidate)['result'] is None
    review = env.client.get(URL).get_json()['review']
    assert review['result'] is None and 'unavailable' in review['hold_reason']


def test_lazy_lifecycle_delegates_to_the_runtime_with_the_api_module(env, monkeypatch):
    api = SimpleNamespace(name='hivemind_routes')
    calls = []

    @contextmanager
    def verdict_guard(got_api, candidate):
        calls.append(('guard', got_api, candidate.key))
        yield {}

    def reviewer_status(got_api, candidate):
        calls.append(('status', got_api, candidate.key))
        return {'state': 'pass', 'hold_reason': '', 'result': env.store.find(candidate)['result'],
                'can_record': True}

    monkeypatch.setitem(sys.modules, 'mc.hivemind_reviewer', SimpleNamespace(
        verdict_guard=verdict_guard, reviewer_status=reviewer_status))
    routes._verdict_guard, routes._reviewer_status = routes.lifecycle_for(api)
    assert submit(env).status_code == 200
    kinds = [c[0] for c in calls]
    assert kinds[0] == 'guard' and 'status' in kinds
    assert all(c[1] is api and c[2] == env.candidate.key for c in calls)
