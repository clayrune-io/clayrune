"""MC-1179 AP5: an interpreter path with a space must still run the guard.

PowerShell reads a leading quoted word as a string expression, so
`"C:/a b/python.exe" guard.py` is a parse error there (exit 1). Codex and
Gemini treat any hook exit other than 2 as ALLOW, so on such an install the
process guard was a silent no-op for them. Each case below builds the command
for one vendor with a REAL interpreter living under a directory with a space
and runs it through the shell that vendor uses, requiring exit 2 on a block
and exit 0 on an ordinary command.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mc import guardrail_hooks as gh
from mc import hook_command_shell as hcs

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows shell parsing')

BLOCK = json.dumps({'tool_name': 'run_shell_command',
                    'tool_input': {'command': 'taskkill /IM clayrune_no_such_image.exe /F'}})
ALLOW = json.dumps({'tool_name': 'run_shell_command', 'tool_input': {'command': 'echo hi'}})
DENIAL = 'image-name termination is blocked'


@pytest.fixture(scope='module')
def spaced_python(tmp_path_factory):
    """A working interpreter whose path contains a space (a venv launcher)."""
    root = tmp_path_factory.mktemp('spaced') / 'a b'
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(root)],
                   check=True, capture_output=True, timeout=120)
    exe = root / 'Scripts' / 'python.exe'
    assert exe.is_file() and ' ' in str(exe)
    return str(exe)


def _run(argv, payload):
    return subprocess.run(argv, input=payload, capture_output=True, text=True, timeout=60)


def _blocks_and_allows(prefix, command, verbatim=False):
    # cmd.exe's `/s /c` strips the outer quotes of ONE command line; handing it
    # a list would have Python escape the embedded quotes first.
    def argv(): return f'{" ".join(prefix)} "{command}"' if verbatim else prefix + [command]
    blocked = _run(argv(), BLOCK)
    allowed = _run(argv(), ALLOW)
    assert blocked.returncode == 2, (command, blocked.returncode, blocked.stderr)
    assert DENIAL in blocked.stderr, blocked.stderr
    assert allowed.returncode == 0, (command, allowed.returncode, allowed.stderr)


def _powershell():
    ps = shutil.which('powershell.exe') or shutil.which('pwsh.exe')
    if not ps:
        pytest.skip('no PowerShell')
    return ps


def _git_bash():
    for base in (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)')):
        if base:
            p = Path(base) / 'Git' / 'bin' / 'bash.exe'
            if p.is_file():
                return str(p)
    pytest.skip('git-bash not installed')


def _generated_command(vendor, python_exe, tmp_path):
    from tools.guards import install_hooks
    _, after, _ = install_hooks.plan_generate(vendor, python_exe=python_exe,
                                              clayrune_home=tmp_path)
    cfg = install_hooks.VENDOR_CONFIGS[vendor]
    group = next(g for g in after['hooks'][cfg['event']]
                 if any(h.get('name') == gh.HOOK_NAME for h in g['hooks']))
    return group['hooks'][0]['command']


def test_codex_inline_command_runs_the_guard_under_powershell(spaced_python):
    """Codex's `-c` value, TOML-unescaped, run the way Codex runs it. Parse
    error (exit 1, read as allow) before the fix."""
    value = gh.codex_hook_config_args(python_exe=spaced_python)[-1]
    match = re.search(r'command="(.*?)",name=', value)
    assert match, value
    command = match.group(1)
    command = command.replace('\\\\', '\\').replace('\\"', '"')
    assert '"' in command  # the spaced interpreter really is quoted
    # `&` splits a `-c` value that passes through codex.CMD; it must not appear.
    assert '&' not in command
    _blocks_and_allows([_powershell(), '-NoProfile', '-NonInteractive', '-Command'], command)


def test_gemini_generated_command_runs_the_guard_under_powershell(spaced_python, tmp_path):
    command = _generated_command('gemini', spaced_python, tmp_path)
    assert command.startswith('& "')
    # gemini appends its own re-raise after ours; keep it in the test.
    _blocks_and_allows([_powershell(), '-NoProfile', '-NonInteractive', '-Command'],
                       command + '; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }')


def test_qwen_under_comspec_powershell_gets_the_powershell_form(spaced_python, tmp_path, monkeypatch):
    monkeypatch.setenv('ComSpec', _powershell())
    command = _generated_command('qwen', spaced_python, tmp_path)
    assert command.startswith('& "')
    _blocks_and_allows([_powershell(), '-NoProfile', '-Command'], command)


def test_qwen_under_cmd_keeps_the_plain_quoted_form(spaced_python, tmp_path, monkeypatch):
    monkeypatch.setenv('ComSpec', os.environ.get('SystemRoot', r'C:\Windows') + r'\System32\cmd.exe')
    command = _generated_command('qwen', spaced_python, tmp_path)
    assert command.startswith('"') and not command.startswith('& ')
    _blocks_and_allows([f'"{os.environ["ComSpec"]}"', '/d', '/s', '/c'], command, verbatim=True)


def test_claude_command_runs_the_guard_under_git_bash(spaced_python, tmp_path):
    command = _generated_command('claude', spaced_python, tmp_path)
    assert command.startswith('"') and not command.startswith('& ')
    _blocks_and_allows([_git_bash(), '-c'], command)


def test_a_space_free_interpreter_is_emitted_bare_for_every_shell():
    for shell in (None, hcs.SH, hcs.CMD, hcs.POWERSHELL, hcs.POWERSHELL_INLINE):
        assert hcs.quote_hook_tokens(['C:/py/python.exe', 'C:/g/guard.py'], shell) == \
            'C:/py/python.exe C:/g/guard.py'


def test_only_the_interpreter_position_takes_the_call_prefix():
    toks = ['C:/a b/python.exe', 'C:/g h/guard.py']
    assert hcs.quote_hook_tokens(toks, hcs.POWERSHELL) == '& "C:/a b/python.exe" "C:/g h/guard.py"'
    assert hcs.quote_hook_tokens(toks, hcs.POWERSHELL_INLINE) == '. "C:/a b/python.exe" "C:/g h/guard.py"'
    assert hcs.quote_hook_tokens(toks, hcs.SH) == '"C:/a b/python.exe" "C:/g h/guard.py"'
    assert hcs.quote_hook_tokens(toks, hcs.CMD) == '"C:/a b/python.exe" "C:/g h/guard.py"'
    assert hcs.quote_hook_tokens(['C:/py/python.exe', 'C:/g h/guard.py'], hcs.POWERSHELL) == \
        'C:/py/python.exe "C:/g h/guard.py"'
