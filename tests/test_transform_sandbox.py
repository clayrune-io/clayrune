"""Claude tool-free transforms run in a neutral cwd with auto-memory/CLAUDE.md off.

Pins mc/transform_sandbox.py: a project cwd made every isolated oneshot load that
project's CLAUDE.md + auto-memory (14,042 vs 6,687 prompt tokens, measured
2026-10-06); the env switches take the floor to 4,279.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mc.agent_runtime as ar
from mc import transform_sandbox as ts


def _project_dir(tmp_path):
    d = tmp_path / 'proj'
    d.mkdir()
    (d / 'CLAUDE.md').write_text('project rules', encoding='utf-8')
    return str(d)


def _fake_run(calls):
    def fake(cmd, **kw):
        calls.append({'cmd': cmd, **kw})
        r = MagicMock()
        r.returncode = 0
        r.stdout = 'ok'
        r.stderr = ''
        return r
    return fake


def test_neutral_cwd_is_created_under_clayrune_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(ts, '_cached', None)
    path = Path(ts.neutral_cwd())
    assert path == tmp_path / '.clayrune' / ts.NEUTRAL_DIRNAME
    assert path.is_dir() and not any(path.iterdir())


def test_neutral_cwd_recreated_if_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(ts, '_cached', None)
    first = Path(ts.neutral_cwd())
    first.rmdir()
    assert Path(ts.neutral_cwd()).is_dir()


def test_transform_env_keeps_parent_env_and_sets_switches(monkeypatch):
    monkeypatch.setenv('SOME_PARENT_VAR', 'x')
    env = ts.transform_env()
    assert env['SOME_PARENT_VAR'] == 'x'
    assert env['CLAUDE_CODE_DISABLE_AUTO_MEMORY'] == '1'
    assert env['CLAUDE_CODE_DISABLE_CLAUDE_MDS'] == '1'


def test_oneshot_ignores_caller_project_cwd(tmp_path):
    proj = _project_dir(tmp_path)
    calls = []
    with patch('subprocess.run', side_effect=_fake_run(calls)):
        res = ar.ClaudeRuntime().oneshot(prompt='p', stdin_text='body', cwd=proj)
    assert res is not None and res.text == 'ok'
    assert calls[0]['cwd'] == ts.neutral_cwd() != proj
    assert calls[0]['env']['CLAUDE_CODE_DISABLE_AUTO_MEMORY'] == '1'
    assert calls[0]['env']['CLAUDE_CODE_DISABLE_CLAUDE_MDS'] == '1'


def test_oneshot_brief_still_travels_in_the_prompt(tmp_path):
    """The brief is the model's only source of project context -- it must come
    from the prompt, never from the cwd."""
    calls = []
    with patch('subprocess.run', side_effect=_fake_run(calls)):
        ar.ClaudeRuntime().oneshot(prompt='INSTR', system_prompt='BRIEF',
                                   stdin_text='body', cwd=_project_dir(tmp_path))
    sent = calls[0]['input']
    assert sent.startswith('BRIEF\n\nINSTR') and 'body' in sent


def test_stream_text_uses_neutral_cwd_and_env(tmp_path):
    proj = _project_dir(tmp_path)
    seen = {}

    class FakeProc:
        stdin = io.StringIO()
        stdout = io.StringIO('')
        stderr = io.StringIO('')
        returncode = 0

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    def fake_popen(cmd, **kw):
        seen.update(kw)
        return FakeProc()

    with patch('subprocess.Popen', side_effect=fake_popen):
        list(ar.ClaudeRuntime().stream_text(prompt='p', cwd=proj))
    assert seen['cwd'] == ts.neutral_cwd() != proj
    assert seen['env']['CLAUDE_CODE_DISABLE_AUTO_MEMORY'] == '1'


def test_describe_image_uses_neutral_cwd_and_env(tmp_path):
    img = tmp_path / 'a.png'
    img.write_bytes(b'\x89PNG')
    calls = []
    rt = ar.ClaudeRuntime()
    with patch.object(ar, '_authorize_text_transform', return_value=None), \
            patch('subprocess.run', side_effect=_fake_run(calls)):
        rt.describe_image(str(img), prompt='what')
    assert calls[0]['cwd'] == ts.neutral_cwd()
    assert calls[0]['env']['CLAUDE_CODE_DISABLE_AUTO_MEMORY'] == '1'
