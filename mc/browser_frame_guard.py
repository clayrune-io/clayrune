"""Keep a mobile pane's screencast frame the size its metrics override declares.

MC-1083 follow-up (Ron, 2026-10-10, phone): x.com drawn at a third of the
pane's width, left-aligned in a black frame, through reloads and Desktop-site
toggles. His screenshot's ratio, 275/857 = 0.32, is 411/1280: a frame the
width of the pane's default 1280px window with the 411px mobile page drawn
in its left part.

Measured on throwaway panes (harness, real Chromium): when a window resize
lands AFTER Emulation.setDeviceMetricsOverride -- the launch-time window pin
racing the pane's first viewport report is one way -- Chromium keeps laying
the page out at the override (innerWidth 411) but captures the screencast at
the window's size (deviceWidth 492 at the 500px window floor). A
stop/start of the screencast does not fix it, and neither does a second
viewport report: `_device_mode_commands` is idempotent on (mode, view), so it
sends nothing. Sending the override again does (492 -> 411 within one frame).

So this does not chase each event that can cause the split. It compares the
frame metadata the pane is drawn from against the override, and re-asserts
the override when they disagree for longer than a resize takes to settle.
Bounded: MAX_TRIES per episode, then it stops and logs.
"""
from __future__ import annotations

from mc.core import _log

GRACE_S = 1.0     # a mode switch or rotation legitimately mismatches this long
RETRY_S = 1.5     # time for a re-assert's own fresh frame to arrive
MAX_TRIES = 3
TOLERANCE_PX = 2


def _expected(session):
    if session.get('device_mode') != 'mobile':
        return None
    view = session.get('device_mode_view') or session.get('view')
    return tuple(view) if view else None


def observe(session, sid, dw, dh, now):
    """A frame from `sid` reported CSS size (dw, dh). Start, continue or end
    a mismatch episode."""
    want = _expected(session)
    st = session.setdefault('frame_guard', {})
    if want is None:
        st.clear()
        return
    if st.get('want') != want:
        st.clear()
        st['want'] = want
    st['sid'] = sid
    st['seen'] = (dw, dh)
    if abs(dw - want[0]) <= TOLERANCE_PX and abs(dh - want[1]) <= TOLERANCE_PX:
        if st.get('tries'):
            _log(f"[browser] {session.get('session_id')} frame back to {want[0]}x{want[1]} "
                 f"after {st['tries']} override re-assert(s)", flush=True)
        st.clear()
        st['want'] = want
        return
    if st.get('since') is None:
        st['since'] = now
        st['tries'] = 0


def due(session, active_sid, now):
    """'resend', 'reset' or None: what the reader should do about a mismatch
    on the active tab right now."""
    st = session.get('frame_guard') or {}
    if st.get('since') is None or st.get('sid') != active_sid:
        return None
    if _expected(session) != st.get('want') or session.get('screencast_paused'):
        return None
    if now - st['since'] < GRACE_S:
        return None
    if st.get('last') is not None and now - st['last'] < RETRY_S:
        return None
    if st['tries'] >= MAX_TRIES:
        if not st.get('gave_up'):
            st['gave_up'] = True
            _log(f"[browser] {session.get('session_id')} frame still {st['seen']} vs override "
                 f"{st['want']} after {MAX_TRIES} re-asserts; leaving it", flush=True)
        return None
    st['tries'] += 1
    st['last'] = now
    action = 'resend' if st['tries'] == 1 else 'reset'
    _log(f"[browser] {session.get('session_id')} frame {st['seen']} != override {st['want']}; "
         f"{action} #{st['tries']}", flush=True)
    return action


def heal_commands(session, action):
    """CDP (method, params) for the active tab. 'reset' clears first, so the
    override is a real change even if Chromium treats an identical one as a
    no-op; then a stop/start, which is what makes headless emit a frame."""
    from mc.blueprints import browser_routes as br

    want = (session.get('frame_guard') or {}).get('want') or _expected(session)
    if not want:
        return []
    vw, vh = want
    cmds = [('Emulation.clearDeviceMetricsOverride', {})] if action == 'reset' else []
    cmds.append(br._mobile_metrics_cmd(vw, vh, session.get('dpr', 1)))
    cmds.append(('Page.stopScreencast', {}))
    cmds.append(('Page.startScreencast', session.get('screencast_params', br._SCREENCAST_PARAMS)))
    return cmds
