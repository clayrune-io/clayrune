"""The generated guard command must block under EVERY shell a vendor CLI may
run hooks through — not just the one the author's prompt happened to use.

Qwen live pass run 3 (2026-09-19, docs/_journal/provider-live/qwen/
guardrail.md): a Qwen agent executed `taskkill /IM <decoy> /F` and the decoy
died. qwen-code 0.23.4 runs hooks via `bash -c` whenever MSYSTEM is MINGW*/
MSYS* (a Clayrune started from git-bash), bash ate the backslashes in
`C:\\...\\python.exe`, the hook exited 127, and qwen maps every exit other
than 0/2 to "allow". These tests spawn the command exactly as qwen does
(`<shell> <prefix> <command>`, no extra quoting) and require exit code 2.

§9 (same day) closes the second half of the same class: a `ComSpec` pointing
at PowerShell puts the hook on `powershell -Command`, which reports 1 for any
non-zero child exit — the guard's 2 never reached qwen. See
`guardrail_hooks._EXIT_CODE_SUFFIX`.
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
from mc import process_guard as pg

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows shell parsing')

PAYLOAD = json.dumps({'tool_name': 'run_shell_command',
                      'tool_input': {'command': 'taskkill /IM clayrune_no_such_image.exe /F'}})
# An ordinary command the guard must NOT block. Every shell case asserts this
# too: a command string that mis-parses tends to fail the SAME way for both
# verdicts (python exits 2 on "can't open file", which reads as a block), so a
# block-only assertion can pass while the guard is really denying everything.
ALLOW_PAYLOAD = json.dumps({'tool_name': 'run_shell_command',
                            'tool_input': {'command': 'echo hi'}})
DENIAL = 'image-name termination is blocked'


def _git_bash():
    for base in (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)')):
        if base:
            p = Path(base) / 'Git' / 'bin' / 'bash.exe'
            if p.is_file():
                return str(p)
    return None


def _run(argv, payload=PAYLOAD):
    return subprocess.run(argv, input=payload, capture_output=True, text=True, timeout=60)


def _assert_blocks_and_allows(prefix, extra_suffix=''):
    """The guard, run exactly as a vendor runs it: `<shell> <argsPrefix> <command>`,
    the command handed over as ONE argv element with no extra quoting."""
    cmd = gh.guard_shell_command(python_exe=sys.executable) + extra_suffix
    blocked = _run(prefix + [cmd], PAYLOAD)
    allowed = _run(prefix + [cmd], ALLOW_PAYLOAD)
    assert blocked.returncode == 2, (blocked.returncode, blocked.stderr)
    assert DENIAL in blocked.stderr, blocked.stderr
    assert allowed.returncode == 0, (allowed.returncode, allowed.stderr)


def test_guard_command_has_no_backslash_on_windows():
    cmd = gh.guard_shell_command(python_exe=sys.executable)
    assert '\\' not in cmd


def test_guard_blocks_when_qwen_runs_hooks_through_git_bash():
    bash = _git_bash()
    if not bash:
        pytest.skip('git-bash not installed')
    _assert_blocks_and_allows([bash, '-c'])


def test_guard_blocks_when_qwen_runs_hooks_through_cmd():
    comspec = os.environ.get('ComSpec') or 'cmd.exe'
    _assert_blocks_and_allows([comspec, '/d', '/s', '/c'])


def test_guard_blocks_when_gemini_runs_hooks_through_powershell():
    ps = shutil.which('powershell.exe') or shutil.which('pwsh.exe')
    if not ps:
        pytest.skip('no PowerShell')
    # gemini-cli 0.59.0 appends this itself so the hook's exit code survives.
    # Ours fires first; gemini's is dead code after it. Asserted together
    # because the two suffixes are concatenated in production.
    _assert_blocks_and_allows(
        [ps, '-NoProfile', '-NonInteractive', '-Command'],
        extra_suffix='; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }')


# ── §9: ComSpec pointing at PowerShell (2026-09-19) ─────────────────────────
# qwen-code's getShellConfiguration() (chunk-V545KI73.js:1034-1042) reads
# ComSpec and, when it ends in powershell.exe/pwsh.exe, runs hooks as
# `<ComSpec> -NoProfile -Command <command>` — and qwen appends NO exit-code
# re-raise of its own. `powershell -Command <native.exe>` reports 1 for any
# non-zero child code, so the guard's exit 2 arrived as 1 and qwen's
# convertPlainTextToHookOutput maps every code other than 0/2 to allow.
# The next two FAIL on 7b4809a.

def test_guard_blocks_when_qwen_runs_hooks_through_powershell_no_vendor_suffix():
    """Qwen's EXACT argv prefix and NO vendor-appended suffix — the §8
    fail-open. Before the fix this returned 1, which qwen reads as allow."""
    ps = shutil.which('powershell.exe') or shutil.which('pwsh.exe')
    if not ps:
        pytest.skip('no PowerShell')
    _assert_blocks_and_allows([ps, '-NoProfile', '-Command'])


def _codex_inline_command():
    """The command string as it reaches codex.exe, TOML-unescaped."""
    args = gh.codex_hook_config_args(python_exe=sys.executable)
    m = re.search(r'command="(.*?)",name=', args[-1])
    assert m, args[-1]
    return m.group(1).replace('\\\\', '\\').replace('\\"', '"'), args[-1]


def test_guard_blocks_when_codex_runs_the_inline_hook_through_powershell():
    """Codex 0.154.0 runs hooks as `powershell.exe -NoProfile -Command <cmd>`
    — measured live 2026-09-19 by a PreToolUse probe that read its own parent
    process. Without the exit-code suffix the guard's 2 arrived as 1 and Codex
    printed `hook: PreToolUse Failed` and ran the kill anyway. FAILS on
    7b4809a."""
    ps = shutil.which('powershell.exe') or shutil.which('pwsh.exe')
    if not ps:
        pytest.skip('no PowerShell')
    command, _ = _codex_inline_command()
    blocked = _run([ps, '-NoProfile', '-NonInteractive', '-Command', command], PAYLOAD)
    allowed = _run([ps, '-NoProfile', '-NonInteractive', '-Command', command], ALLOW_PAYLOAD)
    assert blocked.returncode == 2, (blocked.returncode, blocked.stderr)
    assert DENIAL in blocked.stderr, blocked.stderr
    assert allowed.returncode == 0, (allowed.returncode, allowed.stderr)


def test_codex_hook_has_no_tool_name_matcher():
    """`matcher="shell"` never matched: Codex 0.154.0 reports its shell tool
    as `Bash` (measured — a probe hook dumped `"tool_name": "Bash"`), so the
    PreToolUse event never fired and the guard was absent from every Codex
    launch. Filtering belongs to `process_guard.hook_main`, which already
    knows every vendor's shell-tool name; a second filter here can only add a
    silent fail-open. FAILS on 7b4809a."""
    _, hooks_value = _codex_inline_command()
    assert 'matcher=' not in hooks_value, hooks_value
    assert 'Bash' in pg._SHELL_TOOL_NAMES
    assert pg.hook_main({'tool_name': 'Bash',
                         'tool_input': {'command': 'taskkill /IM x.exe /F'}}) == 2
    assert pg.hook_main({'tool_name': 'read_file', 'tool_input': {}}) == 0


def test_guard_command_reraises_the_exit_code_on_windows():
    cmd = gh.guard_shell_command(python_exe=sys.executable)
    assert cmd.endswith(' ; exit $LASTEXITCODE'), cmd
    # The space before `;` is load-bearing: glued to the script path, cmd.exe
    # hands `...process_guard.py;` to python, which then exits 2 on EVERY
    # command — a silent fail-closed.
    assert not cmd.endswith('.py; exit $LASTEXITCODE'), cmd


# ── The vendor facts the suffix depends on, re-read from the INSTALLED CLIs ──

def _installed_bundle(*parts):
    roots = [Path.home() / '.npm-global' / 'node_modules']
    appdata = os.environ.get('APPDATA')
    if appdata:
        roots.append(Path(appdata) / 'npm' / 'node_modules')
    for root in roots:
        p = root.joinpath(*parts)
        if p.is_file():
            return p
    return None


def _fn_body(text, header):
    body = text.split(header, 1)[1]
    return body[:body.index('\n}\n') + 3]


def test_gemini_still_runs_windows_hooks_only_through_powershell():
    """§8 asserted Gemini is safe because gemini-cli re-raises $LASTEXITCODE.
    Re-read rather than trusted: if a future gemini-cli ever picked bash on
    Windows, its `if (...) { ... }` suffix concatenated after ours would be a
    bash PARSE error and every shell call would fail closed."""
    src = None
    for name in ('chunk-S4PJ76PA.js', 'chunk-SM627E5R.js', 'chunk-YSBB75DZ.js'):
        src = _installed_bundle('@google', 'gemini-cli', 'bundle', name)
        if src:
            break
    if not src:
        pytest.skip('gemini-cli bundle not installed')
    text = src.read_text(encoding='utf-8', errors='replace')
    assert 'command = `${command}; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }`;' in text
    body = _fn_body(text, 'function getShellConfiguration() {')
    assert 'MSYSTEM' not in body, body
    # Every Windows branch resolves to powershell; only the POSIX tail is bash.
    win = body.split('if (isWindows()) {', 1)[1]
    win = win[:win.rindex('  return {')]
    assert 'shell: "bash"' not in win, win
    assert len(re.findall(r'shell: "powershell"', win)) >= 2, win


def test_qwen_picks_powershell_from_comspec_and_adds_no_exit_suffix():
    src = _installed_bundle('@qwen-code', 'qwen-code', 'chunks', 'chunk-V545KI73.js')
    if not src:
        pytest.skip('qwen-code bundle not installed')
    body = _fn_body(src.read_text(encoding='utf-8', errors='replace'),
                    'function getShellConfiguration() {')
    assert 'powershell.exe' in body and '"-NoProfile", "-Command"' in body, body
    hooks = _installed_bundle('@qwen-code', 'qwen-code', 'chunks', 'chunk-DCRVSIK6.js')
    if hooks:
        h = hooks.read_text(encoding='utf-8', errors='replace')
        h = h.split('async executeCommandHook(', 1)[1][:8000]
        assert 'LASTEXITCODE' not in h, 'qwen started re-raising; re-check the suffix'


# ── Qwen's own shell tool: pinned to cmd.exe ────────────────────────────────
# The same MSYSTEM switch also moves qwen's run_shell_command onto git-bash,
# where MSYS path conversion turned `taskkill /PID <n> /F` into
# `taskkill 'C:/Program Files/Git/PID' ...` (live replay, 2026-09-19).

from mc import agent_runtime as art  # noqa: E402
from mc.agent_runtime import QwenRuntime, SessionHandle  # noqa: E402


def test_pin_blanks_git_bash_markers(monkeypatch):
    monkeypatch.setenv('MSYSTEM', 'MINGW64')
    monkeypatch.setenv('TERM', 'cygwin')
    env = art._pin_qwen_windows_shell({})
    assert env == {'MSYSTEM': '', 'TERM': ''}


def test_pin_leaves_a_plain_env_alone(monkeypatch):
    monkeypatch.delenv('MSYSTEM', raising=False)
    monkeypatch.setenv('TERM', 'xterm-256color')
    assert art._pin_qwen_windows_shell({}) == {}


def test_qwen_dispatch_and_followup_carry_the_pin(monkeypatch, tmp_path):
    monkeypatch.setenv('MC_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('MSYSTEM', 'MINGW64')
    captured = {}

    def _fake(self, cmd, full_prompt, project_path, project_id, task, mc_sid,
              session_dict, incognito, env, callbacks, register_process, **kw):
        captured['dispatch'] = env
        return SessionHandle(mc_session_id=mc_sid, provider='qwen', mode='A',
                             project_path=project_path, project_id=project_id,
                             session_dict=session_dict if session_dict is not None else {})

    class _Stdin:
        def write(self, s): pass
        def close(self): pass

    class _Proc:
        stdin = _Stdin()
        stdout = iter([])
        pid = 1
        def wait(self): return 0
        def poll(self): return None

    def _popen(*a, **k):
        captured['followup'] = k.get('env')
        return _Proc()

    monkeypatch.setattr(art, '_mode_a_dispatch', _fake)
    monkeypatch.setattr(art.subprocess, 'Popen', _popen)
    rt = QwenRuntime()
    rt._bin_cache = 'qwen'
    rt.dispatch(project_path='/p', task='x', session_dict={})
    rt.write_followup(SessionHandle(mc_session_id='s', provider='qwen', mode='A',
                                    project_path='/p', project_id='p',
                                    session_dict={'log_lines': [], 'proc': None}), 'hi')
    assert captured['dispatch']['MSYSTEM'] == ''
    assert captured['followup']['MSYSTEM'] == ''
