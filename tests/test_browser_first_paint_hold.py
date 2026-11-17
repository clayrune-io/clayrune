"""The launch navigation waits for the window's first frame (MC-1200, mc/browser_first_paint_hold.py)."""
from mc import browser_first_paint_hold as hold

LAUNCH = ('Page.navigate', {'url': 'https://example.com/'})


def _armed(now=10.0):
    s = {}
    hold.arm(s, now, [LAUNCH])
    return s


def test_launch_navigation_is_held_until_released():
    s = _armed()
    assert hold.holding(s)
    assert hold.release(s, 10.4, 'frame') == [LAUNCH]
    assert not hold.holding(s)
    assert s['first_paint_hold_result'] == {'reason': 'frame', 'waited_s': 0.4, 'released': 1}


def test_queued_navigate_is_deferred_behind_the_launch_one_in_order():
    s = _armed()
    typed = {'url': 'https://typed.example/'}
    assert hold.defer(s, 'Page.navigate', typed) is True
    assert hold.release(s, 11.0, 'frame') == [LAUNCH, ('Page.navigate', typed)]


def test_other_commands_are_never_deferred():
    s = _armed()
    for m in ('Input.dispatchMouseEvent', 'Page.reload', 'Browser.setWindowBounds',
              'Page.startScreencast'):
        assert hold.defer(s, m, {}) is False
    assert hold.release(s, 10.1, 'frame') == [LAUNCH]


def test_nothing_is_deferred_once_released():
    s = _armed()
    hold.release(s, 10.1, 'frame')
    assert hold.defer(s, 'Page.navigate', {'url': 'x'}) is False
    assert hold.release(s, 10.2, 'frame') == []


def test_hold_expires_after_hold_s_so_a_page_that_never_paints_still_navigates():
    s = _armed(now=10.0)
    assert not hold.expired(s, 10.0 + hold.HOLD_S - 0.01)
    assert hold.expired(s, 10.0 + hold.HOLD_S)
    assert hold.release(s, 10.0 + hold.HOLD_S, 'timeout') == [LAUNCH]
    assert not hold.expired(s, 99.0)


def test_unarmed_session_is_untouched():
    s = {}
    assert not hold.holding(s)
    assert not hold.expired(s, 1.0)
    assert hold.defer(s, 'Page.navigate', {'url': 'x'}) is False
    assert hold.release(s, 1.0, 'frame') == []
