"""mc/desk_publish.py -- the only module that posts to X.

What these tests guard, in the order it would cost to lose them:

  1. Idempotency: a repeat `publish()` call for the same item id must never
     make a second HTTP call and must return the FIRST receipt unchanged.
     This is the single guarantee standing between a retried route/tick and a
     duplicate, BILLED post (docs/THE_DESK_SIMPLIFICATION_PLAN.md step 2).
  2. Failure is loud and terminal: 4xx, 5xx, timeout and a missing credential
     all raise PublishError with the error text kept, and none of them ever
     write a receipt (a receipt with no real post would poison the
     idempotency guard forever).
  3. A missing credential fails closed BEFORE any network call.
  4. The receipt shape: permalink, post id, UTC time, exact body posted.

No real network call anywhere in this file. The HTTP-shape tests (success,
4xx, 5xx, timeout, malformed response) patch `urllib.request.urlopen` itself
so `_post_tweet`'s own parsing and error-extraction code actually runs; the
pure-logic tests (idempotency call counting, credential failure, input
validation) patch `_post_tweet` directly since the HTTP shape is not what
they're checking.
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
    monkeypatch.setattr(desk_publish, '_get_username', lambda token: 'clayrune')
    return desk_publish


def _item(item_id='item1', body='hello world', platform='x'):
    return {'id': item_id, 'body': body, 'platform': platform}


class _FakeUrlopenResponse:
    """Stands in for the context manager `urllib.request.urlopen` returns."""
    def __init__(self, payload):
        self._data = json.dumps(payload).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._data


def _http_error(code, reason, body):
    return urllib.error.HTTPError(
        'https://api.x.com/2/tweets', code, reason,
        hdrs=email.message.Message(), fp=BytesIO(body.encode('utf-8')))


def _mock_ok(post_id='171234'):
    return patch.object(desk_publish, '_post_tweet',
                        lambda token, body: {'data': {'id': post_id, 'text': body}})


# -- success + receipt shape, via the real urlopen boundary --------------------

def test_publish_success_returns_full_receipt(store):
    resp = _FakeUrlopenResponse({'data': {'id': '999', 'text': 'shipped the thing',
                                          'edit_history_post_ids': ['999']}})
    with patch.object(secrets_store, 'get_secret_value', return_value='tok'), \
         patch('urllib.request.urlopen', return_value=resp):
        receipt = store.publish(_item(body='shipped the thing'))
    assert receipt['item_id'] == 'item1'
    assert receipt['post_id'] == '999'
    assert receipt['permalink'] == 'https://x.com/clayrune/status/999'
    assert receipt['body'] == 'shipped the thing'
    assert receipt['posted_at'].endswith('Z')


def test_publish_persists_receipt_to_disk(store):
    with patch.object(secrets_store, 'get_secret_value', return_value='tok'), _mock_ok('42'):
        store.publish(_item())
    on_disk = json.loads(store.RECEIPTS_PATH.read_text(encoding='utf-8'))
    assert on_disk['receipts']['item1']['post_id'] == '42'


def test_username_lookup_failure_falls_back_to_generic_permalink(store, monkeypatch):
    def _boom(token):
        raise RuntimeError('users/me unavailable')
    monkeypatch.setattr(desk_publish, '_get_username', _boom)
    with patch.object(secrets_store, 'get_secret_value', return_value='tok'), _mock_ok('55'):
        receipt = store.publish(_item())
    # The post already happened on X -- a lookup failure must not be raised
    # as a PublishError, and the fallback permalink form must still resolve.
    assert receipt['permalink'] == 'https://x.com/i/web/status/55'
    assert receipt['post_id'] == '55'


# -- idempotency ---------------------------------------------------------------

def test_duplicate_call_returns_first_receipt_and_makes_no_second_call(store):
    calls = {'n': 0}

    def _post(token, body):
        calls['n'] += 1
        return {'data': {'id': f'post-{calls["n"]}', 'text': body}}

    with patch.object(secrets_store, 'get_secret_value', return_value='tok'), \
         patch.object(desk_publish, '_post_tweet', _post):
        first = store.publish(_item())
        second = store.publish(_item())

    assert calls['n'] == 1, 'a second call must never reach the network'
    assert second == first
    assert second['post_id'] == 'post-1'


def test_different_items_each_post_once(store):
    with patch.object(secrets_store, 'get_secret_value', return_value='tok'), _mock_ok('1'):
        r1 = store.publish(_item('item1'))
    with patch.object(secrets_store, 'get_secret_value', return_value='tok'), _mock_ok('2'):
        r2 = store.publish(_item('item2'))
    assert r1['item_id'] != r2['item_id']
    assert r1['post_id'] != r2['post_id']


# -- failure: loud, terminal, no receipt ---------------------------------------

def test_missing_credential_fails_closed_before_any_network_call(store):
    def _no_call(token, body):
        raise AssertionError('must not POST without a resolved credential')

    with patch.object(secrets_store, 'get_secret_value',
                      side_effect=secrets_store.SecretNotFound('no such secret')), \
         patch.object(desk_publish, '_post_tweet', _no_call):
        with pytest.raises(desk_publish.PublishError, match='credential unavailable'):
            store.publish(_item())
    assert store.get_receipt('item1') is None


def test_secret_denied_also_fails_closed(store):
    with patch.object(secrets_store, 'get_secret_value',
                      side_effect=secrets_store.SecretDenied('scoped elsewhere')):
        with pytest.raises(desk_publish.PublishError, match='credential unavailable'):
            store.publish(_item())
    assert store.get_receipt('item1') is None


def test_http_4xx_via_real_urlopen_boundary_raises_and_keeps_body(store):
    err = _http_error(403, 'Forbidden', '{"title": "Forbidden", "detail": "duplicate content"}')
    with patch.object(secrets_store, 'get_secret_value', return_value='tok'), \
         patch('urllib.request.urlopen', side_effect=err):
        with pytest.raises(desk_publish.PublishError, match='HTTP 403.*duplicate content'):
            store.publish(_item())
    assert store.get_receipt('item1') is None


def test_http_5xx_via_real_urlopen_boundary_raises_and_no_receipt(store):
    err = _http_error(500, 'Internal Server Error', '{"title": "Internal error"}')
    with patch.object(secrets_store, 'get_secret_value', return_value='tok'), \
         patch('urllib.request.urlopen', side_effect=err):
        with pytest.raises(desk_publish.PublishError, match='HTTP 500'):
            store.publish(_item())
    assert store.get_receipt('item1') is None


def test_timeout_via_real_urlopen_boundary_raises_and_no_receipt(store):
    with patch.object(secrets_store, 'get_secret_value', return_value='tok'), \
         patch('urllib.request.urlopen', side_effect=TimeoutError('timed out')):
        with pytest.raises(desk_publish.PublishError, match='request failed'):
            store.publish(_item())
    assert store.get_receipt('item1') is None


def test_url_error_via_real_urlopen_boundary_raises_and_no_receipt(store):
    with patch.object(secrets_store, 'get_secret_value', return_value='tok'), \
         patch('urllib.request.urlopen',
              side_effect=urllib.error.URLError('network unreachable')):
        with pytest.raises(desk_publish.PublishError, match='request failed'):
            store.publish(_item())
    assert store.get_receipt('item1') is None


def test_malformed_response_missing_post_id_raises_and_no_receipt(store):
    resp = _FakeUrlopenResponse({'data': {'text': 'no id in here'}})
    with patch.object(secrets_store, 'get_secret_value', return_value='tok'), \
         patch('urllib.request.urlopen', return_value=resp):
        with pytest.raises(desk_publish.PublishError, match='no post id'):
            store.publish(_item())
    assert store.get_receipt('item1') is None


def test_failed_call_can_be_retried_by_the_caller_and_then_succeeds(store):
    """Idempotency caches SUCCESS only -- a failed attempt is not latched."""
    with patch.object(secrets_store, 'get_secret_value',
                      side_effect=secrets_store.SecretNotFound('no such secret')):
        with pytest.raises(desk_publish.PublishError):
            store.publish(_item())

    with patch.object(secrets_store, 'get_secret_value', return_value='tok'), _mock_ok('7'):
        receipt = store.publish(_item())
    assert receipt['post_id'] == '7'


# -- input validation ------------------------------------------------------------

def test_missing_item_id_raises_without_touching_network(store):
    def _no_call(token, body):
        raise AssertionError('must not POST without an id')
    with patch.object(desk_publish, '_post_tweet', _no_call):
        with pytest.raises(desk_publish.PublishError, match="no 'id'"):
            store.publish({'body': 'x', 'platform': 'x'})


def test_empty_body_raises_without_touching_network(store):
    def _no_call(token, body):
        raise AssertionError('must not POST an empty body')
    with patch.object(desk_publish, '_post_tweet', _no_call):
        with pytest.raises(desk_publish.PublishError, match='no body'):
            store.publish(_item(body='   '))


def test_non_x_platform_refused_without_touching_network(store):
    def _no_call(token, body):
        raise AssertionError('must not POST for an unsupported platform')
    with patch.object(desk_publish, '_post_tweet', _no_call):
        with pytest.raises(desk_publish.PublishError, match='only posts to X'):
            store.publish(_item(platform='linkedin'))


def test_unwired_receipts_path_fails_closed_before_any_post(monkeypatch):
    monkeypatch.setattr(desk_publish, 'RECEIPTS_PATH', None)

    def _no_call(token, body):
        raise AssertionError('must not POST without a durable receipts path')

    with patch.object(secrets_store, 'get_secret_value', return_value='tok'), \
         patch.object(desk_publish, '_post_tweet', _no_call):
        with pytest.raises(desk_publish.PublishError, match='RECEIPTS_PATH is not wired'):
            desk_publish.publish(_item())


def test_corrupt_receipts_store_refuses_to_post(store):
    # An unreadable store cannot prove an item was NOT already posted, so it
    # must never read as "no receipt yet" on the publish path.
    store.RECEIPTS_PATH.write_text('{not json', encoding='utf-8')

    def _no_call(token, body):
        raise AssertionError('must not POST when the receipts store is unreadable')

    with patch.object(secrets_store, 'get_secret_value', return_value='tok'), \
         patch.object(desk_publish, '_post_tweet', _no_call):
        with pytest.raises(desk_publish.PublishError, match='unreadable'):
            store.publish(_item())
    assert store.RECEIPTS_PATH.read_text(encoding='utf-8') == '{not json'


def test_timeout_error_warns_the_post_may_have_gone_out(store):
    with patch.object(secrets_store, 'get_secret_value', return_value='tok'), \
         patch('urllib.request.urlopen', side_effect=TimeoutError('timed out')):
        with pytest.raises(desk_publish.PublishError, match='MAY have gone out'):
            store.publish(_item())
