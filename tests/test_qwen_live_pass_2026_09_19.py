"""Qwen-path defects from the second Qwen live pass (2026-09-19, run
0919123134, qwen3-coder-plus; evidence docs/_journal/provider-live/qwen/,
gitignored). Claude passed the same cells at ~32k first turn.

1. First turn 71k-187k "tokens". Three stacked counting errors plus one real
   cost, all measured against qwen-code 0.23.4 the same day:
   - `result.usage.input_tokens` SUMS every request of the turn (a read_file
     turn: 52,562 = 26,262 + 26,300);
   - `normalize_context_tokens` then ADDS `cache_read_input_tokens`, which
     Qwen already counts inside `input_tokens` (that turn: 96,412 reported);
   - the sum also includes a background request the CLI makes after EVERY
     turn, the managed auto-memory extractor (39,733 = 27,274 + 12,459 on a
     one-word reply). That one is real spend, not just a counting error, so
     it is switched off through qwen's lowest-precedence defaults layer.
2. Usage never measured: the driver had no Qwen per-request source. The
   CLI's own chat recording has one per request.
3. MCP not wired: a project that never opted into MCP trimming dispatched
   Qwen with the deny-all sentinel, so the fixture server Claude and Gemini
   reached was "not configured" on Qwen.
4. No rollover: the non-Claude follow-up branch returned before the token
   trigger was consulted, so no Qwen/Gemini/Codex chat could ever roll.
Every test here fails on 5f92ad3.
"""
import inspect
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
for p in (PROJECT_ROOT, PROJECT_ROOT / 'tools' / 'provider-live'):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pytest  # noqa: E402

import mc.agent_runtime as art  # noqa: E402
from mc.agent_runtime import QwenRuntime, SessionHandle  # noqa: E402
import live_gates as G  # noqa: E402

QSID = '68d97ce8-b069-452b-895c-30af1c8bca41'


def _ui(prompt_id, rid, inp, cached, out):
    return {'type': 'system', 'subtype': 'ui_telemetry', 'provenance': 'system',
            'systemPayload': {'uiEvent': {
                'event.name': 'qwen-code.api_response', 'prompt_id': prompt_id,
                'response_id': rid, 'input_token_count': inp,
                'cached_content_token_count': cached, 'output_token_count': out}}}


def _assistant(prompt, cached, out):
    return {'type': 'assistant', 'provenance': 'assistant_output',
            'usageMetadata': {'promptTokenCount': prompt, 'candidatesTokenCount': out,
                              'cachedContentTokenCount': cached,
                              'totalTokenCount': prompt + out}}


# The read_file turn recorded live (shape and numbers verbatim), plus the
# extractor request an auto-memory-on run adds after it.
RECORDING = [
    {'type': 'user', 'provenance': 'real_user'},
    _ui(f'{QSID}########0', 'chatcmpl-1', 26262, 21925, 36),
    _assistant(26262, 21925, 36),
    {'type': 'tool_result', 'provenance': 'tool_result'},
    _ui(f'{QSID}########0', 'chatcmpl-2', 26300, 21925, 2),
    _assistant(26300, 21925, 2),
    _ui(f'{QSID}#managed-auto-memory-extractor-a8acbbfa#0', 'chatcmpl-3', 12459, 0, 80),
    _ui(f'{QSID}########0', 'chatcmpl-2', 26300, 21925, 2),  # re-appended copy
]
RESULT_USAGE = {'input_tokens': 52562, 'output_tokens': 38,
                'cache_read_input_tokens': 43850, 'total_tokens': 52600}


def _write_recording(tmp_path, records=RECORDING):
    f = tmp_path / f'{QSID}.jsonl'
    f.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
    return f


# ── 1. context of the turn = last request, not the sum ──────────────────────

class TestTurnContext:
    def test_old_normalization_is_the_inflated_figure(self):
        # Pins the bug: what the shared reader used to store for this turn.
        assert art.normalize_context_tokens(RESULT_USAGE) == 96412

    def test_qwen_runtime_reads_last_request_from_recording(self, tmp_path, monkeypatch):
        f = _write_recording(tmp_path)
        rt = QwenRuntime()
        monkeypatch.setattr(rt, 'transcript_path', lambda pp, sid: f if sid == QSID else None)
        handle = SessionHandle(mc_session_id='m1', provider='qwen', mode='A',
                               project_path='/p', project_id='p',
                               session_dict={'provider_session_id': QSID})
        assert rt.turn_context_tokens(handle, RESULT_USAGE, {'num_turns': 2}) == 26300

    def test_shared_reader_stores_the_per_request_figure(self, tmp_path, monkeypatch):
        f = _write_recording(tmp_path)
        monkeypatch.setattr(QwenRuntime, 'transcript_path',
                            lambda self, pp, sid: f if sid == QSID else None)

        class _P:
            stdout = iter([
                json.dumps({'type': 'system', 'subtype': 'init', 'session_id': QSID}) + '\n',
                json.dumps({'type': 'result', 'subtype': 'success', 'session_id': QSID,
                            'is_error': False, 'num_turns': 2, 'usage': RESULT_USAGE}) + '\n'])
            pid = 4242

            def wait(self):
                return 0

            def poll(self):
                return 0

        proc = _P()
        session = {'log_lines': [], 'proc': proc, 'status': 'running'}
        handle = SessionHandle(mc_session_id='m1', provider='qwen', mode='A',
                               project_path='/p', project_id='p', session_dict=session,
                               meta={'callbacks': {}})
        art._mode_a_reader(proc, handle, QwenRuntime())
        assert session['context_tokens'] == 26300

    def test_no_recording_single_request_uses_input_alone(self):
        usage = {'input_tokens': 26238, 'cache_read_input_tokens': 20000}
        assert art.qwen_turn_context_tokens(None, usage, 1) == 26238

    def test_no_recording_multi_request_is_unknown(self):
        assert art.qwen_turn_context_tokens(None, RESULT_USAGE, 2) is None

    def test_other_runtimes_keep_normalization(self):
        # Codex used to be the example here; it now overrides too
        # (tests/test_codex_turn_context_tokens.py).
        from mc.agent_runtime import OpenCodeRuntime
        h = SessionHandle(mc_session_id='m', provider='opencode', mode='A',
                          project_path='/p', project_id='p', session_dict={})
        assert OpenCodeRuntime().turn_context_tokens(h, {'input_tokens': 10,
                                                         'cache_read_input_tokens': 5}) == 15


class TestAutoMemoryExtractorOff:
    def _capture_dispatch_env(self, monkeypatch):
        captured = {}

        def _fake(self, cmd, full_prompt, project_path, project_id, task, mc_sid,
                  session_dict, incognito, env, callbacks, register_process, **kw):
            captured['env'] = env
            return SessionHandle(mc_session_id=mc_sid, provider='qwen', mode='A',
                                 project_path=project_path, project_id=project_id,
                                 session_dict=session_dict if session_dict is not None else {})

        monkeypatch.setattr(art, '_mode_a_dispatch', _fake)
        rt = QwenRuntime()
        rt._bin_cache = 'qwen'
        rt.dispatch(project_path='/p', task='x', session_dict={})
        return captured['env']

    def test_dispatch_points_qwen_at_defaults_with_extractor_off(self, tmp_path, monkeypatch):
        monkeypatch.setenv('MC_DATA_DIR', str(tmp_path))
        monkeypatch.delenv('QWEN_CODE_SYSTEM_DEFAULTS_PATH', raising=False)
        env = self._capture_dispatch_env(monkeypatch)
        path = Path(env['QWEN_CODE_SYSTEM_DEFAULTS_PATH'])
        assert path.parent == tmp_path / '.clayrune'
        data = json.loads(path.read_text(encoding='utf-8'))
        assert data['memory']['enableManagedAutoMemory'] is False

    def test_write_followup_carries_it_too(self, tmp_path, monkeypatch):
        monkeypatch.setenv('MC_DATA_DIR', str(tmp_path))
        monkeypatch.delenv('QWEN_CODE_SYSTEM_DEFAULTS_PATH', raising=False)
        captured = {}

        class _Stdin:
            def write(self, s): pass
            def close(self): pass

        class _Proc:
            stdin = _Stdin()
            stdout = iter([])
            pid = 1
            def wait(self): return 0
            def poll(self): return None

        def _popen(*a, **k):
            captured['env'] = k.get('env')
            return _Proc()

        monkeypatch.setattr(art.subprocess, 'Popen', _popen)
        rt = QwenRuntime()
        rt._bin_cache = 'qwen'
        rt.write_followup(SessionHandle(mc_session_id='s', provider='qwen', mode='A',
                                        project_path='/p', project_id='p',
                                        session_dict={'log_lines': [], 'proc': None}), 'hi')
        assert captured['env']['QWEN_CODE_SYSTEM_DEFAULTS_PATH'].endswith('qwen-system-defaults.json')

    def test_user_defaults_path_is_left_alone(self, tmp_path, monkeypatch):
        monkeypatch.setenv('MC_DATA_DIR', str(tmp_path))
        monkeypatch.setenv('QWEN_CODE_SYSTEM_DEFAULTS_PATH', '/users/own.json')
        env = art._inject_qwen_defaults_env({})
        assert 'QWEN_CODE_SYSTEM_DEFAULTS_PATH' not in env


# ── 2. measured usage in the live driver ────────────────────────────────────

class TestDriverMeasuredUsage:
    def test_ingest_every_request_measured_deduplicated(self):
        text = ''.join(json.dumps(r) + '\n' for r in RECORDING)
        calls = G.ingest_native_usage('qwen', text, 'mc1')
        assert [c.provenance for c in calls] == [G.MEASURED] * 3
        assert [c.context for c in calls] == [26262, 26300, 12459]
        assert calls[0].cache_read == 21925 and calls[0].input == 26262 - 21925

    def test_driver_reads_the_isolated_homes_recording(self, tmp_path):
        import codex_run
        d = tmp_path / '.qwen' / 'projects' / 'c--x' / 'chats'
        d.mkdir(parents=True)
        (d / f'{QSID}.jsonl').write_text('{"a":1}\n', encoding='utf-8')

        class _Inst:
            home = str(tmp_path)

        class _Ctx:
            vendor = 'qwen'
            inst = _Inst()

        assert codex_run.read_native_transcript(_Ctx(), {'provider_session_id': QSID}) == '{"a":1}\n'


# ── 3. MCP reaches Qwen ─────────────────────────────────────────────────────

FIXTURE = {'command': 'python', 'args': ['fixture_mcp_server.py', 'ACC153']}


class TestQwenMcp:
    @pytest.fixture()
    def ar(self, monkeypatch):
        from mc.blueprints import agent_routes
        from mc import mcp as mcp_mod
        monkeypatch.setattr(mcp_mod, '_read_global_servers',
                            lambda: {'clayrune_fixture': dict(FIXTURE),
                                     'web': {'type': 'http', 'url': 'https://h/mcp'}})
        monkeypatch.setattr(mcp_mod, '_read_project_servers', lambda pp: {})
        return agent_routes

    def test_not_opted_in_gets_the_fleet_gemini_gets(self, ar):
        out = json.loads(ar._runtime_mcp_config_json({'project_path': '/p'}, 'qwen'))
        assert out['mcpServers']['clayrune_fixture'] == FIXTURE
        cmd = QwenRuntime().build_command(mcp_config_json=json.dumps(out))
        i = cmd.index('--allowed-mcp-server-names')
        assert 'clayrune_fixture' in cmd[i + 1:]
        assert '__clayrune_none__' not in cmd

    def test_http_server_uses_qwens_http_key_not_sse_url(self, ar):
        out = json.loads(ar._runtime_mcp_config_json({'project_path': '/p'}, 'qwen'))
        assert out['mcpServers']['web'] == {'httpUrl': 'https://h/mcp'}

    def test_opted_in_set_is_converted_and_still_trimmed(self, ar, monkeypatch):
        monkeypatch.setattr(ar, '_mcp_server_catalog', lambda project: {
            'clayrune_fixture': dict(FIXTURE), 'web': {'type': 'http', 'url': 'https://h/mcp'}})
        p = {'project_path': '/p', 'enabled_mcp_servers': ['web']}
        out = json.loads(ar._runtime_mcp_config_json(p, 'qwen'))
        assert out == {'mcpServers': {'web': {'httpUrl': 'https://h/mcp'}}}

    def test_other_providers_unchanged(self, ar):
        # claude/codex read the project's MCP config natively — this resolver
        # is only consulted for the gemini-cli-shaped providers (qwen and, as
        # of vendor-parity gap 3, gemini itself). See TestGeminiMcp below.
        assert ar._runtime_mcp_config_json({'project_path': '/p'}, 'claude') == ''
        assert ar._runtime_mcp_config_json({'project_path': '/p'}, 'codex') == ''

    def test_dispatch_site_uses_it(self, ar):
        src = inspect.getsource(ar._dispatch_via_runtime)
        assert '_runtime_mcp_config_json(p, provider_name)' in src


# ── 3b. MCP reaches Gemini (vendor-parity gap 3, docs/VENDOR_HARNESS_MATRIX.md)

class TestGeminiMcp:
    """Gemini used to get its servers from `mc.mcp.sync_to_gemini`, which
    MERGES into the user's own `~/.gemini/settings.json` (additive-only,
    AGENT_RULES forbids editing that file) — so a Gemini dispatch saw MC's
    fleet PLUS whatever the user already had there (tradingview,
    sequential-thinking, the raw `mail` server AGENT_RULES forbids agents
    reading directly), live-measured 2026-09-25. Gemini now joins the same
    resolver qwen already used (`_runtime_mcp_config_json`), and
    `GeminiRuntime.build_command` turns that into `--allowed-mcp-server-names`
    restricted to EXACTLY those names."""

    @pytest.fixture()
    def ar(self, monkeypatch):
        from mc.blueprints import agent_routes
        from mc import mcp as mcp_mod
        monkeypatch.setattr(mcp_mod, '_read_global_servers',
                            lambda: {'clayrune_fixture': dict(FIXTURE),
                                     'web': {'type': 'http', 'url': 'https://h/mcp'}})
        monkeypatch.setattr(mcp_mod, '_read_project_servers', lambda pp: {})
        return agent_routes

    def test_not_opted_in_gets_the_trimmed_fleet(self, ar):
        out = json.loads(ar._runtime_mcp_config_json({'project_path': '/p'}, 'gemini'))
        assert out['mcpServers']['clayrune_fixture'] == FIXTURE

    def test_build_command_restricts_to_exactly_those_names(self, ar):
        from mc.agent_runtime import GeminiRuntime
        out = ar._runtime_mcp_config_json({'project_path': '/p'}, 'gemini')
        cmd = GeminiRuntime().build_command(mcp_config_json=out)
        i = cmd.index('--allowed-mcp-server-names')
        names = cmd[i + 1:]
        assert 'clayrune_fixture' in names
        assert 'web' in names
        assert '__clayrune_none__' not in names
        # The leak this fix closes: neither tradingview nor the raw `mail`
        # server (both live only in the user's own ~/.gemini/settings.json,
        # never touched by this resolver) may appear in the restricted set.
        assert 'tradingview' not in names
        assert 'mail' not in names

    def test_empty_config_reaches_the_deny_all_sentinel(self):
        from mc.agent_runtime import GeminiRuntime
        cmd = GeminiRuntime().build_command(mcp_config_json='')
        i = cmd.index('--allowed-mcp-server-names')
        assert cmd[i + 1:i + 2] == ['__clayrune_none__']

    def test_malformed_config_fails_closed_not_open(self):
        from mc.agent_runtime import GeminiRuntime
        cmd = GeminiRuntime().build_command(mcp_config_json='{not json')
        i = cmd.index('--allowed-mcp-server-names')
        assert cmd[i + 1:i + 2] == ['__clayrune_none__']

    def test_session_settings_path_declares_exactly_the_servers(self, tmp_path, monkeypatch):
        from mc import agent_runtime as ar_mod
        monkeypatch.setattr(ar_mod, '_guardrail_hooks_dir', lambda: tmp_path)
        monkeypatch.setattr(ar_mod, '_guardrail_launch_file', lambda vendor: None)
        cfg = json.dumps({'mcpServers': {'clayrune_fixture': dict(FIXTURE)}})
        path = ar_mod._gemini_session_settings_path(cfg)
        assert path is not None and path.is_file()
        written = json.loads(path.read_text(encoding='utf-8'))
        assert written['mcpServers'] == {'clayrune_fixture': dict(FIXTURE)}

    def test_inject_gemini_env_points_at_the_session_settings_file(self, tmp_path, monkeypatch):
        from mc import agent_runtime as ar_mod
        monkeypatch.setattr(ar_mod, '_guardrail_hooks_dir', lambda: tmp_path)
        monkeypatch.setattr(ar_mod, '_guardrail_launch_file', lambda vendor: None)
        cfg = json.dumps({'mcpServers': {'clayrune_fixture': dict(FIXTURE)}})
        env = ar_mod._inject_gemini_env({}, cfg)
        assert env['GEMINI_CLI_SYSTEM_SETTINGS_PATH'].endswith('.json')
        assert Path(env['GEMINI_CLI_SYSTEM_SETTINGS_PATH']).is_file()

    def test_dispatch_never_edits_the_users_global_settings_file(self):
        """Do NOT edit ~/.gemini/settings.json — the whole point of this
        fix. dispatch()/write_followup() must not call sync_to_gemini or
        anything that writes there."""
        import inspect as _inspect
        from mc.agent_runtime import GeminiRuntime
        for src in (_inspect.getsource(GeminiRuntime.dispatch),
                    _inspect.getsource(GeminiRuntime.write_followup)):
            assert 'sync_to_gemini' not in src
            assert '.gemini/settings.json' not in src


# ── 4. token rollover on the non-Claude follow-up path ──────────────────────

class _RecRuntime:
    def __init__(self):
        self.calls = []

    def transcript_path(self, project_path, session_id):
        return None  # no transcript -> labeled fallback handoff

    def write_followup(self, handle, message):
        self.calls.append((dict(handle.session_dict), message))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import local_auth as la
    from mc.delegation_delivery import DeliveryStore

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    pp = tmp_path / 'proj'
    pp.mkdir()
    project = {'id': 'p1', 'project_path': str(pp), 'provider': 'qwen'}
    monkeypatch.setattr(ar, 'load_project', lambda pid: project)
    monkeypatch.setattr(ar, '_delivery_store', DeliveryStore(tmp_path / 'd.db'))
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [])
    monkeypatch.setattr(ar._memory_turn, 'refresh_for_turn', lambda *a, **k: {'block': ''})
    monkeypatch.setattr(ar._behavior_tail, 'render', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_fresh_context_for', lambda *a, **k: 'FRESH CONTEXT')
    monkeypatch.setitem(mc_state.CONFIG, 'context_rollover_tokens', 200_000)
    activity = []
    monkeypatch.setattr(ar, '_log_agent_activity', lambda pid, line: activity.append(line))
    rt = _RecRuntime()
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: rt)
    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    server.app.config['TESTING'] = True
    try:
        yield {'client': server.app.test_client(), 'sessions': mc_state.agent_sessions,
               'rt': rt, 'activity': activity}
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snapshot)


def _qsession(ctx):
    return {'session_id': 'q1', 'project_id': 'p1', 'provider': 'qwen', 'mode': 'A',
            'status': 'idle', 'task': 'chat', 'log_lines': ['> Ron: hi', 'hello'],
            'started_at': '2026-09-19T20:00:00Z', 'proc': None, 'process_alive': False,
            'provider_session_id': QSID, 'context_tokens': ctx,
            'last_output_time': 0.0, 'last_status_change_time': 0.0}


def _wait(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def test_over_threshold_qwen_followup_rolls_and_logs(env):
    env['sessions']['q1'] = _qsession(233_881)
    r = env['client'].post('/api/project/p1/agent/followup',
                           json={'session_id': 'q1', 'message': 'keep going'})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _wait(lambda: env["rt"].calls), env["sessions"]["q1"]["log_lines"]
    seen_session, msg = env['rt'].calls[0]
    assert 'provider_session_id' not in seen_session, 'must start a fresh thread, not --resume'
    assert msg.endswith('keep going') and msg != 'keep going'
    assert any('233k tokens' in l for l in env['activity']), env['activity']
    assert any('233k tokens' in l for l in env['sessions']['q1']['log_lines'])


def test_under_threshold_qwen_followup_resumes(env):
    env['sessions']['q1'] = _qsession(40_000)
    env['client'].post('/api/project/p1/agent/followup',
                       json={'session_id': 'q1', 'message': 'keep going'})
    assert _wait(lambda: env["rt"].calls), env["sessions"]["q1"]["log_lines"]
    seen_session, msg = env['rt'].calls[0]
    assert seen_session['provider_session_id'] == QSID
    assert msg == 'keep going'
    assert not any('Auto-fresh' in l for l in env['activity'])
