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


# ── read-only loopback smokes — prove wire() bound the global deps ────────────

def test_providers_endpoint_ok(client):
    resp = client.get('/api/agent/providers')
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), (list, dict))


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


def test_model_pin_same_tier_not_pending(client):
    # Pinning the tier already running is a no-op switch (no respawn needed).
    _seed_model_session('mdl-3', model='opus')
    r = client.post('/api/project/mdl-proj/agent/mdl-3/model',
                    json={'model': 'claude-opus-4-8'})
    assert r.status_code == 200
    assert r.get_json()['pending'] is False


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
    assert resp.get_json() == {'found': False}


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
        assert resp.get_json() == {'found': True, 'trigger_type': 'schedule'}
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
    assert resp.get_json() == {'found': True, 'trigger_type': 'hivemind_worker'}


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
        assert resp.get_json() == {'found': True, 'trigger_type': 'manual'}
    finally:
        mc_state.agent_sessions.pop('s2', None)
