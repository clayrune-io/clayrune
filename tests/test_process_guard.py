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

