"""Article chat: real store/routes, stub only the toolless model seam."""
import json
from types import SimpleNamespace

import pytest
from flask import Flask

from mc import desk, desk_article_chat as chat, desk_story, desk_studio_articles as arts
from mc.blueprints import desk_routes, desk_studio_articles_routes, desk_article_chat_routes

BASE = '/api/desk/studio/articles/artd-one'
TABS = [{'id': 'draft', 'label': 'Draft', 'body': 'Original [[claim|fact]]'},
        {'id': 'other', 'label': 'Other', 'body': 'Leave this version alone.'}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    projects = {'alpha': {'id': 'alpha', 'project_path': str(tmp_path)}}
    desk_routes.wire(load_projects_fn=lambda: list(projects.values()),
                     load_project_fn=projects.get, store_path=tmp_path / 'desk.json',
                     signals_path=tmp_path / 'signals.jsonl', uploads_root=tmp_path)
    app = Flask(__name__)
    for bp in (desk_routes.bp, desk_studio_articles_routes.bp, desk_article_chat_routes.bp):
        app.register_blueprint(bp)
    client = app.test_client()
    assert client.put(BASE, json={'rev': 0, 'topic': 'A topic', 'tabs': TABS}).status_code == 200
    answer = {'text': json.dumps({'reply': 'Here is a proposed rewrite.', 'proposal': {'body': 'Proposed text'}})}
    calls = []

    def fake(provider, **kw):
        calls.append(dict(kw, provider=provider))
        if callable(answer['text']):
            return answer['text']()
        if isinstance(answer['text'], Exception):
            raise answer['text']
        return answer['text']

    monkeypatch.setattr(desk_story, '_call_model', fake)
    monkeypatch.setattr(desk_story, '_resolve_engine', lambda p, c: SimpleNamespace(provider='codex', model='chosen-model'))
    return client, answer, calls, projects


def say(client, **kw):
    return client.post(BASE + '/chat', json=dict(message='rewrite', tab_id='draft', body=TABS[0]['body'], **kw))


def apply(client, turn, action='accept', rev=None):
    if rev is None:
        rev = client.get(BASE).json['rev']
    return client.post(BASE + '/chat/' + turn['id'], json={'action': action, 'rev': rev})


def test_context_current_unsaved_text_and_proposal_never_writes(env):
    c, _, calls, _ = env
    sent = 'Unsaved current text\n[[image:path|Caption]]'
    res = c.post(BASE + '/chat', json={'message': 'rewrite', 'tab_id': 'draft', 'body': sent, 'agent': 'ignored'})
    assert res.status_code == 200
    assert res.json['agent'] == {'ref': None, 'name': 'Studio agent'}
    payload = json.loads(calls[0]['stdin_text'])
    assert payload['tab']['body'] == sent and payload['topic'] == 'A topic'
    assert sent not in calls[0]['prompt']
    assert calls[0]['provider'] == 'codex' and calls[0]['model'] == 'chosen-model'
    assert c.get(BASE).json['tabs'] == TABS and c.get(BASE).json['rev'] == 1
    assert c.get(BASE + '/chat').json['thread'] == res.json['turns']
    # Unsaved text is the proposal's base, so it cannot overwrite the saved older version.
    assert apply(c, res.json['turns'][1]).status_code == 409


def test_accept_and_undo_preserve_other_tab_and_status(env):
    c, _, _, _ = env
    turn = say(c).json['turns'][1]
    accepted = apply(c, turn)
    assert accepted.status_code == 200
    art = accepted.json['article']
    assert art['rev'] == 2 and art['tabs'][0]['body'] == 'Proposed text'
    assert art['tabs'][1] == TABS[1]
    assert accepted.json['turn']['proposal']['status'] == 'applied'
    assert apply(c, turn).status_code == 409
    undo = apply(c, turn, 'undo')
    assert undo.status_code == 200 and undo.json['article']['tabs'] == TABS
    assert undo.json['article']['rev'] == 3
    assert undo.json['turn']['proposal']['status'] == 'undone'
    assert apply(c, turn, 'undo').status_code == 409


@pytest.mark.parametrize('when', ['accept', 'undo'])
def test_changed_draft_and_stale_rev_refuse_without_writes(env, when):
    c, _, _, _ = env
    turn = say(c).json['turns'][1]
    if when == 'undo':
        assert apply(c, turn).status_code == 200
    assert apply(c, turn, when, rev=0).status_code == 409
    art = c.get(BASE).json
    art['tabs'][0]['body'] = 'New manual edits'
    assert c.put(BASE, json=art).status_code == 200
    assert apply(c, turn, when).status_code == 409
    assert c.get(BASE).json['tabs'][0]['body'] == 'New manual edits'


def test_chat_survives_put_trash_restore_and_does_not_cross_drafts(env):
    c, _, _, _ = env
    turns = say(c).json['turns']
    assert c.put(BASE, json=c.get(BASE).json).status_code == 200
    token = c.delete(BASE).json['token']
    assert c.get(BASE + '/chat').status_code == 404
    assert c.post('/api/desk/studio/articles/trash/' + token + '/restore').status_code == 200
    assert c.get(BASE + '/chat').json['thread'] == turns
    assert c.put(BASE + 'two', json={'rev': 0, 'topic': 'Same title'}).status_code == 200
    assert c.get(BASE + 'two/chat').json['thread'] == []


def test_project_planner_is_server_resolved_not_client_or_campaign(env, monkeypatch):
    c, _, calls, _ = env
    art = c.get(BASE).json
    art['project_id'] = 'alpha'
    c.put(BASE, json=art)
    assert say(c).status_code == 409 and not calls
    desk.upsert_presence('alpha', {'desk_agent': 'project:planner'})
    seen = []
    monkeypatch.setattr(desk_story, '_agent', lambda ref, p: seen.append((ref, p)) or
                        {'name': 'planner', 'agent_name': 'Planner', 'body': 'A character', 'engine': {'effort': 'high'}})
    res = say(c)
    assert res.status_code == 200 and seen[0][0] == 'project:planner'
    assert seen[0][1]['id'] == 'alpha'
    assert res.json['agent']['name'] == 'Planner'
    assert calls[0]['effort'] == 'high' and 'A character' in calls[0]['system_prompt']


def test_deleted_project_and_unknown_agent_fail_before_model(env, monkeypatch):
    c, _, calls, projects = env
    art = c.get(BASE).json
    art['project_id'] = 'alpha'
    c.put(BASE, json=art)
    desk.upsert_presence('alpha', {'desk_agent': 'global:gone'})
    monkeypatch.setattr(desk_story._chars, 'read_character', lambda *a, **kw: None)
    assert say(c).status_code == 404 and not calls
    del projects['alpha']
    assert say(c).status_code == 404 and not calls


@pytest.mark.parametrize('patch', [{'message': ''}, {'message': 'x' * 2001}, {'body': None},
                                  {'body': 'x' * 100001}, {'tab_id': 'missing'}])
def test_invalid_context_refused_without_model(env, patch):
    c, _, calls, _ = env
    body = {'message': 'rewrite', 'tab_id': 'draft', 'body': 'text', **patch}
    assert c.post(BASE + '/chat', json=body).status_code in (400, 409)
    assert not calls


@pytest.mark.parametrize('raw,status', [('Good opening.', 200), ('{"broken', 502),
                                       ('', 502), (RuntimeError('model offline'), 502)])
def test_plain_prose_and_model_failures(env, raw, status):
    c, answer, _, _ = env
    answer['text'] = raw
    res = say(c)
    assert res.status_code == status
    assert c.get(BASE).json['tabs'] == TABS
    if status == 200:
        assert res.json['turns'][1]['proposal'] is None
    else:
        assert c.get(BASE + '/chat').json['thread'] == []


@pytest.mark.parametrize('proposal', [{'body': ''}, {'body': 42}, {'body': 'x' * 100001}, 'oops'])
def test_invalid_proposal_keeps_reply_and_cannot_apply(env, proposal):
    c, answer, _, _ = env
    answer['text'] = json.dumps({'reply': 'A useful reply.', 'proposal': proposal})
    res = say(c)
    turn = res.json['turns'][1]
    assert turn['proposal'] is None and turn['proposal_error']
    assert apply(c, turn).status_code == 404


def test_thread_budget_and_conversation_context(env, monkeypatch):
    c, _, calls, _ = env
    monkeypatch.setattr(chat, 'MAX_TURNS', 4)
    for _ in range(4):
        assert say(c).status_code == 200
    assert len(c.get(BASE + '/chat').json['thread']) == 4
    assert len(json.loads(calls[-1]['stdin_text'])['conversation']) == 4
    assert c.get(BASE).json['rev'] == 1


def test_followup_sees_previous_proposal_and_its_acceptance_status(env):
    c, _, calls, _ = env
    turn = say(c).json['turns'][1]
    say(c)
    proposal = json.loads(calls[-1]['stdin_text'])['latest_proposal']
    assert proposal == {'tab_id': 'draft', 'body': 'Proposed text', 'status': 'proposed'}
    # Apply the most recent proposal, then talk about it with the new draft text.
    newest = c.get(BASE + '/chat').json['thread'][-1]
    assert apply(c, newest).status_code == 200
    c.post(BASE + '/chat', json={'message': 'why?', 'tab_id': 'draft', 'body': 'Proposed text'})
    assert json.loads(calls[-1]['stdin_text'])['latest_proposal']['status'] == 'applied'


def test_deleted_draft_while_model_works_is_not_recreated(env):
    c, answer, _, _ = env
    def remove():
        arts.delete_article('artd-one')
        return '{"reply":"Here it is", "proposal":null}'
    answer['text'] = remove
    assert say(c).status_code == 404
    assert c.get(BASE).status_code == 404
