"""YouTube's upload contract; fake driver only, no signed-in Studio or upload."""
import types

import pytest

from mc import desk, desk_accounts as accounts, desk_assisted_post as ap
from mc import desk_assisted_youtube as yt, desk_pieces as pieces, desk_tick as tick
from mc.desk_connect import browser_connections, registry, type_view
from tests.test_desk_assisted_post import env, mock_pane, FakeDriver, _store, _asset
from tests.test_desk_assisted_accounts import world, _campaign, _stored, NOW


def test_named_youtube_connect_creates_placeable_account(env):
    browser_connections._done.clear()
    clean = browser_connections.clean_draft({'name': '@my-channel', 'url': 'https://studio.youtube.com/',
                                             'browser_profile': 'youtube-channel', 'signin_method': 'google'})
    result, duplicate = browser_connections.commit('youtube-1234', clean)
    account = accounts.get_account(result['account_id'])
    assert not duplicate and result['kind'] == 'account'
    assert account['label'] == 'YouTube · my-channel'
    assert account['capability'] == 'assisted' and account['connected']
    assert account['browser_profile'] == 'youtube-channel' and account['signin_method'] == 'google'
    assert not account.get('permission_policy', {}).get('scopes')
    assert not result['post'] and not result['read']
    profile = registry.profile('youtube')
    assert profile['account_kinds'][0]['id'] == 'account'
    view = type_view.project_service('youtube')
    assert view['service']['makes_account']


@pytest.mark.parametrize('body,assets,reason', [
    ('Title', [], 'exactly one video'),
    ('Title', [_asset('a.png')], 'exactly one video'),
    ('Title', [_asset('b.mp4', 'video'), _asset('b.mp4', 'video')], 'exactly one video'),
    ('t' * 101, [_asset('b.mp4', 'video')], '100 characters'),
    ('Title\n' + 'd' * 5001, [_asset('b.mp4', 'video')], '5000 characters'),
    ('', [_asset('b.mp4', 'video')], 'no text'),
    ('Title <bad>', [_asset('b.mp4', 'video')], 'cannot contain'),
])
def test_invalid_upload_refused_before_any_pane(env, body, assets, reason):
    _store(assets, platform='youtube', body=body)
    with pytest.raises(ap.AssistedError, match=reason):
        ap.prepare('p1', 'v1', driver_factory=lambda profile: pytest.fail('browser touched'))


def test_unicode_boundaries_and_approved_text_mapping(env):
    title, description = '🎥' * 100, '文' * 5000
    _store([_asset('b.mp4', 'video')], platform='youtube', body=title + '\n' + description)
    post = ap.approved_post('p1', 'v1')
    assert yt.text_fields(post['body']) == {'title': title, 'description': description}
    assert post['media'][0].endswith('b.mp4')


class YouTubeDriver(FakeDriver):
    def replace_text(self, css, text):
        self.calls.append(('replace', css, text))
        return self.caption_ok


def test_details_filled_without_next_save_or_publish(env):
    ap.validate_route(yt)
    _store([_asset('b.mp4', 'video')], platform='youtube', body='Approved title\nApproved description')
    d = YouTubeDriver()
    out = ap.run(ap.approved_post('p1', 'v1'), d)
    assert out['state'] == 'ready_for_human'
    assert [c[1] for c in d.calls if c[0] == 'locate'] == ['Create', 'Upload videos']
    assert [c[2] for c in d.calls if c[0] == 'replace'] == ['Approved title', 'Approved description']
    assert 'Publish/Save' in out['message']
    # A changed page offering either final button cannot turn an intermediate step into a submit.
    for label in ('Publish', 'Save'):
        d = YouTubeDriver(offer={'Upload videos': label})
        with pytest.raises(ap.AssistedError) as e:
            ap.run(ap.approved_post('p1', 'v1'), d)
        assert e.value.code == 'final_submit_refused'
        assert len([c for c in d.calls if c[0] == 'click']) == 1
        bad = types.SimpleNamespace(**{**vars(yt), 'STEPS': ({'op': 'click', 'label': label},)})
        with pytest.raises(ValueError, match='final-submit'):
            ap.validate_route(bad)


def test_approval_rejects_no_video_and_tick_never_publishes(world):
    accounts.create_account('youtube', 'channel', account_id='ch-yt')
    _campaign(world, ['ch-yt'])
    pieces.create_piece('camp-a', 'post', 'Piece title is not uploaded', body='Upload title\nDescription', piece_id='p1')
    pieces.add_version('p1', 'ch-yt', version_id='v1')
    pieces.update_version('p1', 'v1', {'state': 'needs_review'})
    with pytest.raises(pieces.PieceError, match='exactly one video'):
        pieces.approve_version('p1', 'v1', scheduled_at=None)
    # Store-level fixture: no media parser or actual upload is needed to test approval/tick.
    with desk._store_lock:
        store = desk._read_store()
        store['pieces']['p1']['assets'] = [{'id': 'a1', 'kind': 'video', 'path': 'b.mp4'}]
        desk._write_store(store)
    tick.approve_and_send('p1', 'v1', now=NOW)
    assert _stored('p1', 'v1')['state'] == 'approved'
    tick.run_once(now=NOW)
    assert world.published == []


def test_old_none_account_is_not_migrated_by_read(env):
    account = accounts.create_account('youtube', 'old', capability='none')
    assert accounts.get_account(account['id'])['capability'] == 'none'


def test_mock_chromium_replaces_filename_and_clears_description(mock_pane):
    from mc.desk_assisted_pane import PaneDriver
    br, session, site = mock_pane
    driver = PaneDriver(session)
    driver.navigate(site.base)
    ok, _ = br._cdp_evaluate(session, '''document.body.innerHTML =
      '<div id="title" contenteditable="true">filename.mp4</div><textarea id="description">default description</textarea>' ''')
    assert ok
    try:
        assert driver.replace_text('#title', 'Approved title')
        assert driver.replace_text('#description', '')
        assert br._cdp_evaluate(session, 'document.querySelector("#title").textContent')[1] == 'Approved title'
        assert br._cdp_evaluate(session, 'document.querySelector("#description").value')[1] == ''
    finally:
        driver.close()
