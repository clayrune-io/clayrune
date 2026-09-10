"""The Desk — routes (mc/blueprints/desk_routes.py).

Guards the surface contract rather than re-testing the store (tests/test_desk.py
does that): status codes, validation, and the two properties that are design
decisions rather than implementation details —

  * a campaign without a THESIS is refused, because a campaign without one is a
    folder, and the Board's job is to answer "why is this running now";
  * there is NO publish route, and `POST /api/desk/ledger` only records that a
    human already released something.
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
        load_projects_fn=lambda: [{'social_pending_count': 2},
                                  {'social_pending_count': 1}],
        load_project_fn=lambda _pid: None,
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
    )
    app.register_blueprint(desk_routes.bp)
    return app.test_client()


# -- signals ------------------------------------------------------------------

def test_post_and_list_signals(client):
    r = client.post('/api/desk/signals', json={
        'project_id': 'mission_control', 'kind': 'release',
        'summary': 'Shipped drag-to-hire, now live'})
    assert r.status_code == 201
    assert r.get_json()['story_score'] > 0

    rows = client.get('/api/desk/signals').get_json()
    assert len(rows) == 1
    assert rows[0]['project_id'] == 'mission_control'


def test_signal_requires_project_and_summary(client):
    assert client.post('/api/desk/signals', json={'summary': 'x'}).status_code == 400
    assert client.post('/api/desk/signals', json={'project_id': 'p'}).status_code == 400


def test_bad_min_score_is_a_400_not_a_500(client):
    assert client.get('/api/desk/signals?min_score=soon').status_code == 400


def test_signal_filters(client):
    client.post('/api/desk/signals', json={
        'project_id': 'a', 'kind': 'release', 'summary': 'Shipped a real thing'})
    client.post('/api/desk/signals', json={
        'project_id': 'b', 'kind': 'commit', 'summary': 'chore: lint'})
    assert len(client.get('/api/desk/signals?project_id=a').get_json()) == 1
    hot = client.get('/api/desk/signals?min_score=0.35').get_json()
    assert len(hot) == 1 and hot[0]['project_id'] == 'a'


# -- voices -------------------------------------------------------------------

def test_voices_list_and_patch(client):
    names = [v['name'] for v in client.get('/api/desk/voices').get_json()]
    assert names == ['personal', 'product']

    r = client.patch('/api/desk/voices/personal', json={'banned': ['leverage']})
    assert r.status_code == 200 and r.get_json()['banned'] == ['leverage']


def test_unknown_voice_is_404(client):
    assert client.get('/api/desk/voices/marketing').status_code == 404
    assert client.patch('/api/desk/voices/marketing', json={}).status_code == 404
    assert client.post('/api/desk/voices/marketing/edit', json={}).status_code == 404


def test_edit_learns_and_reports_when_it_did_not(client):
    r = client.post('/api/desk/voices/personal/edit', json={
        'before': 'Excited to announce our game-changing feature!',
        'after': 'Shipped drag-to-hire. Three days, two rewrites.',
        'draft_id': 'd-1'})
    assert r.status_code == 200 and r.get_json()['learned'] is True

    # A cosmetic edit is a real answer, not an error.
    r2 = client.post('/api/desk/voices/personal/edit',
                     json={'before': 'same text', 'after': 'same text'})
    assert r2.status_code == 200 and r2.get_json()['learned'] is False


def test_brief_carries_the_rewrite(client):
    client.post('/api/desk/voices/personal/edit', json={
        'before': 'We are thrilled to leverage synergies across the stack',
        'after': 'I rewrote the scheduler and it came out slower.'})
    brief = client.get('/api/desk/voices/personal/brief').get_json()['brief']
    assert 'I rewrote the scheduler' in brief


# -- campaigns ----------------------------------------------------------------

def test_campaign_crud(client):
    r = client.post('/api/desk/campaigns', json={
        'title': 'Agent persistence', 'thesis': 'Clayrune keeps agents alive',
        'voice': 'product'})
    assert r.status_code == 201
    cid = r.get_json()['id']

    assert client.patch(f'/api/desk/campaigns/{cid}',
                        json={'state': 'running'}).get_json()['state'] == 'running'
    assert len(client.get('/api/desk/campaigns?state=running').get_json()) == 1
    assert client.delete(f'/api/desk/campaigns/{cid}').status_code == 200
    assert client.get('/api/desk/campaigns').get_json() == []


def test_a_campaign_without_a_thesis_is_refused(client):
    """A campaign without a thesis is a folder — the Board must answer *why*."""
    assert client.post('/api/desk/campaigns',
                       json={'title': 'Stuff'}).status_code == 400


def test_bad_state_and_voice_are_400(client):
    cid = client.post('/api/desk/campaigns',
                      json={'title': 't', 'thesis': 'th'}).get_json()['id']
    assert client.patch(f'/api/desk/campaigns/{cid}',
                        json={'state': 'launched'}).status_code == 400
    assert client.post('/api/desk/campaigns',
                       json={'title': 't', 'thesis': 'th',
                             'voice': 'marketing'}).status_code == 400


def test_missing_campaign_is_404(client):
    assert client.patch('/api/desk/campaigns/nope', json={'state': 'running'}).status_code == 404
    assert client.delete('/api/desk/campaigns/nope').status_code == 404


# -- ledger -------------------------------------------------------------------

def test_ledger_records_and_consumes_its_signal(client):
    sig = client.post('/api/desk/signals', json={
        'project_id': 'mission_control', 'kind': 'release',
        'summary': 'Shipped the Desk'}).get_json()

    r = client.post('/api/desk/ledger', json={
        'platform': 'x', 'voice': 'personal', 'body': 'Shipped the Desk today',
        'signal_id': sig['id'], 'project_id': 'mission_control'})
    assert r.status_code == 201

    # The signal is now spent, so it stops being offered as raw material.
    assert client.get('/api/desk/signals?unconsumed=1').get_json() == []


def test_ledger_validation(client):
    assert client.post('/api/desk/ledger', json={'platform': 'x'}).status_code == 400
    assert client.post('/api/desk/ledger',
                       json={'platform': 'x', 'body': 'b',
                             'voice': 'marketing'}).status_code == 400


def test_outcome_roundtrip(client):
    pid = client.post('/api/desk/ledger', json={
        'platform': 'x', 'body': 'hello world'}).get_json()['id']
    r = client.post(f'/api/desk/ledger/{pid}/outcome', json={'likes': 9})
    assert r.get_json()['outcome'] == {'likes': 9}
    assert client.post('/api/desk/ledger/nope/outcome', json={}).status_code == 404


def test_repeat_check_catches_a_re_announcement(client):
    client.post('/api/desk/ledger', json={
        'platform': 'x',
        'body': 'Shipped drag-to-hire: grab an agent off the Floor and drop '
                'it on a project to hire it.'})
    r = client.post('/api/desk/repeat-check', json={
        'body': 'Shipped drag-to-hire: grab an agent off the Floor and drop '
                'it onto a project to hire it.'}).get_json()
    assert r['repeat'] is True and r['matches']

    clean = client.post('/api/desk/repeat-check', json={
        'body': 'Restore points now keep ten snapshots plus anything pinned'}).get_json()
    assert clean['repeat'] is False


def test_repeat_check_on_empty_body(client):
    assert client.post('/api/desk/repeat-check', json={'body': '  '}).get_json() == {
        'repeat': False, 'matches': []}


# -- overview -----------------------------------------------------------------

def test_overview_sums_pending_across_projects(client):
    d = client.get('/api/desk/overview').get_json()
    assert d['pending_drafts'] == 3, 'the Desk is cross-project by construction'
    assert d['voices'] == ['personal', 'product']
    assert d['running'] == 0


def test_overview_survives_a_broken_project_loader(client):
    def boom():
        raise RuntimeError('projects unreadable')
    desk_routes.wire(load_projects_fn=boom)
    d = client.get('/api/desk/overview').get_json()
    assert d['pending_drafts'] == 0, 'a bad loader degrades the count, not the page'


# -- the structural guarantee -------------------------------------------------

def test_there_is_no_publish_route(client):
    """If this test starts failing because someone added a publish route, the
    approval gate has stopped being structural — read the module docstring."""
    src = (PROJECT_ROOT / 'mc' / 'blueprints' / 'desk_routes.py').read_text(encoding='utf-8')
    for forbidden in ('requests.', 'urllib.request', 'httpx.', 'tweepy'):
        assert forbidden not in src
    assert client.post('/api/desk/publish', json={}).status_code == 404
