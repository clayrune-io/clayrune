"""Every delete target judged on its own (Quill, 2026-10-07 §2).

The old exemption was `any(marker in command) and '..' not in command`, so a
scratch marker ANYWHERE in the text (a comment, a second target, a later
command) let a recursive delete of home through, and a folder whose NAME
contained a marker (`scratchpad-app`) counted as scratch. Measured at hook
level on 76f501a5; same logic in v2.4.6 and since the MVP (24785676).

Each case runs under both shell tool names: `Bash` (bash AND PowerShell
readings, since Codex sends PowerShell as Bash) and `PowerShell`.

steward/fence.py is human-owned, so the fix ships as
docs/patches/fence-scratch-per-target.patch. Until it is applied, a case the
old fence gets wrong xfails (it never fails); once fence.py carries
`_deletes_all_scratch`, every case asserts.
"""
import functools
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from steward import fence as _fence
from steward.fence import classify_action

_PATCHED = hasattr(_fence, '_deletes_all_scratch')

_SHELL_TOOLS = ('Bash', 'PowerShell')
_REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _temp_env(monkeypatch):
    # TEMP / TMP / TMPDIR all name the interpreter's real temp dir, so the
    # cases read the same on every machine. Home is left real: it exists and
    # is never inside temp.
    t = tempfile.gettempdir()
    for k in ('TEMP', 'TMP', 'TMPDIR'):
        monkeypatch.setenv(k, t)


def _blocked(tool, cmd):
    return classify_action(tool, {'command': cmd}).blocked


# ── Quill's measured bypasses: every one allowed before, every one refused now ─
QUILL_BYPASSES = [
    'rm -rf C:/Users/levir/Documents /tmp/x',
    'rm -rf ~ /tmp/build',
    'rm -rf "$HOME"  # /tmp/',
    'rm -rf "$HOME"; ls /tmp/',
    'rm -rf C:/Users/levir/Documents C:/Users/levir/AppData/Local/Temp/x',
    'rm -rf C:/work/scratchpad-app',
    'Remove-Item -Recurse -Force C:\\Users\\levir\\Documents, '
    'C:\\Users\\levir\\AppData\\Local\\Temp\\x',
    'Remove-Item -Recurse -Force $HOME  # \\temp\\',
]


@pytest.mark.parametrize('tool', _SHELL_TOOLS)
@pytest.mark.parametrize('cmd', QUILL_BYPASSES)
def test_quill_bypasses_are_refused(tool, cmd):
    assert _blocked(tool, cmd), (tool, cmd)


# Quill's controls, refused before and still refused.
QUILL_CONTROLS = [
    'rm -rf C:/Users/levir/Documents',
    'rm -rf C:/Users/LEVIR~1',
    'rm -rf C:/PROGRA~1',
    'rm -rf C:/Users/levir/AppData/Local/Temp/../../..',
    'Remove-Item -Recurse -Force C:\\Users\\levir\\Documents',
]


@pytest.mark.parametrize('tool', _SHELL_TOOLS)
@pytest.mark.parametrize('cmd', QUILL_CONTROLS)
def test_quill_controls_stay_refused(tool, cmd):
    assert _blocked(tool, cmd), (tool, cmd)


# ── One non-scratch target, wherever it hides, refuses the whole command ─────
REFUSE_CASES = [
    # the temp root itself is not inside a scratch root
    'rm -rf /tmp',
    'rm -rf /tmp/',
    'rm -rf /tmp/.',
    'rm -rf "$TEMP"',
    # names that merely contain a marker
    'rm -rf /tmpfoo',
    'rm -rf /tmp*',
    'rm -rf C:/work/_scratch-old',
    'rm -rf C:/work/my_scratch',
    # a second target, a second command, a comment, glued text
    'rm -rf /tmp/x ~',
    'rm -rf "/tmp/x" "$HOME"',
    'rm -rf /tmp/x; rm -rf ~',
    'rm -rf /tmp/x && rm -rf C:/Users/levir/Documents',
    'rm -rf _scratch/a || rm -rf ~',
    'rm -rf ~/Documents # _scratch',
    'rm -rf ~  # rm -rf /tmp/x',
    'rm -rf $HOME#/tmp/',
    'rm -rf /tmp/x C:/work/scratchpad-app',
    'Remove-Item -Path /tmp/x,~ -Recurse -Force',
    'Remove-Item -Recurse -Force ~, /tmp/x',
    # escapes
    'rm -rf /tmp/x/../..',
    'rm -rf _scratch/../secrets',
    'rm -rf /tmp/x/.../y',
    # expansion the hook cannot see the value of
    'D=/tmp/x; rm -rf $D',
    'rm -rf /tmp/x$(echo)',
    'rm -rf /tmp/`echo x`',
    'rm -rf /tmp/{x,y} ~',
    'rm -rf {/tmp/x,~}',
    'rm -rf ~user/x',
    'rm -rf /tmp/x$UNKNOWN_VAR',
    'rm -rf C:/Users/*/AppData/Local/Temp/x',
    # a variable the same command reassigns
    'TEMP=~; rm -rf $TEMP/x',
    'export TMPDIR=$HOME && rm -rf $TMPDIR/x',
    'TMPDIR="$HOME" rm -rf $TMPDIR/x',
    '$env:TEMP = $HOME; Remove-Item -Recurse $env:TEMP\\x',
    'Set-Item env:TEMP C:\\Users; Remove-Item -Recurse $env:TEMP\\x',
    # PowerShell block comments and here-strings hide text
    'rm -rf $HOME <# /tmp/ #>',
    'Remove-Item -Recurse -Force $HOME <# \\temp\\ #>',
    # deletes this check cannot read refuse rather than pass
    'echo /tmp/ | xargs rm -rf ~',
    'eval "rm -rf /tmp/x ~"',
    'find /tmp -exec rm -rf ~ \\;',
    'gci ~ | Remove-Item -Recurse -Force',
    'Get-ChildItem ~ | % { ri $_ }',
    'Get-ChildItem /tmp/x | % { ri $_; ri -Recurse ~ }',
    'Remove-Item (Join-Path $HOME Documents) -Recurse',
    # a nested shell is judged on what it will run
    'bash -c "rm -rf $HOME"',
    "bash -c 'rm -rf /tmp/x ~'",
    'powershell -Command "Remove-Item -Recurse $HOME"',
    "powershell -NoProfile -Command 'Remove-Item -Recurse -Force ~'",
    'cmd /c "rd /s /q %USERPROFILE%"',
    'cmd /c "rd /s /q %TEMP%\\x & rd /s /q %USERPROFILE%"',
    # a link made in the same command cannot be resolved yet
    'ln -s ~ /tmp/h && rm -rf /tmp/h/',
    'cmd /c mklink /J %TEMP%\\h %USERPROFILE% & rd /s /q %TEMP%\\h',
]


@pytest.mark.parametrize('tool', _SHELL_TOOLS)
@pytest.mark.parametrize('cmd', REFUSE_CASES)
def test_any_non_scratch_target_refuses(tool, cmd):
    assert _blocked(tool, cmd), (tool, cmd)


@pytest.mark.parametrize('tool', _SHELL_TOOLS)
def test_an_existing_folder_next_to_a_scratch_target_refuses(tool):
    repo = str(_REPO).replace('\\', '/')
    assert _blocked(tool, f'rm -rf {repo} /tmp/x')
    assert _blocked(tool, f'rm -rf /tmp/x "{repo}"')
    assert _blocked(tool, f'Remove-Item -Recurse -Force "{_REPO}", "{tempfile.gettempdir()}\\x"')


# ── Scratch-scoped deletes stay allowed ─────────────────────────────────────
ALLOW_CASES = [
    'rm -rf /tmp/x',
    'rm -rf /tmp/a /tmp/b',
    'rm -rf -- /tmp/x',
    'sudo rm -rf /tmp/x',
    '(rm -rf /tmp/x)',
    'rm -rf /tmp/x  # cleanup ~',
    'rm -rf /tmp/x 2>/dev/null',
    'rm -rf /tmp/x >/dev/null 2>&1',
    'rm -rf _scratch/out',
    'rm -rf _scratch',
    'rm -rf scratchpad/run1',
    'rm -rf _scratch/a; rm -rf /tmp/b',
    'cd proj && rm -rf _scratch/out',
    'git status && rm -rf _scratch/out && echo done',
    "bash -c 'rm -rf /tmp/x'",
    'rm -rf C:/Users/x/AppData/Local/Temp/foo',
    'rm -rf C:/Users/x/AppData/Local/Temp/*',
    'Remove-Item -Recurse -Force C:\\Users\\x\\AppData\\Local\\Temp\\foo',
    'Remove-Item -Recurse -Force C:\\Users\\x\\AppData\\Local\\Temp\\foo -ErrorAction SilentlyContinue',
    'Remove-Item -Recurse -Force -Path /tmp/x -Confirm:$false',
    'Remove-Item -Recurse -Force C:\\Users\\x\\AppData\\Local\\Temp\\a, '
    'C:\\Users\\x\\AppData\\Local\\Temp\\b',
    'cmd /c "rd /s /q %TEMP%\\x"',
    'Get-ChildItem _scratch\\x | ri -Recurse -Force',
    'Get-ChildItem _scratch\\x | % { ri $_ }',
]


@pytest.mark.parametrize('tool', _SHELL_TOOLS)
@pytest.mark.parametrize('cmd', ALLOW_CASES)
def test_scratch_deletes_stay_allowed(tool, cmd):
    d = classify_action(tool, {'command': cmd})
    assert not d.blocked, (tool, cmd, d.reason)


@pytest.mark.parametrize('tool', _SHELL_TOOLS)
def test_real_temp_dir_by_absolute_path_is_scratch(tool):
    t = tempfile.gettempdir()
    assert not _blocked(tool, f'rm -rf "{t.replace(os.sep, "/")}/x"')
    assert not _blocked(tool, f'Remove-Item -Recurse -Force "{t}{os.sep}x"')


# PowerShell-only spellings: allowed through the PowerShell tool. Through the
# Bash tool name bash would read them differently ($env is a bash variable,
# $TEMP an unset PowerShell one), so they refuse there.
PS_ONLY_ALLOW = [
    'Remove-Item -Recurse -Force $env:TEMP\\x',
    'Remove-Item -Recurse -Force "$env:TEMP\\x"',
    'Remove-Item -Recurse -Force ${env:TEMP}\\x',
    'Remove-Item -Recurse -Force ~\\_scratch\\x',
]


@pytest.mark.parametrize('cmd', PS_ONLY_ALLOW)
def test_powershell_spellings_allowed_through_the_powershell_tool(cmd):
    d = classify_action('PowerShell', {'command': cmd})
    assert not d.blocked, (cmd, d.reason)


@pytest.mark.parametrize('cmd', PS_ONLY_ALLOW[:3] + ['rm -rf "$TEMP/x"'])
def test_spellings_the_two_shells_read_differently_refuse_through_bash(cmd):
    assert _blocked('Bash', cmd)


def test_bash_variable_spelling_refuses_through_powershell():
    # In PowerShell $TEMP is an unset variable, so "$TEMP/x" is C:\x.
    assert _blocked('PowerShell', 'rm -rf "$TEMP/x"')


# ── The real path decides: links and 8.3 names ──────────────────────────────
def _make_dir_link(link: Path, target: Path) -> bool:
    try:
        if os.name == 'nt':
            r = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                               capture_output=True)
            return r.returncode == 0 and link.exists()
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        return False


@pytest.mark.parametrize('tool', _SHELL_TOOLS)
def test_link_inside_temp_pointing_out_is_judged_by_where_it_lands(tool, tmp_path):
    link = tmp_path / 'out'
    if not _make_dir_link(link, _REPO):
        pytest.skip('cannot create a directory link here')
    try:
        p = str(link).replace('\\', '/')
        assert _blocked(tool, f'rm -rf "{p}/"'), tool
        assert _blocked(tool, f'rm -rf "{p}/steward"'), tool
    finally:
        os.rmdir(link) if os.name == 'nt' else link.unlink()


def _short_name(path: Path):
    if os.name != 'nt':
        return None
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024)
    s = buf.value if n else None
    return s if s and '~' in s else None


@pytest.mark.parametrize('tool', _SHELL_TOOLS)
def test_short_names_are_resolved(tool, tmp_path):
    inside = tmp_path / 'a_long_directory_name_for_83'
    inside.mkdir()
    short_in = _short_name(inside)
    short_repo = _short_name(_REPO)
    if not short_in and not short_repo:
        pytest.skip('8.3 names are not generated on this volume')
    if short_in:
        assert not _blocked(tool, f'rm -rf "{short_in}"'), (tool, short_in)
    if short_repo:
        assert _blocked(tool, f'rm -rf "{short_repo}"'), (tool, short_repo)
        assert _blocked(tool, f'rm -rf "{short_repo}" /tmp/x'), (tool, short_repo)


def _xfail_until_patched(fn):
    @functools.wraps(fn)
    def run(*a, **kw):
        try:
            return fn(*a, **kw)
        except AssertionError:
            if _PATCHED:
                raise
            pytest.xfail('needs docs/patches/fence-scratch-per-target.patch')
    return run


for _name, _obj in list(globals().items()):
    if _name.startswith('test_') and callable(_obj):
        globals()[_name] = _xfail_until_patched(_obj)
