"""Open authorization URLs server-side so resolved app fields stay out of JSON."""
from mc.core import _log


def open_signin(url: str, profile: str) -> str:
    from mc.blueprints import browser_routes
    try:
        # The launcher registers its initial URL in the Process Manager and
        # passes it to Chromium. Navigate over CDP after launch instead.
        session, error = browser_routes._launch_browser(None, 'about:blank', profile=profile)
        if error or session is None:
            raise RuntimeError()
        if session.get('url') != url:
            session['url'] = url
            session['cmd_queue'].put(('Page.navigate', {'url': url}))
        return session['session_id']
    except Exception:
        _log('[desk_oauth] could not open saved-app sign-in pane', flush=True)
        from mc.desk_oauth import OAuthError
        raise OAuthError('pane_failed', 'The sign-in page could not open; try signing in again.', 502) from None
