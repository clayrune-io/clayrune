"""The live browser pane as an assisted-post `Driver` (mc/desk_assisted_post.py).

Drives the tab the person is looking at in a pane that is ALREADY OPEN on the account's
profile: it never launches a browser and never touches a profile that is not open, so the
signed-in session is the person's own. One extra CDP connection to that tab, off the pane's
reader thread (same shape as `browser_routes._cdp_evaluate`).

Every selector and label arrives from route data in code (`mc/desk_assisted_<network>.py`).
Nothing the page says is used to build an expression: values go in through `json.dumps`.
"""
from __future__ import annotations

import json
import time

from mc.blueprints import browser_routes as br
from mc.browser_hidden_read import _Conn
from mc.desk_assisted_post import AssistedError

_SETTLE_POLL_S = 0.25

# Visible candidates for css, each with the label a person would read: its own text, else its
# aria-label, else an icon's aria-label/title inside it.
_FIND_JS = r"""(function(css, want){
  var out = null;
  function visible(el){ var r = el.getBoundingClientRect(); var s = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && s.display !== 'none'; }
  function label(el){
    var t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (t) return t;
    var a = el.getAttribute('aria-label'); if (a) return a.trim();
    var i = el.querySelector('svg[aria-label], [aria-label], title');
    if (i) return ((i.getAttribute && i.getAttribute('aria-label')) || i.textContent || '').trim();
    return ''; }
  var nodes = document.querySelectorAll(css);
  for (var n = 0; n < nodes.length; n++) {
    var el = nodes[n]; if (!visible(el)) continue;
    var l = label(el);
    if (want === null) return {x: 0, y: 0, label: l};
    if (want.indexOf(l.toLowerCase()) >= 0) { var r = el.getBoundingClientRect();
      return {x: r.left + r.width / 2, y: r.top + r.height / 2, label: l}; }
  }
  return null;
})"""


class PaneDriver:
    def __init__(self, session: dict):
        self.session = session
        self._page = None

    # -- plumbing -------------------------------------------------------------------
    def _conn(self) -> _Conn:
        if self._page is None:
            targets = br._cdp_targets(self.session, timeout=2)
            page = br._pick_page_target(targets, self.session.get('live_url') or self.session.get('url') or '')
            if not page:
                raise AssistedError('the browser pane has no open page', 'pane_not_open')
            self._page = _Conn(br._cdp_page_conn(self.session, page, timeout=5))
        return self._page

    def _eval(self, expression: str):
        ok, value = br._cdp_evaluate(self.session, expression, timeout=5)
        return value if ok else None

    def close(self) -> None:
        if self._page is not None:
            self._page.close()
            self._page = None

    # -- Driver ---------------------------------------------------------------------
    def navigate(self, url: str) -> None:
        self.session['url'] = url
        self._page = None                       # the tab may be replaced by the navigation
        self.session['cmd_queue'].put(('Page.navigate', {'url': url}))
        end = time.time() + 20
        while time.time() < end:
            if self._eval('document.readyState') == 'complete' and \
                    (self._eval('location.href') or '').startswith(url.rstrip('/')):
                return
            time.sleep(_SETTLE_POLL_S)

    def wait(self, css: str, timeout_s: float) -> bool:
        end = time.time() + timeout_s
        while time.time() < end:
            if self._eval(f'{_FIND_JS}({json.dumps(css)}, null)'):
                return True
            time.sleep(_SETTLE_POLL_S)
        return False

    def locate(self, css: str, labels: list[str]) -> dict | None:
        wanted = [w.lower() for w in labels]
        return self._eval(f'{_FIND_JS}({json.dumps(css)}, {json.dumps(wanted)})')

    def present(self, css: str, label: str) -> bool:
        return bool(self.locate(css, [label]))

    def click_at(self, x: float, y: float) -> None:
        c = self._conn()
        for typ in ('mouseMoved', 'mousePressed', 'mouseReleased'):
            c.call('Input.dispatchMouseEvent', {'type': typ, 'x': x, 'y': y, 'button': 'left',
                                                'buttons': 1 if typ == 'mousePressed' else 0, 'clickCount': 1})

    def attach(self, css: str, paths: list[str]) -> bool:
        c = self._conn()
        r = c.call('Runtime.evaluate', {'expression': f'document.querySelector({json.dumps(css)})',
                                        'returnByValue': False})
        object_id = ((r.get('result') or {}).get('objectId'))
        if not object_id:
            return False
        c.call('DOM.setFileInputFiles', {'objectId': object_id, 'files': list(paths)})
        return True

    def insert_text(self, css: str, text: str) -> bool:
        focused = self._eval(f'(function(){{var e=document.querySelector({json.dumps(css)});'
                             'if(!e)return false;e.focus();return document.activeElement===e||e.contains(document.activeElement);})()')
        if not focused:
            return False
        self._conn().call('Input.insertText', {'text': text})
        return True

    def replace_text(self, css: str, text: str) -> bool:
        # Studio pre-fills the title from the filename. Select only this field's
        # contents before typing, including when clearing an empty description.
        focused = self._eval(f'''(function(){{
          const e=document.querySelector({json.dumps(css)}); if(!e)return false;
          e.focus();
          if(document.activeElement!==e&&!e.contains(document.activeElement))return false;
          if(e instanceof HTMLInputElement||e instanceof HTMLTextAreaElement)e.select();
          else {{const r=document.createRange();r.selectNodeContents(e);
            const s=window.getSelection();s.removeAllRanges();s.addRange(r);}}
          return true;
        }})()''')
        if not focused:
            return False
        c = self._conn()
        c.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'Backspace', 'code': 'Backspace', 'windowsVirtualKeyCode': 8})
        c.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'Backspace', 'code': 'Backspace', 'windowsVirtualKeyCode': 8})
        c.call('Input.insertText', {'text': text})
        return True


def pane_driver_for(profile: str) -> PaneDriver:
    session = br._session_using_profile(profile)
    if not session:
        raise AssistedError(f"open the browser pane on profile '{profile}' and sign in first: the "
                            'post is filled in inside your own signed-in pane', 'pane_not_open')
    return PaneDriver(session)
