"""Real registered CLI trees, including an exited wrapper with inherited pipes."""
import json
import os
import subprocess
import sys
import time

import pytest
import requests
import psutil

from mc import agent_runtime as rt, api_credits_offload as offload
from mc import transform_process_deadline as process
from mc.agent_mail_launder import MailLaunder
from mc.agent_mail_store import MailStore, MailStoreConfig


@pytest.mark.parametrize('root_exits', [False, True], ids=['live-root', 'exited-root'])
def test_cli_descendant_pipe_cannot_outlive_total_deadline(monkeypatch, tmp_path, root_exits):
    runtime = rt.ClaudeRuntime()
    monkeypatch.setattr(rt, 'claude_oneshot_available', lambda: True)
    monkeypatch.setattr(rt, 'get_runtime', lambda name: runtime)
    monkeypatch.setattr(rt, '_authorize_text_transform', lambda *a, **kw: None)
    monkeypatch.setattr(offload, 'try_offload', lambda **kw: None)
    monkeypatch.setattr(offload, 'note_subscription_call', lambda **kw: None)
    monkeypatch.setattr(rt._transform_sandbox, 'neutral_cwd', lambda: str(tmp_path))
    monkeypatch.setattr(rt._transform_sandbox, 'transform_env', lambda: dict(os.environ))
    pid_file = tmp_path / 'child.pid'
    sleeper = tmp_path / 'synthetic_sleeper.py'
    sleeper.write_text('import time\ntime.sleep(7)\n')
    wrapper = tmp_path / 'synthetic_cli.py'
    wrapper.write_text('''import subprocess,sys,time,json,urllib.request
from pathlib import Path
p = subprocess.Popen([sys.executable, sys.argv[2]], stdin=subprocess.DEVNULL)
import psutil
Path(sys.argv[1]).write_text(json.dumps(dict(pid=p.pid, created=psutil.Process(p.pid).create_time())))
try:
    body = json.dumps(dict(pid=p.pid, name='Fenn synthetic inherited-pipe sleeper', project_id='mission_control', command='python sleep(7)')).encode()
    req = urllib.request.Request('http://localhost:5199/api/processes/register', data=body, headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=2) as response:
        response.read()
except Exception:
    p.kill()
    p.wait()
    raise
if sys.argv[3] != 'exit':
    time.sleep(10)
''')
    monkeypatch.setattr(runtime, '_oneshot_argv', lambda **kw: [
        sys.executable, str(wrapper), str(pid_file), str(sleeper), 'exit' if root_exits else 'sleep'])
    original = subprocess.Popen
    parents = []

    def register(proc):
        try:
            response = requests.post('http://localhost:5199/api/processes/register',
                json={'pid': proc.pid, 'name': 'Fenn synthetic CLI wrapper',
                      'project_id': 'mission_control', 'command': 'python synthetic_cli.py'}, timeout=2)
            response.raise_for_status()
        except Exception:
            proc.kill()
            proc.wait()
            raise

    def registered_popen(*a, **kw):
        proc = original(*a, **kw)
        parents.append(proc)
        if os.name != 'nt':
            register(proc)  # the new-session group is already established
        return proc

    monkeypatch.setattr(subprocess, 'Popen', registered_popen)
    if os.name == 'nt':
        original_assign = process.windows_process_job.create_for

        def assign_then_register(pid, **kwargs):
            # Register immediately AFTER the job is assigned, so the test's
            # HTTP request does not widen the production Popen/assign window.
            handle = original_assign(pid, **kwargs)
            register(parents[-1])
            return handle

        monkeypatch.setattr(process.windows_process_job, 'create_for', assign_then_register)
    readers = []
    stops = []
    original_stop = process._stop_owned

    def observed_stop(proc, **ownership):
        readers.append(ownership['reader'])
        stops.append(proc.poll())
        return original_stop(proc, **ownership)

    monkeypatch.setattr(process, '_stop_owned', observed_stop)
    store = MailStore(tmp_path / 'mail.sqlite3', config=MailStoreConfig(lease_seconds=6.2))
    store.register('install')
    store.bind_thread('install', 'thread', 'project', 'session')
    store.insert_event(event_id='event', registration_id='install', thread_token='thread',
                       message_id='<id@example.test>', body='Friday works.')
    start = time.monotonic()
    try:
        result = MailLaunder(store, enabled=True).process_next()
        elapsed = time.monotonic() - start
        print('CLI_TREE', {'elapsed': elapsed, 'lease': 6.2, 'budget': 3.2,
                          'result': result, 'parent_exit': parents[0].poll(),
                          'child_pid': json.loads(pid_file.read_text())['pid'],
                          'root_exited_before_cancel': stops[0] is not None,
                          'pipe_reader_finished': not readers[0].is_alive()})
        assert result['state'] == 'blocked', result
        assert result['reason'] == 'extraction_timeout', result
        # Dave's 13:14 decision: cleanup is inside the 3.2s total budget.
        assert elapsed < 3.2 + .25  # scheduling margin only
        assert parents[0].poll() is not None
        if root_exits:
            assert stops == [0], 'root must exit BEFORE cancellation to exercise the blocker'
        assert readers and not readers[0].is_alive()
        child_pid = json.loads(pid_file.read_text())['pid']
        assert not psutil.pid_exists(child_pid)
        assert store.claim_for_laundering() is None
    finally:
        for proc in parents:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        if pid_file.exists():
            try:
                identity = json.loads(pid_file.read_text())
                child = psutil.Process(identity['pid'])
                if child.is_running() and child.create_time() == identity['created']:
                    child.kill()
                    child.wait(timeout=3)
            except psutil.NoSuchProcess:
                pass
