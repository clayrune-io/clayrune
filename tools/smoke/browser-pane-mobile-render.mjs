// MC-980 round 3 (Ron, from his phone, 2026-09-26): navigation now works
// (browser-pane-mobile-navigate.mjs), but the RENDERED PAGE is wrong.
// Screenshots: a Google tab opened via "New tab" rendered its DESKTOP layout
// shrunk; a LinkedIn tab's mobile-layout content (and its fixed bottom nav)
// filled only ~83% of the frame's width and ~55% of its height, the rest
// empty page background; black letterbox bands sat above/below the page;
// the tab switcher's tap on the other tab did nothing.
//
// Root cause, confirmed against the real code (not just theory):
//  - `_device_mode_commands`' Emulation.setDeviceMetricsOverride /
//    setTouchEmulationEnabled are sent ONLY to whichever tab is ACTIVE at the
//    moment the mode switches — a plain 2-tuple queued with no session_id,
//    stamped with `_active_session_id(session)` when drained. A tab that
//    attaches AFTER that (New tab, or one that existed before mobile mode
//    was ever entered) got the mobile UA (that path already checked
//    device_mode) but never the metrics override, so it rendered at
//    whatever its REAL window ended up being — its desktop layout, shrunk.
//  - That real window has a floor: measured directly via raw CDP
//    (`_scratch` diagnostics, not kept), `--window-size=412,915` still
//    reports a real innerWidth of 500 before any override lands. The
//    Emulation override is declarative and IS pixel-exact regardless of that
//    floor (same diagnostic: override to 412x915 on a window stuck at
//    500x764 still produced a 412x915 screencast frame, metadata and decoded
//    JPEG both) — so the fix relies on it alone for mobile mode rather than
//    fighting the floor via Browser.setWindowBounds.
//
// Fix: (mc/blueprints/browser_routes.py)
//  1. `Target.attachedToTarget` now sends the current mode's metrics
//     override + touch emulation to every newly attached target, not just
//     the mobile UA it already sent.
//  2. `_switch_active_tab` now resends the same to whichever target is
//     becoming active — closes the same gap for a tab that predates the
//     session's mobile-mode entry (tab-switch instead of tab-create).
//  3. `_apply_view` no longer runs the real-window `_fit_windows` for mobile
//     sessions at all (futile below the floor, and its "correct once" loop
//     mis-solves for window chrome against a floored reading) — it sends
//     the override, THEN restarts the screencast, so no frame is ever
//     captured from stale pre-override window state (the likely source of
//     the letterbox bands).
//
// This smoke reproduces (1) via the frame's reported CSS-px size — the
// `<img>` element's `aspect-ratio` style is set from the SSE `w`/`h` fields,
// themselves `session['frame_w']/['frame_h']` straight off
// Page.screencastFrame's own metadata, so it is a direct, real-CDP signal,
// not a guess about internal state. Reproduces (4) via the address bar
// actually reflecting the tab switched to, like browser-pane-mobile-navigate.
// A local fixture page (not the internet) is enough — the bug is about
// viewport dimensions, not page content.
//
// RUN: node tools/smoke/browser-pane-mobile-render.mjs   (exit 0 = all pass)
import { chromium } from 'playwright';
import { spawn } from 'child_process';
import { mkdtempSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import net from 'net';

const REPO = new URL('../../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const fails = [];
const fail = m => { fails.push(m); console.log(`❌ FAIL — ${m}`); };
const ok = m => console.log(`✅ ${m}`);

const freePort = () => new Promise(res => {
  const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
});

const tmp = mkdtempSync(join(tmpdir(), 'bp-mobile-render-'));
const port = await freePort();
const py = process.env.PYTHON || 'python';
const harness = spawn(py, [join(REPO, 'tools/smoke/browser_pane_harness.py'), String(port), tmp],
  { cwd: REPO, stdio: ['pipe', 'pipe', 'pipe'] });
let harnessOut = '';
harness.stderr.on('data', d => { harnessOut += d; });
const pagePort = await new Promise((res, rej) => {
  const t = setTimeout(() => rej(new Error('harness did not start:\n' + harnessOut)), 30000);
  harness.stdout.on('data', d => {
    harnessOut += d;
    const m = /READY \d+ (\d+)/.exec(harnessOut);
    if (m) { clearTimeout(t); res(Number(m[1])); }
  });
  harness.on('exit', c => rej(new Error(`harness exited ${c}:\n${harnessOut}`)));
});
const BASE = `http://127.0.0.1:${port}`;
const PAGE = `http://127.0.0.1:${pagePort}/page.html`;
const browser = await chromium.launch();

async function stopAll(page) {
  const st = await page.evaluate(() => fetch('/api/project/smoke/browser/status').then(r => r.json()));
  for (const s of st.sessions || [])
    await page.evaluate(sid => fetch('/api/browser/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sid }) }), s.session_id);
}

async function openPane(initialUrl) {
  // No `isMobile` here on purpose: it makes Chromium apply real viewport-meta
  // emulation, and the harness shell (unlike the real app's static/index.html)
  // serves no <meta name="viewport"> tag at all — Chromium's no-tag fallback
  // then reports a ~980px layout viewport regardless of the requested 412,
  // so `_bpIsMobile()` (window.innerWidth <= 960) reads false and the pane
  // opens in DESKTOP mode, hiding every mobile-only selector this test needs.
  // `hasTouch` alone (as browser-pane-mobile.mjs already established) gives
  // real touch input without that fallback.
  const page = await browser.newPage({ viewport: { width: 412, height: 915 }, hasTouch: true });
  page.on('pageerror', e => fail(`page error: ${e.message}`));
  await page.goto(BASE + '/');
  await page.waitForFunction(() => typeof window.openBrowserPane === 'function');
  await page.evaluate(u => { localStorage.removeItem('mc_browser_pane_geom'); window.openBrowserPane(u, 'smoke'); }, initialUrl);
  await page.waitForSelector('#mc-browser-pane');
  return page;
}

// The <img>'s aspect-ratio style is set to a static "1280/800" default at DOM
// creation (browser-pane.js line ~252, from the BP_VIEW_W/BP_VIEW_H constants)
// BEFORE any real frame arrives — that default is a false positive for "a
// frame landed", so skip it and wait for the SSE to overwrite it with the
// server's real w/h (browser-pane.js `d.w && d.h` gate at line ~692).
// Compare PARSED numbers, not the raw string — the browser re-serializes
// `el.style.aspectRatio` with its own spacing ("1280 / 800"), so a literal
// string match against the "1280/800" we wrote in JS silently fails open and
// lets the default slip through as if it were a real frame.
async function waitForFrameSize(page, label, timeoutMs = 15000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    const ar = await page.locator('#mc-browser-pane [data-bp="screen"]').evaluate(el => el.style.aspectRatio);
    const m = /^(\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)$/.exec(ar || '');
    if (m) {
      const w = parseFloat(m[1]), h = parseFloat(m[2]);
      if (!(w === 1280 && h === 800)) return { w, h };
    }
    await page.waitForTimeout(200);
  }
  fail(`${label} — no real (non-default) screencast frame dimensions arrived in ${timeoutMs}ms`);
  return null;
}

async function waitForAddress(page, predicate, label, timeoutMs = 15000) {
  const t0 = Date.now();
  let last = '';
  while (Date.now() - t0 < timeoutMs) {
    last = await page.locator('#mc-browser-pane [data-bp="url"]').inputValue();
    if (predicate(last)) return last;
    await page.waitForTimeout(200);
  }
  fail(`${label} — address bar never reflected it, stuck at "${last}" after ${timeoutMs}ms`);
  return last;
}

try {
  const page = await openPane(PAGE);
  const origDims = await waitForFrameSize(page, 'initial tab frame size');
  if (origDims) ok(`initial tab reports a frame ${origDims.w}x${origDims.h}`);

  // ── defect 3: a tab opened via "New tab" after mobile mode is already on
  //    must get the SAME mobile device-metrics override, not render its
  //    desktop layout shrunk into whatever real window it got ──
  await page.locator('#mc-browser-pane [data-bp="tabsbtn"]').click();
  await page.locator('#mc-browser-pane [data-bp="tabswitch-new"]').click();
  // the new tab auto-focuses (a real browser opens "new tab" in front); its
  // own screencast frame replaces the one just asserted above.
  await page.waitForTimeout(500);
  const newTabDims = await waitForFrameSize(page, 'new-tab frame size');
  // waitForFrameSize returns on the ORIGINAL tab's already-landed 412x809
  // frame while "+ New tab" is still being created (~250ms+ on this box), and
  // the address bar reads "" immediately because the click blanks it
  // optimistically — neither proves the new tab exists. Opening the switcher
  // then lists only the original tab, the tap hits an already-active row, and
  // the new tab takes focus afterwards for good (address bar stuck at "").
  // The tabs-count badge on the bottom bar is set from the server's `tabs`
  // event (_bpRenderTabs), which also carries active_target_id = the new tab.
  await page.waitForFunction(
    () => document.querySelector('#mc-browser-pane [data-bp="tabsbtn"]')?.textContent === '2',
    null, { timeout: 15000 });
  if (origDims && newTabDims) {
    const dw = Math.abs(newTabDims.w - origDims.w);
    if (dw <= 8) ok(`new tab ("+ New tab") reports frame width ${newTabDims.w}, matching the original tab's ${origDims.w} (within ${dw}px) — mobile override reached it`);
    else fail(`new tab reports frame width ${newTabDims.w} vs original tab's ${origDims.w} (off by ${dw}px) — it rendered without the mobile override, i.e. defect 3`);
  }

  // ── defect 4: the tab switcher's tap on the OTHER (original) tab must
  //    actually switch to it — check via a real TOUCH tap, not a mouse
  //    click, per Ron's report that it reproduces specifically under touch ──
  await page.locator('#mc-browser-pane [data-bp="tabsbtn"]').click();
  const otherRow = page.locator('#mc-browser-pane [data-bp="tabswitch-list"] [data-bp-tab]').first();
  await otherRow.waitFor({ state: 'visible' });
  const box = await otherRow.boundingBox();
  await page.touchscreen.tap(box.x + box.width / 2, box.y + box.height / 2);
  await waitForAddress(page, v => v === PAGE, 'tab switcher touch tap: switch back to the original tab');
  ok('tab switcher: a real touch tap on the other tab switched the pane to it');

  // ── defect 3, second form: the tab just switched back to must ALSO still
  //    report the same mobile frame size (it was the ACTIVE tab when mobile
  //    mode was entered, so this one already worked pre-fix — a regression
  //    guard, not a new assertion) ──
  const backDims = await waitForFrameSize(page, 'frame size after switching back');
  if (origDims && backDims) {
    const dw = Math.abs(backDims.w - origDims.w);
    if (dw <= 8) ok(`after switching back, frame width ${backDims.w} still matches the original ${origDims.w}`);
    else fail(`after switching back, frame width ${backDims.w} drifted from the original ${origDims.w} by ${dw}px`);
  }

  await stopAll(page);
  await page.close();

  // ── defect 1 (letterbox): the desktop pane must be completely unaffected
  //    by any of the mobile-only code path changes above ──
  {
    const dpage = await browser.newPage({ viewport: { width: 1300, height: 900 } });
    dpage.on('pageerror', e => fail(`desktop page error: ${e.message}`));
    await dpage.goto(BASE + '/');
    await dpage.waitForFunction(() => typeof window.openBrowserPane === 'function');
    await dpage.evaluate(u => { localStorage.removeItem('mc_browser_pane_geom'); window.openBrowserPane(u, 'smoke'); }, PAGE);
    await dpage.waitForSelector('#mc-browser-pane');
    const grip = await dpage.locator('#mc-browser-pane [data-bp="grip"]').count();
    if (grip > 0) ok('desktop viewport: resize grip still present — mobile-only changes did not touch the desktop window path');
    else fail('desktop viewport: resize grip missing — a mobile-only change leaked into the desktop pane');
    await stopAll(dpage);
    await dpage.close();
  }
} finally {
  await browser.close();
  harness.stdin.end();
  await new Promise(r => { harness.on('exit', r); setTimeout(r, 15000); });
  try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
}

console.log(fails.length ? `\n${fails.length} FAILED` : '\nALL PASS');
process.exit(fails.length ? 1 : 0);
