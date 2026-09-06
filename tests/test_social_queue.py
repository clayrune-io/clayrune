"""Social approvals queue (project_routes.py) — Phase 1.

Store shape mirrors the backlog: a `social_queue` list on the project dict,
persisted through the same load_project/save_project pair (per CLAUDE.md's
DATA_DIR rule, this is a field on the project record, NOT a second sidecar
file — the plan this ships from called for a standalone
`data/social_queue.json`, which would have needed a new
EXCLUDED_SIDECAR_SUFFIXES entry for no benefit over reusing the existing
pattern).

Phase 1 stops at "approved" — there is no /post route and nothing here makes
an outbound network call. The one piece of real logic is the attribution
guard: approve() must refuse an `originated: true` draft whose body is
missing the exact line AGENT_RULES.md requires, and must allow it once the
line is present. Fixture mirrors tests/test_backlog_links.py.
"""
import sys
import threading
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ATTRIBUTION_LINE = 'Written by me - Edited by Claude'


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers the blueprint on first import)
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


def _make_project(client, pid='proj1'):
    (client.data_dir / f'{pid}.json').write_text('{"id": "%s", "name": "Proj 1"}' % pid,
                                                   encoding='utf-8')


def test_queue_starts_empty(client):
    _make_project(client)
    res = client.get('/api/project/proj1/social/queue')
    assert res.status_code == 200
    assert res.get_json() == []


def test_post_creates_pending_draft(client):
    _make_project(client)
    res = client.post('/api/project/proj1/social/queue', json={
        'platform': 'discord', 'body': 'hello world', 'originated': True,
    })
    assert res.status_code == 200
    item = res.get_json()['item']
    assert item['status'] == 'pending'
    assert item['platform'] == 'discord'
    assert item['originated'] is True
    assert item['id']

    listing = client.get('/api/project/proj1/social/queue').get_json()
    assert len(listing) == 1
    assert listing[0]['id'] == item['id']


def test_post_requires_body(client):
    _make_project(client)
    res = client.post('/api/project/proj1/social/queue', json={'platform': 'discord'})
    assert res.status_code == 400


def test_patch_edits_body(client):
    _make_project(client)
    item = client.post('/api/project/proj1/social/queue',
                        json={'platform': 'discord', 'body': 'draft one'}).get_json()['item']
    res = client.patch(f"/api/project/proj1/social/queue/{item['id']}", json={'body': 'draft two'})
    assert res.status_code == 200
    assert res.get_json()['item']['body'] == 'draft two'


def test_attribution_guard_blocks_then_allows(client):
    """The finding this ticket exists to prove: an originated draft missing the
    attribution line is refused on approve, and the SAME item is approved once
    the line is added — the guard checks the current body, not a cached copy.
    """
    _make_project(client)
    item = client.post('/api/project/proj1/social/queue', json={
        'platform': 'linkedin', 'body': 'Shipped a new feature today.', 'originated': True,
    }).get_json()['item']

    blocked = client.post(f"/api/project/proj1/social/queue/{item['id']}/approve")
    assert blocked.status_code == 400
    assert ATTRIBUTION_LINE in blocked.get_json()['error']
    # Refusal must not have flipped the status.
    still_pending = client.get('/api/project/proj1/social/queue').get_json()[0]
    assert still_pending['status'] == 'pending'

    client.patch(f"/api/project/proj1/social/queue/{item['id']}",
                 json={'body': f"Shipped a new feature today.\n\n{ATTRIBUTION_LINE}"})

    allowed = client.post(f"/api/project/proj1/social/queue/{item['id']}/approve")
    assert allowed.status_code == 200
    assert allowed.get_json()['item']['status'] == 'approved'
    assert allowed.get_json()['item']['decided_at']


def test_attribution_guard_exempts_replies(client):
    _make_project(client)
    item = client.post('/api/project/proj1/social/queue', json={
        'platform': 'reddit', 'body': 'Thanks for the feedback!', 'originated': False,
    }).get_json()['item']
    res = client.post(f"/api/project/proj1/social/queue/{item['id']}/approve")
    assert res.status_code == 200
    assert res.get_json()['item']['status'] == 'approved'


def test_reject_takes_note_and_pushes_back(client):
    _make_project(client)
    item = client.post('/api/project/proj1/social/queue',
                        json={'platform': 'discord', 'body': 'draft', 'originated': False}).get_json()['item']
    res = client.post(f"/api/project/proj1/social/queue/{item['id']}/reject",
                       json={'note': 'wrong tone, try again'})
    assert res.status_code == 200
    updated = res.get_json()['item']
    assert updated['status'] == 'needs_changes'
    assert updated['note'] == 'wrong tone, try again'
    assert updated['decided_at']


def test_no_post_endpoint_exists(client):
    """Phase 1 scope guard: there must be nothing outbound to call."""
    _make_project(client)
    item = client.post('/api/project/proj1/social/queue',
                        json={'platform': 'discord', 'body': 'draft', 'originated': False}).get_json()['item']
    res = client.post(f"/api/project/proj1/social/queue/{item['id']}/post")
    assert res.status_code == 404
