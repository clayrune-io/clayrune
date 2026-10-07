"""The displayed storyboard price bounds every paid child submission."""
import pytest

from mc import desk_engines as eng
from mc.desk_render_price_confirmation import shown_total
from tests.test_desk_engines import client, uploads, vault, vendor, board, gated, _sc, _render_body, FORGED  # noqa: F401
from tests.test_higgsfield_picture_render import contract, mcp, generations  # noqa: F401


@pytest.mark.parametrize('value', [None, True, False, '14', -1, float('nan'), float('inf'), 10**400, {}, []])
def test_invalid_displayed_total_is_not_an_approval(value):
    with pytest.raises(eng.Refused) as error:
        shown_total({'shown_total': value})
    assert error.value.code == 'invalid_input'


def picture_board(board, uploads):
    path = uploads / 'desk/library/image/pic.png'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'fixture-picture')
    board([_sc('s1', picture={'path': 'desk/library/image/pic.png'}),
           _sc('s2', picture={'path': 'desk/library/image/pic.png'})])


def body(**kw):
    return _render_body(engine_id='higgsfield_mcp', model_id='seedance_2_5', **kw)


def test_picture_price_increase_is_shown_before_any_generation_then_accepted(client, board, uploads, mcp):
    picture_board(board, uploads)
    eng.set_limit('higgsfield_mcp', 30)
    request = body(shown_total=4, idempotency_key='confirmed-click')
    quote = client.post('/api/desk/engines/render/estimate', json=request).get_json()
    assert quote['estimate']['credits'] == 4 and quote['estimate']['picture_pending']
    refused = client.post('/api/desk/engines/renders', json=request)
    out = refused.get_json()
    assert (refused.status_code, out['code']) == (409, 'render_price_changed')
    assert out['prepared_total'] == 14 and out['shown_total'] == 4 and out['currency'] == 'credits'
    assert out['estimate']['credits'] == 14 and not out['estimate'].get('picture_pending')
    assert [s['credits'] for s in out['plan']['scenes']] == [7, 7]
    assert 'Price with your pictures is 14 credits (was 4). Press Render again to accept.' == out['error']
    assert not generations(mcp[0])
    assert eng._read_store()['renders'] == eng._read_store()['render_idem'] == eng._read_store()['jobs'] == {}
    accepted = client.post('/api/desk/engines/renders', json={**request, 'shown_total': out['prepared_total']})
    assert accepted.status_code == 201, accepted.get_json()
    assert len(generations(mcp[0])) == 2
    # Existing preparation is in-memory only: both attempts upload both pictures.
    assert sum(n == 'media_upload' for n, _ in mcp[0]) == 4
    replay = client.post('/api/desk/engines/renders', json={**request, 'shown_total': 4})
    assert replay.status_code == 200 and replay.get_json()['replay']
    assert len(generations(mcp[0])) == 2


@pytest.mark.parametrize('total', [14, 15])
def test_equal_or_lower_prepared_price_proceeds(client, board, uploads, mcp, total):
    picture_board(board, uploads)
    eng.set_limit('higgsfield_mcp', 30)
    out = client.post('/api/desk/engines/renders', json=body(shown_total=total))
    assert out.status_code == 201, out.get_json()
    assert len(generations(mcp[0])) == 2


def test_price_can_rise_again_before_acceptance(client, board, uploads, mcp):
    picture_board(board, uploads)
    eng.set_limit('higgsfield_mcp', 30)
    assert client.post('/api/desk/engines/renders', json=body(shown_total=4)).status_code == 409
    mcp[1]['cost'] = 8
    out = client.post('/api/desk/engines/renders', json=body(shown_total=14))
    assert out.status_code == 409 and out.get_json()['prepared_total'] == 16
    assert not generations(mcp[0])


def test_missing_total_refuses_before_upload(client, board, uploads, mcp):
    picture_board(board, uploads)
    eng.set_limit('higgsfield_mcp', 30)
    request = body()
    del request['shown_total']
    out = client.post('/api/desk/engines/renders', json=request)
    assert out.status_code == 400 and out.get_json()['code'] == 'invalid_input'
    assert not any(n == 'media_upload' for n, _ in mcp[0]) and not generations(mcp[0])


def test_accepted_price_does_not_override_credit_cap(client, board, uploads, mcp):
    picture_board(board, uploads)
    eng.set_limit('higgsfield_mcp', 10)
    out = client.post('/api/desk/engines/renders', json=body(shown_total=14))
    assert out.status_code == 409 and out.get_json()['code'] == 'over_job_limit'
    assert not generations(mcp[0])


def test_price_acceptance_does_not_bypass_passcode(gated, board, uploads, mcp):
    picture_board(board, uploads)
    eng.set_limit('higgsfield_mcp', 30)
    mcp[0].clear()
    out = gated.post('/api/desk/engines/renders', json=body(shown_total=14), headers=FORGED)
    assert out.status_code == 403 and not mcp[0]


def test_dollar_price_increase_is_also_refused(client, board, vendor):
    from tests.test_desk_engines import _two_clip_vendor, _job_posts, HIGGS_T2V
    board([_sc('s1'), _sc('s2')])
    _two_clip_vendor(vendor)
    eng.set_limit('higgsfield', 5)
    out = client.post('/api/desk/engines/renders', json=_render_body(shown_total=0.4))
    assert out.status_code == 409 and out.get_json()['prepared_total'] == 0.42
    assert out.get_json()['currency'] == 'usd'
    assert [s['usd'] for s in out.get_json()['plan']['scenes']] == [0.21, 0.21]
    assert not _job_posts(vendor, HIGGS_T2V)
