"""How the browser pane's frames and events reach the pane (backlog 629d2205).

One session, two transports, one event source:

  /api/browser/stream   SSE, JSON per event, the JPEG as base64 inside it. The original
                        transport and the fallback; its bytes are unchanged.
  /api/browser/frames   the same events as length-prefixed BINARY messages over a plain
                        chunked HTTP response -- the JPEG goes out as raw bytes (no base64,
                        -25% on the wire, no JSON string to build or parse per frame). The
                        client reads it with fetch() + a ReadableStream, so it needs no
                        WebSocket server (Flask/werkzeug has none, and a new dependency is
                        not worth it) and works anywhere SSE does: it is an ordinary HTTP
                        response, so Cloudflare's tunnel and the APK's Chromium WebView
                        carry it the same way. The pane falls back to SSE if it does not
                        start (see static/js/browser-pane.js `_bpOpenStream`).

Both are driven by `events()`, so a tab/dialog/download payload cannot reach one transport
and miss the other.

Wake-up: the generator used to poll the session every 33 ms. It now parks on a Condition
that `bump()` signals, so a frame is sent the moment the CDP reader has it (the poll added
up to 33 ms of latency and capped delivery at ~29 fps). `bump()` is the one place a trigger
counter (frame_seq, tabs_seq, ...) is advanced. A generator also wakes on a short timeout,
so a counter bumped by code that does not call bump() (a hand-built test session, a status
change) is still seen, just not instantly.

Frames are capped at `browser_stream_max_fps` (default 30, the old poll's ceiling): the
screencast produces up to 60/s, and nothing in the path tells us how fast the viewer's link
is, so lifting the cap would only fill a slow tunnel's buffers with stale frames. The
newest frame always wins; a viewer that cannot keep up skips frames (the socket write
blocks, so the next iteration reads the latest one), never queues them."""
import base64
import json
import struct
import threading
import time as _time
from typing import Any, Iterator

from mc import state

FRAMES_MIMETYPE = 'application/x-clayrune-frames'
FRAMES_MAGIC = b'CRF1'          # first 4 bytes of a /frames response: proves the body is ours
MSG_PING, MSG_JSON, MSG_FRAME = 0, 1, 2

_HEARTBEAT_S = 2.0              # keep the connection open through idle proxies
_WAKE_TIMEOUT_S = 0.25          # upper bound on seeing a change nobody signalled
_DEFAULT_MAX_FPS = 30
_TOKEN_SLACK = 0.75             # a frame this close to a whole token is sent (timer jitter)


def new_wake_state(session):
    """Give a session the Condition `bump()` signals. Called once, where the session is built."""
    session['wake'] = threading.Condition()
    session['wake_ver'] = 0


def bump(session, key):
    """Advance trigger counter `key` and wake every stream generator parked on the session."""
    cond = session.get('wake')
    if cond is None:            # hand-built session (tests): counter only
        session[key] = session.get(key, 0) + 1
        return
    with cond:
        session[key] = session.get(key, 0) + 1
        session['wake_ver'] = session.get('wake_ver', 0) + 1
        cond.notify_all()


def _wait(session, ver, timeout):
    """Park until `bump()` moves `wake_ver` off `ver` (read BEFORE the caller looked at the
    counters, so a bump that lands in between is not lost) or `timeout` passes."""
    cond = session.get('wake')
    if cond is None:
        _time.sleep(min(timeout, 0.033))
        return
    with cond:
        cond.wait_for(lambda: session.get('wake_ver', 0) != ver or session.get('status') != 'running',
                      timeout)


def max_fps():
    try:
        v = float(state.CONFIG.get('browser_stream_max_fps', _DEFAULT_MAX_FPS))
    except (TypeError, ValueError):
        v = _DEFAULT_MAX_FPS
    return min(120.0, max(1.0, v))


def events(session) -> Iterator[tuple[str, Any, Any]]:
    """Yield `(kind, body, b64_jpeg)` for one viewer until the session stops running:
    ('frame', meta dict, b64 JPEG) | ('msg', dict, None) | ('ping', None, None) | ('end', dict, None).

    `frame_seq`/`downloads_seq`/`tabs_seq`/`dialogs_seq`/`file_chooser_seq` are independent
    triggers on one channel deliberately: a download never bumps frame_seq (Chromium does not
    repaint for one -- see Browser.downloadWillBegin in _run_cdp, it is why the pane used to
    freeze), so downloads_seq is the ONLY way progress or completion reaches the pane; tabs and
    dialogs are the same pattern."""
    last = last_dl = last_tabs = last_dialog = last_file_chooser = -1
    fps = max_fps()
    # Token bucket, not "at least 1/fps since the last frame": the screencast arrives every
    # ~16.7 ms, so a min-gap of 33.3 ms landed a frame a hair early about half the time and
    # skipped it, delivering ~25 fps under a 30 cap (measured). A bucket sends 30 of 60.
    tokens, refilled_at = 1.0, _time.monotonic()
    last_out = _time.monotonic()
    while session['status'] == 'running':
        ver = session.get('wake_ver', 0)
        seq = session.get('frame_seq', 0)
        dl_seq = session.get('downloads_seq', 0)
        tabs_seq = session.get('tabs_seq', 0)
        dialog_seq = session.get('dialogs_seq', 0)
        file_chooser_seq = session.get('file_chooser_seq', 0)
        sent = False
        gate = 0.0
        if seq != last and session.get('frame'):
            now = _time.monotonic()
            tokens = min(2.0, tokens + (now - refilled_at) * fps)
            refilled_at = now
            if tokens >= _TOKEN_SLACK:
                tokens -= 1.0
                last = seq
                yield ('frame', {'seq': seq,
                                 'url': session.get('live_url') or session.get('url'),
                                 'w': session.get('frame_w'),
                                 'h': session.get('frame_h'),
                                 's': session.get('page_scale')}, session['frame'])
                sent = True
            else:
                gate = (_TOKEN_SLACK - tokens) / fps
        if dl_seq != last_dl:
            last_dl = dl_seq
            yield ('msg', {'downloads': list(session.get('downloads', {}).values())}, None)
            sent = True
        # Guarded on key presence (not just the `or {}`/`or 0` defaults) so a hand-built
        # session without tab/dialog state never sees these payloads at all -- only sessions
        # `_run_cdp` actually initialized (which always sets both) do.
        if 'tabs' in session and tabs_seq != last_tabs:
            last_tabs = tabs_seq
            tabs = [{'target_id': tid, 'url': t.get('url', ''), 'title': t.get('title', ''),
                     'opener_id': t.get('opener_id')}
                    for tid, t in (session.get('tabs') or {}).items()]
            yield ('msg', {'tabs': tabs, 'active_target_id': session.get('active_target_id')}, None)
            sent = True
        if 'dialog' in session and dialog_seq != last_dialog:
            last_dialog = dialog_seq
            yield ('msg', {'dialog': session.get('dialog')}, None)
            sent = True
        if 'file_chooser' in session and file_chooser_seq != last_file_chooser:
            last_file_chooser = file_chooser_seq
            fc = session.get('file_chooser')
            yield ('msg', {'file_chooser': {'mode': fc.get('mode')} if fc else None}, None)
            sent = True
        now = _time.monotonic()
        if sent:
            last_out = now
            continue
        if now - last_out >= _HEARTBEAT_S:
            last_out = now
            yield ('ping', None, None)
        # A frame held back by the fps cap needs a wake when its gap ends, not 250 ms later.
        _wait(session, ver, min(_WAKE_TIMEOUT_S, gate) if gate > 0 else _WAKE_TIMEOUT_S)
    yield ('end', {'status': session['status'], 'error': session.get('error')}, None)


def sse_gen(session):
    """The SSE body for one /api/browser/stream connection. Module-level (not a closure in the
    route) so tests can drive it directly: a streaming response only ends when the session
    stops running, which Flask's test client would wait on forever."""
    for ev in events(session):
        kind = ev[0]
        if kind == 'frame':
            # key order is part of the wire format the tests and older panes see
            payload = json.dumps({'seq': ev[1]['seq'], 'img': ev[2], 'url': ev[1]['url'],
                                  'w': ev[1]['w'], 'h': ev[1]['h'], 's': ev[1]['s']})
            yield f'data: {payload}\n\n'
        elif kind == 'msg' or kind == 'end':
            yield f'data: {json.dumps(ev[1])}\n\n'
        else:
            yield ': ping\n\n'


def _msg(kind, body):
    return struct.pack('>BI', kind, len(body)) + body


def bin_gen(session):
    """Binary body for /api/browser/frames: FRAMES_MAGIC, then messages of
    `type:u8 | length:u32be | body`. JSON messages carry what an SSE `data:` would; a frame's
    body is `metaLen:u32be | meta JSON | raw JPEG`."""
    yield FRAMES_MAGIC
    for kind, body, img in events(session):
        if kind == 'frame':
            meta = json.dumps(body).encode()
            try:
                jpeg = base64.b64decode(img)
            except Exception as e:
                print(f'[browser] frame b64 decode failed: {e}', flush=True)
                continue
            yield _msg(MSG_FRAME, struct.pack('>I', len(meta)) + meta + jpeg)
        elif kind == 'msg' or kind == 'end':
            yield _msg(MSG_JSON, json.dumps(body).encode())
        else:
            yield _msg(MSG_PING, b'')
