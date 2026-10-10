"""Apply the current dashboard viewer's size and device mode to a pane."""
from __future__ import annotations

import threading


def apply_view(session, view, mobile=None):
    # Serialise the whole transition, not only the real-window fit. A phone
    # resize must not interleave its metrics with a desktop takeover.
    from mc.blueprints import browser_routes as br

    with session.setdefault('viewer_mode_lock', threading.Lock()):
        session['view'] = view
        session['screencast_params'] = br._screencast_params_for(session.get('dpr', 1), view)
        is_mobile = bool(mobile) if mobile is not None else session.get('device_mode') == 'mobile'
        previous = session.get('device_mode')
        session['mobile'] = is_mobile
        if previous != ('mobile' if is_mobile else 'desktop'):
            # MC-1083: a pane that turned desktop left no trace of what turned
            # it. Name the transition and the report that asked for it.
            br._log(f"[browser] {session.get('session_id')} device mode {previous} -> "
                    f"{'mobile' if is_mobile else 'desktop'} (viewport {view}, "
                    f"reported mobile={mobile})", flush=True)
        q = session['cmd_queue']
        for cmd in br._device_mode_commands(session, is_mobile, view):
            q.put(cmd)
        if is_mobile:
            # Below Chromium's window floor, use only matching mobile metrics.
            if previous == 'desktop':
                q.put(('Page.reload', {}))
            if session.get('status') == 'running' and not session.get('screencast_paused'):
                q.put(('Page.stopScreencast', {}))
                q.put(('Page.startScreencast', session['screencast_params']))
        else:
            # Queuing the clear is insufficient: fitting uses another CDP
            # connection and may measure before the reader drains that queue.
            if previous == 'mobile':
                br._fit_and_rearm(session, reset_device=True)
                q.put(('Page.reload', {}))
            else:
                br._fit_and_rearm(session)
