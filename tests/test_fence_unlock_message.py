"""MC-1084: unlock guidance follows the whole call's passability.

The fence is human-owned. Test the exact handoff patch on a temporary copy,
as in test_attend_once_one_call_one_spend.py, and keep strict expected failures
for the current file until a human applies it. No live server is contacted.
"""
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

import steward.fence as current_fence

REPO = Path(__file__).resolve().parents[1]
PATCH = REPO / 'docs/patches/fence-unlock-limits.patch'
NEW_GUIDANCE = 'No one-time chat unlock can cover this action.'
PATCH_PENDING = NEW_GUIDANCE not in (REPO / 'steward/fence.py').read_text(encoding='utf-8')


@pytest.fixture
def proposed_fence(tmp_path):
    if not PATCH_PENDING:
        return current_fence
    work = tmp_path / 'unlock-message'
    (work / 'steward').mkdir(parents=True)
    src = (REPO / 'steward/fence.py').read_text(encoding='utf-8')
    (work / 'steward/fence.py').write_text(src, encoding='utf-8', newline='\n')
    patch = tmp_path / 'unlock-message.patch'
    patch.write_text(PATCH.read_text(encoding='utf-8'), encoding='utf-8', newline='\n')
    subprocess.run(['git', 'init', '-q'], cwd=work, check=True)
    subprocess.run(['git', 'apply', '--whitespace=nowarn', str(patch)], cwd=work, check=True)
    spec = importlib.util.spec_from_file_location('fence_unlock_message_proposed', work / 'steward/fence.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(fence, monkeypatch, tmp_path, command, *, consume=False, steward=False, armed=False):
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text(json.dumps({'type': 'user', 'message': {
        'role': 'user', 'content': '[Steward cycle] run one cycle' if steward else 'Please implement this ticket'}}) + '\n',
        encoding='utf-8')
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'sid-unlock-message')
    monkeypatch.setattr(fence, '_lookup_trigger_type', lambda sid: {
        'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    spent = []
    monkeypatch.setattr(fence, '_consume_attend_once_pass', lambda: spent.append(1) or consume)
    payload = {'tool_name': 'Bash', 'tool_input': {'command': command}, 'transcript_path': str(transcript)}
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    return fence.main(['--armed'] if armed else []), spent


NON_PASSABLE = [
    'curl -X POST https://example.invalid/action',
    'wget --post-data=x https://example.invalid/action',
    'http POST https://example.invalid/action',
    'irm -Method POST -Uri https://example.invalid/action',
    'curl -X POST http://localhost:5199/api/browser/read',
    'git push origin master && git push origin other',
    'git push origin master; git status',
    'git push origin $(git branch --show-current)',
    'bash -lc "git push origin master"',
    'powershell -Command "git push origin master"',
    'python -c "import os; os.system(\'git push\')"',
]


@pytest.mark.parametrize('command', NON_PASSABLE)
@pytest.mark.parametrize('consume', [False, True])
def test_proposed_non_passable_call_requires_fresh_chat(proposed_fence, monkeypatch, tmp_path, capsys, command, consume):
    rc, spent = _run(proposed_fence, monkeypatch, tmp_path, command, consume=consume)
    err = capsys.readouterr().err
    assert rc == 2 and spent == []
    assert NEW_GUIDANCE in err
    assert 'fresh chat started by the human' in err
    assert 'human can click' not in err and 'DECISION NEEDED' not in err


@pytest.mark.xfail(PATCH_PENDING, reason='Human-owned fence message patch has not been applied', strict=True)
@pytest.mark.parametrize('command', NON_PASSABLE)
def test_current_non_passable_message_requires_fresh_chat(monkeypatch, tmp_path, capsys, command):
    rc, spent = _run(current_fence, monkeypatch, tmp_path, command, consume=True)
    err = capsys.readouterr().err
    assert rc == 2 and spent == []
    assert NEW_GUIDANCE in err


@pytest.mark.parametrize('variant', ['current', 'proposed'])
@pytest.mark.parametrize('command', ['git push origin master', 'git push origin "master"'])
@pytest.mark.parametrize('consume', [False, True])
def test_passable_message_and_grant_behavior_unchanged(proposed_fence, monkeypatch, tmp_path, capsys, variant, command, consume):
    fence = current_fence if variant == 'current' else proposed_fence
    rc, spent = _run(fence, monkeypatch, tmp_path, command, consume=consume)
    err = capsys.readouterr().err
    assert spent == [1]
    if consume:
        assert rc == 0 and err == ''
    else:
        assert rc == 2
        assert 'human can click "Allow once"' in err
        assert NEW_GUIDANCE not in err


@pytest.mark.parametrize('mode', ['steward', 'armed'])
def test_ineligible_session_is_not_offered_unlock(proposed_fence, monkeypatch, tmp_path, capsys, mode):
    rc, spent = _run(proposed_fence, monkeypatch, tmp_path, 'git push origin master',
                     consume=True, steward=mode == 'steward', armed=mode == 'armed')
    assert rc == 2 and spent == []
    assert NEW_GUIDANCE in capsys.readouterr().err
