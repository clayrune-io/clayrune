"""Literal retry is quote-only, bounded, and reuses the final priced render arguments."""
from __future__ import annotations

import copy

import pytest

from mc import desk_engines as eng
from mc import desk_higgsfield_preset_decline as decline
from tests.test_higgsfield_picture_render import contract, mcp, request  # noqa: F401
from tests.test_desk_engines import client, uploads, vault, vendor, board, _sc, _render_body  # noqa: F401


def notice(data=None):
    return {'notice': {'type': 'preset_recommendation', 'message': 'PRIVATE MESSAGE', 'data': data if data is not None else {
        'preset': {'id': 'preset-123', 'name': 'PRIVATE NAME', 'preview_url': 'https://private.example'},
        'retry_literal_with': {'declined_preset_id': 'preset-123', 'prompt': 'PRIVATE REWRITE', 'use_unlim': True},
        'use_preset_with': {'preset_id': 'PRIVATE PRESET', 'model': 'PRIVATE MODEL'}}}}


@pytest.fixture
def logs(monkeypatch):
    from mc import desk_higgsfield_quote_response as response
    rows = []
    monkeypatch.setattr(decline, '_log', lambda text, **kw: rows.append(text))
    monkeypatch.setattr(response, '_log', lambda text, **kw: rows.append(text))
    return rows


@pytest.mark.parametrize('data', [
    {'preset': {'id': 'preset-123'}}, {'preset': {'preset_id': 'preset-123'}},
    {'retry_literal_with': {'declined_preset_id': 'preset-123'}},
    {'preset': {'id': 'preset-123', 'preset_id': 'preset-123'},
     'retry_literal_with': {'declined_preset_id': 'preset-123'}},
    {'preset': {'id': '123e4567-e89b-12d3-a456-426614174000'}},
])
def test_identifier_sources_agree_and_only_decline_is_forwarded(contract, data, logs):
    args = {'params': {'prompt': 'A desk in the office', 'model': 'kling3_0', 'use_unlim': False, 'get_cost': True}}
    original = copy.deepcopy(args)
    calls = []
    def call(name, params):
        calls.append(copy.deepcopy(params))
        return notice(data) if len(calls) == 1 else {'cost': {'credits_exact': 7}}
    out, priced, note = decline.quote(contract, call, 'generate_video', args)
    assert args == original and calls[0] == original
    assert calls[1] == {'params': {**original['params'], 'declined_preset_id': decline._declined_id(notice(data))}}
    assert priced == calls[1] and decline.credits(out) == 7 and note == decline.NOTE
    assert len(logs) == 1 and 'PRIVATE' not in logs[0]


@pytest.mark.parametrize('data', [None, [], 'PRIVATE', {}, {'preset_id': 'unapproved-source'},
    {'preset': None}, {'preset': {}}, {'preset': 'preset-123'},
    {'preset': {'id': 'a'}, 'retry_literal_with': {'declined_preset_id': 'b'}},
    {'preset': {'id': 'a', 'preset_id': 'b'}},
    {'preset': {'id': 'a'}, 'retry_literal_with': {}},
    {'preset': {'id': None}, 'retry_literal_with': {'declined_preset_id': 'a'}},
])
def test_missing_malformed_disagreeing_sources_never_retry(contract, data, logs):
    calls = []
    def call(name, args):
        calls.append(args)
        out = notice()
        out['notice']['data'] = data
        return out
    with pytest.raises(eng.EngineError, match='valid, agreeing decline identifier'):
        decline.quote(contract, call, 'generate_video', {'params': {'model': 'kling3_0', 'get_cost': True}})
    assert len(calls) == 1 and 'PRIVATE' not in ''.join(logs)


@pytest.mark.parametrize('ident', ['', ' a', 'a ', 'a\n', 'a.b', 'https://a', '../a', '-a',
                                  'a;cmd', 'a/b', 'é', 'a' * 129, True, 123, {}, []])
def test_strict_bounded_ascii_identifier(contract, ident):
    calls = []
    with pytest.raises(eng.EngineError, match='decline identifier'):
        decline.quote(contract, lambda n, a: calls.append(a) or notice({'preset': {'id': ident}}),
                      'generate_video', {'params': {'model': 'kling3_0', 'get_cost': True}})
    assert len(calls) == 1


@pytest.mark.parametrize('out', [notice(), {'notice': {'type': 'unknown'}, 'cost': {'credits': 7}}, {},
    {'cost': {'credits': True}}, {'cost': {'credits_exact': -1}}, {'cost': {'credits': '7'}},
    {'cost': {'credits': float('inf')}}, {'cost': {'credits': 7}, 'error': 'PRIVATE'}])
def test_retry_stops_on_second_notice_or_unusable_cost(contract, out, logs):
    calls = []
    def call(n, a):
        calls.append(a)
        return notice() if len(calls) == 1 else out
    with pytest.raises(eng.EngineError, match='usable literal price'):
        decline.quote(contract, call, 'generate_video', {'params': {'model': 'kling3_0', 'get_cost': True}})
    assert len(calls) == 2 and 'PRIVATE' not in ''.join(logs)


def test_no_declared_parameter_no_retry(contract):
    del contract['tools'][0]['inputSchema']['properties']['params']['anyOf'][0]['properties']['declined_preset_id']
    calls = []
    with pytest.raises(eng.EngineError, match='has not declared'):
        decline.quote(contract, lambda n, a: calls.append(a) or notice(), 'generate_video',
                      {'params': {'model': 'kling3_0', 'get_cost': True}})
    assert len(calls) == 1


def scripted(monkeypatch, *, changed_cost=False, submit_notice=False, transport_error=False):
    real = eng._mcp_post
    def post(token, body, **kw):
        out = real(token, body, **kw)
        if body['method'] == 'tools/call' and body['params']['name'] == 'generate_video':
            p = body['params']['arguments']['params']
            if (p.get('get_cost') is True and 'declined_preset_id' not in p) or (submit_notice and not p.get('get_cost')):
                return {'structuredContent': notice(), 'isError': transport_error}
            if changed_cost and p.get('medias') and p.get('get_cost'):
                return {'structuredContent': {'cost': {'credits': 70, 'credits_exact': 70}}}
        return out
    monkeypatch.setattr(eng, '_mcp_post', post)


@pytest.mark.parametrize('picture', [False, True])
@pytest.mark.parametrize('transport_error', [False, True])
def test_estimate_free_literal_retry_note_and_picture_state(mcp, logs, monkeypatch, picture, transport_error):
    scripted(monkeypatch, transport_error=transport_error)
    out = eng.estimate(request(prompt='A desk in the office', first_frame={'path': 'pic.png'} if picture else None))
    est = out['estimate']
    assert est['credits'] == 2 and decline.NOTE in est['note']
    assert bool(est.get('picture_pending')) is picture
    assert len(mcp[0]) == 2 and all(n == 'generate_video' for n, _ in mcp[0])
    assert all(a['params']['get_cost'] is True and a['params']['use_unlim'] is False
               and a['params']['prompt'] == 'A desk in the office' for _, a in mcp[0])
    assert 'PRIVATE' not in ''.join(logs)


@pytest.mark.parametrize('picture', [False, True])
def test_render_uses_exact_final_literal_quote_and_idempotency(client, uploads, mcp, monkeypatch, picture):
    (uploads / 'pic.png').write_bytes(b'fixture-picture')
    scripted(monkeypatch)
    eng.set_limit('higgsfield_mcp', 20)
    body = request(prompt='A desk in the office', first_frame={'path': 'pic.png'} if picture else None)
    response = client.post('/api/desk/engines/jobs', json=body)
    assert response.status_code == 201, response.get_json()
    calls = [a['params'] for n, a in mcp[0] if n == 'generate_video']
    submitted = [a for a in calls if not a.get('get_cost')]
    assert len(submitted) == 1 and submitted[0]['declined_preset_id'] == 'preset-123'
    assert submitted[0] == {k: v for k, v in calls[-2].items() if k != 'get_cost'}
    assert submitted[0]['prompt'] == 'A desk in the office' and submitted[0]['use_unlim'] is False
    assert response.get_json()['job']['cost_credits'] == (7 if picture else 2)
    before = len(mcp[0])
    replay = client.post('/api/desk/engines/jobs', json=body)
    assert replay.status_code == 200 and replay.get_json()['replay'] and len(mcp[0]) == before


def test_final_literal_picture_quote_rechecks_credit_cap(client, uploads, mcp, monkeypatch):
    (uploads / 'pic.png').write_bytes(b'fixture-picture')
    scripted(monkeypatch, changed_cost=True)
    eng.set_limit('higgsfield_mcp', 20)
    response = client.post('/api/desk/engines/jobs', json=request())
    assert response.status_code == 409, response.get_json()
    assert not any(n == 'generate_video' and not a['params'].get('get_cost') for n, a in mcp[0])


@pytest.mark.parametrize('picture', [False, True])
@pytest.mark.parametrize('transport_error', [False, True])
def test_submit_notice_never_retries_retains_reservation_and_key(client, uploads, mcp, monkeypatch, picture, logs, transport_error):
    (uploads / 'pic.png').write_bytes(b'fixture-picture')
    scripted(monkeypatch, submit_notice=True, transport_error=transport_error)
    eng.set_limit('higgsfield_mcp', 20)
    body = request(first_frame={'path': 'pic.png'} if picture else None)
    response = client.post('/api/desk/engines/jobs', json=body)
    assert response.status_code == 201, response.get_json()
    job = response.get_json()['job']
    assert job['status'] == 'failed' and 'could not be confirmed' in job['failure']['message']
    assert job['cost_credits'] == (7 if picture else 2)
    assert len([a for n, a in mcp[0] if n == 'generate_video' and not a['params'].get('get_cost')]) == 1
    before = len(mcp[0])
    replay = client.post('/api/desk/engines/jobs', json=body)
    assert replay.get_json()['replay'] and len(mcp[0]) == before
    assert 'PRIVATE' not in ''.join(logs)


def test_storyboard_prices_all_literal_requests_before_any_submission(client, board, mcp, monkeypatch):
    board([_sc('1'), _sc('2')])
    scripted(monkeypatch)
    eng.set_limit('higgsfield_mcp', 20)
    body = _render_body(engine_id='higgsfield_mcp', model_id='kling3_0')
    response = client.post('/api/desk/engines/renders', json=body)
    assert response.status_code == 201, response.get_json()
    calls = [a['params'] for n, a in mcp[0] if n == 'generate_video']
    paid = [i for i, a in enumerate(calls) if not a.get('get_cost')]
    assert len(paid) == 2 and all(a['get_cost'] for a in calls[:paid[0]])
    assert all(calls[i]['declined_preset_id'] == 'preset-123' for i in paid)
