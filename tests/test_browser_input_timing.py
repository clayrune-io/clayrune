"""Input timing contract: strict privacy projection, bounded storage and stages."""
import json

from flask import Flask
import pytest

from mc import browser_input_timing as timing


@pytest.fixture
def client():
    with timing._lock:
        timing._samples.clear()
        timing._total = 0
    app = Flask(__name__)
    app.register_blueprint(timing.bp)
    return app.test_client()


def sample(**changes):
    return dict(client_id=123, sample_id=1, request_id=1, queue_depth=2,
                t_key=10, t_sent=20, t_ack=50, t_frame=60,
                outcome=1, http_status=200, transport=1) | changes


def post(client, samples=None, **changes):
    return client.post('/api/browser/input-timing', json={
        'user_agent': 'phone', 'samples': [sample()] if samples is None else samples,
        **changes})


def test_roundtrip_and_incremental_no_cache(client):
    assert post(client).json == {'ok': True, 'accepted': 1}
    assert post(client, [sample(sample_id=2)]).status_code == 200
    response = client.get('/api/browser/input-timing?after=1')
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.json['total_received'] == 2
    assert response.json['samples'] == [{**sample(sample_id=2), 'user_agent': 'phone', 'index': 2}]


@pytest.mark.parametrize('extra', ['text', 'key', 'url', 'session_id', 'password'])
def test_rejects_non_numeric_fields_without_echo(client, extra):
    response = post(client, [sample(**{extra: 'sensitive-marker'})])
    assert response.status_code == 400
    assert 'sensitive-marker' not in response.text
    assert client.get('/api/browser/input-timing').json['retained'] == 0


@pytest.mark.parametrize('changes', [
    {'t_key': True}, {'t_sent': '20'}, {'t_ack': float('nan')},
    {'t_frame': float('inf')}, {'t_key': -1}, {'t_key': 1e20},
    {'sample_id': 1.5}, {'outcome': 4}, {'transport': 3},
    {'queue_depth': 501}, {'http_status': 600}, {'t_sent': 9},
    {'t_ack': 19}, {'t_frame': 49}, {'t_ack': None},
    {'t_frame': None}, {'outcome': 0}, {'outcome': 2},
])
def test_rejects_bad_stage_numbers(client, changes):
    assert post(client, [sample(**changes)]).status_code == 400


@pytest.mark.parametrize('outcome,ack', [(0, 50), (2, None), (2, 50), (3, None)])
def test_missing_frame_and_failure_are_explicit(client, outcome, ack):
    assert post(client, [sample(outcome=outcome, t_ack=ack, t_frame=None)]).status_code == 200


def test_atomic_validation_does_not_store_valid_prefix(client):
    assert post(client, [sample(), sample(text='private')]).status_code == 400
    assert timing._total == 0


@pytest.mark.parametrize('data', [None, [], {}, {'samples': []},
    {'user_agent': 'phone', 'samples': []},
    {'user_agent': 'phone', 'samples': [sample()] * 51},
    {'user_agent': 42, 'samples': [sample()]},
    {'user_agent': 'a' * 513, 'samples': [sample()]},
    {'user_agent': 'phone\nsecret', 'samples': [sample()]},
    {'user_agent': 'phone', 'samples': [sample()], 'text': 'sensitive-marker'},
])
def test_rejects_malformed_batches(client, data):
    assert client.post('/api/browser/input-timing', data=json.dumps(data),
                       content_type='application/json').status_code == 400


def test_body_limit(client):
    assert client.post('/api/browser/input-timing', data=b'x' * (timing.BODY_LIMIT + 1),
                       content_type='application/json').status_code == 413


def test_ring_eviction(client):
    for batch in range(21):
        assert post(client, [sample(sample_id=batch * 50 + n) for n in range(50)]).status_code == 200
    data = client.get('/api/browser/input-timing').json
    assert data['retained'] == data['capacity'] == 1000
    assert data['evicted'] == 50
    assert data['samples'][0]['index'] == 51
    assert data['samples'][-1]['index'] == 1050


@pytest.mark.parametrize('after', ['-1', 'bad', '1.5'])
def test_rejects_bad_cursor(client, after):
    assert client.get('/api/browser/input-timing?after=' + after).status_code == 400
