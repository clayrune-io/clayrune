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

Two checks:
  1. No live setDeviceMetricsOverride in browser_routes. Anything that tells the
     page a size other than the one it has re-creates this class of bug.
  2. Empirically, with the SHIPPED window sizing, innerWidth/innerHeight equal
     the screencast's deviceWidth/deviceHeight.

RUN:  python tools/smoke/browser_pane_viewport.py
Exit 0 = the page and the picture agree.
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
src = (REPO / 'mc' / 'blueprints' / 'browser_routes.py').read_text(encoding='utf-8')
live_override = [ln.strip() for ln in src.split('\n')
                 if 'setDeviceMetricsOverride' in ln and not ln.strip().startswith('#')]
check('no live setDeviceMetricsOverride', not live_override,
      'an override must MATCH what the screencast captures, or it re-creates a '
      'band of the page nobody can see or reach: ' + '; '.join(live_override))


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

if FAILS:
    print(f'\nFAIL — {len(FAILS)} check(s) broken: {", ".join(FAILS)}')
    sys.exit(1)
print('\nPASS — the page and the picture agree, at dpr=1 and dpr=2.')
