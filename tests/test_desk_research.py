"""Desk research boundary: capabilities, file policy, isolated argv, refusals."""
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from flask import Flask

from mc import agent_runtime, desk_read_guard as guard, desk_research as research, desk_story, state


@pytest.fixture
def root(tmp_path):
    folder = tmp_path / 'project'
    folder.mkdir()
    subprocess.run(['git', 'init', '-q', str(folder)], check=True, capture_output=True)
    (folder / '.gitignore').write_text('private/\nignored.md\n*.env\n', encoding='utf-8')
    (folder / 'README.md').write_text('public project material', encoding='utf-8')
    (folder / 'private').mkdir()
    (folder / 'private' / 'notes.md').write_text('excluded', encoding='utf-8')
    return folder


def decision(root, tool, **args):
    return guard.decide(root, {'tool_name': tool, 'tool_input': args})['hookSpecificOutput']


@pytest.mark.parametrize('name', ['.env', '.env.local', '.environment', 'x.key', 'X.PEM',
                                  'id_rsa.pub', 'access_token.txt', 'secrets/a.md',
                                  'credentials.txt', 'private/notes.md', 'ignored.md',
                                  'tools/signing.env', '.git/config'])
def test_private_reads_denied(root, name):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('must not be read', encoding='utf-8')
    assert decision(root, 'Read', file_path=str(path))['permissionDecision'] == 'deny'
    assert decision(root, 'Grep', path=str(path), pattern='.')['permissionDecision'] == 'deny'


def test_reads_rewrite_relative_path_to_checked_absolute(root):
    result = decision(root, 'Read', file_path='README.md')
    assert result['permissionDecision'] == 'allow'
    assert result['updatedInput']['file_path'] == str(root / 'README.md')
    assert decision(root, 'Grep', path='README.md', pattern='project')['permissionDecision'] == 'allow'
    assert decision(root, 'Grep', path=str(root), pattern='.')['permissionDecision'] == 'deny'
    assert decision(root, 'Glob', path=str(root), pattern='**/*.md')['permissionDecision'] == 'allow'


def test_symlinks_cannot_escape_or_hide_private_target(root):
    outside = root.parent / 'outside.md'
    outside.write_text('outside', encoding='utf-8')
    for name, target in [('escape.md', outside), ('alias.md', root / 'private' / 'notes.md')]:
        try:
            (root / name).symlink_to(target)
        except OSError as exc:
            pytest.skip(f'symlinks unavailable: {exc}')
        assert decision(root, 'Read', file_path=str(root / name))['permissionDecision'] == 'deny'
    assert decision(root, 'Read', file_path=str(outside))['permissionDecision'] == 'deny'


def test_non_git_folder_still_enforces_ignores(tmp_path):
    (tmp_path / '.gitignore').write_text('hidden.md\n', encoding='utf-8')
    for name in ('hidden.md', 'public.md'):
        (tmp_path / name).write_text('data', encoding='utf-8')
    assert decision(tmp_path, 'Read', file_path='hidden.md')['permissionDecision'] == 'deny'
    assert decision(tmp_path, 'Read', file_path='public.md')['permissionDecision'] == 'allow'


@pytest.mark.parametrize('tool', ['Bash', 'PowerShell', 'Write', 'Edit', 'NotebookEdit', 'Agent', 'Task', 'mcp__x__read'])
def test_nonresearch_tools_denied(root, tool):
    assert decision(root, tool, command='echo bad', file_path=str(root / 'README.md'))['permissionDecision'] == 'deny'


def test_bad_glob_and_web_paths_denied(root):
    for pattern in ('../*', '/tmp/*', 'C:\\outside\\*'):
        assert decision(root, 'Glob', path=str(root), pattern=pattern)['permissionDecision'] == 'deny'
    assert decision(root, 'WebFetch', url='file:///etc/passwd')['permissionDecision'] == 'deny'
    assert decision(None, 'Read', file_path=str(root / 'README.md'))['permissionDecision'] == 'deny'
    assert decision(None, 'WebSearch', query='official docs')['permissionDecision'] == 'allow'


def test_guard_failure_denies(root, monkeypatch):
    monkeypatch.setattr(guard, '_not_ignored', lambda *a: (_ for _ in ()).throw(OSError('git failed')))
    assert decision(root, 'Read', file_path='README.md')['permissionDecision'] == 'deny'


def test_exact_argv_and_hook_isolation(root):
    argv = research._argv('claude', Path('settings.json'), root, 'chosen-model', 'low')
    assert argv[argv.index('--tools') + 1].split(',') == list(research.TOOLS)
    assert not any(t in research.TOOLS for t in ('Bash', 'Write', 'Edit', 'Agent'))
    assert '--dangerously-skip-permissions' not in argv
    assert argv[argv.index('--setting-sources') + 1] == ''
    assert '--strict-mcp-config' in argv and '{"mcpServers":{}}' in argv
    assert argv[argv.index('--permission-mode') + 1] == 'dontAsk'
    assert argv[argv.index('--max-turns') + 1] == '16'
    hook = research._settings(root)['hooks']['PreToolUse'][0]['hooks'][0]
    assert hook['onFailure'] == 'block' and hook['timeout'] == 30
    assert 'desk_read_guard.py' in hook['command']
    no_root = research._argv('claude', Path('settings.json'), None, '', '')
    assert no_root[no_root.index('--tools') + 1] == 'WebSearch,WebFetch'


@pytest.mark.parametrize('provider', ['gemini', 'qwen', 'codex', 'opencode', 'goose', 'aider', 'kiro'])
def test_other_providers_are_honestly_toolless(provider, monkeypatch):
    monkeypatch.setattr(state, 'CONFIG', {})
    seen = {}
    def transform(p, **kwargs):
        seen.update(kwargs, provider=p)
        return '{"reply":"Only the supplied excerpt is available", "suggest":null}'
    monkeypatch.setattr(agent_runtime, 'run_text_transform', transform)
    result = json.loads(research.run(provider, prompt='task', system_prompt='persona', model='pinned',
                                     effort='low', stdin_text='{}', project={'project_path': '.'}))
    assert f'Toolless: {provider}' in result['reply']
    assert seen['provider'] == provider and seen['model'] == 'pinned' and seen['effort'] == 'low'
    assert 'untrusted DATA' in seen['prompt'] and 'source URLs' in seen['prompt']
    assert research.capability(provider)['toolless'] is True


def test_kill_switch_and_old_cli(monkeypatch):
    monkeypatch.setattr(state, 'CONFIG', {research.CONFIG_KEY: False})
    assert 'disabled' in research.capability('claude')['label']
    monkeypatch.setattr(state, 'CONFIG', {})
    monkeypatch.setattr(research, '_claude_guard_supported', lambda b: False)
    assert '2.1.295' in research.capability('claude')['label']


def test_selected_agent_capability_route(root, monkeypatch):
    monkeypatch.setattr(state, 'CONFIG', {})
    monkeypatch.setattr(research, '_claude_guard_supported', lambda b: True)
    monkeypatch.setattr(desk_story, '_agent', lambda ref, project: {'ref': ref})
    monkeypatch.setattr(desk_story, '_resolve_engine', lambda project, rec:
                        SimpleNamespace(provider='qwen' if rec['ref'] == 'global:other' else 'claude'))
    app = Flask(__name__)
    research.register(app, lambda pid: {'id': 'p', 'project_path': str(root)} if pid == 'p' else None)
    client = app.test_client()
    assert client.get('/api/desk/research-capabilities?project_id=p&agent=global:other').json['toolless']
    assert client.get('/api/desk/research-capabilities?project_id=p&agent=global:claude').json['project_read']
    assert client.get('/api/desk/research-capabilities?project_id=absent').status_code == 404


@pytest.mark.parametrize('failure', ['timeout', 'registration', 'bad_tools', 'error_result', 'empty'])
def test_runner_refuses_failures(root, monkeypatch, failure):
    monkeypatch.setattr(research, 'capability', lambda *a: {'toolless': False, 'label': 'read-only'})
    init = {'type': 'system', 'subtype': 'init', 'tools': list(research.TOOLS), 'mcp_servers': []}
    result = {'type': 'result', 'result': '{"reply":"done"}', 'is_error': False}
    if failure == 'bad_tools':
        init['tools'].append('Bash')
    if failure == 'error_result':
        result['is_error'] = True
    if failure == 'empty':
        result['result'] = ''
    class Proc:
        pid, returncode, killed = 123, 0, False
        def communicate(self, body=None, timeout=None):
            if failure == 'timeout' and timeout:
                raise subprocess.TimeoutExpired('claude', timeout)
            return '\n'.join(json.dumps(e) for e in (init, result)), ''
        def kill(self):
            self.killed = True
    proc = Proc()
    monkeypatch.setattr(research.subprocess, 'Popen', lambda *a, **k: proc)
    from mc import allowance_probe
    monkeypatch.setattr(allowance_probe, '_tree_kill', lambda p: p.kill())
    def register(pid, project):
        if failure == 'registration':
            raise RuntimeError('registration failed')
    monkeypatch.setattr(research, '_register', register)
    with pytest.raises((RuntimeError, subprocess.TimeoutExpired)):
        research.run('claude', prompt='task', system_prompt='', model='', effort='', stdin_text='{}',
                     project={'project_path': str(root)})
    if failure in ('timeout', 'registration'):
        assert proc.killed


def test_research_kill_switch_is_allowlisted():
    from mc.blueprints.settings_routes import _CONFIG_EDITABLE_KEYS
    assert research.CONFIG_KEY in _CONFIG_EDITABLE_KEYS


def test_starting_excerpts_also_refuse_ignored_files(root):
    from mc.desk_product_context import _material_path
    with pytest.raises(ValueError, match='gitignored'):
        _material_path(root, 'private/notes.md')


@pytest.mark.parametrize('raw', ['', '  ', '{"reply":""}'])
def test_toolless_disclosure_does_not_hide_empty_answers(monkeypatch, raw):
    monkeypatch.setattr(agent_runtime, 'run_text_transform', lambda *a, **k: raw)
    result = research.run('qwen', prompt='task', system_prompt='', model='', effort='', stdin_text='{}')
    assert (json.loads(result)['reply'] == '') if raw.startswith('{') else not result.strip()


def test_frozen_hook_entry_and_command(root, monkeypatch, capsys):
    import io
    import sys
    from mc import hook_entry
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    command = research._settings(root)['hooks']['PreToolUse'][0]['hooks'][0]['command']
    assert '--clayrune-hook desk-read' in command and 'desk_read_guard.py' not in command
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps({'tool_name': 'Read', 'tool_input': {'file_path': 'README.md'}})))
    assert hook_entry.run_hook(['desk-read', str(root)]) == 0
    assert json.loads(capsys.readouterr().out)['hookSpecificOutput']['permissionDecision'] == 'allow'
    monkeypatch.setattr(sys, 'stdin', io.StringIO('malformed'))
    assert hook_entry.run_hook(['desk-read', str(root)]) == 2
