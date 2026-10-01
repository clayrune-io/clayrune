"""Desk v1 R1-W S7 - Suggest (M9/M10) and Revise (M20) (plan §1 Brief, §1 Review).

Pinned:

  * a picked agent is dispatched with the brief, `strict_character`, `source: agent`,
    and never approves, starts or sends anything: no picked agent is a 409, not a
    silent default;
  * M10 is the only write an agent has for suggestions: it refuses any text that
    names a limit, an account that is not in the workspace, a bad time; it merges
    key by key; and it shows up on the campaign as `how.suggested` /
    `suggestBlocker`, where a client PATCH of `how` can neither wipe nor forge it;
  * Suggested times land as `origin: agent, state: suggested` slots and re-running
    Suggest replaces only those, never a person's own or an accepted slot;
  * M20 refuses a version already past review (409), an unknown claim, a custom
    style with no note, and carries the claim / selection / note into the brief.
"""
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk as _desk  # noqa: E402
from mc import desk_accounts as _accounts  # noqa: E402
from mc import desk_pieces as _pieces  # noqa: E402
from mc.blueprints import desk_routes  # noqa: E402

CID = 'camp-s'


@pytest.fixture
def env(tmp_path):
    calls = []

    def fake_dispatch(project_id, task, _resume_id, **kw):
        calls.append({'project_id': project_id, 'task': task, **kw})
        return 'sess-9'

    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: [{'id': 'alpha', 'name': 'Alpha'}],
        load_project_fn=lambda pid: {'id': 'alpha', 'name': 'Alpha'} if pid == 'alpha' else None,
        dispatch_fn=fake_dispatch,
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
        uploads_root=tmp_path,
    )
    app.register_blueprint(desk_routes.bp)
    c = app.test_client()
    c.calls = calls  # type: ignore[attr-defined]
    _accounts.create_account('x', '@clayrune', account_id='ch-x')
    _accounts.create_account('blog', 'blog', account_id='ch-blog')
    r = c.post('/api/desk/campaigns?shape=v1', json={
        'id': CID, 'state': 'draft', 'projectId': 'alpha', 'rules': {},
        'map': {'stop': 'what', 'done': []},
        'plan': {'brief': 'Announce restore points', 'title': 'Restore', 'accounts': ['ch-x'],
                 'cadence': {'per_week': 3}, 'end': {'post_cap': 9}}})
    assert r.status_code == 201, r.get_json()
    return c


def _pick(agent='global:dave'):
    _desk.upsert_presence('alpha', {'desk_agent': agent})


def _campaign(c):
    return next(x for x in c.get('/api/desk/workspace').get_json()['campaigns'] if x['id'] == CID)


# -- M9 -------------------------------------------------------------------------------------

def test_suggest_needs_a_picked_agent_and_a_project(env):
    r = env.post(f'/api/desk/campaigns/{CID}/suggest', json={})
    assert r.status_code == 409 and r.get_json()['pick_agent'] is True and env.calls == []
    assert env.post('/api/desk/campaigns/nope/suggest', json={}).status_code == 404


def test_suggest_dispatches_the_picked_agent_with_the_brief(env):
    _pick('global:dave')
    r = env.post(f'/api/desk/campaigns/{CID}/suggest', json={'note': 'avoid Fridays'})
    assert r.status_code == 202 and r.get_json() == {'ok': True, 'session_id': 'sess-9'}
    call = env.calls[0]
    assert call['character'] == 'global:dave' and call['strict_character'] is True and call['source'] == 'agent'
    task = call['task']
    assert 'NOT STARTING IT' in task and 'avoid Fridays' in task
    assert f'/api/desk/campaigns/{CID}/suggestions' in task
    assert 'ch-x' in task and 'ch-blog' not in task          # only the campaign's chosen accounts


# -- M10 ------------------------------------------------------------------------------------

def test_suggestions_merge_and_project_onto_the_campaign(env):
    r = env.put(f'/api/desk/campaigns/{CID}/suggestions', json={
        'what': [{'title': 'Undo anything in two clicks', 'channel_id': 'ch-x', 'because': ['f1']}],
        'when': [{'at': '2027-03-02T09:00:00Z', 'because': ['f2']}],
        'where': [{'channel_id': 'ch-x'}],
        'because': ['f1', 'f2']})
    assert r.status_code == 200, r.get_json()
    how = _campaign(env)['how']['suggested']
    assert how['what'][0]['title'] == 'Undo anything in two clicks' and how['what'][0]['channelId'] == 'ch-x'
    assert how['where']['channelId'] == 'ch-x' and how['when']['slotId'] and how['because'] == ['f1', 'f2']
    slots = _campaign(env)['when']['slots']
    assert [(s['origin'], s['state'], s['at']) for s in slots] == [('agent', 'suggested', '2027-03-02T09:00:00Z')]
    # a later call that sends only `what` leaves when/where as they were
    env.put(f'/api/desk/campaigns/{CID}/suggestions', json={'what': []})
    how = _campaign(env)['how']['suggested']
    assert 'what' not in how and how['where']['channelId'] == 'ch-x' and how['when']['slotId']


def test_rerunning_suggest_replaces_only_the_suggested_agent_slots(env):
    env.put(f'/api/desk/campaigns/{CID}/suggestions', json={'when': [{'at': '2027-03-02T09:00:00Z'}]})
    with _desk._store_lock:
        st = _desk._read_store()
        st['campaigns'][CID]['when']['slots'] += [
            {'id': 'mine', 'at': '2027-03-05T09:00:00Z', 'origin': 'user', 'state': 'planned'},
            {'id': 'took', 'at': '2027-03-06T09:00:00Z', 'origin': 'agent', 'state': 'accepted'}]
        _desk._write_store(st)
    env.put(f'/api/desk/campaigns/{CID}/suggestions', json={'when': [{'at': '2027-03-09T09:00:00Z'}]})
    ids = {(s['id'], s['at'][:10]) for s in _campaign(env)['when']['slots']}
    assert ('mine', '2027-03-05') in ids and ('took', '2027-03-06') in ids
    assert not any(a == '2027-03-02' for _, a in ids) and any(a == '2027-03-09' for _, a in ids)


@pytest.mark.parametrize('body', [
    {'what': [{'title': 'Raise the cadence to 50 a week'}]},           # names a limit
    {'what': [{'title': 'ok', 'channel_id': 'ch-nope'}]},             # not in the workspace
    {'when': [{'at': 'tomorrow-ish'}]},
    {'where': [{}]},
    {'what': 'a string'},
    {'budget': 5},                                                     # not a suggestion key
    {'blocker': {'id': 'q', 'question': 'which?', 'answers': [{'id': 'a', 'label': 'A'}]}},   # 1 answer
])
def test_suggestions_refuse_commitments_and_junk(env, body):
    r = env.put(f'/api/desk/campaigns/{CID}/suggestions', json=body)
    assert r.status_code == 400, r.get_json()
    assert 'suggested' not in (_campaign(env).get('how') or {})


def test_blocker_round_trips_and_clears(env):
    blocker = {'id': 'q1', 'question': 'Which launch date?',
               'answers': [{'id': 'a', 'label': 'Monday'}, {'id': 'b', 'label': 'Friday'}]}
    assert env.put(f'/api/desk/campaigns/{CID}/suggestions', json={'blocker': blocker}).status_code == 200
    assert _campaign(env)['suggestBlocker']['question'] == 'Which launch date?'
    env.put(f'/api/desk/campaigns/{CID}/suggestions', json={'blocker': None})
    assert _campaign(env)['suggestBlocker'] is None


def test_a_client_patch_of_how_cannot_wipe_or_forge_suggestions(env):
    env.put(f'/api/desk/campaigns/{CID}/suggestions', json={'what': [{'title': 'Real one'}]})
    r = env.patch(f'/api/desk/campaigns/{CID}', json={'how': {'strategy': 'plain', 'suggested': {'what': []}}})
    assert r.status_code == 200, r.get_json()
    c = _campaign(env)
    assert c['how']['strategy'] == 'plain' and c['how']['suggested']['what'][0]['title'] == 'Real one'


def test_suggest_unknown_campaign_is_404(env):
    assert env.put('/api/desk/campaigns/nope/suggestions', json={'what': []}).status_code == 404


# -- M20 ----------------------------------------------------------------------------------------

def _piece(state='needs_review', **kw):
    _pieces.create_piece(CID, 'post', 'Launch', piece_id='p1', body='Original text',
                         claims=kw.get('claims'))
    _pieces.add_version('p1', 'ch-x', body='Original text', version_id='v1')
    if state != 'drafting':
        _pieces.update_version('p1', 'v1', {'state': state})
    return '/api/desk/pieces/p1/versions/v1/revise'


def test_revise_dispatches_with_style_selection_and_note(env):
    _pick()
    url = _piece()
    r = env.post(url, json={'style': 'shorter', 'selection': 'Original', 'note': 'keep the emoji'})
    assert r.status_code == 202 and r.get_json()['session_id'] == 'sess-9'
    call = env.calls[0]
    assert call['character'] == 'global:dave' and call['strict_character'] is True
    t = call['task']
    assert 'NOT APPROVING OR SENDING' in t and 'Make it shorter' in t
    assert 'Original text' in t and 'keep the emoji' in t and 'REVISE ONLY THIS PASSAGE' in t
    assert '/api/desk/pieces/p1/versions/v1' in t and '"state":"needs_review"' in t


def test_revise_claim_and_custom_note(env):
    _pick()
    url = _piece(claims=[{'id': 'c1', 'text': '40% faster'}])
    assert env.post(url, json={'claim_id': 'c1'}).status_code == 202
    assert '40% faster' in env.calls[0]['task'] and 'THE CLAIM TO FIX' in env.calls[0]['task']
    assert env.post(url, json={'style': 'custom', 'note': 'funnier'}).status_code == 202


@pytest.mark.parametrize('body, code', [
    ({}, 400), ({'style': 'louder'}, 400), ({'style': 'custom'}, 400), ({'claim_id': 'zzz'}, 400)])
def test_revise_refuses_unclear_requests(env, body, code):
    _pick()
    url = _piece()
    assert env.post(url, json=body).status_code == code and env.calls == []


def test_revise_refuses_a_version_past_review_and_a_missing_agent(env):
    url = _piece()
    r = env.post(url, json={'style': 'shorter'})
    assert r.status_code == 409 and r.get_json()['pick_agent'] is True
    _pick()
    with _desk._store_lock:
        st = _desk._read_store()
        st['pieces']['p1']['versions'][0]['state'] = 'submitted'
        _desk._write_store(st)
    r = env.post(url, json={'style': 'shorter'})
    assert r.status_code == 409 and 'submitted' in r.get_json()['error'] and env.calls == []
    assert env.post('/api/desk/pieces/p1/versions/zz/revise', json={'style': 'shorter'}).status_code == 404
    assert env.post('/api/desk/pieces/nope/versions/v1/revise', json={'style': 'shorter'}).status_code in (404, 400)
