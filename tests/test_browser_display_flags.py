"""MC-1090: the pane's GPU and screen launch flags (mc/browser_display_flags.py)."""
import pytest

from mc import browser_display_flags as bdf
from mc import state
from mc.blueprints import browser_routes as br
from tests.test_browser_routes import _stub_launch_deps, profiles  # noqa: F401  (fixture)


@pytest.mark.parametrize('window, screen', [
    ((1296, 951), (1920, 1080)),      # the default 1280x800 view
    ((1920, 1080), (1920, 1080)),
    ((1921, 1000), (2560, 1440)),
    ((2576, 1751), (3840, 2160)),     # _MAX_VIEW plus window chrome
    ((5000, 1000), (5000, 2160)),
])
def test_screen_always_holds_the_launch_window(window, screen):
    assert bdf.screen_for(*window) == screen
    sw, sh = bdf.screen_for(*window)
    assert sw >= window[0] and sh >= window[1]


def test_largest_launch_window_fits_a_listed_screen():
    w = br._MAX_VIEW[0] + br.WINDOW_CHROME_W
    h = br._MAX_VIEW[1] + br.WINDOW_CHROME_H
    assert bdf.screen_for(w, h) in bdf._SCREENS


def test_dpr1_gpu_on():
    assert bdf.display_args(1296, 951, 1.0) == ['--screen-info={0,0 1920x1080}']


def test_hidpi_screen_info_is_physical_pixels_with_the_ratio():
    assert bdf.display_args(1296, 951, 2.0) == ['--screen-info={0,0 3840x2160 devicePixelRatio=2}']
    assert bdf.display_args(1296, 951, 1.5) == ['--screen-info={0,0 2880x1620 devicePixelRatio=1.5}']


def test_gpu_off_restores_disable_gpu():
    assert bdf.display_args(1296, 951, 1.0, gpu=False)[0] == '--disable-gpu'


def test_no_scale_flag_ever():
    # --force-device-scale-factor double-divides a --screen-info size (measured
    # on Chromium 151); the two must never be combined.
    for dpr in (1.0, 1.25, 2.0):
        assert not any('force-device-scale-factor' in a for a in bdf.display_args(1296, 951, dpr))


def test_launch_drops_disable_gpu_by_default(profiles, monkeypatch):
    captured = _stub_launch_deps(monkeypatch, profiles)
    session, err = br._launch_browser('proj', 'https://example.com')
    assert err is None
    assert '--disable-gpu' not in captured['args']
    assert '--screen-info={0,0 1920x1080}' in captured['args']


def test_launch_config_can_turn_the_gpu_back_off(profiles, monkeypatch):
    captured = _stub_launch_deps(monkeypatch, profiles)
    monkeypatch.setitem(state.CONFIG, 'browser_pane_gpu', False)
    session, err = br._launch_browser('proj', 'https://example.com')
    assert err is None
    assert '--disable-gpu' in captured['args']
