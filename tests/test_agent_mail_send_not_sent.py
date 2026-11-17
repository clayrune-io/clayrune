"""MC-1179 pass 05b / MB13: what a SEND may and may not treat as "not sent".

Two defects behind "a question gets one attempt at being mailed":

* A connection that never opened (DNS, refused, network unreachable) was
  recorded as `uncertain / transport_unavailable`, a state no caller ever sends
  again. Nothing had left the machine, so it is `retryable`. A failure with any
  chance that request bytes were written (timeout, reset, TLS) stays
  `uncertain` and is never re-sent.
* The send path applied the inbound WAKE preconditions (`rollover_required`,
  `provider_quota_held`). They guard resuming a conversation, not mailing a
  note or a question out of it.

No real mail, network or push: every transport here is a recording fake.
"""
import socket
import urllib.error

import pytest

from test_agent_mail_outbound import out, kit, setup, env, prepare  # noqa: F401
from mc.agent_mail_service_client import HttpsTransport, ServiceRefused
from mc.agent_mail_target import MailHeld


def _raiser(exc):
    def hook(*_args):
        raise exc
    return hook


# ── transport: only a failure BEFORE any byte is written is "not sent" ────────

class _Opener:
    def __init__(self, exc):
        self.exc = exc

    def open(self, request, timeout=None):
        raise self.exc


def _post(exc):
    t = HttpsTransport(origin='https://mail.example.test', opener=_Opener(exc))
    with pytest.raises(ServiceRefused, match='^connection_lost$') as info:
        t.post('https://mail.example.test', '/v1/pull/send', {}, b'{}')
    return info.value


@pytest.mark.parametrize('reason', [
    socket.gaierror(11001, 'getaddrinfo failed'),
    ConnectionRefusedError(10061, 'refused'),
    OSError(101, 'Network is unreachable'),
])
def test_connect_failures_are_marked_not_sent(reason):
    from mc.agent_mail_service_client import ServiceNotSent
    assert isinstance(_post(urllib.error.URLError(reason)), ServiceNotSent)


@pytest.mark.parametrize('exc', [
    urllib.error.URLError(socket.timeout('timed out')),   # may be a send/receive timeout
    urllib.error.URLError(ConnectionResetError(104, 'reset')),
    ConnectionResetError(104, 'reset by peer'),           # raised from getresponse(): request was sent
    TimeoutError('read timed out'),
    RuntimeError('anything else'),
])
def test_anything_that_may_have_written_stays_plain_connection_lost(exc):
    from mc.agent_mail_service_client import ServiceNotSent
    assert not isinstance(_post(exc), ServiceNotSent)


# ── Outbound.send: state written for each ─────────────────────────────────────

def test_not_sent_connection_failure_is_retryable_and_a_later_send_goes_out(out):
    from mc.agent_mail_service_client import ServiceNotSent
    first = prepare(out)
    out.recording.hook = _raiser(ServiceNotSent('connection_lost'))
    sent = out.outbound.send(first['intent_id'])
    assert sent['state'] == 'retryable' and sent['reason'] == 'transport_unavailable'
    # Same intent, now reachable: it goes out (retryable is a state with a consumer).
    out.recording.hook = lambda *a: None
    assert out.outbound.send(first['intent_id'])['state'] == 'accepted'


def test_possibly_written_connection_failure_is_still_uncertain_and_never_resent(out):
    first = prepare(out)
    out.recording.hook = _raiser(ServiceRefused('connection_lost'))
    assert out.outbound.send(first['intent_id'])['state'] == 'uncertain'
    out.recording.hook = lambda *a: None
    calls = len(out.recording.calls)
    assert out.outbound.send(first['intent_id'])['state'] == 'uncertain'
    assert len(out.recording.calls) == calls


# ── send does not apply the wake preconditions ────────────────────────────────

def test_quota_and_rollover_do_not_block_a_send(out, monkeypatch):
    from mc.blueprints import agent_routes as ar
    first = prepare(out)
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **k: {'error': 'exhausted'})
    monkeypatch.setattr(ar, '_auto_fresh_trigger', lambda *a, **k: ('too_big', 1))
    assert out.outbound.send(first['intent_id'])['state'] == 'accepted'


def test_the_wake_path_still_applies_them(out, monkeypatch):
    """prepare() binds the thread through MailWake, which guards a WAKE; unchanged."""
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **k: {'error': 'exhausted'})
    with pytest.raises(MailHeld, match='provider_quota_held'):
        prepare(out, request_id='note-wake')
