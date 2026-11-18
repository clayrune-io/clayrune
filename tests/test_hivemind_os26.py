"""MC-1179 OS26: what a person sends into a hivemind is delivered, and a
worker's blocker reaches a person.

No hivemind, worker or orchestrator is started: the manifest and bus are built
on disk (the `client` fixture of test_hivemind_routes), the orchestrator's
runtime dispatch is replaced by a recorder, and the push queue by a list.
"""
import json

import pytest

from mc import hivemind_user_directives as directives
from mc.blueprints import push_mobile
from tests.test_hivemind_routes import HUMAN, _create, client  # noqa: F401


def _orchestrator_prompt(client, monkeypatch, hm_id):
    """The task text the orchestrator would be dispatched with."""
    seen = []
    monkeypatch.setattr(client.hm, '_hm_runtime_dispatch', lambda **kw: seen.append(kw))
    assert client.hm._hm_dispatch_orchestrator(hm_id, 'synthesize')
    for t in list(type(client.hm.threading.Thread(target=None)).started):
        t.target()
    assert len(seen) == 1
    return seen[0]['task']


# ── (a) a directive reaches the orchestrator's next prompt ───────────────────

def test_a_directive_to_the_orchestrator_is_in_its_next_prompt(client, monkeypatch):
    hm_id = _create(client)['hivemind']['id']
    r = client.post(f'/api/hivemind/{hm_id}/intervene',
                    json={'message': 'Skip the billing module entirely'}, headers=HUMAN)
    assert r.status_code == 200
    prompt = _orchestrator_prompt(client, monkeypatch, hm_id)
    assert 'DIRECTIVES FROM THE PERSON RUNNING THIS HIVEMIND' in prompt
    assert 'Skip the billing module entirely' in prompt


def test_no_directive_adds_no_block(client, monkeypatch):
    hm_id = _create(client)['hivemind']['id']
    assert 'DIRECTIVES FROM THE PERSON' not in _orchestrator_prompt(client, monkeypatch, hm_id)


def test_only_a_persons_words_to_the_orchestrator_count(client):
    hm_id = _create(client)['hivemind']['id']
    hm = client.hm
    base = {'timestamp': '2026-11-17T10:00:00Z', 'type': 'directive'}
    hm._hm_append_bus_message(hm_id, dict(base, id='1', **{'from': 'ws_001', 'to': 'orchestrator'}, content='from a worker'))
    hm._hm_append_bus_message(hm_id, dict(base, id='2', **{'from': 'user', 'to': 'ws_001'}, content='for a worker'))
    hm._hm_append_bus_message(hm_id, dict(base, id='3', **{'from': 'user', 'to': 'orchestrator'}, content='mine'))
    assert [m['content'] for m in directives.directives(hm, hm_id)] == ['mine']


def test_the_prompt_keeps_the_last_directives_only(client):
    hm_id = _create(client)['hivemind']['id']
    for i in range(directives.LIMIT + 3):
        client.post(f'/api/hivemind/{hm_id}/intervene', json={'message': f'note {i}'}, headers=HUMAN)
    got = [m['content'] for m in directives.directives(client.hm, hm_id)]
    assert got == [f'note {i}' for i in range(3, directives.LIMIT + 3)]


# ── (b)/(c) an answer reaches the worker that asked; the worker is told to poll

def test_an_answer_addressed_to_the_asking_workstream_is_what_its_poll_returns(client):
    hm_id = _create(client)['hivemind']['id']
    client.post(f'/api/hivemind/{hm_id}/workstreams/ws_001/handoff',
                json={'open_questions': ['Which database?']})
    q = json.loads((client.hm_dir / hm_id / 'knowledge' / 'open_questions.jsonl')
                   .read_text(encoding='utf-8').strip())
    assert q['workstream'] == 'ws_001'          # the dashboard sends the answer to this id
    client.post(f'/api/hivemind/{hm_id}/intervene',
                json={'message': 'Postgres', 'target': q['workstream']}, headers=HUMAN)
    got = client.get(f'/api/hivemind/{hm_id}/bus/poll/ws_001').get_json()
    assert [m['content'] for m in got] == ['Postgres']


def test_the_worker_capability_list_names_the_poll_call(client):
    from mc import hivemind_worker
    hm_id = _create(client)['hivemind']['id']
    ctx = hivemind_worker.build_worker_context(client.hm, hm_id, 'ws_001')
    assert f'/api/hivemind/{hm_id}/bus/poll/ws_001' in ctx


# ── (d) "Synthesize now" starts a synthesis ──────────────────────────────────

def test_synthesize_dispatches_the_orchestrator_synthesis(client, monkeypatch):
    hm_id = _create(client)['hivemind']['id']
    calls = []
    monkeypatch.setattr(client.hm, '_hm_dispatch_orchestrator', lambda *a: calls.append(a) or 'hm_orch_x')
    r = client.post(f'/api/hivemind/{hm_id}/synthesize', headers=HUMAN)
    assert r.status_code == 200 and r.get_json()['started'] is True
    assert calls == [(hm_id, 'synthesize')]


def test_synthesize_is_409_while_the_orchestrator_is_busy_and_404_for_no_run(client, monkeypatch):
    hm_id = _create(client)['hivemind']['id']
    monkeypatch.setattr(client.hm, '_hm_dispatch_orchestrator', lambda *a: None)
    assert client.post(f'/api/hivemind/{hm_id}/synthesize', headers=HUMAN).status_code == 409
    assert client.post('/api/hivemind/hm_nope/synthesize', headers=HUMAN).status_code == 404


def test_synthesize_is_human_only(client, monkeypatch):
    hm_id = _create(client)['hivemind']['id']
    calls = []
    monkeypatch.setattr(client.hm, '_hm_dispatch_orchestrator', lambda *a: calls.append(a))
    assert client.post(f'/api/hivemind/{hm_id}/synthesize').status_code == 403   # an agent's curl: no Origin
    assert calls == []


# ── (e) a worker's escalation raises a push ──────────────────────────────────

@pytest.fixture()
def pushes(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(push_mobile, 'PUSH_VAPID_PATH', tmp_path / 'push_vapid.json')
    monkeypatch.setattr(push_mobile, '_queue_push', lambda title, body, **kw: sent.append((title, body, kw)))
    return sent


def test_an_escalation_raises_a_push(client, pushes):
    hm_id = _create(client)['hivemind']['id']
    r = client.post(f'/api/hivemind/{hm_id}/escalate', json={'from': 'ws_001', 'content': 'Blocked on creds'})
    assert r.status_code == 200
    assert len(pushes) == 1
    title, body, kw = pushes[0]
    assert 'Hivemind needs you' in title and 'ws_001' in body and 'Blocked on creds' in body
    assert kw['project_id'] == 'thm'


def test_a_project_with_push_off_gets_none(client, pushes):
    hm_id = _create(client)['hivemind']['id']
    client.projects['thm']['notify_push_enabled'] = False
    assert client.post(f'/api/hivemind/{hm_id}/escalate',
                       json={'from': 'ws_001', 'content': 'x'}).status_code == 200
    assert pushes == []


def test_a_push_that_fails_does_not_fail_the_escalation(client, pushes, monkeypatch):
    hm_id = _create(client)['hivemind']['id']

    def boom(*a, **k):
        raise RuntimeError('no subscribers table')
    monkeypatch.setattr(push_mobile, '_queue_push', boom)
    r = client.post(f'/api/hivemind/{hm_id}/escalate', json={'from': 'ws_001', 'content': 'x'})
    assert r.status_code == 200 and r.get_json()['ok'] is True
