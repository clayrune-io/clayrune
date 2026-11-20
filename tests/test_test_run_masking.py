"""MC-1171: a test run whose exit status is hidden from Clayrune is reported.

Three layers: the `classify()` table, the status line through each engine's
real stream reader, and the status line through the real job route.
"""
from __future__ import annotations

import importlib
import io
import json
from pathlib import Path
import sys
import time
from typing import Any, cast

import pytest
from flask import Flask

from mc import reply_line_kind as rlk
from mc import state, test_run_detection as detection, test_run_masking as masking
from mc.core import TimestampedLines

M, C, U, N = masking.MASKED, masking.CLEAN, masking.UNKNOWN, masking.NOT_TEST
LAUNCHER = 'python tools/test-run.py --project p --port 5199 -- python -m pytest tests'

# (shell, command, state, reason)
TABLE = [
    # M1 piped
    ('bash', 'pytest tests | tail -15', M, 'piped'),
    ('bash', 'python -m pytest tests 2>&1 | tail -15', M, 'piped'),
    ('bash', '.venv/Scripts/python.exe -m pytest -p no:cacheprovider tests | tee out.log', M, 'piped'),
    ('bash', 'pytest tests |\n tail -3', M, 'piped'),
    ('bash', 'timeout 600 python -m pytest tests | tee out.log', M, 'piped'),
    ('bash', 'PYTHONPATH=. pytest tests | head', M, 'piped'),
    ('bash', 'npm test | cat', M, 'piped'),
    ('bash', 'npm run test | tail -20', M, 'piped'),
    ('bash', 'node --test | tail', M, 'piped'),
    ('bash', 'npx playwright test | tee pw.log', M, 'piped'),
    ('bash', "pytest -k 'a|b' | tail -3", M, 'piped'),
    ('bash', 'cd repo && pytest tests | tail -5', M, 'piped'),
    ('bash', 'for f in a b; do pytest $f | tail -1; done', M, 'piped'),
    ('bash', 'out=$(pytest | tail -1)', M, 'piped'),
    # pipefail in effect
    ('bash', 'set -o pipefail; pytest tests | tail -15', C, ''),
    ('bash', 'set -euo pipefail\npytest tests | tail -15', C, ''),
    ('bash', 'bash -o pipefail -c "pytest tests | tail -15"', C, ''),
    ('bash', 'set -o pipefail; set +o pipefail; pytest tests | tail', M, 'piped'),
    ('bash', 'bash -c "pytest tests | tail -15"', M, 'piped'),
    # M2 followed
    ('bash', 'pytest tests; true', M, 'followed'),
    ('bash', 'pytest tests; echo done', M, 'followed'),
    ('bash', 'npx playwright test; true', M, 'followed'),
    ('bash', 'pytest tests > out.log 2>&1; tail -5 out.log', M, 'followed'),
    ('bash', 'pytest tests\necho finished', M, 'followed'),
    ('bash', 'set -e; pytest && echo ok; echo done', M, 'followed'),
    # M3 or_fallback
    ('bash', 'pytest tests || true', M, 'or_fallback'),
    ('bash', 'pytest tests || echo failed', M, 'or_fallback'),
    ('bash', 'python -m pytest tests || :', M, 'or_fallback'),
    # M4 script the detector cannot read
    ('bash', 'bash run_tests.sh', M, 'script_unreadable'),
    ('bash', './tools/smoke.sh', M, 'script_unreadable'),
    ('bash', 'bash build.sh', U, 'opaque_script'),
    # C1 && passes a failing status through
    ('bash', 'a && pytest', C, ''),
    ('bash', 'cd x && pytest', C, ''),
    ('bash', 'pytest && echo ok', C, ''),
    # C2 redirection as the last command
    ('bash', 'pytest > file 2>&1', C, ''),
    ('bash', 'python -m pytest tests &> out.log', C, ''),
    # C3 the launcher
    ('bash', LAUNCHER, C, ''),
    ('bash', LAUNCHER + ' | tail -5', C, ''),
    ('bash', LAUNCHER + '; echo done', C, ''),
    # C4 the failing status still ends the command
    ('bash', 'pytest || exit 1', C, ''),
    ('bash', 'pytest tests; exit $?', C, ''),
    ('bash', 'set -e; pytest; echo done', C, ''),
    # C5 a quoted pipe is an argument
    ('bash', 'pytest -k "a|b"', C, ''),
    ('bash', "pytest -k 'a|b or c;d'", C, ''),
    ('bash', 'pytest tests/', C, ''),
    # here-documents
    ('bash', "bash <<'EOF'\npytest tests | tail -3\nEOF", M, 'piped'),
    ('bash', "bash <<'EOF'\nset -o pipefail\npytest tests | tail -3\nEOF", C, ''),
    ('bash', "python - <<'PY'\nprint('pytest | tail; x')\nPY", N, ''),
    ('bash', "cat <<EOF > notes.txt\npytest tests | tail\nEOF", N, ''),
    # U: handled by hand, control flow, background, substitution
    ('bash', 'pytest tests; rc=$?; tail -5 log; exit $rc', U, 'exit_status_handled'),
    ('bash', 'pytest tests | tee out.log; exit ${PIPESTATUS[0]}', U, 'exit_status_handled'),
    ('bash', 'if pytest; then echo ok; fi', U, 'complex'),
    ('bash', '(cd x && pytest)', U, 'complex'),
    ('bash', 'pytest &', U, 'backgrounded'),
    ('bash', 'echo "$(pytest)"', U, 'substitution'),
    ('bash', 'pytest -k "unterminated', U, 'unparsed'),
    ('bash', 'pytest ' + 'x' * (detection.MAX_COMMAND + 1), U, 'too_long'),
    # not test runs
    ('bash', 'echo "pytest | tail"', N, ''),
    ('bash', 'git log | head', N, ''),
    ('bash', 'pip install pytest; echo ok', N, ''),
    ('bash', 'rg pytest tests | wc -l', N, ''),
    ('bash', 'ls -la', N, ''),
    ('bash', '', N, ''),
    # a shell started from another shell
    ('bash', 'cmd //c "pytest | more"', M, 'piped'),
    ('bash', "sh -c 'pytest tests; true'", M, 'followed'),
    # PowerShell
    ('powershell', 'pytest tests | Select-Object -Last 15', M, 'piped'),
    ('powershell', 'python -m pytest tests 2>&1 | Out-File out.log', M, 'piped'),
    ('powershell', 'pytest; Write-Host done', M, 'followed'),
    ('powershell', 'python -m pytest tests; exit $LASTEXITCODE', C, ''),
    ('powershell', '& .venv\\Scripts\\python.exe -m pytest tests', C, ''),
    ('powershell', 'python -m pytest tests *> out.log', C, ''),
    ('powershell', 'cd x; pytest', C, ''),
    ('powershell', 'pytest -k "a|b"', C, ''),
    ('powershell', 'pytest; $code = $LASTEXITCODE; Write-Host done; exit $code', U,
     'exit_status_handled'),
    ('powershell', 'if ($x) { pytest }', U, 'complex'),
    ('powershell', 'pytest @"\nx\n"@', U, 'unparsed'),
    ('powershell', 'Get-ChildItem | Select-Object -First 3', N, ''),
    # cmd
    ('cmd', 'pytest | more', M, 'piped'),
    ('cmd', 'pytest & echo done', M, 'followed'),
    ('cmd', 'pytest || echo failed', M, 'or_fallback'),
    ('cmd', 'cd tests && pytest', C, ''),
    ('cmd', 'pytest || exit /b 1', C, ''),
    ('cmd', 'pytest > out.txt 2>&1', C, ''),
    ('cmd', 'pytest & exit /b %errorlevel%', C, ''),
    ('cmd', 'pytest -k "a|b"', C, ''),
    ('cmd', 'if exist x (pytest)', U, 'complex'),
    ('cmd', 'pytest & if errorlevel 1 echo failed', U, 'complex'),
    ('cmd', 'dir | findstr py', N, ''),
    # shell not known: an answer only when all three shells agree
    (None, 'pytest tests/', C, ''),
    (None, 'pytest tests | tail -15', M, 'piped'),
    (None, 'cd x && pytest', C, ''),
    (None, 'pytest; true', U, 'shell_unknown'),
    (None, "/bin/bash -lc 'pytest tests | tail -20'", U, 'shell_unknown'),
    (None, 'powershell.exe -Command "pytest tests | Select-Object -Last 5"', M, 'piped'),
    (None, 'git status', N, ''),
]


def test_the_table_has_at_least_forty_rows():
    assert len(TABLE) >= 40
    assert {row[0] for row in TABLE} == {'bash', 'powershell', 'cmd', None}


@pytest.mark.parametrize('shell,command,expected,reason', TABLE,
                         ids=[f'{row[0]}:{row[1][:48]!r}' for row in TABLE])
def test_classify_table(shell, command, expected, reason):
    verdict = masking.classify(command, shell)
    assert (verdict.state, verdict.reason) == (expected, reason)
    assert verdict.masked is (expected == M)
    line = masking.status_line(verdict)
    if expected == M:
        assert line.startswith(masking.LABEL + ' UNVERIFIED: ')
        assert 'tools/test-run.py' in line and '\n' not in line
        assert rlk.is_status_line(line)
    else:
        assert line == ''   # unknown is never reported, and never as clean


def test_unknown_is_not_a_test_run_answer_either_way():
    assert masking.classify('if pytest; then echo ok; fi', 'bash').is_test_run is None
    assert masking.classify('pytest', 'bash').is_test_run is True
    assert masking.classify('ls', 'bash').is_test_run is False


def test_recognisers_are_the_detection_module_s_own(monkeypatch):
    """A runner is whatever `candidate()` says it is: nothing is copied."""
    monkeypatch.setattr(masking, 'candidate', lambda name, command: command[:1] == ['mytest'])
    assert masking.classify('mytest | tail', 'bash').state == M
    assert masking.classify('pytest | tail', 'bash').state == N


def test_the_line_names_the_other_command_and_never_the_arguments():
    line = masking.status_line(masking.classify(
        'pytest tests -k secret_token | tail -15 --secret-flag', 'bash'))
    assert 'piped into tail' in line
    assert 'secret' not in line and '-15' not in line


# ── scripts ─────────────────────────────────────────────────────────────────

def test_a_readable_script_is_judged_by_its_contents():
    scripts = {'run_tests.sh': 'cd repo\npytest tests | tail -15\n',
               'ok.sh': 'set -euo pipefail\npytest tests | tail -15\n',
               'build.sh': 'make all\n',
               'last.sh': '#!/bin/bash\npytest tests\n'}
    def read(path):
        return scripts.get(path.removeprefix('./'))
    verdict = masking.classify('bash run_tests.sh', 'bash', read_script=read)
    assert (verdict.state, verdict.reason, verdict.script) == (M, 'piped', 'run_tests.sh')
    assert ' in run_tests.sh' in masking.status_line(verdict)
    assert masking.classify('bash ok.sh', 'bash', read_script=read).state == C
    assert masking.classify('bash build.sh', 'bash', read_script=read).state == N
    assert masking.classify('./last.sh', 'bash', read_script=read).state == C
    assert masking.classify('bash last.sh; echo done', 'bash', read_script=read).state == M


def test_caller_pipefail_covers_the_top_level_only():
    """The job route runs bash under `-o pipefail`; a script it starts with a
    plain `bash x.sh` does not inherit that."""
    read = {'run_tests.sh': 'pytest tests | tail -15\n'}.get
    assert masking.classify('pytest tests | tail -15', 'bash', pipefail=True).state == C
    assert masking.classify('bash run_tests.sh', 'bash', read_script=read,
                            pipefail=True).state == M
    assert masking.classify('source run_tests.sh', 'bash', read_script=read,
                            pipefail=True).state == C


def test_file_reader_reads_only_a_plain_bounded_local_file(tmp_path, monkeypatch):
    (tmp_path / 'run_tests.sh').write_text('pytest tests; true\n', encoding='utf-8')
    read = masking.file_reader(str(tmp_path))
    assert read('run_tests.sh') == 'pytest tests; true\n'
    assert read('missing.sh') is None and read('$HOME/x.sh') is None
    assert read('//server/share/x.sh') is None and read('') is None
    assert masking.file_reader(None)('run_tests.sh') is None
    monkeypatch.setattr(masking, 'MAX_SCRIPT', 4)
    assert read('run_tests.sh') is None
    verdict = masking.classify('bash run_tests.sh', 'bash', read_script=read)
    assert (verdict.state, verdict.reason) == (M, 'script_unreadable')


def test_a_script_that_calls_itself_stops_at_the_depth_limit():
    read = {'loop.sh': 'bash loop.sh\n'}.get
    verdict = masking.classify('bash loop.sh', 'bash', read_script=read)
    assert (verdict.state, verdict.reason) == (U, 'nested_too_deep')


def test_a_reader_that_raises_is_an_unreadable_script():
    def broken(path):
        raise OSError('private')
    assert masking.classify('bash build.sh', 'bash', read_script=broken).state == U


# ── tool call surfacing ─────────────────────────────────────────────────────

PIPED = 'python -m pytest tests | tail -15'


def use(call='call-1', command=PIPED, name='Bash'):
    return {'type': 'tool_use', 'id': call, 'name': name, 'input': {'command': command}}


def envelope(*blocks, kind='assistant'):
    return {'type': kind, 'message': {'content': list(blocks)}}


class FakeProc:
    def __init__(self, records):
        self.stdout = io.StringIO('\n'.join(json.dumps(r) for r in records) + '\n')
        self.pid = -1

    def wait(self):
        return 0

    def poll(self):
        return 0


def _records(reader, command):
    if reader.startswith('claude') or reader == 'qwen':
        name = 'Bash' if reader.startswith('claude') else 'run_shell_command'
        return [envelope({'type': 'text', 'text': 'Running the tests.'}, use(command=command, name=name)),
                envelope({'type': 'tool_result', 'tool_use_id': 'call-1', 'content': 'ok'}, kind='user'),
                {'type': 'result', 'session_id': 'vendor', 'num_turns': 1}]
    if reader == 'codex':
        item = {'type': 'command_execution', 'id': 'i', 'command': command}
        return [{'type': 'item.started', 'item': item},
                {'type': 'item.completed', 'item': {**item, 'exit_code': 0}},
                {'type': 'turn.completed', 'usage': {}}]
    return [{'type': 'tool_use', 'tool_id': 'g', 'tool_name': 'run_shell_command',
             'parameters': {'command': command}},
            {'type': 'tool_result', 'tool_id': 'g', 'exit_code': 0},
            {'type': 'result', 'status': 'success'}]


def _run_reader(monkeypatch, tmp_path, reader, command):
    """Feed one shell call through the engine's real stream reader."""
    from mc import agent_runtime as runtime
    server = importlib.import_module('server')
    monkeypatch.setitem(state.CONFIG, detection.FLAG, False)
    proc = FakeProc(_records(reader, command))
    session: dict[str, Any] = {
        'project_id': 'p', 'session_id': 's', 'proc': proc, 'log_lines': TimestampedLines(),
        'status': 'running', 'provider': reader.split('-')[0], '_agent_cwd': str(tmp_path),
        'last_output_time': 0.0, 'last_status_change_time': 0.0}
    monkeypatch.setitem(state.agent_sessions, 's', session)
    if reader.startswith('claude'):
        getattr(server, '_read_agent_stream_b' if reader.endswith('b')
                else '_read_agent_stream')(proc, session)
    else:
        handle = runtime.SessionHandle(mc_session_id='s', provider=reader, mode='A',
                                       project_path=str(tmp_path), project_id='p',
                                       session_dict=session, meta={})
        if reader == 'gemini':
            runtime.GeminiRuntime()._read_stream(cast(Any, proc), handle)
        else:
            engine = runtime.CodexRuntime() if reader == 'codex' else runtime.QwenRuntime()
            runtime._mode_a_reader(cast(Any, proc), handle, engine)
    return [str(line) for line in session['log_lines']]


def _verdict_lines(lines):
    return [line for line in lines if line.startswith(masking.LABEL)]


READERS = ['claude-a', 'claude-b', 'codex', 'qwen', 'gemini']


@pytest.mark.parametrize('reader', READERS)
def test_masked_shell_call_adds_one_status_line_after_its_tool_line(monkeypatch, tmp_path, reader):
    monkeypatch.setitem(state.CONFIG, masking.SETTING, True)
    lines = _run_reader(monkeypatch, tmp_path, reader, PIPED)
    found = _verdict_lines(lines)
    assert len(found) == 1, lines          # Codex surfaces the call twice: still one
    assert found[0] == masking.status_line(masking.classify(PIPED, 'bash'))
    assert 'UNVERIFIED' in found[0] and 'piped into tail' in found[0]
    assert rlk.is_status_line(found[0].strip())
    assert lines[lines.index(found[0]) - 1].startswith('[tool: ')
    assert not any('[stream error:' in line for line in lines)


@pytest.mark.parametrize('reader', READERS)
def test_setting_off_means_no_line(monkeypatch, tmp_path, reader):
    monkeypatch.setitem(state.CONFIG, masking.SETTING, False)
    lines = _run_reader(monkeypatch, tmp_path, reader, PIPED)
    assert _verdict_lines(lines) == []
    assert any(line.startswith('[tool: ') for line in lines)


@pytest.mark.parametrize('reader', READERS)
@pytest.mark.parametrize('command', ['python -m pytest tests', 'git log | head',
                                     'if pytest; then echo ok; fi'])
def test_clean_unknown_and_non_test_calls_add_no_line(monkeypatch, tmp_path, reader, command):
    monkeypatch.setitem(state.CONFIG, masking.SETTING, True)
    assert _verdict_lines(_run_reader(monkeypatch, tmp_path, reader, command)) == []


def test_the_setting_defaults_on_and_is_editable():
    server = importlib.import_module('server')
    from mc.blueprints import settings_routes
    assert server._load_config()[masking.SETTING] is True
    assert masking.SETTING in settings_routes._CONFIG_EDITABLE_KEYS
    floor = (Path(__file__).resolve().parents[1] / 'static' / 'js' / 'floor-preferences.js')
    assert f"key: '{masking.SETTING}'" in floor.read_text(encoding='utf-8')


def test_the_reply_scan_skips_the_line(monkeypatch, tmp_path):
    from mc import agent_runtime as runtime
    from mc.blueprints import agent_routes as ar
    monkeypatch.setitem(state.CONFIG, masking.SETTING, True)
    session: dict[str, Any] = {'log_lines': TimestampedLines(['The answer.']),
                               '_agent_cwd': str(tmp_path)}
    masking.note_shell_call(session, 'Bash', {'command': PIPED}, 'c1')
    assert len(session['log_lines']) == 2
    assert runtime._collect_trailing_reply_text(list(session['log_lines'])) == 'The answer.'
    assert ar._last_reply_text(session) == 'The answer.'


def test_tool_call_shapes(tmp_path):
    cwd = str(tmp_path)
    line = masking.tool_call_line
    assert line('PowerShell', {'command': 'pytest tests | Select-Object -Last 5'}, cwd)
    assert line('exec_command', {'cmd': 'pytest tests | tail -5'}, cwd)
    assert line('shell', {'command': ['bash', '-lc', 'pytest tests | tail -5']}, cwd)
    assert line('exec_command', json.dumps({'cmd': 'pytest tests | tail -5'}), cwd)
    assert line('shell', {'command': ['pytest', '-k', 'a|b']}, cwd) == ''
    assert line('Read', {'command': PIPED}, cwd) == ''
    assert line('Bash', {'command': None}, cwd) == ''
    assert line('Bash', 'not json', cwd) == ''
    assert line(['Bash'], {'command': PIPED}, cwd) == ''
    (tmp_path / 'run_tests.sh').write_text('pytest tests | tail -15\n', encoding='utf-8')
    assert ' in run_tests.sh' in line('Bash', {'command': 'bash run_tests.sh'}, cwd)


def test_note_shell_call_never_raises_and_never_logs_the_command(monkeypatch):
    logs = []
    monkeypatch.setattr(masking, '_log', lambda msg, **_: logs.append(msg))
    monkeypatch.setitem(state.CONFIG, masking.SETTING, True)
    def broken(*args, **kwargs):
        raise OSError('secret command text')
    monkeypatch.setattr(masking, 'tool_call_line', broken)
    session: dict[str, Any] = {'log_lines': []}
    masking.note_shell_call(session, 'Bash', {'command': PIPED})
    assert session['log_lines'] == []
    assert logs and not any('secret' in msg for msg in logs)


# ── the job route ───────────────────────────────────────────────────────────

def _wait_for(predicate, timeout=20.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture()
def job_client(tmp_path, monkeypatch):
    from mc.blueprints import agent_routes as ar
    from mc.delegation_delivery import DeliveryStore
    app = Flask('masked-verdict-job-test')
    app.register_blueprint(ar.bp)
    project_path = tmp_path / 'proj'
    project_path.mkdir()
    project = {'id': 'p1', 'project_path': str(project_path), 'provider': 'codex'}
    monkeypatch.setattr(ar, 'load_project', lambda pid: project if pid == 'p1' else None)
    monkeypatch.setattr(ar, 'DATA_DIR', tmp_path / 'data' / 'projects')
    monkeypatch.setattr(ar, '_POPEN_FLAGS', 0)
    monkeypatch.setattr(ar, '_STARTUPINFO', None)
    monkeypatch.setattr(ar, '_persist_pid_ledger', lambda: None)
    monkeypatch.setattr(ar, '_proc_identity', lambda pid: ('test.exe', 0.0))
    monkeypatch.setattr(ar, '_delivery_store', DeliveryStore(tmp_path / 'delivery.sqlite3'))
    session = {'project_id': 'p1', 'session_id': 's1', 'status': 'idle', 'provider': 'codex',
               'log_lines': TimestampedLines()}
    monkeypatch.setitem(ar.agent_sessions, 's1', session)
    yield app.test_client(), session, project_path, ar


def _finish_job(job_client, command, shell=None):
    """Start `command` through the real route; return (job, callback text)."""
    client, _session, _path, ar = job_client
    body = {'command': command, **({'shell': shell} if shell else {})}
    resp = client.post('/api/project/p1/agent/s1/job', json=body)
    assert resp.status_code == 200, resp.get_json()
    job_id = resp.get_json()['job_id']
    assert _wait_for(lambda: ar._delivery_store.status('outbox', f'job:{job_id}', 'p1') is not None)
    row = ar._delivery_store.status('outbox', f'job:{job_id}', 'p1')
    return ar._agent_jobs.get_job(job_id), json.loads(row['payload'])['payload']['message']


def _fake_pytest(path):
    """A `pytest` on PATH for the job's shell that fails with exit 3."""
    script = path / 'pytest'
    script.write_text('#!/bin/sh\necho "1 failed"\nexit 3\n', encoding='utf-8', newline='\n')
    script.chmod(0o755)


def test_job_ending_in_true_reports_unverified_and_keeps_its_exit_code(job_client, monkeypatch):
    _client, session, path, _ar = job_client
    monkeypatch.setitem(state.CONFIG, masking.SETTING, True)
    _fake_pytest(path)
    command = 'PATH="$PWD:$PATH"; pytest tests; true'
    job, message = _finish_job(job_client, command)
    if job['shell'] != 'bash':
        pytest.skip('no bash on this host')
    assert job['exit_code'] == 0             # the masked code is reported as it is
    expected = masking.status_line(masking.classify(command, 'bash'))
    assert expected and 'true runs after the test command' in expected
    assert [str(line) for line in session['log_lines']] == [expected]
    assert rlk.is_status_line(expected)
    assert expected in message
    assert '(exit code 0)' in message and f'Command: {command}' in message
    assert message.index(expected) < message.index('Command: ')


def test_job_pipe_inside_a_script_is_reported_and_a_top_level_pipe_is_not(job_client, monkeypatch):
    _client, session, path, _ar = job_client
    monkeypatch.setitem(state.CONFIG, masking.SETTING, True)
    _fake_pytest(path)
    (path / 'run_tests.sh').write_text('PATH="$PWD:$PATH"\npytest tests | tail -15\n',
                                       encoding='utf-8', newline='\n')
    job, message = _finish_job(job_client, 'bash run_tests.sh')
    if job['shell'] != 'bash':
        pytest.skip('no bash on this host')
    assert job['exit_code'] == 0             # tail's exit, which is the point
    lines = [str(line) for line in session['log_lines']]
    assert len(lines) == 1 and 'piped into tail' in lines[0] and ' in run_tests.sh' in lines[0]
    assert lines[0] in message
    # The route itself runs bash under -o pipefail: the real exit arrives.
    job, message = _finish_job(job_client, 'PATH="$PWD:$PATH" pytest tests | tail -15')
    assert job['exit_code'] == 3 and '(exit code 3)' in message
    assert len(session['log_lines']) == 1
    assert masking.LABEL not in message


def test_job_setting_off_means_no_line_and_no_sentence(job_client, monkeypatch):
    _client, session, path, _ar = job_client
    monkeypatch.setitem(state.CONFIG, masking.SETTING, False)
    _fake_pytest(path)
    job, message = _finish_job(job_client, 'PATH="$PWD:$PATH"; pytest tests; true')
    if job['shell'] != 'bash':
        pytest.skip('no bash on this host')
    assert list(session['log_lines']) == []
    assert masking.LABEL not in message and 'UNVERIFIED' not in message


def test_clean_and_non_test_jobs_add_nothing(job_client, monkeypatch):
    _client, session, _path, _ar = job_client
    monkeypatch.setitem(state.CONFIG, masking.SETTING, True)
    code = "print('hi')"
    _job, message = _finish_job(job_client, f'"{sys.executable}" -c "{code}"; echo done')
    assert list(session['log_lines']) == []
    assert masking.LABEL not in message


def test_job_line_shapes(tmp_path):
    base = {'cwd': str(tmp_path), 'command': 'pytest tests | Select-Object -Last 5'}
    assert 'piped into select-object' in masking.job_line({**base, 'shell': 'powershell'})
    assert masking.job_line({**base, 'shell': 'argv'}) == ''       # the launcher's own jobs
    assert masking.job_line({'shell': 'cmd', 'command': 'pytest | more', 'cwd': None})
    assert masking.job_line({'shell': 'sh', 'command': 'pytest | tail'})   # sh has no pipefail
    assert masking.job_line({'shell': 'bash', 'command': 'pytest | tail'}) == ''
    assert masking.job_line({}) == ''


def test_a_job_session_without_a_log_still_gets_its_callback(job_client, monkeypatch):
    _client, session, path, _ar = job_client
    monkeypatch.setitem(state.CONFIG, masking.SETTING, True)
    _fake_pytest(path)
    session.pop('log_lines')
    job, message = _finish_job(job_client, 'PATH="$PWD:$PATH"; pytest tests; true')
    if job['shell'] != 'bash':
        pytest.skip('no bash on this host')
    assert 'UNVERIFIED' in message
