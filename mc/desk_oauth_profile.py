"""The named browser profile a Connect-flow sign-in is made in, and when the flow may delete it.

A held sign-in (`desk_oauth_hold`) is made in the browser pane on a NAMED profile
(`desk_oauth.profile_name`). That profile is a saved login at the vendor: the pane the person
signed in on writes cookies into it. When the person backs out, the held sign-in expires, or the
Save does not take it, the flow must not leave that login behind, because nothing in Clayrune
would ever list it against a connection.

Rule (Wren, 2026-10-05): delete the profile ONLY if THIS flow created it. `existed()` is read at
the start of the flow, before the pane opens; a profile that was already there (an earlier
sign-in, or the person's own saved login) is never touched, whatever happens to the flow.

Deleting means closing the Chromium on it FIRST, gracefully (`browser_routes._kill_browser_session`
asks Chromium to close itself, which is also what flushes the profile), and only then removing
the directory: the browser-profile rule in CLAUDE.md. A directory removed under a live Chromium
leaves a half-lived browser writing into nothing, and on Windows the delete fails on the open files.

Best effort and logged, never raised: this runs from a timer thread and from a cancel route.
The module imports `browser_routes` lazily (it is a heavy blueprint module).
"""
from __future__ import annotations

import os
import shutil

from mc.core import _log


def _br():
    from mc.blueprints import browser_routes
    return browser_routes


def existed(name: str) -> bool:
    """True when a saved profile of this name is already on disk. Says nothing about a login."""
    try:
        return bool(_br().named_profile_exists(name))
    except Exception as e:
        _log(f'[desk_oauth] reading whether profile {name!r} exists failed: {type(e).__name__}', flush=True)
        return True            # unknown counts as "was there": the safe answer is never to delete


def forget(name: str | None) -> bool:
    """Close any pane Chromium on the profile (gracefully), then remove its directory.
    True when the directory is gone afterwards. Call it only for a profile this flow created."""
    if not name:
        return False
    try:
        br = _br()
        path = br._profile_dir(name)
        if not path:
            return False
        with br.browser_lock:
            sessions = [s for s in br.browser_sessions.values() if s.get('profile') == name]
        for s in sessions:                       # the pane on it, closed the way a user's close is
            br._kill_browser_session(s)
            with br.browser_lock:
                br.browser_sessions.pop(s.get('session_id'), None)
        if not os.path.isdir(path):
            return True
        shutil.rmtree(path, ignore_errors=True)
        gone = not os.path.isdir(path)
        _log(f'[desk_oauth] sign-in profile {name!r} created by a dropped flow '
             f'{"removed" if gone else "could not be fully removed (a file is still open)"}', flush=True)
        return gone
    except Exception as e:
        _log(f'[desk_oauth] removing sign-in profile {name!r} failed: {type(e).__name__}', flush=True)
        return False
