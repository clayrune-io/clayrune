#!/usr/bin/env python
"""Regression guard: the page's viewport must equal the frame we capture.

WHY THIS EXISTS
---------------
The pane used to send `Emulation.setDeviceMetricsOverride(1280x800)`. The page
believed it and laid out in an 800px-tall viewport — but `Page.startScreencast`
captures the REAL window surface, which was 1264x649. So 151px of every page was
laid out and never shown, and no amount of scrolling could reach it: the page
had scrollHeight == clientHeight == 800 and was certain everything already fit.

Ron hit it on a Discord captcha whose Submit button sat in that dead band.
Visible to the page. Invisible to him. Unreachable.

Nothing in the app can notice this. Every frame arrives, the pane renders it,
clicks map correctly into the frame — the missing strip simply never existed as
far as any code is concerned. So it needs a measurement, not an assertion about
behaviour.

Four checks:
  1. setDeviceMetricsOverride is issued from exactly ONE place --
     _device_mode_commands, MC-980's mobile-pane device-mode switch -- and
     there its width/height are the CURRENT view (the `vw, vh` derived from
     `view or session['view']`, i.e. what the window was just fit to), never
     a literal. Anything telling the page a size other than the one it has
     re-creates this class of bug; MC-980 needed a live override to make the
     REMOTE PAGE present as a phone, so the old "none anywhere" ban narrowed
     to "only from the one call site whose whole job is keeping it in sync."
  2. Empirically, with the SHIPPED window sizing, innerWidth/innerHeight equal
     the screencast's deviceWidth/deviceHeight (desktop / no override).
  3. HiDPI: the scale flag alone must not reopen check 2.
  4. Empirically, in MOBILE device-mode: the override makes the page agree
     with the frame at the phone size; a resize inside mobile mode re-issues
     it (an earlier cut of _device_mode_commands was idempotent on MODE only,
     so a same-mode resize left a STALE override declaring the old size while
     the window moved on -- exactly this bug, caught by Dave's review before
     it shipped); and returning to desktop leaves NO live override, proven by
     a subsequent desktop resize landing at the new size, not the old
     mobile one.

RUN:  python tools/smoke/browser_pane_viewport.py
Exit 0 = the page and the picture agree, in every mode.
"""
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from mc.blueprints import browser_routes as br  # noqa: E402

FAILS = []


def check(name, ok, detail=''):
    print(('OK   ' if ok else 'FAIL ') + name + (f' — {detail}' if detail and not ok else ''))
    if not ok:
        FAILS.append(name)


# ── 1. the source must not tell the page a size it does not have ────────────
import ast

src = (REPO / 'mc' / 'blueprints' / 'browser_routes.py').read_text(encoding='utf-8')
tree = ast.parse(src)
top_funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
device_fn = top_funcs.get('_device_mode_commands')
check('_device_mode_commands exists', device_fn is not None)

if device_fn is not None:
    device_src = ast.get_source_segment(src, device_fn) or ''
    # The CDP method as an actual string literal (a real call site), not the
    # bare word showing up in a comment/docstring cross-reference to this
    # function -- _fit_windows and _run_cdp both mention it in prose.
    method_literal = re.compile(r"""['"]Emulation\.setDeviceMetricsOverride['"]""")
    outside_calls = []
    for name, fn in top_funcs.items():
        if name == '_device_mode_commands':
            continue
        body = ast.get_source_segment(src, fn) or ''
        for ln in body.split('\n'):
            if method_literal.search(ln):
                outside_calls.append(f'{name}: {ln.strip()}')
    check('setDeviceMetricsOverride is only ever issued from _device_mode_commands',
          not outside_calls,
          'a second call site can drift out of sync with the window it describes: '
          + '; '.join(outside_calls))

    # The override's width/height must be the `vw, vh` this call just derived
    # from the CURRENT view, not a literal -- that derivation IS the fix.
    derives_from_view = bool(re.search(
        r"vw,\s*vh\s*=\s*view\s+or\s+session\.get\(\s*'view'\s*\)", device_src))
    uses_vw_vh = bool(re.search(
        r"'width':\s*vw\s*,\s*'height':\s*vh\b", device_src))
    check('the override width/height come from the current view, not a literal',
          derives_from_view and uses_vw_vh,
          f'derives_from_view={derives_from_view} uses_vw_vh={uses_vw_vh} -- '
          'setDeviceMetricsOverride must declare exactly what the window was '
          'just fit to, or a mismatch reopens MC-976')


# ── 2. the page and the picture must agree ──────────────────────────────────
def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def chromium_path():
    for fn in ('_find_chromium', '_chromium_path', '_resolve_chromium'):
        f = getattr(br, fn, None)
        if callable(f):
            try:
                v = f()
                if v:
                    return v
            except Exception:
                pass
    for c in ('chrome', 'chromium', 'msedge'):
        p = shutil.which(c)
        if p:
            return p
    return None


CHROMIUM = chromium_path()
if not CHROMIUM:
    print('SKIP — no Chromium on this machine; the source check above still ran.')
    sys.exit(1 if FAILS else 0)

try:
    import websocket  # websocket-client
except ImportError:
    print('SKIP — websocket-client not installed; the source check above still ran.')
    sys.exit(1 if FAILS else 0)

port = free_port()
udd = tempfile.mkdtemp(prefix='pane-viewport-guard-')
# The SHIPPED sizing, read from the module — so changing it here is not enough
# to make this pass.
win_w = br.VIEW_W + br.WINDOW_CHROME_W
win_h = br.VIEW_H + br.WINDOW_CHROME_H
proc = subprocess.Popen(
    [CHROMIUM, '--headless=new', f'--remote-debugging-port={port}',
     '--remote-allow-origins=*', f'--user-data-dir={udd}', '--no-first-run',
     '--no-default-browser-check', '--disable-gpu',
     f'--window-size={win_w},{win_h}', 'about:blank'],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

try:
    tabs = None
    for _ in range(40):
        try:
            tabs = json.loads(urllib.request.urlopen(
                f'http://127.0.0.1:{port}/json/list', timeout=2).read())
            if tabs:
                break
        except Exception:
            time.sleep(0.25)
    page = next((t for t in (tabs or []) if t.get('type') == 'page'), None)
    if not page:
        check('chromium exposed a page target', False, 'no page in /json/list')
        raise SystemExit(1)

    ws = websocket.create_connection(page['webSocketDebuggerUrl'],
                                     suppress_origin=True, timeout=10)
    seq = [0]

    def send(method, params=None):
        seq[0] += 1
        ws.send(json.dumps({'id': seq[0], 'method': method, 'params': params or {}}))
        return seq[0]

    send('Page.enable')
    send('Runtime.enable')
    # data: URL — no network, and tall enough to be scrollable if it needs to be.
    send('Page.navigate', {'url': 'data:text/html,<body style="margin:0">'
                                  '<div style="height:3000px">tall</div></body>'})
    time.sleep(2.5)
    send('Page.startScreencast', {'format': 'jpeg', 'quality': 55,
                                  'maxWidth': win_w, 'maxHeight': win_h,
                                  'everyNthFrame': 1})
    cast = None
    deadline = time.time() + 10
    while time.time() < deadline and cast is None:
        try:
            m = json.loads(ws.recv())
        except Exception:
            break
        if m.get('method') == 'Page.screencastFrame':
            md = m['params'].get('metadata') or {}
            cast = (md.get('deviceWidth'), md.get('deviceHeight'))

    eid = send('Runtime.evaluate', {
        'expression': '({w: innerWidth, h: innerHeight})', 'returnByValue': True})
    believes = None
    deadline = time.time() + 8
    while time.time() < deadline and believes is None:
        try:
            m = json.loads(ws.recv())
        except Exception:
            break
        if m.get('id') == eid:
            believes = m.get('result', {}).get('result', {}).get('value')
    ws.close()

    pw, ph = (believes or {}).get('w'), (believes or {}).get('h')
    cw, ch = cast or (None, None)
    check('the screencast reported a frame size', bool(cw and ch), str(cast))
    check('the page reported its viewport', bool(pw and ph), str(believes))
    check('page viewport == captured frame', (pw, ph) == (cw, ch),
          f'page lays out {pw}x{ph} but we capture {cw}x{ch} — '
          f'{(ph or 0) - (ch or 0)}px of every page would be invisible and '
          f'unreachable')
    # Not a correctness property, just a nudge: if the chrome allowance drifts
    # far off, the viewport quietly gets short. Wide tolerance on purpose.
    check('the viewport is near the size we aim for',
          ph is not None and abs(ph - br.VIEW_H) <= 40,
          f'aimed for {br.VIEW_H}, got {ph} — retune WINDOW_CHROME_H')
finally:
    proc.kill()
    shutil.rmtree(udd, ignore_errors=True)


# ── 3. HiDPI (MC-976 gap #8): the scale flag must NOT reopen check 2 ────────
#
# --force-device-scale-factor is a Chromium LAUNCH flag, not the CDP
# Emulation.setDeviceMetricsOverride check 1 above forbids — it changes only
# the backing pixel density, so the page's believed viewport (innerWidth/
# innerHeight, in CSS px) must still equal what the screencast reports
# (deviceWidth/deviceHeight, also CSS px) exactly as it does at dpr=1. What
# changes is the DECODED bitmap: same CSS-pixel frame, ~dpr-times the actual
# pixels, so text reads sharp. This also gives the frame-bytes-before/after
# measurement MC-976 B4 asked for.
try:
    from PIL import Image
    import io, base64
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False
    print('SKIP HiDPI bitmap-size check — Pillow not installed '
          '(frame-size numbers below still print from raw byte counts).')


def capture_one_frame(dpr):
    """Launch a fresh Chromium at `dpr` (production _clamp_dpr/
    _screencast_params_for, not a hand-rolled copy) and return
    (believes, cast, jpeg_bytes) for the same fixed page."""
    port = free_port()
    udd = tempfile.mkdtemp(prefix=f'pane-hidpi-guard-{dpr}-')
    scale = br._clamp_dpr(dpr)
    args = [CHROMIUM, '--headless=new', f'--remote-debugging-port={port}',
            '--remote-allow-origins=*', f'--user-data-dir={udd}', '--no-first-run',
            '--no-default-browser-check', '--disable-gpu',
            f'--window-size={win_w},{win_h}']
    if scale != 1:
        args.append(f'--force-device-scale-factor={scale}')
    args.append('about:blank')
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        tabs = None
        for _ in range(40):
            try:
                tabs = json.loads(urllib.request.urlopen(
                    f'http://127.0.0.1:{port}/json/list', timeout=2).read())
                if tabs:
                    break
            except Exception:
                time.sleep(0.25)
        page = next((t for t in (tabs or []) if t.get('type') == 'page'), None)
        if not page:
            return None, None, None
        ws = websocket.create_connection(page['webSocketDebuggerUrl'],
                                         suppress_origin=True, timeout=10)
        seq = [0]

        def send(method, params=None):
            seq[0] += 1
            ws.send(json.dumps({'id': seq[0], 'method': method, 'params': params or {}}))
            return seq[0]

        send('Page.enable')
        send('Runtime.enable')
        send('Page.navigate', {'url': 'data:text/html,<body style="margin:0;'
                                      'font:16px sans-serif"><h1>Hello HiDPI '
                                      '0123456789</h1></body>'})
        time.sleep(1.5)
        # The real production scaling function — _SCREENCAST_PARAMS' own
        # maxWidth/maxHeight already equal win_w/win_h (VIEW_W/H + chrome),
        # so this is exactly what a live launch at this dpr would send.
        send('Page.startScreencast', br._screencast_params_for(scale))
        jpeg = None
        cast = None
        deadline = time.time() + 10
        while time.time() < deadline and jpeg is None:
            try:
                m = json.loads(ws.recv())
            except Exception:
                break
            if m.get('method') == 'Page.screencastFrame':
                p = m['params']
                md = p.get('metadata') or {}
                cast = (md.get('deviceWidth'), md.get('deviceHeight'))
                jpeg = base64.b64decode(p['data'])
                send('Page.screencastFrameAck', {'sessionId': p['sessionId']})

        eid = send('Runtime.evaluate', {
            'expression': '({w: innerWidth, h: innerHeight})', 'returnByValue': True})
        believes = None
        deadline = time.time() + 8
        while time.time() < deadline and believes is None:
            try:
                m = json.loads(ws.recv())
            except Exception:
                break
            if m.get('id') == eid:
                believes = m.get('result', {}).get('result', {}).get('value')
        ws.close()
        return believes, cast, jpeg
    finally:
        proc.kill()
        shutil.rmtree(udd, ignore_errors=True)


believes1, cast1, jpeg1 = capture_one_frame(1)
believes2, cast2, jpeg2 = capture_one_frame(2)

check('dpr=2 frame captured', jpeg2 is not None, 'no screencast frame arrived at dpr=2')
if jpeg1 is not None and jpeg2 is not None:
    believes1_wh = (believes1 or {}).get('w'), (believes1 or {}).get('h')
    believes2_wh = (believes2 or {}).get('w'), (believes2 or {}).get('h')
    check('dpr=1 and dpr=2 report the SAME CSS viewport',
          believes1_wh == cast1 and believes2_wh == cast2 and believes1_wh == believes2_wh,
          f'dpr=1 believes={believes1} cast={cast1}; '
          f'dpr=2 believes={believes2} cast={cast2} — a HiDPI launch must not '
          're-create the CDP-override viewport mismatch check 2 guards against')
    print(f'    frame bytes: dpr=1 -> {len(jpeg1)}, dpr=2 -> {len(jpeg2)}  '
          f'ratio={len(jpeg2)/len(jpeg1):.2f}x')
    if HAVE_PIL:
        size1 = Image.open(io.BytesIO(jpeg1)).size
        size2 = Image.open(io.BytesIO(jpeg2)).size
        print(f'    decoded bitmap: dpr=1 -> {size1}, dpr=2 -> {size2}')
        check('dpr=2 bitmap is ~2x the dpr=1 bitmap (text actually sharper)',
              size2[0] >= size1[0] * 1.8 and size2[1] >= size1[1] * 1.8,
              f'{size1} -> {size2} did not scale with the dpr flag')

# ── 4. mobile device-mode: override syncs on resize, clears on return ───────
#
# MC-980's own regression, caught in Dave's review before it shipped: the
# first cut of _device_mode_commands was idempotent on MODE alone, so a
# same-mode resize (phone rotation, the sheet resizing under a soft
# keyboard) matched the mode already recorded and returned [] -- the
# override kept declaring the OLD size while the window moved on. Same shape
# as the original MC-976 bug, just re-entered from the mobile side. This
# drives the real function's commands against a real Chromium and checks
# what the page believes AND what gets captured at each step, not just that
# some command was returned.
def mobile_device_mode_check():
    port = free_port()
    udd = tempfile.mkdtemp(prefix='pane-mobile-guard-')
    proc = subprocess.Popen(
        [CHROMIUM, '--headless=new', f'--remote-debugging-port={port}',
         '--remote-allow-origins=*', f'--user-data-dir={udd}', '--no-first-run',
         '--no-default-browser-check', '--disable-gpu',
         f'--window-size={win_w},{win_h}', 'about:blank'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        tabs = None
        for _ in range(40):
            try:
                tabs = json.loads(urllib.request.urlopen(
                    f'http://127.0.0.1:{port}/json/list', timeout=2).read())
                if tabs:
                    break
            except Exception:
                time.sleep(0.25)
        page = next((t for t in (tabs or []) if t.get('type') == 'page'), None)
        if not page:
            check('mobile device-mode: chromium exposed a page target', False,
                  'no page in /json/list')
            return

        ws = websocket.create_connection(page['webSocketDebuggerUrl'],
                                         suppress_origin=True, timeout=10)
        seq = [0]

        def send(method, params=None):
            seq[0] += 1
            ws.send(json.dumps({'id': seq[0], 'method': method, 'params': params or {}}))
            return seq[0]

        def wait_for(mid):
            deadline = time.time() + 8
            while time.time() < deadline:
                try:
                    m = json.loads(ws.recv())
                except Exception:
                    break
                if m.get('id') == mid:
                    return m
            return None

        def believes():
            eid = send('Runtime.evaluate', {
                'expression': '({w: innerWidth, h: innerHeight})', 'returnByValue': True})
            m = wait_for(eid)
            v = ((m or {}).get('result') or {}).get('result', {}).get('value') or {}
            return v.get('w'), v.get('h')

        def captured_size():
            # A synchronous screenshot rather than a screencast frame -- it
            # reflects state right now, with no ack/streaming timing to race.
            if not HAVE_PIL:
                return None
            cid = send('Page.captureScreenshot', {'format': 'png'})
            m = wait_for(cid)
            data = ((m or {}).get('result') or {}).get('data')
            if not data:
                return None
            return Image.open(io.BytesIO(base64.b64decode(data))).size

        send('Page.enable')
        send('Runtime.enable')
        # A real `<meta name=viewport>` tag, same as virtually every mobile-
        # optimized site (and what MC-980's UA/touch/mobile overrides mean to
        # be detected by) -- without it, mobile:true falls back to Chrome's
        # ~980px "assume a desktop page shrunk to fit" heuristic for
        # non-responsive content, which is a real quirk of mobile browsers
        # generally (a real phone does the same on a page missing this tag),
        # not something _device_mode_commands' job is to paper over.
        send('Page.navigate', {'url': 'data:text/html,'
                                      '<meta name="viewport" content="width=device-width, initial-scale=1">'
                                      '<body style="margin:0"><div style="height:3000px">tall</div></body>'})
        time.sleep(1.5)

        # Minimal fake session -- _device_mode_commands only reads/writes
        # these five keys.
        session = {'view': (br.VIEW_W, br.VIEW_H), 'dpr': 1, 'ua_override': None,
                  'device_mode': 'desktop', 'device_mode_view': None}
        desktop_view = (br.VIEW_W, br.VIEW_H)

        v1 = (412, 915)
        for method, params in br._device_mode_commands(session, True, view=v1):
            send(method, params)
        time.sleep(0.6)
        b1, c1 = believes(), captured_size()
        check('mobile mode: page reports the phone view', b1 == v1, f'{b1} != {v1}')
        if HAVE_PIL:
            check('mobile mode: captured frame matches the phone view', c1 == v1, f'{c1} != {v1}')

        v2 = (390, 844)
        cmds2 = br._device_mode_commands(session, True, view=v2)
        check('mobile resize re-issues the override (not a same-mode no-op)', bool(cmds2),
              "a resize inside mobile mode returned no commands -- this is the exact "
              "stale-override bug Dave's review caught: the page would stay at the OLD "
              "size while the pane moved on")
        for method, params in cmds2:
            send(method, params)
        time.sleep(0.6)
        b2, c2 = believes(), captured_size()
        check('mobile resize: page follows the NEW phone view, not the stale one',
              b2 == v2, f'{b2} != {v2} (previous {v1})')
        if HAVE_PIL:
            check('mobile resize: captured frame follows the NEW phone view', c2 == v2, f'{c2} != {v2}')

        cmds3 = br._device_mode_commands(session, False)
        check('return to desktop clears the override (clearDeviceMetricsOverride)',
              any(m == 'Emulation.clearDeviceMetricsOverride' for m, _ in cmds3), str(cmds3))
        for method, params in cmds3:
            send(method, params)
        time.sleep(0.6)
        b3, c3 = believes(), captured_size()
        check('back to desktop: page reverts to the real window size, not stuck on mobile',
              b3 == desktop_view,
              f'{b3} != {desktop_view} (mobile was {v2}) -- a live override left over from '
              f'mobile mode would leave it pinned to the mobile size')
        if HAVE_PIL:
            check('back to desktop: captured frame reverts too', c3 == desktop_view,
                  f'{c3} != {desktop_view}')

        ws.close()
    finally:
        proc.kill()
        shutil.rmtree(udd, ignore_errors=True)


mobile_device_mode_check()

if FAILS:
    print(f'\nFAIL — {len(FAILS)} check(s) broken: {", ".join(FAILS)}')
    sys.exit(1)
print('\nPASS — the page and the picture agree, at dpr=1 and dpr=2, and across mobile <-> desktop.')
