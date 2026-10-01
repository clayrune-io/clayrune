"""Desk v1 MC-1019 — the generation-engine connector (`mc/desk_engines.py` + the
`/api/desk/engines` routes). NO REAL VENDOR CALL AND NO REAL SPEND anywhere in
this file: `desk_engines._http_request` is replaced by a scripted fake, and the
vault is replaced by four patched `secrets_store` functions. Pinned:

  * the descriptors list every engine with a connected flag, and no response,
    job record or error text ever carries a credential value;
  * a request a model cannot do is refused before anything is sent (Veo 1:1,
    Veo 1080p off 8 s, references off 8 s, a first frame the model does not take,
    an asset outside data/uploads);
  * submit is refused for an unattended caller and, for ANY caller, without the
    retyped dashboard passcode (MC-995); the passcode is never stored;
  * the campaign's `how.budget` is the only cap: no budget set, an estimate over
    what is left, and a second job past the remainder all refuse (409) with
    nothing sent; ledger post costs count against it; a vendor-rejected submit
    gives its reservation back, a dropped connection does not;
  * `desk.idempotency_key` makes a retry return the first job, one vendor call;
  * Higgsfield (estimate endpoint, first-frame upload without the key, poll to
    completed / failed / nsfw), Veo (operation poll, download carries the key
    only to Google, a redirect elsewhere does not), Gemini image and OpenAI image
    (sync, billed usage replaces the estimate, edits multipart) each run end to end
    and write their output under data/uploads/desk/generated/.
"""
import base64
import json
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk as _desk  # noqa: E402
from mc import desk_engines as eng  # noqa: E402
from mc import secrets_store  # noqa: E402
from mc.blueprints import desk_routes  # noqa: E402

PROJECTS = [{'id': 'alpha', 'name': 'Alpha'}]
CID = 'camp-a'
KEY_ID = 'hfkeyid-SENTINEL-1'
KEY_SECRET = 'hfsecret-SENTINEL-2'
GEMINI_KEY = 'gemkey-SENTINEL-3'
OPENAI_KEY = 'oaikey-SENTINEL-4'
SENTINELS = (KEY_ID, KEY_SECRET, GEMINI_KEY, OPENAI_KEY)
PASSCODE = 'unlock1234'
FORGED = {'Origin': 'http://localhost:5199'}


def _png(w=3, h=2) -> bytes:
    return (b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + w.to_bytes(4, 'big') + h.to_bytes(4, 'big')
            + b'\x08\x02\x00\x00\x00' + b'\x00' * 8)


class FakeVendor:
    """Scripted `_http_request`. `routes` maps (METHOD, url-substring) to a
    response or a list of responses (consumed in order, the last one repeats);
    a response is (status, headers, bytes) or an Exception to raise."""

    def __init__(self):
        self.routes: dict = {}
        self.calls: list[dict] = []

    def on(self, method, needle, *responses):
        self.routes[(method, needle)] = list(responses)

    def json(self, status, obj):
        return (status, {'content-type': 'application/json'}, json.dumps(obj).encode())

    def __call__(self, method, url, *, headers=None, body=None, timeout=30, max_bytes=0):
        self.calls.append({'method': method, 'url': url, 'headers': dict(headers or {}), 'body': body})
        for (m, needle), resp in self.routes.items():
            if m == method and needle in url:
                r = resp[0] if len(resp) == 1 else resp.pop(0)
                if isinstance(r, Exception):
                    raise r
                return r
        raise AssertionError(f'unscripted vendor call: {method} {url}')

    def to(self, needle, method=None):
        return [c for c in self.calls if needle in c['url'] and (method is None or c['method'] == method)]


@pytest.fixture
def vendor(monkeypatch):
    v = FakeVendor()
    monkeypatch.setattr(eng, '_http_request', v)
    return v


@pytest.fixture
def vault(monkeypatch):
    """Which vault entries 'exist'. Values are handed out by name, as the real
    `get_secret_value` does, and registered for redaction like the real one."""
    state = {'entries': {'higgsfield': (KEY_SECRET, KEY_ID), 'gemini-api': (GEMINI_KEY, ''),
                         'openai-api': (OPENAI_KEY, '')}}

    def list_secrets(project_id=None, **kw):
        return [{'name': n, 'username': u} for n, (_s, u) in state['entries'].items()]

    def get_secret_value(name, *, consumer, project_id=None, unattended=False):
        if name not in state['entries']:
            raise secrets_store.SecretNotFound(name)
        secrets_store.register_dispensed(name, state['entries'][name][0])
        return state['entries'][name][0]

    monkeypatch.setattr(secrets_store, 'list_secrets', list_secrets)
    monkeypatch.setattr(secrets_store, 'is_readable', lambda n: n in state['entries'])
    monkeypatch.setattr(secrets_store, 'get_secret_value', get_secret_value)
    monkeypatch.setattr(secrets_store, 'get_username',
                        lambda n, project_id=None: state['entries'][n][1])
    return state


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    root = tmp_path / 'uploads'
    root.mkdir()
    monkeypatch.setattr(eng, 'UPLOADS_ROOT', root)
    monkeypatch.setattr(eng, 'JOBS_PATH', tmp_path / 'jobs.json')
    return root


@pytest.fixture
def client(tmp_path, monkeypatch, uploads, vault, vendor):
    # The passcode gate is exercised for real in the "human proof" section via `gated`.
    monkeypatch.setattr(desk_routes, '_require_human_passcode', lambda data: None)
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: PROJECTS,
        load_project_fn=lambda pid: next((p for p in PROJECTS if p['id'] == pid), None),
        store_path=tmp_path / 'desk.json', signals_path=tmp_path / 'sig.jsonl')
    app.register_blueprint(desk_routes.bp)
    return app.test_client()


@pytest.fixture
def unattended():
    from mc.state import agent_sessions
    snapshot = dict(agent_sessions)
    agent_sessions.clear()
    agent_sessions['dispatch-1'] = {'status': 'running', 'trigger_type': 'dispatch'}
    yield
    agent_sessions.clear()
    agent_sessions.update(snapshot)


@pytest.fixture
def gated(client, tmp_path, monkeypatch):
    from mc.blueprints import local_auth
    from mc.blueprints.secrets_routes import _require_human_passcode as real
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    monkeypatch.setattr(desk_routes, '_require_human_passcode', real)
    local_auth._local_auth_set_passcode(PASSCODE)
    local_auth._LOCAL_AUTH_FAILS.clear()
    return client


def _campaign(budget=None, state='draft'):
    how = {'budget': budget} if budget is not None else {}
    return _desk.create_campaign('Alpha', '', voiceless_ok=True, project_id='alpha',
                                 campaign_id=CID, state=state, how=how)


def _own(amount):
    return {'source': 'own', 'amount': amount}


_n = iter(range(10 ** 6))


def _veo(**kw):
    d = {'engine_id': 'google', 'model_id': 'veo-3.1-generate-preview', 'kind': 'video',
         'prompt': 'A calm product shot', 'aspect_ratio': '16:9', 'duration_sec': 8,
         'resolution': '720p', 'campaign_id': CID,
         'desk': {'idempotency_key': f'k{next(_n)}'}}
    d.update(kw)
    return d


def _higgs_video(**kw):
    d = {'engine_id': 'higgsfield', 'model_id': 'kling-video/v2.5-turbo/pro/text-to-video',
         'kind': 'video', 'prompt': 'A calm product shot', 'aspect_ratio': '16:9',
         'duration_sec': 5, 'campaign_id': CID, 'desk': {'idempotency_key': f'k{next(_n)}'}}
    d.update(kw)
    return d


def _gemini_img(**kw):
    d = {'engine_id': 'google', 'model_id': 'gemini-3.1-flash-image', 'kind': 'image',
         'prompt': 'A product hero', 'aspect_ratio': '1:1', 'resolution': '1K',
         'campaign_id': CID, 'desk': {'idempotency_key': f'k{next(_n)}'}}
    d.update(kw)
    return d


def _openai_img(**kw):
    d = {'engine_id': 'openai', 'model_id': 'gpt-image-2.5-sunburst', 'kind': 'image',
         'prompt': 'A product hero', 'aspect_ratio': '1:1', 'campaign_id': CID,
         'desk': {'idempotency_key': f'k{next(_n)}'}}
    d.update(kw)
    return d


def _submit(client, body, **kw):
    return client.post('/api/desk/engines/jobs', json=body, **kw)


def _assert_no_secret(text):
    for s in SENTINELS:
        assert s not in text, f'credential value leaked: {s}'


# ── engines list ─────────────────────────────────────────────────────────────

def test_engines_listed_with_connected_status_and_no_secret(client):
    r = client.get('/api/desk/engines')
    assert r.status_code == 200
    ids = {e['id']: e for e in r.get_json()['engines']}
    assert set(ids) == {'higgsfield', 'google', 'openai'}
    assert all(e['connected'] == {'ready': True, 'vault_entry': e['auth']['vault_entry'], 'reason': None}
               for e in ids.values())
    assert {e['auth']['vault_entry'] for e in ids.values()} == {'higgsfield', 'gemini-api', 'openai-api'}
    assert all('vendor' not in m for e in ids.values() for m in e['models'])
    veo = next(m for m in ids['google']['models'] if m['model_id'] == 'veo-3.1-generate-preview')
    assert veo['status'] == 'preview' and veo['aspect_ratios'] == ['9:16', '16:9']
    _assert_no_secret(r.get_data(as_text=True))


def test_not_connected_reasons(client, vault):
    del vault['entries']['openai-api']
    vault['entries']['higgsfield'] = (KEY_SECRET, '')          # key id missing
    ids = {e['id']: e['connected'] for e in client.get('/api/desk/engines').get_json()['engines']}
    assert ids['openai']['ready'] is False and "no vault entry named 'openai-api'" in ids['openai']['reason']
    assert ids['higgsfield']['ready'] is False and 'username' in ids['higgsfield']['reason']
    assert ids['google']['ready'] is True


# ── estimate ─────────────────────────────────────────────────────────────────

def test_estimate_veo_is_a_dated_price_table(client):
    _campaign(_own(10))
    r = client.post('/api/desk/engines/estimate', json=_veo())
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    assert j['estimate']['usd'] == pytest.approx(3.2)               # $0.40/s x 8 s
    assert j['estimate']['basis'] == 'table' and j['estimate']['read'] == '2026-09-30'
    assert j['budget']['remaining'] == 10 and j['fits'] is True


def test_estimate_marks_openai_approximate(client):
    _campaign(_own(10))
    r = client.post('/api/desk/engines/estimate', json=_openai_img())
    assert r.status_code == 200, r.get_json()
    e = r.get_json()['estimate']
    assert e['approximate'] is True and e['basis'] == 'table' and 'approximate' in e['note']
    assert 0 < e['usd'] < 0.2


def test_estimate_higgsfield_uses_the_engine_endpoint_with_the_key(client, vendor):
    _campaign(_own(10))
    vendor.on('POST', '/estimate/', vendor.json(200, {'credits': '3', 'usd': '0.21'}))
    r = client.post('/api/desk/engines/estimate', json=_higgs_video())
    assert r.get_json()['estimate'] == {'usd': 0.21, 'basis': 'engine', 'read': r.get_json()['estimate']['read'],
                                        'approximate': False, 'note': None}
    call = vendor.to('/estimate/kling-video/v2.5-turbo/pro/text-to-video')[0]
    assert call['headers']['Authorization'] == f'Key {KEY_ID}:{KEY_SECRET}'
    assert json.loads(call['body']) == {'prompt': 'A calm product shot', 'duration': 5}
    _assert_no_secret(r.get_data(as_text=True))


def test_estimate_not_connected_names_the_vault_entry(client, vault):
    del vault['entries']['higgsfield']
    r = client.post('/api/desk/engines/estimate', json=_higgs_video())
    assert r.status_code == 409
    assert r.get_json()['code'] == 'not_connected' and r.get_json()['vault_entry'] == 'higgsfield'


def test_estimate_failure_is_refused_not_substituted(client, vendor):
    vendor.on('POST', '/estimate/', vendor.json(500, {'detail': 'boom'}))
    r = client.post('/api/desk/engines/estimate', json=_higgs_video())
    assert r.status_code == 502 and r.get_json()['code'] == 'estimate_failed'


# ── validation (scan rule 5) ─────────────────────────────────────────────────

@pytest.mark.parametrize('patch, needle', [
    ({'aspect_ratio': '1:1'}, 'cannot do 1:1'),
    ({'resolution': '1080p', 'duration_sec': 4}, 'duration_sec=8'),
    ({'duration_sec': 5}, 'seconds'),
    ({'model_id': 'veo-9'}, 'no model'),
    ({'engine_id': 'nope'}, 'unknown engine'),
    ({'prompt': '  '}, 'prompt is empty'),
    ({'kind': 'image'}, 'makes video'),
    ({'last_frame': {'path': 'x'}}, 'no last frame'),
    ({'audio': True, 'model_id': 'veo-3.1-lite-generate-preview', 'resolution': '4k'}, 'resolution'),
    ({'count': 2}, 'count must be'),
])
def test_validation_refuses_what_a_model_cannot_do(client, vendor, patch, needle):
    _campaign(_own(100))
    r = client.post('/api/desk/engines/estimate', json=_veo(**patch))
    assert r.status_code == 400, r.get_json()
    assert needle in r.get_json()['error']
    assert vendor.calls == []


def test_references_require_eight_seconds_and_a_model_that_takes_them(client, uploads):
    _campaign(_own(100))
    img = uploads / 'ref.png'
    img.write_bytes(_png())
    refs = [{'path': str(img)}]
    r = client.post('/api/desk/engines/estimate', json=_veo(reference_images=refs, duration_sec=4))
    assert r.status_code == 400 and 'duration_sec=8' in r.get_json()['error']
    r = client.post('/api/desk/engines/estimate', json=_veo(reference_images=refs * 4))
    assert r.status_code == 400 and 'at most 3' in r.get_json()['error']
    r = client.post('/api/desk/engines/estimate',
                    json=_veo(reference_images=refs, model_id='veo-3.1-lite-generate-preview'))
    assert r.status_code == 400 and 'at most 0' in r.get_json()['error']
    assert client.post('/api/desk/engines/estimate', json=_veo(reference_images=refs)).status_code == 200


def test_image_to_video_needs_a_first_frame(client):
    _campaign(_own(100))
    r = client.post('/api/desk/engines/estimate',
                    json=_higgs_video(model_id='kling-video/v2.5-turbo/pro/image-to-video'))
    assert r.status_code == 400 and 'needs a first frame' in r.get_json()['error']


def test_asset_outside_uploads_is_refused_before_any_upload(client, vendor, tmp_path):
    _campaign(_own(100))
    outside = tmp_path / 'secret.png'
    outside.write_bytes(_png())
    vendor.on('POST', '/estimate/', vendor.json(200, {'credits': '1', 'usd': '0.05'}))
    r = _submit(client, _higgs_video(model_id='kling-video/v2.5-turbo/pro/image-to-video',
                                     first_frame={'path': str(outside)}))
    assert r.status_code == 400 and 'data/uploads' in r.get_json()['error']
    assert not vendor.to('files/generate-upload-url') and not vendor.to('/kling-video/', 'POST')[1:]
    # the reservation was given back
    assert client.post('/api/desk/engines/estimate', json=_higgs_video()).get_json()['budget']['spent'] == 0


# ── submit gates ─────────────────────────────────────────────────────────────

def test_submit_refused_for_unattended_caller_and_nothing_sent(client, vendor, unattended):
    _campaign(_own(100))
    r = _submit(client, _veo())
    assert r.status_code == 403 and 'needs a human' in r.get_json()['error']
    assert vendor.calls == []
    assert not Path(eng.JOBS_PATH).exists()


@pytest.mark.parametrize('body', [{}, {'passcode': 'nope-wrong-code'}, {'passcode': 12345}])
def test_submit_refused_without_the_right_passcode(gated, vendor, body):
    _campaign(_own(100))
    r = _submit(gated, {**_veo(), **body}, headers=FORGED)
    assert r.status_code == 403 and r.get_json()['error'] == 'bad_passcode'
    assert vendor.calls == [] and not Path(eng.JOBS_PATH).exists()


def test_submit_refused_when_no_passcode_is_configured(gated, vendor, tmp_path, monkeypatch):
    from mc.blueprints import local_auth
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'absent.json')
    _campaign(_own(100))
    r = _submit(gated, {**_veo(), 'passcode': PASSCODE}, headers=FORGED)
    assert r.status_code == 403 and r.get_json()['error'] == 'passcode_required'
    assert vendor.calls == []


def test_submit_succeeds_with_passcode_and_never_stores_it(gated, vendor):
    _campaign(_own(100))
    vendor.on('POST', ':predictLongRunning', vendor.json(200, {'name': 'models/veo-3.1-generate-preview/operations/abc'}))
    r = _submit(gated, {**_veo(), 'passcode': PASSCODE}, headers=FORGED)
    assert r.status_code == 201, r.get_json()
    assert PASSCODE not in Path(eng.JOBS_PATH).read_text()
    assert PASSCODE not in r.get_data(as_text=True)


# ── budget: the campaign's how.budget is the only cap ────────────────────────

def test_no_budget_set_refuses_every_job(client, vendor):
    _campaign({'source': 'none'})
    r = _submit(client, _veo())
    assert r.status_code == 409 and r.get_json()['code'] == 'over_budget'
    assert r.get_json()['budget']['amount'] == 0
    assert not vendor.to(':predictLongRunning')


def test_campaign_without_a_budget_key_refuses_too(client, vendor):
    _campaign()
    assert _submit(client, _veo()).get_json()['code'] == 'over_budget'


def test_estimate_over_the_remaining_budget_is_refused_with_nothing_sent(client, vendor):
    _campaign(_own(3))                                              # Veo 8 s @ $0.40 = $3.20
    r = _submit(client, _veo())
    assert r.status_code == 409 and r.get_json()['code'] == 'over_budget'
    assert r.get_json()['estimate']['usd'] == pytest.approx(3.2) and r.get_json()['budget']['remaining'] == 3
    assert not vendor.to(':predictLongRunning')


def test_reservation_counts_so_a_second_job_past_the_remainder_is_refused(client, vendor):
    _campaign(_own(5))
    vendor.on('POST', ':predictLongRunning', vendor.json(200, {'name': 'models/m/operations/one'}))
    assert _submit(client, _veo()).status_code == 201               # $3.20 of $5
    r = _submit(client, _veo())                                     # another $3.20: only $1.80 left
    assert r.status_code == 409 and r.get_json()['budget']['remaining'] == pytest.approx(1.8)
    assert len(vendor.to(':predictLongRunning')) == 1


def test_ledger_post_costs_count_against_the_same_budget(client, vendor):
    _campaign(_own(5))
    _desk.record_published(platform='x', voice='v', body='b', campaign_id=CID, cost=2.0)
    r = _submit(client, _veo())                                     # $3.20 vs $3.00 left
    assert r.status_code == 409 and r.get_json()['budget']['spent'] == 2.0
    assert r.get_json()['budget']['remaining'] == pytest.approx(3.0)


def test_a_closed_or_unknown_campaign_is_refused(client, vendor):
    _campaign(_own(100), state='dropped')
    assert _submit(client, _veo()).get_json()['code'] == 'campaign_closed'
    assert _submit(client, _veo(campaign_id='nope')).status_code == 404
    assert _submit(client, {k: v for k, v in _veo().items() if k != 'campaign_id'}).status_code == 400


def test_idempotency_key_is_required(client):
    _campaign(_own(100))
    r = _submit(client, _veo(desk={}))
    assert r.status_code == 400 and 'idempotency_key' in r.get_json()['error']


def test_idempotent_retry_returns_the_first_job_with_one_vendor_call(client, vendor):
    _campaign(_own(100))
    vendor.on('POST', ':predictLongRunning', vendor.json(200, {'name': 'models/m/operations/one'}))
    body = _veo(desk={'idempotency_key': 'same'})
    a = _submit(client, body)
    b = _submit(client, body)
    assert (a.status_code, b.status_code) == (201, 200)
    assert a.get_json()['job']['job_id'] == b.get_json()['job']['job_id'] and b.get_json()['replay'] is True
    assert len(vendor.to(':predictLongRunning')) == 1
    assert client.post('/api/desk/engines/estimate', json=body).get_json()['budget']['spent'] == pytest.approx(3.2)


# ── Higgsfield ───────────────────────────────────────────────────────────────

def test_higgsfield_video_end_to_end(client, vendor, uploads):
    _campaign(_own(5))
    vendor.on('POST', '/estimate/', vendor.json(200, {'credits': '3', 'usd': '0.21'}))
    vendor.on('POST', '/kling-video/v2.5-turbo/pro/text-to-video',
              vendor.json(200, {'status': 'queued', 'request_id': 'req-1',
                                'status_url': 'https://evil.example/steal', 'cancel_url': 'x'}))
    vendor.on('GET', '/requests/req-1/status',
              vendor.json(200, {'status': 'queued'}), vendor.json(200, {'status': 'in_progress'}),
              vendor.json(200, {'status': 'completed', 'video': {'url': 'https://cdn.higgs.example/o.mp4'}}))
    vendor.on('GET', 'cdn.higgs.example', (200, {'content-type': 'video/mp4'}, b'MP4DATA'))

    j = _submit(client, _higgs_video()).get_json()['job']
    assert j['status'] == 'queued' and j['cost_usd'] == 0.21
    jid = j['job_id']
    assert json.loads(vendor.to('/kling-video/v2.5-turbo/pro/text-to-video', 'POST')[-1]['body']) == \
        {'prompt': 'A calm product shot', 'duration': 5}
    assert not any('evil.example' in c['url'] for c in vendor.calls)         # status_url is ours, not theirs

    assert client.get(f'/api/desk/engines/jobs/{jid}').get_json()['job']['status'] == 'queued'
    assert client.get(f'/api/desk/engines/jobs/{jid}').get_json()['job']['status'] == 'rendering'
    done = client.get(f'/api/desk/engines/jobs/{jid}').get_json()['job']
    assert done['status'] == 'ready' and done['cost_usd'] == 0.21
    out = done['outputs'][0]
    p = Path(out['local_path'])
    assert p.read_bytes() == b'MP4DATA' and p.suffix == '.mp4'
    assert uploads / 'desk' / 'generated' / CID in p.parents
    assert out['mime'] == 'video/mp4' and out['duration_sec'] == 5
    # the download carries no Higgsfield key
    assert 'Authorization' not in vendor.to('cdn.higgs.example')[0]['headers']
    # a terminal job is not polled again
    n = len(vendor.calls)
    assert client.get(f'/api/desk/engines/jobs/{jid}').get_json()['job']['status'] == 'ready'
    assert len(vendor.calls) == n
    _assert_no_secret(Path(eng.JOBS_PATH).read_text())


@pytest.mark.parametrize('status, kind, free', [('nsfw', 'moderation', True), ('failed', 'engine', True),
                                                ('canceled', 'canceled', True)])
def test_higgsfield_failed_runs_are_free_and_classified(client, vendor, status, kind, free):
    _campaign(_own(5))
    vendor.on('POST', '/estimate/', vendor.json(200, {'credits': '3', 'usd': '0.21'}))
    vendor.on('POST', '/kling-video/', vendor.json(200, {'status': 'queued', 'request_id': 'r2'}))
    vendor.on('GET', '/requests/r2/status', vendor.json(200, {'status': status}))
    jid = _submit(client, _higgs_video()).get_json()['job']['job_id']
    j = client.get(f'/api/desk/engines/jobs/{jid}').get_json()['job']
    assert j['status'] == 'failed' and j['failure']['kind'] == kind
    assert j['cost_usd'] == 0
    assert client.post('/api/desk/engines/estimate', json=_higgs_video()).get_json()['budget']['spent'] == 0


def test_higgsfield_first_frame_goes_through_a_presigned_upload_without_the_key(client, vendor, uploads):
    _campaign(_own(5))
    img = uploads / 'first.png'
    img.write_bytes(_png())
    vendor.on('POST', '/estimate/', vendor.json(200, {'credits': '3', 'usd': '0.21'}))
    vendor.on('POST', 'files/generate-upload-url', vendor.json(200, {
        'upload_url': 'https://s3.example/put', 'public_url': 'https://files.higgs.example/p.png',
        'content_type': 'image/png', 'upload_headers': {'Content-Type': 'image/png', 'x-amz-tagging': 't=1'}}))
    vendor.on('PUT', 's3.example/put', (200, {}, b''))
    vendor.on('POST', '/kling-video/v2.5-turbo/pro/image-to-video',
              vendor.json(200, {'status': 'queued', 'request_id': 'r3'}))
    r = _submit(client, _higgs_video(model_id='kling-video/v2.5-turbo/pro/image-to-video',
                                     first_frame={'path': str(img)}))
    assert r.status_code == 201, r.get_json()
    put = vendor.to('s3.example/put', 'PUT')[0]
    assert put['body'] == _png() and put['headers'] == {'Content-Type': 'image/png', 'x-amz-tagging': 't=1'}
    assert json.loads(vendor.to('/kling-video/v2.5-turbo/pro/image-to-video', 'POST')[-1]['body'])['image_url'] \
        == 'https://files.higgs.example/p.png'
    assert json.loads(vendor.to('files/generate-upload-url')[0]['body']) == {'content_type': 'image/png'}


def test_higgsfield_image_body_and_count(client, vendor):
    _campaign(_own(5))
    vendor.on('POST', '/estimate/', vendor.json(200, {'credits': '1', 'usd': '0.0128'}))
    vendor.on('POST', '/higgsfield-ai/soul/standard', vendor.json(200, {'status': 'queued', 'request_id': 'r4'}))
    r = _submit(client, {'engine_id': 'higgsfield', 'model_id': 'higgsfield-ai/soul/standard', 'kind': 'image',
                         'prompt': 'hero', 'aspect_ratio': '16:9', 'resolution': '4K', 'count': 4,
                         'campaign_id': CID, 'desk': {'idempotency_key': 'img1'}})
    assert r.status_code == 201, r.get_json()
    assert json.loads(vendor.to('/higgsfield-ai/soul/standard', 'POST')[-1]['body']) == \
        {'prompt': 'hero', 'num_images': 4, 'resolution': '4K', 'aspect_ratio': '16:9'}


# ── vendor errors at submit ──────────────────────────────────────────────────

def test_vendor_rejection_gives_the_reservation_back(client, vendor):
    _campaign(_own(5))
    vendor.on('POST', ':predictLongRunning', vendor.json(401, {'error': 'API key not valid'}))
    r = _submit(client, _veo())
    assert r.status_code == 502 and r.get_json()['failure'] == 'auth' and r.get_json()['code'] == 'engine_refused'
    assert client.post('/api/desk/engines/estimate', json=_veo()).get_json()['budget']['spent'] == 0


@pytest.mark.parametrize('status, body, kind', [
    (429, {'error': 'slow down'}, 'quota'), (400, {'error': 'prompt blocked by safety'}, 'moderation'),
    (422, {'error': 'bad'}, 'invalid_input'), (503, {'error': 'down'}, 'engine')])
def test_vendor_error_classes(client, vendor, status, body, kind):
    _campaign(_own(5))
    vendor.on('POST', ':predictLongRunning', vendor.json(status, body))
    assert _submit(client, _veo()).get_json()['failure'] == kind


def test_dropped_connection_keeps_the_reservation_and_the_idempotency_key(client, vendor):
    import urllib.error
    _campaign(_own(5))
    vendor.on('POST', ':predictLongRunning', urllib.error.URLError('reset'))
    body = _veo(desk={'idempotency_key': 'drop'})
    j = _submit(client, body).get_json()['job']
    assert j['status'] == 'failed' and 'may have accepted' in j['failure']['message']
    assert j['cost_usd'] == pytest.approx(3.2)
    r = _submit(client, body)
    assert r.status_code == 200 and r.get_json()['replay'] is True and len(vendor.to(':predictLongRunning')) == 1


def test_error_text_that_echoes_the_key_is_redacted(client, vendor):
    _campaign(_own(5))
    vendor.on('POST', ':predictLongRunning', vendor.json(400, {'error': f'bad key {GEMINI_KEY} supplied'}))
    r = _submit(client, _veo())
    assert r.status_code == 502
    _assert_no_secret(r.get_data(as_text=True))
    assert '[redacted:gemini-api]' in r.get_json()['error']


def test_unwired_job_store_refuses_before_any_vendor_call(client, vendor, monkeypatch):
    _campaign(_own(5))
    monkeypatch.setattr(eng, 'JOBS_PATH', None)
    with pytest.raises(RuntimeError, match='not wired'):
        eng.submit(_veo())
    assert not vendor.to(':predictLongRunning')


def test_unreadable_job_store_never_reads_as_nothing_spent(client, vendor):
    _campaign(_own(5))
    Path(eng.JOBS_PATH).write_text('{not json')
    with pytest.raises(RuntimeError, match='unreadable'):
        eng.budget_state(CID)


# ── Veo ──────────────────────────────────────────────────────────────────────

def _veo_submit(client, vendor, **kw):
    vendor.on('POST', ':predictLongRunning', vendor.json(200, {'name': 'models/veo-3.1-generate-preview/operations/op1'}))
    return _submit(client, _veo(**kw)).get_json()['job']['job_id']


def test_veo_end_to_end_download_carries_the_key_only_to_google(client, vendor, uploads):
    _campaign(_own(10))
    jid = _veo_submit(client, vendor)
    body = json.loads(vendor.to(':predictLongRunning', 'POST')[0]['body'])
    assert body == {'instances': [{'prompt': 'A calm product shot'}],
                    'parameters': {'aspectRatio': '16:9', 'resolution': '720p', 'durationSeconds': '8'}}
    assert vendor.to(':predictLongRunning', 'POST')[0]['headers']['x-goog-api-key'] == GEMINI_KEY

    op = '/v1beta/models/veo-3.1-generate-preview/operations/op1'
    vendor.on('GET', op, vendor.json(200, {'done': False}), vendor.json(200, {
        'done': True, 'response': {'generateVideoResponse': {'generatedSamples': [
            {'video': {'uri': 'https://generativelanguage.googleapis.com/v1beta/files/f1:download'}}]}}}))
    vendor.on('GET', 'files/f1:download', (302, {'location': 'https://storage.other.example/blob'}, b''))
    vendor.on('GET', 'storage.other.example/blob', (200, {'content-type': 'video/mp4'}, b'VEO'))

    assert client.get(f'/api/desk/engines/jobs/{jid}').get_json()['job']['status'] == 'rendering'
    done = client.get(f'/api/desk/engines/jobs/{jid}').get_json()['job']
    assert done['status'] == 'ready' and Path(done['outputs'][0]['local_path']).read_bytes() == b'VEO'
    assert vendor.to('files/f1:download')[0]['headers']['x-goog-api-key'] == GEMINI_KEY
    assert 'x-goog-api-key' not in vendor.to('storage.other.example')[0]['headers']      # not forwarded
    assert done['cost_usd'] == pytest.approx(3.2)


def test_veo_safety_block_is_moderation_and_the_cost_stays_reserved(client, vendor):
    _campaign(_own(10))
    jid = _veo_submit(client, vendor)
    vendor.on('GET', '/operations/op1', vendor.json(200, {'done': True, 'response': {'generateVideoResponse': {
        'raiMediaFilteredCount': 1, 'raiMediaFilteredReasons': ['blocked']}}}))
    j = client.get(f'/api/desk/engines/jobs/{jid}').get_json()['job']
    assert j['status'] == 'failed' and j['failure']['kind'] == 'moderation'
    assert j['cost_usd'] == pytest.approx(3.2)          # billing of blocked runs is unestablished: not refunded


def test_veo_operation_error_fails_the_job(client, vendor):
    _campaign(_own(10))
    jid = _veo_submit(client, vendor)
    vendor.on('GET', '/operations/op1', vendor.json(200, {'done': True, 'error': {'code': 13, 'message': 'internal'}}))
    j = client.get(f'/api/desk/engines/jobs/{jid}').get_json()['job']
    assert j['status'] == 'failed' and j['failure']['kind'] == 'engine'


def test_veo_download_failure_is_retried_then_expires_after_48h(client, vendor, monkeypatch):
    _campaign(_own(10))
    jid = _veo_submit(client, vendor)
    vendor.on('GET', '/operations/op1', vendor.json(200, {'done': True, 'response': {'generateVideoResponse': {
        'generatedSamples': [{'video': {'uri': 'https://generativelanguage.googleapis.com/dl'}}]}}}))
    vendor.on('GET', 'googleapis.com/dl', (503, {}, b'busy'))
    j = client.get(f'/api/desk/engines/jobs/{jid}').get_json()['job']
    assert j['status'] == 'rendering' and j['outputs'] == []
    store = json.loads(Path(eng.JOBS_PATH).read_text())
    store['jobs'][jid]['ready_seen_at'] = '2020-01-01T00:00:00Z'
    Path(eng.JOBS_PATH).write_text(json.dumps(store))
    j = client.get(f'/api/desk/engines/jobs/{jid}').get_json()['job']
    assert j['status'] == 'failed' and j['failure']['kind'] == 'expired'


def test_veo_references_and_first_frame_are_inline_base64(client, vendor, uploads):
    _campaign(_own(10))
    img = uploads / 'r.png'
    img.write_bytes(_png())
    _veo_submit(client, vendor, reference_images=[{'path': str(img)}], first_frame={'path': str(img)})
    inst = json.loads(vendor.to(':predictLongRunning', 'POST')[0]['body'])['instances'][0]
    want = {'inlineData': {'mimeType': 'image/png', 'data': base64.b64encode(_png()).decode()}}
    assert inst['image'] == want and inst['referenceImages'] == [{'image': want, 'referenceType': 'asset'}]


# ── Gemini image (sync) ──────────────────────────────────────────────────────

def test_gemini_image_is_ready_on_submit_and_saved_with_its_size(client, vendor, uploads):
    _campaign(_own(1))
    vendor.on('POST', '/v1beta/interactions', vendor.json(200, {
        'output_image': {'mime_type': 'image/png', 'data': base64.b64encode(_png(640, 480)).decode()}}))
    r = _submit(client, _gemini_img())
    j = r.get_json()['job']
    assert r.status_code == 201 and j['status'] == 'ready' and j['cost_usd'] == pytest.approx(0.067)
    out = j['outputs'][0]
    assert (out['width'], out['height'], out['mime']) == (640, 480, 'image/png')
    assert Path(out['local_path']).read_bytes() == _png(640, 480)
    assert uploads / 'desk' / 'generated' / CID in Path(out['local_path']).parents
    sent = json.loads(vendor.to('/v1beta/interactions')[0]['body'])
    assert sent['model'] == 'gemini-3.1-flash-image'
    assert sent['response_format'] == {'type': 'image', 'mime_type': 'image/png', 'aspect_ratio': '1:1', 'image_size': '1K'}
    assert vendor.to('/v1beta/interactions')[0]['headers']['x-goog-api-key'] == GEMINI_KEY


def test_gemini_image_with_no_image_in_the_answer_releases_the_reservation(client, vendor):
    _campaign(_own(1))
    vendor.on('POST', '/v1beta/interactions', vendor.json(200, {'output_text': 'blocked by safety'}))
    r = _submit(client, _gemini_img())
    assert r.status_code == 502 and r.get_json()['failure'] == 'moderation'
    assert client.post('/api/desk/engines/estimate', json=_gemini_img()).get_json()['budget']['spent'] == 0


def test_gemini_flash_lite_only_does_1k(client):
    _campaign(_own(1))
    r = client.post('/api/desk/engines/estimate', json=_gemini_img(model_id='gemini-3.1-flash-lite-image', resolution='2K'))
    assert r.status_code == 400 and 'resolution must be' in r.get_json()['error']


# ── OpenAI image (sync) ──────────────────────────────────────────────────────

def test_openai_image_billed_usage_replaces_the_estimate(client, vendor):
    _campaign(_own(1))
    vendor.on('POST', '/v1/images/generations', vendor.json(200, {
        'data': [{'b64_json': base64.b64encode(_png(10, 20)).decode()}],
        'usage': {'input_tokens': 100, 'output_tokens': 1000,
                  'input_tokens_details': {'text_tokens': 100, 'image_tokens': 0}}}))
    j = _submit(client, _openai_img(aspect_ratio='16:9')).get_json()['job']
    assert j['status'] == 'ready'
    assert j['cost_usd'] == pytest.approx((1000 * 30 + 100 * 5) / 1e6)          # $0.0305, from `usage`
    assert j['estimate']['approximate'] is True and j['estimate']['billed_usd'] == j['cost_usd']
    call = vendor.to('/v1/images/generations')[0]
    assert call['headers']['Authorization'] == f'Bearer {OPENAI_KEY}'
    assert json.loads(call['body']) == {'model': 'gpt-image-2.5-sunburst', 'prompt': 'A product hero',
                                        'size': '1536x864', 'quality': 'medium', 'output_format': 'png', 'n': 1}
    assert (j['outputs'][0]['width'], j['outputs'][0]['height']) == (10, 20)
    _assert_no_secret(Path(eng.JOBS_PATH).read_text())


def test_openai_references_use_the_edits_endpoint_multipart(client, vendor, uploads):
    _campaign(_own(1))
    img = uploads / 'o.png'
    img.write_bytes(_png())
    vendor.on('POST', '/v1/images/edits', vendor.json(200, {'data': [{'b64_json': base64.b64encode(_png()).decode()}]}))
    j = _submit(client, _openai_img(reference_images=[{'path': str(img)}])).get_json()['job']
    assert j['status'] == 'ready'
    call = vendor.to('/v1/images/edits')[0]
    assert call['headers']['Content-Type'].startswith('multipart/form-data; boundary=')
    assert b'name="image[]"; filename="ref0.png"' in call['body'] and _png() in call['body']
    assert b'name="model"\r\n\r\ngpt-image-2.5-sunburst' in call['body']
    assert not vendor.to('/v1/images/generations')


def test_openai_interrupted_sync_job_is_failed_with_cost_kept(client, vendor, monkeypatch):
    _campaign(_own(1))
    j = eng._new_job(eng.parse_request(_openai_img()), _desk.list_campaigns()[0], eng.Estimate(0.04, 'table', 'x'))
    j['created_at'] = '2020-01-01T00:00:00Z'
    j['status'] = 'rendering'
    store = eng._read_store()
    store['jobs'][j['job_id']] = j
    eng._write_store(store)
    out = client.get(f"/api/desk/engines/jobs/{j['job_id']}").get_json()['job']
    assert out['status'] == 'failed' and out['cost_usd'] == 0.04 and not vendor.calls


def test_unknown_job_is_404(client):
    assert client.get('/api/desk/engines/jobs/gen-nope').status_code == 404
