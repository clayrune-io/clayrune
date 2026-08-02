"""Unit tests for the browser-pane module (mc/blueprints/browser_routes.py).

Cover the pure helpers and the optional/lazy feature-gate without launching a
real Chromium (that's integration territory, exercised manually)."""
import os

import pytest

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
