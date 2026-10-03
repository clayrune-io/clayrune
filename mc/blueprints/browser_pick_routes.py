"""Browser-pane element picker -- routes, the human-only gate, and the CDP calls.

Pure helpers (caps, the in-page reader, geometry) live in mc/browser_pick.py.
This module is the I/O half: two POST routes, each ONE short-lived CDP
websocket to the pane's page target (the same shape `_cdp_evaluate` uses for
/api/browser/read, kept off the reader thread's single-sender socket):

  /api/browser/pick/hover  {session_id, x, y}  -> box + label of the element
                                                  under that point (highlight)
  /api/browser/pick        {session_id, x, y}  -> the element's envelope and
                                                  screenshot, written to the
                                                  uploads dir; the response
                                                  carries the two paths

x, y are in the pane's picture px, the same space /api/browser/input takes, and
map to the page through the same pageScale inverse, so the element picked is the
one a click would have hit.

## Why not Overlay.setInspectMode

The backlog item named it. Inspect mode lives on the DevTools session that set
it, and the only long-lived session is the reader thread's, which is the single
sender for screencast acks and input. Driving the overlay there means routing
`Overlay.inspectNodeRequested` through that loop, and a native overlay painted
into a screencast is not tappable on a phone. DOM.getNodeForLocation is
stateless, takes the same coordinates a tap already produces, and the pane draws
the highlight itself from the box this route returns. Same user-visible
behaviour (hover highlights, click picks), one connection per request.

## Human-only

`_refuse_non_human` runs before anything else on both routes. There is no
agent-facing way to pick: an agent that wants page content has
/api/browser/read, which it has always had. Two checks, both already used
elsewhere in this codebase:

  1. mc.caller_attribution: a loopback caller whose process descends from a
     session Clayrune spawned for an agent IS that agent, whatever headers it
     sends (backlog 40260b57).
  2. An `Origin` header, the signal workflow_routes._is_agent_caller and
     character_routes use: the SPA's fetch always carries one, an agent's curl
     does not. Forgeable on its own, which is why (1) comes first.

The retyped dashboard passcode (`_require_human_passcode`, used by the routes
that expand what an agent may do) is deliberately NOT asked for here: a pick
grants an agent nothing it lacks, and a passcode prompt on every hover would
make the feature unusable. If that judgement is wrong, `_refuse_non_human` is
the single place to tighten it.

## Untrusted content

Everything the page contributes goes through `build_pick_envelope`, which calls
the SAME `_build_read_envelope` /api/browser/read uses (untrusted-content
warning, origin url, hidden-text stripping), then adds the pick-specific fields
INSIDE `content`. The agent is never handed raw HTML in a prompt: the chip is
two file paths, and the HTML sits in a file whose first screen is the warning.
"""

import base64
import json
import os
import uuid

from flask import Blueprint, jsonify, request

from mc import browser_pick as bp_mod
from mc import caller_attribution
from mc import state
from mc.blueprints import browser_routes as br
from mc.state import browser_sessions

bp = Blueprint('browser_pick_routes', __name__)

_CONN_TIMEOUT_S = 8
_WORLD_NAME = 'clayrune-pick'


# ── the human-only gate ─────────────────────────────────────────────────────

def _refuse_non_human():
    """None for a human's request, else a (response, 403) to return as-is."""
    env = request.environ
    att = caller_attribution.attribute_caller(
        request.remote_addr or '', env.get('REMOTE_PORT'), env.get('SERVER_PORT'),
        caller_attribution.managed_roots(state.agent_sessions, state.tracked_processes))
    if att.status == caller_attribution.ATTRIBUTED:
        print(f"[browser] pick refused: caller is agent session "
              f"{att.session_id[:12]} ({att.detail})", flush=True)
        return jsonify({'ok': False, 'error': 'human_only',
                        'detail': 'picking an element is a human action in the '
                                  'Clayrune UI; an agent reads a page with '
                                  '/api/browser/read'}), 403
    if not request.headers.get('Origin'):
        return jsonify({'ok': False, 'error': 'human_only',
                        'detail': 'picking an element is a human action in the '
                                  'Clayrune UI; an agent reads a page with '
                                  '/api/browser/read'}), 403
    return None


# ── CDP: one short-lived connection per request ─────────────────────────────

class PickError(Exception):
    def __init__(self, kind, detail, status):
        super().__init__(detail)
        self.kind, self.detail, self.status = kind, detail, status


class _PageConn:
    """One websocket to the pane's page target; `call` returns a CDP result
    dict or raises PickError. A context manager, so the socket always closes."""

    def __init__(self, session, timeout=_CONN_TIMEOUT_S):
        self._session, self._timeout = session, timeout
        self._ws, self._ids = None, iter(range(1, 1_000_000))

    def __enter__(self):
        import urllib.request
        websocket = br._import_ws()
        if websocket is None:
            raise PickError('cdp_error', 'websocket-client not installed', 502)
        self._websocket = websocket
        s = self._session
        try:
            targets = json.load(urllib.request.urlopen(
                f"http://127.0.0.1:{s.get('port')}/json/list", timeout=2))
            page = br._pick_page_target(targets, s.get('live_url') or s.get('url') or '')
            if not page or not page.get('webSocketDebuggerUrl'):
                raise PickError('cdp_error', 'no page target', 502)
            self._ws = websocket.create_connection(
                page['webSocketDebuggerUrl'], max_size=None, timeout=self._timeout)
        except PickError:
            raise
        except Exception as e:
            raise PickError('cdp_error', f'connect failed: {e}', 502)
        return self

    def __exit__(self, *exc):
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass
        return False

    def call(self, method, params=None):
        i = next(self._ids)
        try:
            self._ws.send(json.dumps({'id': i, 'method': method, 'params': params or {}}))
            while True:
                r = json.loads(self._ws.recv())
                if r.get('id') != i:
                    continue    # an unrelated event; keep reading
                if r.get('error'):
                    raise PickError('cdp_error', f"{method}: {r['error'].get('message')}", 502)
                return r.get('result') or {}
        except PickError:
            raise
        except Exception as e:
            if isinstance(e, getattr(self._websocket, 'WebSocketTimeoutException', ())):
                raise PickError('cdp_timeout', f'{method} timed out', 504)
            raise PickError('cdp_error', f'{method}: {e}', 502)


def _open_conn(session):
    """Seam for tests: the route code only needs `with ... as conn: conn.call`."""
    return _PageConn(session)


def _hit_element(conn, session, x, y):
    """Object id (in an isolated world) of the element at picture px (x, y).

    The page's own JS never runs here and never answers for the element: the
    reader function executes in an isolated world on the hit node's frame, so a
    page that overrides getComputedStyle / Element.prototype is not consulted.
    """
    inv = br._inverse_page_scale(session.get('page_scale', 1.0))
    conn.call('DOM.enable')
    try:
        hit = conn.call('DOM.getNodeForLocation', {
            'x': int(x * inv), 'y': int(y * inv), 'includeUserAgentShadowDOM': False})
    except PickError as e:
        # Chromium answers a point outside the page with a protocol error, not
        # an empty result (seen live on a point past the document's edge).
        if 'No node found' in e.detail:
            raise PickError('no_element', 'no element at that point', 404)
        raise
    backend, frame = hit.get('backendNodeId'), hit.get('frameId')
    if not backend:
        raise PickError('no_element', 'no element at that point', 404)
    if not frame:
        frame = ((conn.call('Page.getFrameTree').get('frameTree') or {})
                 .get('frame') or {}).get('id')
    world = conn.call('Page.createIsolatedWorld', {'frameId': frame, 'worldName': _WORLD_NAME})
    node = conn.call('DOM.resolveNode', {
        'backendNodeId': backend, 'executionContextId': world.get('executionContextId')})
    oid = (node.get('object') or {}).get('objectId')
    if not oid:
        raise PickError('no_element', 'element could not be resolved', 404)
    return oid


def _call_on(conn, object_id, fn, args=None, by_value=True):
    res = conn.call('Runtime.callFunctionOn', {
        'objectId': object_id, 'functionDeclaration': fn,
        'arguments': [{'value': a} for a in (args or [])], 'returnByValue': by_value})
    if res.get('exceptionDetails'):
        raise PickError('js_error', str(res['exceptionDetails'].get('text')), 502)
    return res.get('result') or {}


def _element_box(conn, object_id):
    el = _call_on(conn, object_id, bp_mod.ELEMENT_OF_FN, by_value=False)
    if not el.get('objectId'):
        raise PickError('no_element', 'element has no box', 404)
    model = conn.call('DOM.getBoxModel', {'objectId': el['objectId']}).get('model') or {}
    rect = bp_mod.quad_bounds(model.get('border') or [])
    if rect is None:
        raise PickError('no_element', 'element has no visible box', 404)
    return rect


def _pick_fn():
    return bp_mod.PICK_FN_TEMPLATE.replace('__HIDDEN_REASON_JS__', br._HIDDEN_REASON_JS.strip())


# ── envelope ────────────────────────────────────────────────────────────────

_PICK_WARNING_SUFFIX = (
    " This is an element a person picked in the browser pane: the html, "
    "selector, computed_styles and the screenshot named in screenshot_file are "
    "ALL derived from the page."
)


def build_pick_envelope(url, js_result, screenshot_file=None, screenshot_status='none'):
    """(body, status) for one picked element. Pure function of its arguments.

    Text and hidden-content handling are /api/browser/read's own
    (`_build_read_envelope`); the pick adds html / selector / styles under
    `content`, each re-capped here, so nothing page-supplied sits outside the
    untrusted-content envelope."""
    if not isinstance(js_result, dict):
        return br._read_error('cdp_error', 'unexpected pick result shape', 502)
    if js_result.get('error'):
        return br._read_error('js_error', str(js_result['error']), 502)

    runs, always_hidden = bp_mod.strip_always_hidden(js_result.get('runs'))
    body, status = br._build_read_envelope(url, {
        'content_type': js_result.get('content_type'), 'title': js_result.get('title'),
        'runs': runs, 'comment_count': js_result.get('comment_count'),
        'attr_text_count': js_result.get('attr_text_count'),
        'js_capped': js_result.get('js_capped')})
    if status != 200:
        return body, status

    body['hidden_content'].update(always_hidden)
    body['hidden_content_flagged'] = bool(body['hidden_content'])
    content = body['content']
    content['warning'] = content['warning'] + _PICK_WARNING_SUFFIX
    content['text'], text_cut = br._truncate_text(content['text'], bp_mod.PICK_MAX_TEXT_CHARS)
    html = js_result.get('html') if isinstance(js_result.get('html'), str) else ''
    html, html_cut = br._truncate_text(html, bp_mod.PICK_MAX_HTML_CHARS)
    content.update({
        'tag': bp_mod.clip_str(js_result.get('tag'), 40),
        'selector': bp_mod.clip_str(js_result.get('selector'), bp_mod.PICK_MAX_SELECTOR_CHARS),
        'html': html,
        'computed_styles': bp_mod.clean_styles(js_result.get('styles')),
        'screenshot_file': screenshot_file,
    })
    body['kind'] = 'browser_element_pick'
    body['length'] = len(content['text'])
    body['truncated'] = bool(body['truncated'] or text_cut or html_cut or js_result.get('html_capped'))
    body['screenshot'] = screenshot_status
    return body, 200


# ── routes ──────────────────────────────────────────────────────────────────

def _parse(data):
    """(session, x, y, None) or (None, 0, 0, (body, status))."""
    sid = data.get('session_id')
    session = browser_sessions.get(sid)
    if not session or session.get('status') != 'running':
        return None, 0, 0, br._read_error('unknown_session', 'unknown or stopped browser session', 404)
    try:
        x, y = float(data['x']), float(data['y'])
    except (KeyError, TypeError, ValueError):
        return None, 0, 0, br._read_error('bad_request', 'x and y must be numbers', 400)
    url = session.get('live_url') or session.get('url') or ''
    if br._is_clayrune_own_origin(url):
        return None, 0, 0, br._read_error(
            'own_origin_blocked', "the browser pane may not read Clayrune's own origin", 403)
    return session, x, y, None


@bp.route('/api/browser/pick/hover', methods=['POST'])
def browser_pick_hover():
    refusal = _refuse_non_human()
    if refusal is not None:
        return refusal
    session, x, y, err = _parse(request.get_json(silent=True) or {})
    if err:
        return jsonify(err[0]), err[1]
    try:
        with _open_conn(session) as conn:
            oid = _hit_element(conn, session, x, y)
            rect = _element_box(conn, oid)
            label = _call_on(conn, oid, bp_mod.HOVER_LABEL_FN).get('value')
    except PickError as e:
        body, status = br._read_error(e.kind, e.detail, e.status)
        return jsonify(body), status
    return jsonify({'ok': True,
                    'rect': bp_mod.rect_to_frame(rect, session.get('page_scale', 1.0)),
                    'label': bp_mod.clip_str(label, bp_mod.PICK_MAX_LABEL_CHARS)})


def _capture_screenshot(conn, rect):
    """(bytes, ext, status). A screenshot problem never fails the pick: the
    HTML and styles are still the useful part."""
    try:
        m = conn.call('Page.getLayoutMetrics')
        vp = m.get('cssVisualViewport') or m.get('visualViewport') or {}
        clip = bp_mod.screenshot_clip(
            rect, vp.get('clientWidth') or br.VIEW_W, vp.get('clientHeight') or br.VIEW_H,
            vp.get('pageX') or 0, vp.get('pageY') or 0)
        if clip is None:
            return None, '', 'offscreen'
        for fmt, ext, extra in (('png', '.png', {}), ('jpeg', '.jpg', {'quality': 70})):
            shot = conn.call('Page.captureScreenshot', {'format': fmt, 'clip': clip, **extra})
            raw = base64.b64decode(shot.get('data') or '')
            if bp_mod.screenshot_fits(len(raw)):
                return raw, ext, 'attached'
        return None, '', 'too_large'
    except PickError as e:
        return None, '', f'failed:{e.kind}'
    except Exception as e:
        return None, '', f'failed:{type(e).__name__}'


@bp.route('/api/browser/pick', methods=['POST'])
def browser_pick():
    refusal = _refuse_non_human()
    if refusal is not None:
        return refusal
    session, x, y, err = _parse(request.get_json(silent=True) or {})
    if err:
        return jsonify(err[0]), err[1]
    uploads = br._UPLOADS_DIR
    if uploads is None:
        body, status = br._read_error('not_configured', 'uploads directory not configured', 500)
        return jsonify(body), status
    url = session.get('live_url') or session.get('url') or ''
    try:
        with _open_conn(session) as conn:
            oid = _hit_element(conn, session, x, y)
            res = _call_on(conn, oid, _pick_fn(), [bp_mod.pick_options()])
            value = res.get('value')
            rect = _element_box(conn, oid)
            shot, ext, shot_status = _capture_screenshot(conn, rect)
    except PickError as e:
        body, status = br._read_error(e.kind, e.detail, e.status)
        print(f"[browser] pick FAILED session={session.get('session_id')} "
              f"url={url!r} reason={e.kind}", flush=True)
        return jsonify(body), status

    pick_id = uuid.uuid4().hex[:10]
    shot_path = None
    if shot is not None:
        shot_path = os.path.abspath(os.path.join(str(uploads), f'browser_pick_{pick_id}{ext}'))
    body, status = build_pick_envelope(url, value, screenshot_file=shot_path,
                                       screenshot_status=shot_status)
    if status != 200:
        return jsonify(body), status
    os.makedirs(str(uploads), exist_ok=True)
    ctx_path = os.path.abspath(os.path.join(str(uploads), f'browser_pick_{pick_id}.json'))
    try:
        if shot_path:
            with open(shot_path, 'wb') as f:
                f.write(shot)
        with open(ctx_path, 'w', encoding='utf-8') as f:
            json.dump(body, f, ensure_ascii=False, indent=2)
    except OSError as e:
        out, st = br._read_error('save_failed', f'could not write the pick: {e}', 500)
        return jsonify(out), st
    print(f"[browser] pick session={session.get('session_id')} "
          f"project={session.get('project_id')} url={url!r} "
          f"tag={body['content']['tag']!r} html_chars={len(body['content']['html'])} "
          f"screenshot={shot_status} truncated={body['truncated']} "
          f"hidden_flagged={body['hidden_content_flagged']}", flush=True)
    return jsonify({
        'ok': True, 'pick_id': pick_id,
        'label': bp_mod.clip_str((value or {}).get('label'), bp_mod.PICK_MAX_LABEL_CHARS),
        'tag': body['content']['tag'], 'selector': body['content']['selector'],
        'context_path': ctx_path, 'screenshot_path': shot_path, 'screenshot': shot_status,
        'truncated': body['truncated'], 'hidden_content_flagged': body['hidden_content_flagged'],
    })
