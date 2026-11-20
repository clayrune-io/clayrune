"""MC-1179 MB7: one pipe write produces one receipt call.

`agent_mail_pipe.write` used to call `scope.accept()` from the worker thread
and again from the calling thread whenever the write finished inside the
acknowledgment window. It was harmless only because every step behind
`accept` happened to be idempotent. No real process, store or mail here.
"""
import threading
import time

from mc import agent_mail_continuation as continuation, agent_mail_pipe


class _Scope:
    def __init__(self):
        self.calls = []
        self.waited = False
        self._lock = threading.Lock()

    def begin(self):
        pass

    def accept(self):
        with self._lock:
            self.calls.append('accept')

    def unknown(self):
        with self._lock:
            self.calls.append('unknown')


class _Stdin:
    def __init__(self, *, block=None, fail=False):
        self.block, self.fail = block, fail
        self.written = []

    def write(self, data):
        if self.block is not None:
            self.block.wait(5)
        if self.fail:
            raise BrokenPipeError('synthetic')
        self.written.append(data)

    def flush(self):
        pass

    def close(self):
        pass


class _Proc:
    def __init__(self, stdin):
        self.stdin = stdin


def _settle():
    """Wait for the writer thread this call started to finish."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not any(t.name == 'mail-followup-stdin' for t in threading.enumerate()):
            return
        time.sleep(.005)
    raise AssertionError('mail writer thread did not finish')


def _write(stdin):
    scope = _Scope()
    with continuation.activate(scope):
        result = agent_mail_pipe.write(_Proc(stdin), 'payload', {}, None, False)
    return scope, result


def test_write_inside_the_window_accepts_exactly_once():
    scope, result = _write(_Stdin())
    assert result == {'ack': 'written'}
    # The caller's accept is synchronous: MailWake reads the receipt on return.
    assert scope.calls == ['accept']
    _settle()
    assert scope.calls == ['accept']


def test_failed_write_inside_the_window_is_unknown_exactly_once():
    scope, result = _write(_Stdin(fail=True))
    assert result == {'ack': 'unknown'}
    _settle()
    assert scope.calls == ['unknown']


def test_late_write_is_unknown_then_one_late_accept(monkeypatch):
    monkeypatch.setattr(agent_mail_pipe, 'ACK_SECONDS', .02)
    gate = threading.Event()
    scope, result = _write(_Stdin(block=gate))
    assert result == {'ack': 'unknown'}
    assert scope.calls == ['unknown']
    gate.set()
    _settle()
    # The durable late receipt still arrives, once, after the caller's unknown.
    assert scope.calls == ['unknown', 'accept']


def test_late_failed_write_stays_one_unknown(monkeypatch):
    monkeypatch.setattr(agent_mail_pipe, 'ACK_SECONDS', .02)
    gate = threading.Event()
    scope, _ = _write(_Stdin(block=gate, fail=True))
    gate.set()
    _settle()
    assert scope.calls == ['unknown']
