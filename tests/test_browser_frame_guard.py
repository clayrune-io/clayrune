"""MC-1083 follow-up: the mobile frame guard's episode logic (no Chromium)."""
from mc import browser_frame_guard as fg


def _mobile(view=(411, 754)):
    return {'session_id': 't', 'device_mode': 'mobile', 'device_mode_view': view,
            'view': view, 'dpr': 2}


def test_matching_frame_never_heals():
    s = _mobile()
    fg.observe(s, 'A', 411.0, 754.0, 0.0)
    assert fg.due(s, 'A', 10.0) is None


def test_mismatch_waits_out_the_grace_then_resends_then_resets():
    s = _mobile()
    fg.observe(s, 'A', 492.0, 754.0, 0.0)
    assert fg.due(s, 'A', 0.5) is None            # a resize may still be landing
    assert fg.due(s, 'A', fg.GRACE_S) == 'resend'
    assert fg.due(s, 'A', fg.GRACE_S + 0.1) is None  # its own frame gets time to arrive
    assert fg.due(s, 'A', fg.GRACE_S + fg.RETRY_S) == 'reset'


def test_gives_up_after_max_tries():
    s = _mobile()
    fg.observe(s, 'A', 1280.0, 754.0, 0.0)
    t, acts = fg.GRACE_S, []
    for _ in range(fg.MAX_TRIES + 3):
        acts.append(fg.due(s, 'A', t))
        t += fg.RETRY_S
    assert [a for a in acts if a] == ['resend'] + ['reset'] * (fg.MAX_TRIES - 1)


def test_matching_frame_ends_the_episode():
    s = _mobile()
    fg.observe(s, 'A', 492.0, 754.0, 0.0)
    assert fg.due(s, 'A', 2.0) == 'resend'
    fg.observe(s, 'A', 411.0, 754.0, 2.1)
    assert fg.due(s, 'A', 9.0) is None
    assert 'since' not in s['frame_guard']


def test_within_tolerance_counts_as_match():
    s = _mobile()
    fg.observe(s, 'A', 411.0 + fg.TOLERANCE_PX, 754.0 - fg.TOLERANCE_PX, 0.0)
    assert fg.due(s, 'A', 10.0) is None


def test_desktop_session_is_left_alone():
    s = {'device_mode': 'desktop', 'view': (1280, 800)}
    fg.observe(s, 'A', 492.0, 754.0, 0.0)
    assert fg.due(s, 'A', 10.0) is None


def test_frame_from_another_tab_does_not_heal_the_active_one():
    s = _mobile()
    fg.observe(s, 'B', 492.0, 754.0, 0.0)
    assert fg.due(s, 'A', 10.0) is None


def test_paused_screencast_is_not_rearmed():
    s = _mobile()
    fg.observe(s, 'A', 492.0, 754.0, 0.0)
    s['screencast_paused'] = True
    assert fg.due(s, 'A', 10.0) is None


def test_view_change_restarts_the_episode():
    # A rotation: frames at the old size are expected until the new override lands.
    s = _mobile()
    fg.observe(s, 'A', 411.0, 754.0, 0.0)
    s['device_mode_view'] = (754, 411)
    fg.observe(s, 'A', 411.0, 754.0, 5.0)
    assert fg.due(s, 'A', 5.5) is None
    assert fg.due(s, 'A', 5.0 + fg.GRACE_S) == 'resend'


def test_heal_commands_target_the_override_and_rearm():
    s = _mobile()
    s['screencast_params'] = {'format': 'jpeg', 'maxWidth': 854}
    fg.observe(s, 'A', 492.0, 754.0, 0.0)
    resend = fg.heal_commands(s, 'resend')
    assert [m for m, _ in resend] == ['Emulation.setDeviceMetricsOverride',
                                      'Page.stopScreencast', 'Page.startScreencast']
    assert resend[0][1]['width'] == 411 and resend[0][1]['height'] == 754
    assert resend[0][1]['mobile'] is True and resend[0][1]['deviceScaleFactor'] == 2
    assert resend[2][1] == s['screencast_params']
    reset = fg.heal_commands(s, 'reset')
    assert reset[0][0] == 'Emulation.clearDeviceMetricsOverride'
    assert reset[1:] == resend
