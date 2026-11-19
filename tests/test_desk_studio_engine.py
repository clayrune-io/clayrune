"""A Studio item's own engine pick (`mc/desk_studio_engine.py`, Ron 2026-11-18).

Pinned:

  * every Studio model call (storyboard generate, storyboard chat, article chat) runs on
    the item's engine pick: a standalone item on its own (`engine`), an item that belongs
    to a campaign on the CAMPAIGN's pick, even when the campaign has none (the item's own
    pick is not consulted, so the page can show what runs);
  * with no pick anywhere, the call is exactly what it was: the agent's own engine and effort;
  * a pick the live catalog does not offer is a 400 and nothing is saved or called; an
    article's pick is saved and read back, `null` clears it, and one equal to the stored
    pick is not re-checked (a catalog that moved on must not make a draft unsaveable);
  * a pick that cannot run (signed out, out of allowance, gone from the catalog) is a 409
    with the reason and the model is never called: no fallback to the agent's default;
  * an effort-only pick is checked against the item's agent's provider.
"""
import json
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk as _desk  # noqa: E402
from mc import desk_accounts as _accounts  # noqa: E402
from mc import desk_campaign_engine as eng  # noqa: E402
from mc import desk_pieces as _pieces  # noqa: E402
from mc import desk_story  # noqa: E402
from mc.blueprints import (desk_article_chat_routes, desk_routes,  # noqa: E402
                           desk_studio_articles_routes)

CID = 'camp-s'
ART = '/api/desk/studio/articles/artd-one'
DAVE = {'name': 'dave', 'agent_name': 'Dave', 'body': 'You plan campaigns plainly.',
        'engine': {'provider': 'claude', 'model': 'claude-sonnet-5-5', 'effort': 'medium'}}
CAT = {
    'claude': {'display_name': 'Claude Code', 'model_selection': True,
               'models': ['claude-fable-5-2', 'claude-sonnet-5-5'], 'efforts': ['low', 'medium', 'high']},
    'codex': {'display_name': 'Codex', 'model_selection': True,
              'models': ['gpt-6.2-sol'], 'efforts': ['low', 'high']},
}
CODEX = {'provider': 'codex', 'model': 'gpt-6.2-sol', 'effort': 'high'}
SCENES = [{'label': 'Open', 'line': 'A desk.', 'duration_sec': 4}]
BOARD = json.dumps({'scenes': [{'label': 'A', 'line': 'one', 'duration_sec': 4}]})
CHAT = json.dumps({'reply': 'ok', 'suggest': None})
ARTICLE = json.dumps({'reply': 'ok', 'proposal': None})


@pytest.fixture
def env(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.config['TESTING'] = True
    projects = {'alpha': {'id': 'alpha', 'name': 'Alpha', 'project_path': str(tmp_path)}}
    desk_routes.wire(load_projects_fn=lambda: list(projects.values()), load_project_fn=projects.get,
                     store_path=tmp_path / 'desk.json', signals_path=tmp_path / 'desk_signals.jsonl',
                     uploads_root=tmp_path)
    for bp in (desk_routes.bp, desk_studio_articles_routes.bp, desk_article_chat_routes.bp):
        app.register_blueprint(bp)
    monkeypatch.setattr(eng, 'catalog', lambda: CAT)
    state = {'unavailable': '', 'answer': BOARD}
    monkeypatch.setattr(eng, 'availability_error', lambda provider, model='': state['unavailable'])
    calls = []

    def fake_model(provider, **kw):
        calls.append(dict(kw, provider=provider))
        return state['answer']

    monkeypatch.setattr(desk_story, '_call_model', fake_model)
    monkeypatch.setattr(desk_story._chars, 'read_character', lambda _s, name, **_k: DAVE if name == 'dave' else None)
    c = app.test_client()
    _accounts.create_account('x', '@clayrune', account_id='ch-x')
    r = c.post('/api/desk/campaigns?shape=v1', json={
        'id': CID, 'state': 'draft', 'projectId': 'alpha', 'rules': {}, 'map': {'stop': 'what', 'done': []},
        'plan': {'brief': 'Announce', 'title': 'Restore', 'accounts': ['ch-x'], 'cadence': {'per_week': 3},
                 'end': {'post_cap': 9}}})
    assert r.status_code == 201, r.get_json()
    _desk.upsert_presence('alpha', {'desk_agent': 'global:dave'})
    return c, calls, state


def _campaign_pick(client, pick):
    r = client.patch(f'/api/desk/campaigns/{CID}?shape=v1', json={'how': {'engine': pick}})
    assert r.status_code == 200, r.get_json()


def _piece_owner(client):
    piece = _pieces.create_piece(CID, 'video', 'A video')
    r = client.put(f'/api/desk/pieces/{piece["id"]}/storyboard', json={'rev': 0, 'scenes': [], 'story': 'A story.'})
    assert r.status_code == 200, r.get_json()
    return {'kind': 'piece', 'id': piece['id']}


def _standalone_owner(client, item='item1'):
    r = client.put(f'/api/desk/studio/{item}/storyboard', json={'rev': 0, 'scenes': [], 'story': 'A story.'})
    assert r.status_code == 200, r.get_json()
    return {'kind': 'studio', 'id': item}


def _gen(client, **body):
    return client.post('/api/desk/storyboard/generate', json=dict({'mode': 'board', 'story': 'A story.'}, **body))


def _chat(client, owner, **body):
    return client.post('/api/desk/storyboard/chat', json=dict(
        {'owner': owner, 'message': 'hi', 'story': 'A story.', 'scenes': SCENES, 'scene': None}, **body))


def _ran_on(call):
    return call['provider'], call['model'], call['effort']


DEFAULT = ('claude', 'claude-sonnet-5-5', 'medium')


# -- storyboard generate ----------------------------------------------------------------

def test_generate_with_no_pick_runs_on_the_agents_own_engine(env):
    client, calls, _ = env
    assert _gen(client, agent='global:dave', project_id='alpha').status_code == 200
    assert _ran_on(calls[0]) == DEFAULT


def test_generate_for_a_standalone_item_runs_on_its_own_pick(env):
    client, calls, _ = env
    r = _gen(client, agent='global:dave', project_id='alpha', owner={'kind': 'studio', 'id': 'item1'}, engine=CODEX)
    assert r.status_code == 200, r.get_json()
    assert _ran_on(calls[0]) == ('codex', 'gpt-6.2-sol', 'high')


def test_generate_for_a_piece_runs_on_its_campaigns_pick_not_the_items(env):
    client, calls, _ = env
    _campaign_pick(client, {'provider': 'codex', 'model': 'gpt-6.2-sol', 'effort': 'low'})
    owner = _piece_owner(client)
    r = _gen(client, agent='global:dave', project_id='alpha', owner=owner,
             engine={'provider': 'claude', 'model': 'claude-fable-5-2'})
    assert r.status_code == 200, r.get_json()
    assert _ran_on(calls[0]) == ('codex', 'gpt-6.2-sol', 'low')


def test_a_piece_whose_campaign_has_no_pick_ignores_the_items_own(env):
    client, calls, _ = env
    owner = _piece_owner(client)
    assert _gen(client, agent='global:dave', project_id='alpha', owner=owner, engine=CODEX).status_code == 200
    assert _ran_on(calls[0]) == DEFAULT


def test_generate_refuses_a_pick_the_catalog_does_not_offer_and_calls_no_model(env):
    client, calls, _ = env
    r = _gen(client, agent='global:dave', project_id='alpha', engine={'provider': 'claude', 'model': 'gpt-6.2-sol'})
    assert r.status_code == 400 and 'does not offer a model' in r.get_json()['error']
    assert calls == []


def test_generate_with_a_pick_that_cannot_run_is_a_409_and_calls_no_model(env):
    client, calls, state = env
    state['unavailable'] = 'codex is not signed in (not_logged_in)'
    r = _gen(client, agent='global:dave', project_id='alpha', engine=CODEX)
    assert r.status_code == 409
    msg = r.get_json()['error']
    assert 'codex is not signed in' in msg and 'Nothing was run on another engine' in msg and 'this item' in msg
    assert calls == []


def test_generate_for_a_piece_names_the_campaign_when_its_pick_cannot_run(env):
    client, calls, state = env
    _campaign_pick(client, CODEX)
    owner = _piece_owner(client)
    state['unavailable'] = 'codex allowance is exhausted until 3pm'
    r = _gen(client, agent='global:dave', project_id='alpha', owner=owner)
    assert r.status_code == 409 and 'this campaign is set to run on codex' in r.get_json()['error']
    assert calls == []


def test_an_effort_only_pick_is_checked_against_the_agents_provider(env):
    client, calls, _ = env
    assert _gen(client, agent='global:dave', project_id='alpha', engine={'effort': 'high'}).status_code == 200
    assert _ran_on(calls[0]) == ('claude', 'claude-sonnet-5-5', 'high')
    r = _gen(client, agent='global:dave', project_id='alpha', engine={'effort': 'xhigh'})
    assert r.status_code == 400 and 'does not offer effort' in r.get_json()['error']
    assert len(calls) == 1


def test_an_owner_that_is_not_kind_and_id_is_a_400(env):
    client, calls, _ = env
    assert _gen(client, owner='piece-1').status_code == 400
    assert calls == []


# -- storyboard chat --------------------------------------------------------------------

def test_chat_for_a_standalone_item_runs_on_its_own_pick(env):
    client, calls, state = env
    state['answer'] = CHAT
    owner = _standalone_owner(client)
    r = _chat(client, owner, agent='global:dave', project_id='alpha', engine=CODEX)
    assert r.status_code == 200, r.get_json()
    assert _ran_on(calls[0]) == ('codex', 'gpt-6.2-sol', 'high')


def test_chat_for_a_piece_runs_on_its_campaigns_pick_not_the_items(env):
    client, calls, state = env
    state['answer'] = CHAT
    _campaign_pick(client, CODEX)
    owner = _piece_owner(client)
    r = _chat(client, owner, agent='global:dave', project_id='alpha', engine={'effort': 'low'})
    assert r.status_code == 200, r.get_json()
    assert _ran_on(calls[0]) == ('codex', 'gpt-6.2-sol', 'high')


def test_chat_with_a_pick_that_cannot_run_is_a_409_and_saves_no_turn(env):
    client, calls, state = env
    owner = _standalone_owner(client)
    state['unavailable'] = 'codex is not signed in (not_logged_in)'
    r = _chat(client, owner, agent='global:dave', project_id='alpha', engine=CODEX)
    assert r.status_code == 409 and calls == []
    assert client.get(f'/api/desk/studio/{owner["id"]}/storyboard').get_json().get('thread') in (None, [])


# -- the article ------------------------------------------------------------------------

def _article(client, **extra):
    r = client.put(ART, json=dict({'rev': 0, 'topic': 'A topic', 'project_id': 'alpha'}, **extra))
    return r


def _say(client):
    return client.post(ART + '/chat', json={'message': 'rewrite', 'tab_id': 'tab-draft', 'body': 'Draft text'})


def test_an_article_pick_is_saved_read_back_and_cleared_by_null(env):
    client, _, _ = env
    r = _article(client, engine=CODEX)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['engine'] == CODEX
    assert client.get(ART).get_json()['engine'] == CODEX
    rev = client.get(ART).get_json()['rev']
    r = client.put(ART, json={'rev': rev, 'topic': 'A topic', 'engine': None})
    assert r.status_code == 200 and r.get_json()['engine'] is None


def test_a_save_that_omits_the_engine_keeps_the_pick(env):
    client, _, _ = env
    _article(client, engine=CODEX)
    rev = client.get(ART).get_json()['rev']
    r = client.put(ART, json={'rev': rev, 'topic': 'Renamed'})
    assert r.status_code == 200 and r.get_json()['engine'] == CODEX


def test_an_article_pick_the_catalog_does_not_offer_is_a_400_and_nothing_is_saved(env):
    client, _, _ = env
    assert _article(client).status_code == 200
    rev = client.get(ART).get_json()['rev']
    r = client.put(ART, json={'rev': rev, 'topic': 'A topic', 'engine': {'provider': 'nope', 'model': 'x'}})
    assert r.status_code == 400 and 'unknown provider' in r.get_json()['error']
    assert client.get(ART).get_json()['engine'] is None and client.get(ART).get_json()['rev'] == rev


def test_an_effort_only_article_pick_is_checked_against_the_project_agents_provider(env):
    client, _, _ = env
    assert _article(client, engine={'effort': 'high'}).status_code == 200
    rev = client.get(ART).get_json()['rev']
    r = client.put(ART, json={'rev': rev, 'topic': 'A topic', 'engine': {'effort': 'xhigh'}})
    assert r.status_code == 400 and 'does not offer effort' in r.get_json()['error']


def test_a_stored_article_pick_the_catalog_dropped_does_not_block_a_keystroke_save(env, monkeypatch):
    client, _, _ = env
    _article(client, engine=CODEX)
    monkeypatch.setattr(eng, 'catalog', lambda: {'claude': CAT['claude']})   # codex gone
    rev = client.get(ART).get_json()['rev']
    r = client.put(ART, json={'rev': rev, 'topic': 'A topic', 'engine': CODEX,
                              'tabs': [{'id': 'tab-draft', 'label': 'Draft', 'body': 'typing'}]})
    assert r.status_code == 200 and r.get_json()['engine'] == CODEX


def test_article_chat_with_no_pick_runs_on_the_agents_own_engine(env):
    client, calls, state = env
    state['answer'] = ARTICLE
    _article(client)
    assert _say(client).status_code == 200
    assert _ran_on(calls[0]) == DEFAULT


def test_article_chat_runs_on_the_articles_own_pick(env):
    client, calls, state = env
    state['answer'] = ARTICLE
    _article(client, engine=CODEX)
    assert _say(client).status_code == 200
    assert _ran_on(calls[0]) == ('codex', 'gpt-6.2-sol', 'high')


def test_article_chat_for_a_campaign_runs_on_the_campaigns_pick_not_its_own(env):
    client, calls, state = env
    state['answer'] = ARTICLE
    _article(client, engine={'provider': 'claude', 'model': 'claude-fable-5-2'})
    _campaign_pick(client, CODEX)
    rev = client.get(ART).get_json()['rev']
    assert client.put(ART, json={'rev': rev, 'topic': 'A topic', 'campaign_id': CID}).status_code == 200
    assert _say(client).status_code == 200
    assert _ran_on(calls[0]) == ('codex', 'gpt-6.2-sol', 'high')


def test_article_chat_for_a_campaign_with_no_pick_ignores_the_articles_own(env):
    client, calls, state = env
    state['answer'] = ARTICLE
    _article(client, engine=CODEX, campaign_id=CID)
    assert _say(client).status_code == 200
    assert _ran_on(calls[0]) == DEFAULT


def test_article_chat_with_a_pick_that_cannot_run_is_a_409_and_calls_no_model(env, monkeypatch):
    client, calls, state = env
    state['answer'] = ARTICLE
    _article(client, engine=CODEX)
    state['unavailable'] = 'codex is not signed in (not_logged_in)'
    r = _say(client)
    assert r.status_code == 409 and 'Nothing was run on another engine' in r.get_json()['error']
    assert calls == []
    monkeypatch.setattr(eng, 'catalog', lambda: {'claude': CAT['claude']})
    state['unavailable'] = ''
    r = _say(client)
    assert r.status_code == 409 and 'no longer holds' in r.get_json()['error']
    assert calls == []
