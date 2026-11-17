// A resized page still reaches the pane (MC-1197).
// REAL backend (tools/smoke/browser_pane_harness.py: real Chromium on the CDP pipe) + the
// REAL static/js/browser-pane.js. Every check reads the pixels and size of the rendered
// <img>, not the pane's own word for it.
//
// Headless Chromium keeps a screencast running across a main-frame resize but often never
// sends the picture drawn at the new size; on a static page nothing repaints after that, so
// the pane stays on the old frame (or black, if the resize came before the first frame).
// Measured 2026-11-17 without the reader's Page.frameResized re-arm, no CPU load: 12 of 22
// raw resizes produced no frame in 6s, and 3 of 4 launches straight onto a static page
// stayed black (Chromium resizes a new document by 1px right after its first paint).
//
//   1. Launch the pane straight onto a static page, three times: each paints the page.
//   2. Five window resizes that bypass the pane's own fit (no stop+start after them, the
//      way a page-driven or Chromium-driven resize arrives): each delivers a new frame at
//      the new height to the pane's <img> within 3s.
//   3. The same five resizes on two sessions launched through the API with NO pane attached
//      (how an agent opens one): each bumps the server's frame to the new height within 3s.
//      This is the check that fails without the re-arm and without CPU load (12 of 22
//      stuck, measured); with a pane attached the stall only showed up under load.
//
// RUN: node tools/smoke/browser-pane-resize-frame.mjs   (exit 0 = all pass)
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

const tmp = mkdtempSync(join(tmpdir(), 'bp-resize-frame-'));
const port = await freePort();
const harness = spawn(process.env.PYTHON || 'python', [join(REPO, 'tools/smoke/browser_pane_harness.py'), String(port), tmp],
  { cwd: REPO, stdio: ['pipe', 'pipe', 'pipe'] });
let harnessOut = '';
harness.stderr.on('data', d => { harnessOut += d; });
const pagePort = await new Promise((res, rej) => {
  const t = setTimeout(() => rej(new Error('harness did not start:\n' + harnessOut)), 30000);
  harness.stdout.on('data', d => { harnessOut += d; const m = /READY \d+ (\d+)/.exec(harnessOut); if (m) { clearTimeout(t); res(Number(m[1])); } });
  harness.on('exit', c => rej(new Error(`harness exited ${c}:\n${harnessOut}`)));
});
const BASE = `http://127.0.0.1:${port}`;
const PAGE = `http://127.0.0.1:${pagePort}/page.html`;
const browser = await chromium.launch();

// The harness page is #1565c0; sample the middle of the <img> through a canvas.
const isBlue = p => p && p.b > 150 && p.r < 80;
const imgInfo = page => page.evaluate(() => {
  const img = document.querySelector('#mc-browser-pane [data-bp="screen"]');
  if (!img || !img.complete || !img.naturalWidth) return null;
  const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight;
  const g = c.getContext('2d'); g.drawImage(img, 0, 0);
  const d = g.getImageData(Math.floor(c.width / 2), Math.floor(c.height * 0.6), 1, 1).data;
  return { r: d[0], g: d[1], b: d[2], w: img.naturalWidth, h: img.naturalHeight,
           frames: window._bpStats && window._bpStats.frames };
});

async function openPane(url) {
  const page = await browser.newPage({ viewport: { width: 1300, height: 900 } });
  page.on('pageerror', e => fail(`page error: ${e.message}`));
  await page.goto(BASE + '/');
  await page.waitForFunction(() => typeof window.openBrowserPane === 'function');
  await page.evaluate(u => window.openBrowserPane(u, 'smoke'), url);
  await page.waitForSelector('#mc-browser-pane [data-bp-tab]', { timeout: 20000 });
  return page;
}

async function waitFor(page, pred, ms) {
  let px = null; const t0 = Date.now();
  while (Date.now() - t0 < ms) { px = await imgInfo(page); if (pred(px)) return { px, took: Date.now() - t0 }; await page.waitForTimeout(150); }
  return { px, took: null };
}

const sessionIds = page => page.evaluate(() => fetch('/api/project/smoke/browser/status').then(r => r.json()))
  .then(st => (st.sessions || []).map(s => s.session_id));
const harnessState = sid => fetch(`${BASE}/_harness/session/${sid}`).then(r => r.json());

async function stopAll(page) {
  for (const sid of await sessionIds(page))
    await page.evaluate(s => fetch('/api/browser/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: s }) }), sid);
  await page.close();
}

try {
  // ── 1: straight onto a static page ──
  for (let k = 0; k < 3; k++) {
    const page = await openPane(PAGE);
    const { px, took } = await waitFor(page, isBlue, 15000);
    if (took != null) ok(`direct launch ${k + 1}/3: static page painted after ${took}ms (frames=${px.frames})`);
    else {
      const [sid] = await sessionIds(page);
      fail(`direct launch ${k + 1}/3: no page in 15s, pane shows ${JSON.stringify(px)}, server ${JSON.stringify(sid && await harnessState(sid))}`);
    }
    await stopAll(page);
  }
  // ── 2: resizes that bypass the pane's fit ──
  {
    const page = await openPane(PAGE);
    const first = await waitFor(page, isBlue, 15000);
    const [sid] = await sessionIds(page);
    if (first.took == null || !sid) fail(`resize setup: page never painted (${JSON.stringify(first.px)})`);
    else {
      let good = 0;
      for (let r = 0; r < 5; r++) {
        await page.waitForTimeout(1200);          // let the previous frame settle; no repaint is pending
        const before = await imgInfo(page);
        const h = 640 + 23 * r;
        const res = await fetch(`${BASE}/_harness/raw_resize/${sid}`, { method: 'POST',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ w: 1136, h }) }).then(x => x.json());
        if (!res.ok) { fail(`resize ${r + 1}: harness refused: ${JSON.stringify(res)}`); continue; }
        const { px, took } = await waitFor(page, p => isBlue(p) && p.h !== before.h && p.frames > before.frames, 3000);
        if (took != null) { good++; continue; }
        fail(`resize ${r + 1}/5 to h=${h}: no new frame in 3s; pane ${JSON.stringify(before)} -> ${JSON.stringify(px)}, server ${JSON.stringify(await harnessState(sid))}`);
      }
      if (good === 5) ok('5/5 resizes that bypass the pane fit delivered a new-size frame to the pane');
    }
    await stopAll(page);
  }
  // ── 3: API-launched, no pane attached ──
  {
    const api = (u, b) => fetch(BASE + u, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(b) }).then(x => x.json());
    let good = 0, total = 0;
    for (let k = 0; k < 2; k++) {
      const { session_id: sid } = await api('/api/browser/launch', { project_id: 'smoke', url: PAGE, ephemeral: true });
      let st = null; const t0 = Date.now();
      while (Date.now() - t0 < 15000) { st = await harnessState(sid); if (st.frame_seq > 0 && st.frame[0]) break; await new Promise(r => setTimeout(r, 200)); }
      if (!(st && st.frame_seq > 0)) { fail(`api session ${k + 1}: no first frame in 15s: ${JSON.stringify(st)}`); await api('/api/browser/stop', { session_id: sid }); continue; }
      for (let r = 0; r < 5; r++) {
        await new Promise(res => setTimeout(res, 1200));
        const before = await harnessState(sid);
        const h = 620 + 31 * r + 7 * k;
        total++;
        const res = await api(`/_harness/raw_resize/${sid}`, { w: 1136, h });
        if (!res.ok) { fail(`api resize: harness refused: ${JSON.stringify(res)}`); continue; }
        let after = before; const t1 = Date.now();
        while (Date.now() - t1 < 3000) { after = await harnessState(sid); if (after.frame_seq > before.frame_seq && after.frame[1] !== before.frame[1]) break; await new Promise(x => setTimeout(x, 150)); }
        if (after.frame_seq > before.frame_seq && after.frame[1] !== before.frame[1]) good++;
        else fail(`api session ${k + 1} resize ${r + 1} to h=${h}: no new-size frame in 3s (frame ${JSON.stringify(before.frame)} seq ${before.frame_seq} -> ${JSON.stringify(after.frame)} seq ${after.frame_seq}, err ${after.error})`);
      }
      await api('/api/browser/stop', { session_id: sid });
    }
    if (good === total && total === 10) ok('10/10 resizes on API-launched sessions (no pane) delivered a new-size frame to the server');
  }
} catch (e) { fail('smoke crashed: ' + (e.stack || e)); }
finally {
  await browser.close();
  try { harness.stdin.end(); } catch (e) {}
  setTimeout(() => { try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {} process.exit(fails.length ? 1 : 0); }, 1500);
}
