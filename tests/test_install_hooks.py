import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools' / 'guards'))

import install_hooks  # noqa: E402
from mc import guardrail_hooks as gh  # noqa: E402


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
    cmd = install_hooks.guard_command(Path('C:/no/spaces/process_guard.py'),
                                       python_exe='C:/no/spaces/python.exe')
    assert cmd == 'C:/no/spaces/python.exe "C:\\no\\spaces\\process_guard.py"'
    assert '""' not in cmd


def test_guard_command_quotes_an_interpreter_with_a_space():
    cmd = install_hooks.guard_command(Path('C:/no/spaces/process_guard.py'),
                                       python_exe='C:/Program Files/python.exe')
    assert cmd.startswith('"C:/Program Files/python.exe"')


def test_python_exe_override_is_used(tmp_path):
    clayrune_home = tmp_path / '.clayrune'
    result = install_hooks.generate('gemini', apply=True, clayrune_home=clayrune_home,
                                     python_exe='C:/venv/python.exe')
    data = _read(clayrune_home, 'gemini')
    command = data['hooks']['BeforeTool'][0]['hooks'][0]['command']
    assert 'C:/venv/python.exe' in command


def test_codex_merges_with_real_hooks_json_without_modifying_it(tmp_path):
    clayrune_home = tmp_path / '.clayrune'
    real_home = tmp_path / 'real_home'
    real_hooks_path = real_home / '.codex' / 'hooks.json'
    real_hooks_path.parent.mkdir(parents=True)
    users_own_hooks = {
        'hooks': {
            'Stop': [{'hooks': [{'type': 'command', 'command': 'python C:/users/own/stop-hook.py'}]}]
        }
    }
    real_hooks_path.write_text(json.dumps(users_own_hooks), encoding='utf-8')

    result = install_hooks.generate('codex', apply=True, real_home=real_home,
                                     clayrune_home=clayrune_home)

    assert result['changed'] is True
    # The user's real file is completely untouched.
    assert json.loads(real_hooks_path.read_text(encoding='utf-8')) == users_own_hooks
    # The GENERATED file has both: the user's own Stop hook, verbatim...
    generated = _read(clayrune_home, 'codex')
    assert generated['hooks']['Stop'] == users_own_hooks['hooks']['Stop']
    # ...and Clayrune's own PreToolUse guard.
    assert generated['hooks']['PreToolUse'][0]['hooks'][0]['name'] == install_hooks.HOOK_NAME
    assert generated['hooks']['PreToolUse'][0]['matcher'] == 'shell'


def test_codex_reinstall_replaces_only_our_group_not_the_users(tmp_path):
    clayrune_home = tmp_path / '.clayrune'
    real_home = tmp_path / 'real_home'
    real_hooks_path = real_home / '.codex' / 'hooks.json'
    real_hooks_path.parent.mkdir(parents=True)
    real_hooks_path.write_text(json.dumps({
        'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'python stop.py'}]}]}
    }), encoding='utf-8')

    install_hooks.generate('codex', apply=True, real_home=real_home, clayrune_home=clayrune_home,
                            guard_script=Path('old/process_guard.py'))
    install_hooks.generate('codex', apply=True, real_home=real_home, clayrune_home=clayrune_home,
                            guard_script=Path('new/process_guard.py'))

    generated = _read(clayrune_home, 'codex')
    assert len(generated['hooks']['PreToolUse']) == 1
    assert 'new' in generated['hooks']['PreToolUse'][0]['hooks'][0]['command']
    assert generated['hooks']['Stop'] == [{'hooks': [{'type': 'command', 'command': 'python stop.py'}]}]


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


def test_codex_first_generation_diff_legitimately_shows_the_merged_real_content(tmp_path):
    # NOT a leak: codex's file is a real merge (unverified override semantics
    # — see module docstring), so the first-ever diff for it necessarily shows
    # the user's pre-existing hooks.json content, because from the (empty)
    # destination's own perspective all of it is new. This is disclosed,
    # intended behavior for a merge — Dave asked to see the exact diff a
    # write would produce, and for codex that IS the merged result.
    clayrune_home = tmp_path / '.clayrune'
    real_home = tmp_path / 'real_home'
    real_hooks_path = real_home / '.codex' / 'hooks.json'
    real_hooks_path.parent.mkdir(parents=True)
    real_hooks_path.write_text(json.dumps({
        'hooks': {'SessionStart': [{'hooks': [{'type': 'command', 'command': 'echo real-hook'}]}]}
    }), encoding='utf-8')

    first = install_hooks.generate('codex', apply=True, real_home=real_home,
                                    clayrune_home=clayrune_home)
    assert 'real-hook' in first['diff']
    assert 'clayrune-process-guard' in first['diff']

    # But a SECOND generation, with nothing changed on either side, shows
    # nothing — it does not re-surface the same real content as if it were
    # new every time.
    second = install_hooks.generate('codex', apply=False, real_home=real_home,
                                     clayrune_home=clayrune_home)
    assert second['changed'] is False
    assert second['diff'] == ''


def test_non_dict_json_refuses_to_read_the_real_file(tmp_path):
    clayrune_home = tmp_path / '.clayrune'
    real_home = tmp_path / 'real_home'
    real_hooks_path = real_home / '.codex' / 'hooks.json'
    real_hooks_path.parent.mkdir(parents=True)
    real_hooks_path.write_text('[1, 2, 3]', encoding='utf-8')

    try:
        install_hooks.generate('codex', apply=True, real_home=real_home, clayrune_home=clayrune_home)
        assert False, 'expected RuntimeError'
    except RuntimeError as e:
        assert 'not an object' in str(e)
    assert real_hooks_path.read_text(encoding='utf-8') == '[1, 2, 3]'


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
    assert not gh.launch_file_path('codex', clayrune_home).exists()


def test_generate_for_boot_survives_one_vendor_failing(tmp_path):
    clayrune_home = tmp_path / '.clayrune'
    real_home = tmp_path / 'real_home'
    bad = real_home / '.codex' / 'hooks.json'
    bad.parent.mkdir(parents=True)
    bad.write_text('not json', encoding='utf-8')

    results = install_hooks.generate_for_boot(clayrune_home=clayrune_home, real_home=real_home,
                                               installed_vendors=['codex', 'gemini'])

    by_vendor = {r['vendor']: r for r in results}
    assert 'error' in by_vendor['codex']
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
    assert str(tmp_path / 'other_checkout' / 'mc' / 'process_guard.py') in command
    assert 'C:/venv/python.exe' in command


def test_cli_dry_run_default_never_writes(tmp_path):
    clayrune_home = tmp_path / '.clayrune'
    rc = install_hooks.main(['--vendor', 'qwen', '--clayrune-home', str(clayrune_home)])
    assert rc == 0
    assert not (clayrune_home / 'hooks' / 'qwen-settings.json').exists()
