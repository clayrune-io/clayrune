"""A campaign's own engine pick (`mc/desk_campaign_engine.py`, Ron 2026-11-18).

Pinned:

  * no `how.engine` means today's behaviour exactly: the agent's own engine and effort,
    and no keyword added to a dispatch;
  * a pick is refused on save (400, the reason named) when the live catalog does not
    offer its provider, model or effort, and `null` clears it;
  * a pick equal to the stored one is not re-checked, so a catalog that moved on does
    not make an unrelated Brief edit unsaveable;
  * the campaign chat runs on the pick (provider, model, effort reach `_call_model`);
  * a pick that cannot run (signed out, out of allowance, gone from the catalog) fails
    the call with that reason (409) and the model is never called: no fallback to the
    agent's default;
  * the Desk dispatches carry the pick as provider/model/effort overrides, and are
    refused (never run elsewhere) when it cannot run.
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
from mc import desk_engines as _engines  # noqa: E402
from mc import desk_story  # noqa: E402
from mc.blueprints import desk_routes  # noqa: E402

CID = 'camp-e'
DAVE = {'name': 'dave', 'agent_name': 'Dave', 'body': 'You plan campaigns plainly.',
        'engine': {'provider': 'claude', 'model': 'claude-sonnet-5-5', 'effort': 'medium'}}
CAT = {
    'claude': {'display_name': 'Claude Code', 'model_selection': True,
               'models': ['claude-fable-5-2', 'claude-sonnet-5-5'], 'efforts': ['low', 'medium', 'high']},
    'codex': {'display_name': 'Codex', 'model_selection': True,
              'models': ['gpt-6.2-sol'], 'efforts': ['low', 'high']},
    'kiro': {'display_name': 'Kiro', 'model_selection': False, 'models': [], 'efforts': []},
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.config['TESTING'] = True
    dispatched = []

    def fake_dispatch(pid, brief, resume, **kw):
        dispatched.append(kw)
        return 'sess-1'

    monkeypatch.setattr(desk_routes, 'dispatch_agent', None)
    desk_routes.wire(
        load_projects_fn=lambda: [{'id': 'alpha', 'name': 'Alpha'}],
        load_project_fn=lambda pid: {'id': 'alpha', 'name': 'Alpha'} if pid == 'alpha' else None,
        dispatch_fn=fake_dispatch,
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
        uploads_root=tmp_path,
    )
    app.register_blueprint(desk_routes.bp)
    monkeypatch.setattr(_engines, 'JOBS_PATH', tmp_path / 'engine_jobs.json')
    monkeypatch.setattr(eng, 'catalog', lambda: CAT)
    state = {'unavailable': ''}
    monkeypatch.setattr(eng, 'availability_error', lambda provider, model='': state['unavailable'])
    calls = []

    def fake_model(provider, **kw):
        calls.append(dict(kw, provider=provider))
        return json.dumps({'reply': 'ok', 'suggest': None})

    monkeypatch.setattr(desk_story, '_call_model', fake_model)
    monkeypatch.setattr(desk_story._chars, 'read_character', lambda _s, name, **_k: DAVE if name == 'dave' else None)
    c = app.test_client()
    _accounts.create_account('x', '@clayrune', account_id='ch-x')
    r = c.post('/api/desk/campaigns?shape=v1', json={
        'id': CID, 'state': 'draft', 'projectId': 'alpha', 'rules': {},
        'map': {'stop': 'what', 'done': []},
        'plan': {'brief': 'Announce restore points', 'title': 'Restore', 'accounts': ['ch-x'],
                 'cadence': {'per_week': 3}, 'end': {'post_cap': 9}}})
    assert r.status_code == 201, r.get_json()
    _desk.upsert_presence('alpha', {'desk_agent': 'global:dave'})
    return c, calls, dispatched, state


def _patch(client, how):
    return client.patch(f'/api/desk/campaigns/{CID}?shape=v1', json={'how': how})


def _stored_pick():
    return eng.pick_of(next(c for c in _desk.list_campaigns() if c['id'] == CID))


# -- validate ---------------------------------------------------------------------------

@pytest.mark.parametrize('raw', [None, {}, {'provider': '', 'model': ' '}])
def test_an_empty_pick_is_the_agent_default(raw):
    assert eng.validate(raw, cat=CAT) is None


def test_a_catalog_pick_is_normalised():
    assert eng.validate({'provider': 'Codex', 'model': 'gpt-6.2-sol', 'effort': 'high'}, cat=CAT) == \
        {'provider': 'codex', 'model': 'gpt-6.2-sol', 'effort': 'high'}


@pytest.mark.parametrize('raw, words', [
    ('codex', 'must be an object'),
    ({'provider': 'nope', 'model': 'x'}, 'unknown provider'),
    ({'provider': 'claude', 'model': 'gpt-6.2-sol'}, 'does not offer a model'),
    ({'model': 'gpt-6.2-sol'}, 'needs its provider'),
    ({'provider': 'claude'}, 'needs its model'),
    ({'provider': 'kiro', 'model': 'x'}, 'chooses its own model'),
    ({'provider': 'codex', 'model': 'gpt-6.2-sol', 'effort': 'max'}, 'does not offer effort'),
    ({'provider': 'claude', 'model': 'claude-fable-5-2', 'flavour': 'x'}, "no field 'flavour'"),
    ({'provider': 7}, 'must be text'),
])
def test_a_pick_the_catalog_does_not_offer_is_refused_with_the_reason(raw, words):
    with pytest.raises(eng.EngineError) as e:
        eng.validate(raw, cat=CAT)
    assert e.value.status == 400 and words in str(e.value)


def test_an_effort_alone_is_checked_against_the_agents_provider():
    assert eng.validate({'effort': 'high'}, agent_provider='claude', cat=CAT) == {'effort': 'high'}
    with pytest.raises(eng.EngineError, match='pick an agent first'):
        eng.validate({'effort': 'high'}, cat=CAT)
    with pytest.raises(eng.EngineError, match='does not offer effort'):
        eng.validate({'effort': 'xhigh'}, agent_provider='claude', cat=CAT)


# -- the save path ----------------------------------------------------------------------

def test_a_valid_pick_is_saved_and_read_back(env):
    client, *_ = env
    pick = {'provider': 'codex', 'model': 'gpt-6.2-sol', 'effort': 'high'}
    r = _patch(client, {'engine': pick})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['how']['engine'] == pick
    assert _stored_pick() == pick


def test_a_pick_the_catalog_does_not_offer_is_a_400_and_nothing_is_stored(env):
    client, *_ = env
    r = _patch(client, {'engine': {'provider': 'claude', 'model': 'gpt-6.2-sol'}})
    assert r.status_code == 400 and 'does not offer a model' in r.get_json()['error']
    assert _stored_pick() is None


def test_null_clears_the_pick(env):
    client, *_ = env
    _patch(client, {'engine': {'provider': 'codex', 'model': 'gpt-6.2-sol'}})
    r = _patch(client, {'engine': None})
    assert r.status_code == 200 and 'engine' not in (r.get_json().get('how') or {})
    assert _stored_pick() is None


def test_a_stored_pick_the_catalog_dropped_does_not_block_an_unrelated_save(env, monkeypatch):
    client, *_ = env
    pick = {'provider': 'codex', 'model': 'gpt-6.2-sol'}
    assert _patch(client, {'engine': pick}).status_code == 200
    monkeypatch.setattr(eng, 'catalog', lambda: {'claude': CAT['claude']})   # codex gone
    r = _patch(client, {'engine': pick, 'strategy': 'edited'})
    assert r.status_code == 200 and _stored_pick() == pick
    # ...but picking it afresh on another campaign is refused
    assert _patch(client, {'engine': {'provider': 'codex', 'model': 'gpt-6.2-sol', 'effort': 'low'}}).status_code == 400


def test_a_create_with_a_bad_pick_is_a_400(env):
    client, *_ = env
    r = client.post('/api/desk/campaigns?shape=v1', json={
        'id': 'camp-bad', 'state': 'draft', 'projectId': 'alpha', 'plan': {'title': 'T', 'brief': 'b'},
        'how': {'engine': {'provider': 'nope', 'model': 'x'}}})
    assert r.status_code == 400 and 'unknown provider' in r.get_json()['error']


# -- the turn runs on the pick ----------------------------------------------------------

def test_without_a_pick_the_chat_runs_on_the_agents_own_engine(env):
    client, calls, _, _ = env
    assert client.post(f'/api/desk/campaigns/{CID}/chat', json={'message': 'hi'}).status_code == 200
    assert (calls[0]['provider'], calls[0]['model'], calls[0]['effort']) == ('claude', 'claude-sonnet-5-5', 'medium')


def test_a_pick_reaches_the_model_call(env):
    client, calls, _, _ = env
    _patch(client, {'engine': {'provider': 'codex', 'model': 'gpt-6.2-sol', 'effort': 'high'}})
    assert client.post(f'/api/desk/campaigns/{CID}/chat', json={'message': 'hi'}).status_code == 200
    assert (calls[0]['provider'], calls[0]['model'], calls[0]['effort']) == ('codex', 'gpt-6.2-sol', 'high')


def test_an_effort_only_pick_keeps_the_agents_model(env):
    client, calls, _, _ = env
    _patch(client, {'engine': {'effort': 'high'}})
    assert client.post(f'/api/desk/campaigns/{CID}/chat', json={'message': 'hi'}).status_code == 200
    assert (calls[0]['provider'], calls[0]['model'], calls[0]['effort']) == ('claude', 'claude-sonnet-5-5', 'high')


def test_a_pick_that_cannot_run_fails_the_turn_with_the_reason_and_calls_no_model(env):
    client, calls, _, state = env
    _patch(client, {'engine': {'provider': 'codex', 'model': 'gpt-6.2-sol'}})
    state['unavailable'] = 'codex is not signed in (not_logged_in)'
    r = client.post(f'/api/desk/campaigns/{CID}/chat', json={'message': 'hi'})
    assert r.status_code == 409
    msg = r.get_json()['error']
    assert 'codex is not signed in' in msg and 'Nothing was run on another engine' in msg
    assert calls == []


def test_a_pick_the_catalog_no_longer_offers_fails_the_turn(env, monkeypatch):
    client, calls, _, _ = env
    _patch(client, {'engine': {'provider': 'codex', 'model': 'gpt-6.2-sol'}})
    monkeypatch.setattr(eng, 'catalog', lambda: {'claude': CAT['claude']})
    r = client.post(f'/api/desk/campaigns/{CID}/chat', json={'message': 'hi'})
    assert r.status_code == 409 and 'no longer holds' in r.get_json()['error']
    assert calls == []


# -- the dispatches carry the pick ------------------------------------------------------

def test_a_dispatch_carries_the_pick_as_overrides(env):
    client, _, dispatched, _ = env
    _patch(client, {'engine': {'provider': 'codex', 'model': 'gpt-6.2-sol', 'effort': 'high'}})
    r = client.post(f'/api/desk/campaigns/{CID}/suggest', json={})
    assert r.status_code == 202, r.get_json()
    assert dispatched[0]['provider_override'] == 'codex'
    assert dispatched[0]['model_override'] == 'gpt-6.2-sol'
    assert dispatched[0]['effort_override'] == 'high'


def test_a_dispatch_with_no_pick_adds_no_keyword(env):
    client, _, dispatched, _ = env
    assert client.post(f'/api/desk/campaigns/{CID}/suggest', json={}).status_code == 202
    assert not {'provider_override', 'model_override', 'effort_override'} & set(dispatched[0])


def test_a_dispatch_is_refused_when_the_pick_cannot_run(env):
    client, _, dispatched, state = env
    _patch(client, {'engine': {'provider': 'codex', 'model': 'gpt-6.2-sol'}})
    state['unavailable'] = 'codex allowance is exhausted until 3pm'
    r = client.post(f'/api/desk/campaigns/{CID}/suggest', json={})
    assert r.status_code == 409 and 'allowance is exhausted' in r.get_json()['error']
    assert dispatched == []
