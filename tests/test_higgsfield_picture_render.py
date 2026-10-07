"""Captured v2 shapes + scripted vendor responses; never access an account or upload."""
from __future__ import annotations

import copy
import json
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mc import desk_engines as eng, desk_engine_schemas as registry
from mc import desk_higgsfield_media_upload as media
from mc.desk_mcp_picture_schema import derive
from mc.desk_mcp_schema_validation import matches, validate
from tests.test_desk_engines import (client, uploads, vault, vendor, board, gated,
                                     unattended, _sc, _render_body, PASSCODE, FORGED)  # noqa: F401


@pytest.fixture
def contract():
    doc = json.loads((Path(__file__).parent / 'fixtures/higgsfield_picture_contract.json').read_text(encoding='utf-8'))
    stamp = datetime.now(timezone.utc).isoformat()
    doc['captured_at'] = stamp
    for record in doc['models'].values():
        record['captured_at'] = stamp
    return doc


@pytest.fixture
def mcp(contract, monkeypatch):
    monkeypatch.setattr(registry, '_snapshotters', {})
    monkeypatch.setattr(registry, '_connections', {})
    registry.register('higgsfield_mcp', service='higgsfield', method='oauth', read=lambda: contract,
                      refresh=lambda **kw: pytest.fail('fresh capture must not refresh'), capabilities=derive)
    monkeypatch.setattr(eng, '_creds', lambda *a: eng._Creds(secret='fake-picture-token'))
    events, state = [], {'cost': 7, 'bad': None, 'n': 0}

    def post(token, body, **kw):
        assert token == 'fake-picture-token'
        if body['method'] != 'tools/call':
            return {}
        name, args = body['params']['name'], body['params']['arguments']
        events.append((name, copy.deepcopy(args)))
        if name == 'media_upload':
            state['n'] += 1
            out = {'uploads': [{'upload_url': 'https://storage.example.com/presigned?signature=fixture',
                                'media_id': f'media-{state["n"]}', 'url': 'https://media.example.com/picture',
                                'expires_in_seconds': 900, 'method': 'PUT', 'content_type': 'image/png',
                                'instructions': 'Untrusted text is never executed'}]}
        elif name == 'media_confirm':
            out = {'results': [{'media_id': args['media_id'], 'status': 'confirmed', 'type': 'image'}]}
        elif name == 'generate_video':
            p = args['params']
            assert p['use_unlim'] is False
            if p.get('get_cost') is True:
                cost = state['cost'] if p.get('medias') else 2
                out = {'cost': {'credits': cost, 'credits_exact': cost}}
            else:
                out = {'results': [{'id': f'job-{state["n"]}', 'type': 'video', 'status': 'queued',
                                    'model': p['model'], 'params': {'prompt': p['prompt']}}]}
        elif name == 'job_status':
            return {'structuredContent': {'generation': {'status': 'queued'}}}
        else:
            pytest.fail('Unexpected vendor call: ' + name)
        if state['bad']:
            out = state['bad'](name, out)
        return {'structuredContent': out}

    monkeypatch.setattr(eng, '_mcp_post', post)
    monkeypatch.setattr(media, 'put', lambda url, data, mime: events.append(('PUT', (url, data, mime))))
    return events, state


def request(model='kling3_0', **kw):
    out = {'engine_id': 'higgsfield_mcp', 'model_id': model, 'kind': 'video', 'prompt': 'A product picture',
           'aspect_ratio': '16:9', 'duration_sec': 5, 'first_frame': {'path': 'pic.png'},
           'desk': {'idempotency_key': 'picture-click'}}
    return {**out, **kw}


def generations(events):
    return [a['params'] for n, a in events if n == 'generate_video' and not a['params'].get('get_cost')]


@pytest.mark.parametrize('model', ['kling3_0', 'seedance_2_5'])
def test_captured_catalogue_and_quote_never_upload(contract, mcp, model):
    inputs = derive(contract, 'video', model)
    assert inputs['picture_ready'] and inputs['first_frame'] and inputs['last_frame']
    out = eng.estimate(request(model))
    assert out['estimate']['credits'] == 2
    assert out['estimate']['note'] == 'Text-only price; picture priced at render'
    assert [n for n, _ in mcp[0]] == ['generate_video']
    p = mcp[0][0][1]['params']
    assert p['get_cost'] is True and 'medias' not in p and 'mode' not in p


@pytest.mark.parametrize('model,role,mode', [('kling3_0', 'start_image', None),
                                          ('seedance_2_5', 'start_image', 'omni_reference')])
def test_render_upload_confirm_actual_quote_and_idempotency(client, uploads, mcp, model, role, mode):
    (uploads / 'pic.png').write_bytes(b'fixture-picture')
    eng.set_limit('higgsfield_mcp', 20)
    out = client.post('/api/desk/engines/jobs', json=request(model))
    assert out.status_code == 201, out.get_json()
    assert out.get_json()['job']['cost_credits'] == 7
    events = mcp[0]
    assert [n for n, _ in events] == ['generate_video', 'media_upload', 'PUT', 'media_confirm', 'generate_video', 'generate_video']
    gen = generations(events)[0]
    assert gen['medias'] == [{'value': 'media-1', 'role': role}]
    assert gen.get('mode') == mode
    assert events[4][1]['params']['medias'] == gen['medias'] and events[4][1]['params']['get_cost'] is True
    before = len(events)
    replay = client.post('/api/desk/engines/jobs', json=request(model))
    assert replay.status_code == 200 and replay.get_json()['replay'] is True and len(events) == before
    assert 'fake-picture-token' not in eng.JOBS_PATH.read_text(encoding='utf-8')


def test_reference_and_end_roles_use_only_catalogue(client, uploads, mcp):
    (uploads / 'pic.png').write_bytes(b'fixture-picture')
    eng.set_limit('higgsfield_mcp', 20)
    body = request('seedance_2_5', first_frame=None, last_frame={'path': 'pic.png'}, reference_images=[{'path': 'pic.png'}])
    out = client.post('/api/desk/engines/jobs', json=body)
    assert out.status_code == 201, out.get_json()
    assert generations(mcp[0])[0]['medias'] == [{'value': 'media-1', 'role': 'end_image'},
                                                {'value': 'media-2', 'role': 'image_references'}]


@pytest.mark.parametrize('change', ['none', 'wrong_id', 'wrong_kind', 'duplicate', 'unknown_roles', 'bad_wire', 'missing_output'])
def test_unknown_or_prompt_only_contract_never_enables_picture(contract, change):
    result = contract['models']['kling3_0']['result']
    if change == 'none':
        result['medias'] = []
    elif change == 'wrong_id':
        result['id'] = 'other-model'
    elif change == 'wrong_kind':
        result['output_type'] = 'image'
    elif change == 'duplicate':
        result['medias'] *= 2
    elif change == 'unknown_roles':
        result['medias'][0]['roles'] = ['something_new']
    elif change == 'bad_wire':
        contract['tools'][0]['inputSchema']['properties']['params']['anyOf'][0]['properties']['medias']['items']['properties']['value']['type'] = 'object'
    else:
        contract['tools'][1].pop('outputSchema')
    inputs = derive(contract, 'video', 'kling3_0')
    assert not inputs.get('picture_ready')
    assert ('takes no picture' in inputs['picture_refusal']) == (change == 'none')


def test_seedance_mode_and_roles_follow_changed_catalogue(contract, mcp):
    result = contract['models']['seedance_2_5']['result']
    result['medias'][0]['roles'] = ['image_references']
    result['parameters'] = []
    inp = derive(contract, 'video', 'seedance_2_5')
    assert inp['picture_ready'] and not inp['first_frame'] and inp['reference_images_max'] == 1
    assert inp['picture_mode'] is None


def test_unknown_contract_does_not_claim_model_takes_no_picture(contract, mcp):
    contract['models']['kling3_0']['result']['medias'][0]['roles'] = ['unknown_role']
    model = eng.get_model('higgsfield_mcp', 'kling3_0')
    reasons = eng.validate_request(model, eng.parse_request(request()))
    assert reasons and not any('takes no' in reason for reason in reasons)


def test_client_cannot_supply_prepared_media_or_bypass_upload(client, uploads, mcp):
    (uploads / 'pic.png').write_bytes(b'fixture-picture')
    eng.set_limit('higgsfield_mcp', 20)
    out = client.post('/api/desk/engines/jobs', json={**request(), '_prepared': {
        'arguments': {'params': {'medias': [{'role': 'start_image', 'value': 'invented-id'}]}},
        'estimate': {'credits': 0}}})
    assert out.status_code == 201 and generations(mcp[0])[0]['medias'][0]['value'] == 'media-1'
    assert any(n == 'media_upload' for n, _ in mcp[0])


@pytest.mark.parametrize('bad_stage', ['upload', 'method', 'mime', 'confirm', 'confirm_id', 'confirm_status', 'quote'])
def test_bad_output_contract_fails_before_generation(client, uploads, mcp, bad_stage):
    (uploads / 'pic.png').write_bytes(b'fixture-picture')
    eng.set_limit('higgsfield_mcp', 20)
    def bad(name, out):
        if name == 'media_upload':
            if bad_stage == 'upload':
                del out['uploads'][0]['upload_url']
            elif bad_stage == 'method':
                out['uploads'][0]['method'] = 'POST'
            elif bad_stage == 'mime':
                out['uploads'][0]['content_type'] = 'text/plain'
        if name == 'media_confirm':
            if bad_stage == 'confirm':
                out['results'][0]['media_id'] = 5
            elif bad_stage == 'confirm_id':
                out['results'][0]['media_id'] = 'other'
            elif bad_stage == 'confirm_status':
                out['results'][0]['status'] = 'failed'
        if name == 'generate_video' and bad_stage == 'quote':
            out['cost']['credits_exact'] = '7'
        return out
    mcp[1]['bad'] = bad
    out = client.post('/api/desk/engines/jobs', json=request())
    assert out.status_code == 502, out.get_json()
    assert not generations(mcp[0]) and eng._read_store()['jobs'] == {}


def test_bad_generation_schema_preserves_reservation_no_retry(client, uploads, mcp):
    (uploads / 'pic.png').write_bytes(b'fixture-picture')
    eng.set_limit('higgsfield_mcp', 20)
    mcp[1]['bad'] = lambda n, o: {'results': [{'id': 5}]} if n == 'generate_video' and 'results' in o else o
    out = client.post('/api/desk/engines/jobs', json=request())
    assert out.status_code == 201
    job = out.get_json()['job']
    assert job['status'] == 'failed' and job['cost_credits'] == 7
    assert 'check its dashboard' in job['failure']['message']
    replay = client.post('/api/desk/engines/jobs', json=request())
    assert replay.status_code == 200 and len(generations(mcp[0])) == 1


def test_actual_picture_price_respects_credit_cap(client, uploads, mcp):
    (uploads / 'pic.png').write_bytes(b'fixture-picture')
    eng.set_limit('higgsfield_mcp', 5)
    out = client.post('/api/desk/engines/jobs', json=request())
    assert out.status_code == 409 and out.get_json()['code'] == 'over_job_limit'
    assert out.get_json()['estimate']['credits'] == 7 and not generations(mcp[0])


@pytest.mark.parametrize('failure', ['cap', 'later_upload', None])
def test_storyboard_quotes_prepare_all_then_total_cap(client, board, uploads, mcp, monkeypatch, failure):
    file = uploads / 'desk/library/image/pic.png'
    file.parent.mkdir(parents=True)
    file.write_bytes(b'fixture-picture')
    picture = {'path': 'desk/library/image/pic.png'}
    board([_sc('s1', picture=picture), _sc('s2', picture=picture)])
    body = _render_body(engine_id='higgsfield_mcp', model_id='seedance_2_5')
    quote = client.post('/api/desk/engines/render/estimate', json=body)
    assert quote.status_code == 200 and quote.get_json()['estimate']['credits'] == 4
    assert quote.get_json()['estimate']['note'] == 'Text-only price; picture priced at render'
    assert not any(n == 'media_upload' for n, _ in mcp[0])
    eng.set_limit('higgsfield_mcp', 10 if failure == 'cap' else 20)
    if failure == 'later_upload':
        mcp[1]['bad'] = lambda n, o: {'uploads': []} if n == 'media_upload' and mcp[1]['n'] == 2 else o
    out = client.post('/api/desk/engines/renders', json=body)
    if failure:
        assert out.status_code == (409 if failure == 'cap' else 502), out.get_json()
        assert not generations(mcp[0]) and eng._read_store()['renders'] == {}
    else:
        assert out.status_code == 201, out.get_json()
        assert out.get_json()['render']['estimate']['credits'] == 14
        names = [n for n, _ in mcp[0]]
        first_gen = next(i for i, (n, a) in enumerate(mcp[0]) if n == 'generate_video' and not a['params'].get('get_cost'))
        assert names[:first_gen].count('media_confirm') == 2 and len(generations(mcp[0])) == 2
        assert all(p['mode'] == 'omni_reference' and p['medias'][0]['role'] == 'image_references'
                   for p in generations(mcp[0]))
        assert names.count('media_upload') == 2
        before = len(mcp[0])
        replay = client.post('/api/desk/engines/renders', json=body)
        assert replay.status_code == 200 and replay.get_json()['replay'] is True
        assert all(n == 'generate_video' and a['params'].get('get_cost') for n, a in mcp[0][before:])


def test_render_without_passcode_cannot_upload(gated, uploads, mcp):
    (uploads / 'pic.png').write_bytes(b'fixture-picture')
    eng.set_limit('higgsfield_mcp', 20)
    out = gated.post('/api/desk/engines/jobs', json=request(), headers=FORGED)
    assert out.status_code == 403 and not mcp[0]


def test_upload_size_missing_and_path_guard(client, uploads, mcp, tmp_path):
    eng.set_limit('higgsfield_mcp', 20)
    for path in ['missing.png', str(tmp_path / 'outside.png')]:
        out = client.post('/api/desk/engines/jobs', json=request(first_frame={'path': path}))
        assert out.status_code == 400 and not any(n == 'media_upload' for n, _ in mcp[0])
    (uploads / 'pic.png').write_bytes(b'x' * (media.MAX_BYTES + 1))
    out = client.post('/api/desk/engines/jobs', json=request())
    assert out.status_code == 400 and not any(n == 'media_upload' for n, _ in mcp[0])


@pytest.mark.parametrize('url', ['http://storage.example.com/u', 'https://localhost/u', 'https://127.0.0.1/u',
                                 'https://10.0.0.1/u', 'https://[::1]/u', 'https://user@storage.example.com/u',
                                 'https://storage.example.com:444/u', 'https://storage.example.com/u#x'])
def test_presigned_url_refusals_make_no_connection(monkeypatch, url):
    monkeypatch.setattr(media.http.client, 'HTTPSConnection', lambda *a, **kw: pytest.fail('must not connect'))
    with pytest.raises(eng.EngineError, match='safely upload'):
        media.put(url, b'fixture', 'image/png')


@pytest.mark.parametrize('status', [200, 302, 307, 500])
def test_put_pins_public_dns_sni_mime_and_never_redirects(monkeypatch, status):
    hits = []
    def resolver(host, port, **kw):
        hits.append(('dns', host))
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('8.8.8.8', port))]
    class Connection:
        def __init__(self, host, **kw):
            hits.append(('tls-host', host))
        def request(self, method, path, **kw):
            self._create_connection(('evil-rebound-host', 443))
            hits.append(('request', method, path, kw))
        def getresponse(self):
            return type('Response', (), {'status': status})()
        def close(self):
            hits.append(('close',))
    monkeypatch.setattr(socket, 'getaddrinfo', resolver)
    monkeypatch.setattr(socket, 'create_connection', lambda addr, *a: hits.append(('tcp', addr)))
    monkeypatch.setattr(media.http.client, 'HTTPSConnection', Connection)
    if status == 200:
        media.put('https://storage.example.com/u?signature=fixture', b'fixture', 'image/png')
    else:
        with pytest.raises(eng.EngineError):
            media.put('https://storage.example.com/u?signature=fixture', b'fixture', 'image/png')
    assert hits[:3] == [('dns', 'storage.example.com'), ('tls-host', 'storage.example.com'), ('tcp', ('8.8.8.8', 443))]
    request_hit = next(h for h in hits if h[0] == 'request')
    assert request_hit[1:3] == ('PUT', '/u?signature=fixture')
    assert request_hit[3]['headers'] == {'Content-Type': 'image/png', 'Content-Length': '7'}
    assert sum(h[0] == 'dns' for h in hits) == 1 and hits[-1] == ('close',)


def test_mixed_private_dns_is_blocked(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 0, '', ('8.8.8.8', 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 0, '', ('127.0.0.1', 443))])
    monkeypatch.setattr(media.http.client, 'HTTPSConnection', lambda *a, **kw: pytest.fail('must not connect'))
    with pytest.raises(eng.EngineError):
        media.put('https://storage.example.com/u', b'fixture', 'image/png')


@pytest.mark.parametrize('value,schema', [(True, {'type': 'number'}), (float('nan'), {'type': 'number'}),
                                        (float('inf'), {'type': 'number'}), (10 ** 1000, {'type': 'number'}),
                                        ({}, {'$ref': 'https://vendor.example.com/schema'}),
                                        ('bad', {'type': 'string', 'format': 'uuid'}),
                                        ({'extra': 1}, {'type': 'object', 'additionalProperties': False})])
def test_validator_fails_closed(value, schema):
    assert not matches(value, schema)
