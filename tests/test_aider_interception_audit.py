"""Negative coverage evidence for BUILTINS-5b; does not certify an adapter.

Opt in with CLAYRUNE_TEST_AIDER_AUDIT=1 and CLAYRUNE_AIDER_AUDIT_PYTHON pointing
to an isolated Python containing aider-chat==0.86.2. No Aider CLI/model turn,
microphone, real shell command, or operator state is used.
"""
import json
import os
from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get('CLAYRUNE_TEST_AIDER_AUDIT') != '1',
    reason='opt-in installed Aider negative interception audit')


def probe(scenario, tmp_path):
    python = os.environ['CLAYRUNE_AIDER_AUDIT_PYTHON']
    result = subprocess.run(
        [python, str(Path(__file__).with_name('aider_interception_probe.py')),
         scenario, str(tmp_path)],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.splitlines()[-1])
    assert evidence['version'] == '0.86.2', 'Re-audit changed upstream source'
    assert evidence['coder_type'] == 'WholeFileCoder'
    return evidence


def test_candidate_blocks_real_apply_updates(tmp_path):
    result = probe('apply_updates', tmp_path)
    assert result['refused'] and result['unchanged']


@pytest.mark.parametrize('scenario', ['handle_shell', 'cmd_run'])
def test_candidate_blocks_named_shell_paths(tmp_path, scenario):
    result = probe(scenario, tmp_path)
    assert result['refused'] and result['shell_guard_calls'] == ['git push']


def test_prepare_to_edit_can_create_without_text_writer(tmp_path):
    result = probe('prepare_touch', tmp_path)
    assert result['exists'] and not result['refused']


def test_patch_delete_bypasses_text_writer(tmp_path):
    result = probe('patch_delete', tmp_path)
    assert not result['exists'] and not result['refused']


def test_save_bypasses_text_writer(tmp_path):
    result = probe('save', tmp_path)
    assert not result['unchanged'] and not result['refused']


def test_git_bypasses_common_shell_helper(tmp_path):
    result = probe('git', tmp_path)
    assert result['subprocess_calls'] == ['git push']
    assert result['shell_guard_calls'] == []


def test_native_audio_write_bypasses_python_open_audit(tmp_path):
    result = probe('native_voice', tmp_path)
    assert result['exists'] and not result['unchanged'] and not result['refused']
