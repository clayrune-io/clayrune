"""Request-level tests for the agent-dispatch family
(mc/blueprints/agent_routes.py).

Added with blueprint step 1.12 (MODERNIZATION_PLAN.md Phase 5). 1.12 is the
largest extraction (the agent dispatch/stream/followup/transcript/usage/
provider-auth surface, 33 routes). It is a pure move: the route handlers are
byte-verbatim, the memory/scribe/condense machinery STAYS in server.py and is
late-bound into the blueprint via wire(). These tests therefore guard the move
itself rather than re-deriving behavior already covered by
test_auth_routes.py (provider auth), test_auto_model_router.py (the router),
and test_telemetry.py (/api/usage shape):

  - REGISTRATION PARITY: every route the family is supposed to own is present
    on app.url_map under the agent_routes blueprint — the single guard that
    catches a broken register_blueprint()/wire() seam (a move's worst silent
    failure).
  - read-only endpoint smokes on loopback (providers / usage / router-stats /
    recent-runs) prove wire() actually bound the global-scope deps.
  - the app-wide local_auth gate still covers the moved routes (same auth
    contract as 1.8/1.9/1.11): a non-loopback peer with no passcode → 401.

Determinism: patches mc.blueprints.agent_routes.* ONLY (the Phase-0 test-port
rule — never server.*). DATA_DIR is pointed at an empty tmp dir so the usage /
router-stats / recent-runs globs see a clean slate. agent_sessions (a mc.state
object shared with the blueprint by import) is snapshot/cleared/restored in
place — the 1.8 cross-test-pollution lesson.
"""
import io
import json
import os
import sys
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LAN = {'REMOTE_ADDR': '192.168.1.50'}

# The exact route surface 1.12 owns. A change here is intentional API churn and
# must be made deliberately — that is the point of pinning it.
EXPECTED_ROUTES = {
    '/api/agent/providers',
    '/api/agent/upload-image',
    '/api/agent/<provider>/auth-login',
    '/api/agent/<provider>/auth-login-remote',
    '/api/agent/<provider>/auth-login-remote/code',
    '/api/agent/<provider>/auth-login-remote/status',
    '/api/agent/<provider>/auth-logout',
    '/api/agent/<provider>/allowance/recheck',
    '/api/agent/<provider>/auth-probe',
    '/api/agent/<provider>/auth-status',
    '/api/agent/provider/<name>/auth',
    '/api/agent/provider/<name>/env',
    '/api/agent/provider/<name>/login-launch',
    '/api/agent/provider/<name>/install-launch',
    '/api/agent/providers/install-launch',
    '/api/claude/auth-probe',
    '/api/claude/auth-status',
    '/api/claude/login-launch',
    '/api/plan-file',
    '/api/plans/delete',
    # Documents tab (MC-939): broadens the plans-only read gate to also
    # cover markdown an agent wrote for the project (not just ~/.claude/plans/).
    '/api/document-file',
    '/api/recent-runs',
    '/api/router/stats',
    '/api/usage',
    '/api/project/<project_id>/agent/dispatch',
    '/api/project/<project_id>/agent/followup',
    '/api/project/<project_id>/agent/guardian-reset',
    '/api/project/<project_id>/agent/interrupt',
    '/api/project/<project_id>/agent/log',
    '/api/project/<project_id>/agent/plan-file',
    '/api/project/<project_id>/agent/send',
    '/api/project/<project_id>/agent/<session_id>/model',
    '/api/project/<project_id>/agent/session',
    '/api/project/<project_id>/agent/status',
    '/api/project/<project_id>/agent/stop',
    '/api/project/<project_id>/agent/stream',
    '/api/project/<project_id>/agent/delegation/inbox',
    '/api/project/<project_id>/agent/delegation/retry',
    '/api/project/<project_id>/agent/delegation/status',
    '/api/project/<project_id>/agent/delegation/status-list',
    '/api/project/<project_id>/conversations',
    # Conversation redesign (2026-07-11): full-transcript fetch for the resume
    # preview, transcript repair, and cross-project chat search.
    '/api/project/<project_id>/conversation/<claude_session_id>',
    '/api/project/<project_id>/plans',
    '/api/project/<project_id>/documents',
    '/api/project/<project_id>/search-chats',
    '/api/project/<project_id>/session/<session_id>/reconstruct',
    '/api/project/<project_id>/transcript/<claude_session_id>',
    '/api/project/<project_id>/transcript/<claude_session_id>/reconstruct',
    # In-chat search: the complete, uncapped transcript buffer for the
    # currently-open conversation, so a client-side find can reach matches
    # older than what's rendered/buffered client-side. Deliberately not
    # 409-gated on a live session like .../reconstruct — search must work on a
    # conversation that's still running.
    '/api/project/<project_id>/transcript/<claude_session_id>/full-buffer',
    '/api/project/<project_id>/workflows',
    '/api/search/global',
    # MC-923: server-side unattended-context lookup for mc.secrets_store —
    # given a claude_session_id (the Claude Code CLI's own, not caller-typed),
    # returns the trigger_type MC recorded for it at dispatch time.
    '/api/session/trigger-type',
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client; agent_routes global-scope deps patched on the MODULE."""
    import server  # noqa: F401  (registers the blueprint + runs wire() on import)
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import local_auth as la

    # Deterministic gate: no LAN passcode configured this run (loopback exempt,
    # LAN rejected). Path points at a non-existent file.
    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    # Empty data dir so the usage/router-stats/recent-runs globs are clean.
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data_dir)

    # F6: the install-launch routes now touch the PowerShell execution policy
    # on win32. No test may read or WRITE the real one (this box's, in CI or
    # on Ron's machine) — default to "already RemoteSigned, nothing to do",
    # and make any accidental write loud. F6 tests override both.
    monkeypatch.setattr(ar, '_read_powershell_execution_scopes',
                        lambda: {'CurrentUser': 'RemoteSigned'})

    def _no_real_policy_write():
        raise AssertionError('test attempted to write the real execution policy')
    monkeypatch.setattr(ar, '_set_powershell_execution_policy_remotesigned',
                        _no_real_policy_write)

    # mc.state.agent_sessions is a shared object (blueprint imports it) —
    # snapshot, clear, restore IN PLACE; never rebind (split-brain).
    sess_snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()

    server.app.config['TESTING'] = True
    try:
        yield server.app.test_client()
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(sess_snapshot)


# ── registration parity — the move's load-bearing guard ───────────────────────

def test_blueprint_registered(client):
    import server
    assert 'agent_routes' in server.app.blueprints


def test_all_expected_routes_present_under_blueprint(client):
    """Every 1.12 route exists AND is owned by the agent_routes blueprint."""
    import server
    owned = {
        rule.rule for rule in server.app.url_map.iter_rules()
        if rule.endpoint.startswith('agent_routes.')
    }
    missing = EXPECTED_ROUTES - owned
    assert not missing, f'routes missing from agent_routes blueprint: {sorted(missing)}'


def test_no_unexpected_agent_routes(client):
    """Pin the surface: a NEW route under the blueprint must be added to
    EXPECTED_ROUTES deliberately (guards accidental scope creep on re-merge)."""
    import server
    owned = {
        rule.rule for rule in server.app.url_map.iter_rules()
        if rule.endpoint.startswith('agent_routes.')
    }
    extra = owned - EXPECTED_ROUTES
    assert not extra, f'unpinned routes under agent_routes blueprint: {sorted(extra)}'


class _InstallHealth:
    installed = False
    binary_path = None
    version = None
    auth_state = None
    install_hint = 'npm install -g @openai/codex'


class _InstallRuntime:
    def health_check(self):
        return _InstallHealth()


def test_install_launch_onboards_missing_node_before_provider(monkeypatch, client):
    """Regression for the original fresh-machine failure: npm was absent,
    so install-launch returned `npm not found` without opening anything.
    The repair command is fixed/provider-scoped and is only tested with mocks.
    """
    from mc.blueprints import agent_routes as ar
    calls = []
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: _InstallRuntime())
    monkeypatch.setattr(ar.shutil, 'which', lambda name: None if name == 'npm' else '/x/' + name)
    monkeypatch.setattr(ar.sys, 'platform', 'win32')
    monkeypatch.setattr(ar, '_launch_install_terminal',
                        lambda command: (calls.append(command) or 'sess-1', None))

    # Exact pre-fix behavior: the route stopped here and never opened an
    # onboarding terminal. Keep the reproduction beside the regression so a
    # future simplification cannot quietly restore the dead end.
    legacy_required = ar._install_command_required_binary(_InstallHealth.install_hint)
    assert legacy_required == 'npm'
    assert not ar.shutil.which(legacy_required)

    response = client.post('/api/agent/provider/codex/install-launch')
    assert response.status_code == 200
    body = response.get_json()
    assert body['ok'] is True
    assert body['prerequisite'] == 'npm'
    assert body['session_id'] == 'sess-1'
    assert calls == [
        'set "PATH=%ProgramFiles%\\nodejs;%APPDATA%\\npm;%PATH%" '
        '&& (where npm >nul 2>&1 || winget install --id OpenJS.NodeJS.LTS '
        '-e --silent --source winget '
        '--accept-source-agreements --accept-package-agreements) '
        '&& for /f "tokens=1 delims=." %v in (\'npm -v\') do '
        '(if %v GEQ 12 (npm install -g --allow-scripts=@openai/codex @openai/codex) '
        'else (npm install -g @openai/codex))'
    ]


class _BatchInstallRuntime:
    def __init__(self, hint):
        self._hint = hint

    def health_check(self):
        h = _InstallHealth()
        h.install_hint = self._hint
        return h


def test_install_launch_batch_runs_node_prereq_once(monkeypatch, client):
    """F7 (clean-VM run 2026-09-18): "Install selected" used to call the
    single-provider route once per vendor, each opening its OWN terminal —
    ticking Claude + Gemini launched two concurrent `winget install ...
    NodeJS` calls that raced each other. The batch route must compose ONE
    terminal command with the Node/npm prerequisite embedded exactly ONCE,
    even though both selected vendors need it.
    """
    from mc.blueprints import agent_routes as ar
    runtimes = {
        'codex': _BatchInstallRuntime('npm install -g @openai/codex'),
        'gemini': _BatchInstallRuntime('npm install -g @google/gemini-cli'),
    }
    calls = []
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: runtimes[name])
    monkeypatch.setattr(ar.shutil, 'which', lambda name: None if name == 'npm' else '/x/' + name)
    monkeypatch.setattr(ar.sys, 'platform', 'win32')
    monkeypatch.setattr(ar, '_launch_install_terminal',
                        lambda command: (calls.append(command) or 'sess-1', None))

    response = client.post('/api/agent/providers/install-launch',
                           json={'names': ['codex', 'gemini']})
    assert response.status_code == 200
    body = response.get_json()
    assert body['ok'] is True
    assert body['unsupported'] == []
    assert sorted(body['installed']) == ['codex', 'gemini']
    assert body['session_id'] == 'sess-1'
    assert len(calls) == 1, 'must open exactly one terminal for the whole batch'
    command = calls[0]
    # The node-install snippet appears exactly once, and both packages'
    # install lines are chained after it in one sequential command.
    assert command.count('winget install --id OpenJS.NodeJS.LTS') == 1
    assert 'npm install -g @openai/codex' in command
    assert 'npm install -g @google/gemini-cli' in command
    assert command.index('winget install --id OpenJS.NodeJS.LTS') < command.index('npm install -g @openai/codex')


def test_install_launch_batch_skips_unsupported_names(monkeypatch, client):
    """An untrusted/malformed hint is skipped (reported in `unsupported`)
    rather than aborting the whole batch — same fail-closed discipline as
    the single-provider route's 'unsupported' rejection."""
    from mc.blueprints import agent_routes as ar
    runtimes = {
        'codex': _BatchInstallRuntime('npm install -g @openai/codex'),
        'evil': _BatchInstallRuntime('npm install -g @openai/codex; curl https://evil.invalid'),
    }
    calls = []
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: runtimes[name])
    monkeypatch.setattr(ar.shutil, 'which', lambda name: '/x/' + name)  # npm present
    monkeypatch.setattr(ar, '_launch_install_terminal',
                        lambda command: (calls.append(command) or 'sess-1', None))

    response = client.post('/api/agent/providers/install-launch',
                           json={'names': ['codex', 'evil']})
    assert response.status_code == 200
    body = response.get_json()
    assert body['ok'] is True
    assert body['unsupported'] == ['evil']
    assert body['installed'] == ['codex']
    assert calls == ['npm install -g @openai/codex']


def test_install_launch_rejects_untrusted_hint_when_npm_missing(monkeypatch, client):
    from mc.blueprints import agent_routes as ar

    class Runtime:
        def health_check(self):
            h = _InstallHealth()
            h.install_hint = 'npm install -g @openai/codex; curl https://evil.invalid'
            return h

    launched = []
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: Runtime())
    monkeypatch.setattr(ar.shutil, 'which', lambda name: None)
    monkeypatch.setattr(ar, '_launch_install_terminal',
                        lambda command: (launched.append(command) or None, 'unreachable'))
    response = client.post('/api/agent/provider/codex/install-launch')
    assert response.status_code == 200
    assert response.get_json()['ok'] is False
    assert 'unsupported' in response.get_json()['error']
    assert launched == []


def test_install_launch_onboards_missing_pip_before_aider(monkeypatch, client):
    """Fenn blocker #5, pip half: a clean machine has neither pip nor uv, and
    Aider's install_hint requires pip. Before this fix the route returned
    'pip not found on PATH' without opening anything, mirroring the original
    npm/Node dead end above.
    """
    from mc.blueprints import agent_routes as ar

    class Runtime:
        def health_check(self):
            h = _InstallHealth()
            h.install_hint = 'pip install aider-chat  # or: uv tool install aider-chat'
            return h

    calls = []
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: Runtime())
    monkeypatch.setattr(ar.shutil, 'which', lambda name: None)
    monkeypatch.setattr(ar.sys, 'platform', 'win32')
    monkeypatch.setattr(ar, '_launch_install_terminal',
                        lambda command: (calls.append(command) or 'sess-1', None))

    response = client.post('/api/agent/provider/aider/install-launch')
    assert response.status_code == 200
    body = response.get_json()
    assert body['ok'] is True
    assert body['prerequisite'] == 'pip'
    assert calls == [
        'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" '
        '&& set "PATH=%USERPROFILE%\\.local\\bin;%PATH%" '
        '&& uv tool install aider-chat'
    ]


def test_install_launch_prefers_existing_uv_over_bootstrap(monkeypatch, client):
    """If uv is already on PATH (but pip is not), reuse it instead of
    re-bootstrapping — same 'don't invent, don't repeat work' discipline as
    the npm branch's "already have npm" short-circuit."""
    from mc.blueprints import agent_routes as ar

    class Runtime:
        def health_check(self):
            h = _InstallHealth()
            h.install_hint = 'pip install aider-chat  # or: uv tool install aider-chat'
            return h

    calls = []
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: Runtime())
    monkeypatch.setattr(ar.shutil, 'which', lambda name: '/x/uv' if name == 'uv' else None)
    monkeypatch.setattr(ar, '_launch_install_terminal',
                        lambda command: (calls.append(command) or 'sess-1', None))

    response = client.post('/api/agent/provider/aider/install-launch')
    assert response.status_code == 200
    body = response.get_json()
    assert body['ok'] is True
    assert body['prerequisite'] is None
    assert calls == ['uv tool install aider-chat']


def test_install_launch_rejects_untrusted_hint_when_pip_missing(monkeypatch, client):
    from mc.blueprints import agent_routes as ar

    class Runtime:
        def health_check(self):
            h = _InstallHealth()
            h.install_hint = 'pip install aider-chat; curl https://evil.invalid'
            return h

    launched = []
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: Runtime())
    monkeypatch.setattr(ar.shutil, 'which', lambda name: None)
    monkeypatch.setattr(ar, '_launch_install_terminal',
                        lambda command: (launched.append(command) or None, 'unreachable'))
    response = client.post('/api/agent/provider/aider/install-launch')
    assert response.status_code == 200
    assert response.get_json()['ok'] is False
    assert 'unsupported' in response.get_json()['error']
    assert launched == []


# ── read-only loopback smokes — prove wire() bound the global deps ────────────

def test_providers_endpoint_ok(client):
    resp = client.get('/api/agent/providers')
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), (list, dict))


def test_providers_endpoint_reports_remote_login_and_probe_cost(client, monkeypatch):
    """The unified provider row (walkthrough.js _renderProviderRow) is data
    driven: remote_login tells the single Sign in button (provider-auth.js
    settingsProviderTerminalLogin) whether /auth-login-remote can handle this
    vendor (URL-capture or a real PTY) before it falls back to the host
    terminal window; the Check status tooltip discloses quota spend from
    capabilities.auth_probe_spends_quota — which used to be on the dataclass
    but never serialised, so the client could not see it."""
    from mc import pty_backend
    monkeypatch.setattr(pty_backend, 'pty_available', lambda: False)
    providers = {p['name']: p for p in client.get('/api/agent/providers').get_json()['providers']}
    assert providers['claude']['remote_login'] is True       # `claude auth login` pipes its URL
    assert providers['gemini']['remote_login'] is False      # needs a PTY, none available
    assert providers['gemini']['capabilities']['auth_probe_spends_quota'] is True
    assert providers['claude']['capabilities']['auth_probe_spends_quota'] is False
    monkeypatch.setattr(pty_backend, 'pty_available', lambda: True)
    providers = {p['name']: p for p in client.get('/api/agent/providers').get_json()['providers']}
    assert providers['gemini']['remote_login'] is True       # a real PTY covers every CLI


def test_providers_endpoint_reports_allowance_exhausted(client, tmp_path):
    """VENDOR_AGNOSTIC_PROGRAM.md §4 item 4: the chooser must show the SAME
    fact a dispatch call would refuse on — 'allowance_exhausted' comes off
    mc.allowance_state, not a second, drifting notion of quota exhaustion."""
    from mc import allowance_state as al
    al.wire(tmp_path / 'allowance_state.json')
    try:
        al.record_exhaustion('codex', limit_kind='usage_limit',
                             resets_at_display='Sep 24, 2026 7:58 AM')
        resp = client.get('/api/agent/providers')
        assert resp.status_code == 200
        providers = resp.get_json()['providers']
        codex = next(p for p in providers if p['name'] == 'codex')
        claude = next(p for p in providers if p['name'] == 'claude')
        assert codex['allowance_exhausted'] == \
            'Out of allowance, resets Sep 24, 2026 7:58 AM'
        assert claude['allowance_exhausted'] == ''
    finally:
        al._STATE = {}


def test_providers_endpoint_reports_in_use(client, monkeypatch):
    """The `default` provider is always in_use; providers nobody touches
    aren't. Backs the auth-banner suppression in provider-auth.js.

    Patches load_projects/list_characters — this repo's own real project/
    character data (e.g. the market-scout character pins gemini) would
    otherwise leak into `in_use` and make the assertion environment-dependent."""
    from mc.blueprints import agent_routes as ar
    import mc.characters as _chars
    monkeypatch.setattr(ar, 'load_projects', lambda: [])
    monkeypatch.setattr(_chars, 'list_characters', lambda **kw: [])
    monkeypatch.setattr(ar._agent_runtime, 'claude_installed', lambda: True)
    resp = client.get('/api/agent/providers')
    body = resp.get_json()
    by_name = {p['name']: p for p in body['providers']}
    assert by_name['claude']['in_use'] is True   # unset default_provider → claude
    assert 'gemini' in by_name and by_name['gemini']['in_use'] is False


def test_providers_endpoint_refresh_forces_auth_probe(client, monkeypatch):
    """F8 (clean-VM run 2026-09-18): Claude was signed in — `claude auth
    status` showed loggedIn:true — but /api/agent/providers kept reporting
    not_logged_in until the server restarted, because health_check() only
    reads the in-memory auth cache and nothing had re-probed it since the
    sign-in happened outside Clayrune. `?refresh=1` (walkthrough.js
    wtRefreshProviders, the "Check setup status" button) must call
    auth_probe() — the one thing every runtime guarantees actually
    re-checks — instead of serving health_check()'s cached auth_state.
    """
    from mc.blueprints import agent_routes as ar
    rt = ar._agent_runtime.get_runtime('claude')
    probe_calls = []

    monkeypatch.setattr(rt, 'health_check', lambda: ar._agent_runtime.HealthStatus(
        installed=True, binary_path=Path('/x/claude'), version='1.0',
        auth_state=ar._agent_runtime.AuthState(status='not_logged_in', last_checked='t0'),
    ))

    def fake_auth_probe():
        probe_calls.append(1)
        return {'ok': True, 'status': 'ok', 'method': 'session',
                'error_text': None, 'last_checked': 't1'}
    monkeypatch.setattr(rt, 'auth_probe', fake_auth_probe)

    resp = client.get('/api/agent/providers')
    assert resp.status_code == 200
    claude = next(p for p in resp.get_json()['providers'] if p['name'] == 'claude')
    assert claude['auth_status'] == 'not_logged_in'
    assert probe_calls == [], 'a plain GET must not spend a live probe'

    resp2 = client.get('/api/agent/providers?refresh=1')
    assert resp2.status_code == 200
    claude2 = next(p for p in resp2.get_json()['providers'] if p['name'] == 'claude')
    assert claude2['auth_status'] == 'ok'
    assert probe_calls == [1]


# ── _providers_in_use() — resolver precedence for the auth-banner gate ────────
# Isolated from the real project/character stores: `client` only patches
# ar.DATA_DIR (read by the usage/router-stats globs), not the separately-wired
# `load_projects` reference or the global characters dir — so these patch
# ar.load_projects and mc.characters.list_characters directly (the function
# does `from mc import characters as _chars` internally, which resolves the
# same live module object patched here).

def test_providers_in_use_includes_project_pin(client, monkeypatch):
    from mc.blueprints import agent_routes as ar
    import mc.characters as _chars
    monkeypatch.setattr(ar, 'load_projects', lambda: [{'id': 'p1', 'provider': 'codex'}])
    monkeypatch.setattr(_chars, 'list_characters', lambda **kw: [])
    monkeypatch.setattr(ar._agent_runtime, 'claude_installed', lambda: True)
    in_use = ar._providers_in_use()
    assert 'codex' in in_use
    assert 'claude' in in_use  # still the unset-config default


def test_providers_in_use_includes_global_character_pin(client, monkeypatch):
    from mc.blueprints import agent_routes as ar
    import mc.characters as _chars
    monkeypatch.setattr(ar, 'load_projects', lambda: [])

    def _fake_list_characters(project_path=None, project_id=None, **kw):
        if project_path is None:
            return [{'name': 'reviewer', 'engine': {'provider': 'gemini'}}]
        return []
    monkeypatch.setattr(_chars, 'list_characters', _fake_list_characters)
    in_use = ar._providers_in_use()
    assert 'gemini' in in_use


def test_providers_in_use_excludes_untouched_provider(client, monkeypatch):
    from mc.blueprints import agent_routes as ar
    import mc.characters as _chars
    monkeypatch.setattr(ar, 'load_projects', lambda: [{'id': 'p1'}])  # no pin
    monkeypatch.setattr(_chars, 'list_characters', lambda **kw: [])
    monkeypatch.setattr(ar._agent_runtime, 'claude_installed', lambda: True)
    in_use = ar._providers_in_use()
    assert in_use == {'claude'}  # nothing pinned aider/gemini/etc → not in_use


def test_usage_endpoint_ok_empty(client):
    """Clean data dir → usage responds 200 with the documented shape."""
    resp = client.get('/api/usage')
    assert resp.status_code == 200
    body = resp.get_json()
    assert 'total' in body and 'by_provider' in body
    assert body['total']['input_tokens'] == 0


def test_router_stats_endpoint_ok(client):
    resp = client.get('/api/router/stats')
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), dict)


def test_recent_runs_endpoint_ok(client):
    resp = client.get('/api/recent-runs')
    assert resp.status_code == 200


# ── auth contract — the app-wide gate still covers the moved routes ───────────

def test_moved_route_behind_lan_gate(client):
    """A non-loopback peer with no passcode is 401'd BEFORE the handler runs —
    same contract the routes had while they lived in server.py."""
    resp = client.get('/api/usage', environ_overrides=LAN)
    assert resp.status_code == 401


# ── SSE cursor-overshoot guard (2026-06-11) ───────────────────────────────────
# log_lines can be rebuilt SHORTER under the same session_id (revive after a
# restart/purge reseeds from the transcript; the 2000→1500 cap slams the
# array). A client cursor beyond the array used to starve the stream forever —
# heartbeats only, chat frozen even in focus. The guard must emit a `reset`
# and replay from zero. These pull only the events yielded before the
# generator's first sleep, so they're fast and deterministic.

def _sse_events(resp, n):
    gen = resp.response
    out = []
    for _ in range(n):
        chunk = next(gen)
        if isinstance(chunk, bytes):
            chunk = chunk.decode('utf-8')
        out.append(json.loads(chunk[len('data: '):].strip()))
    return out


def _seed_stream_session(sid, lines):
    from mc import state as mc_state
    mc_state.agent_sessions[sid] = {
        'project_id': 'sse-test-proj', 'mode': 'B', 'status': 'running',
        'log_lines': list(lines),
    }


def test_stream_cursor_overshoot_resets_and_replays(client):
    """since > len(log_lines) → first event is `reset`, then the full replay."""
    _seed_stream_session('sse-overshoot', ['a', 'b', 'c'])
    resp = client.get(
        '/api/project/sse-test-proj/agent/stream?session=sse-overshoot&since=9999',
        buffered=False)
    try:
        evs = _sse_events(resp, 5)
    finally:
        resp.close()
    assert evs[0] == {'type': 'reset'}
    assert [e.get('text') for e in evs[1:4]] == ['a', 'b', 'c']
    assert all(e.get('type') == 'output' for e in evs[1:4])
    assert [e.get('line_index') for e in evs[1:4]] == [1, 2, 3]
    assert evs[4].get('type') == 'turn_start'


def test_stream_normal_cursor_no_reset(client):
    """since within bounds → no reset, delivery starts at the cursor."""
    _seed_stream_session('sse-normal', ['a', 'b', 'c'])
    resp = client.get(
        '/api/project/sse-test-proj/agent/stream?session=sse-normal&since=1',
        buffered=False)
    try:
        evs = _sse_events(resp, 3)
    finally:
        resp.close()
    assert [e.get('text') for e in evs[:2]] == ['b', 'c']
    assert [e.get('line_index') for e in evs[:2]] == [2, 3]
    assert evs[2].get('type') == 'turn_start'


def test_stream_exact_cursor_no_reset(client):
    """since == len(log_lines) (in sync) → no reset, no replay."""
    _seed_stream_session('sse-exact', ['a', 'b', 'c'])
    resp = client.get(
        '/api/project/sse-test-proj/agent/stream?session=sse-exact&since=3',
        buffered=False)
    try:
        evs = _sse_events(resp, 1)
    finally:
        resp.close()
    assert evs[0].get('type') == 'turn_start'


# ── live context counter (docs/CONTEXT_ECONOMY_SPEC.md §5) ───────────────────
# Two gaps closed together: (1) GET /agent/status never returned
# context_tokens at all (the key was absent, not null — a serializer
# omission, not the field failing to be set on the live session dict), and
# (2) nothing pushed the figure over SSE mid-turn, only at turn boundaries.

def test_status_endpoint_returns_context_tokens_and_window(client):
    """Regression: /agent/status previously omitted `context_tokens` and
    `context_window` entirely — confirmed live against 3 running Claude
    sessions that had recorded a figure internally but never surfaced it."""
    from mc import state as mc_state
    mc_state.agent_sessions['ctx-status'] = {
        'session_id': 'ctx-status', 'project_id': 'sse-test-proj', 'mode': 'B',
        'status': 'idle', 'log_lines': [], 'provider': 'claude',
        'context_tokens': 84213, 'task': 't', 'started_at': '',
    }
    resp = client.get('/api/project/sse-test-proj/agent/status')
    assert resp.status_code == 200
    row = next(s for s in resp.get_json()['sessions'] if s['session_id'] == 'ctx-status')
    assert row['context_tokens'] == 84213
    assert row['context_window'] == 200_000  # Claude's declared max


def test_status_endpoint_context_tokens_none_not_zero_when_unknown(client):
    """A session that never recorded a figure must report null, never a
    fabricated 0 — 'unknown stays unknown' (VENDOR_AGNOSTIC_PROGRAM.md §4)."""
    from mc import state as mc_state
    mc_state.agent_sessions['ctx-unknown'] = {
        'session_id': 'ctx-unknown', 'project_id': 'sse-test-proj', 'mode': 'B',
        'status': 'idle', 'log_lines': [], 'provider': 'gemini',
        'task': 't', 'started_at': '',
    }
    resp = client.get('/api/project/sse-test-proj/agent/status')
    row = next(s for s in resp.get_json()['sessions'] if s['session_id'] == 'ctx-unknown')
    assert row['context_tokens'] is None
    assert row['context_window'] is None  # Gemini declares no context_window today


def test_stream_emits_context_event_on_change(client):
    """The SSE stream must push a `context` event carrying the SAME figure
    the auto-fresh trigger reads (session['context_tokens']), independent of
    turn_complete/status — the live-during-a-turn requirement."""
    _seed_stream_session('sse-ctx', [])
    from mc import state as mc_state
    mc_state.agent_sessions['sse-ctx']['context_tokens'] = 84_000
    mc_state.agent_sessions['sse-ctx']['provider'] = 'claude'
    resp = client.get(
        '/api/project/sse-test-proj/agent/stream?session=sse-ctx&since=0',
        buffered=False)
    try:
        evs = _sse_events(resp, 2)
    finally:
        resp.close()
    ctx_evs = [e for e in evs if e.get('type') == 'context']
    assert ctx_evs, evs
    assert ctx_evs[0]['context_tokens'] == 84_000
    assert ctx_evs[0]['context_window'] == 200_000


def test_stream_emits_no_context_event_when_never_recorded(client):
    """A session with no context_tokens figure at all must not emit a
    `context` event — never a fabricated 0/'—' push for a vendor/session
    that simply has nothing to report yet."""
    _seed_stream_session('sse-ctx-none', ['a'])
    resp = client.get(
        '/api/project/sse-test-proj/agent/stream?session=sse-ctx-none&since=0',
        buffered=False)
    try:
        evs = _sse_events(resp, 2)  # output line 'a', then turn_start
    finally:
        resp.close()
    assert not any(e.get('type') == 'context' for e in evs)


# ── in-chat model switcher: POST /agent/<sid>/model (pin/clear) ───────────────

def _seed_model_session(sid, provider='claude', model='claude-opus-4-8',
                        pinned=''):
    from mc import state as mc_state
    mc_state.agent_sessions[sid] = {
        'project_id': 'mdl-proj', 'mode': 'B', 'status': 'idle',
        'provider': provider, 'model': model, 'model_source': 'manual',
        'pinned_model': pinned, 'log_lines': [],
    }


def test_model_pin_sets_pinned_model(client):
    from mc import state as mc_state
    _seed_model_session('mdl-1')
    r = client.post('/api/project/mdl-proj/agent/mdl-1/model',
                    json={'model': 'claude-haiku-4-5-20251001'})
    assert r.status_code == 200
    body = r.get_json()
    assert body['ok'] and body['pinned_model'] == 'claude-haiku-4-5-20251001'
    # Running model was Opus → a tier change is pending for the next turn.
    assert body['pending'] is True
    assert mc_state.agent_sessions['mdl-1']['pinned_model'] == 'claude-haiku-4-5-20251001'


def test_model_clear_unpins(client):
    from mc import state as mc_state
    _seed_model_session('mdl-2', pinned='claude-haiku-4-5-20251001')
    r = client.post('/api/project/mdl-proj/agent/mdl-2/model', json={'model': ''})
    assert r.status_code == 200
    assert r.get_json()['pinned_model'] == ''
    assert mc_state.agent_sessions['mdl-2']['pinned_model'] == ''


def test_model_pin_same_tier_different_version_is_pending(client):
    # A tier alias and a pinned version are not the same explicit choice.
    _seed_model_session('mdl-3', model='opus')
    r = client.post('/api/project/mdl-proj/agent/mdl-3/model',
                    json={'model': 'claude-opus-4-8'})
    assert r.status_code == 200
    assert r.get_json()['pending'] is True


def test_model_pin_rejects_bad_id(client):
    _seed_model_session('mdl-4')
    r = client.post('/api/project/mdl-proj/agent/mdl-4/model',
                    json={'model': 'evil --dangerously-skip-permissions'})
    assert r.status_code == 400


def test_model_pin_claude_only(client):
    _seed_model_session('mdl-5', provider='gemini')
    r = client.post('/api/project/mdl-proj/agent/mdl-5/model',
                    json={'model': 'claude-opus-4-8'})
    assert r.status_code == 400


def test_model_pin_session_not_found(client):
    r = client.post('/api/project/mdl-proj/agent/nope/model',
                    json={'model': 'claude-opus-4-8'})
    assert r.status_code == 404


# ── resume re-resolves a tracked tier; a pin never moves (model-hierarchy-
# simplification, 2026-09-22) ───────────────────────────────────────────────
# `_continuation_model` is the function `_dispatch_agent_internal`'s resume
# branch calls (agent_routes.py ~8511) to decide what `--model` a resumed
# conversation gets. An unpinned/auto conversation must re-run
# engine_selection's tier lookup on every call — never memorize a snapshot —
# so a new release is picked up the next time the chat is resumed. A pinned
# conversation must ignore config changes entirely.
#
# `state.CONFIG` is the same process-wide singleton every other test file
# reads (not reset by the `client` fixture, which only touches
# agent_sessions/DATA_DIR) — a bare `.pop('agent_model', None)` teardown
# DELETES whatever value was there before this test ran instead of restoring
# it, permanently flipping later tests' config from "concrete pin" to
# "absent -> tier:best" for the rest of the pytest session. Root-caused a
# cross-file failure in tests/test_authorized_runtime_bridge.py (its
# duck-typed FakeRuntime has no latest_for(), so the tier path it never used
# to hit started raising AttributeError). Snapshot-and-restore the exact
# prior value/absence, same as the `client` fixture already does for
# agent_sessions.
_MISSING = object()


def _set_config(ar, **values):
    """Set state.CONFIG keys, returning a restore() that undoes exactly this
    call — even when a key was absent before, never leaving a stale value."""
    originals = {k: ar.state.CONFIG.get(k, _MISSING) for k in values}
    ar.state.CONFIG.update(values)

    def restore():
        for k, v in originals.items():
            if v is _MISSING:
                ar.state.CONFIG.pop(k, None)
            else:
                ar.state.CONFIG[k] = v
    return restore


def test_continuation_model_reresolves_tracked_tier_on_each_call(client):
    from mc.blueprints import agent_routes as ar
    session = {'model_auto_requested': True, 'agent_model': 'claude-opus-5-5'}
    restore = _set_config(ar, agent_model='tier:best', auto_model_enabled=False)
    try:
        assert ar._continuation_model(session, {}) == 'opus'
        # A new global tier choice takes effect on the VERY NEXT resume —
        # nothing about the prior resolution is cached on the session.
        ar.state.CONFIG['agent_model'] = 'tier:fast'
        assert ar._continuation_model(session, {}) == 'haiku'
    finally:
        restore()


def test_continuation_model_pinned_chat_never_moves(client):
    from mc.blueprints import agent_routes as ar
    session = {'model_auto_requested': False, 'pinned_model': 'claude-opus-5',
               'agent_model': 'claude-opus-5'}
    restore = _set_config(ar, agent_model='tier:best')
    try:
        # Global tracking changed underneath it; a pinned chat ignores it.
        assert ar._continuation_model(session, {}) == 'claude-opus-5'
        ar.state.CONFIG['agent_model'] = 'tier:fast'
        assert ar._continuation_model(session, {}) == 'claude-opus-5'
    finally:
        restore()


def test_resolve_dispatch_model_reresolves_tier_between_calls():
    """The dispatch-time fallback (used by both fresh dispatch and the auto/
    tracked resume branch) is a live lookup, not a cached value: a global
    tier change is visible on the very next call, no restart required."""
    from mc.blueprints import agent_routes as ar
    restore = _set_config(ar, agent_model='tier:best', auto_model_enabled=False)
    try:
        assert ar._resolve_dispatch_model({}, '')[0] == 'opus'
        ar.state.CONFIG['agent_model'] = 'tier:balanced'
        assert ar._resolve_dispatch_model({}, '')[0] == 'sonnet'
    finally:
        restore()


# ── Auth probe: "Reached max turns" must not be read as an auth failure ───────

def _run_probe_with(monkeypatch, *, returncode, stdout='', stderr=''):
    """Drive _run_claude_auth_probe with a faked subprocess result."""
    import types
    from mc.blueprints import agent_routes as ar

    r = types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'claude')
    monkeypatch.setattr(ar.subprocess, 'run', lambda *a, **k: r)
    # Reset to a known-bad state so a no-op would be visible as a failure.
    with ar._claude_auth_lock:
        ar._claude_auth_state.update(ok=False, reason='unknown',
                                     last_error_text='seed', last_probe_at=None)
    return ar._run_claude_auth_probe()


def test_auth_probe_max_turns_is_ok(client, monkeypatch):
    # rc=1 + "Reached max turns" = the CLI authenticated and ran; NOT an auth error.
    state = _run_probe_with(monkeypatch, returncode=1,
                            stderr='Error: Reached max turns (1)')
    assert state['ok'] is True
    assert state['reason'] is None


def test_auth_probe_clean_exit_is_ok(client, monkeypatch):
    state = _run_probe_with(monkeypatch, returncode=0, stdout='ok')
    assert state['ok'] is True


def test_auth_probe_real_auth_error_still_fails(client, monkeypatch):
    state = _run_probe_with(monkeypatch, returncode=1,
                            stderr='Invalid API key · please run /login')
    assert state['ok'] is False
    assert state['reason'] == 'not_logged_in'


def test_auth_probe_unknown_nonzero_fails_closed(client, monkeypatch):
    # A non-zero exit with no recognized signal must still fail closed.
    state = _run_probe_with(monkeypatch, returncode=1, stderr='segfault')
    assert state['ok'] is False
    assert state['reason'] == 'unknown'


# ── per-provider model selection (composer "Model" picker) ────────────────────

def test_providers_endpoint_carries_per_provider_model_catalog(client):
    """The composer's Model picker rebuilds its options from this payload when
    the Agent picker changes — so every provider record must carry `models`."""
    body = client.get('/api/agent/providers').get_json()
    provs = body['providers'] if isinstance(body, dict) else body
    by_name = {p['name']: p for p in provs}
    assert 'models' in by_name['claude']
    assert any(m['id'] == 'claude-opus-5' for m in by_name['claude']['models'])
    assert [m['id'] for m in by_name['codex']['models']] == [
        'gpt-6-astra',
        'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna',
        'gpt-5.5', 'gpt-5.4', 'gpt-5.4-mini',
    ]
    # No claude id may appear in a non-claude catalog — that cross-contamination
    # is exactly what produced `codex -m claude-opus-5`.
    for name in ('codex', 'gemini'):
        assert not [m for m in by_name[name]['models']
                    if m['id'].startswith('claude-')], name
    # kiro-cli takes no model flag → empty catalog → picker hidden.
    assert by_name['kiro']['models'] == []


def test_resolve_runtime_model_blocks_the_claude_id_leak():
    """A project pinned to Opus must not push that id into a codex spawn; an
    explicit per-chat pick must pass through untouched."""
    from mc.blueprints import agent_routes as ar
    from mc import agent_runtime

    codex = agent_runtime.get_runtime('codex')
    proj = {'agent_model': 'claude-opus-5'}
    assert ar._resolve_runtime_model(codex, proj) == ''
    assert ar._resolve_runtime_model(codex, proj, 'gpt-5.6-sol') == 'gpt-5.6-sol'
    # A custom id we've never catalogued is still the user's explicit intent.
    assert ar._resolve_runtime_model(codex, proj, 'gpt-6-future') == 'gpt-6-future'
    # A project default the runtime DOES accept is still inherited.
    assert ar._resolve_runtime_model(codex, {'agent_model': 'gpt-5.6-terra'}) == 'gpt-5.6-terra'


def test_status_model_field_does_not_borrow_the_claude_default():
    """Header-pill truthfulness: a codex session that got no --model must report
    an empty model, not the project's claude default."""
    from mc.blueprints import agent_routes as ar
    assert ar._session_model_field({'provider': 'codex'}, 'claude-opus-5') == ''
    assert ar._session_model_field({'provider': 'claude'}, 'claude-opus-5') == 'claude-opus-5'
    assert ar._session_model_field({'provider': 'codex', 'agent_model': 'gpt-5'},
                                   'claude-opus-5') == 'gpt-5'
    # No provider recorded (legacy session) = claude, so the fallback applies.
    assert ar._session_model_field({}, 'claude-opus-5') == 'claude-opus-5'


# ── MC-923: /api/session/trigger-type ──────────────────────────────────────
# mc.secrets_store's fail-closed unattended-context detection calls this.

def test_trigger_type_requires_a_session_id(client):
    resp = client.get('/api/session/trigger-type')
    assert resp.status_code == 400
    assert resp.get_json()['found'] is False


def test_trigger_type_not_found_when_unknown(client):
    resp = client.get('/api/session/trigger-type?claude_session_id=nope-nobody')
    assert resp.status_code == 200
    # fence_unattended_enabled rides along on every response (2026-09-14,
    # UNATTENDED_AGENT_PERMISSIONS_AUDIT) — steward/fence.py's generalized
    # arming check piggybacks it here to save a second /api/config round trip.
    assert resp.get_json() == {'found': False, 'fence_unattended_enabled': True}


def test_trigger_type_found_in_live_session(client):
    """The common case: a mid-session with-secret.py call, before the session
    has completed (and so before the persisted agent_log row would have it)."""
    from mc import state as mc_state
    mc_state.agent_sessions['s1'] = {
        'project_id': 'proj-a', 'claude_session_id': 'live-csid-123',
        'trigger_type': 'schedule',
    }
    try:
        resp = client.get('/api/session/trigger-type?claude_session_id=live-csid-123')
        assert resp.get_json() == {'found': True, 'trigger_type': 'schedule',
                                   'fence_unattended_enabled': True}
    finally:
        mc_state.agent_sessions.pop('s1', None)


def test_trigger_type_falls_back_to_persisted_agent_log(client):
    """A session that already completed and dropped out of agent_sessions is
    still findable in its project's persisted log."""
    from mc.blueprints import agent_routes as ar
    (ar.DATA_DIR / 'proj-a_agent_log.json').write_text(json.dumps([
        {'session_id': 's-old', 'claude_session_id': 'done-csid-999',
         'trigger_type': 'hivemind_worker'},
    ]), encoding='utf-8')
    resp = client.get('/api/session/trigger-type?claude_session_id=done-csid-999')
    assert resp.get_json() == {'found': True, 'trigger_type': 'hivemind_worker',
                               'fence_unattended_enabled': True}


def test_trigger_type_missing_field_defaults_to_manual(client):
    """A row with no trigger_type at all (older entry shape) must not be
    silently treated as unattended-only-safe — it's the same default
    `session.get('trigger_type', 'manual')` uses everywhere else it's read."""
    from mc import state as mc_state
    mc_state.agent_sessions['s2'] = {
        'project_id': 'proj-a', 'claude_session_id': 'no-tt-csid',
    }
    try:
        resp = client.get('/api/session/trigger-type?claude_session_id=no-tt-csid')
        assert resp.get_json() == {'found': True, 'trigger_type': 'manual',
                                   'fence_unattended_enabled': True}
    finally:
        mc_state.agent_sessions.pop('s2', None)


def test_trigger_type_reports_fence_unattended_enabled_false_when_configured(client):
    """A human turning the fence-arming off-switch off (Settings) must be
    visible through this endpoint — it's what steward/fence.py reads live."""
    from mc import state as mc_state
    mc_state.agent_sessions['s3'] = {
        'project_id': 'proj-a', 'claude_session_id': 'flag-off-csid',
        'trigger_type': 'schedule',
    }
    before = mc_state.CONFIG.get('fence_unattended_enabled')
    mc_state.CONFIG['fence_unattended_enabled'] = False
    try:
        resp = client.get('/api/session/trigger-type?claude_session_id=flag-off-csid')
        assert resp.get_json() == {'found': True, 'trigger_type': 'schedule',
                                   'fence_unattended_enabled': False}
    finally:
        mc_state.agent_sessions.pop('s3', None)
        mc_state.CONFIG['fence_unattended_enabled'] = before


# ── POST /api/project/<id>/agent/dispatch stamps trigger_type='dispatch' ────
# for an agent-sourced call (2026-09-14, UNATTENDED_AGENT_PERMISSIONS_AUDIT).
# Before this fix the route always left trigger_type at its 'manual' default,
# so a session another agent spawned unattended via this exact endpoint was
# indistinguishable from a human clicking "+New chat" — invisible to
# is_unattended_caller, the secrets vault's detect_unattended_context, and
# steward/fence.py's generalized arming, all of which trust trigger_type.

def test_dispatch_route_stamps_dispatch_trigger_type_for_agent_source(client, monkeypatch):
    from mc.blueprints import agent_routes as ar
    captured = {}
    monkeypatch.setattr(ar, '_dispatch_agent_internal',
                        lambda *a, **kw: captured.update(kw) or 'sid-x')
    # No Origin header, no 'client' field → the existing source='agent'
    # heuristic (a raw agent/curl dispatch, not the browser UI).
    resp = client.post('/api/project/p1/agent/dispatch', json={'task': 'do a thing'})
    assert resp.status_code == 200
    assert captured.get('source') == 'agent'
    assert captured.get('trigger_type') == 'dispatch'


def test_dispatch_route_keeps_manual_trigger_type_for_ui_source(client, monkeypatch):
    from mc.blueprints import agent_routes as ar
    captured = {}
    monkeypatch.setattr(ar, '_dispatch_agent_internal',
                        lambda *a, **kw: captured.update(kw) or 'sid-x')
    resp = client.post('/api/project/p1/agent/dispatch', json={'task': 'do a thing'},
                       headers={'Origin': 'http://localhost:5199'})
    assert resp.status_code == 200
    assert captured.get('source') != 'agent'
    assert captured.get('trigger_type') == 'manual'


def test_dispatch_route_keeps_manual_trigger_type_for_explicit_ui_client(client, monkeypatch):
    from mc.blueprints import agent_routes as ar
    captured = {}
    monkeypatch.setattr(ar, '_dispatch_agent_internal',
                        lambda *a, **kw: captured.update(kw) or 'sid-x')
    resp = client.post('/api/project/p1/agent/dispatch',
                       json={'task': 'do a thing', 'source': 'ui'})
    assert resp.status_code == 200
    assert captured.get('trigger_type') == 'manual'


# ── _dispatch_via_runtime -> CodexRuntime.dispatch sandbox wiring ─────────────
# (UNATTENDED_AGENT_PERMISSIONS_AUDIT §4 risk #1). agent_runtime.py never
# reads server CONFIG directly (testability — see ClaudeRuntime.build_command's
# docstring), so `_dispatch_via_runtime` must compute the flag from
# mc.state.CONFIG and hand it across the seam as a plain bool on every codex
# dispatch. A stub runtime stands in for CodexRuntime so this doesn't need a
# real `codex` binary — only the wiring is under test here (the decision
# logic itself is covered by tests/test_codex_unattended_sandbox.py).

class _StubCodexRuntime:
    name = 'codex'

    def model_supported(self, model):
        return False

    def latest_for(self, tier):
        """Stand-in for AgentRuntime.latest_for: these tests exercise
        dispatch-flag plumbing, not model-hierarchy tier tracking, so an
        empty tier head (native default) keeps them independent of it."""
        return ''

    def __init__(self):
        self.dispatch_kwargs = None

    def build_command(self, **kwargs):
        return ['codex', 'exec']

    def dispatch(self, **kwargs):
        self.dispatch_kwargs = kwargs
        from mc import agent_runtime as art
        return art.SessionHandle(
            mc_session_id=kwargs.get('mc_session_id') or 'sid-stub',
            provider='codex', mode='A',
            project_path=kwargs.get('project_path', ''),
            project_id=kwargs.get('project_id', ''),
            session_dict=kwargs.get('session_dict') or {},
            started_at='2026-09-14T00:00:00Z',
            capabilities=None, meta={},
        )


def _dispatch_via_runtime_with_stub(monkeypatch, *, trigger_type,
                                    codex_unattended_sandbox_config):
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    stub = _StubCodexRuntime()
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: stub)
    before = mc_state.CONFIG.get('codex_unattended_sandbox')
    mc_state.CONFIG['codex_unattended_sandbox'] = codex_unattended_sandbox_config
    try:
        ar._dispatch_via_runtime(
            {'id': 'proj-stub', 'project_path': str(Path(__file__).parent)},
            'do a thing', provider_name='codex', trigger_type=trigger_type)
    finally:
        mc_state.CONFIG['codex_unattended_sandbox'] = before
        mc_state.agent_sessions.clear()
    return stub


def test_dispatch_via_runtime_passes_config_flag_true_to_codex(monkeypatch, client):
    stub = _dispatch_via_runtime_with_stub(
        monkeypatch, trigger_type='schedule', codex_unattended_sandbox_config=True)
    assert stub.dispatch_kwargs['unattended_sandbox_enabled'] is True


def test_dispatch_via_runtime_passes_config_flag_false_to_codex(monkeypatch, client):
    stub = _dispatch_via_runtime_with_stub(
        monkeypatch, trigger_type='schedule', codex_unattended_sandbox_config=False)
    assert stub.dispatch_kwargs['unattended_sandbox_enabled'] is False


def test_dispatch_via_runtime_defaults_flag_true_when_unset(monkeypatch, client):
    from mc import state as mc_state
    mc_state.CONFIG.pop('codex_unattended_sandbox', None)
    stub = _dispatch_via_runtime_with_stub(
        monkeypatch, trigger_type='workflow', codex_unattended_sandbox_config=True)
    # Config key absent entirely (fresh install pre-migration) still resolves
    # to the documented default (True) via state.CONFIG.get(..., True).
    assert stub.dispatch_kwargs['unattended_sandbox_enabled'] is True


def test_dispatch_via_runtime_carries_trigger_type_onto_session_dict(monkeypatch, client):
    stub = _dispatch_via_runtime_with_stub(
        monkeypatch, trigger_type='hivemind_worker', codex_unattended_sandbox_config=True)
    assert stub.dispatch_kwargs['session_dict']['trigger_type'] == 'hivemind_worker'


def test_providers_in_use_codex_only_install_drops_claude_fallback(client, monkeypatch):
    """The Keegan case: default_provider unset, claude CLI not installed. The
    'claude' fallback is not a choice anyone made, so claude must not be
    in_use, or the Claude sign-in banner nags a Codex-only user forever (the
    picker never offers itself when only one CLI is installed)."""
    from mc.blueprints import agent_routes as ar
    from mc import state
    import mc.characters as _chars
    monkeypatch.setattr(ar, 'load_projects', lambda: [])
    monkeypatch.setattr(_chars, 'list_characters', lambda **kw: [])
    monkeypatch.setattr(ar._agent_runtime, 'claude_installed', lambda: False)
    monkeypatch.setitem(state.CONFIG, 'default_provider', '')
    assert 'claude' not in ar._providers_in_use()
    # An explicit choice always counts, installed or not.
    monkeypatch.setitem(state.CONFIG, 'default_provider', 'codex')
    assert ar._providers_in_use() == {'codex'}


# ── Missing-CLI dispatch fails fast, once, with the install message ──────────
# Fresh-install report 2026-09-15: a Codex dispatch with no `codex` binary on
# PATH surfaced a generic 500 ("dispatch failed: codex CLI not installed...")
# instead of the single-source-of-truth _cli_missing_message() every other
# missing-CLI path already used (FileNotFoundError from a raw Popen). Root
# cause: every non-claude runtime's dispatch() pre-flight check raised a bare
# RuntimeError, which is NOT the FileNotFoundError agent_dispatch()'s except
# clause special-cased — so it fell into the generic branch, and the session
# never got marked non-retryable, leaving a follow-up on the same session free
# to have Guardian burn through GUARDIAN_MAX_RECOVERIES retrying a failure no
# retry can fix. CLINotInstalledError (mc/agent_runtime.py) closes both gaps:
# a dedicated, catchable class, always routed through _cli_missing_message(),
# and always trips the session's circuit breaker on the first failure.

class _StubMissingCLIRuntime:
    """Stands in for CodexRuntime/GeminiRuntime/etc. when their real binary
    isn't installed — dispatch() raises exactly what those runtimes raise."""
    name = 'codex'
    display_name = 'Codex'

    def model_supported(self, model):
        return False

    def latest_for(self, tier):
        """See _StubCodexRuntime.latest_for — same reasoning."""
        return ''

    def build_command(self, **kwargs):
        return ['codex', 'exec']

    def dispatch(self, **kwargs):
        from mc import agent_runtime as art
        raise art.CLINotInstalledError(
            "codex CLI not installed — run: npm install -g @openai/codex")

    def health_check(self):
        from mc import agent_runtime as art
        return art.HealthStatus(
            installed=False, binary_path=None, version=None,
            auth_state=art.AuthState(status='unknown', last_checked=''),
            install_hint='npm install -g @openai/codex')


def test_dispatch_via_runtime_missing_cli_trips_circuit_breaker_once(monkeypatch, client):
    """No retry loop: the session created for the failed dispatch is marked
    non-retryable on the FIRST attempt — recovery_attempts stays at its
    initial 0, and _guardian_should_recover refuses ever to fire for it."""
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    stub = _StubMissingCLIRuntime()
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: stub)
    try:
        with pytest.raises(Exception):
            ar._dispatch_via_runtime(
                {'id': 'proj-missing-cli', 'project_path': str(Path(__file__).parent)},
                'do a thing', provider_name='codex', trigger_type='manual')
        sessions = [s for s in mc_state.agent_sessions.values()
                    if s.get('project_id') == 'proj-missing-cli']
        assert len(sessions) == 1
        session = sessions[0]
        assert session['status'] == 'error'
        assert session['circuit_breaker_tripped'] is True
        assert session['pending_recovery_message'] is None
        assert session.get('recovery_attempts', 0) == 0  # never even tried once
        assert ar._guardian_should_recover(session) is False
        assert any('npm install -g @openai/codex' in line
                  for line in session['log_lines'])
    finally:
        mc_state.agent_sessions.clear()


def test_dispatch_route_missing_cli_returns_install_message_not_generic(client, monkeypatch):
    """The route-level response must be the SAME _cli_missing_message() text
    Settings and the FileNotFoundError path already use — not the generic
    'dispatch failed: <raw exception>' branch a bare RuntimeError used to
    fall into."""
    from mc.blueprints import agent_routes as ar
    stub = _StubMissingCLIRuntime()
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: stub)
    monkeypatch.setattr(ar, 'load_project', lambda pid: {
        'id': 'proj-missing-cli-2', 'project_path': str(Path(__file__).parent)})
    resp = client.post('/api/project/proj-missing-cli-2/agent/dispatch',
                       json={'task': 'do a thing', 'provider': 'codex',
                             'source': 'ui'})
    assert resp.status_code == 500
    body = resp.get_json()
    assert not body['error'].startswith('dispatch failed:')
    assert 'Install it with: npm install -g @openai/codex' in body['error']
    from mc import state as mc_state
    mc_state.agent_sessions.clear()


# ── F6: PowerShell ExecutionPolicy during vendor install ─────────────────────

def _scopes(**kw):
    base = {'MachinePolicy': 'Undefined', 'UserPolicy': 'Undefined', 'Process': 'Undefined',
            'CurrentUser': 'Undefined', 'LocalMachine': 'Undefined'}
    base.update(kw)
    return base


@pytest.mark.parametrize('scopes,action,effective', [
    # Clean Windows client: nothing defined anywhere -> Undefined -> fix it.
    (_scopes(), 'set', 'Undefined'),
    (_scopes(LocalMachine='Restricted'), 'set', 'Restricted'),
    (_scopes(CurrentUser='Restricted'), 'set', 'Restricted'),
    # CurrentUser outranks LocalMachine: a user-level RemoteSigned stands even
    # over a Restricted machine default, and vice versa for a user AllSigned.
    (_scopes(CurrentUser='RemoteSigned', LocalMachine='Restricted'), 'unchanged', 'RemoteSigned'),
    (_scopes(CurrentUser='AllSigned', LocalMachine='RemoteSigned'), 'left_alone', 'AllSigned'),
    (_scopes(LocalMachine='AllSigned'), 'left_alone', 'AllSigned'),
    (_scopes(LocalMachine='RemoteSigned'), 'unchanged', 'RemoteSigned'),
    (_scopes(CurrentUser='Bypass'), 'unchanged', 'Bypass'),
    (_scopes(LocalMachine='Unrestricted'), 'unchanged', 'Unrestricted'),
    # Group Policy always wins and is never overridden, blocking or not.
    (_scopes(MachinePolicy='AllSigned'), 'left_alone', 'AllSigned'),
    (_scopes(MachinePolicy='Restricted', CurrentUser='Undefined'), 'left_alone', 'Restricted'),
    (_scopes(UserPolicy='AllSigned'), 'left_alone', 'AllSigned'),
    (_scopes(MachinePolicy='RemoteSigned', CurrentUser='Restricted'), 'unchanged', 'RemoteSigned'),
    # A Process-scope value is per-shell and must not steer a persistent write.
    (_scopes(Process='Bypass'), 'set', 'Undefined'),
    (_scopes(Process='AllSigned'), 'set', 'Undefined'),
    # Case/whitespace from the shell must not defeat the check.
    (_scopes(CurrentUser=' restricted '), 'set', 'restricted'),
])
def test_execution_policy_decision(scopes, action, effective):
    from mc.blueprints import agent_routes as ar
    assert ar._execution_policy_decision(scopes) == (action, effective)


def test_execution_policy_set_reports_and_writes_once(monkeypatch):
    from mc.blueprints import agent_routes as ar
    writes = []
    monkeypatch.setattr(ar.sys, 'platform', 'win32')
    monkeypatch.setattr(ar, '_read_powershell_execution_scopes',
                        lambda: _scopes(LocalMachine='Restricted'))
    monkeypatch.setattr(ar, '_set_powershell_execution_policy_remotesigned',
                        lambda: writes.append(1))
    out = ar._ensure_powershell_execution_policy()
    assert writes == [1]
    assert out['action'] == 'set' and out['effective'] == 'Restricted'
    assert 'RemoteSigned' in out['message'] and 'Restricted' in out['message']
    assert 'Undo with' in out['message']


@pytest.mark.parametrize('scopes,action', [
    (_scopes(LocalMachine='AllSigned'), 'left_alone'),
    (_scopes(MachinePolicy='AllSigned'), 'left_alone'),
    (_scopes(LocalMachine='RemoteSigned'), 'unchanged'),
])
def test_execution_policy_never_writes_when_not_needed(monkeypatch, scopes, action):
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(ar.sys, 'platform', 'win32')
    monkeypatch.setattr(ar, '_read_powershell_execution_scopes', lambda: scopes)
    monkeypatch.setattr(ar, '_set_powershell_execution_policy_remotesigned',
                        lambda: pytest.fail('must not write'))
    out = ar._ensure_powershell_execution_policy()
    assert out['action'] == action
    # Only a deliberate, blocking policy earns a UI message; an already-fine
    # one stays silent.
    assert bool(out['message']) == (action == 'left_alone')


def test_execution_policy_write_failure_is_reported_not_raised(monkeypatch):
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(ar.sys, 'platform', 'win32')
    monkeypatch.setattr(ar, '_read_powershell_execution_scopes', lambda: _scopes())
    monkeypatch.setattr(ar, '_set_powershell_execution_policy_remotesigned',
                        lambda: 'access denied')
    out = ar._ensure_powershell_execution_policy()
    assert out['action'] == 'failed'
    assert 'access denied' in out['message']


def test_execution_policy_unreadable_and_non_windows_do_nothing(monkeypatch):
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(ar, '_read_powershell_execution_scopes',
                        lambda: pytest.fail('non-windows must not probe'))
    monkeypatch.setattr(ar.sys, 'platform', 'linux')
    assert ar._ensure_powershell_execution_policy()['action'] == 'not_applicable'
    monkeypatch.setattr(ar.sys, 'platform', 'win32')
    monkeypatch.setattr(ar, '_read_powershell_execution_scopes', lambda: None)
    monkeypatch.setattr(ar, '_set_powershell_execution_policy_remotesigned',
                        lambda: pytest.fail('unreadable policy must not be written'))
    assert ar._ensure_powershell_execution_policy() == {
        'action': 'unknown', 'effective': None, 'message': ''}


def test_install_launch_routes_surface_execution_policy(monkeypatch, client):
    """Both install routes must set the policy AFTER the terminal launched and
    return the message the UI shows; a failed launch must not touch it."""
    from mc.blueprints import agent_routes as ar
    runtimes = {'gemini': _BatchInstallRuntime('npm install -g @google/gemini-cli')}
    order = []
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: runtimes[name])
    monkeypatch.setattr(ar.shutil, 'which', lambda name: '/x/' + name)
    monkeypatch.setattr(ar, '_launch_install_terminal',
                        lambda c: (order.append('launch') or 'sess-1', None))
    monkeypatch.setattr(ar, '_ensure_powershell_execution_policy',
                        lambda: order.append('policy') or {
                            'action': 'set', 'effective': 'Restricted', 'message': 'MSG'})
    single = client.post('/api/agent/provider/gemini/install-launch').get_json()
    batch = client.post('/api/agent/providers/install-launch',
                        json={'names': ['gemini']}).get_json()
    assert order == ['launch', 'policy', 'launch', 'policy']
    assert single['execution_policy']['message'] == 'MSG'
    assert batch['execution_policy']['message'] == 'MSG'
    assert single['session_id'] == 'sess-1'
    assert batch['session_id'] == 'sess-1'

    order.clear()
    monkeypatch.setattr(ar, '_launch_install_terminal', lambda c: (None, 'no terminal'))
    failed = client.post('/api/agent/provider/gemini/install-launch').get_json()
    assert failed['ok'] is False and 'policy' not in order


class _FakeInstallProc:
    """Minimal Popen stand-in for launch_pipe_session, real pipe so the
    verbatim _read_terminal_stream reader thread runs end-to-end (same trick
    test_terminal_routes.py's FakeProc uses). Closed immediately so the
    reader observes EOF right away and the test doesn't hang on a real
    child."""
    _next_pid = 993000

    def __init__(self):
        r, w = os.pipe()
        self.stdout = os.fdopen(r, 'rb')
        self.stdin = io.BytesIO()
        os.close(w)  # immediate EOF
        _FakeInstallProc._next_pid += 1
        self.pid = _FakeInstallProc._next_pid

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0

    def kill(self):
        pass


def test_install_launch_opens_a_real_terminal_session(monkeypatch, client):
    """F-install (clean-VM run 2026-09-22): the original bug was FALSE
    SUCCESS — `start cmd` returning as soon as the shell spawned, and no
    window ever appearing (wrong Windows session / over the tunnel), while
    the route still answered ok:true. This does NOT mock
    `_launch_install_terminal` — it exercises the real
    launch_pipe_session -> terminal_routes plumbing (only subprocess.Popen is
    faked, same seam test_terminal_routes.py uses) and asserts a genuine
    entry lands in mc.state.terminal_sessions, keyed by the session_id the
    response hands back — the thing openTerminalPopout(session_id) actually
    attaches to, not just a truthy flag.
    """
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import terminal_routes as tr

    before_terms = dict(mc_state.terminal_sessions)
    before_procs = dict(mc_state.tracked_processes)
    mc_state.terminal_sessions.clear()
    mc_state.tracked_processes.clear()
    try:
        runtimes = {'codex': _BatchInstallRuntime('npm install -g @openai/codex')}
        monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: runtimes[name])
        monkeypatch.setattr(ar.shutil, 'which', lambda name: '/x/' + name)  # npm present
        monkeypatch.setattr(ar, '_npm_major_version', lambda npm_bin: 12)
        monkeypatch.setattr(tr, 'subprocess', types.SimpleNamespace(
            Popen=lambda *a, **kw: _FakeInstallProc(), PIPE=-1, STDOUT=-2))

        response = client.post('/api/agent/providers/install-launch',
                               json={'names': ['codex']})
        assert response.status_code == 200
        body = response.get_json()
        assert body['ok'] is True
        sid = body['session_id']
        assert sid and len(sid) == 12
        assert body['pty'] is False
        assert sid in mc_state.terminal_sessions
        session = mc_state.terminal_sessions[sid]
        assert session['command'] == \
            'npm install -g --allow-scripts=@openai/codex @openai/codex'
        assert bool(session.get('is_pty')) is False
    finally:
        mc_state.terminal_sessions.clear()
        mc_state.terminal_sessions.update(before_terms)
        mc_state.tracked_processes.clear()
        mc_state.tracked_processes.update(before_procs)


def test_install_launch_failure_returns_ok_false_with_command(monkeypatch, client):
    """Requirement: a launch that genuinely could not start must return
    ok:false with a usable `command` for the user to run by hand — never
    report success for a window nobody can see. Exercises the real
    launch_pipe_session failure path (Popen raising), not a mocked seam."""
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import terminal_routes as tr

    def _boom(*a, **kw):
        raise OSError('no such file or directory')
    monkeypatch.setattr(tr, 'subprocess', types.SimpleNamespace(
        Popen=_boom, PIPE=-1, STDOUT=-2))

    runtimes = {'codex': _BatchInstallRuntime('npm install -g @openai/codex')}
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: runtimes[name])
    monkeypatch.setattr(ar.shutil, 'which', lambda name: '/x/' + name)
    monkeypatch.setattr(ar, '_npm_major_version', lambda npm_bin: 12)

    single = client.post('/api/agent/provider/codex/install-launch').get_json()
    assert single['ok'] is False
    assert single['command'] == 'npm install -g --allow-scripts=@openai/codex @openai/codex'
    assert 'session_id' not in single

    batch = client.post('/api/agent/providers/install-launch',
                        json={'names': ['codex']}).get_json()
    assert batch['ok'] is False
    assert batch['command'] == 'npm install -g --allow-scripts=@openai/codex @openai/codex'
    assert batch['unsupported'] == []
    assert 'session_id' not in batch


def test_install_launch_batch_still_one_terminal_with_real_launcher(monkeypatch, client):
    """F7's one-terminal/one-prereq property must survive routing through the
    real _launch_install_terminal -> launch_pipe_session path, not just the
    mocked '_launch_terminal_for_binary' seam the property was originally
    proven against."""
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import terminal_routes as tr

    before_terms = dict(mc_state.terminal_sessions)
    before_procs = dict(mc_state.tracked_processes)
    mc_state.terminal_sessions.clear()
    mc_state.tracked_processes.clear()
    try:
        runtimes = {
            'codex': _BatchInstallRuntime('npm install -g @openai/codex'),
            'gemini': _BatchInstallRuntime('npm install -g @google/gemini-cli'),
        }
        popen_calls = []

        def _popen(*a, **kw):
            popen_calls.append(a)
            return _FakeInstallProc()
        monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: runtimes[name])
        monkeypatch.setattr(ar.shutil, 'which', lambda name: None if name == 'npm' else '/x/' + name)
        monkeypatch.setattr(ar.sys, 'platform', 'win32')
        monkeypatch.setattr(tr, 'subprocess', types.SimpleNamespace(
            Popen=_popen, PIPE=-1, STDOUT=-2))

        response = client.post('/api/agent/providers/install-launch',
                               json={'names': ['codex', 'gemini']})
        body = response.get_json()
        assert body['ok'] is True
        assert len(popen_calls) == 1, 'must spawn exactly one process for the whole batch'
        command = popen_calls[0][0]
        assert command.count('winget install --id OpenJS.NodeJS.LTS') == 1
        assert 'npm install -g @openai/codex' in command
        assert 'npm install -g @google/gemini-cli' in command
    finally:
        mc_state.terminal_sessions.clear()
        mc_state.terminal_sessions.update(before_terms)
        mc_state.tracked_processes.clear()
        mc_state.tracked_processes.update(before_procs)


# Root-cause correction, 2026-09-22 (verified on the real VM after the fix
# above shipped): NOT session isolation — Session 2 is Ron's own console.
# The real bug is QUOTING. The composed batch command below (captured
# verbatim from the VM for claude+gemini, npm missing) embeds its own double
# quotes (`set "PATH=..."`, `"tokens=1 delims=."`), && chains, parens and a
# `for /f` loop's `%v` variable. `_launch_terminal_for_binary`'s
# `start "" cmd /k "\"{bin_str}\""` wrapper was built for a single binary
# path — nesting this string inside it produces unbalanced/mis-parsed
# quotes, cmd dies instantly, and Popen(shell=True) has already returned
# "success" by then (defect #1, unchanged). Routing install through
# `_launch_install_terminal` -> `launch_pipe_session` sidesteps this because
# `launch_pipe_session` hands the command to
# `subprocess.Popen(command, shell=True, ...)` with NO re-wrapping — but
# that needs its own pin, or a future refactor could reintroduce wrapping
# and silently reopen this exact bug.
_VERBATIM_COMPOUND_INSTALL_COMMAND = (
    'set "PATH=%ProgramFiles%\\nodejs;%APPDATA%\\npm;%PATH%" '
    '&& (where npm >nul 2>&1 || winget install --id OpenJS.NodeJS.LTS '
    '-e --silent --source winget '
    '--accept-source-agreements --accept-package-agreements) '
    '&& for /f "tokens=1 delims=." %v in (\'npm -v\') do '
    '(if %v GEQ 12 (npm install -g --allow-scripts=@anthropic-ai/claude-code @anthropic-ai/claude-code) '
    'else (npm install -g @anthropic-ai/claude-code)) '
    '&& for /f "tokens=1 delims=." %v in (\'npm -v\') do '
    '(if %v GEQ 12 (npm install -g --allow-scripts=@google/gemini-cli @google/gemini-cli) '
    'else (npm install -g @google/gemini-cli))'
)


def test_batch_install_command_matches_verbatim_vm_capture(monkeypatch):
    """Pin `_provider_install_command_batch`'s real output for claude+gemini
    with npm missing against the exact string captured on the VM, so the
    compound-quoting fixture below is provably the real shape and not a
    hand-typed approximation."""
    from mc.blueprints import agent_routes as ar

    runtimes = {
        'claude': _BatchInstallRuntime('npm install -g @anthropic-ai/claude-code'),
        'gemini': _BatchInstallRuntime('npm install -g @google/gemini-cli'),
    }
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: runtimes[name])
    monkeypatch.setattr(ar.shutil, 'which', lambda name: None)
    monkeypatch.setattr(ar.sys, 'platform', 'win32')
    command, unsupported, prereq_added = ar._provider_install_command_batch(
        ['claude', 'gemini'])
    assert command == _VERBATIM_COMPOUND_INSTALL_COMMAND
    assert unsupported == []
    assert prereq_added is True


def test_install_launch_command_survives_intact_through_pipe_session(monkeypatch, client):
    """Requirement (Ron, 2026-09-22): feed the verbatim compound command
    through the real install launch path and assert it reaches Popen
    byte-for-byte unmodified — not re-quoted, not re-wrapped in
    `start "" cmd /k`. A test only checking 'a session id came back' would
    not catch a quoting regression; this one would, because it fails loudly
    if a future change routes install back through
    `_launch_terminal_for_binary`'s wrapper or otherwise touches the string."""
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import terminal_routes as tr

    before_terms = dict(mc_state.terminal_sessions)
    before_procs = dict(mc_state.tracked_processes)
    mc_state.terminal_sessions.clear()
    mc_state.tracked_processes.clear()
    try:
        runtimes = {
            'claude': _BatchInstallRuntime('npm install -g @anthropic-ai/claude-code'),
            'gemini': _BatchInstallRuntime('npm install -g @google/gemini-cli'),
        }
        popen_calls = []

        def _popen(*a, **kw):
            popen_calls.append(a)
            return _FakeInstallProc()
        monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: runtimes[name])
        monkeypatch.setattr(ar.shutil, 'which', lambda name: None)  # npm missing -> bootstrap path
        monkeypatch.setattr(ar.sys, 'platform', 'win32')
        monkeypatch.setattr(tr, 'subprocess', types.SimpleNamespace(
            Popen=_popen, PIPE=-1, STDOUT=-2))

        response = client.post('/api/agent/providers/install-launch',
                               json={'names': ['claude', 'gemini']})
        body = response.get_json()
        assert body['ok'] is True, body
        assert len(popen_calls) == 1
        launched_command = popen_calls[0][0]
        assert launched_command == _VERBATIM_COMPOUND_INSTALL_COMMAND, (
            'the compound install command must reach Popen unmodified — got:\n'
            f'{launched_command!r}')
        # Not re-wrapped in the OS-window quote sandwich that broke this originally.
        assert 'cmd /k' not in launched_command
        assert not launched_command.startswith('start ')
    finally:
        mc_state.terminal_sessions.clear()
        mc_state.terminal_sessions.update(before_terms)
        mc_state.tracked_processes.clear()
        mc_state.tracked_processes.update(before_procs)


def test_launch_terminal_for_binary_rejects_compound_command(monkeypatch):
    """Requirement (Ron, 2026-09-22): the OS-window path
    (`_launch_terminal_for_binary`) is still used by the two interactive
    sign-in callers with a single resolved binary path. It must reject a
    compound shell command rather than silently producing the exact broken
    `start "" cmd /k` quote-nesting this whole bug was. Confirms the guard
    fires on the real captured fixture and returns an error string without
    touching subprocess at all."""
    from mc.blueprints import agent_routes as ar

    def _boom(*a, **kw):
        raise AssertionError('must not attempt to launch a rejected compound command')
    monkeypatch.setattr(ar.subprocess, 'Popen', _boom)
    monkeypatch.setattr(ar.sys, 'platform', 'win32')

    err = ar._launch_terminal_for_binary(_VERBATIM_COMPOUND_INSTALL_COMMAND)
    assert err is not None
    assert 'compound' in err.lower()

    # A genuine single binary path still launches normally (no regression
    # for the real callers: interactive claude/codex/etc sign-in).
    monkeypatch.setattr(ar.subprocess, 'Popen', lambda *a, **kw: None)
    assert ar._launch_terminal_for_binary(r'C:\Users\x\AppData\Roaming\npm\claude.cmd') is None


def test_launch_terminal_for_binary_allows_ampersand_in_path(monkeypatch):
    """The guard must not over-reject. A LONE `&` or `|` sits inside the
    wrapper's own quotes and is harmless; Windows folder names legitimately
    contain `&` (`C:\Tools\A&B\claude.cmd`). Rejecting those would refuse
    an interactive sign-in that worked before the guard existed — a
    regression traded for the bug it was meant to stop."""
    from mc.blueprints import agent_routes as ar

    seen = []
    monkeypatch.setattr(ar.sys, 'platform', 'win32')
    monkeypatch.setattr(ar.subprocess, 'Popen',
                        lambda cmd, **kw: seen.append(cmd))

    assert ar._launch_terminal_for_binary(r'C:\Tools\A&B\claude.cmd') is None
    assert len(seen) == 1 and r'A&B' in seen[0]
