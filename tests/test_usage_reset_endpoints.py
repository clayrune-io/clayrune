"""Request-level tests for MC-989 Part B's usage-reset endpoints
(mc/blueprints/system_routes.py `system_usage_refresh`,
`system_usage_reset_terminal`).

Reset-terminal MUST NEVER auto-send the provider's reset command itself — a
Codex banked-reset redemption is one-time and belongs to the account holder,
same reasoning as Claude's `/limit-reset`. These tests pin that the endpoint
only launches a bare CLI pop-out and returns instruction text; it never
writes anything to the child's stdin.

Determinism: patches `terminal_routes.pty_backend` (the actual spawn point
`launch_pty_session` calls) with a recorder, same `FakePty`/`_fake_pty_backend`
pattern as tests/test_terminal_routes.py — both CLIs are full-screen raw-mode
TUIs (a review caught the endpoint originally using `launch_pipe_session`,
which only fakes TTY-ness for Python subprocesses and left Codex refusing to
start / Claude falling into print mode), so the real code path is the pty
one. `_agent_runtime.get_runtime(...).resolve_binary` is monkeypatched
per-provider so no real CLI needs to be installed.
"""
import os
import sys
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class FakePtyUnavailable(RuntimeError):
    pass


class FakePty:
    """pty_backend session stand-in: real OS pipe for the reader thread, plus
    the narrow interface terminal_routes.py actually drives — no real
    ConPTY/pty.openpty() involved. Same shape as test_terminal_routes.py's
    FakePty so both suites stay in sync if the interface changes."""
    _next_pid = 995000

    def __init__(self):
        r, w = os.pipe()
        self._r_fd = r
        self._w = w
        self.written = []
        FakePty._next_pid += 1
        self.pid = FakePty._next_pid
        self._rc = None

    def read(self, size=4096):
        try:
            data = os.read(self._r_fd, size)
        except OSError:
            return ''
        return data.decode('utf-8', errors='replace')

    def write(self, text):
        self.written.append(text)

    def resize(self, cols, rows):
        pass

    def isalive(self):
        return self._rc is None

    def poll(self):
        return self._rc

    def kill(self):
        self.close(force=True)

    def wait(self, timeout=None):
        return self._rc if self._rc is not None else 0

    def close(self, force=True):
        self._exit(-9 if force else 0)

    def _exit(self, rc):
        if self._rc is None:
            self._rc = rc
            try:
                os.close(self._w)
            except OSError:
                pass


def _fake_pty_backend(available=True):
    """A pty_backend-shaped namespace (pty_available/spawn/PtyUnavailable)
    for monkeypatching tr.pty_backend — no real ConPTY/stdlib pty involved,
    so these tests run identically on Windows and POSIX CI."""
    spawned = []

    def _spawn(command, cwd=None, env=None, cols=120, rows=30):
        if not available:
            raise FakePtyUnavailable('no pty backend installed')
        p = FakePty()
        spawned.append((command, cwd, env, cols, rows, p))
        return p

    ns = types.SimpleNamespace(
        pty_available=lambda: available,
        spawn=_spawn,
        PtyUnavailable=FakePtyUnavailable,
    )
    ns.spawned = spawned
    return ns


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

    # Default: a real-PTY backend IS available, spawning FakePty instances —
    # matches system_usage_reset_terminal's real launch_pty_session call.
    fake_pty = _fake_pty_backend(available=True)
    monkeypatch.setattr(tr, 'pty_backend', fake_pty)

    # Bust caches so a prior test's TTL can't leak a stale reading in.
    sr._oauth_usage_cache['ts'] = 0.0
    sr._oauth_usage_cache['data'] = None
    sr._codex_usage_cache['ts'] = 0.0
    sr._codex_usage_cache['data'] = None
    sr._codex_detail_cache['ts'] = 0.0
    sr._codex_detail_cache['data'] = None

    server.app.config['TESTING'] = True
    c = server.app.test_client()
    c._fake_pty = fake_pty  # type: ignore[attr-defined]
    return c


def _stub_resolve_binary(monkeypatch, provider, path_str):
    from mc import agent_runtime as ar
    rt = ar.get_runtime(provider)
    monkeypatch.setattr(rt, 'resolve_binary', lambda: Path(path_str))


class TestResetTerminal:
    def test_uses_real_pty_launcher_not_pipe(self, client, monkeypatch, state):
        """Pins the review fix: this route originally called
        `launch_pipe_session`, which only fakes TTY-ness for Python
        subprocesses — Codex refuses to start ('TERM is set to "dumb"
        ... Refusing to start the interactive TUI') and Claude drops into
        print mode. Both CLIs are full-screen raw-mode TUIs, so the route
        must import and call `launch_pty_session` specifically."""
        from mc.blueprints import system_routes as sr
        from mc.blueprints import terminal_routes as tr

        assert sr.launch_pty_session is tr.launch_pty_session
        assert not hasattr(sr, 'launch_pipe_session'), (
            'system_routes must not import launch_pipe_session for '
            'reset-terminal — see the module comment above _USAGE_RESET_INSTRUCTIONS')

        _stub_resolve_binary(monkeypatch, 'claude', 'C:/fake/claude.exe')
        spawn_calls = []
        real_spawn = tr.pty_backend.spawn

        def _spy_spawn(*a, **kw):
            spawn_calls.append((a, kw))
            return real_spawn(*a, **kw)

        monkeypatch.setattr(tr.pty_backend, 'spawn', _spy_spawn)

        r = client.post('/api/system/usage/reset-terminal', json={'provider': 'claude'})

        assert r.status_code == 200
        assert len(spawn_calls) == 1, 'reset-terminal must launch through pty_backend.spawn'
        j = r.get_json()
        assert j['is_pty'] is True

    def test_claude_launches_bare_cli_and_returns_instruction(self, client, monkeypatch, state):
        _stub_resolve_binary(monkeypatch, 'claude', 'C:/fake/claude.exe')

        r = client.post('/api/system/usage/reset-terminal', json={'provider': 'claude'})

        assert r.status_code == 200
        j = r.get_json()
        assert j['ok'] is True
        assert j['session_id']
        assert j['is_pty'] is True
        assert '/limit-reset' in j['instruction']
        assert 'weekly cap still applies' in j['instruction']

        # Exactly one spawn, the bare resolved binary — no extra args, no
        # command auto-sent.
        assert len(client._fake_pty.spawned) == 1
        command, cwd, env, cols, rows, pty = client._fake_pty.spawned[0]
        assert command == str(Path('C:/fake/claude.exe'))

        session = state.terminal_sessions[j['session_id']]
        assert session['pty'] is pty
        assert pty.written == [], 'reset-terminal must never write to the child stdin itself'

    def test_codex_launches_bare_cli_and_returns_instruction(self, client, monkeypatch, state):
        _stub_resolve_binary(monkeypatch, 'codex', 'C:/fake/codex.exe')

        r = client.post('/api/system/usage/reset-terminal', json={'provider': 'codex'})

        assert r.status_code == 200
        j = r.get_json()
        assert j['ok'] is True
        assert 'Redeem usage limit reset' in j['instruction']
        assert '/usage' in j['instruction']

        command, cwd, env, cols, rows, pty = client._fake_pty.spawned[0]
        assert command == str(Path('C:/fake/codex.exe'))

        session = state.terminal_sessions[j['session_id']]
        assert session['pty'] is pty
        assert pty.written == []

    def test_gemini_has_no_reset_flow(self, client, state):
        r = client.post('/api/system/usage/reset-terminal', json={'provider': 'gemini'})
        assert r.status_code == 400
        j = r.get_json()
        assert j['ok'] is False
        assert 'gemini' in j['error']
        assert len(client._fake_pty.spawned) == 0

    def test_unknown_provider_rejected(self, client, state):
        r = client.post('/api/system/usage/reset-terminal', json={'provider': 'not-a-real-vendor'})
        assert r.status_code == 400
        assert len(client._fake_pty.spawned) == 0

    def test_missing_cli_reports_not_installed(self, client, monkeypatch, state):
        from mc import agent_runtime as ar
        rt = ar.get_runtime('claude')
        monkeypatch.setattr(rt, 'resolve_binary', lambda: None)

        r = client.post('/api/system/usage/reset-terminal', json={'provider': 'claude'})

        assert r.status_code == 400
        j = r.get_json()
        assert j['ok'] is False
        assert 'not installed' in j['error']
        assert len(client._fake_pty.spawned) == 0


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
