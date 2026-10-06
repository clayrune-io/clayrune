// The pane's binary frame transport and its SSE fallback (backlog 629d2205).
// REAL backend (tools/smoke/browser_pane_harness.py: real Chromium on the CDP pipe, real
// /api/browser/frames and /api/browser/stream) + the REAL static/js/browser-pane.js. Every
// check reads the pixels out of the rendered <img>, not the pane's own word for it.
//
//   1. Default: the pane takes /api/browser/frames (transport 'bin'), paints the page from
//      Blob URLs, and delivers many frames.
//   2. localStorage mc_bp_transport=sse forces the legacy EventSource path (data: URLs).
//   3. /frames answers 404 (older server / proxy that rejects it): falls back to SSE, paints.
//   4. /frames answers 200 text/html (a proxy or SPA catch-all rewriting it): falls back to SSE.
//   5. /frames never produces a byte (a proxy holding a streaming body): the first-bytes
//      watchdog gives up after ~6s and falls back to SSE.
//   6. The binary stream drops mid-session: the pane reopens it and keeps painting.
//
// RUN: node tools/smoke/browser-pane-binary-stream.mjs   (exit 0 = all pass)
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

const tmp = mkdtempSync(join(tmpdir(), 'bp-bin-stream-'));
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
  return { r: d[0], g: d[1], b: d[2], src: img.src.slice(0, 5), t: window._bpStats && window._bpStats.transport,
           frames: window._bpStats && window._bpStats.frames };
});

async function openAndLoad({ pref, setup, target = PAGE }) {
  const page = await browser.newPage({ viewport: { width: 1300, height: 900 } });
  page.on('pageerror', e => fail(`page error: ${e.message}`));
  const frameReqs = [];
  page.on('request', r => { if (r.url().includes('/api/browser/frames')) frameReqs.push(Date.now()); });
  await page.addInitScript(p => { try { if (p) localStorage.setItem('mc_bp_transport', p); } catch (e) {} }, pref || '');
  if (setup) await setup(page);
  await page.goto(BASE + '/');
  await page.waitForFunction(() => typeof window.openBrowserPane === 'function');
  await page.evaluate(() => window.openBrowserPane('about:blank', 'smoke'));
  await page.waitForSelector('#mc-browser-pane [data-bp-tab]', { timeout: 20000 });
  const url = page.locator('#mc-browser-pane [data-bp="url"]');
  await url.click(); await url.fill(target); await url.press('Enter');
  return { page, frameReqs };
}

async function waitBlue(page, ms = 20000) {
  let px = null; const t0 = Date.now();
  while (Date.now() - t0 < ms) { px = await imgInfo(page); if (isBlue(px)) return px; await page.waitForTimeout(250); }
  return px;
}

async function stopAll(page) {
  const st = await page.evaluate(() => fetch('/api/project/smoke/browser/status').then(r => r.json()));
  for (const s of st.sessions || [])
    await page.evaluate(sid => fetch('/api/browser/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sid }) }), s.session_id);
  await page.close();
}

try {
  // ── 1: default = binary ──
  {
    const { page } = await openAndLoad({});
    const px = await waitBlue(page);
    if (isBlue(px) && px.t === 'bin' && px.src === 'blob:') ok(`default: binary transport painted the page from a Blob URL (frames=${px.frames})`);
    else fail(`default: expected bin + blob:, got ${JSON.stringify(px)}`);
    await stopAll(page);
  }
  // ── 2: forced SSE ──
  {
    const { page } = await openAndLoad({ pref: 'sse' });
    const px = await waitBlue(page);
    if (isBlue(px) && px.t === 'sse' && px.src === 'data:') ok('mc_bp_transport=sse: legacy EventSource path painted from a data: URL');
    else fail(`forced sse: expected sse + data:, got ${JSON.stringify(px)}`);
    await stopAll(page);
  }
  // ── 3: /frames 404 ──
  {
    const { page } = await openAndLoad({ setup: p => p.route('**/api/browser/frames*', r => r.fulfill({ status: 404, body: 'no' })) });
    const px = await waitBlue(page);
    if (isBlue(px) && px.t === 'sse') ok('/frames 404: fell back to SSE and painted');
    else fail(`404 fallback: got ${JSON.stringify(px)}`);
    await stopAll(page);
  }
  // ── 4: /frames 200 text/html ──
  {
    const { page } = await openAndLoad({ setup: p => p.route('**/api/browser/frames*', r => r.fulfill({ status: 200, contentType: 'text/html', body: '<html></html>' })) });
    const px = await waitBlue(page);
    if (isBlue(px) && px.t === 'sse') ok('/frames answered text/html: fell back to SSE and painted');
    else fail(`html fallback: got ${JSON.stringify(px)}`);
    await stopAll(page);
  }
  // ── 5: /frames never produces a byte ──
  {
    const t0 = Date.now();
    const { page } = await openAndLoad({ setup: p => p.route('**/api/browser/frames*', () => { /* held open, never answered */ }) });
    const px = await waitBlue(page, 30000);
    const took = Date.now() - t0;
    if (isBlue(px) && px.t === 'sse' && took > 5000) ok(`silent /frames: first-bytes watchdog fell back to SSE after ~${Math.round(took / 1000)}s and painted`);
    else fail(`watchdog fallback: got ${JSON.stringify(px)} after ${took}ms`);
    await stopAll(page);
  }
  // ── 6: binary stream drops after it worked (needs a page that keeps repainting, so
  //       there are enough chunks to cut the first stream) ──
  {
    const { page, frameReqs } = await openAndLoad({
      target: `http://127.0.0.1:${pagePort}/dense.html`,
      setup: p => p.addInitScript(() => {
        const of = window.fetch; let n = 0;
        window.fetch = async (u, o) => {
          const r = await of(u, o);
          if (!String(u).includes('/api/browser/frames') || n++ > 0) return r;
          const rd = r.body.getReader(); let c = 0;     // the first stream is cut after 20 chunks
          const body = new ReadableStream({ async pull(ctl) {
            const { value, done } = await rd.read();
            if (done || ++c > 20) { ctl.close(); try { rd.cancel(); } catch (e) {} return; }
            ctl.enqueue(value);
          } });
          return new Response(body, { status: r.status, headers: r.headers });
        };
      }) });
    await page.waitForTimeout(4000);
    const stats = () => page.evaluate(() => ({ ...window._bpStats }));   // not imgInfo: a repainting <img> is often mid-load
    const f0 = (await stats()).frames;
    await page.waitForTimeout(1500);
    const info = await stats();
    if (frameReqs.length >= 2 && info && info.transport === 'bin' && info.frames > f0)
      ok(`dropped binary stream was reopened (${frameReqs.length} /frames requests), still binary and painting (${f0} -> ${info.frames} frames)`);
    else fail(`reconnect: /frames requests=${frameReqs.length}, info=${JSON.stringify(info)}, frames earlier=${f0}`);
    await stopAll(page);
  }
} catch (e) { fail('smoke crashed: ' + (e.stack || e)); }
finally {
  await browser.close();
  try { harness.stdin.end(); } catch (e) {}
  setTimeout(() => { try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {} process.exit(fails.length ? 1 : 0); }, 1500);
}
