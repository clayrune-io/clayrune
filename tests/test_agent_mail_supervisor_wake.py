"""MC-1186 / MB9: a reply taken into custody is delivered without waiting out the tick,
and a merely pending recovery step does not stretch the tick to 15 minutes."""
import threading
import time
from types import SimpleNamespace

import pytest

from mc import agent_mail_supervisor as module
from mc.agent_mail_pull_client import PullClient
from test_agent_mail_pull_client import Peer, World, connect, eventually, offered
from test_agent_mail_pull_receiver import INSTALL, MANIFEST

PENDING = {'ok': False, 'steps': [{'name': 'flows', 'state': 'pending', 'reason': 'flow_pending', 'count': 1}]}
FAILED = {'ok': False, 'steps': [{'name': 'inbound', 'state': 'failed', 'reason': 'step_failed', 'count': 0}]}
HELD = {'ok': False, 'steps': [{'name': 'store', 'state': 'held', 'reason': 'store_missing', 'count': 0}]}
CLEAN = {'ok': True, 'steps': [{'name': 'store', 'state': 'ok', 'reason': 'ok', 'count': 0}]}


class Stub:
    def __init__(self, report=CLEAN):
        self.report, self.calls, self.seen = report, 0, []

    def run(self):
        self.calls += 1
        self.seen.append(self.on_run() if getattr(self, 'on_run', None) else None)
        return self.report


def _supervisor(stub, **kw):
    return module.Supervisor(stub, config={module.FLAG: True}, interval_s=60, max_backoff_s=900, **kw)


def test_pending_steps_do_not_back_off_the_tick():
    s = _supervisor(Stub(PENDING))
    delays = []
    for _ in range(5):
        s.tick()
        delays.append(s.delay())
    assert delays == [60] * 5 and s.failures == 0


@pytest.mark.parametrize('report', [FAILED, HELD])
def test_failed_and_held_steps_still_back_off(report):
    s = _supervisor(Stub(report))
    delays = []
    for _ in range(3):
        s.tick()
        delays.append(s.delay())
    assert delays == [120, 240, 480]


def test_pending_does_not_mask_a_failed_step_in_the_same_pass():
    both = {'ok': False, 'steps': PENDING['steps'] + FAILED['steps']}
    s = _supervisor(Stub(both))
    s.tick()
    assert s.delay() == 120


def test_custody_signal_ends_the_wait_early():
    stub = Stub()
    s = _supervisor(stub)
    try:
        assert s.start()
        eventually(lambda: stub.calls == 1)
        time.sleep(.2)
        assert stub.calls == 1            # the 60 s tick is nowhere near due
        s.custody_event.set()
        eventually(lambda: stub.calls == 2, timeout=3)
    finally:
        s.stop(2)


def test_signal_during_a_pass_is_not_lost():
    stub = Stub()
    s = _supervisor(stub)
    stub.on_run = lambda: s.custody_event.set() if stub.calls == 1 else None
    try:
        assert s.start()
        eventually(lambda: stub.calls == 2, timeout=3)
    finally:
        s.stop(2)


def test_stop_still_returns_promptly_with_the_event_wait():
    s = _supervisor(Stub())
    assert s.start()
    time.sleep(.1)
    began = time.monotonic()
    s.stop(2)
    assert time.monotonic() - began < 1.5 and not s._thread


def test_pull_client_sets_the_event_only_after_custody_commits(w):
    peer = Peer()
    client = PullClient(w.store, w.seal, w.http, install_id=INSTALL, reviewed_service=lambda: (w.pin, MANIFEST),
                        socket_factory=peer.factory)
    event = client.custody_event = threading.Event()
    seen = []
    original = client._send

    def send(kind, *args, **kwargs):
        if kind == 'custody_ack':
            seen.append((len(w.rows('events')), event.is_set()))
        return original(kind, *args, **kwargs)
    client._send = send
    try:
        client.start()
        eventually(lambda: peer.connections == 1)
        connect(w, peer)
        assert not event.is_set()           # a hello commits no mail
        peer.send(offered(w))
        eventually(event.is_set)
        eventually(lambda: bool(seen))      # the ack is sent just after the signal, on the client thread
        assert len(w.rows('events')) == 1
        assert seen == [(1, True)]          # committed before the signal and before the ack
    finally:
        assert client.stop()
        peer.close()


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def test_reply_taken_into_custody_reaches_recovery_without_waiting_out_the_tick(w):
    """Real PullClient under a real Supervisor with a 60 s interval: the second
    recovery pass (the one that would launder and wake) runs within seconds of the
    commit, and already sees the stored reply."""
    peer = Peer()
    client = PullClient(w.store, w.seal, w.http, install_id=INSTALL, reviewed_service=lambda: (w.pin, MANIFEST),
                        socket_factory=peer.factory)
    stub = Stub()
    stub.on_run = lambda: len(w.rows('events'))
    factory = SimpleNamespace(pull_client=lambda: client, health=lambda: {'state': 'ok', 'reason': ''},
                              open_store=lambda: w.store)
    s = module.Supervisor(stub, factory=factory, config={module.FLAG: True}, interval_s=60,
                          clock=lambda: w.now / 1000)
    try:
        assert s.start()
        eventually(lambda: peer.connections == 1 and stub.calls >= 1)
        connect(w, peer)
        first = stub.calls
        peer.send(offered(w))
        eventually(lambda: stub.calls > first and stub.seen[-1] == 1, timeout=4)
    finally:
        s.stop(3)
        client.stop()
        peer.close()
