"""A saved ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN must not move Claude runs off
the subscription plan.

Settings > Agent Providers writes every provider's saved keys into the server's
os.environ (so Goose/Aider/Qwen inherit theirs) and Claude children inherit the
same environ. Claude Code ranks those two vars above the claude.ai login, so a
key saved for Goose silently put every Claude run on API billing. These tests
pin mc/provider_env_scope.py: the key reaches the runtime it was saved under,
and Claude only when saved under claude or present in the server's own shell.

Status-panel half: static/js/system-status.js warns when the Claude run's
apiKeySource (system/init -> _capture_system_init -> data/system_status.json)
is anything but 'none'. There is no JS harness in the repo, so that part is
driven through node (skipped when node is absent) against the real file.
"""
import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import launch_marker, provider_env_scope as pes  # noqa: E402

KEY = 'ANTHROPIC_API_KEY'
TOKEN = 'ANTHROPIC_AUTH_TOKEN'


@pytest.fixture()
def scope(monkeypatch):
    """Empty scope state, empty original shell env, no guarded vars in os.environ."""
    monkeypatch.setattr(pes, '_saved', {})
    monkeypatch.setattr(pes, '_ORIGINAL_ENV', {})
    monkeypatch.delenv(KEY, raising=False)
    monkeypatch.delenv(TOKEN, raising=False)
    return pes


def _server_saves(monkeypatch, provider, key, value):
    """What agent_provider_set_env / hydrate do: record + write os.environ."""
    pes.note_saved(provider, key, value)
    monkeypatch.setenv(key, value)


# ── env scoping ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('provider', ['goose', 'aider', 'qwen'])
@pytest.mark.parametrize('name', [KEY, TOKEN])
def test_key_saved_under_another_provider_does_not_reach_claude(scope, monkeypatch, provider, name):
    _server_saves(monkeypatch, provider, name, 'sk-not-for-claude')
    assert name not in launch_marker.launch_env('manual')
    assert name not in launch_marker.launch_env('dispatch')
    assert name not in pes.claude_child_env()


@pytest.mark.parametrize('provider', ['goose', 'aider', 'qwen'])
def test_non_claude_runtimes_still_get_their_key(scope, monkeypatch, provider):
    import os
    _server_saves(monkeypatch, provider, KEY, 'sk-for-' + provider)
    # Non-Claude spawn sites inherit os.environ untouched.
    assert os.environ[KEY] == 'sk-for-' + provider
    launch_marker.launch_env('manual')  # building a Claude env must not mutate it
    assert os.environ[KEY] == 'sk-for-' + provider


def test_key_saved_under_claude_reaches_claude(scope, monkeypatch):
    _server_saves(monkeypatch, 'claude', KEY, 'sk-claude')
    assert launch_marker.launch_env('manual')[KEY] == 'sk-claude'
    assert pes.claude_child_env()[KEY] == 'sk-claude'


def test_claude_saved_value_wins_over_another_providers_when_both_saved(scope, monkeypatch):
    _server_saves(monkeypatch, 'claude', KEY, 'sk-claude')
    _server_saves(monkeypatch, 'goose', KEY, 'sk-goose')  # last writer owns os.environ
    assert launch_marker.launch_env('manual')[KEY] == 'sk-claude'


def test_key_in_the_original_shell_env_reaches_claude(scope, monkeypatch):
    monkeypatch.setattr(pes, '_ORIGINAL_ENV', {KEY: 'sk-shell'})
    monkeypatch.setenv(KEY, 'sk-shell')
    pes.note_saved('goose', KEY, 'sk-goose')  # hydrate: shell wins, os.environ unchanged
    assert launch_marker.launch_env('manual')[KEY] == 'sk-shell'


def test_shell_key_is_restored_when_a_provider_save_overwrote_os_environ(scope, monkeypatch):
    monkeypatch.setattr(pes, '_ORIGINAL_ENV', {KEY: 'sk-shell'})
    monkeypatch.setenv(KEY, 'sk-shell')
    _server_saves(monkeypatch, 'goose', KEY, 'sk-goose')  # set_env overwrites os.environ
    assert launch_marker.launch_env('manual')[KEY] == 'sk-shell'


def test_clearing_the_provider_key_removes_the_scoping(scope, monkeypatch):
    _server_saves(monkeypatch, 'goose', KEY, 'sk-goose')
    pes.note_saved('goose', KEY, '')
    monkeypatch.setenv(KEY, 'sk-from-somewhere')
    assert launch_marker.launch_env('manual')[KEY] == 'sk-from-somewhere'


def test_no_provider_keys_saved_is_a_no_op(scope, monkeypatch):
    """Ron's install: provider_env.json holds a gemini key only."""
    pes.note_saved('gemini', 'GEMINI_API_KEY', 'g-key')  # not a guarded name
    monkeypatch.setenv('GEMINI_API_KEY', 'g-key')
    before = dict(__import__('os').environ)
    env = launch_marker.launch_env('manual')
    expected = dict(before)
    expected.pop(launch_marker.LAUNCH_MARKER_ENV, None)
    assert env == expected
    assert pes.claude_child_env() == before


def test_explicit_base_without_saved_keys_is_untouched(scope):
    assert launch_marker.launch_env('manual', base={'A': '1', KEY: 'x'}) == {'A': '1', KEY: 'x'}


def test_case_variants_are_dropped(scope):
    pes.note_saved('goose', KEY, 'sk-goose')
    env = pes.scope_claude_env({'anthropic_api_key': 'sk-goose', 'A': '1'})
    assert env == {'A': '1'}


def test_oneshot_transform_env_is_scoped(scope, monkeypatch):
    from mc import transform_sandbox
    _server_saves(monkeypatch, 'goose', KEY, 'sk-goose')
    env = transform_sandbox.transform_env()
    assert KEY not in env
    assert env.get('CLAUDE_CODE_DISABLE_AUTO_MEMORY') == '1'  # TRANSFORM_ENV still applied


def test_mcp_scan_claude_call_is_scoped(scope, monkeypatch):
    from mc import mcp_installer
    seen = {}

    def fake_run(cmd, **kw):
        seen['env'] = kw['env']
        return subprocess.CompletedProcess(cmd, 0, '', '')

    monkeypatch.setattr(mcp_installer.subprocess, 'run', fake_run)
    _server_saves(monkeypatch, 'aider', KEY, 'sk-aider')
    mcp_installer._run(['claude', '-p', 'x'], claude_child=True)
    assert KEY not in seen['env']
    mcp_installer._run(['git', 'status'])  # non-Claude callers keep the full env
    assert seen['env'][KEY] == 'sk-aider'


# ── in-app restart: the successor must not inherit the hydrated keys ──────

_SUCCESSOR = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
from mc import provider_env_scope as p   # the successor's own import-time snapshot
key, goose = sys.argv[2], sys.argv[3]
p.note_saved('goose', key, goose)        # _hydrate_provider_env_into_os on startup
if key not in os.environ:
    os.environ[key] = goose
print(p.claude_child_env().get(key, ''))
"""


def _successor_claude_key(env):
    """Start a fresh interpreter on `env` (what the relaunched server sees) and
    return the key its Claude child env would carry ('' if none)."""
    r = subprocess.run([sys.executable, '-I', '-c', _SUCCESSOR, str(PROJECT_ROOT), KEY, 'sk-goose-FAKE'],
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_restart_does_not_launder_a_goose_key_into_the_shell_snapshot(scope, monkeypatch):
    _server_saves(monkeypatch, 'goose', KEY, 'sk-goose-FAKE')  # old server, hydrated
    import os
    # Before the fix the successor got os.environ as-is: it reads the key as the shell's own.
    assert _successor_claude_key(dict(os.environ)) == 'sk-goose-FAKE'
    env = pes.relaunch_env()
    assert KEY not in env
    assert _successor_claude_key(env) == ''


def test_restart_keeps_a_genuine_shell_key(scope, monkeypatch):
    monkeypatch.setattr(pes, '_ORIGINAL_ENV', {KEY: 'sk-shell-FAKE'})
    monkeypatch.setenv(KEY, 'sk-shell-FAKE')
    _server_saves(monkeypatch, 'goose', KEY, 'sk-goose-FAKE')  # save route overwrote os.environ
    env = pes.relaunch_env()
    assert env[KEY] == 'sk-shell-FAKE'
    assert _successor_claude_key(env) == 'sk-shell-FAKE'


def test_relaunch_env_drops_unsnapshotted_guarded_names_but_keeps_the_rest(scope, monkeypatch):
    monkeypatch.setenv(TOKEN, 'tok-hydrated-FAKE')
    monkeypatch.setenv('GEMINI_API_KEY', 'g-key')
    env = pes.relaunch_env()
    assert TOKEN not in env and env['GEMINI_API_KEY'] == 'g-key'
    assert KEY not in pes.relaunch_env(base={'anthropic_api_key': 'x', 'A': '1'})


def test_every_server_relaunch_goes_through_relaunch_env():
    def calls_in(rel, fn_name):
        tree = ast.parse((PROJECT_ROOT / rel).read_text(encoding='utf-8'))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == fn_name)
        return {ast.unparse(c.func) for c in ast.walk(fn) if isinstance(c, ast.Call)}
    restart = calls_in('mc/blueprints/system_routes.py', '_perform_server_restart_async')
    assert 'relaunch_env' in restart and 'os.environ.copy' not in restart
    assert 'relaunch_env' in calls_in('mc/mac_update.py', '_spawn_swap')


# ── real wiring: hydrate at startup + the Settings save route ─────────────

@pytest.fixture()
def ar_env(scope, tmp_path, monkeypatch):
    import server  # noqa: F401  (registers the blueprint + runs wire())
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(ar, 'PROVIDER_ENV_PATH', tmp_path / 'provider_env.json')
    return ar, server


def test_hydrate_scopes_saved_keys_per_provider(ar_env, monkeypatch):
    import os
    ar, _ = ar_env
    (ar.PROVIDER_ENV_PATH).write_text(json.dumps({
        'goose': {KEY: 'sk-goose'},
        'gemini': {'GEMINI_API_KEY': 'g-key'},
    }), encoding='utf-8')
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    ar._hydrate_provider_env_into_os()
    assert os.environ[KEY] == 'sk-goose' and os.environ['GEMINI_API_KEY'] == 'g-key'
    env = launch_marker.launch_env('manual')
    assert KEY not in env
    assert env['GEMINI_API_KEY'] == 'g-key'


def test_hydrate_keeps_a_claude_scoped_key_for_claude(ar_env, monkeypatch):
    ar, _ = ar_env
    ar.PROVIDER_ENV_PATH.write_text(json.dumps({'claude': {KEY: 'sk-claude'}}), encoding='utf-8')
    ar._hydrate_provider_env_into_os()
    assert launch_marker.launch_env('manual')[KEY] == 'sk-claude'


@pytest.mark.parametrize('provider,reaches_claude', [('goose', False), ('claude', True)])
def test_settings_save_route_scopes_the_key(ar_env, monkeypatch, provider, reaches_claude):
    import os
    ar, server = ar_env
    with server.app.test_request_context(json={'key': KEY, 'value': 'sk-saved'}):
        resp = ar.agent_provider_set_env(provider)
    assert resp.get_json() == {'ok': True, 'key': KEY}
    assert os.environ[KEY] == 'sk-saved'  # every runtime still inherits it
    assert (KEY in launch_marker.launch_env('manual')) is reaches_claude


# ── every Claude spawn that bills a run carries a scoped env ──────────────

# Spawns that go through launch_marker.launch_env are also pinned by
# tests/test_launch_marker.py's structural guard.
_SCOPED_ENV_CALLS = {'claude_child_env', 'launch_env', 'transform_env'}


def _env_kw_is_scoped(call):
    env = next((k.value for k in call.keywords if k.arg == 'env'), None)
    return (isinstance(env, ast.Call)
            and getattr(env.func, 'attr', getattr(env.func, 'id', None)) in _SCOPED_ENV_CALLS)


def _spawns_in(rel, resolver_names):
    tree = ast.parse((PROJECT_ROOT / rel).read_text(encoding='utf-8'))
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
        if not any(getattr(c.func, 'id', getattr(c.func, 'attr', None)) in resolver_names for c in calls):
            continue
        for c in calls:
            if getattr(c.func, 'attr', None) in ('Popen', 'run') and \
                    getattr(c.func.value, 'id', None) == 'subprocess':
                out.append((rel, fn.name, c.lineno, _env_kw_is_scoped(c)))
    return out


def test_every_claude_spawn_is_scoped():
    resolvers = {'_resolve_claude', '_oneshot_argv', '_stream_argv'}
    by_file = {}
    for rel in ('mc/blueprints/agent_routes.py', 'mc/blueprints/system_routes.py',
                'mc/memory.py', 'mc/agent_runtime.py'):
        by_file[rel] = _spawns_in(rel, resolvers)
        assert by_file[rel], f'{rel}: no Claude spawn found - the walker went blind'
    # A `--version` probe needs no credentials.
    exempt = {'_claude_health_check_hook'}
    bad = [(r, f, ln) for sites in by_file.values() for r, f, ln, ok in sites
           if not ok and f not in exempt]
    assert not bad, f'Claude spawns without a scoped env=: {bad}'


# ── status panel warning ──────────────────────────────────────────────────

_NODE_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[1], 'utf8');
const noop = () => {};
const ctx = {
  console, window: {addEventListener: noop}, esc: s => String(s),
  document: {getElementById: () => null, addEventListener: noop,
             querySelector: () => null, querySelectorAll: () => []},
  _agentProviders: [], localStorage: {getItem: () => null},
  setInterval: noop, setTimeout: noop,
};
vm.createContext(ctx);
vm.runInContext(src, ctx);
const out = {};
for (const [label, s] of Object.entries(JSON.parse(process.argv[2]))) {
  out[label] = ctx._renderStatusTab(s, {}, {color: 'ok', message: 'ok'});
}
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_status_panel_warns_when_claude_is_billed_to_an_api_key():
    cases = {
        'none': {'apiKeySource': 'none', 'captured_at': 't'},
        'unset': {'apiKeySource': '', 'captured_at': 't'},
        'env': {'apiKeySource': 'env', 'captured_at': 't'},
        'user': {'apiKeySource': 'user', 'captured_at': 't'},
    }
    r = subprocess.run(
        ['node', '-e', _NODE_HARNESS, str(PROJECT_ROOT / 'static' / 'js' / 'system-status.js'),
         json.dumps(cases)],
        capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert r.returncode == 0, r.stderr
    html = json.loads(r.stdout)
    warn = 'billed to an API key'
    assert warn not in html['none'] and 'amber' not in html['none'].split('Auth')[1]
    assert warn not in html['unset']
    for src in ('env', 'user'):
        assert warn in html[src]
        assert 'not your subscription plan' in html[src]
        assert f'({src})' in html[src]
        assert 'ssp-v amber' in html[src]  # the file's existing warning style
