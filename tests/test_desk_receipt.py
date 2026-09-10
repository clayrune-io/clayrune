"""The receipt chain: released -> posted -> a ledger row.

WHY THIS FILE EXISTS. A 2026-09-10 design audit of the shipped Desk found that
`approve_social_queue_item` flipped the status to `approved` and nothing ever
called `desk.record_published`. The story ledger was written only by its own unit
tests. Three things were silently impossible as a result, and none of them threw:

  * the repetition guard (`similar_published`) had nothing to compare against, so
    it could never catch a re-announcement;
  * the Calendar and Ledger surfaces rendered their empty state forever;
  * there was no row for engagement to attach to, which blocks every reactions
    feature downstream.

The gap was invisible because every individual piece passed its own tests. These
tests assert the pieces are CONNECTED.
"""
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk  # noqa: E402
from mc.blueprints import project_routes  # noqa: E402


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(desk, 'SIGNALS_PATH', tmp_path / 'desk_signals.jsonl')

    sig = desk.append_signal('p', 'release', 'Shipped drag-to-hire', ref='abc123')
    project = {'id': 'p', 'social_queue': [{
        'id': 'd1', 'project_id': 'p', 'platform': 'x', 'voice': 'ron',
        'body': 'Shipped drag-to-hire today. Three days, two rewrites.',
        'signal_id': sig['id'], 'status': 'approved', 'originated': False,
    }]}
    monkeypatch.setattr(project_routes, 'load_project', lambda pid: project)
    monkeypatch.setattr(project_routes, 'save_project', lambda pid, d: None)

    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(project_routes.bp)
    c = app.test_client()
    c.project = project
    c.signal_id = sig['id']
    return c


def test_marking_posted_writes_a_ledger_row(ctx):
    r = ctx.post('/api/project/p/social/queue/d1/posted',
                 json={'url': 'https://x.com/RanLevi15/status/1'})
    assert r.status_code == 200
    body = r.get_json()
    assert body['ledger_written'] is True
    assert body['reactions_readable'] is True

    rows = desk.list_ledger()
    assert len(rows) == 1
    assert rows[0]['platform'] == 'x' and rows[0]['voice'] == 'ron'
    assert rows[0]['url'] == 'https://x.com/RanLevi15/status/1'
    assert rows[0]['signal_id'] == ctx.signal_id


def test_the_repetition_guard_can_now_actually_fire(ctx):
    """The whole point: before this route existed, `already_said` was always
    False because nothing ever populated the ledger."""
    assert desk.already_said('Shipped drag-to-hire today. Three days, two rewrites.') is False
    ctx.post('/api/project/p/social/queue/d1/posted', json={'url': 'https://x.com/a/1'})
    assert desk.already_said('Shipped drag-to-hire today. Three days, two rewrites.') is True


def test_the_signal_is_spent(ctx):
    assert desk.list_signals(unconsumed_only=True)
    ctx.post('/api/project/p/social/queue/d1/posted', json={'url': 'https://x.com/a/1'})
    assert desk.list_signals(unconsumed_only=True) == []


def test_marking_posted_is_idempotent(ctx):
    first = ctx.post('/api/project/p/social/queue/d1/posted',
                     json={'url': 'https://x.com/a/1'}).get_json()
    second = ctx.post('/api/project/p/social/queue/d1/posted',
                      json={'url': 'https://x.com/a/1'}).get_json()
    assert second['already'] is True
    assert second['post_id'] == first['post_id']
    assert len(desk.list_ledger()) == 1, 'a double-click must not double the ledger'


def test_a_pending_draft_cannot_route_around_approval(ctx):
    ctx.project['social_queue'][0]['status'] = 'pending'
    r = ctx.post('/api/project/p/social/queue/d1/posted', json={'url': 'https://x.com/a/1'})
    assert r.status_code == 409
    assert desk.list_ledger() == []


def test_no_permalink_still_records_but_says_so(ctx):
    """Honest degradation: the row exists, and the caller is told plainly that
    reactions can never be read for it."""
    body = ctx.post('/api/project/p/social/queue/d1/posted', json={}).get_json()
    assert body['ledger_written'] is True
    assert body['reactions_readable'] is False
    assert desk.list_ledger()[0]['url'] is None


def test_a_ledger_failure_never_leaves_the_queue_lying(ctx, monkeypatch):
    """The human DID post. Refusing the status because the ledger write failed
    would make the queue misreport the state of the world."""
    def boom(**kw):
        raise RuntimeError('ledger on fire')
    monkeypatch.setattr(desk, 'record_published', boom)

    r = ctx.post('/api/project/p/social/queue/d1/posted', json={'url': 'https://x.com/a/1'})
    assert r.status_code == 200
    assert r.get_json()['ledger_written'] is False
    assert ctx.project['social_queue'][0]['status'] == 'posted'


def test_missing_item_and_project(ctx, monkeypatch):
    assert ctx.post('/api/project/p/social/queue/nope/posted', json={}).status_code == 404
    monkeypatch.setattr(project_routes, 'load_project', lambda pid: None)
    assert ctx.post('/api/project/p/social/queue/d1/posted', json={}).status_code == 404


def test_this_route_does_not_publish():
    """It records that a human already posted. If it ever gains an outbound
    call it has become the thing the approval gate exists to prevent."""
    src = (PROJECT_ROOT / 'mc' / 'blueprints' / 'project_routes.py').read_text(encoding='utf-8')
    fn = src[src.index('def mark_social_queue_item_posted'):]
    fn = fn[:fn.index('@bp.route', 10)]
    for forbidden in ('requests.', 'urllib.request', 'httpx.', 'tweepy'):
        assert forbidden not in fn
