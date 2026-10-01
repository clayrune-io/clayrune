"""Desk v1 R1-W S1 — the campaign write routes the Home/Project/Setup surfaces call.

`?shape=v1` on `POST/PATCH /api/desk/campaigns` takes the v1 campaign object and
answers in it (static/js/desk-v1-store.js), translating state words at the route
while the store keeps the legacy ones; without it both directions are unchanged.
`PATCH /api/desk/presence/<pid>` gains `state`, which pauses/resumes the project's
campaigns in the same write. Pinned:

  * a v1 draft is accepted with no voice and no thesis, keeps the client's id,
    and is stored as `draft`; a v1 create may not name any other started state;
  * a taken id is a 409, a malformed id a 400;
  * v1 PATCH words are translated in (active->running, archived->dropped) and the
    answer is translated out; the same PATCH without `shape=v1` is the old route;
  * client-local draft flags (`_touched`, `rules`, ...) never reach the store;
  * pausing a project records each live campaign's prior state, leaves finished
    ones and other projects' alone; resuming restores them, and a campaign that no
    longer passes the Start gate stays paused and is reported in `held`.
"""
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import desk_routes  # noqa: E402
from mc import desk as _desk  # noqa: E402

PROJECTS = [{'id': 'alpha', 'name': 'Alpha'}, {'id': 'beta', 'name': 'Beta'}]


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Not testing the passcode gate here (tests/test_desk_approval.py does).
    monkeypatch.setattr(desk_routes, '_require_human_passcode', lambda data: None)
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: PROJECTS,
        load_project_fn=lambda pid: next((p for p in PROJECTS if p['id'] == pid), None),
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
    )
    app.register_blueprint(desk_routes.bp)
    return app.test_client()


def _draft(**kw):
    body = {'id': 'camp-draft-1', 'state': 'draft', 'projectId': 'alpha',
            'subject': None, 'goal': {'current': 0}, 'rules': {},
            '_prefillProjectId': 'alpha', '_discardIfUntouched': True,
            'map': {'stop': 'how', 'done': []},
            'plan': {'brief': 'Promote Alpha.', 'title': 'Alpha campaign', 'accounts': [],
                     'cadence': {'per_week': None}, 'end': {'date': None, 'post_cap': None}}}
    body.update(kw)
    return body


def _stored(cid):
    return next(c for c in _desk.list_campaigns() if c['id'] == cid)


# -- create -------------------------------------------------------------------

def test_v1_draft_with_no_voice_and_no_thesis_is_created(client):
    r = client.post('/api/desk/campaigns?shape=v1', json=_draft())
    assert r.status_code == 201, r.get_json()
    out = r.get_json()
    assert out['id'] == 'camp-draft-1'                      # the client's own id
    assert out['state'] == 'draft'
    assert out['projectId'] == 'alpha' and out['plan']['title'] == 'Alpha campaign'
    stored = _stored('camp-draft-1')
    assert stored['state'] == 'draft' and stored['voices'] == [] and stored['voice'] is None
    assert stored['project_id'] == 'alpha' and stored['title'] == 'Alpha campaign'
    assert stored['thesis'] == 'Promote Alpha.'             # plan.brief is the thesis
    assert stored['map'] == {'stop': 'how', 'done': []}


def test_client_local_draft_flags_never_reach_the_store(client):
    client.post('/api/desk/campaigns?shape=v1', json=_draft(_touched=True))
    stored = _stored('camp-draft-1')
    assert not [k for k in stored if k.startswith('_')]
    assert 'rules' not in stored and 'projectId' not in stored


def test_v1_create_without_a_state_is_a_proposal(client):
    body = _draft()
    del body['state']
    assert client.post('/api/desk/campaigns?shape=v1', json=body).get_json()['state'] == 'proposed'


@pytest.mark.parametrize('state', ['active', 'completed', 'archived', 'running', 'paused', 'bogus'])
def test_v1_create_cannot_start_a_campaign(client, state):
    r = client.post('/api/desk/campaigns?shape=v1', json=_draft(state=state))
    assert r.status_code == 400
    assert _desk.list_campaigns() == []


def test_taken_id_is_409_and_does_not_overwrite(client):
    client.post('/api/desk/campaigns?shape=v1', json=_draft())
    r = client.post('/api/desk/campaigns?shape=v1', json=_draft(projectId='beta'))
    assert r.status_code == 409
    assert _stored('camp-draft-1')['project_id'] == 'alpha'


@pytest.mark.parametrize('bad', ['', 'a b', '../x', 'x' * 81, 7])
def test_malformed_client_id_is_400(client, bad):
    r = client.post('/api/desk/campaigns?shape=v1', json=_draft(id=bad))
    assert r.status_code == 400


def test_server_assigns_an_id_when_the_client_sends_none(client):
    body = _draft()
    del body['id']
    out = client.post('/api/desk/campaigns?shape=v1', json=body).get_json()
    assert out['id'].startswith('camp-')


def test_legacy_create_is_unchanged(client):
    # no shape=v1: title+thesis still required, and a campaign still gets the
    # default voice when none is named (the v1 route deliberately does not).
    assert client.post('/api/desk/campaigns', json=_draft()).status_code == 400
    r = client.post('/api/desk/campaigns', json={'title': 'T', 'thesis': 'X'})
    assert r.status_code == 201 and r.get_json()['voices'] and r.get_json()['state'] == 'proposed'
    assert 'projectId' not in r.get_json()


# -- patch --------------------------------------------------------------------

def test_v1_patch_translates_words_in_and_out(client):
    client.post('/api/desk/campaigns?shape=v1', json=_draft())
    r = client.patch('/api/desk/campaigns/camp-draft-1?shape=v1',
                     json={'state': 'paused', 'projectId': 'beta',
                           'plan': {'title': 'Beta push', 'accounts': ['x:ron']}})
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert out['state'] == 'paused' and out['projectId'] == 'beta'
    assert out['plan']['title'] == 'Beta push'
    stored = _stored('camp-draft-1')
    assert stored['state'] == 'paused' and stored['project_id'] == 'beta'
    assert stored['title'] == 'Beta push'                    # mirrored from plan.title
    client.patch('/api/desk/campaigns/camp-draft-1?shape=v1', json={'state': 'archived'})
    assert _stored('camp-draft-1')['state'] == 'dropped'


def test_v1_patch_only_touches_the_keys_it_names(client):
    client.post('/api/desk/campaigns?shape=v1', json=_draft())
    client.patch('/api/desk/campaigns/camp-draft-1?shape=v1', json={'projectId': 'beta'})
    stored = _stored('camp-draft-1')
    assert stored['project_id'] == 'beta' and stored['title'] == 'Alpha campaign'
    assert stored['state'] == 'draft'


def test_v1_patch_cannot_start_a_campaign(client):
    # R1-W S2: Start is its own human route (POST .../start), not a state PATCH.
    client.post('/api/desk/campaigns?shape=v1', json=_draft())
    r = client.patch('/api/desk/campaigns/camp-draft-1?shape=v1', json={'state': 'active'})
    assert r.status_code == 400 and '/start' in r.get_json()['error']
    assert _stored('camp-draft-1')['state'] == 'draft'


def test_v1_start_gate_still_applies(client):
    client.post('/api/desk/campaigns?shape=v1', json=_draft(goal={'metric': 'signups', 'target': None}))
    r = client.post('/api/desk/campaigns/camp-draft-1/start')
    assert r.status_code == 409 and 'cannot start' in r.get_json()['error']
    assert _stored('camp-draft-1')['state'] == 'draft'


def test_legacy_patch_still_answers_in_stored_words(client):
    client.post('/api/desk/campaigns?shape=v1', json=_draft())
    out = client.patch('/api/desk/campaigns/camp-draft-1', json={'state': 'running'}).get_json()
    assert out['state'] == 'running' and 'projectId' not in out


def test_patch_missing_campaign_is_404(client):
    assert client.patch('/api/desk/campaigns/nope?shape=v1', json={'state': 'paused'}).status_code == 404


def test_v1_delete_removes_a_draft(client):
    client.post('/api/desk/campaigns?shape=v1', json=_draft())
    assert client.delete('/api/desk/campaigns/camp-draft-1').status_code == 200
    assert _desk.list_campaigns() == []
    assert client.delete('/api/desk/campaigns/camp-draft-1').status_code == 404


def test_v1_campaign_in_drops_client_local_keys():
    out = _desk.v1_campaign_in({'id': 'x', '_touched': True, 'rules': {}, 'projectId': 'p',
                                'state': 'completed', 'plan': {'title': ' T ', 'brief': 'B'}})
    assert out == {'state': 'done', 'project_id': 'p', 'plan': {'title': ' T ', 'brief': 'B'},
                   'title': 'T', 'thesis': 'B'}


# -- project pause / resume ----------------------------------------------------

def _mk(client, cid, project, state, **kw):
    client.post('/api/desk/campaigns?shape=v1', json=_draft(id=cid, projectId=project, **kw))
    with _desk._store_lock:
        store = _desk._read_store()
        store['campaigns'][cid]['state'] = state
        _desk._write_store(store)


def _states():
    return {c['id']: c['state'] for c in _desk.list_campaigns()}


def test_pause_cascades_and_resume_restores(client):
    _mk(client, 'a-run', 'alpha', 'running')
    _mk(client, 'a-prop', 'alpha', 'proposed')
    _mk(client, 'a-draft', 'alpha', 'draft')
    _mk(client, 'a-done', 'alpha', 'done')
    _mk(client, 'a-held', 'alpha', 'paused')
    _mk(client, 'b-run', 'beta', 'running')

    r = client.patch('/api/desk/presence/alpha', json={'state': 'paused'})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body['state'] == 'paused'
    assert body['cascade']['changed'] == 3
    assert {c['id'] for c in body['cascade']['campaigns']} == {'a-run', 'a-prop', 'a-draft'}
    assert all(c['state'] == 'paused' for c in body['cascade']['campaigns'])
    assert _states() == {'a-run': 'paused', 'a-prop': 'paused', 'a-draft': 'paused',
                         'a-done': 'done', 'a-held': 'paused', 'b-run': 'running'}
    assert _stored('a-run')['pre_pause_state'] == 'running'
    assert 'pre_pause_state' not in _stored('a-held')       # was paused before; not ours to resume

    ws = client.get('/api/desk/workspace').get_json()
    assert {p['id']: p['state'] for p in ws['projects']}['alpha'] == 'paused'

    r = client.patch('/api/desk/presence/alpha', json={'state': 'active'})
    assert r.status_code == 200 and r.get_json()['cascade']['held'] == []
    assert _states() == {'a-run': 'running', 'a-prop': 'proposed', 'a-draft': 'draft',
                         'a-done': 'done', 'a-held': 'paused', 'b-run': 'running'}
    assert 'pre_pause_state' not in _stored('a-run')


def test_resume_holds_a_campaign_that_no_longer_passes_the_start_gate(client):
    _mk(client, 'a-run', 'alpha', 'running', goal={'metric': 'signups', 'target': 10, 'source': 'manual'})
    client.patch('/api/desk/presence/alpha', json={'state': 'paused'})
    client.patch('/api/desk/campaigns/a-run?shape=v1', json={'goal': {'metric': 'signups', 'target': None}})
    body = client.patch('/api/desk/presence/alpha', json={'state': 'active'}).get_json()
    assert body['state'] == 'active'                          # the project resumes ...
    assert body['cascade']['held'] == [{'id': 'a-run', 'reasons': ['goal has no target',
                                                                   'goal has no measurement source']}]
    assert _states()['a-run'] == 'paused'                     # ... the campaign does not
    assert _stored('a-run')['pre_pause_state'] == 'running'   # and can still be resumed later


def test_pause_is_idempotent(client):
    _mk(client, 'a-run', 'alpha', 'running')
    client.patch('/api/desk/presence/alpha', json={'state': 'paused'})
    again = client.patch('/api/desk/presence/alpha', json={'state': 'paused'}).get_json()
    assert again['cascade']['changed'] == 0
    assert _stored('a-run')['pre_pause_state'] == 'running'   # not overwritten with 'paused'


def test_presence_state_must_be_known_and_other_keys_stay_refused(client):
    assert client.patch('/api/desk/presence/alpha', json={'state': 'archived'}).status_code == 400
    r = client.patch('/api/desk/presence/alpha', json={'budget': {'amount': 1}})
    assert r.status_code == 400
    assert client.patch('/api/desk/presence/nope', json={'state': 'paused'}).status_code == 404


def test_presence_desk_agent_patch_has_no_cascade_key(client):
    body = client.patch('/api/desk/presence/alpha', json={'desk_agent': None}).get_json()
    assert 'cascade' not in body
