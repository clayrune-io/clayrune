"""Backlog 1d940d0f: `claude_signin_channel` ('auto' default | 'terminal').

'terminal' = Claude sign-in only through the MC-928 real-PTY session; the
one-time-code relay (`/auth-login-remote/code`) refuses and the piped
URL-capture path is never taken, with or without a PTY backend. 'auto' is
today's behaviour and must not change.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.agent_runtime as _ar
from mc import claude_signin_channel as csc
from mc import state


@pytest.fixture()
def client():
    import server
    server.app.config['TESTING'] = True
    return server.app.test_client()


def _channel(monkeypatch, value):
    monkeypatch.setitem(state.CONFIG, 'claude_signin_channel', value)


def _no_captured_sessions():
    from mc.blueprints import agent_routes as ar
    with ar._captured_login_lock:
        ar._captured_login_sessions.clear()


class TestChannelResolution:
    def test_default_is_auto(self, monkeypatch):
        monkeypatch.delitem(state.CONFIG, 'claude_signin_channel', raising=False)
        assert csc.signin_channel() == 'auto'
        assert not csc.terminal_only()

    def test_server_declares_the_default(self):
        import server  # noqa: F401
        # The defaults table is built in server.py; a fresh CONFIG carries it.
        assert state.CONFIG.get('claude_signin_channel', 'auto') == 'auto'

    @pytest.mark.parametrize('raw,want', [
        ('auto', 'auto'), ('', 'auto'), (None, 'auto'), ('  AUTO ', 'auto'),
        ('terminal', 'terminal'), (' Terminal', 'terminal'),
        ('termnal', 'terminal'),   # typo lands on the stricter side
    ])
    def test_normalisation(self, monkeypatch, raw, want):
        _channel(monkeypatch, raw)
        assert csc.signin_channel() == want


class TestTerminalChannel:
    def test_uses_pty_and_never_the_piped_path(self, client, monkeypatch):
        from mc.blueprints import agent_routes as ar
        _channel(monkeypatch, 'terminal')
        with patch.object(_ar.get_runtime('claude'), 'resolve_binary', return_value=Path('/fake/claude')), \
             patch.object(ar.pty_backend, 'pty_available', return_value=True), \
             patch.object(ar, 'launch_pty_session', return_value=('abc123', None)) as mock_launch, \
             patch.object(ar.subprocess, 'Popen') as mock_popen:
            resp = client.post('/api/agent/claude/auth-login-remote')
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body['ok'] is True and body['pty'] is True
        assert body['session_id'] == 'abc123'
        assert body['remote_capable'] is True
        mock_popen.assert_not_called()
        mock_launch.assert_called_once()
        args, kwargs = mock_launch.call_args
        assert args[1] == str(Path('/fake/claude'))
        assert kwargs['argv_extra'] == ['auth', 'login']

    def test_no_pty_is_a_clear_error_not_a_fallback(self, client, monkeypatch):
        from mc.blueprints import agent_routes as ar
        _channel(monkeypatch, 'terminal')
        with patch.object(_ar.get_runtime('claude'), 'resolve_binary', return_value=Path('/fake/claude')), \
             patch.object(ar.pty_backend, 'pty_available', return_value=False), \
             patch.object(ar, 'launch_pty_session') as mock_launch, \
             patch.object(ar.subprocess, 'Popen') as mock_popen:
            resp = client.post('/api/agent/claude/auth-login-remote')
        assert resp.status_code == 503
        body = json.loads(resp.data)
        assert body['ok'] is False
        assert body['signin_channel'] == 'terminal'
        assert 'real-PTY' in body['error']
        mock_launch.assert_not_called()
        mock_popen.assert_not_called()

    def test_failed_pty_launch_is_an_error_not_a_fallback(self, client, monkeypatch):
        from mc.blueprints import agent_routes as ar
        _channel(monkeypatch, 'terminal')
        with patch.object(_ar.get_runtime('claude'), 'resolve_binary', return_value=Path('/fake/claude')), \
             patch.object(ar.pty_backend, 'pty_available', return_value=True), \
             patch.object(ar, 'launch_pty_session', return_value=(None, 'boom')), \
             patch.object(ar.subprocess, 'Popen') as mock_popen:
            resp = client.post('/api/agent/claude/auth-login-remote')
        assert resp.status_code == 503
        body = json.loads(resp.data)
        assert body['error'] == 'boom' and body['signin_channel'] == 'terminal'
        mock_popen.assert_not_called()

    def test_code_relay_refuses_with_403_and_never_writes_stdin(self, client, monkeypatch):
        from mc.blueprints import agent_routes as ar
        _channel(monkeypatch, 'terminal')
        _no_captured_sessions()
        fake_proc = type('P', (), {})()
        fake_proc.stdin = type('S', (), {'write': lambda *a, **k: (_ for _ in ()).throw(
            AssertionError('code written to the CLI stdin'))})()
        with ar._captured_login_lock:
            ar._captured_login_sessions['claude'] = {
                'proc': fake_proc, 'status': 'url_ready', 'url': 'https://x',
                'output': '', 'exit_code': None}
        try:
            resp = client.post('/api/agent/claude/auth-login-remote/code', json={'code': 'abc'})
        finally:
            _no_captured_sessions()
        assert resp.status_code == 403
        assert 'terminal' in json.loads(resp.data)['error']

    def test_other_providers_are_not_affected(self, client, monkeypatch):
        """The key is Claude's. Gemini's PTY branch behaves as it always did."""
        from mc.blueprints import agent_routes as ar
        _channel(monkeypatch, 'terminal')
        with patch.object(_ar.GeminiRuntime, 'resolve_binary', return_value=Path('/fake/gemini')), \
             patch.object(ar.pty_backend, 'pty_available', return_value=True), \
             patch.object(ar, 'launch_pty_session', return_value=('g1', None)) as mock_launch:
            resp = client.post('/api/agent/gemini/auth-login-remote')
        assert resp.status_code == 200
        assert mock_launch.call_args[1]['argv_extra'] is None

    def test_provider_list_remote_login_flag_needs_a_pty(self, client, monkeypatch, tmp_path):
        from conftest import stub_codex_auth_state, stub_codex_models_cache
        from mc.blueprints import agent_routes as ar
        stub_codex_auth_state(monkeypatch)
        stub_codex_models_cache(monkeypatch, tmp_path)
        _channel(monkeypatch, 'terminal')
        with patch.object(ar.pty_backend, 'pty_available', return_value=False):
            rows = json.loads(client.get('/api/agent/providers').data)
        rows = rows['providers'] if isinstance(rows, dict) else rows
        claude = next(r for r in rows if r['name'] == 'claude')
        assert claude['remote_login'] is False


class TestAutoChannelUnchanged:
    def test_auto_still_uses_the_piped_capture_path(self, client, monkeypatch):
        from mc.blueprints import agent_routes as ar
        _channel(monkeypatch, 'auto')
        _no_captured_sessions()

        class _Proc:
            stdin = None
            stdout = None
            def wait(self): return 0
            def kill(self): pass

        with patch.object(_ar.get_runtime('claude'), 'resolve_binary', return_value=Path('/fake/claude')), \
             patch.object(ar, 'launch_pty_session') as mock_launch, \
             patch.object(ar.subprocess, 'Popen', return_value=_Proc()) as mock_popen, \
             patch.object(ar.threading, 'Thread'), \
             patch.object(ar.threading, 'Timer'), \
             patch.object(ar._time, 'time', side_effect=[0, 0, 100, 100, 100]):
            resp = client.post('/api/agent/claude/auth-login-remote')
        _no_captured_sessions()
        assert resp.status_code == 200
        mock_popen.assert_called_once()
        assert mock_popen.call_args[0][0] == [str(Path('/fake/claude')), 'auth', 'login']
        mock_launch.assert_not_called()

    def test_auto_code_route_still_reaches_the_session_check(self, client, monkeypatch):
        _channel(monkeypatch, 'auto')
        _no_captured_sessions()
        resp = client.post('/api/agent/claude/auth-login-remote/code', json={'code': 'x'})
        assert resp.status_code == 404   # 'no login session', NOT the 403 refusal
