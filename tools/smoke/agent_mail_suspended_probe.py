"""Real Windows roots: pre-assignment execution is impossible; failed setup reaps.

Retains Fenn r4's failed-assignment/no-survivor intent. Its old fixture waited for
the wrapper to exit before returning from Popen; a suspended wrapper cannot do
that. Native failure probes below use real WinAPI calls, not fake kernel APIs or
a substituted _stop_owned. No real mail/model or live configuration.
"""
import ctypes
from ctypes import wintypes
import json
import os
import subprocess
import sys
import time

import psutil
import pytest
import requests

from mc import agent_runtime as rt, api_credits_offload as offload
from mc import transform_process_deadline as process
from mc.agent_mail_launder import MailLaunder
from mc.agent_mail_store import MailStore, MailStoreConfig


@pytest.mark.skipif(sys.platform != 'win32', reason='real Windows suspended startup')
@pytest.mark.parametrize('failure', ['create_for', 'assign', 'resume'])
def test_failed_suspended_setup_has_no_surviving_root_or_descendant(monkeypatch, tmp_path, failure):
    runtime = rt.ClaudeRuntime()
    monkeypatch.setattr(rt, 'claude_oneshot_available', lambda: True)
    monkeypatch.setattr(rt, 'get_runtime', lambda name: runtime)
    monkeypatch.setattr(rt, '_authorize_text_transform', lambda *a, **kw: None)
    monkeypatch.setattr(offload, 'try_offload', lambda **kw: None)
    monkeypatch.setattr(offload, 'note_subscription_call', lambda **kw: None)
    monkeypatch.setattr(rt._transform_sandbox, 'neutral_cwd', lambda: str(tmp_path))
    monkeypatch.setattr(rt._transform_sandbox, 'transform_env', lambda: dict(os.environ))
    marker = tmp_path / 'executed'
    child_file = tmp_path / 'child.json'
    wrapper = tmp_path / 'wrapper.py'
    # Without suspension this immediately spawns, records its child and exits.
    wrapper.write_text('''import json,subprocess,sys
from pathlib import Path
import psutil
Path(sys.argv[1]).touch()
p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(15)'])
Path(sys.argv[2]).write_text(json.dumps(dict(pid=p.pid,created=psutil.Process(p.pid).create_time())))
''')
    monkeypatch.setattr(runtime, '_oneshot_argv', lambda **kw:
                        [sys.executable, str(wrapper), str(marker), str(child_file)])
    original_popen = subprocess.Popen
    original_create = process.windows_process_job.create_for
    roots = []
    observed_handles = []
    native_errors = []

    def spawn(*a, **kw):
        assert kw['creationflags'] & process.windows_process_job.CREATE_SUSPENDED
        root = original_popen(*a, **kw)
        roots.append(root)
        requests.post('http://localhost:5199/api/processes/register', json=dict(
            pid=root.pid, name='Sol r5 suspended wrapper', project_id='mission_control',
            command='synthetic immediate-spawn wrapper'), timeout=2).raise_for_status()
        # Deliberately widen the old race, as Fenn did. The root cannot exit or
        # spawn before create_for, even though the parent waits for it here.
        with pytest.raises(subprocess.TimeoutExpired):
            root.wait(timeout=.15)
        assert not marker.exists()
        assert psutil.Process(root.pid).children(recursive=True) == []
        return root

    def create(pid, **kw):
        root = roots[-1]
        assert root.poll() is None and not marker.exists()
        if failure == 'create_for':
            # Real setup failure: Windows refuses OpenProcess on reserved PID 0.
            handle = original_create(0, **kw)
            assert handle is None
            return handle
        if failure == 'assign':
            # Real failed assignment, not a fake API result. The root remains
            # suspended outside a job, so handle-only abort must clean it up.
            kernel = ctypes.windll.kernel32
            kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel.AssignProcessToJobObject.restype = wintypes.BOOL
            assert not kernel.AssignProcessToJobObject(None, int(root._handle))
            native_errors.append(ctypes.WinError().winerror)
            return None
        handle = original_create(pid, **kw)
        assert handle
        observed_handles.append(handle)
        return handle

    def fail_resume(pid):
        assert observed_handles and not marker.exists()
        kernel = ctypes.windll.kernel32
        kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel.ResumeThread.restype = wintypes.DWORD
        assert kernel.ResumeThread(None) == 0xFFFFFFFF
        error = ctypes.WinError()
        native_errors.append(error.winerror)
        raise error

    monkeypatch.setattr(subprocess, 'Popen', spawn)
    monkeypatch.setattr(process.windows_process_job, 'create_for', create)
    if failure == 'resume':
        monkeypatch.setattr(process.windows_process_job, 'resume_suspended', fail_resume)
    store = MailStore(tmp_path / 'mail.sqlite3', config=MailStoreConfig(lease_seconds=6.2))
    store.register('install')
    store.bind_thread('install', 'thread', 'project', 'session')
    store.insert_event(event_id='event', registration_id='install', thread_token='thread',
                       message_id='<id@example.test>', body='Friday works.')
    start = time.monotonic()
    try:
        result = MailLaunder(store, enabled=True).process_next()
        elapsed = time.monotonic() - start
        assert len(roots) == 1
        assert result['state'] == 'blocked' and result['reason'] == 'extraction_failed'
        assert store.claim_for_laundering() is None
        assert elapsed < 3.2
        assert roots[0].poll() is not None
        assert not psutil.pid_exists(roots[0].pid)
        assert not marker.exists() and not child_file.exists()
        assert all(s is None or s.closed for s in (roots[0].stdin, roots[0].stdout, roots[0].stderr))
        print('SUSPENDED_FAILURE', dict(failure=failure, elapsed=elapsed,
            root_exit=roots[0].returncode, native_errors=native_errors, result=result,
            executed=False, root_alive=False, child_spawned=False))
    finally:
        for root in roots:
            if root.poll() is None:
                root.kill()
                root.wait(timeout=3)
        if child_file.exists():
            identity = json.loads(child_file.read_text())
            try:
                child = psutil.Process(identity['pid'])
                if child.create_time() == identity['created']:
                    child.kill()
                    child.wait(timeout=3)
            except psutil.NoSuchProcess:
                pass
