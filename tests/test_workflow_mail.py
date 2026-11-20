"""Workflow emails bind replies to a completed agent; no real mail is sent."""
import subprocess
import sys
from types import SimpleNamespace

import pytest

from mc import workflows
from mc.workflow_mail import mail_context_args


def _step(status='completed', project_id='project-1', session_id='session-1'):
    return {'status': status, 'project_id': project_id, 'session_id': session_id}


def test_context_uses_latest_completed_agent_across_non_agent_steps():
    run = {'steps': {
        'older': _step(),
        'latest': _step(project_id='apex_trader', session_id='session-2'),
        'wait': {'status': 'completed'},
        'email': {'status': 'pending'},
    }}
    assert mail_context_args(run) == [
        '--project', 'apex_trader', '--session', 'session-2']


@pytest.mark.parametrize('status', ['pending', 'running', 'waiting', 'failed',
                                   'skipped', 'cancelled', 'interrupted'])
def test_context_ignores_non_completed_agent(status):
    run = {'steps': {'older': _step(), 'newer': _step(status, session_id='wrong')}}
    assert mail_context_args(run) == ['--project', 'project-1', '--session', 'session-1']


@pytest.mark.parametrize('missing', ['project_id', 'session_id'])
@pytest.mark.parametrize('value', [None, ''])
def test_context_ignores_incomplete_identity(missing, value):
    newer = _step(session_id='wrong')
    newer[missing] = value
    run = {'steps': {'older': _step(), 'newer': newer}}
    assert mail_context_args(run) == ['--project', 'project-1', '--session', 'session-1']


@pytest.mark.parametrize('run', [{}, {'steps': {}}, {'steps': None},
                                 {'steps': {'action': {'status': 'completed'}}}])
def test_context_without_completed_agent_has_no_flags(run):
    assert mail_context_args(run) == []


@pytest.fixture
def mailer(monkeypatch):
    calls = []
    result = SimpleNamespace(returncode=0, stdout='', stderr='', bodies=[], body_paths=[])

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        # The body file only exists for the length of the call: read it here.
        if '--body-file' in cmd:
            path = cmd[cmd.index('--body-file') + 1]
            result.body_paths.append(path)
            with open(path, encoding='utf-8', newline='') as f:
                result.bodies.append(f.read())
        return result

    # Only opt into the mail call site AFTER replacing subprocess.run.
    monkeypatch.setattr(subprocess, 'run', fake_run)
    monkeypatch.setenv('MC_LIVE_MAIL_TESTS', '1')
    return calls, result


@pytest.mark.parametrize('via_agent_mail', [True, False])
def test_notify_operator_passes_context_and_reports_actual_transport(mailer, via_agent_mail):
    calls, result = mailer
    result.stdout = '[send_mail] sent via agent-mail, intent test' if via_agent_mail else 'sent'
    result.stderr = '' if via_agent_mail else 'agent-mail unavailable; sending via Gmail'
    run = {'id': 'run-test', 'steps': {'agent': _step()}}
    node = {'name': 'email', 'action': 'notify_operator', 'config': {
        'message': 'result', 'to': 'untrusted', 'cc': 'untrusted', 'recipient': 'untrusted'}}
    transport = 'agent-mail' if via_agent_mail else 'Gmail'
    assert workflows._execute_action(run, node, {'name': 'test'}) == (
        True, f'sent via {transport}', None)
    cmd, kwargs = calls[0]
    assert cmd[-4:] == ['--project', 'project-1', '--session', 'session-1']
    assert '--body' not in cmd and result.bodies == ['result']
    assert cmd[cmd.index('--subject') + 1] == '[Clayrune workflow] test: email'
    assert not {'--to', '--cc', '--recipient'} & set(cmd)
    assert kwargs == dict(capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=60)


def test_sender_without_context_preserves_original_command(mailer):
    calls, result = mailer
    assert workflows._send_operator_notification('subject', 'body') == (True, 'sent via Gmail')
    cmd, _ = calls[0]
    assert cmd == [sys.executable, str(workflows.Path(workflows.__file__).resolve().parent.parent
                                    / 'tools/night-review/send_mail.py'),
                   '--subject', 'subject', '--body-file', result.body_paths[0]]
    assert result.bodies == ['body']


@pytest.mark.parametrize('body', ['x' * 40_000, '-starts-with-a-dash', 'line one\r\nline two\n'],
                         ids=['long', 'leading-dash', 'crlf'])
def test_body_travels_in_a_file_not_on_the_command_line(mailer, body):
    """DCW-W4: a 33,000-character argument fails process creation on Windows and
    a body starting with '-' is refused by argparse, so the body is never argv."""
    calls, result = mailer
    assert workflows._send_operator_notification('subject', body) == (True, 'sent via Gmail')
    cmd, _ = calls[0]
    assert '--body' not in cmd and body not in cmd
    assert max(len(arg) for arg in cmd) < 1000
    assert result.bodies == [body]
    assert not workflows.os.path.exists(result.body_paths[0])  # removed after the send


@pytest.mark.parametrize('with_context', [True, False])
@pytest.mark.parametrize('via_agent_mail', [True, False])
def test_approval_uses_same_context_and_preserves_human_decision(
        mailer, monkeypatch, with_context, via_agent_mail):
    calls, result = mailer
    result.stdout = '[send_mail] sent via agent-mail, intent test' if via_agent_mail else 'sent'
    logs = []
    monkeypatch.setattr(workflows, '_log', logs.append)
    run = {'id': 'run-gate', 'status': 'waiting',
           'steps': {'agent': _step()} if with_context else {}}
    workflows._notify_approval_waiting(
        run, {'name': 'gated'}, {'name': 'gate', 'options': ['go', 'stop']})
    cmd, _ = calls[0]
    assert ('--project' in cmd) == with_context
    if with_context:
        assert cmd[-4:] == ['--project', 'project-1', '--session', 'session-1']
    assert not {'--to', '--cc', '--recipient'} & set(cmd)
    body = result.bodies[0]
    assert 'POST /api/workflow-runs/run-gate/decision' in body
    assert 'Options: go, stop' in body
    assert run['status'] == 'waiting'
    assert f'sent via {"agent-mail" if via_agent_mail else "Gmail"}' in logs[-1]


@pytest.mark.parametrize('approval', [True, False])
def test_pytest_guard_still_blocks_mail(mailer, monkeypatch, approval):
    calls, _ = mailer
    monkeypatch.delenv('MC_LIVE_MAIL_TESTS')
    if approval:
        workflows._notify_approval_waiting({'id': 'run-guard'}, {}, {})
    else:
        assert workflows._send_operator_notification('subject', 'body') == (
            True, 'suppressed under pytest')
    assert calls == []


@pytest.mark.parametrize('approval', [True, False])
def test_nonzero_exit_is_reported_even_with_agent_mail_marker(mailer, monkeypatch, approval):
    _, result = mailer
    result.returncode = 4
    result.stdout = '[send_mail] sent via agent-mail'
    result.stderr = 'mail paused'
    if approval:
        logs = []
        monkeypatch.setattr(workflows, '_log', logs.append)
        workflows._notify_approval_waiting({'id': 'run-fail'}, {}, {})
        assert 'failed: send_mail exited 4: mail paused' in logs[-1]
    else:
        assert workflows._send_operator_notification('subject', 'body') == (
            False, 'send_mail exited 4: mail paused')


def test_subprocess_timeout_remains_a_send_failure(mailer, monkeypatch):
    paths = []

    def timeout(cmd, **kwargs):
        paths.append(cmd[cmd.index('--body-file') + 1])
        raise subprocess.TimeoutExpired('send_mail.py', 60)

    monkeypatch.setattr(subprocess, 'run', timeout)
    ok, info = workflows._send_operator_notification('subject', 'body')
    assert not ok
    assert 'timed out' in info
    assert not workflows.os.path.exists(paths[0])  # the body file never outlives the send
