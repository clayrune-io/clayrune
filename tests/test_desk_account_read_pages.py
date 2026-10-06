"""Any platform's Desk account can be read through the browser pane (44712cf4 follow-up).

Pinned:

  * a YouTube / Instagram / unknown-site account takes a browser profile and
    `read_pages` through the same PATCH X and LinkedIn use; both land on the workspace
    record AND the presence copy `PaneDigestReader` reads, and the reader parses them;
  * `read_via: 'api'` is X only: any other site is read through the pane;
  * a blog has nothing to read, so a read setting on it is still refused;
  * `read_pages` is saved strictly (https only, known roles, at most MAX_USER_PAGES):
    a bad entry is a 400 that changes nothing, `[]` clears;
  * an unattended agent cannot set `read_pages` (the pane would open what it names).
"""
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk as _desk  # noqa: E402
from mc import desk_accounts as _accounts  # noqa: E402
from mc.blueprints import desk_routes  # noqa: E402
from mc.desk_engagement_pane_digest import MAX_USER_PAGES, PaneDigestReader  # noqa: E402

PROJECTS = [{'id': 'alpha', 'name': 'Alpha'}]
YT = 'https://studio.youtube.com/channel/UC123/comments'


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(_accounts.secrets_store, 'list_secrets', lambda *a, **k: [])
    monkeypatch.setattr(_accounts.secrets_store, 'is_readable', lambda name: False)
    caller = {'unattended': False}
    monkeypatch.setattr(desk_routes, 'is_unattended_caller', lambda: caller['unattended'])
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: PROJECTS,
        load_project_fn=lambda pid: next((p for p in PROJECTS if p['id'] == pid), None),
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
        uploads_root=tmp_path,
    )
    app.register_blueprint(desk_routes.bp)
    _desk.upsert_presence('alpha', {'accounts': [
        {'channel_id': 'ch-yt', 'platform': 'youtube', 'identity': 'UC123'},
        {'channel_id': 'ch-ig', 'platform': 'instagram', 'identity': 'clayrune'},
        {'channel_id': 'ch-forum', 'platform': 'forum', 'identity': 'ron'},
        {'channel_id': 'ch-x', 'platform': 'x', 'identity': '@a'},
        {'channel_id': 'ch-b', 'platform': 'blog', 'identity': 'b'}]})
    return app.test_client(), caller


def _patch(client, cid, **body):
    return client.patch(f'/api/desk/accounts/{cid}', json=body)


def _by_id(client):
    return {a['id']: a for a in client.get('/api/desk/accounts').get_json()}


def _copy(cid):
    return next(a for a in (_desk.get_presence('alpha') or {})['accounts'] if a['channel_id'] == cid)


@pytest.mark.parametrize('cid', ['ch-yt', 'ch-ig', 'ch-forum'])
def test_a_non_x_account_takes_a_profile_and_pages(env, cid):
    client, _ = env
    pages = [{'role': 'activity', 'url': YT}]
    r = _patch(client, cid, read_via='pane', browser_profile=' Main ', read_pages=pages)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert (body['read_via'], body['browser_profile'], body['read_pages']) == ('pane', 'main', pages)
    assert _by_id(client)[cid]['read_pages'] == pages
    copy = _copy(cid)
    assert copy['browser_profile'] == 'main' and copy['read_pages'] == pages


def test_the_reader_parses_what_the_account_saved(env):
    client, _ = env
    _patch(client, 'ch-yt', browser_profile='main', read_pages=[{'role': 'activity', 'url': YT}])
    rd = PaneDigestReader('alpha', 'youtube', _copy('ch-yt'))
    assert rd._user_pages == [{'role': 'activity', 'url': YT}] and rd._profile == 'main'


def test_pages_alone_and_clearing(env):
    client, _ = env
    assert _patch(client, 'ch-yt', read_pages=[{'role': 'activity', 'url': YT}]).status_code == 200
    assert 'browser_profile' not in _by_id(client)['ch-yt']
    assert _patch(client, 'ch-yt', read_pages=[]).status_code == 200
    assert 'read_pages' not in _by_id(client)['ch-yt'] and 'read_pages' not in _copy('ch-yt')


def test_an_unrelated_edit_leaves_the_pages_alone(env):
    client, _ = env
    _patch(client, 'ch-yt', read_pages=[{'role': 'activity', 'url': YT}])
    assert _patch(client, 'ch-yt', browser_profile='main').status_code == 200
    assert _by_id(client)['ch-yt']['read_pages'] == [{'role': 'activity', 'url': YT}]


def test_api_read_is_x_only(env):
    client, _ = env
    for cid in ('ch-yt', 'ch-ig', 'ch-forum'):
        r = _patch(client, cid, read_via='api')
        assert r.status_code == 400 and 'browser pane' in r.get_json()['error']
        assert 'read_via' not in _by_id(client)[cid]
    assert _patch(client, 'ch-x', read_via='api').status_code == 200


def test_a_blog_still_has_no_read_setting(env):
    client, _ = env
    for body in ({'read_via': 'pane'}, {'browser_profile': 'main'}, {'read_pages': [{'role': 'activity', 'url': YT}]}):
        assert _patch(client, 'ch-b', **body).status_code == 400
    assert _patch(client, 'ch-b', label='My blog').status_code == 200   # a plain edit still works


@pytest.mark.parametrize('bad', [
    'https://studio.youtube.com/',                                  # not a list
    [{'role': 'activity', 'url': 'http://studio.youtube.com/'}],     # not https
    [{'role': 'activity', 'url': 'javascript:alert(1)'}],
    [{'role': 'activity', 'url': 'https://youtube.com@evil.example/'}],   # userinfo
    [{'role': 'activity', 'url': 'https://192.168.0.1/'}],           # IP literal
    [{'role': 'activity', 'url': 7}],
    [{'role': 'dm', 'url': YT}],                                     # unknown role
    [{'role': 'activity', 'url': YT, 'extra': 1}],
    ['https://studio.youtube.com/'],
    [{'role': 'activity', 'url': f'https://example.org/{i}'} for i in range(MAX_USER_PAGES + 1)],
])
def test_a_bad_address_is_refused_and_changes_nothing(env, bad):
    client, _ = env
    r = _patch(client, 'ch-yt', label='Renamed', browser_profile='main', read_pages=bad)
    assert r.status_code == 400
    acc = _by_id(client)['ch-yt']
    assert acc['label'] != 'Renamed' and 'browser_profile' not in acc and 'read_pages' not in acc


def test_duplicate_addresses_collapse_and_whitespace_is_trimmed(env):
    client, _ = env
    r = _patch(client, 'ch-yt', read_pages=[{'role': 'activity', 'url': f' {YT} '}, {'role': 'activity', 'url': YT}])
    assert r.get_json()['read_pages'] == [{'role': 'activity', 'url': YT}]


def test_an_unattended_agent_cannot_set_pages(env):
    client, caller = env
    caller['unattended'] = True
    r = _patch(client, 'ch-yt', read_pages=[{'role': 'activity', 'url': YT}])
    assert r.status_code == 403
    assert 'read_pages' not in _by_id(client)['ch-yt']
    assert _patch(client, 'ch-yt', label='Renamed').status_code == 200    # a label is not a read setting


def test_project_id_files_a_copy_that_carries_the_pages(env):
    client, _ = env
    _desk.upsert_presence('alpha', {'accounts': []})
    r = _patch(client, 'ch-ig', browser_profile='main', read_pages=[{'role': 'activity', 'url': YT}], project_id='alpha')
    assert r.status_code == 200
    assert _copy('ch-ig')['read_pages'] == [{'role': 'activity', 'url': YT}]
