"""Which CDP events re-arm the pane's screencast (MC-1197, mc/browser_screencast_rearm.py)."""
from mc import browser_screencast_rearm as rearm

DEFAULT = {'format': 'jpeg', 'quality': 55}
OWN = {'format': 'jpeg', 'quality': 55, 'maxWidth': 1136, 'maxHeight': 866}


def _cmds(method, session=None, msg_sid='S1', active='S1'):
    return rearm.commands(session if session is not None else {'screencast_params': OWN},
                          method, msg_sid, active, DEFAULT)


def test_load_rearms_with_a_plain_start():
    assert _cmds('Page.frameStoppedLoading') == [('Page.startScreencast', OWN)]


def test_resize_rearms_with_stop_then_start():
    # A bare second start leaves the screencast stalled the same way; only a stop+start
    # makes Chromium send the picture drawn at the new size.
    assert _cmds('Page.frameResized') == [('Page.stopScreencast', {}),
                                         ('Page.startScreencast', OWN)]


def test_minimized_pane_is_never_rearmed():
    s = {'screencast_params': OWN, 'screencast_paused': True}
    assert _cmds('Page.frameResized', s) == []
    assert _cmds('Page.frameStoppedLoading', s) == []


def test_background_tab_is_never_rearmed():
    assert _cmds('Page.frameResized', msg_sid='S2', active='S1') == []
    assert _cmds('Page.frameStoppedLoading', msg_sid='S2', active='S1') == []


def test_other_events_send_nothing():
    for m in ('Page.frameNavigated', 'Page.loadEventFired', 'Page.screencastFrame'):
        assert _cmds(m) == []


def test_session_without_own_params_uses_the_default():
    assert _cmds('Page.frameStoppedLoading', {}) == [('Page.startScreencast', DEFAULT)]


def test_reader_dispatches_every_rearm_event_through_the_module():
    # The reader branches on REARM_EVENTS, so an event added here is re-armed there.
    import inspect
    from mc.blueprints import browser_routes
    src = inspect.getsource(browser_routes)
    assert 'elif method in _rearm.REARM_EVENTS:' in src
    assert "elif method == 'Page.frameStoppedLoading':" not in src
