"""Unit tests for the browser-pane module (mc/blueprints/browser_routes.py).

Cover the pure helpers and the optional/lazy feature-gate without launching a
real Chromium (that's integration territory, exercised manually)."""
from mc.blueprints import browser_routes as br


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
