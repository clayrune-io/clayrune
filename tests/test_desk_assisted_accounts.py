"""The `assisted` account capability (Desk Instagram assisted post, part 2).

Pinned:

  * `assisted` exists only for platforms with a route in `desk_assisted_post.ROUTES`
    (Instagram today): it is their default on create, refused for anything else, and an
    existing read-only (`none`) account of such a platform moves to it, and back, only
    through a PATCH that is human-only and retypes the passcode. A read never migrates it;
  * a version can be approved for an assisted account, media and all (a `direct` account
    refuses media), but NOTHING PUBLISHES IT: `desk_publish.publish` is never called, by
    the Approve click or by the background tick, now or when scheduled. It becomes a
    publishing task for a person, exactly as a manual account's does;
  * a read-only (`none`) account still cannot be approved for;
  * the assisted engine refuses a click on the route's OWN final-submit label even when
    the global final-submit word list does not hold it.
"""
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk as _desk  # noqa: E402
from mc import desk_accounts as _accounts  # noqa: E402
from mc import desk_assisted_instagram as ig  # noqa: E402
from mc import desk_assisted_post as ap  # noqa: E402
from mc import desk_pieces as _pieces  # noqa: E402
from mc import desk_publish as _publish  # noqa: E402
from mc import desk_tick as _tick  # noqa: E402
from mc.blueprints import desk_routes  # noqa: E402

PROJECTS = [{'id': 'alpha', 'name': 'Alpha'}]
CID = 'camp-a'
NOW = datetime.now(timezone.utc)


@pytest.fixture
def world(tmp_path, monkeypatch):
    caller = {'unattended': False}
    passcodes = []
    monkeypatch.setattr(desk_routes, 'is_unattended_caller', lambda: caller['unattended'])
    monkeypatch.setattr(desk_routes, '_require_human_passcode',
                        lambda data: passcodes.append(data.get('passcode')) or None)
    published = []

    def no_publish(*a, **k):
        published.append((a, k))
        raise AssertionError('an assisted version reached desk_publish.publish')
    monkeypatch.setattr(_publish, 'publish', no_publish)
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: (_ for _ in ()).throw(AssertionError('network')))
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    (uploads / 'a.png').write_bytes(b'\x89PNG\r\n\x1a\n' + b'0' * 32)
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(load_projects_fn=lambda: PROJECTS,
                     load_project_fn=lambda pid: next((p for p in PROJECTS if p['id'] == pid), None),
                     store_path=tmp_path / 'desk.json', signals_path=tmp_path / 'sig.jsonl',
                     uploads_root=uploads)
    app.register_blueprint(desk_routes.bp)
    return types.SimpleNamespace(client=app.test_client(), caller=caller, passcodes=passcodes,
                                 published=published, uploads=uploads)


def _campaign(w, accounts):
    plan = {'brief': 'b', 'title': 'T', 'accounts': list(accounts), 'cadence': {'per_week': 5},
            'end': {'post_cap': 10}}
    r = w.client.post('/api/desk/campaigns?shape=v1', json={
        'id': CID, 'state': 'draft', 'projectId': 'alpha', 'rules': {},
        'map': {'stop': 'what', 'done': []}, 'plan': plan,
        'how': {'budget': {'source': 'own', 'amount': 5}}})
    assert r.status_code == 201, r.get_json()
    _desk.start_campaign(CID, today=NOW.date().isoformat())


def _piece(w, account, *, piece='p1', vid='v1', media=True):
    _pieces.create_piece(CID, 'post', 'A post', piece_id=piece)
    _pieces.add_version(piece, account, body='A caption #clay', version_id=vid)
    if media:
        _pieces.add_asset(piece, path=str(w.uploads / 'a.png'))
    _pieces.update_version(piece, vid, {'state': 'needs_review'})
    return piece, vid


def _stored(piece, vid):
    with _desk._store_lock:
        p = _desk._read_store()['pieces'][piece]
    return next(v for v in p['versions'] if v['id'] == vid)


# -- the capability --------------------------------------------------------------------

def test_the_only_assisted_platform_is_the_one_with_a_route(world):
    assert _accounts.assisted_platforms() == tuple(ap.ROUTES) == ('instagram',)
    assert 'assisted' in _accounts.CAPABILITIES


def test_instagram_defaults_to_assisted_and_other_platforms_do_not(world):
    ig_acc = _accounts.create_account('instagram', 'clayrune', account_id='ch-ig')
    assert ig_acc['capability'] == 'assisted'
    assert ig_acc['publish']['ready'] is True and ig_acc['publish'].get('assisted') is True
    assert _accounts.create_account('youtube', 'clayrune', account_id='ch-yt')['capability'] == 'none'
    assert _accounts.create_account('tiktok', 'clayrune', account_id='ch-tt')['capability'] == 'none'
    # read-only stays available for Instagram when asked for
    assert _accounts.create_account('instagram', 'second', capability='none')['capability'] == 'none'


@pytest.mark.parametrize('platform', ['x', 'linkedin', 'blog', 'youtube', 'tiktok'])
def test_assisted_is_refused_where_there_is_no_route(world, platform):
    with pytest.raises(_accounts.AccountError, match='assisted'):
        _accounts.create_account(platform, 'a', capability='assisted')


def test_assisted_on_a_platform_without_a_route_is_not_ready():
    pub = _accounts.publish_state({'platform': 'x', 'capability': 'assisted', 'id': 'a'})
    assert pub['ready'] is False and 'no assisted route' in pub['reason']


# -- the migration: an explicit, passcode-gated PATCH ------------------------------------

def test_a_read_only_instagram_account_moves_to_assisted_only_by_a_passcoded_patch(world):
    _accounts.create_account('instagram', 'old', capability='none', account_id='ch-old')
    # a read never migrates it
    assert _accounts.get_account('ch-old')['capability'] == 'none'
    assert world.client.get('/api/desk/accounts').get_json()[0]['capability'] == 'none'
    r = world.client.patch('/api/desk/accounts/ch-old', json={'capability': 'assisted', 'passcode': '1234'})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['capability'] == 'assisted' and r.get_json()['publish']['ready'] is True
    assert world.passcodes == ['1234']
    assert _accounts.get_account('ch-old')['capability'] == 'assisted'
    back = world.client.patch('/api/desk/accounts/ch-old', json={'capability': 'none', 'passcode': '1234'})
    assert back.status_code == 200 and back.get_json()['capability'] == 'none'


def test_the_capability_patch_is_human_only(world):
    _accounts.create_account('instagram', 'old', capability='none', account_id='ch-old')
    world.caller['unattended'] = True
    r = world.client.patch('/api/desk/accounts/ch-old', json={'capability': 'assisted'})
    assert r.status_code == 403
    assert _accounts.get_account('ch-old')['capability'] == 'none' and world.passcodes == []


@pytest.mark.parametrize('platform,start', [('x', 'direct'), ('youtube', 'none'), ('blog', 'manual')])
def test_the_capability_patch_is_refused_where_there_is_no_route(world, platform, start):
    _accounts.create_account(platform, 'a', account_id='ch-a')
    assert _accounts.get_account('ch-a')['capability'] == start
    r = world.client.patch('/api/desk/accounts/ch-a', json={'capability': 'assisted', 'passcode': '1'})
    assert r.status_code == 400 and _accounts.get_account('ch-a')['capability'] == start


def test_the_capability_patch_takes_only_none_or_assisted(world):
    _accounts.create_account('instagram', 'a', account_id='ch-ig')
    for cap in ('direct', 'manual', 'nope'):
        r = world.client.patch('/api/desk/accounts/ch-ig', json={'capability': cap, 'passcode': '1'})
        assert r.status_code == 400, cap
    assert _accounts.get_account('ch-ig')['capability'] == 'assisted'


# -- approval and the tick -----------------------------------------------------------------

def test_an_assisted_version_with_media_is_approved_and_never_published(world):
    _accounts.create_account('instagram', 'clayrune', account_id='ch-ig')
    _campaign(world, ['ch-ig'])
    piece, vid = _piece(world, 'ch-ig')
    _tick.approve_and_send(piece, vid, now=NOW)
    v = _stored(piece, vid)
    assert v['state'] == 'approved' and v['approved']['account_id'] == 'ch-ig'
    # a task for a person, not a send
    assert v['manual']['copy_text'] == 'A caption #clay' and v['manual']['share_url'] is None
    assert v.get('receipt') is None
    # the background pass leaves it alone, over and over
    for _ in range(3):
        out = _tick.run_once(now=NOW + timedelta(minutes=5))
        assert out['sent'] == [] and out['held'] == [] and out['failed'] == [] and out['errors'] == []
    assert _stored(piece, vid)['state'] == 'approved'
    assert world.published == []


def test_a_scheduled_assisted_version_is_never_published_when_it_falls_due(world):
    _accounts.create_account('instagram', 'clayrune', account_id='ch-ig')
    _campaign(world, ['ch-ig'])
    piece, vid = _piece(world, 'ch-ig')
    at = (NOW + timedelta(hours=2)).isoformat()
    _tick.approve_and_send(piece, vid, scheduled_at=at, now=NOW)
    assert _stored(piece, vid)['state'] == 'scheduled'
    out = _tick.run_once(now=NOW + timedelta(hours=3))
    assert out['sent'] == [] and out['failed'] == [] and out['errors'] == []
    v = _stored(piece, vid)
    assert v['state'] in ('approved', 'scheduled') and v['manual'] and v.get('receipt') is None
    assert world.published == []


def test_a_direct_account_still_refuses_media_and_a_read_only_one_is_not_approvable(world):
    _accounts.create_account('x', '@clayrune', account_id='ch-x')
    _accounts.create_account('instagram', 'ro', capability='none', account_id='ch-ro')
    _campaign(world, ['ch-x', 'ch-ro'])
    p1, v1 = _piece(world, 'ch-ro', piece='p1', vid='v1')
    with pytest.raises(_pieces.PieceError, match='read-only'):
        _pieces.approve_version(p1, v1, scheduled_at=None)
    assert _stored(p1, v1)['state'] == 'needs_review'


# -- the route's own final-submit label --------------------------------------------------

class _Driver:
    def __init__(self, offer):
        self.offer, self.clicks = offer, 0

    def locate(self, css, labels): return {'x': 1.0, 'y': 1.0, 'label': self.offer or labels[0]}
    def click_at(self, x, y): self.clicks += 1
    def wait(self, css, timeout_s): return True


def _route(final='Go live'):
    return types.SimpleNamespace(NETWORK='test', FINAL_SUBMIT={'css': 'b', 'label': final})


def test_a_click_on_the_routes_own_final_label_is_refused_though_the_global_list_lacks_it():
    assert not ap.is_final_submit('Go live')                  # not a word the global list knows
    route = _route()
    # the step itself names it
    d = _Driver(None)
    with pytest.raises(ap.AssistedError) as e:
        ap._click(d, {'css': 'b', 'label': ' go LIVE '}, route)
    assert e.value.code == 'final_submit_refused' and d.clicks == 0
    # an alias names it
    with pytest.raises(ap.AssistedError) as e:
        ap._click(d, {'css': 'b', 'label': 'Next', 'aliases': ('Go live',)}, route)
    assert e.value.code == 'final_submit_refused' and d.clicks == 0
    # the page offers it where the step wanted Next
    d = _Driver('Go live')
    with pytest.raises(ap.AssistedError) as e:
        ap._click(d, {'css': 'b', 'label': 'Next'}, route)
    assert e.value.code == 'final_submit_refused' and e.value.status == 403 and d.clicks == 0


def test_the_instagram_routes_own_final_label_is_refused_in_a_run():
    d = _Driver('Share')
    with pytest.raises(ap.AssistedError) as e:
        ap._click(d, {'css': 'b', 'label': 'Next'}, ig)
    assert e.value.code == 'final_submit_refused' and d.clicks == 0
