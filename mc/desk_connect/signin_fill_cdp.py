"""The browser side of "sign in with a saved login" (slice P2b): one CDP connection to the
pane's page, used for two things only.

    `top()`        the TOP frame's committed URL and security origin, read from the BROWSER
                   (`Page.getFrameTree`). Nothing the page's JavaScript can say changes it; the
                   caller decides from it, before the vault is asked for anything and again
                   immediately before a value is typed.
    `evaluate()`   one expression, run in a fresh ISOLATED WORLD of that top frame
                   (`Page.createIsolatedWorld` + `Runtime.evaluate` with its `contextId`), never
                   the page's own world, so the page's prototypes, listeners and globals cannot
                   see or alter it (`signin_fill_js`). A navigation destroys the world's
                   context, so the expression cannot run in a document it was not made for.

One websocket serves both, so the URL that was checked and the page that is typed into are the
same target. Every failure carries a short machine reason, never the expression (it can hold a
password) and never a page's text.
"""
from __future__ import annotations

import json
from typing import Any

WORLD = 'clayrune-signin'
MAX_EVENTS = 80                     # unrelated CDP events skipped while waiting for one answer


class LinkError(Exception):
    """The pane could not be reached or did not answer. `str()` is a short reason, never page text."""


class PageLink:
    def __init__(self, ws: Any, websocket_mod: Any):
        self._ws = ws
        self._mod = websocket_mod
        self._id = 0

    def _call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        mine = self._id
        try:
            self._ws.send(json.dumps({'id': mine, 'method': method, 'params': params or {}}))
            for _ in range(MAX_EVENTS):
                r = json.loads(self._ws.recv())
                if r.get('id') != mine:
                    continue                                    # an event (frame, target change, ...)
                if 'error' in r:
                    code = (r['error'] or {}).get('code') if isinstance(r['error'], dict) else None
                    raise LinkError(f'cdp_error:{method}:{code}')
                res = r.get('result')
                return res if isinstance(res, dict) else {}
        except LinkError:
            raise
        except Exception as e:
            if isinstance(e, getattr(self._mod, 'WebSocketTimeoutException', ())):
                raise LinkError(f'timeout:{method}') from None
            raise LinkError(f'cdp_error:{method}:{type(e).__name__}') from None
        raise LinkError(f'timeout:{method}')

    def top(self) -> dict:
        """`{'url', 'origin', 'frame_id', 'unreachable'}` of the top frame, from the browser."""
        fr = (self._call('Page.getFrameTree').get('frameTree') or {}).get('frame') or {}
        if not isinstance(fr.get('id'), str):
            raise LinkError('no_frame')
        return {'url': str(fr.get('url') or ''), 'origin': str(fr.get('securityOrigin') or ''),
                'frame_id': fr['id'], 'unreachable': bool(fr.get('unreachableUrl'))}

    def evaluate(self, expression: str) -> tuple[bool, Any]:
        """`(True, value)` of the expression run in a new isolated world of the top frame, or
        `(False, reason)`."""
        try:
            frame_id = self.top()['frame_id']
            world = self._call('Page.createIsolatedWorld', {'frameId': frame_id, 'worldName': WORLD, 'grantUniveralAccess': False})
            ctx = world.get('executionContextId')
            if not isinstance(ctx, int):
                return False, 'no_world'
            res = self._call('Runtime.evaluate', {'expression': expression, 'contextId': ctx,
                                                  'returnByValue': True, 'awaitPromise': False})
        except LinkError as e:
            return False, str(e)
        if res.get('exceptionDetails'):
            return False, 'eval_exception'
        result = res.get('result') or {}
        if result.get('subtype') == 'error':
            return False, 'eval_exception'
        return True, result.get('value')

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass

    def __enter__(self) -> 'PageLink':
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def connect(session: Any, timeout: float = 8) -> PageLink:
    """A `PageLink` to the page target the pane is showing. Raises LinkError."""
    import urllib.request
    from mc.blueprints import browser_routes as _b
    websocket = _b._import_ws()
    if websocket is None:
        raise LinkError('no_websocket_client')
    try:
        targets = json.load(urllib.request.urlopen(f'http://127.0.0.1:{session.get("port")}/json/list', timeout=2))
        page = _b._pick_page_target(targets, session.get('live_url') or session.get('url') or '')
        if not page or not page.get('webSocketDebuggerUrl'):
            raise LinkError('no_page_target')
        ws = websocket.create_connection(page['webSocketDebuggerUrl'], max_size=None, timeout=timeout)
    except LinkError:
        raise
    except Exception as e:
        raise LinkError(f'connect_failed:{type(e).__name__}') from None
    return PageLink(ws, websocket)
