"""Second app windows for the frozen desktop app (pywebview).

WKWebView (the frozen Mac app) swallows a script-invoked `window.open()` — see
`openExternal` in static/index.html — so the dashboard's Pop Out button cannot
open a real second window from JS there. pywebview's `js_api` bridge can: the
page calls `window.pywebview.api.open_chat_window(...)` and Python opens a
second native window on the same local server.

The bridge builds the URL itself from a project + session id. It never accepts
a URL from the page, so a script in the webview cannot use it to open anything
but this app's own chat-only view.

Public methods here are exposed to page JavaScript by pywebview (every
attribute not starting with `_`), so the module handle, the port and the window
table are underscore-private.
"""

import threading
from urllib.parse import urlencode


class PopoutApi:
    """pywebview `js_api` object: open / focus one chat window per session."""

    def __init__(self, webview_module, port, log=print):
        self._webview = webview_module
        self._port = port
        self._log = log
        self._lock = threading.Lock()
        self._windows = {}  # session_id -> pywebview Window

    def _url(self, project_id, session_id):
        qs = urlencode({'popout': '1', 'p': project_id, 's': session_id})
        return f'http://127.0.0.1:{self._port}/?{qs}'

    def open_chat_window(self, project_id, session_id, title='Clayrune chat'):
        """Open the chat-only view for one conversation in its own window.

        A conversation already open in a window is brought to the front rather
        than duplicated. Returns {'ok': bool, 'existing': bool}.
        """
        project_id = project_id if isinstance(project_id, str) else ''
        session_id = session_id if isinstance(session_id, str) else ''
        if not project_id or not session_id:
            return {'ok': False, 'error': 'project_id and session_id required'}
        title = title if isinstance(title, str) and title else 'Clayrune chat'
        with self._lock:
            win = self._windows.get(session_id)
            if win is not None:
                try:
                    win.restore()
                    win.show()
                    return {'ok': True, 'existing': True}
                except Exception as e:
                    # The window is gone (closed event raced us) — open a fresh one.
                    self._log(f'[popout] focus existing window failed: {e}')
                    self._windows.pop(session_id, None)
            try:
                win = self._webview.create_window(
                    title[:120],
                    url=self._url(project_id, session_id),
                    width=1000, height=860, min_size=(380, 420),
                )
            except Exception as e:
                self._log(f'[popout] create_window failed: {e}')
                return {'ok': False, 'error': str(e)}
            self._windows[session_id] = win
            try:
                win.events.closed += lambda sid=session_id: self._forget(sid)
            except Exception as e:
                self._log(f'[popout] could not watch window close: {e}')
        return {'ok': True, 'existing': False}

    def _forget(self, session_id):
        with self._lock:
            self._windows.pop(session_id, None)

    def close_all(self):
        """Destroy every popped window (the main window closed)."""
        with self._lock:
            wins = list(self._windows.values())
            self._windows.clear()
        for w in wins:
            try:
                w.destroy()
            except Exception as e:
                self._log(f'[popout] destroy failed: {e}')
