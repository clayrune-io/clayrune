"""Opt-in real aider-chat 0.86.2 proof, no provider calls or operator state."""
import json
import os
from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get('CLAYRUNE_TEST_AIDER_ADAPTER') != '1', reason='opt-in restricted Aider proof')


def probe(scenario, tmp_path):
    result = subprocess.run(
        [os.environ['CLAYRUNE_AIDER_AUDIT_PYTHON'], '-I', '-B',
         str(Path(__file__).with_name('aider_guard_probe.py')), scenario, str(tmp_path)],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.splitlines()[-1])


@pytest.mark.parametrize('scenario', ['prepare_touch', 'patch_delete', 'save', 'git', 'native_voice'])
def test_five_audited_bypasses_are_refused_before_effect(tmp_path, scenario):
    result = probe(scenario, tmp_path)
    assert result['version'] == '0.86.2'
    assert result['refusals'], result
    assert not result['subprocess_calls']
    if scenario in ('prepare_touch', 'native_voice'):
        assert not result['exists']
    else:
        assert result['unchanged']


@pytest.mark.parametrize('scenario', ['protected_whole', 'protected_diff', 'handle_shell',
                                     'cmd_run', 'subprocess_git', 'gitpython_push',
                                     'direct_patch_delete', 'udiff', 'initial_touch',
                                     'installer', 'browser', 'fence_error'])
def test_accepted_boundaries_and_direct_optional_calls_refuse(tmp_path, scenario):
    result = probe(scenario, tmp_path)
    assert result['refusals'] and result['unchanged'], result
    if scenario in ('handle_shell', 'cmd_run', 'subprocess_git', 'gitpython_push'):
        assert any(tool == 'Bash' and 'push' in args['command']
                   for tool, args, _ in result['checks'])


@pytest.mark.parametrize('command', ['/voice', '/web https://example.invalid', '/save x',
                                    '/git status', '/test echo x', '/lint', '/paste',
                                    '/chat-mode patch', '/run-not-reviewed x', '/r echo x',
                                    '!echo x', '/unknown'])
def test_command_allowlist_is_exact(tmp_path, command):
    result = probe('command:' + command, tmp_path)
    assert result['refusals'] and result['unchanged'], result


@pytest.mark.parametrize('scenario', ['allow_whole', 'allow_diff', 'allow_prepare',
                                     'allow_shell', 'allow_gitpython'])
def test_allowed_real_aider_operations(tmp_path, scenario):
    result = probe(scenario, tmp_path)
    assert not result['refusals'], result
    if scenario in ('allow_whole', 'allow_diff'):
        assert result['allowed_content'] == 'changed\n'
        assert any(tool == 'Write' and args['file_path'].endswith('allowed.txt')
                   for tool, args, _ in result['checks'])


@pytest.mark.parametrize('scenario', ['source_drift', 'resource_drift', 'extension_drift'])
def test_real_installed_tree_drift_refuses(tmp_path, scenario):
    assert 'drift' in probe(scenario, tmp_path)['refused']


def test_import_shadow_refuses_before_loading_other_code(tmp_path):
    result = probe('import_shadow', tmp_path)
    assert 'import-path drift' in result['refused'] and not result['executed']


@pytest.mark.parametrize('message,rc', [('/run echo aider-runner-allowed', 0),
                                       ('/voice', 2), ('/git push', 2), ('', 2)])
def test_real_runner_uses_restricted_entry_not_general_cli(tmp_path, message, rc):
    message_path = tmp_path / 'message.md'
    message_path.write_text(message, encoding='utf-8')
    # A general Aider CLI would load this config and attempt GUI / voice.
    (tmp_path / '.aider.conf.yml').write_text('gui: true\nvoice-format: wav\n')
    env = os.environ.copy()
    env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path))
    runner = Path(__file__).resolve().parent.parent / 'mc' / 'aider_runner.py'
    result = subprocess.run([os.environ['CLAYRUNE_AIDER_AUDIT_PYTHON'], '-I', '-B',
                             str(runner), '--message-file', str(message_path)],
                            cwd=tmp_path, env=env, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=40)
    assert result.returncode == rc, result.stdout + result.stderr
    if rc:
        assert 'refused' in result.stderr
        assert (message.split()[0] if message else 'empty task') in result.stderr
    else:
        assert 'aider-runner-allowed' in result.stdout


def test_real_selected_install_preflight_and_launch(tmp_path, monkeypatch):
    from mc import aider_launch, state, vendor_fence
    monkeypatch.setitem(state.CONFIG, aider_launch.FLAG, True)
    monkeypatch.setitem(state.CONFIG, 'fence_unattended_enabled', True)
    python = Path(os.environ['CLAYRUNE_AIDER_AUDIT_PYTHON'])
    binary = python.with_name('aider.exe' if os.name == 'nt' else 'aider')
    assert binary.is_file()
    task = tmp_path / 'task.md'
    task.write_text('/run echo preflight-and-runner-allowed', encoding='utf-8')
    env = os.environ.copy()
    env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path))
    cmd = vendor_fence.prepare_command('aider', {'trigger_type': 'schedule'},
                                       [str(binary), '--message-file', str(task)], str(tmp_path), env)
    result = subprocess.run(cmd, cwd=tmp_path, env=env, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'preflight-and-runner-allowed' in result.stdout
