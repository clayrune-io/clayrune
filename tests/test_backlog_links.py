"""Backlog item KEYS (MC-01) and item-to-item LINKS (project_routes.py).

Backs backlog item f52e0d8d: an 8-char uuid slice is stable but unspeakable, so
until now nothing could point one item at another. The handle is a JIRA-style
key — a short per-project prefix, a dash, a sequential number — and `links` are
the relations built on it.

Three properties carry the design and all three are tested here:

1. **A key names exactly one item on the machine.** The prefix is unique across
   projects, so `MC-12` survives being pasted into a chat, an email, or another
   project's item — which a bare `#12` does not, and the cross-project backlog
   view puts several projects on screen at once by default.
2. **Numbers are never recycled.** `backlog_seq` is a high-water mark, so a
   deleted item's number is retired. If it were reused, every stored link to the
   old item would silently re-point at a different one — a link that quietly
   lies is worse than a link that is gone.
3. **Only one direction is persisted.** "A blocks B" is stored as A
   `blocked_by` B and the inverse is rendered client-side. A half-written pair
   is not representable, so the two directions cannot drift apart.

Fixture mirrors tests/test_project_routes.py (module-level patching, test-port
rule: patch mc.blueprints.project_routes.*, never server.*).
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


def _seed(client, pid='tproj', backlog=None, **extra):
    rec = {'id': pid, 'name': 'Test Project', 'status': 'active',
           'backlog': backlog if backlog is not None else [], **extra}
    (client.data_dir / f'{pid}.json').write_text(json.dumps(rec), encoding='utf-8')
    return rec


def _rec(client, pid='tproj'):
    return json.loads((client.data_dir / f'{pid}.json').read_text(encoding='utf-8'))


def _by_id(items, iid):
    return next(i for i in items if i['id'] == iid)


# ── key derivation ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('name,expected', [
    ('Mission Control', 'MC'),
    ('clayrune_website', 'CW'),
    ('engulfing-analyst', 'EA'),
    ('MarketReplay', 'MR'),          # camelCase humps count as words
    ('test', 'TES'),                 # single word -> leading letters
    ('', 'PRJ'),                     # nothing to work with
])
def test_key_derivation(name, expected):
    from mc.blueprints.project_routes import _derive_backlog_key
    assert _derive_backlog_key(name) == expected


def test_key_derivation_never_collides():
    """Property 1. Two projects sharing a prefix would make MC-12 mean two
    different items — the exact ambiguity the prefix exists to remove."""
    from mc.blueprints.project_routes import _derive_backlog_key
    assert _derive_backlog_key('Mission Control', {'MC'}) == 'MIS'
    assert _derive_backlog_key('Mission Control', {'MC', 'MIS'}) == 'MISS'


def test_key_is_derived_once_and_survives_a_rename(client):
    """Re-deriving on read would re-key every item the day a project is renamed,
    and every MC-12 already written down would stop resolving."""
    _seed(client, name='Mission Control')
    client.post('/api/project/tproj/backlog', json={'text': 'x'})
    assert _rec(client)['backlog_key'] == 'MC'

    rec = _rec(client)
    rec['name'] = 'Something Else Entirely'
    (client.data_dir / 'tproj.json').write_text(json.dumps(rec), encoding='utf-8')
    items = client.get('/api/project/tproj/backlog').get_json()
    assert _rec(client)['backlog_key'] == 'MC'
    assert items[0]['key'] == 'MC-01'


def test_changing_the_project_key_restamps_every_item(client):
    """The key is stored on the project AND denormalised onto each item, so a
    deliberate re-key has to reach the items or they'd render the old prefix."""
    _seed(client, name='Mission Control')
    client.post('/api/project/tproj/backlog', json={'text': 'x'})
    rec = _rec(client)
    rec['backlog_key'] = 'CTRL'
    (client.data_dir / 'tproj.json').write_text(json.dumps(rec), encoding='utf-8')
    items = client.get('/api/project/tproj/backlog').get_json()
    assert items[0]['key'] == 'CTRL-01'


# ── numbering ────────────────────────────────────────────────────────────────

def test_new_items_get_sequential_keys(client):
    _seed(client, name='Mission Control')
    keys = []
    for t in ('first', 'second', 'third'):
        r = client.post('/api/project/tproj/backlog', json={'text': t})
        assert r.status_code == 200
        keys.append(r.get_json()['item']['key'])
    # Zero-padded to 2, so a young backlog reads MC-01 and a column lines up.
    assert keys == ['MC-01', 'MC-02', 'MC-03']
    assert _rec(client)['backlog_seq'] == 3


def test_numbers_past_99_widen_rather_than_truncate(client):
    """Padding is a minimum, not a field width — clipping would collide two
    items on one key."""
    _seed(client, name='Mission Control', backlog_seq=99)
    r = client.post('/api/project/tproj/backlog', json={'text': 'hundredth'})
    assert r.get_json()['item']['key'] == 'MC-100'


def test_existing_items_backfilled_oldest_first_on_get(client):
    """Backfill is by created_at, not list order — the list is newest-first, but
    #1 should be the OLDEST item, the way a ticket number reads."""
    _seed(client, name='Mission Control', backlog=[
        {'id': 'c', 'text': 'newest', 'status': 'open', 'created_at': '2026-03-03T00:00:00Z'},
        {'id': 'a', 'text': 'oldest', 'status': 'open', 'created_at': '2026-01-01T00:00:00Z'},
        {'id': 'b', 'text': 'middle', 'status': 'open', 'created_at': '2026-02-02T00:00:00Z'},
    ])
    items = client.get('/api/project/tproj/backlog').get_json()
    assert (_by_id(items, 'a')['key'], _by_id(items, 'b')['key'],
            _by_id(items, 'c')['key']) == ('MC-01', 'MC-02', 'MC-03')
    # Persisted, not recomputed per request.
    assert _by_id(_rec(client)['backlog'], 'a')['num'] == 1


def test_backfill_is_idempotent_and_stable(client):
    _seed(client, backlog=[
        {'id': 'a', 'text': 'x', 'status': 'open', 'created_at': '2026-01-01T00:00:00Z'},
    ])
    first = client.get('/api/project/tproj/backlog').get_json()
    second = client.get('/api/project/tproj/backlog').get_json()
    assert first[0]['num'] == second[0]['num'] == 1
    assert _rec(client)['backlog_seq'] == 1


def test_backfill_adopts_existing_numbers_without_reissuing(client):
    """A project restored from a backup can already carry numbers. Handing one
    of them out again to a NEW item would collide two items on one handle."""
    _seed(client, backlog=[
        {'id': 'a', 'text': 'x', 'status': 'open', 'num': 7,
         'created_at': '2026-01-01T00:00:00Z'},
        {'id': 'b', 'text': 'y', 'status': 'open',
         'created_at': '2026-02-01T00:00:00Z'},
    ])
    items = client.get('/api/project/tproj/backlog').get_json()
    assert _by_id(items, 'a')['num'] == 7
    assert _by_id(items, 'b')['num'] == 8


def test_deleted_number_is_retired_not_recycled(client):
    """Property 2. Reuse would make every stored link to the old item silently
    point at a different one."""
    _seed(client)
    a = client.post('/api/project/tproj/backlog', json={'text': 'a'}).get_json()['item']
    b = client.post('/api/project/tproj/backlog', json={'text': 'b'}).get_json()['item']
    assert (a['num'], b['num']) == (1, 2)
    assert client.delete(f"/api/project/tproj/backlog/{b['id']}").status_code == 200
    c = client.post('/api/project/tproj/backlog', json={'text': 'c'}).get_json()['item']
    assert c['num'] == 3


# ── links ────────────────────────────────────────────────────────────────────

def _two_items(client):
    _seed(client, name='Mission Control')
    a = client.post('/api/project/tproj/backlog', json={'text': 'A'}).get_json()['item']
    b = client.post('/api/project/tproj/backlog', json={'text': 'B'}).get_json()['item']
    return a, b


@pytest.mark.parametrize('ltype', ['blocked_by', 'duplicate_of', 'continues', 'relates_to'])
def test_link_each_supported_type(client, ltype):
    a, b = _two_items(client)
    r = client.post(f"/api/project/tproj/backlog/{a['id']}/links",
                    json={'type': ltype, 'target': f"#{b['num']}"})
    assert r.status_code == 200
    links = r.get_json()['item']['links']
    assert links[0]['type'] == ltype and links[0]['target'] == b['id']


@pytest.mark.parametrize('form', ['{key}', '{keylower}', 'MC-0{num}',
                                  '#{num}', '{num}', '{id}'])
def test_target_accepts_key_hash_bare_number_and_raw_id(client, form):
    """Every one of these is a thing a person or an agent will actually type —
    the key as displayed, in lower case, over-padded, or the old bare number."""
    a, b = _two_items(client)
    target = form.format(num=b['num'], id=b['id'], key=b['key'],
                         keylower=b['key'].lower())
    r = client.post(f"/api/project/tproj/backlog/{a['id']}/links",
                    json={'type': 'relates_to', 'target': target})
    assert r.status_code == 200
    assert r.get_json()['item']['links'][0]['target'] == b['id']


def test_a_foreign_project_key_does_not_fall_through_to_the_number(client):
    """Property 1, enforced. 'CW-2' must NOT quietly resolve to this project's
    #2 — silently linking the wrong item is the confusion the prefix exists to
    prevent, and it would look like it worked."""
    a, b = _two_items(client)
    r = client.post(f"/api/project/tproj/backlog/{a['id']}/links",
                    json={'type': 'relates_to', 'target': 'CW-2'})
    assert r.status_code == 404
    assert 'this project' in r.get_json()['error']


def test_only_one_direction_is_persisted(client):
    """Property 3. The inverse is rendered from the list, never written — so the
    two halves of a pair cannot disagree."""
    a, b = _two_items(client)
    client.post(f"/api/project/tproj/backlog/{a['id']}/links",
                json={'type': 'blocked_by', 'target': f"#{b['num']}"})
    items = client.get('/api/project/tproj/backlog').get_json()
    assert len(_by_id(items, a['id'])['links']) == 1
    assert _by_id(items, b['id']).get('links') in (None, [])


def test_duplicate_link_is_a_noop_not_a_second_row(client):
    a, b = _two_items(client)
    body = {'type': 'blocked_by', 'target': f"#{b['num']}"}
    client.post(f"/api/project/tproj/backlog/{a['id']}/links", json=body)
    r = client.post(f"/api/project/tproj/backlog/{a['id']}/links", json=body)
    assert r.status_code == 200 and r.get_json().get('duplicate') is True
    assert len(_by_id(_rec(client)['backlog'], a['id'])['links']) == 1


def test_two_different_types_to_same_target_both_kept(client):
    a, b = _two_items(client)
    for t in ('blocked_by', 'relates_to'):
        client.post(f"/api/project/tproj/backlog/{a['id']}/links",
                    json={'type': t, 'target': f"#{b['num']}"})
    assert len(_by_id(_rec(client)['backlog'], a['id'])['links']) == 2


def test_link_rejects_self_unknown_type_and_missing_target(client):
    a, b = _two_items(client)
    assert client.post(f"/api/project/tproj/backlog/{a['id']}/links",
                       json={'type': 'blocked_by', 'target': a['key']}
                       ).status_code == 400
    assert client.post(f"/api/project/tproj/backlog/{a['id']}/links",
                       json={'type': 'eats', 'target': f"#{b['num']}"}
                       ).status_code == 400
    assert client.post(f"/api/project/tproj/backlog/{a['id']}/links",
                       json={'type': 'blocked_by', 'target': '#999'}
                       ).status_code == 404
    assert client.post("/api/project/tproj/backlog/zzz/links",
                       json={'type': 'blocked_by', 'target': f"#{b['num']}"}
                       ).status_code == 404
    assert client.post(f"/api/project/nope/backlog/{a['id']}/links",
                       json={'type': 'blocked_by', 'target': '#1'}
                       ).status_code == 404


def test_unlink_removes_only_the_named_type(client):
    a, b = _two_items(client)
    for t in ('blocked_by', 'relates_to'):
        client.post(f"/api/project/tproj/backlog/{a['id']}/links",
                    json={'type': t, 'target': f"#{b['num']}"})
    r = client.delete(f"/api/project/tproj/backlog/{a['id']}/links"
                      f"?type=blocked_by&target={b['key']}")
    assert r.status_code == 200
    remaining = _by_id(_rec(client)['backlog'], a['id'])['links']
    assert [l['type'] for l in remaining] == ['relates_to']


def test_unlink_404s_and_400s(client):
    a, b = _two_items(client)
    assert client.delete(
        f"/api/project/tproj/backlog/{a['id']}/links").status_code == 400
    assert client.delete(f"/api/project/tproj/backlog/{a['id']}/links"
                         f"?type=blocked_by&target=%23{b['num']}"
                         ).status_code == 404


def test_deleting_an_item_sweeps_inbound_links(client):
    """A dangling reference would re-attach if a number were ever recycled."""
    a, b = _two_items(client)
    client.post(f"/api/project/tproj/backlog/{a['id']}/links",
                json={'type': 'blocked_by', 'target': f"#{b['num']}"})
    assert client.delete(f"/api/project/tproj/backlog/{b['id']}").status_code == 200
    assert _by_id(_rec(client)['backlog'], a['id'])['links'] == []


# ── in_progress / blocked visibility ─────────────────────────────────────────
#
# Regression guard for a defect found while wiring links: /api/projects counted
# only status=='open', and the backlog tab rendered only 'open' or 'done'. An
# item moved to in_progress or blocked — both documented statuses the PATCH
# route accepts — therefore appeared in NO list and NO count. The work list
# silently dropped exactly the items being worked on. `_BACKLOG_CLOSED` inverts
# the test (closed-set, not open-set) so an unrecognised status shows up rather
# than vanishing.

@pytest.mark.parametrize('status', ['open', 'in_progress', 'blocked'])
def test_live_statuses_all_count_as_open_in_the_list_payload(client, status):
    _seed(client, backlog=[{'id': 'a', 'text': 'live one', 'status': status}])
    p = next(x for x in client.get('/api/projects').get_json() if x['id'] == 'tproj')
    assert p['backlog_open_count'] == 1
    assert p['backlog_done_count'] == 0
    assert p['backlog_next_text'] == 'live one'


@pytest.mark.parametrize('status', ['done', 'wontdo'])
def test_closed_statuses_do_not_count_as_open(client, status):
    _seed(client, backlog=[{'id': 'a', 'text': 'closed one', 'status': status}])
    p = next(x for x in client.get('/api/projects').get_json() if x['id'] == 'tproj')
    assert (p['backlog_open_count'], p['backlog_done_count']) == (0, 1)
    assert p['backlog_next_text'] == ''


def test_done_at_tracks_closure_not_the_literal_done_string(client):
    _seed(client, backlog=[{'id': 'a', 'text': 'x', 'status': 'open'}])
    r = client.patch('/api/project/tproj/backlog/a', json={'status': 'wontdo'})
    assert r.get_json()['item']['done_at']
    # Any live status clears it — an item that came back has no closure date.
    r = client.patch('/api/project/tproj/backlog/a', json={'status': 'in_progress'})
    assert r.get_json()['item']['done_at'] is None


# ── hand-setting the project key ─────────────────────────────────────────────

def test_project_key_can_be_set_by_hand_and_normalises(client):
    _seed(client, name='Mission Control')
    client.post('/api/project/tproj/backlog', json={'text': 'x'})
    r = client.post('/api/project/tproj', json={'backlog_key': ' cr-l '})
    assert r.status_code == 200
    assert _rec(client)['backlog_key'] == 'CRL'
    assert client.get('/api/project/tproj/backlog').get_json()[0]['key'] == 'CRL-01'


def test_project_key_rejects_bad_shapes(client):
    _seed(client, name='Mission Control')
    assert client.post('/api/project/tproj',
                       json={'backlog_key': '1MC'}).status_code == 400
    assert client.post('/api/project/tproj',
                       json={'backlog_key': 'WAYTOOLONGKEY'}).status_code == 400


def test_project_key_rejects_one_already_taken(client):
    """Two projects on one prefix would make MC-12 name two different items."""
    _seed(client, pid='other', name='Other', backlog_key='XY')
    _seed(client, name='Mission Control')
    r = client.post('/api/project/tproj', json={'backlog_key': 'xy'})
    assert r.status_code == 409
    assert 'already used' in r.get_json()['error']


def test_blank_key_hands_it_back_to_the_deriver(client):
    _seed(client, name='Mission Control')
    client.post('/api/project/tproj/backlog', json={'text': 'x'})
    client.post('/api/project/tproj', json={'backlog_key': 'CRL'})
    assert _rec(client)['backlog_key'] == 'CRL'
    assert client.post('/api/project/tproj',
                       json={'backlog_key': ''}).status_code == 200
    assert 'backlog_key' not in _rec(client)
    assert client.get('/api/project/tproj/backlog').get_json()[0]['key'] == 'MC-01'
