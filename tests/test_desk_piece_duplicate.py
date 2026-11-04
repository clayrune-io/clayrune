"""C4 duplication preserves content and never carries release authority."""
import json
from pathlib import Path
import sys

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mc import desk, desk_pieces as pieces
from mc.blueprints import desk_routes


@pytest.fixture
def env(tmp_path):
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    desk_routes.wire(store_path=tmp_path / 'desk.json', uploads_root=uploads,
                     load_projects_fn=lambda: [{'id': 'alpha'}, {'id': 'beta'}])
    desk.upsert_presence('alpha', {'accounts': [
        {'channel_id': 'x', 'platform': 'x', 'identity': '@source'},
        {'channel_id': 'li', 'platform': 'linkedin', 'identity': 'Source'},
        {'channel_id': 'other', 'platform': 'x', 'identity': '@other'}]})
    for cid, project, accounts in [('source', 'alpha', ['x', 'li']), ('target', 'beta', ['li', 'other'])]:
        desk.create_campaign(cid, '', campaign_id=cid, project_id=project, state='draft',
                             plan={'accounts': accounts, 'title': cid})
    pieces.create_piece('source', 'post', "Ron's post", piece_id='original', body='Base text\nSecond line',
                        claims=[{'id': 'claim', 'text': 'Unverified claim'}])
    for name in ['image.png', 'video.mp4']:
        (uploads / name).write_bytes(b'fixture')
        pieces.add_asset('original', path=name)
    pieces.add_version('original', 'x', body='X customized')
    pieces.add_version('original', 'li', body='LinkedIn customized')
    with desk._store_lock:
        store = desk._read_store()
        original = store['pieces']['original']
        original['draft_status'] = 'in_review'
        for v in original['versions']:
            v.update(state='scheduled', revision=8, scheduled_at='2030-01-02T12:00:00Z',
                     slot_id='slot', approved={'by': 'human'}, receipt={'posted_at': 'yesterday'},
                     failure={'message': 'old'}, manual={'reported': True},
                     claims_state={'claim': {'status': 'accepted'}})
        desk._write_store(store)
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(desk_routes.bp)
    return app.test_client(), uploads, tmp_path


def duplicate(client, data=None):
    return client.post('/api/desk/pieces/original/duplicate', json={} if data is None else data)


@pytest.mark.parametrize('target,accounts,project', [('source', ['x', 'li'], 'alpha'), ('target', ['li'], 'beta')])
def test_copy_content_bounded_fresh_drafts_and_reload(env, target, accounts, project):
    client, _, tmp = env
    before = desk._read_store()
    response = duplicate(client, {'campaign_id': target})
    assert response.status_code == 201, response.get_json()
    post = response.get_json()
    assert post['campaignId'] == target and post['projectId'] == project
    assert post['body'] == 'Base text\nSecond line' and post['draft'] == {'status': 'drafting'}
    assert [v['channelId'] for v in post['versions']] == accounts
    original = before['pieces']['original']
    saved = desk._read_store()
    raw = saved['pieces'][post['id']]
    assert saved['pieces']['original'] == original
    assert saved['campaigns'] == before['campaigns']
    assert [a['path'] for a in raw['assets']] == [a['path'] for a in original['assets']]
    assert not {a['id'] for a in raw['assets']} & {a['id'] for a in original['assets']}
    assert raw['claims'] == original['claims']
    for v in raw['versions']:
        assert v['state'] == 'drafting' and v['revision'] == 0 and not v['claims_state']
        assert v['body'] == next(x['body'] for x in original['versions'] if x['account_id'] == v['account_id'])
        for key in ['scheduled_at', 'slot_id', 'approved', 'receipt', 'failure']:
            assert v[key] is None
        assert 'manual' not in v
    assert all(v['claims'][0]['verdict'] == 'blocked' for v in post['versions'])
    assert json.loads((tmp / 'desk.json').read_text(encoding='utf-8'))['pieces'][post['id']] == raw
    assert next(p for p in client.get('/api/desk/workspace').get_json()['pieces'] if p['id'] == post['id']) == post
    # Editing/removing media from the draft cannot change the source or its file.
    client.patch('/api/desk/pieces/' + post['id'], json={'body': 'Edited copy'})
    client.delete('/api/desk/pieces/' + post['id'] + '/assets/' + post['assets'][0]['id'])
    assert desk._read_store()['pieces']['original'] == original


def test_empty_bound_and_inactive_or_disconnected_versions(env):
    client, _, _ = env
    with desk._store_lock:
        store = desk._read_store()
        store['campaigns']['target']['plan']['accounts'] = []
        desk._write_store(store)
    assert duplicate(client, {'campaign_id': 'target'}).get_json()['versions'] == []
    with desk._store_lock:
        store = desk._read_store()
        store['pieces']['original']['versions'][0]['state'] = 'archived'
        del store['accounts']['li']
        store['presences']['alpha']['accounts'] = [a for a in store['presences']['alpha']['accounts'] if a['channel_id'] != 'li']
        desk._write_store(store)
    assert duplicate(client).get_json()['versions'] == []


@pytest.mark.parametrize('path', ['missing.png', '../outside.png'])
def test_bad_media_refuses_entire_copy(env, path):
    client, _, tmp = env
    with desk._store_lock:
        store = desk._read_store()
        store['pieces']['original']['assets'][-1]['path'] = path
        desk._write_store(store)
    before = (tmp / 'desk.json').read_bytes()
    assert duplicate(client).status_code in (400, 404)
    assert (tmp / 'desk.json').read_bytes() == before


@pytest.mark.parametrize('data,status', [({'campaign_id': 'missing'}, 404), ({'campaign_id': []}, 404),
    ({'id': '../bad'}, 400), ({'id': 'original'}, 409), ({'approved': True}, 400),
    ({'scheduled_at': '2030'}, 400), ([], 400), ('bad', 400)])
def test_refusals_never_write(env, data, status):
    client, _, tmp = env
    before = (tmp / 'desk.json').read_bytes()
    assert duplicate(client, data).status_code == status
    assert (tmp / 'desk.json').read_bytes() == before


def test_archived_or_sent_source_is_copied_without_its_lifecycle(env):
    client, _, _ = env
    with desk._store_lock:
        store = desk._read_store()
        post = store['pieces']['original']
        post['archive'] = {'at': 'yesterday'}
        post['versions'][0]['state'] = 'verified_published'
        desk._write_store(store)
    result = duplicate(client).get_json()
    assert result['archivedAt'] is None and result['versions'][0]['state'] == 'drafting'


def test_non_post_unknown_source_and_archived_target(env):
    client, _, _ = env
    pieces.create_piece('source', 'video', piece_id='video')
    assert client.post('/api/desk/pieces/video/duplicate', json={}).status_code == 400
    assert client.post('/api/desk/pieces/unknown/duplicate', json={}).status_code == 404
    with desk._store_lock:
        store = desk._read_store()
        store['campaigns']['target']['state'] = 'dropped'
        desk._write_store(store)
    assert duplicate(client, {'campaign_id': 'target'}).status_code == 409
