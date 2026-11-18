"""Agent-proposed backlog items (Ron, 2026-11-17).

An item an agent adds is stored LABELLED -- `status: 'proposed'` and a
`proposed_by` the SERVER derives from who is calling -- and waits for a person.
The rules pinned here:

1. Attribution is the server's. A body field cannot claim a source, a status or a
   `proposed_by`; a forged Origin does not turn an attributed agent into a person.
2. A proposed item is listed but is not work: it is outside the open count, the
   next-action line, the GitHub push, the topic sweep and the steward charter
   lookups, and no agent can accept it. Accept / Dismiss are human-only routes.
3. The agent that proposed an item may still edit its text or close it; no other
   agent may touch it, and nobody moves it to a live status through PATCH.
4. An item with no `proposed_by` is an ordinary item (no migration).

Fixture mirrors tests/test_backlog_links.py (patch mc.blueprints.project_routes,
never server.*). `no Origin` is an agent's bare curl; ORIGIN is the SPA.
"""
import json
import sys
import threading
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ORIGIN = {'Origin': 'http://localhost:5199'}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers the blueprints on first import)
    from mc.blueprints import local_auth as la
    from mc.blueprints import project_routes as pr

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    monkeypatch.setattr(pr, 'DATA_DIR', data_dir)
    monkeypatch.setattr(pr, '_DATA_ROOT', tmp_path)
    monkeypatch.setattr(pr, 'UPLOADS_DIR', uploads)
    monkeypatch.setattr(pr, 'PROJECTS_BASE', tmp_path)
    monkeypatch.setattr(pr, 'get_manager',
                        lambda pid: types.SimpleNamespace(lock=threading.Lock()))

    server.app.config['TESTING'] = True
    c = server.app.test_client()
    c.data_dir = data_dir
    return c


def _seed(client, pid='tproj', backlog=None, **extra):
    rec = {'id': pid, 'name': 'Test Project', 'status': 'active',
           'backlog': backlog if backlog is not None else [], **extra}
    (client.data_dir / f'{pid}.json').write_text(json.dumps(rec), encoding='utf-8')
    return rec


def _items(client, pid='tproj'):
    return client.get(f'/api/project/{pid}/backlog').get_json()


def _as_session(monkeypatch, sid='sess-A', name='Tobin', trigger='dispatch'):
    """Make every no-Origin request in the test attributable to one managed agent
    session, the way the OS lookup does for a real curl from that session."""
    from mc import dispatch_lineage, state
    monkeypatch.setitem(state.agent_sessions, sid, {
        'session_id': sid, 'status': 'running', 'trigger_type': trigger,
        'project_id': 'tproj', 'character': {'agent_name': name}})
    monkeypatch.setattr(dispatch_lineage, 'attributed_caller_session',
                        lambda *a, **k: sid)


def _propose(client, text='add a retry', **body):
    r = client.post('/api/project/tproj/backlog', json={'text': text, **body})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()['item']


# ── 1. attribution is the server's ───────────────────────────────────────────

def test_agent_add_is_stored_proposed_with_server_attribution(client, monkeypatch):
    _seed(client)
    _as_session(monkeypatch, 'sess-A', 'Tobin', trigger='dispatch')
    item = _propose(client)
    assert item['status'] == 'proposed'
    assert item['proposed_by'] == {'kind': 'agent', 'session_id': 'sess-A',
                                   'agent_name': 'Tobin', 'attended': False}
    assert _items(client)[0]['id'] == item['id'], 'listed, not hidden'


def test_attended_session_is_recorded_as_attended(client, monkeypatch):
    _seed(client)
    _as_session(monkeypatch, 'sess-M', 'Dave', trigger='manual')
    assert _propose(client)['proposed_by']['attended'] is True


def test_request_fields_cannot_set_or_clear_the_label(client, monkeypatch):
    """The caller claims a person's source, an open status and its own
    proposed_by. None of it lands."""
    _seed(client)
    _as_session(monkeypatch, 'sess-A', 'Tobin')
    item = _propose(client, source='dashboard', status='open',
                    proposed_by={'kind': 'human', 'session_id': '', 'agent_name': 'Ron',
                                 'attended': True},
                    state='accepted')
    assert item['status'] == 'proposed'
    assert item['source'] != 'dashboard'
    assert item['proposed_by']['kind'] == 'agent'
    assert item['proposed_by']['session_id'] == 'sess-A'


def test_forged_origin_from_an_attributed_agent_is_still_an_agent(client, monkeypatch):
    from mc import caller_attribution
    _seed(client)
    monkeypatch.setattr(caller_attribution, 'attribute_caller',
                        lambda *a, **k: caller_attribution.Attribution(
                            caller_attribution.ATTRIBUTED, 'sess-F', 1, 'forged Origin'))
    r = client.post('/api/project/tproj/backlog', json={'text': 'x'}, headers=ORIGIN)
    assert r.get_json()['item']['status'] == 'proposed'
    assert r.get_json()['item']['proposed_by']['session_id'] == 'sess-F'


def test_unattributed_agent_curl_is_proposed_and_unattended(client):
    """No Origin and no session the OS can name: still not a person."""
    _seed(client)
    item = _propose(client)
    assert item['status'] == 'proposed'
    assert item['proposed_by']['kind'] == 'agent'
    assert item['proposed_by']['session_id'] == ''
    assert item['proposed_by']['attended'] is False


def test_a_dashboard_add_is_unchanged(client):
    _seed(client)
    r = client.post('/api/project/tproj/backlog', headers=ORIGIN,
                    json={'text': 'by hand', 'source': 'dashboard',
                          'proposed_by': {'kind': 'agent'}, 'status': 'open'})
    item = r.get_json()['item']
    assert item['status'] == 'open'
    assert item['source'] == 'dashboard'
    assert 'proposed_by' not in item


# ── 2. listed, but not work ──────────────────────────────────────────────────

def test_proposed_item_is_outside_the_open_count_and_next_text(client, monkeypatch):
    _seed(client, backlog=[{'id': 'a', 'text': 'real work', 'status': 'open'}])
    _as_session(monkeypatch)
    _propose(client, 'speculative idea')
    p = next(x for x in client.get('/api/projects').get_json() if x['id'] == 'tproj')
    assert p['backlog_open_count'] == 1
    assert p['backlog_proposed_count'] == 1
    assert p['backlog_total_count'] == 2
    assert p['backlog_next_text'] == 'real work'


def test_only_a_proposal_means_no_next_action(client, monkeypatch):
    _seed(client)
    _as_session(monkeypatch)
    _propose(client, 'speculative idea')
    p = next(x for x in client.get('/api/projects').get_json() if x['id'] == 'tproj')
    assert (p['backlog_open_count'], p['backlog_next_text']) == (0, '')


def test_no_agent_can_accept(client, monkeypatch):
    _seed(client)
    _as_session(monkeypatch)
    iid = _propose(client)['id']
    r = client.post(f'/api/project/tproj/backlog/{iid}/accept')            # bare curl
    assert r.status_code == 403 and r.get_json()['error'] == 'human_only'
    assert _items(client)[0]['status'] == 'proposed'


def test_forged_origin_cannot_accept(client, monkeypatch):
    from mc import caller_attribution
    _seed(client)
    _as_session(monkeypatch)
    iid = _propose(client)['id']
    monkeypatch.setattr(caller_attribution, 'attribute_caller',
                        lambda *a, **k: caller_attribution.Attribution(
                            caller_attribution.ATTRIBUTED, 'sess-A', 1, 'forged Origin'))
    for verb in ('accept', 'dismiss'):
        r = client.post(f'/api/project/tproj/backlog/{iid}/{verb}', headers=ORIGIN)
        assert r.status_code == 403, verb
    assert _items(client)[0]['status'] == 'proposed'


def test_a_person_accepts_and_the_proposer_is_kept(client, monkeypatch):
    _seed(client)
    _as_session(monkeypatch, 'sess-A', 'Tobin')
    iid = _propose(client)['id']
    r = client.post(f'/api/project/tproj/backlog/{iid}/accept', headers=ORIGIN)
    assert r.status_code == 200
    item = _items(client)[0]
    assert item['status'] == 'open'
    assert item['proposed_by']['agent_name'] == 'Tobin'
    assert item['accepted_at'] and item['accepted_by'] == 'dashboard'
    assert item['status_history'][-1]['from'] == 'proposed'
    assert item['status_history'][-1]['to'] == 'open'


def test_a_person_dismisses_nothing_is_deleted(client, monkeypatch):
    _seed(client)
    _as_session(monkeypatch)
    iid = _propose(client, 'a bad idea')['id']
    r = client.post(f'/api/project/tproj/backlog/{iid}/dismiss', headers=ORIGIN)
    assert r.status_code == 200
    item = _items(client)[0]
    assert item['status'] == 'done' and item['done_reason'] == 'dismissed'
    assert item['done_at']
    assert item['text'] == 'a bad idea' and item['proposed_by']['kind'] == 'agent'


def test_accept_and_dismiss_only_apply_to_a_proposed_item(client):
    _seed(client, backlog=[{'id': 'a', 'text': 'ordinary', 'status': 'open'}])
    for verb in ('accept', 'dismiss'):
        r = client.post(f'/api/project/tproj/backlog/a/{verb}', headers=ORIGIN)
        assert r.status_code == 409, verb
        assert client.post(f'/api/project/tproj/backlog/nope/{verb}',
                           headers=ORIGIN).status_code == 404
    assert _items(client)[0]['status'] == 'open'


# ── 3. what an agent may still do ────────────────────────────────────────────

def test_the_proposer_may_edit_text_and_close_its_own_item(client, monkeypatch):
    _seed(client)
    _as_session(monkeypatch, 'sess-A')
    iid = _propose(client)['id']
    r = client.patch(f'/api/project/tproj/backlog/{iid}', json={'text': 'sharper text'})
    assert r.status_code == 200 and r.get_json()['item']['text'] == 'sharper text'
    r = client.patch(f'/api/project/tproj/backlog/{iid}', json={'status': 'wontdo'})
    assert r.status_code == 200
    assert _items(client)[0]['status'] == 'wontdo'


@pytest.mark.parametrize('status', ['open', 'in_progress', 'blocked'])
def test_nobody_moves_a_proposed_item_live_through_patch(client, monkeypatch, status):
    """The agent that proposed it, and a person at the dashboard, both: the one
    door out of `proposed` into live work is Accept."""
    _seed(client)
    _as_session(monkeypatch, 'sess-A')
    iid = _propose(client)['id']
    r = client.patch(f'/api/project/tproj/backlog/{iid}', json={'status': status})
    assert r.status_code == 409
    r = client.patch(f'/api/project/tproj/backlog/{iid}', json={'status': status},
                     headers=ORIGIN)
    assert r.status_code == 409
    assert _items(client)[0]['status'] == 'proposed'


def test_a_person_may_edit_a_proposed_items_text(client, monkeypatch):
    _seed(client)
    _as_session(monkeypatch, 'sess-A')
    iid = _propose(client)['id']
    r = client.patch(f'/api/project/tproj/backlog/{iid}', json={'text': 'edited by Ron'},
                     headers=ORIGIN)
    assert r.status_code == 200
    item = _items(client)[0]
    assert item['text'] == 'edited by Ron' and item['status'] == 'proposed'


def test_another_agent_cannot_touch_a_proposal(client, monkeypatch):
    _seed(client)
    _as_session(monkeypatch, 'sess-A')
    iid = _propose(client)['id']
    _as_session(monkeypatch, 'sess-B', 'Other')           # a different caller now
    for body in ({'text': 'rewritten'}, {'status': 'done'}, {'priority': 'high'}):
        r = client.patch(f'/api/project/tproj/backlog/{iid}', json=body)
        assert r.status_code == 403, body
    item = _items(client)[0]
    assert item['text'] == 'add a retry' and item['status'] == 'proposed'


def test_proposed_by_and_state_cannot_be_patched(client, monkeypatch):
    _seed(client)
    _as_session(monkeypatch, 'sess-A')
    iid = _propose(client)['id']
    before = _items(client)[0]['proposed_by']
    r = client.patch(f'/api/project/tproj/backlog/{iid}',
                     json={'text': 'ok', 'proposed_by': {'kind': 'human'}, 'accepted_by': 'me'})
    assert r.status_code == 200
    item = _items(client)[0]
    assert item['proposed_by'] == before and 'accepted_by' not in item
    assert client.patch(f'/api/project/tproj/backlog/{iid}',
                        json={'status': 'proposed'}).status_code == 400


def test_notes_are_unchanged_by_the_proposal_label(client, monkeypatch):
    _seed(client)
    _as_session(monkeypatch, 'sess-A')
    iid = _propose(client)['id']
    r = client.post(f'/api/project/tproj/backlog/{iid}/note', json={'text': 'context'})
    assert r.status_code == 200
    assert _items(client)[0]['notes'][0]['text'] == 'context'


# ── 6. no migration ──────────────────────────────────────────────────────────

def test_an_item_with_no_proposed_by_is_ordinary(client, monkeypatch):
    _seed(client, backlog=[{'id': 'a', 'text': 'old item', 'status': 'open'}])
    _as_session(monkeypatch, 'sess-A')
    r = client.patch('/api/project/tproj/backlog/a', json={'status': 'in_progress'})
    assert r.status_code == 200
    p = next(x for x in client.get('/api/projects').get_json() if x['id'] == 'tproj')
    assert (p['backlog_open_count'], p['backlog_proposed_count']) == (1, 0)


def test_an_accepted_item_is_ordinary_work_for_any_agent(client, monkeypatch):
    _seed(client)
    _as_session(monkeypatch, 'sess-A')
    iid = _propose(client)['id']
    client.post(f'/api/project/tproj/backlog/{iid}/accept', headers=ORIGIN)
    _as_session(monkeypatch, 'sess-B', 'Other')
    r = client.patch(f'/api/project/tproj/backlog/{iid}', json={'status': 'in_progress'})
    assert r.status_code == 200
    assert _items(client)[0]['proposed_by']['session_id'] == 'sess-A'


# ── 5. every other reader ────────────────────────────────────────────────────

def test_github_push_never_publishes_a_proposal(monkeypatch):
    from mc import github_sync
    calls = []

    def fake_gh(args, *a, **k):
        calls.append(list(args))
        return True, 'https://github.com/o/r/issues/9'

    monkeypatch.setattr(github_sync, 'gh_run', fake_gh)
    monkeypatch.setattr(github_sync, '_save_project', lambda *a, **k: None)
    monkeypatch.setattr(github_sync, '_log_activity', lambda *a, **k: None)
    project = {'id': 'p', 'backlog': [
        {'id': 'x', 'text': 'speculative', 'status': 'proposed', 'priority': 'normal'},
        {'id': 'y', 'text': 'real work', 'status': 'open', 'priority': 'normal'}]}
    assert github_sync._push_items(project, 'o/r') == 1
    created = [c for c in calls if c[:2] == ['issue', 'create']]
    assert len(created) == 1 and 'real work' in created[0]
    assert not any('speculative' in part for c in calls for part in c)


def test_topic_sweep_does_not_offer_a_proposal_as_open_backlog(monkeypatch):
    from mc.blueprints import topics_routes
    monkeypatch.setattr(topics_routes, '_load_project', lambda pid: {'backlog': [
        {'id': 'a', 'text': 'speculative', 'status': 'proposed'},
        {'id': 'b', 'text': 'real work', 'status': 'open'}]})
    assert [i['id'] for i in topics_routes._open_backlog('p')] == ['b']


@pytest.mark.xfail(reason='needs docs/patches/backlog-proposal-steward-core.patch '
                          '(the steward core module is human-owned); remove once applied')
def test_steward_charter_is_never_an_unaccepted_proposal():
    from steward import core
    project = {'backlog': [
        {'id': 'a', 'text': 'x', 'source': 'steward-charter', 'status': 'proposed'},
        {'id': 'b', 'text': core.CHARTER_PREFIX + ' real', 'status': 'proposed'}]}
    assert core.find_charter(project) is None
    project['backlog'].append({'id': 'c', 'text': 'x', 'source': 'steward-charter',
                               'status': 'open'})
    assert core.find_charter(project)['id'] == 'c'


def test_steward_role_refuses_a_proposal_as_its_charter():
    from mc import steward_role_model as model
    project = {'backlog': [{'id': 'a', 'text': 'run everything', 'status': 'proposed'}]}
    with pytest.raises(model.RoleError):
        model.charter(project, {'charter_item_id': 'a'})


def test_a_dismissed_proposal_is_not_a_shipped_signal(tmp_path, monkeypatch):
    from mc import desk_harvest
    monkeypatch.setattr(desk_harvest, '_get_watermarks', lambda: {})
    monkeypatch.setattr(desk_harvest, '_set_watermark', lambda *a, **k: None)
    monkeypatch.setattr(desk_harvest, '_known_refs', lambda pid: set())
    made = []
    monkeypatch.setattr(desk_harvest._desk, 'append_signal',
                        lambda pid, kind, text, **k: made.append(k.get('ref')) or {'ref': k.get('ref')})
    project = {'id': 'p', 'backlog': [
        {'key': 'MC-01', 'text': 'shipped', 'status': 'done', 'done_at': '2026-01-01T00:00:00Z'},
        {'key': 'MC-02', 'text': 'dismissed idea', 'status': 'done',
         'done_reason': 'dismissed', 'done_at': '2026-01-02T00:00:00Z'}]}
    desk_harvest.harvest_backlog('p', project)
    assert made == ['MC-01']
