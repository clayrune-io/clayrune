"""Read one public page in a temporary, signed-out browser pane (spec "Detection and
trust": "15 seconds for a temporary unsigned-in browser pane and `/api/browser/read`
envelope").

What makes it safe to point at an address a stranger typed:

  * a THROWAWAY profile (`ephemeral`): no cookies, no named profile, deleted on close;
  * the pane's network goes through `guard_proxy.GuardProxy` (Chromium flags below):
    private, loopback, link-local and Clayrune addresses are refused at connect time for
    the first load, every redirect and every subresource, DNS rebinding included;
  * the text comes from the SAME `_READ_JS_TEMPLATE` / `_build_read_envelope` as
    `/api/browser/read` (hidden text stripped and counted, non-HTML refused, untrusted
    envelope), through `browser_routes.ProfilePageReader`, whose only commands are
    `Page.navigate` and a text read: nothing is clicked or typed;
  * NO FALLBACK. A failure is returned as a failure. There is no curl/requests path in
    this file, and none may be added: `guidance` on the envelope says why (the
    2026-08-28 tool-downgrade chain).

`read_page` never raises and always closes the pane; `DiscoveryPane.abort()` is the
deadline / cancel hook and is safe from another thread.
"""
from __future__ import annotations

import threading

from mc.blueprints import browser_routes
from mc.core import _log

PAGE_S = 15.0

# Chromium flags that keep the pane inside the proxy. `<-loopback>` removes Chromium's
# built-in "never proxy localhost / 127.0.0.1 / link-local" rule, so a page cannot reach
# Clayrune (or anything else on this machine or its network) by name, by 127.0.0.1 or by
# a link-local address: all of it arrives at the proxy and is refused there. The ONE
# loopback exception is this pane's own debugging port, which the pane harness itself opens
# in a throwaway tab to read the browser's client hints; a page cannot use it (the
# endpoint answers without CORS headers and its websocket needs an unguessable id).
# The resolver rule makes any lookup that bypasses the proxy fail instead of leaking;
# WebRTC and QUIC are the UDP routes that would otherwise skip an HTTP proxy.
def chromium_args(proxy_port: int, cdp_port: int | None = None) -> list[str]:
    bypass = '<-loopback>' + (f';127.0.0.1:{int(cdp_port)}' if cdp_port else '')
    return [
        f'--proxy-server=http://127.0.0.1:{int(proxy_port)}',
        f'--proxy-bypass-list={bypass}',
        '--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1',
        '--disable-quic',
        '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
        '--disable-background-networking',
        '--disable-component-update',
        '--disable-sync',
        '--no-pings',
    ]


class DiscoveryPane(browser_routes.ProfilePageReader):
    """A `ProfilePageReader` on a throwaway, proxied profile with a 15 s budget."""
    SETTLE_TIMEOUT_S = PAGE_S

    def __init__(self, project_id, proxy_port: int):
        super().__init__(project_id, '')
        self._proxy_port = proxy_port
        self._aborted = False
        self._close_lock = threading.Lock()

    def _open(self, url):
        session, err = browser_routes._launch_browser(
            self.project_id, url, ephemeral=True,
            extra_args=lambda cdp_port: chromium_args(self._proxy_port, cdp_port))
        if err or session is None:
            return self._fail('launch_failed', err or 'browser failed to start')
        self._session = session
        if self._aborted:                    # a deadline fired while Chromium was starting
            self.close()
            return self._fail('cdp_timeout', 'the lookup was stopped')
        return None

    def close(self):
        with self._close_lock:
            super().close()

    def abort(self):
        self._aborted = True
        self.close()


_KIND_TO_CODE = {
    'non_html_content': 'page_not_html',
    'cdp_timeout': 'page_timeout',
    'launch_failed': 'pane_unavailable',
    'own_origin_blocked': 'page_blocked',
}
_MESSAGES = {
    'page_not_html': 'The address does not lead to a web page (it is a file or a data feed), so there is nothing to read.',
    'page_timeout': 'The page did not finish loading in time.',
    'pane_unavailable': 'Clayrune could not start its browser to read the page.',
    'page_blocked': 'Clayrune refused to open that address.',
    'page_unreachable': 'The page could not be reached.',
}


def _fail(code: str, message: str | None = None) -> dict:
    return {'ok': False, 'code': code, 'message': message or _MESSAGES[code]}


def _blocking_refusal(proxy) -> dict | None:
    """The refusal that explains a page that would not load: the address (or a redirect
    from it) was a local or disallowed one. A refused ad or tracker host does not count:
    the site is then just unreachable."""
    return next((r for r in proxy.refused if r['code'] in ('private_address', 'bad_host', 'no_address', 'bad_port')
                 and r['host']), None)


def read_page(url: str, proxy, *, project_id: str = 'mission_control', pane: DiscoveryPane | None = None,
              holder: list | None = None) -> dict:
    """`{ok: True, url, title, text, truncated, hidden_flagged, blocked}` (`blocked`: `{host, code}` the proxy refused) or
    `{ok: False, code, message}`. `proxy` is the running `GuardProxy`; `holder`, if
    given, receives the pane first so another thread can `abort()` it. `blocked` lists
    what the proxy refused while the page loaded (a refused ad host is not a failure)."""
    pane = pane or DiscoveryPane(project_id, proxy.port)
    if holder is not None:
        holder.append(pane)
    try:
        body = pane.read(url)
    except Exception as e:
        _log(f'[desk_connect] pane read raised {type(e).__name__}', flush=True)
        return _fail('page_unreachable')
    finally:
        pane.close()
    blocking = _blocking_refusal(proxy)
    if not isinstance(body, dict) or not body.get('ok'):
        kind = body.get('error') if isinstance(body, dict) else None
        code = _KIND_TO_CODE.get(str(kind), 'page_unreachable')
        if code == 'page_unreachable' and blocking:
            code = 'page_blocked'
        return _fail(code, f'Clayrune refused to open that address: {blocking["message"]}' if code == 'page_blocked' and blocking else None)
    final = str(body.get('final_url') or body.get('url') or '')
    if not final.startswith('https://'):
        # chrome-error://… is Chromium's own failure page: the proxy refused, or the site is down.
        if blocking:
            return _fail('page_blocked', f'Clayrune refused to open that address: {blocking["message"]}')
        return _fail('page_unreachable')
    content = body.get('content') or {}
    return {'ok': True, 'url': final, 'title': str(body.get('title') or ''), 'text': str(content.get('text') or ''),
            'truncated': bool(body.get('truncated')), 'hidden_flagged': bool(body.get('hidden_content_flagged')),
            'blocked': [{'host': r['host'], 'code': r['code']} for r in proxy.refused]}
