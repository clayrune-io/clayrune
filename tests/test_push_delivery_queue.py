"""SF15 (MC-1179 pass 11): push delivery must not run on an agent's reader.

`_handle_push_signal` is called from the agent stream-reader threads once per
`result` message. It used to deliver inline: one call to `webpush` per
subscribed device with no timeout, so a push service that accepted the
connection and never answered froze the reader for good. Delivery now goes to
ONE background worker through a bounded queue (mc/push_delivery_queue.py), and
`webpush` gets timeout=10.

No test here makes a network call: `pywebpush` is replaced in sys.modules by a
stub for every test, and the subscription/key/inbox stores are patched.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import sys
import threading
import time
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import push_mobile as pm  # noqa: E402

ENDPOINT = 'https://push.example.test/wpush/v2/SECRETTOKEN'
KEYS = {'p256dh': 'PKEYMATERIAL', 'auth': 'AUTHSECRET'}


class _StubWebPush:
    """Stand-in for pywebpush: records calls, optionally blocks or raises."""

    def __init__(self):
        self.calls = []
        self.release = threading.Event()
        self.block = False
        self.raises = None
        self.entered = threading.Semaphore(0)

    class WebPushException(Exception):
        def __init__(self, msg, response=None):
            super().__init__(msg)
            self.response = response

    def webpush(self, **kw):
        self.calls.append(kw)
        self.entered.release()
        if self.block:
            self.release.wait(timeout=30)
        if self.raises is not None:
            raise self.raises
        return 'ok'


@pytest.fixture()
def stub(monkeypatch):
    s = _StubWebPush()
    mod = types.ModuleType('pywebpush')
    mod.webpush = s.webpush  # type: ignore[attr-defined]
    mod.WebPushException = s.WebPushException  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, 'pywebpush', mod)
    monkeypatch.setattr(pm, '_load_vapid_keys', lambda: {'private': 'PRIVATEKEY', 'public': 'p'})
    monkeypatch.setattr(pm, '_load_push_subscriptions',
                        lambda: {'n1': {'endpoint': ENDPOINT, 'keys': dict(KEYS)}})
    monkeypatch.setattr(pm, '_save_push_subscriptions', lambda subs: None)
    monkeypatch.setattr(pm, '_append_notification', lambda **kw: None)
    monkeypatch.setattr(pm, '_push_subject', lambda: 'mailto:test@example.test')
    monkeypatch.setattr(pm, 'load_project', lambda pid: {'name': 'P'})
    monkeypatch.setattr(pm, '_is_being_watched', lambda pid, sid: False)
    logs = []
    monkeypatch.setattr(pm, '_log', lambda msg, *a, **k: logs.append(str(msg)))
    s.logs = logs  # type: ignore[attr-defined]
    yield s
    s.release.set()


@pytest.fixture()
def fresh_queue(monkeypatch, stub):
    """A private queue so no worker thread outlives the test."""
    from mc import push_delivery_queue as pdq
    made = []

    def make(max_pending=200):
        q = pdq.DeliveryQueue(max_pending=max_pending)
        monkeypatch.setattr(pdq, '_log', lambda msg, *a, **k: stub.logs.append(str(msg)))
        monkeypatch.setattr(pm, '_push_queue', q)
        made.append(q)
        return q

    yield make
    stub.release.set()
    for q in made:
        q.stop()


def _result(text):
    return {'type': 'result', 'result': text}


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def _call_in_thread(fn, *a):
    box = {}

    def run():
        t0 = time.monotonic()
        fn(*a)
        box['elapsed'] = time.monotonic() - t0

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(5)
    return th, box


def test_a_blocked_webpush_does_not_block_the_reader(stub, fresh_queue):
    fresh_queue()
    stub.block = True
    th, box = _call_in_thread(pm._handle_push_signal, 'p', 's', _result('done'))
    try:
        assert not th.is_alive(), '_handle_push_signal is stuck behind webpush'
        assert box['elapsed'] < 1.0
        assert stub.entered.acquire(timeout=5), 'the worker never reached webpush'
    finally:
        stub.release.set()


def test_pushes_are_delivered_in_order(stub, fresh_queue):
    fresh_queue()
    for i in range(5):
        pm._handle_push_signal('p', 's', _result(f'turn {i}'))
    assert _wait(lambda: len(stub.calls) == 5)
    import json
    assert [json.loads(c['data'])['body'] for c in stub.calls] == [f'turn {i}' for i in range(5)]


def test_a_stalled_endpoint_delays_later_pushes_not_the_caller(stub, fresh_queue):
    fresh_queue()
    stub.block = True
    pm._handle_push_signal('p', 's', _result('first'))
    assert stub.entered.acquire(timeout=5)
    th, box = _call_in_thread(pm._handle_push_signal, 'p', 's', _result('second'))
    assert not th.is_alive() and box['elapsed'] < 1.0
    assert len(stub.calls) == 1  # the second waits behind the stalled first
    stub.block = False
    stub.release.set()
    assert _wait(lambda: len(stub.calls) == 2)


@pytest.mark.parametrize('update', ['subscribe', 'unsubscribe', 'replace'])
def test_delivery_keeps_concurrent_subscription_changes(stub, monkeypatch, update):
    from copy import deepcopy

    initial = {'endpoint': ENDPOINT, 'keys': dict(KEYS)}
    persisted = {'n1': initial}
    monkeypatch.setattr(pm, '_load_push_subscriptions', lambda: deepcopy(persisted))

    def save(value):
        persisted.clear()
        persisted.update(deepcopy(value))

    def webpush(**kwargs):
        with pm._push_state_lock:
            if update == 'subscribe':
                persisted['n2'] = {'endpoint': 'https://push.example.test/new'}
            elif update == 'unsubscribe':
                del persisted['n1']
            else:
                persisted['n1'] = {'endpoint': 'https://push.example.test/replacement'}
        if update == 'replace':
            raise stub.WebPushException('gone', types.SimpleNamespace(status_code=410))

    monkeypatch.setattr(pm, '_save_push_subscriptions', save)
    monkeypatch.setattr(sys.modules['pywebpush'], 'webpush', webpush)

    outcome = pm._notify_push('title', 'body')

    if update == 'subscribe':
        assert persisted['n2']['endpoint'].endswith('/new')
        assert persisted['n1']['last_used_at'] > 0
    elif update == 'unsubscribe':
        assert persisted == {}
    else:
        assert persisted['n1']['endpoint'].endswith('/replacement')
        assert outcome['removed'] == 0


@pytest.mark.parametrize('content', [b'{', b'null', b'{"public": "test", "private": "-----BEGIN malformed"}'])
def test_bad_vapid_file_keeps_the_original_bytes(monkeypatch, tmp_path, content):
    path = tmp_path / 'vapid.json'
    path.write_bytes(content)
    monkeypatch.setattr(pm, 'PUSH_VAPID_PATH', path)

    assert pm._load_vapid_keys() == {}
    assert path.read_bytes() == content


def test_corrupt_vapid_keys_do_not_block_fcm(monkeypatch, tmp_path):
    path = tmp_path / 'vapid.json'
    path.write_bytes(b'{')
    monkeypatch.setattr(pm, 'PUSH_VAPID_PATH', path)
    monkeypatch.setattr(pm, '_load_push_subscriptions', lambda: {
        'web': {'endpoint': ENDPOINT, 'keys': KEYS},
        'android': {'type': 'fcm', 'token': 'test-token'},
    })
    monkeypatch.setattr(pm, '_save_push_subscriptions', lambda subs: None)
    delivered = []

    def fcm(sub, payload):
        delivered.append((sub['token'], payload['body']))
        return True, '', False

    monkeypatch.setattr(pm, '_push_send_fcm', fcm)
    result = pm._notify_push('title', 'body', log_inbox=False)
    assert delivered == [('test-token', 'body')]
    assert result['sent'] == 1
    assert result['failed'] == 1
    assert path.read_bytes() == b'{'


def test_simultaneous_first_public_key_requests_publish_one_pair(monkeypatch, tmp_path):
    from flask import Flask
    from py_vapid import Vapid01

    path = tmp_path / 'vapid.json'
    monkeypatch.setattr(pm, 'PUSH_VAPID_PATH', path)
    first_generating = threading.Event()
    second_requested = threading.Event()
    second_generating = threading.Event()
    release_first = threading.Event()
    generate = Vapid01.generate_keys
    app = Flask(__name__)
    app.register_blueprint(pm.bp)

    def generate_keys(vapid):
        if first_generating.is_set():
            second_generating.set()
        else:
            first_generating.set()
            assert release_first.wait(5)
        generate(vapid)

    monkeypatch.setattr(Vapid01, 'generate_keys', generate_keys)

    def public_key(requested=None):
        if requested is not None:
            requested.set()
        with app.test_client() as client:
            return client.get('/api/push/vapid-public-key').get_json()['public_key']

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(public_key)
        try:
            assert first_generating.wait(5)
            second = pool.submit(public_key, second_requested)
            assert second_requested.wait(5)
            second_generating.wait(0.5)
        finally:
            release_first.set()
        first_key = first.result(timeout=5)
        second_key = second.result(timeout=5)

    assert not second_generating.is_set()
    assert first_key == second_key == json.loads(path.read_text(encoding='utf-8'))['public']


def test_turn_end_uses_one_project_read_for_the_inbox(monkeypatch, tmp_path):
    reads = []
    long_name = 'Long project name ' * 6
    path = tmp_path / 'inbox.json'
    monkeypatch.setattr(pm, 'PUSH_NOTIF_PATH', path)
    monkeypatch.setattr(pm, 'load_project', lambda pid: reads.append(pid) or {'name': long_name})
    monkeypatch.setattr(pm, '_is_being_watched', lambda *a: False)
    monkeypatch.setattr(pm, '_push_queue', types.SimpleNamespace(submit=lambda fn: None))

    pm._handle_push_signal('p', 's', _result('done'))

    assert reads == ['p']
    assert pm._load_notifications()[0]['project_name'] == long_name


def test_webpush_gets_a_ten_second_timeout(stub, fresh_queue):
    fresh_queue()
    pm._handle_push_signal('p', 's', _result('done'))
    assert _wait(lambda: len(stub.calls) == 1)
    assert stub.calls[0]['timeout'] == 10


def test_a_full_queue_drops_the_new_push_and_says_so_once(stub, fresh_queue):
    fresh_queue(max_pending=2)
    stub.block = True
    pm._handle_push_signal('p', 's', _result('running'))     # taken by the worker
    assert stub.entered.acquire(timeout=5)
    pm._handle_push_signal('p', 's', _result('queued 1'))
    pm._handle_push_signal('p', 's', _result('queued 2'))     # queue now full (2)
    th, box = _call_in_thread(pm._handle_push_signal, 'p', 's', _result('dropped'))
    assert not th.is_alive() and box['elapsed'] < 1.0, 'a full queue must never block the caller'
    full_lines = [m for m in stub.logs if 'queue full' in m]
    assert len(full_lines) == 1
    stub.block = False
    stub.release.set()
    assert _wait(lambda: len(stub.calls) == 3)
    time.sleep(0.2)
    import json
    assert [json.loads(c['data'])['body'] for c in stub.calls] == ['running', 'queued 1', 'queued 2']


def test_the_worker_is_a_daemon_thread_started_on_first_use(stub, fresh_queue):
    q = fresh_queue()
    assert q.thread is None
    pm._handle_push_signal('p', 's', _result('done'))
    assert q.thread is not None and q.thread.daemon and q.thread.is_alive()
    first = q.thread
    pm._handle_push_signal('p', 's', _result('again'))
    assert q.thread is first, 'one worker, not one per push'


def test_the_unattended_flags_are_still_decided_on_the_callers_thread(stub, fresh_queue, monkeypatch):
    fresh_queue()
    monkeypatch.setattr(pm, 'load_project', lambda pid: {'name': 'P', 'notify_turn_complete': False})
    pm._handle_push_signal('p', 's', _result('done'))
    time.sleep(0.2)
    assert stub.calls == []


@pytest.mark.parametrize('exc_kind', ['generic', 'webpush_with_response'])
def test_a_failed_delivery_logs_the_endpoint_host_only(stub, exc_kind):
    if exc_kind == 'generic':
        stub.raises = RuntimeError(
            "HTTPSConnectionPool(host='push.example.test', port=443): Max retries exceeded "
            "with url: /wpush/v2/SECRETTOKEN (Caused by ReadTimeoutError)")
    else:
        resp = types.SimpleNamespace(status_code=500,
                                     text=f'bad gateway for {ENDPOINT} auth=AUTHSECRET')
        stub.raises = stub.WebPushException(f'Push failed: 500 for {ENDPOINT}', response=resp)
    result = pm._notify_push('t', 'b', log_inbox=False)
    assert result['failed'] == 1
    joined = '\n'.join(stub.logs)
    assert 'push.example.test' in joined
    for secret in ('SECRETTOKEN', '/wpush/v2', 'PKEYMATERIAL', 'AUTHSECRET', 'PRIVATEKEY'):
        assert secret not in joined, secret


def test_the_inbox_row_is_written_at_once_even_when_delivery_is_stalled_or_dropped(stub, fresh_queue, monkeypatch):
    """The Inbox is the history of what pushed: it is written on the caller's
    thread, once per push, and never by the deferred delivery job."""
    rows = []
    monkeypatch.setattr(pm, '_append_notification', lambda **kw: rows.append(kw))
    fresh_queue(max_pending=1)
    stub.block = True
    pm._handle_push_signal('p', 's', _result('first'))     # taken by the worker, stuck in webpush
    assert stub.entered.acquire(timeout=5)
    assert [r['body'] for r in rows] == ['first']           # written before delivery finished
    pm._handle_push_signal('p', 's', _result('second'))    # fills the queue
    pm._handle_push_signal('p', 's', _result('third'))     # dropped: queue full
    assert [r['body'] for r in rows] == ['first', 'second', 'third']
    assert any('queue full' in line for line in stub.logs)
    stub.block = False
    stub.release.set()
    assert _wait(lambda: len(stub.calls) == 2)
    time.sleep(0.05)
    assert len(rows) == 3, 'the delivery job wrote a second Inbox row'
