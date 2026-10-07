"""MC-1066 -- engine network failures are classified and retried by how far the
request got (`mc/desk_net_errors.py`, wired into `desk_engines._transport_call`
and `_mcp_post`). NO NETWORK: `_http_request` / `_OPENER` are scripted fakes.

Pinned:
  * a request that never left this machine (DNS miss, refused, unreachable) is
    NOT_SENT; a read timeout, reset mid-stream or anything else is MAYBE_SENT;
  * NOT_SENT is retried twice (0.5 s then 1.5 s) for EVERY call, paid submits
    included; MAYBE_SENT is retried only when the caller said the call is free,
    and never for a submit or upload;
  * when retries run out the message is plain, says nothing was sent or charged
    (NOT_SENT) or that the result is unknown (paid MAYBE_SENT), and carries no
    raw exception text -- that goes to the log only;
  * a paid submit that was never sent gives its reservation back; one that may
    have been sent keeps it (unchanged).
"""
import errno
import http.client
import io
import json
import socket
import ssl
import urllib.error
from types import SimpleNamespace

import pytest

from mc import desk_engines as eng
from mc import desk_net_errors as net
from tests.test_desk_engines import (  # noqa: F401  (fixtures + helpers)
    _campaign, _higgs_video, _own, _submit, _veo, client, uploads, vault, vendor)

DNS = socket.gaierror(11002, 'getaddrinfo failed')


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    rec: list[float] = []
    monkeypatch.setattr(net.time, 'sleep', rec.append)
    return rec


@pytest.fixture
def logs(monkeypatch):
    rec: list[str] = []
    monkeypatch.setattr(eng, '_log', lambda msg, **kw: rec.append(msg))
    return rec


# -- classification -----------------------------------------------------------

@pytest.mark.parametrize('exc', [
    DNS,
    urllib.error.URLError(DNS),
    ConnectionRefusedError(10061, 'refused'),
    urllib.error.URLError(ConnectionRefusedError(10061, 'refused')),
    OSError(errno.ENETUNREACH, 'Network is unreachable'),
    urllib.error.URLError(OSError(errno.EHOSTUNREACH, 'No route to host')),
    OSError(10051, 'unreachable'),
])
def test_request_that_never_left_is_not_sent(exc):
    assert net.classify(exc) == net.NOT_SENT


@pytest.mark.parametrize('exc', [
    TimeoutError('timed out'),
    socket.timeout('timed out'),
    urllib.error.URLError(TimeoutError('timed out')),
    ConnectionResetError(10054, 'reset'),
    http.client.RemoteDisconnected('closed without response'),
    urllib.error.URLError('unknown url type'),
    urllib.error.URLError(ssl.SSLError('handshake')),
    OSError(errno.EPIPE, 'broken pipe'),
    ValueError('response too large'),
    net.AfterSend(DNS),          # read-stage failure: the request had been sent
])
def test_everything_else_may_have_been_sent(exc):
    assert net.classify(exc) == net.MAYBE_SENT


# -- the retry loop -----------------------------------------------------------

def _flaky(*failures, result='ok'):
    calls = []

    def do():
        calls.append(1)
        if len(calls) <= len(failures):
            raise failures[len(calls) - 1]
        return result
    do.calls = calls
    return do


@pytest.mark.parametrize('free', [True, False])
def test_not_sent_is_retried_twice_with_backoff_for_free_and_paid(free, sleeps):
    do = _flaky(DNS, DNS)
    assert net.run(do, free=free) == 'ok'
    assert len(do.calls) == 3 and sleeps == [0.5, 1.5]


@pytest.mark.parametrize('free', [True, False])
def test_not_sent_that_never_clears_stops_after_three_tries(free, sleeps):
    do = _flaky(DNS, DNS, DNS, DNS)
    with pytest.raises(net.NetFailure) as e:
        net.run(do, free=free)
    assert e.value.kind == net.NOT_SENT and e.value.attempts == 3 and len(do.calls) == 3
    assert sleeps == [0.5, 1.5]


def test_maybe_sent_is_retried_for_a_free_call(sleeps):
    do = _flaky(TimeoutError('t'), ConnectionResetError('r'))
    assert net.run(do, free=True) == 'ok'
    assert len(do.calls) == 3 and sleeps == [0.5, 1.5]


def test_maybe_sent_is_never_retried_for_a_paid_call(sleeps):
    do = _flaky(TimeoutError('t'))
    with pytest.raises(net.NetFailure) as e:
        net.run(do, free=False)
    assert e.value.kind == net.MAYBE_SENT and len(do.calls) == 1 and sleeps == []


def test_a_paid_call_that_fails_unsent_then_sent_is_not_retried_again(sleeps):
    do = _flaky(DNS, TimeoutError('t'), DNS)
    with pytest.raises(net.NetFailure) as e:
        net.run(do, free=False)
    assert e.value.kind == net.MAYBE_SENT and len(do.calls) == 2


def test_oversize_response_is_not_retried_even_when_free(sleeps):
    do = _flaky(ValueError('too big'))
    with pytest.raises(net.NetFailure):
        net.run(do, free=True)
    assert len(do.calls) == 1


def test_retries_stop_at_the_callers_deadline(monkeypatch, sleeps):
    clock = [100.0]
    monkeypatch.setattr(net.time, 'monotonic', lambda: clock[0])
    do = _flaky(DNS, DNS, DNS)
    with pytest.raises(net.NetFailure) as e:
        net.run(do, free=True, deadline=100.4)     # 0.5 s backoff would pass it
    assert e.value.attempts == 1 and sleeps == []


def test_non_transport_errors_propagate_untouched():
    with pytest.raises(KeyError):
        net.run(_flaky(KeyError('bug')), free=True)


# -- messages -----------------------------------------------------------------

def _fail(kind, exc):
    return net.NetFailure(kind, exc, 3)


def test_messages_are_plain_and_free_of_raw_text():
    not_sent = net.message(_fail(net.NOT_SENT, urllib.error.URLError(DNS)), 'Higgsfield', free=False)
    assert not_sent == 'Could not reach Higgsfield (network lookup failed). Nothing was sent or charged.'
    refused = net.message(_fail(net.NOT_SENT, ConnectionRefusedError(10061, 'x')), 'Google', free=True)
    assert refused == 'Could not reach Google (connection refused). Nothing was sent or charged.'
    paid = net.message(_fail(net.MAYBE_SENT, TimeoutError('slow')), 'Higgsfield', free=False)
    assert 'unknown' in paid and 'timed out' in paid and 'Nothing was sent' not in paid
    free = net.message(_fail(net.MAYBE_SENT, ConnectionResetError('r')), 'Higgsfield', free=True)
    assert 'connection dropped' in free and 'cost nothing' in free
    for m in (not_sent, refused, paid, free):
        assert 'getaddrinfo' not in m and '11002' not in m and 'Errno' not in m


def test_service_name_from_url():
    assert net.service_name('https://platform.higgsfield.ai/x') == 'Higgsfield'
    assert net.service_name('https://generativelanguage.googleapis.com/v1beta') == 'Google'
    assert net.service_name('https://api.openai.com/v1') == 'OpenAI'
    assert net.service_name('https://cdn.example.test/f.mp4') == 'the engine'


# -- _transport_call ----------------------------------------------------------

def _count(monkeypatch, *failures):
    calls = []

    def fake(method, url, **kw):
        calls.append(1)
        if len(calls) <= len(failures):
            raise failures[len(calls) - 1]
        return 200, {}, b'{}'
    monkeypatch.setattr(eng, '_http_request', fake)
    return calls


def test_transport_call_dns_miss_exhausted_is_plain_and_raw_text_goes_to_the_log(monkeypatch, logs):
    calls = _count(monkeypatch, *[urllib.error.URLError(DNS)] * 5)
    with pytest.raises(eng.EngineError) as e:
        eng._transport_call('POST', 'https://platform.higgsfield.ai/estimate/m', free=True)
    err = e.value
    assert len(calls) == 3
    assert str(err) == 'Could not reach Higgsfield (network lookup failed). Nothing was sent or charged.'
    assert err.definitive is False and err.not_sent is True and err.plain is True
    assert any('getaddrinfo failed' in line for line in logs) and len(logs) == 3


def test_transport_call_recovers_when_the_lookup_clears(monkeypatch):
    calls = _count(monkeypatch, urllib.error.URLError(DNS))
    assert eng._transport_call('POST', 'https://platform.higgsfield.ai/x')[0] == 200
    assert len(calls) == 2


def test_transport_call_timeout_on_a_paid_call_is_one_try_and_reports_unknown(monkeypatch, logs):
    calls = _count(monkeypatch, TimeoutError('read timed out'), TimeoutError('again'))
    with pytest.raises(eng.EngineError) as e:
        eng._transport_call('POST', 'https://generativelanguage.googleapis.com/v1beta/m:predictLongRunning')
    assert len(calls) == 1
    assert e.value.definitive is False and e.value.not_sent is False and e.value.plain is False
    assert 'unknown' in str(e.value) and 'Google' in str(e.value)


def test_transport_call_timeout_on_a_free_call_is_retried(monkeypatch):
    calls = _count(monkeypatch, TimeoutError('t'), ConnectionResetError('r'))
    assert eng._transport_call('GET', 'https://platform.higgsfield.ai/requests/r1/status', free=True)[0] == 200
    assert len(calls) == 3


def test_free_flag_is_not_inferred_from_the_payload(monkeypatch):
    """A body that says get_cost / estimate does not make a call free."""
    calls = _count(monkeypatch, TimeoutError('t'), TimeoutError('t'))
    with pytest.raises(eng.EngineError):
        eng._transport_call('POST', 'https://platform.higgsfield.ai/estimate/m',
                            body=json.dumps({'params': {'get_cost': True}}).encode())
    assert len(calls) == 1


# -- end to end through the Desk routes ----------------------------------------

def test_estimate_dns_miss_shows_the_plain_message(client, vendor):
    _campaign(_own(10))
    vendor.on('POST', '/estimate/', urllib.error.URLError(DNS))
    r = client.post('/api/desk/engines/estimate', json=_higgs_video())
    assert r.status_code == 502 and r.get_json()['code'] == 'estimate_failed'
    assert r.get_json()['error'] == ('Could not reach Higgsfield (network lookup failed). '
                                     'Nothing was sent or charged. Try Price again.')
    assert len(vendor.to('/estimate/')) == 3


def test_estimate_timeout_is_retried_because_a_price_check_is_free(client, vendor):
    _campaign(_own(10))
    vendor.on('POST', '/estimate/', TimeoutError('t'), TimeoutError('t'),
              vendor.json(200, {'usd': '0.21'}))
    r = client.post('/api/desk/engines/estimate', json=_higgs_video())
    assert r.status_code == 200 and len(vendor.to('/estimate/')) == 3


def test_paid_submit_dns_miss_is_retried_then_gives_the_reservation_back(client, vendor):
    _campaign(_own(5))
    vendor.on('POST', ':predictLongRunning', urllib.error.URLError(DNS))
    r = _submit(client, _veo(desk={'idempotency_key': 'dns'}))
    assert r.status_code == 502 and r.get_json()['code'] == 'engine_refused'
    assert r.get_json()['error'] == 'Could not reach Google (network lookup failed). Nothing was sent or charged.'
    assert len(vendor.to(':predictLongRunning')) == 3
    assert client.post('/api/desk/engines/estimate', json=_veo()).get_json()['budget']['spent'] == 0


def test_paid_submit_timeout_is_sent_once_and_keeps_the_reservation(client, vendor):
    _campaign(_own(5))
    vendor.on('POST', ':predictLongRunning', TimeoutError('read timed out'))
    j = _submit(client, _veo(desk={'idempotency_key': 'to'})).get_json()['job']
    assert len(vendor.to(':predictLongRunning')) == 1
    assert j['status'] == 'failed' and 'unknown' in j['failure']['message']
    assert 'may have accepted' in j['failure']['message'] and j['cost_usd'] == pytest.approx(3.2)


def test_job_status_poll_is_free_so_a_drop_is_retried(client, vendor):
    _campaign(_own(10))
    vendor.on('POST', '/estimate/', vendor.json(200, {'usd': '0.21'}))
    vendor.on('POST', '/kling-video/v2.5-turbo/pro/text-to-video',
              vendor.json(200, {'status': 'queued', 'request_id': 'r9'}))
    jid = _submit(client, _higgs_video()).get_json()['job']['job_id']
    vendor.on('GET', '/requests/r9/status', ConnectionResetError('r'), vendor.json(200, {'status': 'in_progress'}))
    assert client.get(f'/api/desk/engines/jobs/{jid}').get_json()['job']['status'] == 'rendering'
    assert len(vendor.to('/requests/r9/status')) == 2


# -- _mcp_post ------------------------------------------------------------------

class _Resp(io.BytesIO):
    status = 200
    headers = {'content-type': 'application/json'}


def _opener(monkeypatch, *outcomes):
    calls = []

    def open_(req, timeout=None):
        calls.append(1)
        o = outcomes[min(len(calls), len(outcomes)) - 1]
        if isinstance(o, BaseException):
            raise o
        return o() if callable(o) else o
    monkeypatch.setattr(eng, '_OPENER', SimpleNamespace(open=open_))
    return calls


def _ok():
    return _Resp(b'{"jsonrpc":"2.0","id":2,"result":{"tools":[]}}')


BODY = {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}


@pytest.mark.parametrize('free', [True, False])
def test_mcp_dns_miss_is_retried_for_free_and_paid_calls(monkeypatch, free):
    calls = _opener(monkeypatch, urllib.error.URLError(DNS), urllib.error.URLError(DNS), _ok)
    assert eng._mcp_post('t', BODY, expect_id=2, free=free) == {'tools': []}
    assert len(calls) == 3


def test_mcp_dns_miss_exhausted_message(monkeypatch, logs):
    calls = _opener(monkeypatch, urllib.error.URLError(DNS))
    with pytest.raises(eng.EngineError) as e:
        eng._mcp_post('t', BODY, expect_id=2)
    assert len(calls) == 3 and e.value.not_sent is True and e.value.definitive is False
    assert str(e.value) == 'Could not reach Higgsfield (network lookup failed). Nothing was sent or charged.'
    assert any('getaddrinfo failed' in line for line in logs)


def test_mcp_open_timeout_paid_is_one_try_free_is_three(monkeypatch):
    calls = _opener(monkeypatch, TimeoutError('t'))
    with pytest.raises(eng.EngineError) as e:
        eng._mcp_post('t', BODY, expect_id=2)                   # default free=False
    assert len(calls) == 1 and 'unknown' in str(e.value) and e.value.not_sent is False
    calls = _opener(monkeypatch, TimeoutError('t'))
    with pytest.raises(eng.EngineError):
        eng._mcp_post('t', BODY, expect_id=2, free=True)
    assert len(calls) == 3


def test_mcp_reset_while_reading_the_reply_counts_as_sent(monkeypatch):
    class Dropping(_Resp):
        def read1(self, n):
            raise ConnectionResetError(10054, 'reset')

    calls = _opener(monkeypatch, Dropping)
    with pytest.raises(eng.EngineError) as e:
        eng._mcp_post('t', BODY, expect_id=2)
    assert len(calls) == 1 and e.value.not_sent is False and 'unknown' in str(e.value)
    calls = _opener(monkeypatch, Dropping, _ok)
    assert eng._mcp_post('t', BODY, expect_id=2, free=True) == {'tools': []}
    assert len(calls) == 2


def test_mcp_retries_stay_inside_the_callers_timeout(monkeypatch, sleeps):
    clock = [0.0]
    monkeypatch.setattr(eng.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(net.time, 'monotonic', lambda: clock[0])
    calls = _opener(monkeypatch, urllib.error.URLError(DNS))
    with pytest.raises(eng.EngineError):
        eng._mcp_post('t', BODY, expect_id=2, timeout=0.3, free=True)
    assert len(calls) == 1 and sleeps == []


# -- the Higgsfield MCP adapter says which of its calls are free ------------------

def test_adapter_marks_handshake_quote_and_poll_free_but_not_the_submit(monkeypatch):
    from mc.desk_higgsfield_mcp import HiggsfieldMcpAdapter
    seen = []

    def post(token, body, *, expect_id, timeout=None, free=False):
        seen.append((body.get('method'), (body.get('params') or {}).get('name'), free))
        return {'structuredContent': {}} if expect_id == 2 else {}
    monkeypatch.setattr(eng, '_mcp_post', post)
    a, creds = HiggsfieldMcpAdapter(), SimpleNamespace(secret='t')
    a._call(creds, 'generate_video', {'params': {}})
    a._call(creds, 'job_status', {'jobId': 'j'}, free=True)
    assert seen == [('initialize', None, True), ('notifications/initialized', None, True),
                    ('tools/call', 'generate_video', False),
                    ('initialize', None, True), ('notifications/initialized', None, True),
                    ('tools/call', 'job_status', True)]
