import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools' / 'guards'))

import install_hooks  # noqa: E402
from mc import guardrail_hooks as gh  # noqa: E402


def _body(cmd: str) -> str:
    """`cmd` with the Windows exit-code re-raise tail removed.

    W6 (2026-09-19): `guard_command()` now ends with `gh._EXIT_CODE_SUFFIX` on
    Windows so a PowerShell hook runner cannot collapse the guard's exit 2 into
    a 1 (which Qwen and Codex both read as ALLOW — docs/GUARDRAIL_PARITY_EVIDENCE.md
    §9). The QUOTING rules these tests pin are about the two path tokens and are
    unchanged by that tail, so they assert against the body rather than loosening
    `==` into a substring check.
    """
    return cmd[:-len(gh._EXIT_CODE_SUFFIX)] if gh._EXIT_CODE_SUFFIX else cmd


def _read(clayrune_home: Path, vendor: str) -> dict:
    return json.loads(gh.launch_file_path(vendor, clayrune_home).read_text(encoding='utf-8'))


def test_generates_a_clayrune_owned_file_never_touching_real_home(tmp_path):
    clayrune_home = tmp_path / '.clayrune'
    real_home = tmp_path / 'real_home'  # deliberately never created

    for vendor in ('claude', 'gemini', 'qwen'):
        result = install_hooks.generate(vendor, apply=True, real_home=real_home,
                                         clayrune_home=clayrune_home)
        assert result['changed'] is True
        assert not real_home.exists(), f'{vendor} generation must never touch the real home'
        data = _read(clayrune_home, vendor)
        cfg = install_hooks.VENDOR_CONFIGS[vendor]
        entries = data['hooks'][cfg['event']]
        assert len(entries) == 1
        assert entries[0]['matcher'] == cfg['matcher']
        assert entries[0]['hooks'][0]['command'] == install_hooks.guard_command()
        assert entries[0]['hooks'][0]['name'] == install_hooks.HOOK_NAME


def test_second_run_is_a_no_op(tmp_path):
    clayrune_home = tmp_path / '.clayrune'
    install_hooks.generate('gemini', apply=True, clayrune_home=clayrune_home)
    path = gh.launch_file_path('gemini', clayrune_home)
    before = path.read_text(encoding='utf-8')

    result = install_hooks.generate('gemini', apply=True, clayrune_home=clayrune_home)

    assert result['changed'] is False
    assert path.read_text(encoding='utf-8') == before


def test_dry_run_never_writes(tmp_path):
    clayrune_home = tmp_path / '.clayrune'
    result = install_hooks.generate('qwen', apply=False, clayrune_home=clayrune_home)
    assert result['changed'] is True
    assert result['diff']
    assert not gh.launch_file_path('qwen', clayrune_home).exists()


def test_reinstall_with_changed_path_replaces_in_place(tmp_path):
    clayrune_home = tmp_path / '.clayrune'
    install_hooks.generate('qwen', apply=True, clayrune_home=clayrune_home,
                            guard_script=Path('old/process_guard.py'))
    result = install_hooks.generate('qwen', apply=True, clayrune_home=clayrune_home,
                                     guard_script=Path('new/process_guard.py'))

    assert result['changed'] is True
    data = _read(clayrune_home, 'qwen')
    entries = data['hooks']['PreToolUse']
    assert len(entries) == 1
    assert 'new' in entries[0]['hooks'][0]['command']
    assert 'old' not in entries[0]['hooks'][0]['command']


def test_guard_command_leaves_a_no_space_interpreter_unquoted():
    # Regression: quoting an interpreter with no spaces broke Gemini's real
    # hook execution in production (its .cmd launcher's shell parses two
    # adjacent quoted tokens as a syntax error and then treats the failed
    # hook as an ALLOW) — live-tested 2026-09-18, see guard_command's
    # docstring. Claude and Qwen were unaffected by the same input, but the
    # fix (unquote when safe) must hold regardless.
    #
    # W4/MC-947 (2026-09-18): the script path is ALSO unquoted here now — see
    # test_guard_command_leaves_a_no_space_script_unquoted below for the
    # separate live Qwen regression that fix closes.
    cmd = install_hooks.guard_command(Path('C:/no/spaces/process_guard.py'),
                                       python_exe='C:/no/spaces/python.exe')
    assert _body(cmd) == 'C:/no/spaces/python.exe C:/no/spaces/process_guard.py'
    assert '"' not in cmd


def test_guard_command_quotes_an_interpreter_with_a_space():
    cmd = install_hooks.guard_command(Path('C:/no/spaces/process_guard.py'),
                                       python_exe='C:/Program Files/python.exe')
    assert cmd.startswith('"C:/Program Files/python.exe"')


def test_guard_command_leaves_a_no_space_script_unquoted():
    """W4/MC-947, live-verified 2026-09-18: the script path was
    UNCONDITIONALLY quoted, regardless of whether it had a space — the same
    mistake the interpreter path already had one fix for, just on the other
    token. This one broke Qwen specifically: live-reproduced through a real
    `QwenRuntime.dispatch()` in an environment matching Clayrune's own
    server process (no `MSYSTEM`/`TERM`, which makes Qwen's bundled
    `getShellConfiguration()` resolve its hook shell to `cmd.exe` instead of
    git-bash). Every `run_shell_command` call in that session failed —
    `python.exe: can't open file` with the cwd glued onto the still-quoted
    script path, quote characters and all — and Qwen's hook layer treats a
    hook that fails to even launch as `execution_denied`, not an allow, so
    EVERY shell tool call was silently blocked. A dispatched Qwen agent
    hitting this gave up on shell entirely and, in one observed case,
    fabricated a plausible-looking result instead of reporting the failure
    (docs/_journal/provider-live/cross-vendor/W5-notes.md). Re-verified live
    after this fix: the same environment ran `echo`/`curl`/`dir`/`taskkill`
    (correctly still blocked) via `run_shell_command` with no error."""
    cmd = install_hooks.guard_command(Path('C:/no/spaces/process_guard.py'),
                                       python_exe='C:/no/spaces/python.exe')
    assert 'C:/no/spaces/process_guard.py' in cmd
    assert '"' not in cmd


def test_guard_command_quotes_a_script_path_with_a_space():
    cmd = install_hooks.guard_command(Path('C:/Program Files/process_guard.py'),
                                       python_exe='C:/no/spaces/python.exe')
    assert _body(cmd).endswith('"C:/Program Files/process_guard.py"')


def test_python_exe_override_is_used(tmp_path):
    clayrune_home = tmp_path / '.clayrune'
    result = install_hooks.generate('gemini', apply=True, clayrune_home=clayrune_home,
                                     python_exe='C:/venv/python.exe')
    data = _read(clayrune_home, 'gemini')
    command = data['hooks']['BeforeTool'][0]['hooks'][0]['command']
    assert 'C:/venv/python.exe' in command


def test_codex_is_not_in_the_generated_vendor_set():
    # Codex's hooks config is a TOML table, not a file reference — injected
    # INLINE by CodexRuntime.build_command() via
    # mc.guardrail_hooks.codex_hook_config_args(), never generated here. The
    # first version generated ~/.clayrune/hooks/codex-hooks.json as a MERGE
    # with the user's real ~/.codex/hooks.json, which (a) used a config key
    # (`-c hooks='<path>'`) Codex rejects outright — killed every Codex
    # launch — and (b) copied the user's own hooks into a Clayrune-owned
    # file. Both are gone; this pins that Codex never gets a file again.
    assert 'codex' not in install_hooks.VENDOR_CONFIGS


def test_guard_only_vendor_diff_never_shows_unrelated_content(tmp_path):
    # Regression, back when this wrote into the user's real global settings
    # file directly: a wide diff-context window pulled in an unrelated,
    # unchanged line sitting near the insertion point (a live API key).
    # claude/gemini/qwen's generated file is 100% Clayrune's own now, so
    # there's nothing foreign to leak — this pins that it stays that way.
    clayrune_home = tmp_path / '.clayrune'
    result = install_hooks.generate('gemini', apply=False, clayrune_home=clayrune_home)
    assert 'clayrune-process-guard' in result['diff']
    assert result['diff'].count('matcher') == 1  # nothing else in this file


def test_non_dict_generated_file_refuses_to_read(tmp_path):
    clayrune_home = tmp_path / '.clayrune'
    dest = clayrune_home / 'hooks' / 'qwen-settings.json'
    dest.parent.mkdir(parents=True)
    dest.write_text('[1, 2, 3]', encoding='utf-8')

    try:
        install_hooks.generate('qwen', apply=True, clayrune_home=clayrune_home)
        assert False, 'expected RuntimeError'
    except RuntimeError as e:
        assert 'not an object' in str(e)
    assert dest.read_text(encoding='utf-8') == '[1, 2, 3]'


def test_guard_command_points_at_the_one_shared_guard():
    assert str(install_hooks.GUARD_SCRIPT).endswith('process_guard.py')
    assert install_hooks.GUARD_SCRIPT.exists()


def test_generate_for_boot_only_touches_requested_vendors(tmp_path):
    clayrune_home = tmp_path / '.clayrune'
    results = install_hooks.generate_for_boot(clayrune_home=clayrune_home,
                                               installed_vendors=['gemini'])
    assert [r['vendor'] for r in results] == ['gemini']
    assert gh.launch_file_path('gemini', clayrune_home).exists()
    assert not gh.launch_file_path('qwen', clayrune_home).exists()
    assert not gh.launch_file_path('claude', clayrune_home).exists()


def test_generate_for_boot_silently_skips_codex():
    # Codex isn't in VENDOR_CONFIGS at all (inline injection, no file — see
    # module docstring) — passing it in `installed_vendors` (as the caller's
    # generic "which vendors are installed" list naturally would) must be a
    # silent no-op, not an error and not a file.
    results = install_hooks.generate_for_boot(installed_vendors=['codex'])
    assert results == []


def test_generate_for_boot_survives_one_vendor_failing(tmp_path):
    clayrune_home = tmp_path / '.clayrune'
    bad = clayrune_home / 'hooks' / 'qwen-settings.json'
    bad.parent.mkdir(parents=True)
    bad.write_text('not json', encoding='utf-8')

    results = install_hooks.generate_for_boot(clayrune_home=clayrune_home,
                                               installed_vendors=['qwen', 'gemini'])

    by_vendor = {r['vendor']: r for r in results}
    assert 'error' in by_vendor['qwen']
    assert by_vendor['gemini']['changed'] is True
    assert gh.launch_file_path('gemini', clayrune_home).exists()


def test_launch_file_if_exists_is_none_before_generation(tmp_path):
    clayrune_home = tmp_path / '.clayrune'
    assert gh.launch_file_if_exists('gemini', clayrune_home) is None
    install_hooks.generate('gemini', apply=True, clayrune_home=clayrune_home)
    assert gh.launch_file_if_exists('gemini', clayrune_home) is not None


def test_cli_flags_are_wired(tmp_path):
    clayrune_home = tmp_path / '.clayrune'
    rc = install_hooks.main([
        '--vendor', 'gemini', '--clayrune-home', str(clayrune_home),
        '--repo-root', str(tmp_path / 'other_checkout'),
        '--python-exe', 'C:/venv/python.exe',
        '--apply',
    ])
    assert rc == 0
    assert (clayrune_home / 'hooks' / 'gemini-settings.json').exists()
    data = _read(clayrune_home, 'gemini')
    command = data['hooks']['BeforeTool'][0]['hooks'][0]['command']
    assert (tmp_path / 'other_checkout' / 'mc' / 'process_guard.py').as_posix() in command
    assert 'C:/venv/python.exe' in command


def test_cli_dry_run_default_never_writes(tmp_path):
    clayrune_home = tmp_path / '.clayrune'
    rc = install_hooks.main(['--vendor', 'qwen', '--clayrune-home', str(clayrune_home)])
    assert rc == 0
    assert not (clayrune_home / 'hooks' / 'qwen-settings.json').exists()
