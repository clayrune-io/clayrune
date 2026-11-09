"""Actual Windows subprocess.run timeout with an inherited output pipe."""
import json
import os
import subprocess
import sys
import time

import pytest
import requests
import psutil

from mc import agent_runtime as rt, api_credits_offload as offload
from mc.agent_mail_launder import MailLaunder
from mc.agent_mail_store import MailStore, MailStoreConfig


@pytest.mark.skipif(os.name != 'nt', reason='Windows pipe-drain timeout behavior')
def test_cli_descendant_pipe_cannot_outlive_total_deadline(monkeypatch, tmp_path):
    runtime = rt.ClaudeRuntime()
    monkeypatch.setattr(rt, 'claude_oneshot_available', lambda: True)
    monkeypatch.setattr(rt, 'get_runtime', lambda name: runtime)
    monkeypatch.setattr(rt, '_authorize_text_transform', lambda *a, **kw: None)
    monkeypatch.setattr(offload, 'try_offload', lambda **kw: None)
    monkeypatch.setattr(offload, 'note_subscription_call', lambda **kw: None)
    monkeypatch.setattr(rt._transform_sandbox, 'neutral_cwd', lambda: str(tmp_path))
    monkeypatch.setattr(rt._transform_sandbox, 'transform_env', lambda: dict(os.environ))
    pid_file = tmp_path / 'child.pid'
    wrapper = tmp_path / 'synthetic_cli.py'
    wrapper.write_text('''import subprocess,sys,time,json,urllib.request
from pathlib import Path
p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(7)'], stdin=subprocess.DEVNULL)
Path(sys.argv[1]).write_text(str(p.pid))
try:
    body = json.dumps(dict(pid=p.pid, name='Fenn synthetic inherited-pipe sleeper', project_id='mission_control', command='python sleep(7)')).encode()
    req = urllib.request.Request('http://localhost:5199/api/processes/register', data=body, headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=2) as response:
        response.read()
except Exception:
    p.kill()
    p.wait()
    raise
time.sleep(10)
''')
    monkeypatch.setattr(runtime, '_oneshot_argv', lambda **kw: [sys.executable, str(wrapper), str(pid_file)])
    original = subprocess.Popen
    parents = []
    def registered_popen(*a, **kw):
        proc = original(*a, **kw)
        parents.append(proc)
        try:
            response = requests.post('http://localhost:5199/api/processes/register',
                json={'pid': proc.pid, 'name': 'Fenn synthetic CLI wrapper',
                      'project_id': 'mission_control', 'command': 'python synthetic_cli.py'}, timeout=2)
            response.raise_for_status()
        except Exception:
            proc.kill()
            proc.wait()
            raise
        return proc
    monkeypatch.setattr(subprocess, 'Popen', registered_popen)
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
                          'child_pid': int(pid_file.read_text())})
        assert result['state'] == 'blocked', result
        assert result['reason'] == 'extraction_timeout', result
        # Dave's 13:14 decision: cleanup is inside the 3.2s total budget.
        assert elapsed < 3.2 + .25  # scheduling margin only
        assert parents[0].poll() is not None
        assert not psutil.pid_exists(int(pid_file.read_text()))
        assert store.claim_for_laundering() is None
    finally:
        for proc in parents:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        if pid_file.exists():
            try:
                child = psutil.Process(int(pid_file.read_text()))
                if child.is_running():
                    child.kill()
                    child.wait(timeout=3)
            except psutil.NoSuchProcess:
                pass
