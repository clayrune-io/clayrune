"""Independent round-2 probes: synthetic I/O only, no model or credential use."""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
import requests
import urllib3.connection

from mc import agent_runtime as rt, api_credits_offload as offload
from mc.agent_mail_launder import MailLaunder
from mc.agent_mail_store import MailStore, MailStoreConfig


def extraction():
    return json.dumps(dict(reply_text='Friday works.',
        segments=[dict(kind='reply', text='Friday works.')],
        ambiguous_or_mixed=False, extraction_failed=False,
        truncated=False, instruction_like=False))


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    value = rt.ClaudeRuntime()
    monkeypatch.setattr(rt, 'claude_oneshot_available', lambda: True)
    monkeypatch.setattr(rt, 'get_runtime', lambda name: value)
    monkeypatch.setattr(rt, '_authorize_text_transform', lambda *a, **kw: None)
    monkeypatch.setattr(value, '_oneshot_argv', lambda **kw: ['synthetic-claude'])
    monkeypatch.setattr(rt._transform_sandbox, 'neutral_cwd', lambda: str(tmp_path))
    monkeypatch.setattr(rt._transform_sandbox, 'transform_env', lambda: dict(os.environ))
    monkeypatch.setattr(offload, 'try_offload', lambda **kw: None)
    monkeypatch.setattr(offload, 'note_subscription_call', lambda **kw: None)
    return value


def store_at(tmp_path, clock, lease=30):
    value = MailStore(tmp_path / 'mail.sqlite3', clock=clock,
                      config=MailStoreConfig(lease_seconds=lease))
    value.register('install')
    value.bind_thread('install', 'thread', 'project', 'session')
    value.insert_event(event_id='event', registration_id='install',
                       thread_token='thread', message_id='<id@example.test>', body='Friday works.')
    return value


@pytest.mark.parametrize('delta', [-100, -0.001, 0])
def test_deadline_past_at_entry_spends_nothing(runtime, monkeypatch, delta):
    monkeypatch.setattr(offload, 'try_offload', lambda **kw: pytest.fail('API started'))
    monkeypatch.setattr(rt.subprocess, 'run', lambda *a, **kw: pytest.fail('CLI started'))
    with pytest.raises(TimeoutError):
        runtime.oneshot(prompt='Extract', deadline=time.monotonic() + delta)


@pytest.mark.parametrize('elapsed,expected', [(24.999, 'ready'), (25, 'blocked'), (25.001, 'blocked')])
def test_api_success_deadline_boundary(runtime, monkeypatch, tmp_path, elapsed, expected):
    clock = SimpleNamespace(mono=1000., wall=1_000_000.)
    monkeypatch.setattr(time, 'monotonic', lambda: clock.mono)
    store = store_at(tmp_path, lambda: clock.wall)
    def api(**kw):
        clock.mono += elapsed
        clock.wall += elapsed
        return rt.OneshotResult(text=extraction())
    monkeypatch.setattr(offload, 'try_offload', api)
    monkeypatch.setattr(rt.subprocess, 'run', lambda *a, **kw: pytest.fail('CLI started'))
    result = MailLaunder(store, enabled=True).process_next()
    assert result['state'] == expected
    if expected == 'blocked':
        assert result['reason'] == 'extraction_timeout'
        assert MailLaunder(store, enabled=True).process_next()['state'] == 'idle'


@pytest.mark.parametrize('jump,expected', [(-3600, 'ready'), (0, 'ready'), (3600, 'retryable')])
def test_wall_clock_jump_does_not_change_monotonic_budget_or_accept_expired_lease(
        runtime, monkeypatch, tmp_path, jump, expected):
    clock = SimpleNamespace(mono=1000., wall=1_000_000.)
    monkeypatch.setattr(time, 'monotonic', lambda: clock.mono)
    store = store_at(tmp_path, lambda: clock.wall)
    calls = []
    def api(**kw):
        calls.append(('api', kw['timeout']))
        clock.mono += 20
        clock.wall += 20 + jump
        return None
    def cli(*a, **kw):
        calls.append(('cli', kw['timeout']))
        clock.mono += 2
        clock.wall += 2
        return SimpleNamespace(returncode=0, stdout=extraction(), stderr='')
    monkeypatch.setattr(offload, 'try_offload', api)
    monkeypatch.setattr(rt.subprocess, 'run', cli)
    monkeypatch.setattr(rt._transform_process_deadline, 'run', cli)
    result = MailLaunder(store, enabled=True).process_next()
    assert calls == [('api', 25), ('cli', 5)]
    assert result['state'] == expected
    if expected == 'retryable':
        assert result['reason'] == 'lease_expired_during_laundering'
        assert store.get_event('event', include_content=True)['laundered'] is None


def test_real_subprocess_is_killed_on_deadline(runtime, monkeypatch):
    """A harmless Python sleeper replaces the CLI; track/register its own PID."""
    original = subprocess.Popen
    spawned = []
    def popen(*args, **kwargs):
        proc = original(*args, **kwargs)
        spawned.append(proc)
        try:
            response = requests.post('http://localhost:5199/api/processes/register',
                json={'pid': proc.pid, 'name': 'Fenn synthetic deadline sleeper',
                      'project_id': 'mission_control', 'command': 'python -c sleep(8)'}, timeout=2)
            response.raise_for_status()
        except Exception:
            proc.kill()
            proc.wait()
            raise
        return proc
    monkeypatch.setattr(runtime, '_oneshot_argv', lambda **kw:
                        [sys.executable, '-c', 'import time; time.sleep(8)'])
    monkeypatch.setattr(subprocess, 'Popen', popen)
    start = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            runtime.oneshot(prompt='Synthetic', timeout=25, deadline=start + 3.1)
        elapsed = time.monotonic() - start
        print('REAL_CHILD', {'elapsed': elapsed, 'pid': spawned[0].pid,
                             'exit': spawned[0].poll()})
        assert elapsed < 5
        assert spawned[0].poll() is not None
    finally:
        for proc in spawned:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


@pytest.mark.parametrize('lease,chunks', [(4.2, 18), (30, 128)])
def test_http_read_timeout_is_not_a_total_deadline(runtime, monkeypatch, tmp_path, lease, chunks):
    """Real requests/urllib3/http.client reads, synthetic socket pair: no listener.

    Every read arrives within the socket timeout, but the total exceeds the
    claim lease. Do not replace requests with a fake ignoring timeout here.
    """
    # Fixture patched this attribute; restore the original function saved below.
    deadlines = []
    def observed_offload(**kw):
        deadlines.append(kw['deadline'])
        return ORIGINAL_OFFLOAD(**kw)
    monkeypatch.setattr(offload, 'try_offload', observed_offload)
    monkeypatch.setattr(offload.budget, 'load_settings',
        lambda: {'mode': 'on', 'secret_name': 'synthetic'})
    monkeypatch.setattr(offload.budget, 'can_spend', lambda *a: (True, ''))
    monkeypatch.setattr(offload, '_read_key', lambda name: ('synthetic', ''))
    monkeypatch.setattr(offload.budget, 'cost_usd', lambda *a: 0)
    monkeypatch.setattr(offload.budget, 'record', lambda **kw: None)
    monkeypatch.setattr(offload.budget, 'note_error', lambda *a: None)
    monkeypatch.setattr(offload.transport, 'API_URL', 'http://synthetic.invalid/messages')
    client, peer = socket.socketpair()
    def connect(connection):
        connection.sock = client
        client.settimeout(connection.timeout)
    monkeypatch.setattr(urllib3.connection.HTTPConnection, 'connect', connect)
    session = requests.Session()
    session.trust_env = False
    budgets = []
    session_post = session.post
    def post(*args, **kwargs):
        budgets.append(kwargs['timeout'])
        return session_post(*args, **kwargs)
    # The watchdog owns a Session now; instrument that session without
    # replacing requests/urllib3/http.client's real response reads.
    monkeypatch.setattr(session, 'post', post)
    monkeypatch.setattr(requests, 'Session', lambda: session)
    monkeypatch.setattr(rt.subprocess, 'run', lambda *a, **kw: pytest.fail('CLI started'))
    payload = json.dumps({'content': [{'type': 'text', 'text': extraction()}],
                          'stop_reason': 'end_turn'}).encode()
    writer_errors = []
    def feed_response():
        try:
            peer.recv(65536)
            peer.sendall(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n')
            # Each chunk arrives within the idle timeout; total exceeds lease.
            for _ in range(chunks):
                time.sleep(.25)
                peer.sendall(b'1\r\n \r\n')
            peer.sendall(('%x\r\n' % len(payload)).encode() + payload + b'\r\n0\r\n\r\n')
        except Exception as exc:
            writer_errors.append((exc, time.monotonic()))
        finally:
            peer.close()
    thread = threading.Thread(target=feed_response)
    thread.start()
    store = store_at(tmp_path, time.time, lease=lease)
    start = time.monotonic()
    try:
        result = MailLaunder(store, enabled=True).process_next()
        elapsed = time.monotonic() - start
    finally:
        session.close()
        client.close()
        thread.join(timeout=6)
    print('REAL_HTTP', {'elapsed': elapsed, 'lease': lease, 'budgets': budgets, 'result': result,
                         'attempts': store.get_event('event')['attempts'],
                         'writer_errors': writer_errors})
    # Dave's 2026-11-09 13:03 probe-contract decision: permit ONLY a peer
    # disconnect at/after the absolute extraction deadline. 50ms tolerance
    # covers clock/scheduling measurement, never a pre-budget fixture failure.
    assert len(deadlines) == 1
    assert all((isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError))
                or (isinstance(exc, OSError) and getattr(exc, 'winerror', None) in (10053, 10054)))
               and error_time >= deadlines[0] - .05
               for exc, error_time in writer_errors), writer_errors
    assert elapsed < lease
    assert not thread.is_alive()
    store.clock = lambda: time.time() + 3
    claim = store.claim_for_laundering()
    print('AUTO_RETRY', {'claimed': claim is not None,
                          'attempts': claim['attempts'] if claim else None})
    assert claim is None
    # The required behavior: total deadline -> durable timeout, no retry spend.
    assert result['state'] == 'blocked', result
    assert result['reason'] == 'extraction_timeout', result
    persisted = store.get_event('event')
    assert persisted['state'] == 'blocked'
    assert persisted['reason'] == 'extraction_timeout'
    assert persisted['attempts'] == 1


ORIGINAL_OFFLOAD = offload.try_offload
