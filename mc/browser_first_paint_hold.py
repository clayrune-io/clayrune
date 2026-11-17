"""Hold a new pane's navigations until its window has drawn once (MC-1200).

A whole pane session could come up with ZERO screencast frames: about 1 run in 30 of
tools/smoke/browser-pane-binary-stream.mjs check 6 under CPU load. Measured 2026-11-17
with Chromium tracing from launch (Chromium 151, --headless=new):

  * The browser window's FIRST root compositor frame embedded the initial about:blank
    tab's surface at the size the launch fit had just asked for. Before the window
    submitted that frame, the launch's own Page.navigate swapped the tab's document,
    which destroyed that surface's frame sink (viz InvalidateFrameSinkId 11 ms before
    the root frame arrived). viz queued the root frame pending on a surface that could
    now never exist ("SurfaceQueuedPending") and never activated it: 5.7 s with no
    display draw at all. The browser compositor then skipped every BeginFrame as
    kDrawThrottled (its one frame was never acked), the renderer was throttled as
    "undrawn" (rAF at ~1 Hz), and the screencast had nothing to capture. A
    Page.captureScreenshot unstuck it, which is why the smoke's diagnostic "fixed" it.
  * In both healthy traces the same root frame reached viz BEFORE the swap and
    activated at once. In every failing session (6 of 6 logged) no frame had arrived
    before the navigation; in the passing sessions of the same runs one had.

So the order is the fault: a document swap must not overtake the window's first
frame. The pane therefore starts the screencast on the tab as launched, and sends the
launch navigation (plus any Page.navigate queued meanwhile, e.g. the pane's typed URL)
only once the first frame arrives, which proves the display has an active root
surface. HOLD_S bounds the wait so a page that never paints is no worse than before:
the navigation goes out when the hold expires, exactly as it used to go out at once.

Not a watchdog: nothing is retried or restarted, the hold only orders two things the
launch already does.
"""

HOLD_S = 3.0
_KEY = 'first_paint_hold'


def arm(session, now, pending):
    """Start holding navigations; `pending` is the launch's own (method, params) list."""
    session[_KEY] = {'until': now + HOLD_S, 'pending': list(pending), 'armed_at': now}


def holding(session):
    return session.get(_KEY) is not None


def defer(session, method, params):
    """True if `method` was queued behind the hold instead of being sent now."""
    hold = session.get(_KEY)
    if hold is None or method != 'Page.navigate':
        return False
    hold['pending'].append((method, params))
    return True


def release(session, now, reason):
    """End the hold; return the deferred commands in the order they were asked for."""
    hold = session.pop(_KEY, None)
    if hold is None:
        return []
    session['first_paint_hold_result'] = {'reason': reason,
                                          'waited_s': round(now - hold['armed_at'], 3),
                                          'released': len(hold['pending'])}
    return hold['pending']


def expired(session, now):
    hold = session.get(_KEY)
    return hold is not None and now >= hold['until']
