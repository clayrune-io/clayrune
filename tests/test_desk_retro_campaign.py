"""Desk v1 R1-W S8 — M12, the Retro section's backend.

Pinned:

  * `GET /api/desk/campaigns/<id>/retro` is a 404 for an unknown campaign, a 400
    for a non-numeric `term`, and NEVER writes: reading a closed term twice leaves
    the playbook as it was;
  * a post with no number for the per-post metric is not in an arm (never a 0);
    the "need 10 each" floor counts posts with a number;
  * an interim (running) term shows the table and proposes nothing, and the
    propose route refuses it with a 409;
  * a closed term with 12 + 12 numbered posts yields a `finding` row carrying
    arms and evidence; POST proposes it once (a second POST adds nothing) and
    the findings come back as `finding_rows`;
  * an arm whose mean is 0 gives a JSON-safe `ratio: None, unbounded: True`,
    never `Infinity`;
  * angle / spend kind read "too few campaigns" for one campaign.
"""
import json
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import desk_routes  # noqa: E402
from mc import desk as _desk  # noqa: E402

PROJECTS = [{'id': 'alpha', 'name': 'Alpha'}]
CID = 'camp-r'


@pytest.fixture
def client(tmp_path):
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


def _campaign(client, *, term=None):
    body = {'id': CID, 'state': 'draft', 'projectId': 'alpha',
            'goal': {'metric': 'signups', 'target': 30, 'source': 'manual',
                     'entries': [{'at': '2026-07-10', 'value': 22}]},
            'term': term or {'index': 1, 'starts': '2026-06-01', 'ends': '2026-08-01'},
            'rules': {}, 'map': {'stop': 'goal', 'done': []},
            'plan': {'brief': 'Promote Alpha.', 'title': 'Alpha', 'accounts': [],
                     'cadence': {'per_week': None}, 'end': {'date': None, 'post_cap': None}}}
    r = client.post('/api/desk/campaigns?shape=v1', json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _post(client, i, *, fmt, account, clicks, term=1, hour=9):
    r = client.post('/api/desk/ledger', json={
        'platform': 'x', 'voice': _desk.default_voice() or 'ron', 'body': f'post {i}',
        'campaign_id': CID, 'project_id': 'alpha', 'piece_id': f'p{i}', 'format': fmt,
        'account': account, 'term': term, 'cost': 0.015,
        'published_at': f'2026-06-{1 + i % 28:02d}T{hour:02d}:00:00Z'})
    assert r.status_code == 201, r.get_json()
    pid = r.get_json()['id']
    if clicks is not None:
        o = client.post(f'/api/desk/ledger/{pid}/outcome', json={'metric': 'clicks', 'value': clicks})
        assert o.status_code == 200, o.get_json()
    return pid


def _retro(client, expect=200, method='get', query=''):
    r = getattr(client, method)(f'/api/desk/campaigns/{CID}/retro{query}')
    assert r.status_code == expect, r.get_json()
    return r.get_json()


def _dim(retro, name):
    return next(d for d in retro['dimensions'] if d['dimension'] == name)


def test_unknown_campaign_is_404_and_bad_term_is_400(client):
    assert client.get('/api/desk/campaigns/nope/retro').status_code == 404
    assert client.post('/api/desk/campaigns/nope/retro').status_code == 404
    _campaign(client)
    assert client.get(f'/api/desk/campaigns/{CID}/retro?term=abc').status_code == 400


def test_posts_without_a_number_are_not_in_an_arm(client):
    _campaign(client)
    for i in range(12):
        _post(client, i, fmt='post', account='x:ron', clicks=None)
    r = _retro(client)
    fmt = _dim(r, 'format')
    assert fmt['verdict'] == 'too_few_posts'
    assert '(0 and 0; need 10 each)' in fmt['text']          # 12 posts, 0 with a number


def test_interim_term_proposes_nothing_and_post_is_refused(client):
    _campaign(client, term={'index': 1, 'starts': '2026-06-01', 'ends': '2099-01-01'})
    for i in range(12):
        _post(client, i, fmt='post', account='x:ron', clicks=20)
        _post(client, 100 + i, fmt='image', account='x:ron', clicks=5)
    r = _retro(client)
    assert r['status'] == 'interim'
    assert _dim(r, 'format')['verdict'] == 'finding'          # numbers are shown...
    assert r['findings'] == [] and r['finding_rows'] == []    # ...but never proposed
    assert 'Interim' in r['summary']
    err = _retro(client, expect=409, method='post')
    assert 'proposes nothing' in err['error']
    assert _desk.list_findings(project_id='alpha') == []


def _closed_with_two_formats(client, image_clicks=5):
    _campaign(client)
    for i in range(12):
        _post(client, i, fmt='post', account='x:ron', clicks=20 + i % 3)
        _post(client, 100 + i, fmt='image', account='x:ron', clicks=image_clicks)


def test_closed_term_finding_is_proposed_once_and_reading_never_writes(client):
    _closed_with_two_formats(client)
    first = _retro(client)
    assert first['status'] == 'closed'
    fmt = _dim(first, 'format')
    assert fmt['verdict'] == 'finding'
    # equal counts tie by label, so 'image' is arm a; post wins, so the direction is b>a
    assert fmt['arms'] == {'a': 'image', 'b': 'post'}
    assert fmt['effect']['direction'] == 'b>a'
    assert fmt['evidence'] == [{'campaign_id': CID, 'term': 1, 'n_a': 12, 'n_b': 12}]
    _retro(client)                                            # a second read
    assert _desk.list_findings(project_id='alpha') == []      # GET wrote nothing
    assert first['findings'] == []

    proposed = _retro(client, method='post')
    ids = proposed['findings']
    assert ids and all(f['state'] == 'proposed' and f['origin'] == 'unattended'
                       for f in proposed['finding_rows'])
    assert {f['dimension'] for f in proposed['finding_rows']} >= {'format'}
    again = _retro(client, method='post')                     # idempotent
    assert again['findings'] == ids
    assert len(_desk.list_findings(project_id='alpha')) == len(ids)


def test_a_rejected_finding_is_not_reproposed_by_a_second_run(client):
    _closed_with_two_formats(client)
    fid = next(f['id'] for f in _retro(client, method='post')['finding_rows'] if f['dimension'] == 'format')
    assert client.post(f'/api/desk/findings/{fid}/reject').status_code in (200, 403)
    after = _retro(client, method='post')
    assert not any(f['dimension'] == 'format' and f['id'] != fid for f in after['finding_rows'])
    n = len(_desk.list_findings(project_id='alpha'))
    _retro(client, method='post')
    assert len(_desk.list_findings(project_id='alpha')) == n


def test_zero_mean_arm_gives_json_safe_unbounded_ratio(client):
    _closed_with_two_formats(client, image_clicks=0)
    r = _retro(client)
    fmt = _dim(r, 'format')
    assert fmt['verdict'] == 'finding'
    assert fmt['effect']['ratio'] is None and fmt['effect']['unbounded'] is True
    json.dumps(r, allow_nan=False)                            # no Infinity/NaN anywhere
    proposed = _retro(client, method='post')
    json.dumps(proposed, allow_nan=False)


def test_campaign_level_dimensions_read_too_few_campaigns(client):
    _closed_with_two_formats(client)
    r = _retro(client)
    for name in ('angle', 'spend_kind'):
        assert _dim(r, name)['verdict'] == 'too_few_campaigns'
    assert [d['dimension'] for d in r['dimensions']] == [
        'format', 'platform_voice', 'slot', 'angle', 'spend_kind']


def test_goal_and_spend_never_invent_numbers(client):
    _campaign(client)
    r = _retro(client)                                        # no posts at all
    assert r['spend']['publishing'] is None and r['spend']['total'] is None
    assert r['spend']['cost_per_outcome'] is None
    assert r['goal']['actual'] == 22 and r['goal']['target'] == 30
    for i in range(12):
        _post(client, i, fmt='post', account='x:ron', clicks=20 + i % 3)
        _post(client, 100 + i, fmt='image', account='x:ron', clicks=5)
    r = _retro(client)
    assert r['spend']['publishing'] == pytest.approx(0.36)    # 24 posts x 0.015
    assert r['spend']['cost_per_outcome'] == pytest.approx(0.36 / 22, abs=1e-4)
    assert r['spend']['media_cost'] is None
