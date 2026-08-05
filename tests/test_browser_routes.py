"""Unit tests for the browser-pane module (mc/blueprints/browser_routes.py).

Cover the pure helpers and the optional/lazy feature-gate without launching a
real Chromium (that's integration territory, exercised manually)."""
import os

import pytest

from mc import state
from mc.blueprints import browser_routes as br
from mc.state import browser_sessions


def test_free_port_returns_usable_int():
    p = br._free_port()
    assert isinstance(p, int) and 1024 < p < 65536


def test_find_chromium_env_override(tmp_path, monkeypatch):
    fake = tmp_path / "chrome.exe"
    fake.write_text("x")
    monkeypatch.setenv("MC_BROWSER_CHROMIUM", str(fake))
    assert br._find_chromium() == str(fake)


def test_find_chromium_missing_override_falls_through(monkeypatch):
    monkeypatch.setenv("MC_BROWSER_CHROMIUM", "/no/such/chrome.exe")
    # Falls through to the cache glob; returns a path or None, never the bad override.
    assert br._find_chromium() != "/no/such/chrome.exe"


def test_launch_gate_no_chromium(monkeypatch):
    monkeypatch.setattr(br, "_find_chromium", lambda: None)
    session, err = br._launch_browser("proj", "https://example.com")
    assert session is None and "Chromium not found" in err


def test_launch_gate_no_websocket(monkeypatch):
    monkeypatch.setattr(br, "_find_chromium", lambda: "C:/fake/chrome.exe")
    monkeypatch.setattr(br, "_import_ws", lambda: None)
    session, err = br._launch_browser("proj", "https://example.com")
    assert session is None and "websocket-client" in err


def test_view_dims_are_the_render_viewport():
    assert (br.VIEW_W, br.VIEW_H) == (1280, 800)


# ── Persistent profiles ──────────────────────────────────────────────────────

@pytest.fixture()
def profiles(tmp_path, monkeypatch):
    """Both profile roots redirected into tmp_path — these tests delete dirs."""
    eph = tmp_path / 'browser_profiles'
    named = tmp_path / 'browser_profiles_named'
    eph.mkdir()
    named.mkdir()
    monkeypatch.setattr(br, '_profiles_root', lambda: str(eph))
    monkeypatch.setattr(br, '_named_profiles_root', lambda: str(named))
    # The sweep is server-process-only (see SWEEP_ENABLED); these tests are
    # exercising it deliberately, against redirected roots under tmp_path.
    monkeypatch.setattr(br, 'SWEEP_ENABLED', True)
    browser_sessions.clear()
    yield eph, named
    browser_sessions.clear()


def test_named_root_is_not_inside_the_swept_root():
    """LOAD-BEARING: sweep_orphan_profiles() deletes everything under the
    throwaway root that no live session owns. A saved login nested in there
    would be swept the moment its session ended."""
    eph = os.path.normcase(os.path.abspath(br._profiles_root()))
    named = os.path.normcase(os.path.abspath(br._named_profiles_root()))
    assert not named.startswith(eph + os.sep) and named != eph


def test_the_sweep_leaves_saved_profiles_alone(profiles):
    eph, named = profiles
    (eph / 'deadbeef').mkdir()
    (named / 'reddit').mkdir()
    (named / 'reddit' / 'Cookies').write_text('x')
    br.sweep_orphan_profiles()
    assert not (eph / 'deadbeef').exists()      # orphan: gone
    assert (named / 'reddit' / 'Cookies').exists()  # login: kept


@pytest.mark.parametrize('bad', ['', '../escape', 'a/b', 'a\\b', 'x' * 65,
                                 '.hidden', 'a b', None])
def test_bad_profile_names_are_refused(bad, profiles):
    assert br._profile_dir(bad) is None


def test_profile_dir_stays_under_its_root(profiles):
    _eph, named = profiles
    assert br._profile_dir('reddit') == os.path.join(str(named), 'reddit')
    # Case and stray whitespace are canonicalised rather than refused — the
    # name is a label the user types, and 'Reddit' meaning a second profile
    # would be a trap, not a feature.
    assert br._profile_dir('  Reddit ') == br._profile_dir('reddit')


def test_teardown_keeps_a_named_profile_but_drops_a_throwaway(profiles):
    eph, named = profiles
    throwaway = eph / 'abc123'
    throwaway.mkdir()
    saved = named / 'reddit'
    saved.mkdir()
    br._kill_browser_session({'user_data_dir': str(throwaway), 'profile': None})
    br._kill_browser_session({'user_data_dir': str(saved), 'profile': 'reddit'})
    assert not throwaway.exists()
    assert saved.exists(), 'closing the pane must not sign the profile out'


def test_launching_an_open_profile_adopts_that_session(profiles, monkeypatch):
    """Two Chromiums on one user-data-dir corrupt it."""
    monkeypatch.setattr(br, '_find_chromium', lambda: 'C:/fake/chrome.exe')
    monkeypatch.setattr(br, '_import_ws', lambda: object())
    live = {'session_id': 'sid-1', 'profile': 'reddit', 'status': 'running'}
    browser_sessions['sid-1'] = live
    session, err = br._launch_browser('proj', 'https://reddit.com', profile='reddit')
    assert err is None
    assert session is live and session['reused'] is True


def test_a_stopped_session_does_not_block_relaunching_its_profile(profiles, monkeypatch):
    monkeypatch.setattr(br, '_find_chromium', lambda: None)   # stop before spawn
    browser_sessions['sid-1'] = {'session_id': 'sid-1', 'profile': 'reddit',
                                 'status': 'stopped'}
    session, err = br._launch_browser('proj', 'x', profile='reddit')
    assert session is None and 'Chromium not found' in err


def test_invalid_profile_is_rejected_before_anything_launches(profiles, monkeypatch):
    monkeypatch.setattr(br, '_find_chromium', lambda: 'C:/fake/chrome.exe')
    monkeypatch.setattr(br, '_import_ws', lambda: object())
    session, err = br._launch_browser('proj', 'x', profile='../escape')
    assert session is None and 'profile name' in err


# ── Flushing the profile on teardown ─────────────────────────────────────────
# proc.kill() is TerminateProcess: Chromium writes cookies/localStorage on clean
# shutdown, so hard-killing a signed-in profile threw the login away while the
# directory survived to look healthy.

class _FakeProc:
    def __init__(self, exits=True):
        self.pid, self._exits, self.killed, self.waited = 4242, exits, False, False

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True
        if not self._exits:
            raise RuntimeError('still running')
        return 0


def test_named_profile_is_closed_gracefully_not_killed(profiles, monkeypatch):
    saved = profiles[1] / 'reddit'
    saved.mkdir()
    calls = []
    monkeypatch.setattr(br, '_graceful_close', lambda s, **k: calls.append(s) or True)
    proc = _FakeProc()
    br._kill_browser_session({'user_data_dir': str(saved), 'profile': 'reddit',
                              'proc': proc, 'port': 1})
    assert len(calls) == 1, 'a saved login must be flushed before the process dies'
    assert not proc.killed, 'hard kill would discard the cookies Chromium still holds'


def test_a_hung_chromium_still_gets_killed(profiles, monkeypatch):
    """Graceful close is an attempt, not a promise — never leak the process."""
    saved = profiles[1] / 'reddit'
    saved.mkdir()
    monkeypatch.setattr(br, '_graceful_close', lambda s, **k: False)
    proc = _FakeProc()
    br._kill_browser_session({'user_data_dir': str(saved), 'profile': 'reddit',
                              'proc': proc, 'port': 1})
    assert proc.killed


def test_throwaway_profiles_are_not_waited_on(profiles, monkeypatch):
    """Its dir is deleted seconds later, so there is nothing to flush and no
    reason to spend the shutdown budget closing it politely."""
    throwaway = profiles[0] / 'abc123'
    throwaway.mkdir()
    monkeypatch.setattr(br, '_graceful_close',
                        lambda s, **k: pytest.fail('should not flush a throwaway'))
    proc = _FakeProc()
    br._kill_browser_session({'user_data_dir': str(throwaway), 'profile': None,
                              'proc': proc, 'port': 1})
    assert proc.killed and not throwaway.exists()


# ── The sweep may only run in the server process ─────────────────────────────

def test_sweep_is_a_noop_outside_the_server_process(profiles, monkeypatch):
    """It calls anything in the throwaway root that browser_sessions doesn't
    know about an orphan — but only the server's registry knows what is live.
    Any other importer (a test, a debug script, a second MC) would delete a
    running pane's profile out from under it, which is exactly what happened
    on 2026-08-05."""
    eph = profiles[0]
    (eph / 'live-session').mkdir()
    monkeypatch.setattr(br, 'SWEEP_ENABLED', False)
    br.sweep_orphan_profiles()
    assert (eph / 'live-session').exists()


# ── Default profile (config) ─────────────────────────────────────────────────

def test_unnamed_launch_uses_the_configured_default(profiles, monkeypatch):
    monkeypatch.setitem(state.CONFIG, 'browser_default_profile', 'main')
    monkeypatch.setattr(br, '_find_chromium', lambda: 'C:/fake/chrome.exe')
    monkeypatch.setattr(br, '_import_ws', lambda: object())
    live = {'session_id': 'sid-1', 'profile': 'main', 'status': 'running'}
    browser_sessions['sid-1'] = live
    session, err = br._launch_browser('proj', 'https://example.com')
    assert err is None and session is live, 'unnamed launch should resolve to "main"'


def test_ephemeral_opts_out_of_the_configured_default(profiles, monkeypatch):
    monkeypatch.setitem(state.CONFIG, 'browser_default_profile', 'main')
    monkeypatch.setattr(br, '_find_chromium', lambda: None)   # stop before spawn
    browser_sessions['sid-1'] = {'session_id': 'sid-1', 'profile': 'main',
                                 'status': 'running'}
    session, err = br._launch_browser('proj', 'x', ephemeral=True)
    # Reached the spawn gate instead of adopting the live "main" session.
    assert session is None and 'Chromium not found' in err


def test_no_default_configured_keeps_the_throwaway_behaviour(profiles, monkeypatch):
    monkeypatch.setitem(state.CONFIG, 'browser_default_profile', '')
    assert br._default_profile() is None
