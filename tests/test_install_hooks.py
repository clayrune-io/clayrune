import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools' / 'guards'))

import install_hooks  # noqa: E402


def test_fresh_home_writes_hook_for_gemini_and_qwen(tmp_path):
    for vendor in ('gemini', 'qwen'):
        result = install_hooks.install(vendor, tmp_path, apply=True)
        assert result['changed'] is True
        path = Path(result['path'])
        assert path.exists()
        data = json.loads(path.read_text(encoding='utf-8'))
        cfg = install_hooks.VENDOR_CONFIGS[vendor]
        entries = data['hooks'][cfg['event']]
        assert len(entries) == 1
        assert entries[0]['matcher'] == cfg['matcher']
        assert entries[0]['hooks'][0]['command'] == install_hooks.guard_command()
        assert entries[0]['hooks'][0]['name'] == install_hooks.HOOK_NAME


def test_second_run_is_a_no_op(tmp_path):
    install_hooks.install('gemini', tmp_path, apply=True)
    path = tmp_path / '.gemini' / 'settings.json'
    before = path.read_text(encoding='utf-8')

    result = install_hooks.install('gemini', tmp_path, apply=True)

    assert result['changed'] is False
    assert path.read_text(encoding='utf-8') == before


def test_diff_never_shows_unrelated_preexisting_content(tmp_path):
    # Regression: a non-zero diff context window pulled in unchanged lines
    # sitting near the insertion point — on a real ~/.qwen/settings.json this
    # included a live API key. The installer only ever appends, so the diff
    # must show ONLY the new lines, never neighboring existing content.
    settings_path = tmp_path / '.qwen' / 'settings.json'
    settings_path.parent.mkdir(parents=True)
    existing = {'security': {'auth': {'apiKey': 'sk-super-secret-do-not-leak'}}}
    settings_path.write_text(json.dumps(existing), encoding='utf-8')

    result = install_hooks.install('qwen', tmp_path, apply=False)

    assert 'sk-super-secret-do-not-leak' not in result['diff']
    assert 'clayrune-process-guard' in result['diff']


def test_dry_run_never_writes(tmp_path):
    result = install_hooks.install('qwen', tmp_path, apply=False)
    assert result['changed'] is True
    assert result['diff']
    assert not (tmp_path / '.qwen' / 'settings.json').exists()


def test_existing_user_hook_is_preserved_not_overwritten(tmp_path):
    settings_path = tmp_path / '.gemini' / 'settings.json'
    settings_path.parent.mkdir(parents=True)
    existing = {
        'hooks': {
            'BeforeTool': [
                {'matcher': 'write_file', 'hooks': [{'type': 'command', 'command': 'echo mine'}]}
            ]
        },
        'someOtherSetting': True,
    }
    settings_path.write_text(json.dumps(existing), encoding='utf-8')

    result = install_hooks.install('gemini', tmp_path, apply=True)

    assert result['changed'] is True
    data = json.loads(settings_path.read_text(encoding='utf-8'))
    entries = data['hooks']['BeforeTool']
    assert len(entries) == 2
    assert entries[0] == existing['hooks']['BeforeTool'][0]
    assert entries[1]['hooks'][0]['command'] == install_hooks.guard_command()
    assert data['someOtherSetting'] is True


def test_non_dict_json_refuses_to_touch_the_file(tmp_path):
    settings_path = tmp_path / '.qwen' / 'settings.json'
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text('[1, 2, 3]', encoding='utf-8')

    try:
        install_hooks.install('qwen', tmp_path, apply=True)
        assert False, 'expected RuntimeError'
    except RuntimeError as e:
        assert 'not an object' in str(e)
    # File must be untouched.
    assert settings_path.read_text(encoding='utf-8') == '[1, 2, 3]'


def test_guard_command_points_at_the_one_shared_guard():
    assert str(install_hooks.GUARD_SCRIPT).endswith('process_guard.py')
    assert install_hooks.GUARD_SCRIPT.exists()


def test_repo_root_override_points_hook_at_a_different_checkout(tmp_path):
    # A worktree's mc/process_guard.py is deleted with the worktree — writing
    # the REAL install from one must be able to point at the permanent (main)
    # checkout instead, via --repo-root / the guard_script param it maps to.
    other_checkout_guard = tmp_path / 'other_checkout' / 'mc' / 'process_guard.py'
    result = install_hooks.install('gemini', tmp_path / 'home', apply=True,
                                    guard_script=other_checkout_guard)
    assert result['changed'] is True
    data = json.loads(Path(result['path']).read_text(encoding='utf-8'))
    written_command = data['hooks']['BeforeTool'][0]['hooks'][0]['command']
    assert str(other_checkout_guard) in written_command
    assert str(install_hooks.GUARD_SCRIPT) not in written_command


def test_cli_repo_root_flag_is_wired(tmp_path, capsys):
    home = tmp_path / 'home'
    rc = install_hooks.main([
        '--vendor', 'qwen', '--home', str(home),
        '--repo-root', str(tmp_path / 'other_checkout'),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    # The diff is pretty-printed JSON, so backslashes come back doubled.
    expected = json.dumps(str(tmp_path / 'other_checkout' / 'mc' / 'process_guard.py'))[1:-1]
    assert expected in out
    assert json.dumps(str(install_hooks.GUARD_SCRIPT))[1:-1] not in out


def test_python_exe_override_is_used_instead_of_sys_executable(tmp_path):
    result = install_hooks.install('gemini', tmp_path, apply=True, python_exe='C:/venv/python.exe')
    data = json.loads(Path(result['path']).read_text(encoding='utf-8'))
    command = data['hooks']['BeforeTool'][0]['hooks'][0]['command']
    assert 'C:/venv/python.exe' in command
    assert install_hooks.sys.executable not in command


def test_reinstall_with_changed_path_replaces_in_place_not_appends(tmp_path):
    # A path or interpreter change (e.g. after a Clayrune update) must UPDATE
    # the group this installer owns, never accumulate a second stale one.
    install_hooks.install('qwen', tmp_path, apply=True, guard_script=Path('old/process_guard.py'))
    result = install_hooks.install('qwen', tmp_path, apply=True, guard_script=Path('new/process_guard.py'))

    assert result['changed'] is True
    data = json.loads(Path(result['path']).read_text(encoding='utf-8'))
    entries = data['hooks']['PreToolUse']
    assert len(entries) == 1
    assert 'new' in entries[0]['hooks'][0]['command']
    assert 'old' not in entries[0]['hooks'][0]['command']


def test_claude_coexists_with_untagged_legacy_hook(tmp_path):
    # Simulates Ron's REAL live ~/.claude/settings.json: an existing
    # PreToolUse entry with no "name" tag (the legacy, narrower guard this
    # installer did not write and must never touch or remove).
    settings_path = tmp_path / '.claude' / 'settings.json'
    settings_path.parent.mkdir(parents=True)
    legacy = {
        'hooks': {
            'PreToolUse': [
                {'matcher': 'Bash|PowerShell',
                 'hooks': [{'type': 'command',
                            'command': 'python "C:/Users/levir/.claude/hooks/process-guard.py"',
                            'timeout': 10000}]}
            ]
        }
    }
    settings_path.write_text(json.dumps(legacy), encoding='utf-8')

    result = install_hooks.install('claude', tmp_path, apply=True)

    assert result['changed'] is True
    data = json.loads(settings_path.read_text(encoding='utf-8'))
    entries = data['hooks']['PreToolUse']
    assert len(entries) == 2
    # The legacy entry is byte-for-byte untouched.
    assert entries[0] == legacy['hooks']['PreToolUse'][0]
    assert entries[1]['hooks'][0]['name'] == install_hooks.HOOK_NAME

    # Re-running must update ONLY the tagged group, never duplicate or touch
    # the legacy one.
    result2 = install_hooks.install('claude', tmp_path, apply=True,
                                     guard_script=Path('different/process_guard.py'))
    assert result2['changed'] is True
    data2 = json.loads(settings_path.read_text(encoding='utf-8'))
    entries2 = data2['hooks']['PreToolUse']
    assert len(entries2) == 2
    assert entries2[0] == legacy['hooks']['PreToolUse'][0]
    assert 'different' in entries2[1]['hooks'][0]['command']


def test_codex_writes_hooks_json_not_settings_json(tmp_path):
    result = install_hooks.install('codex', tmp_path, apply=True)
    assert result['changed'] is True
    assert Path(result['path']).name == 'hooks.json'
    assert (tmp_path / '.codex' / 'hooks.json').exists()
    data = json.loads((tmp_path / '.codex' / 'hooks.json').read_text(encoding='utf-8'))
    entries = data['hooks']['PreToolUse']
    assert entries[0]['matcher'] == 'shell'


def test_install_for_boot_only_touches_requested_vendors(tmp_path):
    results = install_hooks.install_for_boot(home=tmp_path, installed_vendors=['gemini'])
    assert [r['vendor'] for r in results] == ['gemini']
    assert (tmp_path / '.gemini' / 'settings.json').exists()
    assert not (tmp_path / '.qwen' / 'settings.json').exists()
    assert not (tmp_path / '.claude' / 'settings.json').exists()
    assert not (tmp_path / '.codex' / 'hooks.json').exists()


def test_install_for_boot_survives_one_vendor_failing(tmp_path):
    bad = tmp_path / '.gemini' / 'settings.json'
    bad.parent.mkdir(parents=True)
    bad.write_text('not json', encoding='utf-8')

    results = install_hooks.install_for_boot(home=tmp_path, installed_vendors=['gemini', 'qwen'])

    by_vendor = {r['vendor']: r for r in results}
    assert 'error' in by_vendor['gemini']
    assert by_vendor['qwen']['changed'] is True
    assert (tmp_path / '.qwen' / 'settings.json').exists()
