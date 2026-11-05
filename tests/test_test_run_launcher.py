"""P5 subprocess fixtures use an isolated ephemeral HTTP app, never :5199."""
from __future__ import annotations

import importlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

from flask import Flask, jsonify, request
import psutil
import pytest
from werkzeug.serving import make_server, WSGIRequestHandler

from mc import state
from mc.blueprints import test_run_routes as routes
from mc.test_run_store import TestRunStore

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))
launcher = importlib.import_module('test_run_launcher')
transport = importlib.import_module('test_run_transport')
ENTRY = str(TOOLS / 'test-run.py')


class Quiet(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


@pytest.fixture
def api(tmp_path, monkeypatch):
    routes.close()
    # The actual pytest process is the controlled managed root; the launcher's
    # actual sockets and ancestry are resolved by the production OS resolver.
    proc = SimpleNamespace(pid=os.getpid(), poll=lambda: None)
    sessions = {'owner': dict(session_id='owner', project_id='fixture', proc=proc,
                              _agent_cwd=str(tmp_path), status='running')}
    monkeypatch.setattr(state, 'CONFIG', {routes.FLAG: True})
    monkeypatch.setattr(state, 'agent_sessions', sessions)
    monkeypatch.setattr(state, 'tracked_processes', {})
    routes.wire(data_root=tmp_path / 'data',
                load_project_fn=lambda pid: {'id': pid, 'project_path': str(tmp_path)} if pid == 'fixture' else None,
                load_agent_log_fn=lambda _: [], session_cwd_fn=lambda s, p: s['_agent_cwd'],
                proc_identity_fn=lambda pid: (None, psutil.Process(pid).create_time()))
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    seen, registered, waits = [], [], []
    controls = dict(config=True, refuse_submit=False, refuse_finalize=False, stall=0,
                    incognito=False, sandboxed=False)

    @app.before_request
    def observe():
        if request.method == 'POST':
            seen.append((request.path, request.get_json(), request.headers.get('Authorization')))
            # The production blueprint reads the bounded raw stream directly;
            # recording this fixture request must leave that stream intact.
            request.stream = io.BytesIO(request.get_data())
            if request.path.endswith('/finalize'):
                # Every spawned command PID is already gone or exited by this
                # point. Use create_time-bound observations, not PID text in logs.
                waits.append(all(not p.is_running() or p.status() == psutil.STATUS_ZOMBIE
                                 for p in registered))
        if request.path.endswith('/submit') and controls['refuse_submit']:
            return jsonify(error='do not echo me'), 500
        if request.path.endswith('/finalize') and controls['refuse_finalize']:
            return jsonify(error='do not echo me'), 503

    @app.get('/api/config')
    def config():
        time.sleep(controls['stall'])
        return jsonify(test_run_progress_enabled=controls['config'])

    @app.post('/api/processes/register')
    def register():
        pid = request.get_json()['pid']
        try:
            registered.append(psutil.Process(pid))
        except psutil.NoSuchProcess:
            pass  # A fast fixture may finish before registration, as in production.
        return jsonify(ok=True)

    server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=Quiet)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    value = SimpleNamespace(port=server.server_port, app=app, seen=seen, registered=registered,
                            waits=waits, controls=controls, sessions=sessions, tmp=tmp_path,
                            store=TestRunStore(tmp_path / 'data'))
    yield value
    server.shutdown()
    thread.join(2)
    server.server_close()
    routes.close()


def invoke(api, *arguments, timeout=40, env=None, cwd=None):
    return subprocess.run([sys.executable, ENTRY, '--project', 'fixture', '--port', str(api.port),
                           *arguments], cwd=cwd or api.tmp, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout)


def snapshots(api):
    return api.store.list_runs('fixture', limit=100)


def family(api, size):
    runs = snapshots(api)
    assert len(runs) == size + 1, [(r['state'], r['display_label']) for r in runs]
    parent = next(r for r in runs if r['parent_run_id'] is None)
    children = [r for r in runs if r['parent_run_id'] == parent['run_id']]
    assert len(children) == size
    assert {r['owner_session_id'] for r in runs} == {'owner'}
    assert len({r['process_identity'] for r in runs}) == 1
    assert all(r['process_evidence']['source'] == 'launcher_observed' for r in runs)
    assert api.waits and all(api.waits)
    return parent, children


@pytest.mark.parametrize('code', [0, 1, 5, 17])
def test_actual_exit_and_parent_child_finalization(api, code):
    done = invoke(api, '--', sys.executable, '-c', f'import time; time.sleep(.1); raise SystemExit({code})')
    assert done.returncode == code, done.stderr.decode()
    assert done.stderr == b'', done.stderr.decode()
    parent, children = family(api, 1)
    for record in [parent, *children]:
        assert record['exit_code'] == code
        assert record['state'] == ('passed' if code == 0 else 'failed')
        assert record['unit'] == 'script' and record['selected_total'] == record['completed'] == 1
    assert children[0]['capture'] == 'partial'
    assert children[0]['diagnostics'][0]['code'] == 'framework_reporter_unavailable'


@pytest.mark.skipif(os.name != 'nt', reason='Unsigned DWORD status is a Windows process contract')
@pytest.mark.parametrize('code', [0x80000001, 0xFFFFFFFF])
def test_windows_unsigned_exit_is_not_truncated_by_system_exit(api, code):
    done = invoke(api, '--', sys.executable, '-c',
        f'import ctypes; ctypes.windll.kernel32.ExitProcess(ctypes.c_uint32({code}))')
    assert done.returncode == code and done.stderr == b'', done.stderr.decode()
    parent, children = family(api, 1)
    assert parent['exit_code'] == children[0]['exit_code'] == code


def test_exact_argv_cwd_environment_and_independent_binary_streams(api):
    directory = api.tmp / 'external project with spaces'
    directory.mkdir()
    supplied = ['', 'space here', 'quote"\'literal', '$HOME;$(no)', 'snowman-☃', 'back\\slash']
    env = dict(os.environ, P5_INHERITED='untouched')
    code = ('import json,os,sys; '
            'sys.stdout.buffer.write(json.dumps([sys.argv[1:],os.getcwd(),os.environ["P5_INHERITED"]]).encode()); '
            'sys.stderr.buffer.write(bytes([0,255,10,13]))')
    done = invoke(api, '--cwd', str(directory), '--', sys.executable, '-c', code, *supplied, env=env)
    assert done.returncode == 0 and done.stderr == bytes([0, 255, 10, 13]), done.stderr
    assert json.loads(done.stdout) == [supplied, str(directory), 'untouched']
    family(api, 1)
    # No raw output, commands, environment, receipts or capabilities in persisted records.
    records = '\n'.join(p.read_text(encoding='utf-8') for p in (api.tmp / 'data').rglob('*.json'))
    for text in ['P5_INHERITED', 'untouched', 'snowman', '$HOME', 'sys.stdout', 'finalize_capability']:
        assert text not in records
    for path, body, auth in api.seen:
        if path.endswith('/register'):
            assert body['command'] == 'test-run child (arguments withheld)'
        if path.endswith('/finalize'):
            assert auth not in done.stderr.decode(errors='replace')
    submit_tokens = {auth for path, _, auth in api.seen if path.endswith('/submit')}
    finalize_tokens = {auth for path, _, auth in api.seen if path.endswith('/finalize')}
    assert len(finalize_tokens) == 2
    assert len(submit_tokens.intersection(finalize_tokens)) == 0


def manifest(api, codes, *, stop=True, **overrides):
    path = api.tmp / 'manifest.json'
    path.write_text(json.dumps({'schema': 1, 'stop_on_failure': stop, 'units': [dict(
        argv=[sys.executable, '-c', f'import time; time.sleep(.05); raise SystemExit({code})'], **overrides)
        for code in codes]}), encoding='utf-8')
    return str(path)


@pytest.mark.parametrize('stop,expected_units', [(True, 2), (False, 4)])
def test_manifest_order_fail_fast_or_continue_first_nonzero(api, stop, expected_units):
    done = invoke(api, '--manifest', manifest(api, [0, 7, 4, 0], stop=stop))
    assert done.returncode == 7 and done.stderr == b'', done.stderr.decode()
    parent, children = family(api, expected_units)
    assert parent['selected_total'] == 4 and parent['completed'] == expected_units
    assert parent['counts']['skip'] == 0
    assert parent['counts']['fail'] == (1 if stop else 2)
    # Finalize's contract has no not_run field. Unexecuted = selected-completed;
    # the reporter-only finish carries the explicit count without inventing skips.
    finish = [e for path, body, _ in api.seen if path.endswith('/submit')
              for e in body['events'] if e['type'] == 'run_finished'][-1]
    assert finish['payload']['not_run'] == 4 - expected_units
    assert all(c['completed'] == 1 for c in children)


def test_manifest_scoped_environment_removal_and_cwd(api, monkeypatch):
    monkeypatch.setenv('P5_REMOVE', 'secret fixture value')
    monkeypatch.setenv('P5_ADD', 'inherited value')
    folder = api.tmp / 'nested'
    folder.mkdir()
    path = api.tmp / 'units.json'
    path.write_text(json.dumps({'schema': 1, 'stop_on_failure': True, 'units': [{
        'argv': [sys.executable, '-c', 'import os; print(os.getcwd()); print(os.getenv("P5_REMOVE")); print(os.getenv("P5_ADD"))'],
        'cwd': 'nested', 'env': {('p5_remove' if os.name == 'nt' else 'P5_REMOVE'): None,
                                ('p5_add' if os.name == 'nt' else 'P5_ADD'): 'added'}}]}), encoding='utf-8')
    done = invoke(api, '--manifest', str(path))
    assert done.returncode == 0 and done.stderr == b'', done.stderr.decode()
    assert done.stdout.decode().splitlines() == [str(folder), 'None', 'added']


def test_missing_executable_is_nonzero_and_finalized(api):
    done = invoke(api, '--', str(api.tmp / 'missing-executable'))
    assert done.returncode == 127
    assert b'child_start_failed_FileNotFoundError' in done.stderr
    parent, children = family(api, 1)
    assert parent['state'] == children[0]['state'] == 'failed'


def test_timeout_waits_for_killed_child_and_stops_manifest(api):
    path = api.tmp / 'timeout.json'
    path.write_text(json.dumps({'schema': 1, 'stop_on_failure': False, 'units': [
        {'argv': [sys.executable, '-c', 'import time; time.sleep(30)'], 'timeout_seconds': .15},
        {'argv': [sys.executable, '-c', 'print("must not run")']}]}), encoding='utf-8')
    done = invoke(api, '--manifest', str(path))
    assert done.returncode != 0 and b'must not run' not in done.stdout
    assert done.stderr == b'', done.stderr.decode()
    parent, children = family(api, 1)
    assert parent['state'] == children[0]['state'] == 'timeout'
    assert parent['exit_code'] == children[0]['exit_code'] == done.returncode


def test_cancellation_event_uses_actual_wait_code_on_this_platform(api, monkeypatch):
    original = launcher.execute
    def cancel_before_wait(spec, client, project, cancelled, telemetry, key):
        cancelled.set()
        return original(spec, client, project, cancelled, telemetry, key)
    monkeypatch.setattr(launcher, 'execute', cancel_before_wait)
    args, units, stop = launcher.parse(['--project', 'fixture', '--port', str(api.port),
        '--', sys.executable, '-c', 'import time; time.sleep(30)'])
    code = launcher.run(args, units, stop)
    assert code != 0
    parent, children = family(api, 1)
    assert parent['state'] == children[0]['state'] == 'cancelled'
    assert parent['exit_code'] == children[0]['exit_code'] == code


def test_large_binary_output_does_not_deadlock_or_enter_telemetry(api):
    done = invoke(api, '--', sys.executable, '-c',
        'import sys; sys.stdout.buffer.write(b"x"*1048576); sys.stderr.buffer.write(b"y"*1048576)')
    assert done.returncode == 0 and len(done.stdout) == len(done.stderr) == 1048576
    assert set(done.stdout) == {ord('x')} and set(done.stderr) == {ord('y')}
    family(api, 1)
    assert all('x' * 100 not in json.dumps(body) and 'y' * 100 not in json.dumps(body)
               for path, body, _ in api.seen)


@pytest.mark.skipif(os.name == 'nt', reason='Windows Popen termination is not POSIX signal evidence')
def test_child_signal_is_propagated_as_signal_not_truncated_exit(api):
    done = invoke(api, '--', sys.executable, '-c', 'import os,signal; os.kill(os.getpid(),signal.SIGTERM)')
    assert done.returncode == -signal.SIGTERM
    parent, children = family(api, 1)
    assert parent['process_evidence']['termination'] == 'signalled'
    assert children[0]['process_evidence']['signal'] == signal.SIGTERM


@pytest.mark.skipif(os.name == 'nt', reason='Use POSIX SIGTERM for deterministic cancellation')
def test_cancel_launcher_kills_only_its_child_waits_then_finalizes(api):
    proc = subprocess.Popen([sys.executable, ENTRY, '--project', 'fixture', '--port', str(api.port),
                             '--', sys.executable, '-c', 'import time; time.sleep(30)'],
                            cwd=api.tmp, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not api.registered and time.monotonic() < deadline:
            time.sleep(.02)
        assert api.registered
        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=10)
        assert proc.returncode != 0 and stderr == b'', stderr.decode()
        parent, children = family(api, 1)
        assert parent['state'] == children[0]['state'] == 'cancelled'
        assert parent['exit_code'] == children[0]['exit_code'] == proc.returncode
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


@pytest.mark.parametrize('flag', [False, 'true', 1, None])
def test_disabled_flag_still_executes_exact_child_without_enrollment(api, flag):
    api.controls['config'] = flag
    done = invoke(api, '--', sys.executable, '-c', 'print("original"); raise SystemExit(19)')
    assert done.returncode == 19 and done.stdout.strip() == b'original'
    assert b'capture_disabled' in done.stderr
    assert not any('/test-runs/' in path for path, _, _ in api.seen)
    assert not snapshots(api)


@pytest.mark.parametrize('policy', ['incognito', '_codex_unattended_sandbox'])
def test_restricted_owner_cannot_create_telemetry_or_change_local_result(api, policy):
    api.sessions['owner'][policy] = True
    done = invoke(api, '--', sys.executable, '-c', 'raise SystemExit(9)')
    assert done.returncode == 9 and b'http_403_progress_incomplete' in done.stderr
    assert not snapshots(api)


@pytest.mark.parametrize('operation', ['refuse_submit', 'refuse_finalize'])
def test_failed_telemetry_cannot_mask_real_local_exit(api, operation):
    api.controls[operation] = True
    done = invoke(api, '--', sys.executable, '-c', 'raise SystemExit(23)')
    assert done.returncode == 23 and b'progress_incomplete' in done.stderr
    assert b'do not echo me' not in done.stderr
    if operation == 'refuse_submit':
        assert all(r['state'] == 'failed' and r['capture'] == 'complete' for r in snapshots(api))
        # No successful diagnostic was persisted; local output discloses the gap.
    else:
        assert all(r['state'] not in ('passed', 'failed') for r in snapshots(api))


def test_unavailable_server_preserves_child_and_output(tmp_path):
    # Bind a port without listening, holding it so another service cannot race in.
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        done = subprocess.run([sys.executable, ENTRY, '--project', 'fixture', '--port', str(sock.getsockname()[1]),
                               '--', sys.executable, '-c', 'print("still ran"); raise SystemExit(11)'],
                              cwd=tmp_path, capture_output=True, timeout=5)
    assert done.returncode == 11 and done.stdout.strip() == b'still ran'
    assert b'progress_unavailable' in done.stderr and b'process_registration_failed' in done.stderr


@pytest.mark.parametrize('shell,command,expected', [
    ('sh', 'printf "literal $%s" "value"; exit 8', b'literal $value'),
    ('powershell', 'Write-Output "literal value"; exit 8', b'literal value'),
    ('cmd', 'echo literal value & exit /b 8', b'literal value'),
])
def test_explicit_shell_preserves_command_and_exit(api, shell, command, expected):
    import shutil
    if not shutil.which(shell):
        pytest.skip('requested shell not installed')
    done = invoke(api, '--shell', shell, '--command', command)
    assert done.returncode == 8 and done.stdout.strip() == expected, done.stderr.decode()
    family(api, 1)


@pytest.mark.skipif(os.name != 'nt', reason='Windows cmd raw quoting contract')
def test_cmd_preserves_quoted_executable_script_paths_and_metacharacters(api):
    directory = api.tmp / 'script directory with spaces'
    directory.mkdir()
    script = directory / 'echo arguments.py'
    script.write_text('import json,sys; print(json.dumps(sys.argv[1:]))', encoding='utf-8')
    command = f'"{sys.executable}" "{script}" "space here" "a&b" & exit /b 8'
    done = invoke(api, '--shell', 'cmd', '--command', command)
    assert done.returncode == 8 and done.stderr == b'', done.stderr.decode()
    assert json.loads(done.stdout) == ['space here', 'a&b']
    family(api, 1)


@pytest.mark.parametrize('data', [
    {}, {'argv': []}, {'argv': ['']}, {'argv': ['x', 1]}, {'argv': ['x\0']},
    {'argv': ['x'], 'shell': 'sh'}, {'argv': ['x'], 'unknown': 1},
    {'argv': ['x'], 'env': []}, {'argv': ['x'], 'env': {'A': 1}},
    {'argv': ['x'], 'env': {'A=B': 'c'}}, {'argv': ['x'], 'env': {'A': '\0'}},
    {'argv': ['x'], 'cwd': ''}, {'argv': ['x'], 'cwd': 'missing'},
    {'argv': ['x'], 'timeout_seconds': -1}, {'argv': ['x'], 'timeout_seconds': float('inf')},
    {'argv': ['x'], 'timeout_seconds': True}, {'shell': 'unknown', 'command': 'x'},
])
def test_invalid_unit_rejected_before_start(tmp_path, data):
    with pytest.raises((ValueError, TypeError)):
        launcher.unit(data, str(tmp_path), None)


@pytest.mark.parametrize('raw', [
    '{}', '[]', '{"schema":1,"schema":1}',
    '{"schema":true,"stop_on_failure":true,"units":[{"argv":["x"]}]}',
    '{"schema":1,"stop_on_failure":1,"units":[{"argv":["x"]}]}',
    '{"schema":1,"stop_on_failure":true,"units":[]}',
    '{"schema":1,"stop_on_failure":true,"units":[{"argv":["x"],"timeout_seconds":NaN}]}',
])
def test_malformed_manifest_is_not_executed(tmp_path, raw):
    path = tmp_path / 'bad.json'
    path.write_text(raw, encoding='utf-8')
    with pytest.raises((ValueError, TypeError)):
        launcher.parse(['--project', 'x', '--manifest', str(path)])


def test_manifest_limits_and_mode_conflicts_are_refused_before_execution(tmp_path):
    path = tmp_path / 'too-many.json'
    path.write_text(json.dumps({'schema': 1, 'stop_on_failure': True,
        'units': [{'argv': ['does-not-execute']}] * (launcher.MAX_UNITS + 1)}), encoding='utf-8')
    with pytest.raises(ValueError):
        launcher.parse(['--project', 'x', '--manifest', str(path)])
    path.write_bytes(b'x' * (launcher.MAX_MANIFEST_BYTES + 1))
    with pytest.raises(ValueError):
        launcher.parse(['--project', 'x', '--manifest', str(path)])
    with pytest.raises(ValueError):
        launcher.parse(['--project', 'x', '--manifest', str(path), '--', 'another-command'])


def test_requested_shell_has_no_different_shell_fallback(monkeypatch):
    monkeypatch.setattr(launcher.shutil, 'which', lambda _: None)
    with pytest.raises(ValueError, match='requested shell unavailable'):
        launcher.shell_argv('bash', 'never execute')


@pytest.mark.parametrize('arguments', [
    [], ['--command', 'hi'], ['--shell', 'sh', '--', 'echo', 'hi'],
    ['--port', '0', '--', 'x'], ['--port', '65536', '--', 'x'],
    ['--timeout-seconds', 'nan', '--', 'x'],
])
def test_invalid_invocation_returns_usage_failure(arguments):
    with pytest.raises((ValueError, SystemExit)):
        launcher.parse(['--project', 'x', *arguments])


def test_slow_transport_bounded_queue_and_final_wait(monkeypatch):
    started, release = threading.Event(), threading.Event()
    class Slow:
        def request(self, *a, **kw):
            started.set()
            release.wait(2)
            return {'test_run_progress_enabled': False}
    warnings = []
    monkeypatch.setattr(transport, 'FINAL_DRAIN_SECONDS', .02)
    telemetry = transport.Telemetry(Slow(), warnings.append)
    assert started.wait(1)
    for index in range(transport.QUEUE_SIZE + 10):
        telemetry.send('enroll', str(index), {})
    assert telemetry.pending.qsize() == transport.QUEUE_SIZE
    before = time.monotonic()
    telemetry.close()
    assert time.monotonic() - before < .2
    assert 'telemetry_queue_full_progress_incomplete' in warnings
    assert 'telemetry_final_drain_timeout_progress_incomplete' in warnings
    release.set()
    telemetry.thread.join(1)


def test_receipt_renewal_changes_generation_not_finalization_capability():
    calls = []
    class Client:
        def request(self, method, path, body=None, token=None):
            calls.append((path, body, token))
            if path.endswith('/producer'):
                return {'receipt': {'token': 'new', 'expires_at': time.time() + 3600}}
            return {}
    item = object.__new__(transport.Telemetry)
    item.client = Client()
    record = dict(path='/run', receipt={'token': 'old', 'expires_at': 0}, seq=200, capability='launcher-only')
    item._submit(record, [launcher.event('heartbeat', output=True)])
    assert record['capability'] == 'launcher-only' and record['seq'] == 1
    assert calls[-1][2] == 'new' and calls[-1][1]['events'][0]['producer_seq'] == 1


def test_output_pump_keeps_draining_after_broken_pipe():
    read, write = os.pipe()
    os.write(write, b'fixture bytes')
    os.close(write)
    class Broken:
        def write(self, chunk):
            raise BrokenPipeError()
    output, errors = threading.Event(), threading.Event()
    source = os.fdopen(read, 'rb')
    launcher._pump(source, Broken(), output, errors)
    assert source.closed and output.is_set() and errors.is_set()
