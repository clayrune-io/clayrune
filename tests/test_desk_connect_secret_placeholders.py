"""Wren's audit of the 2026-10-15 Desk package setup: README text must not reach the credential
wrapper as a `{{secret:..}}` placeholder (credentials flow only through the approved list), a
server's stderr must be scrubbed before it is stored, and /connections is human-only."""
import json
import sys

import pytest
from flask import Flask

from mc import secrets_store as vault
from mc import mcp_installer as installer
from mc.blueprints import desk_connect_custom_routes as routes
from mc.desk_connect import custom_connection_operation as operation
from mc.desk_connect import custom_connection_store as store
from mc.desk_connect import github_connection as github
from mc.desk_connect import github_start_command
from mc.desk_connect import github_start_check as start_check
from mc.desk_connect.mcp_errors import ActivationError
from tests.test_desk_connect_github import staged, prepare  # noqa: F401 — reuse the staging fixture

REAL_HANDSHAKE = start_check.handshake      # conftest stubs the module attribute for every test

PLACEHOLDERS = ['{{secret:github}}', '--token={{secret:github.token}}', '{{ secret: github }}', '{{user:reddit}}',
                '{{totp:github.totp}}', '{{SECRET:x}}']


# ── 1. placeholders are refused in what a person submits ────────────────────

@pytest.mark.parametrize('text', PLACEHOLDERS)
def test_args_and_entry_refuse_vault_placeholders(text):
    with pytest.raises(ActivationError) as e:
        operation.clean_fields({'args': ['--ok', text], 'scope': 'global'}, {'github'}, default_name='x')
    assert e.value.code == 'secret_placeholder' and 'Credentials section' in str(e.value)
    with pytest.raises(ActivationError) as e:
        operation.clean_fields({'entry': text, 'scope': 'global'}, {'github'}, default_name='x')
    assert e.value.code == 'secret_placeholder'


def test_plain_arguments_and_braces_still_pass():
    out = operation.clean_fields({'args': ['--port', '8080', '{"a":1}', '{{ not a vault reference }}'], 'scope': 'global'},
                                 set(), default_name='x')
    assert out['args'] == ['--port', '8080', '{"a":1}', '{{ not a vault reference }}']


@pytest.mark.parametrize('text', PLACEHOLDERS)
def test_repository_review_refuses_placeholders_in_command_and_args(staged, text):
    with pytest.raises(ActivationError) as e:
        prepare(staged[0], args=['server.js', text])
    assert e.value.code == 'secret_placeholder'
    with pytest.raises(ActivationError) as e:
        prepare(staged[0], command=text)
    assert e.value.code == 'secret_placeholder'


# ── 2. a README's placeholder never reaches the form ────────────────────────

@pytest.mark.parametrize('args', [['server.js', '--token', '{{secret:github}}'], ['{{user:me}}']])
def test_readme_placeholder_args_are_not_prefilled(staged, monkeypatch, args):
    monkeypatch.setattr(installer, 'extract_config', lambda *a, **k: {'servers': {'s': {'command': 'node', 'args': args}}})
    monkeypatch.setattr(github_start_command, 'infer', lambda *a: {})
    answer = github.stage({'url': 'https://github.com/ZubeidHendricks/youtube-mcp-server'})
    assert answer['command'] == '' and answer['args'] == [] and answer['manual_command']
    assert '{{' not in json.dumps(answer)


def test_readme_placeholder_command_is_not_prefilled(staged, monkeypatch):
    monkeypatch.setattr(installer, 'extract_config', lambda *a, **k: {'servers': {'s': {'command': '{{secret:x}}', 'args': []}}})
    monkeypatch.setattr(github_start_command, 'infer', lambda *a: {})
    answer = github.stage({'url': 'https://github.com/ZubeidHendricks/youtube-mcp-server'})
    assert answer['command'] == '' and answer['manual_command']


# ── 3. `pinned` is true only for a command that runs the saved repository ───

@pytest.mark.parametrize('command,args', [('npx', []), ('pnpx', []), ('bunx', []), ('bunx.cmd', []), ('pnpm', ['dlx', 'p']),
                                          ('yarn', ['dlx', 'p']), ('bun', ['x', 'p']), (r'C:\Tools\pnpm.CMD', ['dlx', 'p'])])
def test_registry_launchers_are_detected(command, args):
    assert github_start_command.registry_launcher(command, args)


@pytest.mark.parametrize('command,args', [('node', ['server.js']), ('pnpm', ['start']), ('yarn', ['run', 'serve']),
                                          ('bun', ['run', 'server.js']), ('python', ['-m', 'server'])])
def test_commands_that_run_the_repository_are_not(command, args):
    assert not github_start_command.registry_launcher(command, args)


@pytest.mark.parametrize('command,args', [('bunx', ['server.js']), ('pnpx', ['x']), ('pnpm', ['dlx', 'x']), ('bun', ['x', 'y'])])
def test_review_refuses_launchers_so_the_card_cannot_claim_pinned(staged, command, args):
    with pytest.raises(ActivationError) as e:
        prepare(staged[0], command=command, args=args)
    assert e.value.code == 'unpinned_command'
    assert prepare(staged[0], command='node')['package']['pinned'] is True


def test_launcher_in_readme_config_is_not_prefilled(staged, monkeypatch):
    monkeypatch.setattr(installer, 'extract_config', lambda *a, **k: {'servers': {'s': {'command': 'pnpm', 'args': ['dlx', 'x']}}})
    monkeypatch.setattr(github_start_command, 'infer', lambda *a: {})
    answer = github.stage({'url': 'https://github.com/ZubeidHendricks/youtube-mcp-server'})
    assert answer['command'] == '' and answer['manual_command']


# ── 4. the start check scrubs what the server wrote before storing it ───────

SECRET = 'correct-horse-battery'               # not token-shaped: only the value-based redaction can catch it
TOKEN = 'ghp_' + 'a1B2c3D4' * 4                # token-shaped: caught by the pattern scrub even with no vault value

LEAKY = r'''
import sys
sys.stderr.write(sys.argv[1] + '\n'); sys.exit(1)
'''


@pytest.fixture
def dispensed(monkeypatch):
    """The vault hands out SECRET for the entry `github.key`, as it does for the real wrapper."""
    def get(name, **kw):
        assert kw['consumer'] == 'desk-start-check'
        vault.register_dispensed(name, SECRET)
        return SECRET
    monkeypatch.setattr(vault, 'get_secret_value', get)
    yield
    vault._forget_dispensed(SECRET)


def _op():
    return {'scope': {'kind': 'global', 'project_id': None}, 'credentials': [{'env': 'API_KEY', 'vault': 'github.key'}]}


def test_scrubber_redacts_the_approved_value_and_token_shaped_words(dispensed):
    scrub = start_check.scrubber(_op())
    out = scrub(f'bad key {SECRET} and {TOKEN} but config.json is missing')
    assert SECRET not in out and TOKEN not in out
    assert 'config.json is missing' in out and 'bad key' in out        # the useful part survives


def test_scrubber_survives_an_unreadable_credential(monkeypatch):
    def get(*a, **k):
        raise vault.SecretsError('locked')
    monkeypatch.setattr(vault, 'get_secret_value', get)
    out = start_check.scrubber(_op())(f'x {TOKEN} y')
    assert TOKEN not in out and out.startswith('x ') and out.endswith(' y')


def test_secret_split_by_the_length_cut_is_still_removed(tmp_path, dispensed):
    """The reason is cut to 300 characters; scrubbing after the cut would keep the first characters of a
    secret that straddles it."""
    script = tmp_path / 'leaky.py'
    script.write_text(LEAKY, encoding='utf-8')
    line = 'e' * 290 + ' ' + SECRET + ' tail'
    raw = REAL_HANDSHAKE([sys.executable, str(script), line], None, 20, scrub=start_check.scrubber(_op()))
    assert raw['code'] == 'exited' and SECRET not in raw['reason'] and SECRET[:6] not in raw['reason']
    unscrubbed = REAL_HANDSHAKE([sys.executable, str(script), line], None, 20)
    assert SECRET[:6] in unscrubbed['reason']                         # control: the cut really does keep a prefix


def test_check_stores_and_returns_the_scrubbed_reason(tmp_path, monkeypatch, dispensed):
    monkeypatch.setattr(store, 'path', lambda: tmp_path / 'approvals.json')
    op = {**_op(), 'server_name': 'srv', 'directory': str(tmp_path), 'ecosystem': 'github'}
    store.put(op, operation.fingerprint(op), 'registered')
    monkeypatch.setattr(start_check, 'handshake', lambda argv, cwd, deadline_s=0, scrub=None: {
        'ok': False, 'code': 'exited', 'reason': scrub(f'API_KEY={SECRET} rejected, token {TOKEN}')})
    out = start_check.check(op)
    stored = json.dumps(store.get('global', None, 'srv')['start_check'])
    for text in (json.dumps(out), stored):
        assert SECRET not in text and TOKEN not in text and 'rejected' in text


# ── 5. /connections is human-only ───────────────────────────────────────────

def test_connections_route_refuses_an_unattended_caller(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    client = app.test_client()
    monkeypatch.setattr(routes._service, 'connections', lambda path_of: [{'server_name': 'srv', 'state': 'registered'}])
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: True)
    r = client.post('/api/desk/connect/custom/connections', json={})
    assert r.status_code == 403 and 'srv' not in r.get_data(as_text=True)
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: False)
    r = client.post('/api/desk/connect/custom/connections', json={})
    assert r.status_code == 200 and r.get_json()['connections'][0]['server_name'] == 'srv'
