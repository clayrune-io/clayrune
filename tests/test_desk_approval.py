"""Desk v1 R1-W S2 — the human approval snapshot.

`camp['approval']` (the legacy bounds tracker) is rewritten by every PATCH, so the
server could not tell what a human approved. `camp['approved']` is the record that
can: written ONLY by the Start / Approve / Renew routes, which an unattended agent
session is refused, and checked by the publisher. Pinned:

  * Start stamps `approved` (bounds + hash + at + by + term) and appends it to
    `approvals`; it is refused (409, nothing written) for a campaign that fails
    the plan-bounds + Start gate, or that is not draft/proposed;
  * an ordinary PATCH never moves `approved`, whether it changes bounds or not,
    and cannot write `approved`/`approvals` itself;
  * a v1 PATCH cannot put a campaign into `running` (Start is its own action);
  * widening any bound after Start leaves the campaign `awaiting_approval` and
    makes `publish_blockers` / `desk_publish.publish` refuse; narrowing does not;
    Approve re-snapshots and clears it;
  * Start / Approve / Renew are 403 for an unattended caller and write nothing;
  * ... and, for ANY caller (an attended agent's curl carries a forged Origin),
    403 without the retyped dashboard passcode (MC-995): no passcode / wrong
    passcode / no passcode configured all write nothing, the right one succeeds;
  * Renew opens the next term and refuses when something other than the term
    was widened since the last approval.
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
from mc import desk_publish  # noqa: E402

PROJECTS = [{'id': 'alpha', 'name': 'Alpha'}]
CID = 'camp-a'


@pytest.fixture
def client(tmp_path, monkeypatch):
    # This file tests the approval snapshot, not the passcode gate: bypass it
    # here (as test_character_routes.py does) and exercise the real gate in the
    # "human proof" section below through `gated`.
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


@pytest.fixture
def unattended(monkeypatch):
    from mc.state import agent_sessions
    snapshot = dict(agent_sessions)
    agent_sessions.clear()
    agent_sessions['dispatch-1'] = {'status': 'running', 'trigger_type': 'dispatch'}
    yield
    agent_sessions.clear()
    agent_sessions.update(snapshot)


def _draft(**kw):
    body = {'id': CID, 'state': 'draft', 'projectId': 'alpha',
            'goal': {'current': 0}, 'rules': {}, 'map': {'stop': 'how', 'done': []},
            'plan': {'brief': 'Promote Alpha.', 'title': 'Alpha campaign',
                     'accounts': ['x:ron'], 'cadence': {'per_week': 3},
                     'end': {'date': '2026-12-01', 'post_cap': 10}}}
    body.update(kw)
    return body


def _stored() -> dict:
    return next(c for c in _desk.list_campaigns() if c['id'] == CID)


def _started(client):
    client.post('/api/desk/campaigns?shape=v1', json=_draft())
    r = client.post(f'/api/desk/campaigns/{CID}/start')
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _patch(client, body):
    r = client.patch(f'/api/desk/campaigns/{CID}?shape=v1', json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


# -- Start --------------------------------------------------------------------

def test_start_stamps_the_snapshot_and_runs_the_campaign(client):
    out = _started(client)
    st = _stored()
    assert st['state'] == 'running' and out['state'] == 'active'
    ap = st['approved']
    assert ap['bounds_hash'] == _desk.compute_bounds_hash(ap['bounds'])
    assert ap['bounds']['accounts'] == ['x:ron'] and ap['bounds']['cadence'] == {'per_week': 3}
    assert ap['by'] == 'human' and ap['term'] == 1
    assert st['approvals'] == [ap]
    assert st['term']['index'] == 1 and st['term']['ends'] == '2026-12-01'
    # the v1 answer carries the human snapshot as `approval`, and the verdict
    assert out['approval'] == ap and out['awaiting_approval'] is False


@pytest.mark.parametrize('plan_patch,problem', [
    ({'accounts': []}, 'no accounts'),
    ({'cadence': {'per_week': None}}, 'no cadence ceiling'),
    ({'end': {'date': None, 'post_cap': None}}, 'no end date or post cap'),
])
def test_start_refused_without_the_plan_bounds(client, plan_patch, problem):
    plan = _draft()['plan']
    plan.update(plan_patch)
    client.post('/api/desk/campaigns?shape=v1', json=_draft(plan=plan))
    r = client.post(f'/api/desk/campaigns/{CID}/start')
    assert r.status_code == 409 and problem in r.get_json()['problems']
    st = _stored()
    assert st['state'] == 'draft' and 'approved' not in st


def test_start_is_refused_for_a_campaign_that_already_started(client):
    _started(client)
    assert client.post(f'/api/desk/campaigns/{CID}/start').status_code == 409


def test_start_missing_campaign_is_404(client):
    assert client.post('/api/desk/campaigns/nope/start').status_code == 404


# -- a PATCH cannot approve ---------------------------------------------------

def test_patch_does_not_move_the_snapshot(client):
    _started(client)
    before = _stored()['approved']
    _patch(client, {'plan': {'title': 'Retitled', 'accounts': ['x:ron'],
                             'cadence': {'per_week': 1},
                             'end': {'date': '2026-12-01', 'post_cap': 10}}})
    st = _stored()
    assert st['approved'] == before and st['approvals'] == [before]
    assert st['plan']['cadence'] == {'per_week': 1}


def test_patch_cannot_write_the_approval_fields(client):
    _started(client)
    before = _stored()['approved']
    forged = {'bounds': {'accounts': ['x:ron', 'x:other'], 'cadence': {'per_week': 99},
                         'end': {}, 'term': {}, 'budget': {}},
              'bounds_hash': 'x', 'at': 'now', 'by': 'human', 'term': 1}
    r = client.patch(f'/api/desk/campaigns/{CID}',
                     json={'approved': forged, 'approvals': [forged], 'started_at': 'x'})
    assert r.status_code == 200
    st = _stored()
    assert st['approved'] == before and st['approvals'] == [before]
    assert st['started_at'] != 'x'


def test_v1_patch_cannot_start_a_campaign(client):
    client.post('/api/desk/campaigns?shape=v1', json=_draft())
    r = client.patch(f'/api/desk/campaigns/{CID}?shape=v1', json={'state': 'active'})
    assert r.status_code == 400
    st = _stored()
    assert st['state'] == 'draft' and 'approved' not in st


def test_a_patch_pause_and_resume_keeps_the_approval(client):
    _started(client)
    _patch(client, {'state': 'paused'})
    assert _stored()['state'] == 'paused'
    out = _patch(client, {'state': 'active'})        # resume: approved is on file
    assert out['state'] == 'active' and out['awaiting_approval'] is False


# -- widening voids it --------------------------------------------------------

def _widen_cadence(client, per_week=10):
    return _patch(client, {'plan': {'title': 'Alpha campaign', 'accounts': ['x:ron'],
                                    'cadence': {'per_week': per_week},
                                    'end': {'date': '2026-12-01', 'post_cap': 10}}})


def test_widening_a_bound_leaves_the_campaign_awaiting_approval(client):
    _started(client)
    out = _widen_cadence(client)
    assert out['awaiting_approval'] is True
    assert _desk.publish_blockers(CID) == ['bounds were widened after the approval; '
                                           'a human must approve again']


def test_adding_an_account_voids_it_and_narrowing_does_not(client):
    _started(client)
    out = _patch(client, {'plan': {'title': 'Alpha campaign', 'accounts': ['x:ron', 'li:co'],
                                   'cadence': {'per_week': 3},
                                   'end': {'date': '2026-12-01', 'post_cap': 10}}})
    assert out['awaiting_approval'] is True
    out = _patch(client, {'plan': {'title': 'Alpha campaign', 'accounts': ['x:ron'],
                                   'cadence': {'per_week': 1},
                                   'end': {'date': '2026-11-01', 'post_cap': 5}}})
    assert out['awaiting_approval'] is False and _desk.publish_blockers(CID) == []


def test_a_widening_that_is_later_undone_by_patch_is_clear_again(client):
    _started(client)
    assert _widen_cadence(client)['awaiting_approval'] is True
    assert _widen_cadence(client, per_week=3)['awaiting_approval'] is False


def test_approve_resnapshots_the_widened_bounds(client):
    _started(client)
    _widen_cadence(client)
    r = client.post(f'/api/desk/campaigns/{CID}/approve')
    assert r.status_code == 200 and r.get_json()['awaiting_approval'] is False
    st = _stored()
    assert st['approved']['bounds']['cadence'] == {'per_week': 10}
    assert len(st['approvals']) == 2 and _desk.publish_blockers(CID) == []


def test_a_tampered_snapshot_fails_closed(client):
    _started(client)
    store = _desk._read_store()
    store['campaigns'][CID]['approved']['bounds']['cadence'] = {'per_week': 500}
    _desk._write_store(store)
    assert 'approval record does not match its own hash' in _desk.publish_blockers(CID)[0]


def test_approve_is_refused_for_a_campaign_that_has_not_started(client):
    client.post('/api/desk/campaigns?shape=v1', json=_draft())
    assert client.post(f'/api/desk/campaigns/{CID}/approve').status_code == 409


# -- the publisher ------------------------------------------------------------

def test_publisher_refuses_a_campaign_with_no_approval(client, tmp_path, monkeypatch):
    monkeypatch.setattr(desk_publish, 'RECEIPTS_PATH', tmp_path / 'receipts.json')
    client.post('/api/desk/campaigns?shape=v1', json=_draft())
    with pytest.raises(desk_publish.PublishError, match='not approved to publish'):
        desk_publish.publish({'id': 'i1', 'body': 'hi', 'platform': 'x', 'campaign_id': CID})


def test_publisher_refuses_after_a_widening_and_never_reaches_the_network(client, tmp_path, monkeypatch):
    monkeypatch.setattr(desk_publish, 'RECEIPTS_PATH', tmp_path / 'receipts.json')
    calls = []
    monkeypatch.setattr(desk_publish, '_post_tweet', lambda *a, **k: calls.append(a) or {})
    _started(client)
    _widen_cadence(client)
    with pytest.raises(desk_publish.PublishError, match='widened'):
        desk_publish.publish({'id': 'i2', 'body': 'hi', 'platform': 'x', 'campaign_id': CID})
    assert calls == []


def test_publisher_refuses_a_paused_campaign(client, tmp_path, monkeypatch):
    monkeypatch.setattr(desk_publish, 'RECEIPTS_PATH', tmp_path / 'receipts.json')
    _started(client)
    _patch(client, {'state': 'paused'})
    with pytest.raises(desk_publish.PublishError, match='not running'):
        desk_publish.publish({'id': 'i3', 'body': 'hi', 'platform': 'x', 'campaign_id': CID})


# -- unattended callers -------------------------------------------------------

@pytest.mark.parametrize('action', ['start', 'approve', 'renew'])
def test_unattended_caller_is_refused_and_nothing_is_written(client, unattended, action):
    client.post('/api/desk/campaigns?shape=v1', json=_draft())
    r = client.post(f'/api/desk/campaigns/{CID}/{action}')
    assert r.status_code == 403
    st = _stored()
    assert st['state'] == 'draft' and 'approved' not in st and 'approvals' not in st


def test_unattended_caller_can_still_patch(client, unattended):
    client.post('/api/desk/campaigns?shape=v1', json=_draft())
    r = client.patch(f'/api/desk/campaigns/{CID}?shape=v1', json={'projectId': 'alpha'})
    assert r.status_code == 200


# -- human proof (MC-995) -----------------------------------------------------

PASSCODE = 'unlock1234'
WRONG = 'nope-wrong-code'
# A forged Origin is what an attended agent's curl carries; it must not matter.
FORGED = {'Origin': 'http://localhost:5199'}


@pytest.fixture
def gated(client, tmp_path, monkeypatch):
    """`client` with the REAL passcode gate back on and a passcode configured."""
    from mc.blueprints import local_auth
    from mc.blueprints.secrets_routes import _require_human_passcode as real
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    monkeypatch.setattr(desk_routes, '_require_human_passcode', real)
    local_auth._local_auth_set_passcode(PASSCODE)
    local_auth._LOCAL_AUTH_FAILS.clear()
    return client


def _post(client, action, body=None):
    return client.post(f'/api/desk/campaigns/{CID}/{action}', json=body or {}, headers=FORGED)


@pytest.mark.parametrize('action', ['start', 'approve', 'renew'])
@pytest.mark.parametrize('body', [{}, {'passcode': WRONG}, {'passcode': 12345}])
def test_approval_action_refused_without_the_right_passcode(gated, action, body):
    gated.post('/api/desk/campaigns?shape=v1', json=_draft())
    r = _post(gated, action, body)
    assert r.status_code == 403 and r.get_json()['error'] == 'bad_passcode'
    st = _stored()
    assert st['state'] == 'draft' and 'approved' not in st and 'approvals' not in st


@pytest.mark.parametrize('action', ['start', 'approve', 'renew'])
def test_approval_action_refused_when_no_passcode_is_configured(gated, tmp_path, monkeypatch, action):
    from mc.blueprints import local_auth
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'never-set.json')
    gated.post('/api/desk/campaigns?shape=v1', json=_draft())
    r = _post(gated, action, {'passcode': PASSCODE})
    assert r.status_code == 403 and r.get_json()['error'] == 'passcode_required'
    st = _stored()
    assert st['state'] == 'draft' and 'approved' not in st


def test_start_succeeds_with_the_right_passcode_and_stores_no_passcode(gated):
    gated.post('/api/desk/campaigns?shape=v1', json=_draft())
    r = _post(gated, 'start', {'passcode': PASSCODE, 'policy_record': {'note': 'x'}})
    assert r.status_code == 200 and r.get_json()['state'] == 'active'
    assert PASSCODE not in repr(_stored())


def test_approve_succeeds_with_the_right_passcode(gated):
    gated.post('/api/desk/campaigns?shape=v1', json=_draft())
    assert _post(gated, 'start', {'passcode': PASSCODE}).status_code == 200
    r = _post(gated, 'approve', {'passcode': PASSCODE})
    assert r.status_code == 200 and r.get_json()['awaiting_approval'] is False


def test_renew_clears_the_gate_with_the_right_passcode(gated):
    gated.post('/api/desk/campaigns?shape=v1', json=_draft())
    assert _post(gated, 'start', {'passcode': PASSCODE}).status_code == 200
    # Term 1 has not ended at this suite's clock, so the route answers its own
    # 409 (not the gate's 403): the right passcode got past the gate.
    r = _post(gated, 'renew', {'passcode': PASSCODE})
    assert r.status_code == 409 and 'not ended' in r.get_json()['error']


def test_unattended_is_still_refused_with_the_right_passcode(gated, unattended):
    gated.post('/api/desk/campaigns?shape=v1', json=_draft())
    # No Origin header: a forged one is exactly what makes is_unattended_caller()
    # say "human", which is why the passcode gate above exists.
    r = gated.post(f'/api/desk/campaigns/{CID}/start', json={'passcode': PASSCODE})
    assert r.status_code == 403 and 'needs a human' in r.get_json()['error']
    assert _stored()['state'] == 'draft'


# -- renew --------------------------------------------------------------------

def test_renew_opens_the_next_term_and_resnapshots(client):
    _started(client)
    r = client.post(f'/api/desk/campaigns/{CID}/renew')
    # term 1 ends 2026-12-01: not over yet at the real clock of this suite
    assert r.status_code == 409 and 'not ended' in r.get_json()['error']
    out = _desk.renew_campaign(CID, today='2026-12-02')
    assert out['term']['index'] == 2 and out['term']['starts'] == '2026-12-01'
    assert out['approved']['term'] == 2 and len(out['approvals']) == 2
    assert [t['index'] for t in out['terms']] == [1, 2]


def test_renew_refuses_when_something_else_was_widened(client):
    _started(client)
    _widen_cadence(client)
    with pytest.raises(_desk.ApprovalRefused, match='other than the term'):
        _desk.renew_campaign(CID, today='2026-12-02')
    assert len(_stored()['approvals']) == 1
