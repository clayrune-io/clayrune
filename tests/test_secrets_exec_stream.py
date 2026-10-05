"""MC-1047: ``tools/with-secret.py --raw`` (and ``--unset``) on a passphrase-locked vault.

The server parents the child (``mc/secrets_exec_stream.py``, routes in
``mc/blueprints/secrets_exec_stream_routes.py``) and the CLI relays its pipes
(``mc/secrets_exec_stream_client.py``). Nothing here mocks the pipes: a real werkzeug server
runs the real blueprint over loopback, and the end-to-end cases launch the real
``tools/with-secret.py`` as a separate process whose own vault is locked, exactly as an agent's
MCP launch is.
"""
from __future__ import annotations

import http.client
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask
from werkzeug.serving import make_server

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SECRET = 'SENTINEL-VALUE-must-never-be-seen-9f3a'
WITH_SECRET = REPO / 'tools' / 'with-secret.py'


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    """A running server half: the vault is passphrase-locked ON DISK and unlocked in THIS
    process (the server). Anything run as a subprocess sees a locked vault."""
    home = tmp_path / '.clayrune'
    monkeypatch.setenv('CLAYRUNE_HOME', str(home))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    from mc import process_ledger, secrets_exec_stream as stream, secrets_store as vault
    from mc.blueprints import secrets_exec_stream_routes, secrets_routes
    vault._dispensed.clear()
    vault._unlocked_key = None
    vault._lock_notified = True
    vault._key_mismatch = False
    vault._exec_token = None
    vault.set_secret('demo.token', SECRET)
    vault.set_secret('other.token', 'other-project-value-77', scope='other_project')
    vault.set_secret('bank.password', 'bank-value-88', allow_unattended=False)
    vault.set_passphrase('a real passphrase')           # leaves THIS process unlocked
    token = vault.ensure_exec_token()
    monkeypatch.setattr(process_ledger, '_PID_LEDGER_PATH', tmp_path / 'mc_child_pids.json')
    monkeypatch.setattr(stream, 'HEARTBEAT_S', 0.2)
    monkeypatch.setattr(stream, 'ATTACH_GRACE_S', 5.0)

    app = Flask(__name__)
    app.register_blueprint(secrets_exec_stream_routes.bp)
    app.register_blueprint(secrets_routes.bp)      # the vault-locked push relay a locked CLI calls
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ns = SimpleNamespace(
        port=server.server_port, token=token, home=home, tmp=tmp_path, vault=vault,
        stream=stream, ledger=tmp_path / 'mc_child_pids.json',
        app=app,
        cli_env={**{k: v for k, v in os.environ.items() if k != 'CLAUDE_CODE_SESSION_ID'},
                 'CLAYRUNE_HOME': str(home), 'CLAYRUNE_SECRETS_KEY_BACKEND': 'file',
                 'MC_PORT': str(server.server_port), 'PYTHONIOENCODING': 'utf-8'})
    yield ns
    stream.shutdown_all()
    server.shutdown()
    thread.join(timeout=3)


def _cli(srv, *argv, env_extra=None):
    env = dict(srv.cli_env)
    env.update(env_extra or {})
    return subprocess.Popen([sys.executable, str(WITH_SECRET), *argv], env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, cwd=str(REPO))


def _lines(pipe) -> queue.Queue:
    q: queue.Queue = queue.Queue()

    def pump():
        for line in iter(pipe.readline, b''):
            q.put(line.decode('utf-8', 'replace').rstrip('\r\n'))
        q.put(None)

    threading.Thread(target=pump, daemon=True).start()
    return q


def _wait(pred, timeout=15.0, what='condition'):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError(f'timed out waiting for {what}')


def _body(body):
    """A request body as the CLI sends it: it carries its own environment."""
    return {'environ': dict(os.environ), **body}


def _launch(srv, body):
    return srv.stream.resolve_launch(_body(body))


def _post_json(srv, body, headers=None):
    conn = http.client.HTTPConnection('127.0.0.1', srv.port, timeout=15)
    h = {'Content-Type': 'application/json', 'X-Clayrune-Exec-Token': srv.token}
    h.update(headers or {})
    conn.request('POST', '/api/secrets/exec-stream', body=json.dumps(_body(body)), headers=h)
    return conn, conn.getresponse()


ECHO_CHILD = (
    "import os, sys\n"
    "print('ready', os.environ.get('TOKEN') == %r, flush=True)\n"
    "print('leak:' + os.environ['TOKEN'], flush=True)\n"
    "for line in sys.stdin:\n"
    "    print('echo:' + line.strip(), flush=True)\n"
    "print('stdin-closed', flush=True)\n"
    "print('to-stderr', file=sys.stderr, flush=True)\n"
    "sys.exit(7)\n"
) % SECRET

SLEEP_CHILD = "import time; time.sleep(120)"


# ── the round trip, through the real CLI process ────────────────────────────

def test_streaming_round_trip_is_interactive_and_returns_the_childs_exit_code(srv):
    cli = _cli(srv, '--raw', '--env', 'TOKEN=demo.token', '--',
               sys.executable, '-u', '-c', ECHO_CHILD)
    out = _lines(cli.stdout)
    assert out.get(timeout=20) == 'ready True'                 # the child got the real value
    assert out.get(timeout=5) == 'leak:[redacted:demo.token]'  # ...and printing it is scrubbed
    for word in ('first', 'second'):                           # request/response, not buffered
        cli.stdin.write(f'{word}\n'.encode())
        cli.stdin.flush()
        assert out.get(timeout=10) == f'echo:{word}'
    cli.stdin.close()                                          # EOF reaches the child
    assert out.get(timeout=10) == 'stdin-closed'
    assert cli.wait(timeout=15) == 7
    assert SECRET not in (cli.stderr.read().decode('utf-8', 'replace'))
    assert not srv.stream._sessions                            # session forgotten


def test_the_value_never_appears_in_any_output_log_or_audit_record(srv, capfd):
    cli = _cli(srv, '--raw', '--env', 'TOKEN=demo.token', '--',
               sys.executable, '-u', '-c', ECHO_CHILD)
    cli.stdin.close()
    stdout = cli.stdout.read().decode('utf-8', 'replace')
    stderr = cli.stderr.read().decode('utf-8', 'replace')
    cli.wait(timeout=20)
    assert SECRET not in stdout and SECRET not in stderr
    seen = capfd.readouterr()
    assert SECRET not in seen.out and SECRET not in seen.err   # the server's own log
    audit = srv.vault.audit_path().read_text(encoding='utf-8')
    assert SECRET not in audit
    events = [json.loads(line) for line in audit.splitlines() if line.strip()]
    assert any(e.get('event') == 'exec_stream_start' for e in events)
    assert any(e.get('event') == 'exec_stream_end' and e.get('reason') == 'exit' for e in events)
    assert any(e.get('event') == 'read' and e.get('consumer') == 'server-exec-stream'
               for e in events)                                # the vault read itself is audited


def test_a_secret_in_a_command_argument_is_resolved_and_the_preview_keeps_the_placeholder(srv):
    code = "import sys, time; print('arg-ok', sys.argv[1] == %r, flush=True); time.sleep(120)" % SECRET
    cli = _cli(srv, '--raw', '--', sys.executable, '-u', '-c', code, '{{secret:demo.token}}')
    launch = _launch(srv, {'command': ['x', '{{secret:demo.token}}']})
    assert launch.preview == 'x {{secret:demo.token}}' and launch.command == ['x', SECRET]
    out = _lines(cli.stdout)
    assert out.get(timeout=20) == 'arg-ok True'
    from mc.state import tracked_processes
    entries = [e for e in tracked_processes.values() if e['name'].startswith('with-secret child')]
    assert len(entries) == 1
    assert SECRET not in json.dumps({k: v for k, v in entries[0].items() if k != 'proc'})
    assert entries[0]['command_preview'].startswith(sys.executable)
    cli.kill()
    cli.wait(timeout=10)


# ── --unset ────────────────────────────────────────────────────────────────

def test_unset_is_applied_to_the_childs_environment_and_a_secret_under_that_name_still_arrives(
        srv, monkeypatch):
    cli_vars = {'NODE_OPTIONS': '--require /evil.js', 'KEEP_ME': 'yes'}   # the CLI's environment
    probe = ("import os; print(os.environ.get('NODE_OPTIONS', 'ABSENT'), "
             "os.environ.get('KEEP_ME'), os.environ.get('TOKEN') == %r)" % SECRET)
    kept = _cli(srv, '--raw', '--env', 'TOKEN=demo.token', '--', sys.executable, '-c', probe,
                env_extra=cli_vars)
    assert kept.communicate(timeout=30)[0].decode().strip() == '--require /evil.js yes True'
    dropped = _cli(srv, '--raw', '--unset', 'NODE_OPTIONS', '--env', 'TOKEN=demo.token', '--',
                   sys.executable, '-c', probe, env_extra=cli_vars)
    assert dropped.communicate(timeout=30)[0].decode().strip() == 'ABSENT yes True'
    same_name = _cli(srv, '--raw', '--unset', 'TOKEN', '--env', 'TOKEN=demo.token', '--',
                     sys.executable, '-c', probe, env_extra=cli_vars)
    assert same_name.communicate(timeout=30)[0].decode().strip().endswith('True')


def test_unset_without_raw_also_goes_through_the_streaming_route(srv):
    probe = "import os; print(os.environ.get('NODE_OPTIONS', 'ABSENT'))"
    cli = _cli(srv, '--unset', 'NODE_OPTIONS', '--env', 'TOKEN=demo.token', '--',
               sys.executable, '-c', probe, env_extra={'NODE_OPTIONS': '--require /evil.js'})
    assert cli.communicate(timeout=30)[0].decode().strip() == 'ABSENT'


# ── refusals ────────────────────────────────────────────────────────────────

def test_a_locked_server_vault_is_refused_and_no_child_starts(srv):
    srv.vault._unlocked_key = None                              # a human has not unlocked it
    srv.vault._lock_notified = True                             # no push relay to the live port
    conn, resp = _post_json(srv, {'env': [['TOKEN', 'demo.token']],
                                  'command': [sys.executable, '-c', 'print(1)']})
    assert resp.status == 423 and json.loads(resp.read())['error'] == 'vault_locked'
    from mc.state import tracked_processes
    assert not srv.stream._sessions and not tracked_processes


def test_the_cli_reports_the_locked_message_when_the_server_vault_is_locked_too(srv):
    srv.vault._unlocked_key = None
    srv.vault._lock_notified = True
    cli = _cli(srv, '--raw', '--env', 'TOKEN=demo.token', '--', sys.executable, '-c', 'print(1)')
    out, err = cli.communicate(timeout=30)
    assert cli.returncode == 2 and out == b''
    assert b'vault is locked' in err


def test_scope_is_enforced(srv):
    conn, resp = _post_json(srv, {'env': [['TOKEN', 'other.token']], 'project_id': 'my_project',
                                  'command': [sys.executable, '-c', 'print(1)']})
    assert resp.status == 400
    assert 'scope' in json.loads(resp.read())['error'].lower()
    assert not srv.stream._sessions


def test_allow_unattended_false_is_enforced_for_an_unattended_caller(srv, monkeypatch):
    conn, resp = _post_json(srv, {'env': [['TOKEN', 'bank.password']], 'unattended': True,
                                  'command': [sys.executable, '-c', 'print(1)']})
    assert resp.status == 400
    body = json.loads(resp.read())
    assert 'unattended' in body['error'].lower()
    assert not srv.stream._sessions
    monkeypatch.setattr(srv.vault, '_lookup_trigger_type', lambda sid: 'manual')
    conn, resp = _post_json(srv, {'env': [['TOKEN', 'bank.password']], 'unattended': False,
                                  'claude_session_id': 'a-manual-chat',
                                  'command': [sys.executable, '-c', 'print(1)']})
    assert resp.status == 200                                   # an attended session is allowed
    resp.close()


def test_the_gates_are_the_exec_routes_gates(srv):
    body = {'env': [['TOKEN', 'demo.token']], 'command': [sys.executable, '-c', 'print(1)']}
    conn = http.client.HTTPConnection('127.0.0.1', srv.port, timeout=10)
    conn.request('POST', '/api/secrets/exec-stream', json.dumps(body),
                 {'Content-Type': 'application/json'})
    assert conn.getresponse().status == 403                     # no token
    conn, resp = _post_json(srv, body, {'X-Clayrune-Exec-Token': 'wrong'})
    assert resp.status == 403
    conn, resp = _post_json(srv, body, {'Cf-Ray': 'abc'})
    assert resp.status == 403 and json.loads(resp.read())['error'] == 'tunnel_refused'
    assert not srv.stream._sessions
    for path in ('/api/secrets/exec-stream/x/stdin', '/api/secrets/exec-stream/x/end'):
        c = http.client.HTTPConnection('127.0.0.1', srv.port, timeout=10)
        c.request('POST', path, b'', {})
        assert c.getresponse().status == 403


def test_a_malformed_body_is_a_400(srv):
    for body in ({}, {'command': []}, {'command': 'ls'}, {'command': ['x'], 'unset': 'NODE_OPTIONS'}):
        conn, resp = _post_json(srv, body)
        assert resp.status == 400, body


def test_the_session_cap_is_enforced(srv, monkeypatch):
    monkeypatch.setattr(srv.stream, 'MAX_SESSIONS', 1)
    body = {'command': [sys.executable, '-c', SLEEP_CHILD]}
    conn1, r1 = _post_json(srv, body)
    assert r1.status == 200
    conn2, r2 = _post_json(srv, body)
    assert r2.status == 429
    r1.close()
    conn1.close()
    _wait(lambda: not srv.stream._sessions, what='first session to end')


# ── the child's lifetime is the relay's ─────────────────────────────────────

def _only_session(srv):
    _wait(lambda: len(srv.stream._sessions) == 1, what='a session to start')
    return next(iter(srv.stream._sessions.values()))


def test_killing_the_cli_kills_the_child(srv):
    cli = _cli(srv, '--raw', '--env', 'TOKEN=demo.token', '--', sys.executable, '-c', SLEEP_CHILD)
    session = _only_session(srv)
    proc = session.proc
    assert proc.poll() is None
    cli.kill()
    cli.wait(timeout=10)
    _wait(lambda: proc.poll() is not None, what='the child to die after its relay died')
    _wait(lambda: not srv.stream._sessions, what='the session to be forgotten')


def test_a_dropped_connection_kills_the_child_and_unregisters_it(srv):
    from mc.state import tracked_processes
    conn, resp = _post_json(srv, {'command': [sys.executable, '-c', SLEEP_CHILD]})
    assert resp.status == 200
    session = _only_session(srv)
    assert session.proc.pid in tracked_processes                # visible to the Process Manager
    ledger = json.loads(srv.ledger.read_text(encoding='utf-8'))
    assert session.proc.pid in [c['pid'] for c in ledger['children']]   # and to the restart reaper
    resp.close()                                                # the relay is gone (the
    conn.close()                                                # response holds the socket too)
    _wait(lambda: session.proc.poll() is not None, what='the child to die')
    _wait(lambda: session.proc.pid not in tracked_processes, what='unregistration')
    ledger = json.loads(srv.ledger.read_text(encoding='utf-8'))
    assert session.proc.pid not in [c['pid'] for c in ledger['children']]


def test_the_end_route_kills_the_child(srv):
    conn, resp = _post_json(srv, {'command': [sys.executable, '-c', SLEEP_CHILD]})
    session = _only_session(srv)
    c = http.client.HTTPConnection('127.0.0.1', srv.port, timeout=10)
    c.request('POST', f'/api/secrets/exec-stream/{session.id}/end', b'',
              {'X-Clayrune-Exec-Token': srv.token})
    assert c.getresponse().status == 204
    _wait(lambda: session.proc.poll() is not None, what='the child to die')
    c.request('POST', f'/api/secrets/exec-stream/{session.id}/end', b'',
              {'X-Clayrune-Exec-Token': srv.token})
    assert c.getresponse().status == 404                        # gone
    resp.close()
    conn.close()


def test_a_session_nobody_attaches_to_is_killed_at_the_deadline(srv, monkeypatch):
    monkeypatch.setattr(srv.stream, 'ATTACH_GRACE_S', 0.3)
    launch = _launch(srv, {'command': [sys.executable, '-c', SLEEP_CHILD]})
    session = srv.stream.start(launch)
    _wait(lambda: session.proc.poll() is not None, what='the unattached child to be killed')


def test_a_stdin_post_to_a_dead_session_is_410_or_404(srv):
    conn, resp = _post_json(srv, {'command': [sys.executable, '-c', 'print(1)']})
    session = _only_session(srv)
    _wait(lambda: session.proc.poll() is not None, what='the child to exit')
    resp.read()                                                 # drain to the exit frame
    c = http.client.HTTPConnection('127.0.0.1', srv.port, timeout=10)
    c.request('POST', f'/api/secrets/exec-stream/{session.id}/stdin', b'late',
              {'X-Clayrune-Exec-Token': srv.token})
    assert c.getresponse().status in (404, 410)


def test_server_exit_ends_every_session(srv):
    launch = _launch(srv, {'command': [sys.executable, '-c', SLEEP_CHILD]})
    session = srv.stream.start(launch)
    session.attach()
    srv.stream.shutdown_all()
    assert session.proc.poll() is not None and not srv.stream._sessions


# ── the redactor ────────────────────────────────────────────────────────────

def test_the_redactor_catches_a_value_split_across_reads_and_holds_nothing_back_otherwise(srv):
    srv.vault._dispensed.clear()
    srv.vault.get_secret_value('demo.token', consumer='test')    # dispense the value
    red = srv.stream.StreamRedactor()
    half = len(SECRET) // 2
    parts = [b'before ' + SECRET.encode()[:half], SECRET.encode()[half:] + b' after\n']
    out = b''.join(red.feed(p) for p in parts) + red.finish()
    assert out == b'before [redacted:demo.token] after\n'
    # A chunk that ends in something that could not start the value is emitted whole.
    assert red.feed(b'{"jsonrpc":"2.0"}\n') == b'{"jsonrpc":"2.0"}\n'
    # A held-back prefix that never completes is released at the end of the stream.
    assert red.feed(SECRET.encode()[:4]) == b'' and red.finish() == SECRET.encode()[:4]


# ── the wire format ─────────────────────────────────────────────────────────

def test_wire_frames_round_trip_and_reject_corruption():
    import io
    from mc import secrets_exec_stream_wire as wire
    buf = io.BytesIO(wire.pack(wire.CH_STDOUT, b'abc') + wire.pack_exit(-3) + wire.pack(wire.CH_HEARTBEAT))
    assert wire.read_frame(buf.read) == (wire.CH_STDOUT, b'abc')
    ch, payload = wire.read_frame(buf.read)
    assert ch == wire.CH_EXIT and wire.unpack_exit(payload) == -3
    assert wire.read_frame(buf.read) == (wire.CH_HEARTBEAT, b'')
    assert wire.read_frame(buf.read) is None
    with pytest.raises(ValueError):
        wire.read_frame(io.BytesIO(wire.pack(wire.CH_STDOUT, b'abcdef')[:-2]).read)
    with pytest.raises(ValueError):
        wire.read_frame(io.BytesIO(b'\x01\xff\xff\xff\xff').read)


# ── --stdin and stderr through the same relay ───────────────────────────────

def test_the_stdin_secret_is_piped_to_the_child_and_stderr_has_its_own_channel(srv):
    code = ("import sys; v = sys.stdin.read(); print('got', v == %r); "
            "print('on-stderr', file=sys.stderr)" % SECRET)
    cli = _cli(srv, '--raw', '--stdin', 'demo.token', '--', sys.executable, '-c', code)
    out, err = cli.communicate(timeout=30)
    assert out.decode().strip() == 'got True'
    assert err.decode().strip() == 'on-stderr'
    assert cli.returncode == 0


# ── the slice-4 Notion launch line, end to end, fixture vault, temp HOME ────

STUB_MCP = """\
import readline from 'node:readline';
const EXPECTED = %r;
const rl = readline.createInterface({ input: process.stdin });
rl.on('line', (line) => {
  const msg = JSON.parse(line);
  if (msg.method === 'initialize') {
    process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: msg.id, result: {
      serverInfo: { name: 'stub-notion' },
      tokenOk: process.env.NOTION_TOKEN === EXPECTED,
      nodeOptionsAbsent: !('NODE_OPTIONS' in process.env),
      nodePathAbsent: !('NODE_PATH' in process.env),
      echoed: process.env.NOTION_TOKEN } }) + String.fromCharCode(10));
  }
});
rl.on('close', () => process.exit(0));
"""


def test_the_notion_launch_line_starts_and_gets_its_token_on_a_passphrase_locked_vault(srv, monkeypatch):
    """The exact args `mc.desk_connect.mcp_activation.launch_config` writes for the curated Notion
    entry, run as an agent session would run them (a separate process, vault locked in it, `--raw`,
    both `--unset`s), with a stub MCP server in place of the downloaded package."""
    import shutil
    if not shutil.which('node'):
        pytest.skip('node is not installed')
    from mc.desk_connect import mcp_activation, mcp_catalogue, mcp_package_store
    entry = mcp_catalogue.for_service('notion')
    assert entry is not None
    srv.vault.set_secret(entry['credential']['vault'], SECRET)
    stub = mcp_package_store.entry_path(entry)
    stub.parent.mkdir(parents=True)
    stub.write_text(STUB_MCP % SECRET, encoding='utf-8')
    assert str(stub).startswith(str(srv.home))                # the fixture home, never the live one
    monkeypatch.setenv('NODE_OPTIONS', '--max-old-space-size=256')   # the server's environment
    monkeypatch.setenv('NODE_PATH', str(srv.tmp))
    cfg = mcp_activation.launch_config(entry)
    assert mcp_activation.is_ours(cfg, entry)
    env = {**srv.cli_env, 'HOME': str(srv.tmp), 'USERPROFILE': str(srv.tmp),
           'NODE_OPTIONS': '--max-old-space-size=256', 'NODE_PATH': str(srv.tmp)}
    proc = subprocess.Popen([cfg['command'], *cfg['args']], env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(srv.tmp))
    out = _lines(proc.stdout)
    proc.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
    proc.stdin.flush()
    first = out.get(timeout=30)
    assert first is not None, proc.stderr.read().decode('utf-8', 'replace')
    reply = json.loads(first)
    assert reply['id'] == 1
    result = reply['result']
    assert result['serverInfo'] == {'name': 'stub-notion'}
    assert result['tokenOk'] is True                           # the child holds the real token
    assert result['nodeOptionsAbsent'] is True and result['nodePathAbsent'] is True
    assert result['echoed'] == '[redacted:notion.token]'       # a stray echo is scrubbed
    proc.stdin.close()
    assert proc.wait(timeout=20) == 0
    assert SECRET not in proc.stderr.read().decode('utf-8', 'replace')
