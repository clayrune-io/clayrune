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
import json
import sys
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
    '/api/agent/<provider>/auth-probe',
    '/api/agent/<provider>/auth-status',
    '/api/agent/provider/<name>/auth',
    '/api/agent/provider/<name>/env',
    '/api/agent/provider/<name>/login-launch',
    '/api/agent/provider/<name>/install-launch',
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
    monkeypatch.setattr(ar, '_launch_terminal_for_binary', lambda command: calls.append(command))

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
    assert calls == [
        'winget install --id OpenJS.NodeJS.LTS -e --silent '
        '--accept-source-agreements --accept-package-agreements '
        '&& set "PATH=%ProgramFiles%\\nodejs;%APPDATA%\\npm;%PATH%" '
        '&& npm install -g @openai/codex'
    ]


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
    monkeypatch.setattr(ar, '_launch_terminal_for_binary', launched.append)
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
    monkeypatch.setattr(ar, '_launch_terminal_for_binary', lambda command: calls.append(command))

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
    monkeypatch.setattr(ar, '_launch_terminal_for_binary', lambda command: calls.append(command))

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
    monkeypatch.setattr(ar, '_launch_terminal_for_binary', launched.append)
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
