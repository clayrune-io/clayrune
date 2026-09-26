// MC-980: at <=960px the browser pane is its OWN full-screen phone-browser
// sheet, not the desktop floating window shrunk to fit. REAL backend, REAL
// Chromium for the pane's own CDP session (tools/smoke/browser_pane_harness.py,
// temp profile dirs only) — REAL Playwright page for the outer Mission
// Control UI, driven at an actual phone viewport (412x915, a Pixel 7).
//
// What Ron saw on his phone (2026-09-26): the address field showed only
// "https:" because back/forward/reload/clipboard/copy/menu/profile/minimize/
// restore shared one row; the close X was off-screen; the pane was a
// draggable floating window half off-screen over the chat.
//
//   1. Phone viewport: the pane fills the screen (fixed inset:0), not a
//      window positioned/sized like the desktop one.
//   2. Not draggable: no resize grip in the DOM, and a drag gesture on the
//      top bar does not move the pane.
//   3. Address bar is address-bar-sized: >= 60% of the viewport width (not
//      squeezed to a few characters by six other controls sharing its row).
//   4. Close control is visible and inside the viewport (not the desktop
//      window's × that ends up off-screen once the window is squeezed).
//   5. Desktop viewport (1300x900, same as the other browser-pane smokes):
//      the floating window behaviour is unchanged — sized/positioned like a
//      window, drag still works, grip still exists.
//
// RUN: node tools/smoke/browser-pane-mobile.mjs   (exit 0 = all pass)
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

const tmp = mkdtempSync(join(tmpdir(), 'bp-mobile-'));
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

async function openPane({ width, height }) {
  const page = await browser.newPage({ viewport: { width, height } });
  page.on('pageerror', e => fail(`page error: ${e.message}`));
  await page.goto(BASE + '/');
  await page.waitForFunction(() => typeof window.openBrowserPane === 'function');
  await page.evaluate(() => { localStorage.removeItem('mc_browser_pane_geom'); window.openBrowserPane(undefined, 'smoke'); });
  await page.waitForSelector('#mc-browser-pane');
  return page;
}

function layout(page) {
  return page.evaluate(() => {
    const win = document.getElementById('mc-browser-pane');
    const r = win.getBoundingClientRect();
    const url = win.querySelector('[data-bp="url"]');
    const close = win.querySelector('[data-bp="close"]');
    const grip = win.querySelector('[data-bp="grip"]');
    const cr = close ? close.getBoundingClientRect() : null;
    return {
      mobile: win.dataset.mobile,
      position: getComputedStyle(win).position,
      pane: { w: Math.round(r.width), h: Math.round(r.height), l: Math.round(r.left), t: Math.round(r.top) },
      urlW: url ? Math.round(url.getBoundingClientRect().width) : 0,
      hasGrip: !!grip,
      closeVisible: !!cr && cr.width > 0 && cr.height > 0 &&
        cr.left >= 0 && cr.top >= 0 && cr.right <= window.innerWidth && cr.bottom <= window.innerHeight,
      innerW: window.innerWidth, innerH: window.innerHeight,
    };
  });
}

async function dragTopBar(page) {
  // Desktop `bar` is packed edge-to-edge with buttons/input (mobile's is not
  // draggable at all, so this is only exercised there) -- the container's own
  // left padding (style `padding:8px 10px`, before the first button's icon
  // box begins) is the one spot pointerdown's `closest('button')`/INPUT guard
  // does not exclude.
  const bar = page.locator('#mc-browser-pane [data-bp="bar"]');
  const box = await bar.boundingBox();
  await page.mouse.move(box.x + 3, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + 3 + 120, box.y + box.height / 2 + 120, { steps: 5 });
  await page.mouse.up();
}

try {
  // ── 1-4: phone viewport ──
  {
    const page = await openPane({ width: 412, height: 915 });
    const before = await layout(page);

    if (before.mobile === '1') ok('phone viewport: pane marks itself mobile (data-mobile=1)');
    else fail(`phone viewport: dataset.mobile=${before.mobile}, want "1"`);

    if (before.position === 'fixed' && before.pane.l === 0 && before.pane.t === 0 &&
        before.pane.w === before.innerW && before.pane.h >= before.innerH - 2)
      ok(`phone viewport: pane fills the screen (${before.pane.w}x${before.pane.h} at ${before.pane.l},${before.pane.t}, position:${before.position})`);
    else
      fail(`phone viewport: pane is ${JSON.stringify(before.pane)} position:${before.position} in a ${before.innerW}x${before.innerH} screen — want a full-screen fixed sheet, not a window`);

    if (!before.hasGrip) ok('phone viewport: no resize grip in the DOM');
    else fail('phone viewport: resize grip is present — mobile pane must not be resizable');

    await dragTopBar(page);
    const after = await layout(page);
    if (after.pane.l === before.pane.l && after.pane.t === before.pane.t)
      ok('phone viewport: dragging the top bar does not move the pane');
    else
      fail(`phone viewport: pane moved from ${before.pane.l},${before.pane.t} to ${after.pane.l},${after.pane.t} on a drag gesture — must not be draggable`);

    const urlFrac = before.urlW / before.innerW;
    if (urlFrac >= 0.6) ok(`phone viewport: address bar is ${before.urlW}px, ${(urlFrac * 100).toFixed(0)}% of ${before.innerW}px viewport (>=60%)`);
    else fail(`phone viewport: address bar is ${before.urlW}px, only ${(urlFrac * 100).toFixed(0)}% of ${before.innerW}px viewport — want >=60%`);

    if (before.closeVisible) ok('phone viewport: close control is visible inside the viewport');
    else fail('phone viewport: close control is not visible/inside the viewport');

    await stopAll(page);
    await page.close();
  }

  // ── 5: desktop viewport unchanged ──
  {
    const page = await openPane({ width: 1300, height: 900 });
    const before = await layout(page);

    if (before.mobile === '0') ok('desktop viewport: pane marks itself non-mobile (data-mobile=0)');
    else fail(`desktop viewport: dataset.mobile=${before.mobile}, want "0"`);

    const fillsScreen = before.pane.w === before.innerW && before.pane.h === before.innerH;
    if (!fillsScreen) ok(`desktop viewport: pane is a floating window (${before.pane.w}x${before.pane.h} in a ${before.innerW}x${before.innerH} screen), not full-screen`);
    else fail('desktop viewport: pane fills the whole screen — the desktop floating window must be unaffected by MC-980');

    if (before.hasGrip) ok('desktop viewport: resize grip is present');
    else fail('desktop viewport: resize grip is missing — desktop pane must stay resizable');

    await dragTopBar(page);
    const after = await layout(page);
    if (after.pane.l !== before.pane.l || after.pane.t !== before.pane.t)
      ok(`desktop viewport: dragging the top bar moves the pane (${before.pane.l},${before.pane.t} -> ${after.pane.l},${after.pane.t})`);
    else
      fail('desktop viewport: pane did not move on a drag gesture — desktop dragging must still work');

    await stopAll(page);
    await page.close();
  }
} finally {
  await browser.close();
  harness.stdin.end();
  await new Promise(r => { harness.on('exit', r); setTimeout(r, 15000); });
  try { rmSync(tmp, { recursive: true, force: true }); } catch {}
}

console.log(fails.length ? `\n${fails.length} FAILED` : '\nALL PASS');
process.exit(fails.length ? 1 : 0);
