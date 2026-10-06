"""Studio's Recent list and standalone articles (mc/desk_studio_articles.py).

Pinned:

  * `GET /api/desk/materials` `recent` lists what Studio made (the Studio folder),
    never a picture uploaded as storyboard scene material: that stays in the
    library's Storyboards folder, and does not use up a slot of the newest-8 cut;
  * a standalone article is saved at rev 0 with a topic, any existing project and
    an optional existing campaign; a stale rev is a 409, an unknown project or
    campaign is refused, and a topic is required;
  * attach makes an ordinary article piece in the campaign and stamps it on the
    draft; a second attach is refused; once that piece is deleted in What the
    draft reads as not attached again;
  * delete moves the draft to a trash token and restore puts it back at its rev;
    an attached draft cannot be deleted.
"""
import io
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import desk_routes, desk_studio_articles_routes  # noqa: E402

PROJECTS = [{'id': 'alpha', 'name': 'Alpha'}, {'id': 'beta', 'name': 'Beta'}]
CID = 'camp-1'
PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 64


@pytest.fixture
def client(tmp_path):
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
    app.register_blueprint(desk_studio_articles_routes.bp)
    c = app.test_client()
    r = c.post('/api/desk/campaigns?shape=v1', json={
        'id': CID, 'state': 'draft', 'projectId': 'alpha', 'rules': {},
        'map': {'stop': 'what', 'done': []},
        'plan': {'brief': 'b', 'title': 'Windows beta', 'accounts': [], 'cadence': {}, 'end': {}}})
    assert r.status_code == 201, r.get_json()
    return c


def _made(client, name):
    r = client.post('/api/desk/materials', data={'file': (io.BytesIO(PNG), name), 'title': name},
                    content_type='multipart/form-data')
    assert r.status_code == 201, r.get_json()


def _scene_picture(client, name):
    r = client.post('/api/desk/studio/draft-1/storyboard/pictures',
                    data={'file': (io.BytesIO(PNG), name)}, content_type='multipart/form-data')
    assert r.status_code == 201, r.get_json()


def _recent_titles(client):
    return [r['title'] for r in client.get('/api/desk/materials').get_json()['recent']]


# -- Recent -------------------------------------------------------------------------

def test_recent_leaves_out_storyboard_scene_uploads(client):
    _made(client, 'capture.png')
    _scene_picture(client, 'Gemini_scene.png')
    out = client.get('/api/desk/materials').get_json()
    assert [t.split('-')[0] for t in _recent_titles(client)] == ['capture']
    folders = {f['title']: f['files'] for f in out['library']['image']}
    assert folders == {'Studio': 1, 'Storyboards': 1}   # still in the library, untouched


def test_scene_uploads_do_not_crowd_what_was_made_out_of_the_newest_eight(client):
    _made(client, 'firstmade.png')
    for i in range(10):
        _scene_picture(client, f'scene{i}.png')
    assert [t.split('-')[0] for t in _recent_titles(client)] == ['firstmade']


# -- articles -----------------------------------------------------------------------

def _put(client, aid='art-1', **body):
    body.setdefault('rev', 0)
    body.setdefault('topic', 'Why local-first matters')
    return client.put(f'/api/desk/studio/articles/{aid}', json=body)


def test_article_for_any_project_with_no_campaign(client):
    r = _put(client, project_id='beta')
    assert r.status_code == 200, r.get_json()
    a = r.get_json()
    assert (a['topic'], a['project_id'], a['campaign_id'], a['rev'], a['attached']) == \
        ('Why local-first matters', 'beta', None, 1, False)
    assert a['tabs'] == [{'id': 'tab-draft', 'label': 'Draft', 'body': ''}]
    assert [x['id'] for x in client.get('/api/desk/studio/articles').get_json()['articles']] == ['art-1']


def test_article_with_no_project_and_no_campaign(client):
    a = _put(client, aid='art-2').get_json()
    assert (a['project_id'], a['campaign_id']) == (None, None)


def test_topic_is_required_and_references_must_exist(client):
    assert _put(client, topic='   ').status_code == 400
    assert _put(client, project_id='nope').status_code == 404
    assert _put(client, campaign_id='nope').status_code == 404
    assert client.get('/api/desk/studio/articles').get_json()['articles'] == []


def test_stale_rev_is_409_and_save_keeps_omitted_fields(client):
    _put(client, project_id='beta', campaign_id=CID)
    r = _put(client, rev=0)
    assert r.status_code == 409 and r.get_json()['problems'] == ['current_rev=1']
    a = _put(client, rev=1, topic='New topic', tabs=[{'id': 't1', 'label': 'Draft', 'body': 'Hello world'}]).get_json()
    assert (a['rev'], a['project_id'], a['campaign_id'], a['tabs'][0]['body']) == (2, 'beta', CID, 'Hello world')
    cleared = _put(client, rev=2, campaign_id='').get_json()
    assert cleared['campaign_id'] is None and cleared['project_id'] == 'beta'


def test_bad_tabs_are_refused(client):
    assert _put(client, tabs=[]).status_code == 400
    assert _put(client, tabs=[{'id': 'a b', 'label': 'x', 'body': ''}]).status_code == 400
    assert _put(client, tabs=[{'id': 'a', 'label': 'x', 'body': ''}, {'id': 'a', 'label': 'y', 'body': ''}]).status_code == 400


def test_attach_makes_a_campaign_piece_and_cannot_repeat(client):
    _put(client, tabs=[{'id': 't1', 'label': 'Draft', 'body': 'one two three'}])
    r = client.post('/api/desk/studio/articles/art-1/attach', json={'campaign_id': CID})
    assert r.status_code == 201, r.get_json()
    out = r.get_json()
    assert (out['piece']['kind'], out['piece']['title'], out['piece']['body'], out['piece']['wordCount'],
            out['piece']['campaignId']) == ('article', 'Why local-first matters', 'one two three', 3, CID)
    art = client.get('/api/desk/studio/articles/art-1').get_json()
    assert (art['attached'], art['campaign_id'], art['piece_id'], art['campaign_title']) == \
        (True, CID, out['piece']['id'], 'Windows beta')
    assert client.post('/api/desk/studio/articles/art-1/attach', json={'campaign_id': CID}).status_code == 409
    assert client.post('/api/desk/studio/articles/art-1/attach', json={'campaign_id': 'nope'}).status_code == 404
    # deleted in What: the draft is a plain draft again
    assert client.delete(f"/api/desk/pieces/{out['piece']['id']}").status_code in (200, 204)
    assert client.get('/api/desk/studio/articles/art-1').get_json()['attached'] is False


def test_delete_and_restore_round_trip(client):
    _put(client)
    _put(client, rev=1, tabs=[{'id': 't1', 'label': 'Draft', 'body': 'keep me'}])
    token = client.delete('/api/desk/studio/articles/art-1').get_json()['token']
    assert client.get('/api/desk/studio/articles/art-1').status_code == 404
    assert client.delete('/api/desk/studio/articles/art-1').status_code == 404
    assert client.post(f'/api/desk/studio/articles/trash/{token}/restore').status_code == 200
    a = client.get('/api/desk/studio/articles/art-1').get_json()
    assert (a['rev'], a['tabs'][0]['body']) == (2, 'keep me')
    assert client.post(f'/api/desk/studio/articles/trash/{token}/restore').status_code == 404
    assert client.post('/api/desk/studio/articles/trash/not-a-token/restore').status_code == 400


def test_attached_article_cannot_be_deleted(client):
    _put(client)
    client.post('/api/desk/studio/articles/art-1/attach', json={'campaign_id': CID})
    r = client.delete('/api/desk/studio/articles/art-1')
    assert r.status_code == 409 and r.get_json()['error'] == 'Attached to Windows beta, detach it there first'
