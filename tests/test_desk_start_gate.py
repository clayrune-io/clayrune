"""The Desk — server-side Start gate (R2-11, IA revision 2 §9 Q1/Q2).

`DeskV1Kit.validatePlan` refused a goal with no target/source and a term over
90 days, but only in the browser: a PATCH straight to `state: running` skipped
both. `mc/desk.py::_start_gate_problems` mirrors them; these tests pin it at
the route, and pin that a legacy campaign (no `goal.metric`, no `term`) still
activates exactly as before.
"""
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import desk_routes  # noqa: E402


@pytest.fixture
def client(tmp_path):
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: [],
        load_project_fn=lambda _pid: None,
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
    )
    app.register_blueprint(desk_routes.bp)
    return app.test_client()


def _make(client, **extra):
    body = {'title': 'Restore points launch', 'thesis': 'Undo any agent mistake',
            'voice': 'product'}
    body.update(extra)
    r = client.post('/api/desk/campaigns', json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()['id']


def _start(client, cid, **patch):
    return client.patch(f'/api/desk/campaigns/{cid}', json={'state': 'running', **patch})


def _state(client, cid):
    return next(c for c in client.get('/api/desk/campaigns').get_json() if c['id'] == cid)['state']


GOOD_GOAL = {'metric': 'beta signups', 'target': 60, 'source': 'manual'}
GOOD_TERM = {'starts': '2026-09-01', 'ends': '2026-10-20'}


def test_goal_without_a_source_is_refused(client):
    cid = _make(client, goal={'metric': 'beta signups', 'target': 60, 'source': None})
    r = _start(client, cid)
    assert r.status_code == 400
    assert 'measurement source' in r.get_json()['error']
    assert _state(client, cid) == 'proposed'


def test_goal_without_a_target_is_refused(client):
    cid = _make(client, goal={'metric': 'beta signups', 'target': None, 'source': 'manual'})
    r = _start(client, cid)
    assert r.status_code == 400
    assert 'no target' in r.get_json()['error']
    assert _state(client, cid) == 'proposed'


def test_goal_missing_both_names_both(client):
    cid = _make(client, goal={'metric': 'beta signups'})
    err = _start(client, cid).get_json()['error']
    assert 'no target' in err and 'measurement source' in err


def test_term_over_90_days_is_refused(client):
    cid = _make(client, goal=GOOD_GOAL, term={'starts': '2026-09-01', 'ends': '2026-12-01'})
    r = _start(client, cid)
    assert r.status_code == 400
    assert '91 days' in r.get_json()['error'] and 'max 90' in r.get_json()['error']
    assert _state(client, cid) == 'proposed'


def test_term_of_exactly_90_days_is_allowed(client):
    cid = _make(client, goal=GOOD_GOAL, term={'starts': '2026-09-01', 'ends': '2026-11-30'})
    assert _start(client, cid).status_code == 200


def test_unparseable_term_is_refused_not_passed(client):
    cid = _make(client, goal=GOOD_GOAL, term={'starts': '2026-09-01', 'ends': 'soon'})
    r = _start(client, cid)
    assert r.status_code == 400
    assert 'not valid ISO dates' in r.get_json()['error']


def test_complete_goal_and_short_term_starts(client):
    cid = _make(client, goal=GOOD_GOAL, term=GOOD_TERM)
    r = _start(client, cid)
    assert r.status_code == 200 and r.get_json()['state'] == 'running'


def test_one_patch_can_fix_the_goal_and_start(client):
    cid = _make(client, goal={'metric': 'beta signups', 'target': 60, 'source': None})
    assert _start(client, cid).status_code == 400
    r = _start(client, cid, goal=GOOD_GOAL)
    assert r.status_code == 200 and r.get_json()['goal']['source'] == 'manual'


def test_resume_from_paused_is_gated_too(client):
    cid = _make(client, goal=GOOD_GOAL, term=GOOD_TERM)
    assert _start(client, cid).status_code == 200
    assert client.patch(f'/api/desk/campaigns/{cid}', json={'state': 'paused'}).status_code == 200
    # The goal loses its source while paused; Resume must not slip past.
    client.patch(f'/api/desk/campaigns/{cid}', json={'goal': {**GOOD_GOAL, 'source': None}})
    r = _start(client, cid)
    assert r.status_code == 400
    assert _state(client, cid) == 'paused'


def test_editing_a_running_campaign_is_not_regated(client):
    """Only the transition INTO running is gated — a running campaign whose
    goal is edited is the Goal stop's business, not a refusal here."""
    cid = _make(client, goal=GOOD_GOAL, term=GOOD_TERM)
    assert _start(client, cid).status_code == 200
    r = client.patch(f'/api/desk/campaigns/{cid}', json={'goal': {**GOOD_GOAL, 'source': None}})
    assert r.status_code == 200


def test_legacy_campaign_with_no_goal_or_term_still_starts(client):
    cid = _make(client)
    assert _start(client, cid).status_code == 200


def test_non_running_patches_are_never_gated(client):
    cid = _make(client, goal={'metric': 'beta signups'})
    r = client.patch(f'/api/desk/campaigns/{cid}', json={'state': 'dropped'})
    assert r.status_code == 200
