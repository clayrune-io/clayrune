"""mc/desk_publish.py -- the LinkedIn organization path and `verify_post` (R1-W S7).

What these tests guard:

  1. The request LinkedIn is sent: `POST /rest/posts`, author
     `urn:li:organization:<id>`, both required headers (`Linkedin-Version`,
     `X-Restli-Protocol-Version`), a bearer token from the LinkedIn vault entry
     and NOT the X one.
  2. The receipt: the post URN comes from the `x-restli-id` header (the API
     answers 201 with no body); the permalink is built from it.
  3. A 2xx without that header is an UNKNOWN outcome (`maybe_posted`), never a
     failure the caller may retry, and a missing organization id fails closed
     before any network call.
  4. Idempotency still holds on this path.
  5. `verify_post`: LinkedIn cannot be read back (None, not a fake True); X is
     checked through GET /2/tweets/:id; "could not check" raises, it is never
     reported as "not there".

No real network call anywhere in this file: `urllib.request.urlopen` is patched
on every test that reaches the transport.
"""
import email.message
import json
import sys
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk_publish  # noqa: E402
from mc import secrets_store  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_publish, 'RECEIPTS_PATH', tmp_path / 'desk_receipts.json')
    return desk_publish


def _item(item_id='v1', body='hello from the page', org='12345678'):
    return {'id': item_id, 'body': body, 'platform': 'linkedin', 'organization_id': org}


class _Resp:
    def __init__(self, headers=None, payload=None):
        self.headers = headers or {}
        self._data = json.dumps(payload or {}).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._data


def _http_error(code, body):
    return urllib.error.HTTPError('https://api.linkedin.com/rest/posts', code, 'err',
                                  hdrs=email.message.Message(), fp=BytesIO(body.encode('utf-8')))


def _secret(seen=None):
    def get(name, **kw):
        if seen is not None:
            seen.append(name)
        return 'tok-' + name
    return patch.object(secrets_store, 'get_secret_value', side_effect=get)


# -- the request ------------------------------------------------------------------

def test_request_shape_and_receipt(store):
    sent = {}

    def fake_urlopen(req, timeout=None):
        sent['url'] = req.full_url
        sent['method'] = req.get_method()
        sent['headers'] = {k.lower(): v for k, v in req.header_items()}
        sent['body'] = json.loads(req.data.decode('utf-8'))
        return _Resp({'x-restli-id': 'urn:li:share:7001'})

    secrets = []
    with _secret(secrets), patch('urllib.request.urlopen', fake_urlopen):
        receipt = store.publish(_item())
    assert sent['url'] == 'https://api.linkedin.com/rest/posts' and sent['method'] == 'POST'
    assert sent['headers']['authorization'] == 'Bearer tok-linkedin.oauth-token'
    assert sent['headers']['linkedin-version'] == desk_publish.LINKEDIN_API_VERSION
    assert sent['headers']['x-restli-protocol-version'] == '2.0.0'
    assert sent['body']['author'] == 'urn:li:organization:12345678'
    assert sent['body']['commentary'] == 'hello from the page'
    assert sent['body']['lifecycleState'] == 'PUBLISHED' and sent['body']['visibility'] == 'PUBLIC'
    assert secrets == ['linkedin.oauth-token']          # never the X token
    assert receipt['platform'] == 'linkedin'
    assert receipt['post_id'] == 'urn:li:share:7001'
    assert receipt['permalink'] == 'https://www.linkedin.com/feed/update/urn:li:share:7001/'
    assert receipt['body'] == 'hello from the page'


def test_idempotent_on_item_id(store):
    calls = {'n': 0}

    def fake_urlopen(req, timeout=None):
        calls['n'] += 1
        return _Resp({'x-restli-id': f'urn:li:share:{calls["n"]}'})

    with _secret(), patch('urllib.request.urlopen', fake_urlopen):
        first = store.publish(_item())
        second = store.publish(_item())
    assert calls['n'] == 1 and second == first


# -- refusals before the network ------------------------------------------------------

@pytest.mark.parametrize('org', ['', None, 'urn:li:organization:1', 'abc', '12 34'])
def test_missing_or_bad_organization_id_fails_closed(store, org):
    def boom(*a, **k):
        raise AssertionError('must not reach the network without a valid organization id')

    with _secret(), patch('urllib.request.urlopen', boom):
        with pytest.raises(desk_publish.PublishError, match='organization id'):
            store.publish(_item(org=org))
    assert not store.RECEIPTS_PATH.exists() or not json.loads(
        store.RECEIPTS_PATH.read_text(encoding='utf-8')).get('receipts')


def test_linkedin_replies_refused(store):
    item = _item()
    item['in_reply_to'] = '99'
    with pytest.raises(desk_publish.PublishError, match='replies'):
        store.publish(item)


# -- failures --------------------------------------------------------------------------

def test_http_error_is_a_failure_not_maybe_posted(store):
    def fake_urlopen(req, timeout=None):
        raise _http_error(403, '{"message":"ACCESS_DENIED: w_organization_social"}')

    with _secret(), patch('urllib.request.urlopen', fake_urlopen):
        with pytest.raises(desk_publish.PublishError, match='LinkedIn API HTTP 403') as ei:
            store.publish(_item())
    assert ei.value.maybe_posted is False
    assert 'ACCESS_DENIED' in str(ei.value)
    assert not json.loads(store.RECEIPTS_PATH.read_text(encoding='utf-8')).get('receipts') \
        if store.RECEIPTS_PATH.exists() else True


def test_no_response_is_maybe_posted(store):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError('connection reset')

    with _secret(), patch('urllib.request.urlopen', fake_urlopen):
        with pytest.raises(desk_publish.PublishError, match='MAY') as ei:
            store.publish(_item())
    assert ei.value.maybe_posted is True


def test_2xx_without_post_id_is_maybe_posted(store):
    with _secret(), patch('urllib.request.urlopen', lambda req, timeout=None: _Resp({})):
        with pytest.raises(desk_publish.PublishError, match='x-restli-id') as ei:
            store.publish(_item())
    assert ei.value.maybe_posted is True


def test_missing_credential_fails_before_any_call(store):
    def boom(*a, **k):
        raise AssertionError('no network without a credential')

    with patch.object(secrets_store, 'get_secret_value',
                      side_effect=secrets_store.SecretsError('no such secret')), \
            patch('urllib.request.urlopen', boom):
        with pytest.raises(desk_publish.PublishError, match='credential unavailable'):
            store.publish(_item())


# -- verify_post -----------------------------------------------------------------------------

def test_verify_linkedin_cannot_be_asked(store):
    def boom(*a, **k):
        raise AssertionError('LinkedIn is never read back')

    with patch('urllib.request.urlopen', boom), patch.object(secrets_store, 'get_secret_value', boom):
        assert store.verify_post('linkedin', 'urn:li:share:1') is None


def test_verify_x_true_false_and_cannot_check(store):
    with patch.object(secrets_store, 'get_secret_value', return_value='tok'):
        with patch.object(desk_publish, '_get_tweet', lambda t, i: {'data': {'id': '77'}}):
            assert store.verify_post('x', '77') is True
        with patch.object(desk_publish, '_get_tweet', lambda t, i: {'errors': [{'title': 'Not Found'}]}):
            assert store.verify_post('x', '77') is False

        def missing(t, i):
            raise _http_error(404, '{}')
        with patch.object(desk_publish, '_get_tweet', missing):
            assert store.verify_post('x', '77') is False

        def rate_limited(t, i):
            raise _http_error(429, 'slow down')
        with patch.object(desk_publish, '_get_tweet', rate_limited):
            with pytest.raises(desk_publish.PublishError, match='429'):
                store.verify_post('x', '77')

        def down(t, i):
            raise urllib.error.URLError('down')
        with patch.object(desk_publish, '_get_tweet', down):
            with pytest.raises(desk_publish.PublishError, match='could not reach X'):
                store.verify_post('x', '77')


def test_verify_x_credential_unavailable_raises(store):
    with patch.object(secrets_store, 'get_secret_value',
                      side_effect=secrets_store.SecretsError('locked')):
        with pytest.raises(desk_publish.PublishError, match='credential unavailable'):
            store.verify_post('x', '77')
