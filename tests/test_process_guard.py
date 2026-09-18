import json

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

