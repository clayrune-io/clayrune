"""Desk v1 R1-W S4 - the piece store (plan M13-M18, M21, M22).

Pinned:

  * pieces live in `desk.json` (a sibling of DATA_DIR), come back in the v1 family
    shape from M1 (`/api/desk/workspace`) and from M13, and a deleted campaign
    takes its pieces with it;
  * a campaign may hold several pieces of one kind, and a piece several assets;
  * M18 can NEVER set approved/scheduled/sending/submitted/verified_published (the
    approval gate is a different route), and a version that already carries an
    approval cannot have its body changed behind it, while sending-or-later
    versions are immutable and their piece cannot be deleted (409);
  * leaving `approved`/`scheduled` clears the approval stamp;
  * an asset's path must stay under `data/uploads`: `..`, absolute paths outside
    and symlinks out are refused, and the answer carries a /api/serve-image `src`;
  * an upload saves into the material library's Uploads folder, is refused by
    type and by size, and leaves no file behind when refused;
  * M22 lists library folders (with their files), other campaigns' articles, and
    says `online` is empty rather than inventing sources;
  * demo data never reaches the store: nothing here reads the fixtures.
"""
import io
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
from mc import desk_pieces as _pieces  # noqa: E402

PROJECTS = [{'id': 'alpha', 'name': 'Alpha'}]
CID = 'camp-p'
PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 64


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_routes, '_require_human_passcode', lambda data: None)
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: PROJECTS,
        load_project_fn=lambda pid: next((p for p in PROJECTS if p['id'] == pid), None),
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
        uploads_root=uploads,
    )
    app.register_blueprint(desk_routes.bp)
    client = app.test_client()
    r = client.post('/api/desk/campaigns?shape=v1', json={
        'id': CID, 'state': 'draft', 'projectId': 'alpha', 'rules': {},
        'map': {'stop': 'what', 'done': []},
        'plan': {'brief': 'b', 'title': 'Pieces', 'accounts': [], 'cadence': {}, 'end': {}}})
    assert r.status_code == 201, r.get_json()
    _desk.upsert_presence('alpha', {'accounts': [
        {'channel_id': 'ch-x', 'platform': 'x', 'identity': '@a'},
        {'channel_id': 'ch-li', 'platform': 'linkedin', 'identity': 'A'}]})
    return client, uploads, tmp_path


def _piece(client, kind='post', **kw):
    body = dict({'campaign_id': CID, 'kind': kind, 'title': f'A {kind}'}, **kw)
    r = client.post('/api/desk/pieces', json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _version(client, pid, account='ch-x', **kw):
    r = client.post(f'/api/desk/pieces/{pid}/versions', json=dict({'account_id': account}, **kw))
    assert r.status_code == 201, r.get_json()
    return r.get_json()['versions'][-1]


def _put_file(uploads, rel, data=PNG):
    p = uploads / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


# -- store + shape ------------------------------------------------------------

def test_create_returns_family_shape_and_lands_in_workspace(env):
    client, _, tmp = env
    p = _piece(client, 'article', id='fam-mine', title='Hello', word_count=640)
    assert p['id'] == 'fam-mine' and p['campaignId'] == CID and p['projectId'] == 'alpha'
    assert p['kind'] == 'article' and p['title'] == 'Hello' and p['wordCount'] == 640
    assert p['assets'] == [] and p['versions'] == []
    ws = client.get('/api/desk/workspace').get_json()
    assert [x['id'] for x in ws['pieces']] == ['fam-mine']
    # outside DATA_DIR: it is in desk.json, not a project record
    assert 'fam-mine' in json.loads((tmp / 'desk.json').read_text(encoding='utf-8'))['pieces']


def test_unknown_campaign_kind_and_duplicate_id(env):
    client, _, _ = env
    assert client.post('/api/desk/pieces', json={'campaign_id': 'nope', 'kind': 'post'}).status_code == 404
    assert client.post('/api/desk/pieces', json={'campaign_id': CID, 'kind': 'tweet'}).status_code == 400
    _piece(client, id='dup')
    r = client.post('/api/desk/pieces', json={'campaign_id': CID, 'kind': 'post', 'id': 'dup'})
    assert r.status_code == 409
    assert client.post('/api/desk/pieces', json={'campaign_id': CID, 'kind': 'post', 'id': '../x'}).status_code == 400


def test_several_pieces_of_one_kind_in_one_campaign(env):
    client, _, _ = env
    ids = {_piece(client, 'video')['id'] for _ in range(3)}
    assert len(ids) == 3
    got = client.get(f'/api/desk/pieces?campaign_id={CID}').get_json()
    assert len(got) == 3 and all(p['kind'] == 'video' for p in got)
    assert client.get('/api/desk/pieces?campaign_id=other').get_json() == []


def test_patch_piece_allowlist_and_validation(env):
    client, _, _ = env
    p = _piece(client)
    r = client.patch(f"/api/desk/pieces/{p['id']}", json={'title': 'New', 'word_count': 12,
                     'source': {'kind': 'existing', 'ref': 'x'}, 'draft_status': 'in_review'})
    out = r.get_json()
    assert r.status_code == 200 and out['title'] == 'New' and out['wordCount'] == 12
    assert out['source'] == {'kind': 'existing', 'ref': 'x'} and out['draft'] == {'status': 'in_review'}
    for bad in ({'kind': 'video'}, {'campaign_id': 'x'}, {'id': 'y'}, {'title': ''},
                {'word_count': -1}, {'draft_status': 'sent'}):
        assert client.patch(f"/api/desk/pieces/{p['id']}", json=bad).status_code == 400, bad
    assert client.patch('/api/desk/pieces/none', json={'title': 'x'}).status_code == 404


def test_deleting_a_campaign_deletes_its_pieces(env):
    client, _, _ = env
    _piece(client)
    assert client.delete(f'/api/desk/campaigns/{CID}').status_code == 200
    assert client.get('/api/desk/pieces').get_json() == []
    assert client.get('/api/desk/workspace').get_json()['pieces'] == []


# -- versions -----------------------------------------------------------------

def test_add_version_needs_a_known_account_and_one_per_account(env):
    client, _, _ = env
    p = _piece(client)
    v = _version(client, p['id'], body='hi')
    assert v['state'] == 'drafting' and v['channelId'] == 'ch-x' and v['body'] == 'hi' and v['revision'] == 0
    r = client.post(f"/api/desk/pieces/{p['id']}/versions", json={'account_id': 'ch-x'})
    assert r.status_code == 409
    r = client.post(f"/api/desk/pieces/{p['id']}/versions", json={'account_id': 'ch-ghost'})
    assert r.status_code == 400
    # an archived version frees its account
    client.patch(f"/api/desk/pieces/{p['id']}/versions/{v['id']}", json={'state': 'archived'})
    assert client.post(f"/api/desk/pieces/{p['id']}/versions", json={'account_id': 'ch-x'}).status_code == 201


@pytest.mark.parametrize('state', ['approved', 'scheduled', 'sending', 'submitted',
                                   'verified_published', 'failed', 'held', 'blocked', 'bogus'])
def test_patch_can_never_set_an_approval_or_send_state(env, state):
    client, _, _ = env
    p = _piece(client)
    v = _version(client, p['id'])
    r = client.patch(f"/api/desk/pieces/{p['id']}/versions/{v['id']}", json={'state': state})
    assert r.status_code == 400
    got = client.get(f"/api/desk/pieces?campaign_id={CID}").get_json()[0]['versions'][0]
    assert got['state'] == 'drafting' and got['approved'] is None


@pytest.mark.parametrize('state', ['needs_review', 'planned', 'skipped', 'archived', 'drafting'])
def test_patch_can_set_the_writable_states(env, state):
    client, _, _ = env
    p = _piece(client)
    v = _version(client, p['id'])
    r = client.patch(f"/api/desk/pieces/{p['id']}/versions/{v['id']}", json={'state': state})
    assert r.status_code == 200 and r.get_json()['versions'][0]['state'] == state


def test_version_fields_and_schedule(env):
    client, _, _ = env
    p = _piece(client)
    v = _version(client, p['id'])
    r = client.patch(f"/api/desk/pieces/{p['id']}/versions/{v['id']}",
                     json={'body': 'text', 'revision': 2, 'scheduled_at': '2026-10-05T10:00:00-07:00', 'format': '9:16'})
    out = r.get_json()['versions'][0]
    assert (out['body'], out['revision'], out['publishAt'], out['format']) == ('text', 2, '2026-10-05T10:00:00-07:00', '9:16')
    for bad in ({'scheduled_at': 'tomorrow'}, {'revision': -1}, {'revision': 'x'}, {'nope': 1}, {'account_id': 'ch-ghost'}):
        assert client.patch(f"/api/desk/pieces/{p['id']}/versions/{v['id']}", json=bad).status_code == 400, bad
    r = client.patch(f"/api/desk/pieces/{p['id']}/versions/{v['id']}", json={'scheduled_at': None})
    assert 'publishAt' not in r.get_json()['versions'][0]
    assert client.patch(f"/api/desk/pieces/{p['id']}/versions/ghost", json={'state': 'planned'}).status_code == 404


def _force(tmp, pid, vid, **fields):
    """Put a version into a state only the (future) human approve route / tick can
    set, straight in the store, to test what the PATCH does around it."""
    path = tmp / 'desk.json'
    store = json.loads(path.read_text(encoding='utf-8'))
    ver = next(v for v in store['pieces'][pid]['versions'] if v['id'] == vid)
    ver.update(fields)
    path.write_text(json.dumps(store), encoding='utf-8')


def test_approved_text_cannot_change_behind_the_approval(env):
    client, _, tmp = env
    p = _piece(client)
    v = _version(client, p['id'], body='approved text')
    _force(tmp, p['id'], v['id'], state='approved', approved={'at': 'x', 'by': 'human'})
    url = f"/api/desk/pieces/{p['id']}/versions/{v['id']}"
    for bad in ({'body': 'sneaky'}, {'format': '1:1'}):
        assert client.patch(url, json=bad).status_code == 409, bad
    # the time may move while approved
    assert client.patch(url, json={'scheduled_at': '2026-10-06T09:00:00Z'}).status_code == 200
    # going back to review clears the stamp, then the body is editable
    r = client.patch(url, json={'state': 'needs_review'})
    out = r.get_json()['versions'][0]
    assert out['state'] == 'needs_review' and out['approved'] is None
    assert client.patch(url, json={'body': 'now ok'}).status_code == 200


def test_skipping_an_approved_version_clears_the_approval(env):
    client, _, tmp = env
    p = _piece(client)
    v = _version(client, p['id'])
    _force(tmp, p['id'], v['id'], state='scheduled', approved={'at': 'x', 'by': 'human'})
    r = client.patch(f"/api/desk/pieces/{p['id']}/versions/{v['id']}", json={'state': 'skipped'})
    out = r.get_json()['versions'][0]
    assert out['state'] == 'skipped' and out['approved'] is None


# -- R1-W S6: When sets times, it never approves or places ------------------------

def test_scheduling_a_time_is_not_approval(env):
    client, _, _ = env
    p = _piece(client)
    v = _version(client, p['id'])
    url = f"/api/desk/pieces/{p['id']}/versions/{v['id']}"
    assert client.patch(url, json={'state': 'planned'}).status_code == 200
    out = client.patch(url, json={'scheduled_at': '2026-10-05T10:00:00-07:00'}).get_json()['versions'][0]
    assert (out['state'], out['approved'], out['publishAt']) == ('planned', None, '2026-10-05T10:00:00-07:00')
    # asking for the approved/scheduled state alongside the time is refused whole: no time lands either
    r = client.patch(url, json={'scheduled_at': '2026-10-06T10:00:00-07:00', 'state': 'scheduled'})
    assert r.status_code == 400
    got = client.get(f'/api/desk/pieces?campaign_id={CID}').get_json()[0]['versions'][0]
    assert (got['state'], got['approved'], got['publishAt']) == ('planned', None, '2026-10-05T10:00:00-07:00')


def test_an_approved_version_keeps_its_approval_through_a_time_change(env):
    client, _, tmp = env
    p = _piece(client)
    v = _version(client, p['id'])
    _force(tmp, p['id'], v['id'], state='scheduled', approved={'at': 'x', 'by': 'human'},
           scheduled_at='2026-10-05T10:00:00-07:00')
    url = f"/api/desk/pieces/{p['id']}/versions/{v['id']}"
    out = client.patch(url, json={'scheduled_at': '2026-10-05T16:00:00-07:00'}).get_json()['versions'][0]
    assert (out['state'], out['approved']['by'], out['publishAt']) == ('scheduled', 'human', '2026-10-05T16:00:00-07:00')
    # moving it to another day goes back through review in the same PATCH: time + needs_review, approval cleared
    out = client.patch(url, json={'scheduled_at': '2026-10-08T09:00:00-07:00', 'state': 'needs_review'}).get_json()['versions'][0]
    assert (out['state'], out['approved'], out['publishAt']) == ('needs_review', None, '2026-10-08T09:00:00-07:00')
    # the approval does not come back by moving the time again
    out = client.patch(url, json={'scheduled_at': None}).get_json()['versions'][0]
    assert out['state'] == 'needs_review' and out['approved'] is None and 'publishAt' not in out


@pytest.mark.parametrize('state', ['sending', 'submitted', 'verified_published', 'you_reported',
                                   'unknown_outcome', 'failed'])
def test_a_version_handed_to_a_platform_cannot_be_rescheduled(env, state):
    client, _, tmp = env
    p = _piece(client)
    v = _version(client, p['id'])
    _force(tmp, p['id'], v['id'], state=state, scheduled_at='2026-10-05T10:00:00-07:00')
    r = client.patch(f"/api/desk/pieces/{p['id']}/versions/{v['id']}", json={'scheduled_at': '2026-10-09T10:00:00-07:00'})
    assert r.status_code == 409
    got = client.get(f'/api/desk/pieces?campaign_id={CID}').get_json()[0]['versions'][0]
    assert got['publishAt'] == '2026-10-05T10:00:00-07:00'


def _when(client, when):
    return client.patch(f'/api/desk/campaigns/{CID}?shape=v1', json={'when': when})


def _workspace_campaign(client):
    return next(c for c in client.get('/api/desk/workspace').get_json()['campaigns'] if c['id'] == CID)


def test_campaign_when_round_trips_slots_and_a_fill(env):
    client, _, _ = env
    p = _piece(client)
    v = _version(client, p['id'], account='ch-x')
    when = {'slots': [
        {'id': 's1', 'at': '2026-10-05T14:00:00-07:00', 'origin': 'user',
         'filled': {'title': 'A post', 'platform': 'x', 'channelId': 'ch-x', 'versionId': v['id']}},
        {'id': 's2', 'at': '2026-10-06T09:00:00-07:00', 'origin': 'agent', 'state': 'suggested', 'because': ['f1']},
    ]}
    r = _when(client, when)
    assert r.status_code == 200, r.get_json()
    camp = _workspace_campaign(client)
    assert camp['when'] == when
    # a slot only reserves a time: it did not touch the version or the campaign's state
    got = client.get(f'/api/desk/pieces?campaign_id={CID}').get_json()[0]['versions'][0]
    assert got['state'] == 'drafting' and got['channelId'] == 'ch-x' and 'publishAt' not in got
    assert camp['state'] == 'draft'


@pytest.mark.parametrize('when', [
    'tomorrow',
    {'slots': 'x'},
    {'slots': [], 'state': 'approved'},
    {'slots': [{'id': 's', 'at': 'soon', 'origin': 'user'}]},
    {'slots': [{'id': 's', 'at': '2026-10-05T14:00:00Z', 'origin': 'robot'}]},
    {'slots': [{'id': 's', 'at': '2026-10-05T14:00:00Z', 'origin': 'user', 'state': 'approved'}]},
    {'slots': [{'id': 's', 'at': '2026-10-05T14:00:00Z', 'origin': 'user', 'version': {}}]},
    {'slots': [{'id': 's', 'at': '2026-10-05T14:00:00Z', 'origin': 'user'},
               {'id': 's', 'at': '2026-10-06T14:00:00Z', 'origin': 'user'}]},
    {'slots': [{'id': 's', 'at': '2026-10-05T14:00:00Z', 'origin': 'user', 'filled': {'versionId': 'ghost'}}]},
])
def test_campaign_when_refuses_what_it_does_not_own(env, when):
    client, _, _ = env
    assert _when(client, when).status_code == 400, when
    assert not (_workspace_campaign(client).get('when') or {}).get('slots')


def test_a_slot_cannot_move_a_version_to_another_account(env):
    client, _, _ = env
    p = _piece(client)
    v = _version(client, p['id'], account='ch-x')
    r = _when(client, {'slots': [{'id': 's', 'at': '2026-10-05T14:00:00Z', 'origin': 'agent',
                                  'filled': {'versionId': v['id'], 'channelId': 'ch-li'}}]})
    assert r.status_code == 400 and 'move a message' in r.get_json()['error']
    got = client.get(f'/api/desk/pieces?campaign_id={CID}').get_json()[0]['versions']
    assert [x['channelId'] for x in got] == ['ch-x']


@pytest.mark.parametrize('state', ['sending', 'submitted', 'verified_published', 'you_reported',
                                   'unknown_outcome', 'failed'])
def test_sent_versions_are_immutable_and_pin_their_piece(env, state):
    client, _, tmp = env
    p = _piece(client)
    v = _version(client, p['id'], body='out the door')
    _force(tmp, p['id'], v['id'], state=state)
    url = f"/api/desk/pieces/{p['id']}/versions/{v['id']}"
    assert client.patch(url, json={'body': 'x'}).status_code == 409
    assert client.patch(url, json={'state': 'archived'}).status_code == 409
    r = client.delete(f"/api/desk/pieces/{p['id']}")
    assert r.status_code == 409 and v['id'] in r.get_json()['error']
    assert client.patch(f"/api/desk/pieces/{p['id']}", json={'body': 'rewrite'}).status_code == 409
    assert client.get('/api/desk/pieces').get_json()  # still there


def test_delete_unsent_piece(env):
    client, _, _ = env
    p = _piece(client)
    _version(client, p['id'])
    assert client.delete(f"/api/desk/pieces/{p['id']}").status_code == 200
    assert client.delete(f"/api/desk/pieces/{p['id']}").status_code == 404


def test_claims_state_and_verdicts(env):
    client, _, _ = env
    p = _piece(client, 'article', claims=[{'id': 'c1', 'text': 'keeps ten snapshots', 'source': None},
                                          {'id': 'c2', 'text': 'sourced', 'source': 'docs'}])
    v = _version(client, p['id'])
    got = client.get('/api/desk/pieces').get_json()[0]['versions'][0]['claims']
    assert [(c['id'], c['verdict']) for c in got] == [('c1', 'blocked'), ('c2', 'ok')]
    url = f"/api/desk/pieces/{p['id']}/versions/{v['id']}"
    assert client.patch(url, json={'claims_state': {'zzz': {'status': 'accepted'}}}).status_code == 400
    assert client.patch(url, json={'claims_state': {'c1': {'status': 'maybe'}}}).status_code == 400
    r = client.patch(url, json={'claims_state': {'c1': {'status': 'edited', 'revised_text': 'keeps snapshots', 'revision': 1}}})
    c1 = r.get_json()['versions'][0]['claims'][0]
    assert c1['verdict'] == 'ok' and c1['text'] == 'keeps snapshots'


# -- assets -------------------------------------------------------------------

def test_a_piece_carries_several_assets_with_serve_image_src(env):
    client, uploads, _ = env
    _put_file(uploads, 'desk/library/image/Shots/a.png')
    _put_file(uploads, 'desk/library/image/Shots/b.png')
    p = _piece(client)
    for name in ('a.png', 'b.png'):
        r = client.post(f"/api/desk/pieces/{p['id']}/assets", json={'path': f'desk/library/image/Shots/{name}'})
        assert r.status_code == 201, r.get_json()
    out = client.get('/api/desk/pieces').get_json()[0]
    assert [a['title'] for a in out['assets']] == ['a.png', 'b.png']
    a = out['assets'][0]
    assert a['kind'] == 'image' and a['path'] == 'desk/library/image/Shots/a.png'
    assert a['src'].startswith('/api/serve-image?path=') and 'a.png' in a['src']


def test_asset_path_must_stay_under_uploads(env, tmp_path):
    client, uploads, tmp = env
    outside = tmp / 'secret.png'
    outside.write_bytes(PNG)
    p = _piece(client)
    url = f"/api/desk/pieces/{p['id']}/assets"
    for path in ('../secret.png', str(outside), 'desk/../../secret.png', '', None, 'nope.png'):
        r = client.post(url, json={'path': path})
        assert r.status_code in (400, 404), (path, r.get_json())
    assert client.get('/api/desk/pieces').get_json()[0]['assets'] == []
    # not an allowed type
    _put_file(uploads, 'desk/x.exe', b'MZ')
    assert client.post(url, json={'path': 'desk/x.exe'}).status_code == 400


def test_asset_symlink_out_of_uploads_is_refused(env):
    client, uploads, tmp = env
    outside = tmp / 'outside.png'
    outside.write_bytes(PNG)
    link = uploads / 'desk' / 'link.png'
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip('symlinks not permitted on this machine')
    p = _piece(client)
    assert client.post(f"/api/desk/pieces/{p['id']}/assets", json={'path': 'desk/link.png'}).status_code == 400


def test_remove_asset_keeps_the_file(env):
    client, uploads, _ = env
    f = _put_file(uploads, 'desk/library/image/Shots/a.png')
    p = _piece(client)
    a = client.post(f"/api/desk/pieces/{p['id']}/assets", json={'path': 'desk/library/image/Shots/a.png', 'id': 'asset-1'}).get_json()['assets'][0]
    r = client.delete(f"/api/desk/pieces/{p['id']}/assets/{a['id']}")
    assert r.status_code == 200 and r.get_json()['assets'] == []
    assert f.exists()
    assert client.delete(f"/api/desk/pieces/{p['id']}/assets/{a['id']}").status_code == 404


def test_upload_lands_in_the_library_and_is_attached(env):
    client, uploads, _ = env
    p = _piece(client)
    r = client.post(f"/api/desk/pieces/{p['id']}/assets", data={'file': (io.BytesIO(PNG), 'My Shot!.png')},
                    content_type='multipart/form-data')
    assert r.status_code == 201, r.get_json()
    a = r.get_json()['assets'][0]
    assert a['path'].startswith('desk/library/image/Uploads/') and a['path'].endswith('.png')
    assert (uploads / a['path']).read_bytes() == PNG
    assert a['title'] == 'My Shot!.png'
    # a second upload of the same name does not overwrite the first
    r2 = client.post(f"/api/desk/pieces/{p['id']}/assets", data={'file': (io.BytesIO(PNG), 'My Shot!.png')},
                     content_type='multipart/form-data')
    assert r2.get_json()['assets'][1]['path'] != a['path']


def test_refused_uploads_leave_no_file(env, monkeypatch):
    client, uploads, _ = env
    p = _piece(client)
    url = f"/api/desk/pieces/{p['id']}/assets"
    r = client.post(url, data={'file': (io.BytesIO(b'MZ'), 'virus.exe')}, content_type='multipart/form-data')
    assert r.status_code == 400
    r = client.post(url, data={'file': (io.BytesIO(b''), 'empty.png')}, content_type='multipart/form-data')
    assert r.status_code == 400
    monkeypatch.setitem(_pieces.MAX_UPLOAD_BYTES, 'image', 10)
    r = client.post(url, data={'file': (io.BytesIO(PNG), 'big.png')}, content_type='multipart/form-data')
    assert r.status_code == 413
    r = client.post('/api/desk/pieces/ghost/assets', data={'file': (io.BytesIO(PNG), 'x.png')}, content_type='multipart/form-data')
    assert r.status_code == 404
    folder = uploads / 'desk' / 'library'
    assert not folder.exists() or not [f for f in folder.rglob('*') if f.is_file()]


# -- Studio saves into the library, attached to nothing (MC-1024) ------------------

def test_studio_save_lands_in_the_library_with_no_piece_and_is_listed(env):
    client, uploads, _ = env
    before = client.get('/api/desk/pieces').get_json()
    r = client.post('/api/desk/materials', data={'file': (io.BytesIO(PNG), 'Hero art.png'), 'title': 'Hero art'},
                    content_type='multipart/form-data')
    assert r.status_code == 201, r.get_json()
    item = r.get_json()
    assert item['kind'] == 'image' and item['title'] == 'Hero art'
    assert item['path'].startswith('desk/library/image/Studio/') and item['path'].endswith('.png')
    assert item['id'] == item['path'] and item['src'].startswith('/api/serve-image?path=')
    assert (uploads / item['path']).read_bytes() == PNG
    # no piece, no campaign touched
    assert client.get('/api/desk/pieces').get_json() == before
    # the What source picker reads M22 for ANY campaign, and finds it
    for cid in (CID, 'camp-2', None):
        out = client.get('/api/desk/materials' + (f'?campaign_id={cid}' if cid else '')).get_json()
        folder = next(f for f in out['library']['image'] if f['title'] == 'Studio')
        assert [i['path'] for i in folder['items']] == [item['path']]


def test_studio_save_refusals_leave_no_file(env, monkeypatch):
    client, uploads, _ = env
    url = '/api/desk/materials'
    assert client.post(url, data={}, content_type='multipart/form-data').status_code == 400
    r = client.post(url, data={'file': (io.BytesIO(b'MZ'), 'virus.exe')}, content_type='multipart/form-data')
    assert r.status_code == 400
    r = client.post(url, data={'file': (io.BytesIO(b''), 'empty.png')}, content_type='multipart/form-data')
    assert r.status_code == 400
    monkeypatch.setitem(_pieces.MAX_UPLOAD_BYTES, 'image', 10)
    r = client.post(url, data={'file': (io.BytesIO(PNG), 'big.png')}, content_type='multipart/form-data')
    assert r.status_code == 413
    folder = uploads / 'desk' / 'library'
    assert not folder.exists() or not [f for f in folder.rglob('*') if f.is_file()]


# -- materials ----------------------------------------------------------------

def test_materials_lists_folders_files_articles_and_no_invented_online(env):
    client, uploads, _ = env
    _put_file(uploads, 'desk/library/image/Screenshots/one.png')
    _put_file(uploads, 'desk/library/image/Screenshots/two.png')
    _put_file(uploads, 'desk/library/image/Screenshots/notes.txt', b'x')
    _put_file(uploads, 'desk/library/video/Clips/c.mp4', b'\x00\x00')
    art = _piece(client, 'article', title='Old article', word_count=300)
    other = client.post('/api/desk/campaigns?shape=v1', json={
        'id': 'camp-2', 'state': 'draft', 'projectId': 'alpha', 'rules': {}, 'map': {'stop': 'what', 'done': []},
        'plan': {'brief': 'b', 'title': 'Two', 'accounts': [], 'cadence': {}, 'end': {}}})
    assert other.status_code == 201
    out = client.get('/api/desk/materials?campaign_id=camp-2').get_json()
    shots = out['library']['image'][0]
    assert shots['title'] == 'Screenshots' and shots['files'] == 2
    assert sorted(i['title'] for i in shots['items']) == ['one.png', 'two.png']
    assert shots['thumb'].startswith('/api/serve-image?path=')
    vid = out['library']['video'][0]
    assert vid['files'] == 1 and vid['thumb'] is None and vid['items'][0]['src'] is None
    assert out['articles'] == [{'id': art['id'], 'title': 'Old article', 'words': 300, 'projectId': 'alpha'}]
    # the campaign's own article is not offered back to itself
    assert client.get(f'/api/desk/materials?campaign_id={CID}').get_json()['articles'] == []
    assert out['online'] == {'video': [], 'image': []}
    assert len(out['recent']) == 3


def test_materials_with_no_library_is_empty_not_invented(env):
    client, _, _ = env
    out = client.get('/api/desk/materials').get_json()
    assert out['library'] == {'video': [], 'image': []}
    assert out['articles'] == [] and out['recent'] == []
