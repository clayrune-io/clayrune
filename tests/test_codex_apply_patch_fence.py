"""MC-975 follow-up (2026-09-25): Codex's apply_patch was not fenced.

Measured on codex-cli 0.155.1 with a stdin-dumping PreToolUse probe: the hook
DOES fire for apply_patch, with the payload shape pinned below (copied from
the probe log, session-specific ids dropped). Both probe writes, one of them
into `.claude/`, went through because the fence only knew Write/Edit.
"""
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mc import guardrail_hooks as gh

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'steward'))
import fence  # noqa: E402

PS = shutil.which('powershell.exe') if os.name == 'nt' else None


def _patch(*lines):
    return {'command': '\n'.join(['*** Begin Patch', *lines, '*** End Patch'])}


def _payload(tool_input, cwd, tool_name='apply_patch'):
    """The measured Codex shape, minus session ids."""
    return json.dumps({'cwd': str(cwd), 'hook_event_name': 'PreToolUse',
                       'permission_mode': 'bypassPermissions',
                       'tool_name': tool_name, 'tool_input': tool_input})


MEASURED_DOTCLAUDE = _patch('*** Add File: .claude/settings.local.json', '+{}')
MEASURED_PLAIN = _patch('*** Add File: patched.txt', '+hello')


def test_patch_paths_cover_every_header():
    ti = _patch('*** Add File: a.txt', '+x', '*** Update File: src/b.py',
                '*** Move to: src/c.py', '@@', '-y', '+z', '*** Delete File: d.txt')
    assert fence.patch_target_paths(ti) == ['a.txt', 'src/b.py', 'src/c.py', 'd.txt']
    crlf = {'command': MEASURED_DOTCLAUDE['command'].replace('\n', '\r\n')}
    assert fence.patch_target_paths(crlf) == ['.claude/settings.local.json']


def test_relative_paths_resolve_against_the_session_cwd(tmp_path):
    calls = fence.as_write_calls('apply_patch', MEASURED_DOTCLAUDE, str(tmp_path))
    assert calls == [('Write', {'file_path': str(tmp_path / '.claude' / 'settings.local.json')})]


@pytest.mark.parametrize('ti', [
    MEASURED_DOTCLAUDE,
    _patch('*** Update File: .claude/settings.json'),
    _patch('*** Delete File: .claude/settings.json'),
    _patch('*** Update File: notes.md', '*** Move to: .claude/skills/x/SKILL.md'),
    _patch('*** Add File: ok.txt', '+x', '*** Add File: steward/fence.py', '+x'),
    _patch('*** Update File: data/skills/_proposed/x/SKILL.md'),
])
def test_classify_blocks_protected_patch_targets(ti):
    assert fence.classify_action('apply_patch', ti).blocked


@pytest.mark.parametrize('ti', [MEASURED_PLAIN, _patch('*** Update File: src/app.py'), {}])
def test_classify_allows_ordinary_patches(ti):
    assert not fence.classify_action('apply_patch', ti).blocked


def test_shell_heredoc_patch_is_classified_too():
    cmd = "apply_patch <<'EOF'\n" + MEASURED_DOTCLAUDE['command'] + "\nEOF"
    calls = fence.as_write_calls('Bash', {'command': cmd}, '/p')
    assert calls[0] == ('Bash', {'command': cmd})
    assert any('.claude' in c[1].get('file_path', '') for c in calls[1:])


def _main(monkeypatch, payload, argv):
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(payload))
    return fence.main(argv)


def test_armed_main_blocks_measured_dotclaude_patch(monkeypatch, tmp_path, capsys):
    assert _main(monkeypatch, _payload(MEASURED_DOTCLAUDE, tmp_path), ['--armed']) == 2
    assert 'STEWARD FENCE blocked' in capsys.readouterr().err
    assert _main(monkeypatch, _payload(MEASURED_PLAIN, tmp_path), ['--armed']) == 0


def test_install_dir_guard_applies_to_patches_unarmed(monkeypatch, tmp_path, capsys):
    """The project-boundary rule is unconditional for Write; same for a patch.
    The hook runs in the session's cwd, which must not be the install itself."""
    monkeypatch.chdir(tmp_path)
    target = (REPO / 'mc' / 'x.py').as_posix()
    ti = _patch(f'*** Update File: {target}')
    assert _main(monkeypatch, _payload(ti, tmp_path), []) == 2
    assert 'Clayrune install directory' in capsys.readouterr().err


@pytest.mark.skipif(not PS, reason='Codex hook shell is PowerShell on Windows only')
def test_armed_patch_block_survives_the_codex_hook_shell(tmp_path):
    cmd = gh.codex_fence_hook_command(armed=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith('CLAUDE_CODE')}
    assert PS
    r = subprocess.run([PS, '-NoProfile', '-Command', cmd], cwd=tmp_path,
                       input=_payload(MEASURED_DOTCLAUDE, tmp_path), capture_output=True,
                       text=True, env=env, timeout=60)
    assert r.returncode == 2 and '.claude' in r.stderr, (r.returncode, r.stderr)
