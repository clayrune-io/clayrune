"""Which CDP events re-arm the pane's screencast, and how (MC-1197).

The pane only ever shows what `Page.screencastFrame` delivers, and headless Chromium only
delivers a frame when it decides to. Two events leave it deciding not to:

  Page.frameStoppedLoading  a cross-document navigation STOPS the active screencast, so
                            every load re-arms it with a plain start (prior art: the
                            black pane fixed in 54369d6 / ee8e2cf).
  Page.frameResized         a main-frame resize does NOT stop the screencast, but the
                            picture drawn at the new size is often never sent. Measured
                            2026-11-17 on a static page, no CPU load: a bare
                            Browser.setWindowBounds was followed by zero frames in 7 of
                            10 resizes (6 s watch), and a direct launch onto a static page
                            stayed black in 3 of 4 because Chromium resizes the new
                            document by 1 px right after its first paint (frame 1280x799,
                            then frameResized, then nothing). Under CPU load the same
                            thing happens after the pane's typed navigation, which is what
                            browser-pane-binary-stream.mjs kept failing on. The screencast
                            is live in that state -- any repaint is delivered -- so a
                            static page stays black until something on it changes.
                            stop + start makes Chromium send the current picture
                            (_fit_and_rearm already does exactly this after its own
                            resizes). A bare second start is not used: the 2026-09-26
                            measurement in _fit_and_rearm saw it stall the same way.

Both are skipped while the pane is minimized (`screencast_paused`: a hidden pane must
cost nothing, and a page event must not undo that) and for any tab but the active one
(only the active tab has a screencast; a background tab's load or resize must not start
one on it). `Page.frameResized` is a main-frame event in Chromium (no params, fired from
the page agent's main-frame resize), so a page resizing its own iframes does not reach it.
"""

REARM_EVENTS = {
    'Page.frameStoppedLoading': 'start',
    'Page.frameResized': 'restart',
}


def commands(session, method, msg_sid, active_sid, default_params):
    """The CDP commands (method, params) to send on the event's own session, or []."""
    kind = REARM_EVENTS.get(method)
    if kind is None or session.get('screencast_paused') or msg_sid != active_sid:
        return []
    params = session.get('screencast_params', default_params)
    if kind == 'start':
        return [('Page.startScreencast', params)]
    return [('Page.stopScreencast', {}), ('Page.startScreencast', params)]
