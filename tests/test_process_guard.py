import json

import pytest

from mc.process_guard import guard_command, hook_main


def test_all_image_name_termination_forms_are_blocked():
    commands = [
        'taskkill /F /IM notepad.exe',
        'Stop-Process -Name notepad',
        'Get-Process notepad | Stop-Process',
        'pkill notepad',
        'killall notepad',
        'wmic process where name="notepad.exe" delete',
    ]
    assert all(guard_command(command, set()) for command in commands)


def test_unrelated_pid_is_allowed_but_clayrune_listener_is_blocked():
    assert guard_command('taskkill /PID 1234', {'5199'}) is None
    reason = guard_command('taskkill /PID 4321', {'4321'})
    assert reason and 'Clayrune listener' in reason


def test_hook_uses_exit_two_and_ignores_non_shell_tools(capsys):
    payload = {'tool_name': 'Bash', 'tool_input': {'command': 'pkill notepad'}}
    assert hook_main(payload) == 2
    assert 'image-name termination' in capsys.readouterr().err
    assert hook_main({'tool_name': 'Read', 'tool_input': {'command': 'pkill notepad'}}) == 0


def test_hook_covers_gemini_and_qwen_shell_tool_name(capsys):
    # Gemini CLI's BeforeTool and Qwen Code's own native shell tool both name
    # it 'run_shell_command' (Claude/Qwen's PreToolUse names it 'Bash' /
    # 'PowerShell' instead) — this is the SAME entry point vendor hook
    # installers point Gemini's and Qwen's own hook config at (W2), so it
    # must recognize both namings.
    payload = {'tool_name': 'run_shell_command',
               'tool_input': {'command': 'taskkill /IM notepad.exe'}}
    assert hook_main(payload) == 2
    assert 'image-name termination' in capsys.readouterr().err



def test_listener_scan_spawns_netstat_without_a_console_window(monkeypatch):
    import subprocess
    from mc import process_guard
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return subprocess.CompletedProcess(
            argv, 0, stdout='  TCP    0.0.0.0:5199    0.0.0.0:0    LISTENING    4242\n')

    monkeypatch.setattr(process_guard.subprocess, 'run', fake_run)
    assert process_guard.clayrune_listener_pids() == {'4242'}
    assert seen['argv'] == ['netstat', '-ano']
    assert seen['creationflags'] == getattr(subprocess, 'CREATE_NO_WINDOW', 0)


# ---- MC-1190 (review pass 10, AP1 + AP3) ---------------------------------
LISTENER = '4242'
OTHER = '1234'

# AP1: image-name kills that used to pass (measured in the report).
IMAGE_SPELLINGS_THAT_USED_TO_PASS = [
    'taskkill /F /FI "IMAGENAME eq notepad.exe"',
    'Stop-Process -ProcessName notepad',
    'spps -Name notepad',
    'Get-Process notepad | kill',
    'gps notepad | spps',
    '(Get-Process notepad).Kill()',
    'Get-CimInstance Win32_Process -Filter "Name=\'notepad.exe\'" | Invoke-CimMethod -MethodName Terminate',
    # beyond the report's seven: the same families, other spellings
    'Get-WmiObject Win32_Process -Filter "name=\'notepad.exe\'" | ForEach-Object { $_.Terminate() }',
    'taskkill -im notepad.exe',
    'Stop-Process -Name:notepad',
    'Get-Process notepad | ForEach-Object { $_.Kill() }',
]

# AP1: listener-PID kills that used to pass (the report's seven, minus the
# indirect one, which has its own list below). One template per spelling.
LISTENER_SPELLINGS = [
    'kill -TERM {pid}',
    'kill 111 {pid}',
    'kill -9 111 {pid}',
    'taskkill /PID 111 /F /PID {pid}',
    'Stop-Process {pid}',
    'Stop-Process -Id 111,{pid}',
    'taskkill /F /FI "PID eq {pid}"',
    'spps -Id 111,{pid} -Force',
    'kill -s TERM {pid}',
]

# Indirect forms: the PIDs cannot be read from the text, so the guard refuses
# when the thing that produces them is a lookup by port or by name.
INDIRECT_REFUSED = [
    'kill $(lsof -ti :5199)',
    'kill -9 $(lsof -ti:5199)',
    'kill `lsof -ti :5199`',
    'kill $(pgrep -f server.py)',
    'kill -9 $(ps aux | grep server | awk \'{print $2}\')',
    'kill $(netstat -ano | findstr :5199)',
    'lsof -ti :5199 | xargs kill',
    'lsof -ti :5199 | xargs -r kill -9',
    'pgrep -f server.py | xargs kill',
    'p=$(lsof -ti :5199); kill $p',
    'for p in $(lsof -ti :5199); do kill $p; done',
    'lsof -ti :5199 | while read p; do kill $p; done',
    'Stop-Process -Id (Get-NetTCPConnection -LocalPort 5199).OwningProcess',
    '$c = Get-NetTCPConnection -LocalPort 5199; Stop-Process -Id $c.OwningProcess',
    'Get-NetTCPConnection -LocalPort 5199 | ForEach-Object { Stop-Process -Id $_.OwningProcess }',
    'foreach ($p in Get-NetTCPConnection -LocalPort 5199) { Stop-Process -Id $p.OwningProcess }',
    'taskkill /F /PID (Get-NetTCPConnection -LocalPort 5199).OwningProcess',
    '$procs = Get-Process node; $procs | Stop-Process',
    '$p = Get-Process node; $p.Kill()',
]

# The allowed pattern: kill the PID you captured from your own spawn.
OWN_PID_ALLOWED = [
    'python server.py & echo $!',
    'python server.py & kill $!',
    'python server.py & pid=$!; sleep 1; kill $pid',
    'kill -9 $pid',
    'kill -TERM "$SERVER_PID"',
    '$p = Start-Process python -ArgumentList srv.py -PassThru; Stop-Process -Id $p.Id',
    'Stop-Process -Id $p.Id -Force',
    'taskkill /PID $pid /F',
    'kill $(cat server.pid)',
    'kill $(jobs -p)',
    'kill $(pgrep -P $$)',
    'netstat -ano | findstr :8765; kill $pid',
    'Get-NetTCPConnection -LocalPort 8765; Stop-Process -Id $p.Id',
    '$p = Start-Process python -PassThru; $p.Kill()',
    'lsof -ti :8765 > /dev/null; kill $!',
    'echo $! | xargs kill',
    'echo done',
    'git status',
]


@pytest.mark.parametrize('command', IMAGE_SPELLINGS_THAT_USED_TO_PASS)
def test_image_name_spellings_from_ap1_are_refused(command):
    reason = guard_command(command, set())
    assert reason and 'image-name termination' in reason, command


@pytest.mark.parametrize('template', LISTENER_SPELLINGS)
def test_listener_pid_is_refused_in_every_spelling(template):
    reason = guard_command(template.format(pid=LISTENER), {LISTENER})
    assert reason and 'Clayrune listener' in reason, template


@pytest.mark.parametrize('template', LISTENER_SPELLINGS)
def test_a_pid_that_is_not_the_listener_is_allowed_in_every_spelling(template):
    assert guard_command(template.format(pid=OTHER), {LISTENER}) is None, template


@pytest.mark.parametrize('command', INDIRECT_REFUSED)
def test_indirect_kill_of_a_looked_up_pid_is_refused(command):
    reason = guard_command(command, {LISTENER})
    assert reason and 'looked up' in reason, command
    # Refused from the text alone: it must not need the listener lookup.
    assert guard_command(command, set()) is not None, command


@pytest.mark.parametrize('command', OWN_PID_ALLOWED)
def test_killing_a_pid_the_agent_captured_itself_stays_allowed(command):
    assert guard_command(command, {LISTENER}) is None, command


def test_every_pre_existing_refusal_still_refuses():
    image = [
        'taskkill /F /IM notepad.exe', 'taskkill /IM "my app.exe"',
        'Stop-Process -Name notepad', 'Get-Process notepad | Stop-Process',
        'Get-Process -Id 4 | Stop-Process', 'pkill notepad', 'killall notepad',
        'wmic process where name="notepad.exe" delete',
    ]
    assert all(guard_command(c, {LISTENER}) for c in image)
    for c in ('taskkill /PID 4242', 'taskkill /F /PID 4242', 'Stop-Process -Id 4242',
              'kill 4242', 'kill -9 4242', 'kill -KILL 4242', 'kill -f 4242',
              'bash -c "kill 4242"'):
        reason = guard_command(c, {LISTENER})
        assert reason and 'Clayrune listener' in reason, c


def test_listener_lookup_is_lazy():
    calls = []

    def source():
        calls.append(1)
        return {LISTENER}

    for command in ('echo hi', 'git status', 'python server.py & kill $!',
                    'Stop-Process -Id $p.Id', 'kill $(cat server.pid)', 'ls /tmp/kill'):
        assert guard_command(command, source) is None, command
    assert calls == []
    assert guard_command('kill 111 4242', source)
    assert calls == [1]
    assert guard_command('kill 1234', source) is None
    assert calls == [1, 1]


def test_listener_lookup_runs_once_per_command():
    calls = []
    assert guard_command('kill 1 2 3 ; kill 4 5', lambda: calls.append(1) or set()) is None
    assert calls == [1]


def test_hook_does_not_run_netstat_for_a_command_with_no_kill(monkeypatch):
    from mc import process_guard
    calls = []
    monkeypatch.setattr(process_guard, 'clayrune_listener_pids',
                        lambda: calls.append(1) or {LISTENER})
    assert hook_main({'tool_name': 'Bash', 'tool_input': {'command': 'ls -la'}}) == 0
    assert calls == []
    assert hook_main({'tool_name': 'Bash', 'tool_input': {'command': 'kill 4242'}}) == 2
    assert calls == [1]


def test_listener_scan_reads_the_port_the_spawn_factory_exports(monkeypatch):
    # AP33: agent_spawn exports the bound port as CLAYRUNE_PORT; a server on a
    # port set only in config.json has no MC_PORT, so the listener was unseen.
    import subprocess
    from mc import process_guard

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 0, stdout='  TCP    0.0.0.0:6123    0.0.0.0:0    LISTENING    777\n')

    monkeypatch.setattr(process_guard.subprocess, 'run', fake_run)
    monkeypatch.delenv('MC_PORT', raising=False)
    monkeypatch.setenv('CLAYRUNE_PORT', '6123')
    assert process_guard.clayrune_listener_pids() == {'777'}
