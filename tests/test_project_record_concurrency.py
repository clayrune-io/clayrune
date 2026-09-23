"""The project record is ONE document every subsystem writes — lost-update guard.

Traced 2026-09-22 (Desk plan step 0, docs/THE_DESK_SIMPLIFICATION_PLAN.md §5).
On 2026-09-10 session 7184503faba3 got HTTP 200 from
`POST /api/project/mission_control/social/queue` and the item was later absent
from every project's queue. No DELETE route and no pruning exists for
`social_queue`, so the only way an item can leave the file is a whole-record
overwrite: a writer that loaded `data/projects/<id>.json` BEFORE the POST and
saved it AFTER. The server runs `threaded=True`, so that interleave is real.

These tests pin the two halves of the fix:

  * `save_project` re-reads under a per-project lock and restores items a
    concurrent writer added, WITHOUT resurrecting ones this caller deleted;
  * `load_projects()` — not the `/api/projects` route — computes
    `social_pending_count`, which is what `/api/desk/overview` sums.
"""
import json
import sys
import threading
import types
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


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
    c.pr = pr
    return c


def _make_project(client, pid='proj1'):
    (client.data_dir / f'{pid}.json').write_text(
        json.dumps({'id': pid, 'name': 'Proj 1'}), encoding='utf-8')


def _queue(client, pid='proj1'):
    return client.get(f'/api/project/{pid}/social/queue').get_json()


# ── (A) the vanished draft ───────────────────────────────────────────────────

def test_queue_post_survives_a_concurrent_stale_project_save(client):
    """The exact 2026-09-10 interleave, run for real on two threads.

    Without the guard the second writer's save rewrites the whole record from a
    copy taken before the POST, and the draft is gone with a 200 already in the
    agent's transcript.
    """
    pr = client.pr
    _make_project(client)
    assert client.post('/api/project/proj1/social/queue',
                       json={'body': 'already queued', 'platform': 'x'}).status_code == 200

    loaded = threading.Event()
    posted = threading.Event()
    failure = []

    def stale_writer():
        # T0 — loads while the queue holds only 'already queued'.
        try:
            p = pr.load_project('proj1')
            loaded.set()
            assert posted.wait(10), 'POST never completed'
            # An unrelated mutation, the way _log_agent_activity makes one.
            p['activity_log'] = [{'ts': 'now', 'msg': 'agent did something'}]
            p['last_updated'] = 'now'
            # T3 — saves the whole record from the stale copy.
            pr.save_project('proj1', p)
        except Exception as e:  # pragma: no cover - surfaced by the assert below
            failure.append(e)

    t = threading.Thread(target=stale_writer, name='stale-writer')
    t.start()
    assert loaded.wait(10), 'writer thread never loaded the record'

    # T2 — the draft POST lands inside the other writer's window.
    r = client.post('/api/project/proj1/social/queue',
                    json={'body': 'the vanished draft', 'platform': 'x'})
    assert r.status_code == 200
    posted.set()
    t.join(10)
    assert not failure, failure
    assert not t.is_alive()

    bodies = [i['body'] for i in _queue(client)]
    assert 'the vanished draft' in bodies, 'the queue POST was silently overwritten'
    assert 'already queued' in bodies
    # The stale writer's own change still landed.
    on_disk = json.loads((client.data_dir / 'proj1.json').read_text(encoding='utf-8'))
    assert on_disk['activity_log'][0]['msg'] == 'agent did something'


def test_backlog_insert_survives_a_concurrent_stale_save(client):
    """Same guard, same record, a different collection — the queue is not special."""
    pr = client.pr
    _make_project(client)

    loaded = threading.Event()
    posted = threading.Event()

    def stale_writer():
        p = pr.load_project('proj1')
        loaded.set()
        posted.wait(10)
        p['current_task'] = 'something else'
        pr.save_project('proj1', p)

    t = threading.Thread(target=stale_writer)
    t.start()
    assert loaded.wait(10)
    assert client.post('/api/project/proj1/backlog',
                       json={'text': 'do the thing'}).status_code in (200, 201)
    posted.set()
    t.join(10)

    items = client.get('/api/project/proj1/backlog').get_json()
    texts = [i['text'] for i in (items if isinstance(items, list) else items.get('backlog', []))]
    assert 'do the thing' in texts


def test_a_deleted_item_is_not_resurrected(client):
    """The guard must restore ADDS, never undo DELETES.

    A blanket "anything on disk that I do not have, keep" would make every
    delete route a no-op. The baseline taken at load time is what tells the two
    apart: in my baseline and gone from my copy == I deleted it.
    """
    pr = client.pr
    _make_project(client)
    client.post('/api/project/proj1/social/queue', json={'body': 'keep me', 'platform': 'x'})
    client.post('/api/project/proj1/social/queue', json={'body': 'delete me', 'platform': 'x'})

    p = pr.load_project('proj1')
    doomed = next(i['id'] for i in p['social_queue'] if i['body'] == 'delete me')
    p['social_queue'] = [i for i in p['social_queue'] if i['id'] != doomed]
    pr.save_project('proj1', p)

    bodies = [i['body'] for i in _queue(client)]
    assert bodies == ['keep me']

    # And it stays deleted across a second save from the same thread — the
    # post-save baseline must replace the pre-save one.
    p2 = pr.load_project('proj1')
    p2['current_task'] = 'x'
    pr.save_project('proj1', p2)
    assert [i['body'] for i in _queue(client)] == ['keep me']


def test_uncontended_save_does_not_re_read_the_record(client, monkeypatch):
    """The guard costs one os.stat, not an 11 ms parse of a multi-MB record.

    Pinned as a test because the cheap version and the expensive version are
    behaviourally identical — nothing else would ever notice the regression.
    """
    pr = client.pr
    _make_project(client)
    client.post('/api/project/proj1/social/queue', json={'body': 'a', 'platform': 'x'})

    reads = []
    real = pr._read_record_raw
    monkeypatch.setattr(pr, '_read_record_raw',
                        lambda pid: (reads.append(pid), real(pid))[1])

    p = pr.load_project('proj1')
    p['current_task'] = 'no contention here'
    pr.save_project('proj1', p)
    assert reads == [], 'uncontended save re-parsed the record for nothing'


def test_saving_a_record_that_never_loaded_the_queue_keeps_it(client):
    """`/api/projects` pops `social_queue` off the dicts it trims. An absent key
    means "I am not carrying this", not "I emptied it" — writing such a record
    back must not erase the collection."""
    pr = client.pr
    _make_project(client)
    client.post('/api/project/proj1/social/queue', json={'body': 'still here', 'platform': 'x'})

    trimmed = pr.load_project('proj1')
    trimmed.pop('social_queue')
    trimmed['name'] = 'Renamed'
    pr.save_project('proj1', trimmed)

    assert [i['body'] for i in _queue(client)] == ['still here']


def test_derived_counts_never_reach_the_file(client):
    """`social_pending_count` is recomputed on every read. Persisting it would
    give the file a number that goes stale and is believed."""
    pr = client.pr
    _make_project(client)
    p = pr.load_project('proj1')
    p['social_pending_count'] = 99
    p['backlog_open_count'] = 99
    pr.save_project('proj1', p)

    raw = json.loads((client.data_dir / 'proj1.json').read_text(encoding='utf-8'))
    assert 'social_pending_count' not in raw
    assert 'backlog_open_count' not in raw


# ── (B) pending_drafts read 0 while drafts were pending ──────────────────────

def test_load_projects_computes_social_pending_count(client):
    """The key `/api/desk/overview` sums. It was set only inside the
    `/api/projects` route, so every other caller of load_projects() saw it
    absent and counted 0."""
    pr = client.pr
    _make_project(client)
    client.post('/api/project/proj1/social/queue', json={'body': 'a', 'platform': 'x'})
    client.post('/api/project/proj1/social/queue', json={'body': 'b', 'platform': 'x'})
    q = _queue(client)
    client.post(f"/api/project/proj1/social/queue/{q[0]['id']}/reject", json={'note': 'no'})

    rec = next(p for p in pr.load_projects() if p['id'] == 'proj1')
    assert rec['social_pending_count'] == 1


def test_desk_overview_pending_drafts_matches_the_real_queue(client, tmp_path):
    """End to end over the REAL load_projects, not a stub that pre-supplies the
    key — the stub in tests/test_desk_routes.py is why this shipped broken."""
    pr = client.pr
    from mc.blueprints import desk_routes

    _make_project(client, 'proj1')
    _make_project(client, 'proj2')
    for body in ('one', 'two'):
        client.post('/api/project/proj1/social/queue', json={'body': body, 'platform': 'x'})
    client.post('/api/project/proj2/social/queue',
                json={'body': 'three', 'platform': 'x', 'originated': False})
    # An approved draft is not a pending one.
    q2 = _queue(client, 'proj2')
    client.post(f"/api/project/proj2/social/queue/{q2[0]['id']}/approve", json={})

    real_pending = sum(
        1 for pid in ('proj1', 'proj2')
        for i in _queue(client, pid) if i['status'] == 'pending')
    assert real_pending == 2

    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=pr.load_projects,
        load_project_fn=pr.load_project,
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
    )
    app.register_blueprint(desk_routes.bp)
    overview = app.test_client().get('/api/desk/overview').get_json()
    assert overview['pending_drafts'] == real_pending
