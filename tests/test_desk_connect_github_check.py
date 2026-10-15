"""MC-1095: a repository whose README documents a required key must ask for it, and Save must say
whether the server actually starts (initialize + tools/list), in the server's own words when not."""
import json
import sys

import pytest
from flask import Flask

from mc import mcp_installer as installer
from mc.desk_connect import custom_connection_store as store
from mc.desk_connect import github_activation as activation
from mc.desk_connect import github_required_keys as credentials
from mc.desk_connect import github_start_check as start_check

REAL_HANDSHAKE = start_check.handshake      # conftest stubs the module attribute for every test

# The shape of github.com/ZubeidHendricks/youtube-mcp-server @36142e3 that recorded no credential:
# a bash block BEFORE the JSON config, three fallback key slots, a VS Code `servers` example and
# an .env.example. Values are the repository's placeholders, never real keys.
README = '''# YouTube MCP Server

## Installation

```bash
npm install -g zubeid-youtube-mcp-server
```

2. Add to your Claude Desktop configuration:

```json
{
  "mcpServers": {
    "zubeid-youtube-mcp-server": {
      "command": "zubeid-youtube-mcp-server",
      "env": {
        "YOUTUBE_API_KEY": "your_primary_youtube_api_key",
        "YOUTUBE_API_KEY2": "your_secondary_youtube_api_key",
        "YOUTUBE_API_KEY3": "your_tertiary_youtube_api_key"
      }
    }
  }
}
```

## Configuration
* `YOUTUBE_TRANSCRIPT_LANG`: Default language for transcripts
'''


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / 'youtube-mcp-server'
    d.mkdir()
    (d / 'README.md').write_text(README, encoding='utf-8')
    (d / '.mcp.json.example').write_text(json.dumps({'servers': {'youtube-mcp': {
        'type': 'stdio', 'command': 'node', 'args': ['dist/cli.js'], 'env': {'YOUTUBE_API_KEY': 'your-api-key-here'}}}}))
    (d / '.env.example').write_text('YOUTUBE_API_KEY=your_primary_api_key_here\nYOUTUBE_API_KEY2=your_secondary_api_key_here\n'
                                    '# YOUTUBE_TRANSCRIPT_LANG=en\nLOG_LEVEL=info\n')
    return d


# ── 1. root cause: the README config was never seen ─────────────────────────

def test_fences_pair_line_by_line_after_a_bash_block():
    blocks = installer.json_fences(README)
    assert len(blocks) == 1 and '"mcpServers"' in blocks[0]


def test_fences_skip_non_json_languages_and_tilde_fences():
    text = '~~~bash\nnpm i\n~~~\n\n```jsonc\n{"a": 1}\n```\n````\n```\n{"b": 2}\n````\n'
    assert installer.json_fences(text) == ['{"a": 1}', '```\n{"b": 2}']


def test_readme_config_after_bash_block_is_extracted(repo):
    extracted = installer.extract_config(str(repo), allow_claude_fallback=False)
    assert extracted['source_tier'] == 2
    assert set(extracted['servers']['zubeid-youtube-mcp-server']['env']) == {'YOUTUBE_API_KEY', 'YOUTUBE_API_KEY2', 'YOUTUBE_API_KEY3'}


@pytest.mark.parametrize('name,value,expected', [
    ('YOUTUBE_API_KEY', 'your_primary_youtube_api_key', True),     # qualified placeholder (was missed)
    ('YOUTUBE_API_KEY2', '', True),                                  # numbered fallback slot (was missed)
    ('YOUTUBE_API_KEY', '${input:apiKey}', True),                     # VS Code input reference
    ('GITHUB_TOKEN', '<token>', True),
    ('YOUTUBE_TRANSCRIPT_LANG', 'en', False),
    ('API_KEY', 'sk-live-looks-real', False),                         # a non-placeholder value is not asked for
    ('LOG_LEVEL', '', False),
])
def test_credential_slot_rules(name, value, expected):
    assert installer.is_credential_slot(name, value) is expected


# ── 2. the setup asks for the documented key ─────────────────────────────────

def test_documented_key_is_asked_and_fallbacks_are_optional(repo):
    configs = installer.extract_config(str(repo), allow_claude_fallback=False)['servers']
    found = credentials.detect(str(repo), configs)
    assert [c['env'] for c in found['ask']] == ['YOUTUBE_API_KEY']
    assert [c['env'] for c in found['optional']] == ['YOUTUBE_API_KEY2', 'YOUTUBE_API_KEY3']
    assert 'README.md' in found['ask'][0]['source'] and '.mcp.json.example' in found['ask'][0]['source']


def test_detection_returns_names_never_values(repo):
    out = json.dumps(credentials.detect(str(repo), installer.extract_config(str(repo), allow_claude_fallback=False)['servers']))
    assert 'your' not in out and 'here' not in out and 'LOG_LEVEL' not in out and 'TRANSCRIPT' not in out


def test_env_example_alone_is_enough(tmp_path):
    (tmp_path / '.env.example').write_text('export SERVICE_TOKEN=\nSERVICE_URL=https://example.invalid\n')
    assert credentials.detect(str(tmp_path)) == {'ask': [{'env': 'SERVICE_TOKEN', 'source': '.env.example'}], 'optional': []}


def test_numbered_slot_documented_first_still_asks_the_base(tmp_path):
    (tmp_path / '.env.example').write_text('KEY_API_KEY2=\nKEY_API_KEY=\n')
    assert [c['env'] for c in credentials.detect(str(tmp_path))['ask']] == ['KEY_API_KEY']


def test_nothing_documented_asks_nothing(tmp_path):
    (tmp_path / 'README.md').write_text('# x\n\n```bash\nnpm start\n```\n')
    assert credentials.detect(str(tmp_path)) == {'ask': [], 'optional': []}


# ── 3. the check: initialize + tools/list against a real child process ──────

FAKE_SERVER = r'''
import json, sys, time
mode = sys.argv[1]
if mode == 'die':
    sys.stderr.write('Error: at least one YouTube API key is required. Set YOUTUBE_API_KEY.\n'); sys.exit(1)
if mode == 'hang':
    time.sleep(60)
for line in sys.stdin:
    m = json.loads(line)
    if m.get('method') == 'initialize':
        out = ({'error': {'code': -32600, 'message': 'unsupported client'}} if mode == 'refuse'
               else {'result': {'protocolVersion': '2025-06-18', 'capabilities': {}, 'serverInfo': {'name': 'fixture'}}})
    elif m.get('method') == 'tools/list':
        page2 = (m.get('params') or {}).get('cursor') == 'p2'
        out = {'result': {'tools': [{'name': 'c'}]} if page2 else {'tools': [{'name': 'a'}, {'name': 'b'}], 'nextCursor': 'p2'}}
    else:
        continue
    print('log line that is not JSON-RPC', flush=True)
    print(json.dumps({'jsonrpc': '2.0', 'id': m['id'], **out}), flush=True)
'''


@pytest.fixture
def fake(tmp_path):
    p = tmp_path / 'fake_mcp.py'
    p.write_text(FAKE_SERVER, encoding='utf-8')
    return lambda mode: [sys.executable, str(p), mode]


def test_working_server_counts_every_page_of_tools(fake):
    raw = REAL_HANDSHAKE(fake('ok'), None, 20)
    assert raw == {'ok': True, 'tools': 3, 'server': 'fixture'}
    assert start_check.summary(raw)['message'] == 'Working: 3 tools.'


def test_server_that_exits_reports_its_own_reason(fake):
    raw = REAL_HANDSHAKE(fake('die'), None, 20)
    assert raw['code'] == 'exited' and 'YouTube API key is required' in raw['reason']
    s = start_check.summary(raw)
    assert s['status'] == 'failed'
    assert s['message'].startswith('Not working: the server stopped before it answered. It said: "Error: at least one YouTube')


def test_silent_server_times_out_and_is_stopped(fake):
    raw = REAL_HANDSHAKE(fake('hang'), None, 1.5)
    assert raw == {'ok': False, 'code': 'no_answer', 'reason': ''}
    assert 'did not answer' in start_check.summary(raw)['message']


def test_initialize_error_is_a_refusal(fake):
    raw = REAL_HANDSHAKE(fake('refuse'), None, 20)
    assert raw['code'] == 'refused' and raw['reason'] == 'unsupported client'


def test_missing_program_is_a_launch_failure(tmp_path):
    assert REAL_HANDSHAKE([str(tmp_path / 'nope.exe')], None, 5)['code'] == 'launch_failed'


def test_reason_is_bounded_printable_text():
    assert start_check._reason('a\x1b[31mred\x07\n' + 'x' * 900) == ('a [31mred ' + 'x' * 900)[:300]


# ── 4. wiring: Save runs the registered command, state stays `registered` ────

def _op(tmp_path, name='srv'):
    return {'scope': {'kind': 'global', 'project_id': None}, 'server_name': name, 'directory': str(tmp_path),
            'credentials': [], 'ecosystem': 'github', 'x': 1}


def test_check_runs_the_registered_gate_command_and_records_by_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'path', lambda: tmp_path / 'approvals.json')
    op = _op(tmp_path)
    from mc.desk_connect import custom_connection_operation as operation
    store.put(op, operation.fingerprint(op), 'registered')
    seen = []
    monkeypatch.setattr(start_check, 'handshake', lambda argv, cwd, deadline_s=0, scrub=None: seen.append((argv, cwd)) or {'ok': False, 'code': 'exited', 'reason': 'key missing'})
    out = start_check.check(op)
    cfg = activation.launch_config(op)
    assert seen == [([cfg['command'], *cfg['args']], str(tmp_path))]
    assert out['status'] == 'failed' and 'key missing' in out['message']
    rec = store.get('global', None, 'srv')
    assert rec['state'] == 'registered'                        # the gate starts only a registered record
    assert start_check.recorded(rec)['message'] == out['message']
    op2 = {**op, 'x': 2}
    store.put(op2, operation.fingerprint(op2), 'registered')   # a new approval starts unchecked
    assert start_check.recorded(store.get('global', None, 'srv')) is None


def test_derive_state_reports_the_check_in_place_of_not_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(activation.npm, '_read', lambda *a: activation.launch_config(rec['operation']))
    monkeypatch.setattr(activation, 'problem', lambda op: '')
    monkeypatch.setattr(activation.secrets_store, 'list_secrets', lambda *a: [])
    rec = {'operation': _op(tmp_path), 'scope': 'global', 'project_id': None, 'server_name': 'srv', 'state': 'registered',
           'fingerprint': 'sha256:f'}
    assert activation.derive_state(rec, None)['message'] == 'Registered; not checked.'
    rec['start_check'] = {'status': 'working', 'tools': 10, 'message': 'Working: 10 tools.', 'fingerprint': 'sha256:f'}
    state = activation.derive_state(rec, None)
    assert state['state'] == 'registered' and state['message'] == 'Working: 10 tools.' and state['check']['tools'] == 10


def test_check_route_is_human_only_and_needs_a_registered_repository(tmp_path, monkeypatch):
    from mc.blueprints import desk_connect_custom_routes as routes
    monkeypatch.setattr(store, 'path', lambda: tmp_path / 'approvals.json')
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    client = app.test_client()
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: True)
    assert client.post('/api/desk/connect/custom/github/check', json={'scope': 'global', 'server_name': 'srv'}).status_code == 403
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: False)
    assert client.post('/api/desk/connect/custom/github/check', json={'scope': 'global', 'server_name': 'srv'}).status_code == 404
    op = _op(tmp_path)
    from mc.desk_connect import custom_connection_operation as operation
    store.put(op, operation.fingerprint(op), 'registered')
    monkeypatch.setattr(activation, 'derive_state', lambda rec, path: {'state': 'changed'})
    assert client.post('/api/desk/connect/custom/github/check', json={'scope': 'global', 'server_name': 'srv'}).status_code == 409
    monkeypatch.setattr(activation, 'derive_state', lambda rec, path: {'state': 'registered'})
    r = client.post('/api/desk/connect/custom/github/check', json={'scope': 'global', 'server_name': 'srv'})
    assert r.status_code == 200 and r.get_json()['message'] == 'Working: 0 tools.'
