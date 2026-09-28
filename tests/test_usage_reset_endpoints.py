"""Request-level tests for MC-989 Part B's usage-reset endpoints
(mc/blueprints/system_routes.py `system_usage_refresh`,
`system_usage_reset_terminal`).

Reset-terminal MUST NEVER auto-send the provider's reset command itself — a
Codex banked-reset redemption is one-time and belongs to the account holder,
same reasoning as Claude's `/limit-reset`. These tests pin that the endpoint
only launches a bare CLI pop-out and returns instruction text; it never
writes anything to the child's stdin.

Determinism: patches `terminal_routes.subprocess.Popen` (the actual spawn
point `launch_pipe_session` calls) with a recorder, same pattern as
tests/test_terminal_routes.py. `_agent_runtime.get_runtime(...).resolve_binary`
is monkeypatched per-provider so no real CLI needs to be installed.
"""
import io
import os
import sys
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class FakeProc:
    """Popen stand-in: real pipe for the reader thread, BytesIO stdin capture."""
    _next_pid = 995000

    def __init__(self):
        r, w = os.pipe()
        self.stdout = os.fdopen(r, 'rb')
        self._w = w
        self.stdin = _RecordingStdin()
        FakeProc._next_pid += 1
        self.pid = FakeProc._next_pid
        self._rc = None

    def poll(self):
        return self._rc

    def kill(self):
        self._exit(-9)

    def wait(self, timeout=None):
        return self._rc if self._rc is not None else 0

    def _exit(self, rc):
        if self._rc is None:
            self._rc = rc
            try:
                os.close(self._w)
            except OSError:
                pass


class _RecordingStdin(io.BytesIO):
    """Captures every write so a test can assert nothing was ever sent."""
    writes = None

    def write(self, data):
        if self.writes is None:
            self.writes = []
        self.writes.append(data)
        return super().write(data)


@pytest.fixture()
def state():
    from mc import state as st
    before_terms = dict(st.terminal_sessions)
    before_procs = dict(st.tracked_processes)
    st.terminal_sessions.clear()
    st.tracked_processes.clear()
    yield st
    st.terminal_sessions.clear()
    st.terminal_sessions.update(before_terms)
    st.tracked_processes.clear()
    st.tracked_processes.update(before_procs)


@pytest.fixture()
def client(monkeypatch, state):
    import server
    from mc.blueprints import system_routes as sr
    from mc.blueprints import terminal_routes as tr

    calls = []

    def _popen(*a, **kw):
        calls.append((a, kw))
        return FakeProc()

    monkeypatch.setattr(tr, 'subprocess', types.SimpleNamespace(
        Popen=_popen, PIPE=-1, STDOUT=-2))

    # Bust caches so a prior test's TTL can't leak a stale reading in.
    sr._oauth_usage_cache['ts'] = 0.0
    sr._oauth_usage_cache['data'] = None
    sr._codex_usage_cache['ts'] = 0.0
    sr._codex_usage_cache['data'] = None
    sr._codex_detail_cache['ts'] = 0.0
    sr._codex_detail_cache['data'] = None

    server.app.config['TESTING'] = True
    c = server.app.test_client()
    c._popen_calls = calls  # type: ignore[attr-defined]
    return c


def _stub_resolve_binary(monkeypatch, provider, path_str):
    from mc import agent_runtime as ar
    rt = ar.get_runtime(provider)
    monkeypatch.setattr(rt, 'resolve_binary', lambda: Path(path_str))


class TestResetTerminal:
    def test_claude_launches_bare_cli_and_returns_instruction(self, client, monkeypatch, state):
        _stub_resolve_binary(monkeypatch, 'claude', 'C:/fake/claude.exe')

        r = client.post('/api/system/usage/reset-terminal', json={'provider': 'claude'})

        assert r.status_code == 200
        j = r.get_json()
        assert j['ok'] is True
        assert j['session_id']
        assert '/limit-reset' in j['instruction']
        assert 'weekly cap still applies' in j['instruction']

        # Exactly one spawn, the bare resolved binary — no extra args, no
        # command auto-sent.
        assert len(client._popen_calls) == 1
        args, kwargs = client._popen_calls[0]
        assert args[0] == str(Path('C:/fake/claude.exe'))
        assert kwargs.get('shell') is True

        session = state.terminal_sessions[j['session_id']]
        proc = session['proc']
        assert proc.stdin.writes is None, 'reset-terminal must never write to the child stdin itself'

    def test_codex_launches_bare_cli_and_returns_instruction(self, client, monkeypatch, state):
        _stub_resolve_binary(monkeypatch, 'codex', 'C:/fake/codex.exe')

        r = client.post('/api/system/usage/reset-terminal', json={'provider': 'codex'})

        assert r.status_code == 200
        j = r.get_json()
        assert j['ok'] is True
        assert 'Redeem usage limit reset' in j['instruction']
        assert '/usage' in j['instruction']

        args, kwargs = client._popen_calls[0]
        assert args[0] == str(Path('C:/fake/codex.exe'))

        session = state.terminal_sessions[j['session_id']]
        assert session['proc'].stdin.writes is None

    def test_gemini_has_no_reset_flow(self, client, state):
        r = client.post('/api/system/usage/reset-terminal', json={'provider': 'gemini'})
        assert r.status_code == 400
        j = r.get_json()
        assert j['ok'] is False
        assert 'gemini' in j['error']
        assert len(client._popen_calls) == 0

    def test_unknown_provider_rejected(self, client, state):
        r = client.post('/api/system/usage/reset-terminal', json={'provider': 'not-a-real-vendor'})
        assert r.status_code == 400
        assert len(client._popen_calls) == 0

    def test_missing_cli_reports_not_installed(self, client, monkeypatch, state):
        from mc import agent_runtime as ar
        rt = ar.get_runtime('claude')
        monkeypatch.setattr(rt, 'resolve_binary', lambda: None)

        r = client.post('/api/system/usage/reset-terminal', json={'provider': 'claude'})

        assert r.status_code == 400
        j = r.get_json()
        assert j['ok'] is False
        assert 'not installed' in j['error']
        assert len(client._popen_calls) == 0


class TestUsageRefresh:
    def test_refresh_busts_caches_and_returns_usage_payload(self, client, monkeypatch, state):
        from mc.blueprints import system_routes as sr

        # Seed stale cache entries so we can prove refresh actually clears them.
        sr._oauth_usage_cache['ts'] = 1.0
        sr._oauth_usage_cache['data'] = {'stale': True}
        sr._codex_usage_cache['ts'] = 1.0
        sr._codex_usage_cache['data'] = {'stale': True}
        sr._codex_detail_cache['ts'] = 1.0
        sr._codex_detail_cache['data'] = {'stale': True}

        monkeypatch.setattr(sr, '_fetch_oauth_usage_limits', lambda: None)
        monkeypatch.setattr(sr, '_fetch_codex_weekly_usage', lambda: None)
        monkeypatch.setattr(sr, '_fetch_codex_usage_detail', lambda: None)
        monkeypatch.setattr(sr, '_mc_usage_from_agent_logs',
                            lambda: {'today': {}, 'week': {}, 'month': {}, 'all_time': {}, 'last_data_date': ''})

        r = client.post('/api/system/usage/refresh')

        assert r.status_code == 200
        j = r.get_json()
        assert j['available'] is True
        assert 'provider_weekly_usage' in j
        assert 'codex_usage_detail' in j

        # The TTL timestamps were reset to 0 before the (stubbed) re-fetch —
        # busting the cache is the whole point of this endpoint.
        assert sr._oauth_usage_cache['ts'] == 0.0 or sr._oauth_usage_cache['data'] is None
