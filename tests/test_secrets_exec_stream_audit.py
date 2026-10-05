"""MC-1047 after Wren's audit (docs/_journal/mc-1047-security-audit-wren.md): each test pins
one finding. Same fixture and helpers as ``test_secrets_exec_stream.py`` (a real werkzeug
server, a real blueprint, the vault passphrase-locked on disk and unlocked only in the server
half)."""
from __future__ import annotations

import http.client
import json
import os
import socket
import sys
import threading
import time

import pytest

from tests.test_secrets_exec_stream import (  # noqa: F401  (srv is a fixture)
    SECRET, SLEEP_CHILD, _body, _cli, _launch, _lines, _only_session, _post_json, _wait, srv)


def _stdin_post(srv, session_id, body=b'', *, headers=None, query=''):
    conn = http.client.HTTPConnection('127.0.0.1', srv.port, timeout=15)
    h = {'X-Clayrune-Exec-Token': srv.token}
    h.update(headers or {})
    conn.request('POST', f'/api/secrets/exec-stream/{session_id}/stdin{query}', body=body,
                 headers=h)
    return conn.getresponse()


# ── P1-1: teardown must kill the child even when a stdin write is blocked ───

def test_teardown_kills_the_child_while_a_stdin_write_is_blocked_on_a_full_pipe(srv):
    session = srv.stream.start(_launch(srv, {'command': [sys.executable, '-c', SLEEP_CHILD]}))
    session.attach()
    outcome: list = []

    def writer():                      # the child never reads stdin: this fills the pipe and blocks
        try:
            session.write_stdin(b'x' * (4 << 20))
            outcome.append('wrote')
        except srv.stream.StdinClosed:
            outcome.append('closed')

    w = threading.Thread(target=writer, daemon=True)
    w.start()
    time.sleep(0.8)
    assert w.is_alive(), 'the write was expected to be blocked on a full pipe'
    t = threading.Thread(target=lambda: session.teardown('client_end'), daemon=True)
    t.start()
    t.join(5)
    try:
        assert not t.is_alive(), 'teardown is stuck behind the blocked stdin write'
        assert session.proc.poll() is not None, 'the child is still alive'
        assert session.id not in srv.stream._sessions
        w.join(5)
        assert not w.is_alive() and outcome == ['closed']     # the writer was released too
    finally:
        if session.proc.poll() is None:
            session.proc.kill()


def test_shutdown_all_does_not_hang_on_a_session_with_a_blocked_stdin_write(srv):
    session = srv.stream.start(_launch(srv, {'command': [sys.executable, '-c', SLEEP_CHILD]}))
    session.attach()
    w = threading.Thread(target=lambda: _swallow(session), daemon=True)
    w.start()
    time.sleep(0.8)
    t = threading.Thread(target=srv.stream.shutdown_all, daemon=True)
    t.start()
    t.join(5)
    try:
        assert not t.is_alive(), 'shutdown_all is stuck behind a blocked stdin write'
        assert session.proc.poll() is not None
    finally:
        if session.proc.poll() is None:
            session.proc.kill()


def _swallow(session):
    try:
        session.write_stdin(b'x' * (4 << 20))
    except Exception:
        pass


# ── P1-2: the resolved argv is never logged or audited ──────────────────────

def test_a_placeholder_as_argv0_leaves_no_value_in_the_audit_file_or_the_logs(srv, monkeypatch,
                                                                                 capfd):
    srv.vault.set_secret('interp.path', sys.executable)      # the "secret" IS the program
    logged: list[str] = []
    real_log = srv.stream._log
    monkeypatch.setattr(srv.stream, '_log', lambda m, *a, **k: (logged.append(m), real_log(m)))
    conn, resp = _post_json(srv, {'command': ['{{secret:interp.path}}', '-c', SLEEP_CHILD]})
    assert resp.status == 200
    session = _only_session(srv)
    from mc.state import tracked_processes
    resp.close()
    conn.close()
    _wait(lambda: session.proc.poll() is not None, what='the child to die')
    _wait(lambda: not srv.stream._sessions, what='the session to end')
    audit = srv.vault.audit_path().read_text(encoding='utf-8')
    events = [json.loads(line) for line in audit.splitlines() if line.strip()]
    start = next(e for e in events if e.get('event') == 'exec_stream_start')
    assert start['program'] == '{{secret:interp.path}}'      # the placeholder as written
    seen = capfd.readouterr()
    everything = audit + '\n'.join(logged) + seen.out + seen.err
    for needle in (sys.executable, os.path.basename(sys.executable)):
        assert needle not in everything
    assert any('{{secret:interp.path}}' in m for m in logged)  # the log line still names it


def test_a_start_failure_does_not_echo_the_resolved_program(srv):
    gone = str(srv.tmp / 'no-such-binary-for-this-test')
    srv.vault.set_secret('ghost.path', gone)
    conn, resp = _post_json(srv, {'command': ['{{secret:ghost.path}}', 'x']})
    text = resp.read().decode('utf-8')
    assert resp.status == 400
    assert gone not in text and 'no-such-binary-for-this-test' not in text
    assert not srv.stream._sessions


# ── found while testing: werkzeug skips close() when the client resets ──────

def test_a_client_that_resets_the_connection_at_once_still_ends_the_child(srv):
    conn, resp = _post_json(srv, {'command': [sys.executable, '-c', SLEEP_CHILD]})
    session = _only_session(srv)
    resp.close()                       # no pause: the reset reaches the server before the first
    conn.close()                       # frame is pulled, which makes werkzeug skip close()
    _wait(lambda: session.proc.poll() is not None, what='the child to die')
    _wait(lambda: not srv.stream._sessions, what='the session to be forgotten')


# ── P2-1: the child's base environment is the CLIENT's, not the server's ────

def test_the_childs_environment_is_the_clients_and_not_the_servers(srv, monkeypatch):
    monkeypatch.setenv('SERVER_ONLY_API_KEY', 'server-provider-key-123')   # hydrated into the server
    probe = ("import os; print(os.environ.get('SERVER_ONLY_API_KEY', 'ABSENT'), "
             "os.environ.get('CLIENT_ONLY_VAR'))")
    cli = _cli(srv, '--raw', '--', sys.executable, '-c', probe,
               env_extra={'CLIENT_ONLY_VAR': 'from-the-caller'})
    assert cli.communicate(timeout=30)[0].decode().strip() == 'ABSENT from-the-caller'


def test_a_request_without_an_environment_is_refused_rather_than_inheriting_the_servers(srv):
    conn = http.client.HTTPConnection('127.0.0.1', srv.port, timeout=15)
    conn.request('POST', '/api/secrets/exec-stream',
                 body=json.dumps({'command': [sys.executable, '-c', 'print(1)']}),
                 headers={'Content-Type': 'application/json',
                          'X-Clayrune-Exec-Token': srv.token})
    resp = conn.getresponse()
    assert resp.status == 400 and 'environ' in json.loads(resp.read())['error']
    assert not srv.stream._sessions
    for bad in ('x', ['a'], {'A': 1}, {'': 'v'}, {'A=B': 'v'}, {'A': 'v\0'}):
        c, r = _post_json(srv, {'command': ['x'], 'environ': bad})
        # _post_json's _body() lets an explicit environ win over the default
        assert r.status == 400, bad


# ── P2-2: a session has a maximum age, and a stalled reader does not pin one ─

def test_a_session_is_ended_at_the_configured_maximum_age(srv, monkeypatch):
    monkeypatch.setattr(srv.stream, '_max_age_s', lambda: 0.6)
    conn, resp = _post_json(srv, {'command': [sys.executable, '-c', SLEEP_CHILD]})
    session = _only_session(srv)
    _wait(lambda: session.proc.poll() is not None, what='the child to be ended at max age')
    _wait(lambda: not srv.stream._sessions, what='the session to be forgotten')
    audit = [json.loads(line) for line in
             srv.vault.audit_path().read_text(encoding='utf-8').splitlines() if line.strip()]
    assert any(e.get('event') == 'exec_stream_end' and e.get('reason') == 'max_age'
               for e in audit)
    resp.close()
    conn.close()


def test_max_age_is_read_from_config_and_defaults_to_24_hours(srv, monkeypatch):
    from mc import state
    monkeypatch.delitem(state.CONFIG, 'exec_stream_max_age_hours', raising=False)
    assert srv.stream._max_age_s() == 24 * 3600
    monkeypatch.setitem(state.CONFIG, 'exec_stream_max_age_hours', 2)
    assert srv.stream._max_age_s() == 7200
    monkeypatch.setitem(state.CONFIG, 'exec_stream_max_age_hours', 0)
    assert srv.stream._max_age_s() == 0                       # 0 disables, like every other knob
    monkeypatch.setitem(state.CONFIG, 'exec_stream_max_age_hours', 'soon')
    assert srv.stream._max_age_s() == 24 * 3600               # junk falls back to the default


def test_a_reader_that_stops_reading_while_the_child_writes_is_cut_off(srv, monkeypatch):
    monkeypatch.setattr(srv.stream, 'STALL_WRITE_S', 1.0)
    monkeypatch.setattr(srv.stream, 'WATCHDOG_TICK_S', 0.2)
    flood = "import sys\nfor _ in range(2000): sys.stdout.buffer.write(b'x' * 65536)\nsys.stdout.flush()\nimport time; time.sleep(120)"
    body = json.dumps(_body({'command': [sys.executable, '-c', flood]})).encode()
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    sock.connect(('127.0.0.1', srv.port))
    sock.sendall(b'POST /api/secrets/exec-stream HTTP/1.1\r\nHost: x\r\n'
                 b'Content-Type: application/json\r\nX-Clayrune-Exec-Token: '
                 + srv.token.encode() + b'\r\nContent-Length: ' + str(len(body)).encode()
                 + b'\r\n\r\n' + body)                       # ...and never read a byte
    try:
        session = _only_session(srv)
        _wait(lambda: session.proc.poll() is not None, timeout=20,
              what='a child whose reader stalled to be killed')
        _wait(lambda: not srv.stream._sessions, what='the session to be forgotten')
        audit = [json.loads(line) for line in
                 srv.vault.audit_path().read_text(encoding='utf-8').splitlines() if line.strip()]
        assert any(e.get('event') == 'exec_stream_end' and e.get('reason') == 'write_stalled'
                   for e in audit)
        # the server thread that was stuck sending is released too: the connection ends
        sock.settimeout(10)
        end = time.monotonic() + 20
        closed = False
        while time.monotonic() < end and not closed:
            try:
                closed = sock.recv(1 << 20) == b''
            except OSError:
                closed = True                                  # reset also counts as closed
        assert closed, 'the stalled connection was left open'
    finally:
        sock.close()


# ── P2-3: a locked vault refuses the route even when no secret is referenced ─

def test_a_secret_free_command_is_refused_while_the_server_vault_is_locked(srv):
    srv.vault._unlocked_key = None
    srv.vault._lock_notified = True
    conn, resp = _post_json(srv, {'command': [sys.executable, '-c', 'print(1)']})
    assert resp.status == 423 and json.loads(resp.read())['error'] == 'vault_locked'
    from mc.state import tracked_processes
    assert not srv.stream._sessions and not tracked_processes


def test_a_secret_free_command_runs_when_the_vault_was_never_locked_by_a_passphrase(srv):
    srv.vault._unlocked_key = None
    srv.vault.wrapped_key_path().unlink()                      # no passphrase configured
    assert srv.vault.lock_state() == 'unconfigured'
    conn, resp = _post_json(srv, {'command': [sys.executable, '-c', 'print(1)']})
    assert resp.status == 200
    resp.read()


# ── P2-5: redaction also covers the JSON-escaped form of a value ────────────

def test_the_redactor_also_catches_a_value_in_its_json_escaped_form(srv):
    tricky = 'ab"cd\\ef/gh-9999'
    srv.vault.set_secret('tricky.token', tricky)
    srv.vault._dispensed.clear()
    srv.vault.get_secret_value('tricky.token', consumer='test')
    red = srv.stream.StreamRedactor()
    reply = json.dumps({'jsonrpc': '2.0', 'result': {'echo': tricky}}) + '\n'
    assert tricky not in reply and json.dumps(tricky)[1:-1] in reply   # the plain form is NOT present
    out = red.feed(reply.encode()) + red.finish()
    assert json.dumps(tricky)[1:-1].encode() not in out
    assert b'[redacted:tricky.token]' in out
    assert json.loads(out)['result']['echo'] == '[redacted:tricky.token]'  # still valid JSON
    # ...also when the escaped form straddles two reads
    mid = len(reply) // 2
    out = red.feed(reply.encode()[:mid]) + red.feed(reply.encode()[mid:]) + red.finish()
    assert b'ab\\"cd' not in out


# ── P2-6: the stdin body cap does not trust Content-Length ──────────────────

def _chunked(total, piece=65536):
    sent = 0
    while sent < total:
        n = min(piece, total - sent)
        sent += n
        yield b'x' * n


DRAIN_CHILD = ("import sys, time\n"
               "while sys.stdin.buffer.read(4096): pass\n"
               "time.sleep(120)")


def test_a_chunked_stdin_post_over_the_cap_is_refused_without_a_content_length(srv, monkeypatch):
    monkeypatch.setattr(srv.stream, 'MAX_STDIN_POST', 100_000)
    conn, resp = _post_json(srv, {'command': [sys.executable, '-c', DRAIN_CHILD]})
    session = _only_session(srv)
    c = http.client.HTTPConnection('127.0.0.1', srv.port, timeout=15)
    c.request('POST', f'/api/secrets/exec-stream/{session.id}/stdin', body=_chunked(2_000_000),
              headers={'X-Clayrune-Exec-Token': srv.token,
                       'Transfer-Encoding': 'chunked'}, encode_chunked=True)
    assert c.getresponse().status == 413
    # a body inside the cap, chunked, still goes through
    c2 = http.client.HTTPConnection('127.0.0.1', srv.port, timeout=15)
    c2.request('POST', f'/api/secrets/exec-stream/{session.id}/stdin', body=_chunked(5000),
               headers={'X-Clayrune-Exec-Token': srv.token,
                        'Transfer-Encoding': 'chunked'}, encode_chunked=True)
    assert c2.getresponse().status == 204
    resp.close()
    conn.close()


def test_a_content_length_over_the_cap_is_still_refused(srv, monkeypatch):
    monkeypatch.setattr(srv.stream, 'MAX_STDIN_POST', 1000)
    conn, resp = _post_json(srv, {'command': [sys.executable, '-c', SLEEP_CHILD]})
    session = _only_session(srv)
    assert _stdin_post(srv, session.id, b'y' * 5000).status == 413
    resp.close()
    conn.close()


# ── the 'Vault locked' push: once per lock period, never while unlocked ─────

@pytest.fixture()
def pushes(srv, monkeypatch):
    from mc.blueprints import push_mobile
    calls: list = []
    monkeypatch.setattr(push_mobile, '_notify_push', lambda *a, **k: calls.append(a))
    return calls


def _notify(srv):
    conn = http.client.HTTPConnection('127.0.0.1', srv.port, timeout=10)
    conn.request('POST', '/api/secrets/notify-vault-locked', b'{}',
                 {'Content-Type': 'application/json', 'X-Clayrune-Exec-Token': srv.token})
    resp = conn.getresponse()
    return resp.status, json.loads(resp.read())


def test_the_vault_locked_relay_does_not_push_while_the_server_vault_is_unlocked(srv, pushes):
    srv.vault._lock_notified = False
    assert srv.vault.lock_state() == 'unlocked'
    assert _notify(srv)[0] == 200
    assert _notify(srv)[0] == 200
    assert pushes == []


def test_the_vault_locked_relay_pushes_once_per_lock_period(srv, pushes):
    srv.vault.lock_now()                                       # a new lock period
    assert srv.vault.lock_state() == 'locked'
    for _ in range(3):                                         # three CLIs, one lock period
        assert _notify(srv)[0] == 200
    assert len(pushes) == 1
    srv.vault.unlock_with_passphrase('a real passphrase')
    assert _notify(srv)[0] == 200 and len(pushes) == 1         # unlocked: nothing
    srv.vault.lock_now()                                       # the NEXT lock period
    assert _notify(srv)[0] == 200 and len(pushes) == 2
