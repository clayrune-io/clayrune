// MC-976 zoom guard: the page must render at the pane's own size, 1 CSS px
// to 1 CSS px, with no letterbox — for the tab the pane opened AND for a
// window.open() popup. REAL backend, REAL Chromium, REAL browser-pane.js
// (tools/smoke/browser_pane_harness.py, temp profile dirs only).
//
// What Ron saw (2026-09-26): a "Sign in with Google" popup opened at headless
// Chromium's fake 800x600 screen, a 784x470 CSS viewport, which the pane then
// stretched ~2.2x to fill itself — huge blurry text, black bars top and bottom.
// The root tab had the milder form of the same bug: a fixed 1280x800 viewport
// scaled to whatever size the pane happened to be.
//
//   1. dpr 1: the tab the pane opened fills the pane at scale 1, no bars.
//   2. The pane is resized: the page follows it.
//   3. A window.open() popup: same — not the popup's own tiny window.
//   4. dpr 2 (HiDPI client): same, the JPEG carries 2x device pixels, and
//      a popup fits too.
//
// RUN: node tools/smoke/browser-pane-fit.mjs   (exit 0 = all pass)
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

const tmp = mkdtempSync(join(tmpdir(), 'bp-fit-'));
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

// The picture as the user sees it: the frame's CSS size (JPEG px / dpr), the
// box the pane gives it, and the object-fit:contain scale + letterbox bars
// that result. scale 1 and zero bars = the page is at its real size.
async function fit(page) {
  return page.evaluate(() => {
    const img = document.querySelector('#mc-browser-pane [data-bp="screen"]');
    if (!img || !img.naturalWidth) return null;
    const dpr = window.devicePixelRatio || 1;
    const fw = img.naturalWidth / dpr, fh = img.naturalHeight / dpr;
    const r = img.parentElement.getBoundingClientRect();
    const s = Math.min(r.width / fw, r.height / fh);
    return { fw: Math.round(fw), fh: Math.round(fh), cw: Math.round(r.width), ch: Math.round(r.height),
             scale: +s.toFixed(3), barX: Math.round(r.width - fw * s), barY: Math.round(r.height - fh * s),
             nat: `${img.naturalWidth}x${img.naturalHeight}` };
  });
}
const fits = f => f && Math.abs(f.scale - 1) <= 0.02 && f.barX <= 4 && f.barY <= 4;

async function expectFit(page, label, extra = () => true) {
  let f = null; const t0 = Date.now();
  while (Date.now() - t0 < 15000) {
    f = await fit(page);
    if (fits(f) && extra(f)) break;
    await page.waitForTimeout(250);
  }
  if (fits(f) && extra(f)) ok(`${label}: frame ${f.fw}x${f.fh} CSS in a ${f.cw}x${f.ch} pane, scale ${f.scale}, bars ${f.barX}/${f.barY}px (JPEG ${f.nat})`);
  else {
    const st = await page.evaluate(() => fetch('/api/project/smoke/browser/status').then(r => r.json())
      .then(d => fetch('/_harness/session/' + d.sessions.find(s => s.status === 'running').session_id))
      .then(r => r.json()).catch(e => String(e)));
    fail(`${label}: ${JSON.stringify(f)} after 15s — want scale 1, no letterbox; server ${JSON.stringify(st)}`);
  }
  return f;
}

async function openPane(deviceScaleFactor = 1) {
  const page = await browser.newPage({ viewport: { width: 1300, height: 900 }, deviceScaleFactor });
  page.on('pageerror', e => fail(`page error: ${e.message}`));
  await page.goto(BASE + '/');
  await page.waitForFunction(() => typeof window.openBrowserPane === 'function');
  await page.evaluate(() => { localStorage.removeItem('mc_browser_pane_geom'); window.openBrowserPane('about:blank', 'smoke'); });
  await page.waitForSelector('#mc-browser-pane');
  return page;
}

async function go(page, url) {
  const bar = page.locator('#mc-browser-pane [data-bp="url"]');
  await bar.click(); await bar.fill(url); await bar.press('Enter');
}

async function stopAll(page) {
  const st = await page.evaluate(() => fetch('/api/project/smoke/browser/status').then(r => r.json()));
  for (const s of st.sessions || [])
    await page.evaluate(sid => fetch('/api/browser/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sid }) }), s.session_id);
}

async function openPopup(page) {
  await go(page, `http://127.0.0.1:${pagePort}/popup.html`);
  await page.waitForTimeout(1500);
  const sid = await page.evaluate(() => fetch('/api/project/smoke/browser/status').then(r => r.json())
    .then(d => d.sessions.find(s => s.status === 'running').session_id));
  for (const action of ['mousePressed', 'mouseReleased'])
    await page.evaluate(([sid, action]) => fetch('/api/browser/input', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sid, type: 'mouse', action, x: 80, y: 40, button: 'left',
                             buttons: action === 'mousePressed' ? 1 : 0, clickCount: 1 }) }), [sid, action]);
  await page.waitForFunction(() => document.querySelectorAll('#mc-browser-pane [data-bp-tab]').length >= 2,
    null, { timeout: 15000 }).catch(() => fail('popup: second tab never appeared'));
  await page.waitForTimeout(1500);   // let the popup's own first frames land
}

try {
  // ── 1 + 2 + 3 at dpr 1 ──
  {
    const page = await openPane(1);
    await go(page, PAGE);
    await expectFit(page, 'dpr 1, opened tab');

    await page.evaluate(() => { const w = document.getElementById('mc-browser-pane'); w.style.width = '900px'; w.style.height = '620px'; });
    await expectFit(page, 'dpr 1, pane resized to 900x620', f => f.cw < 905);

    await openPopup(page);
    await expectFit(page, 'dpr 1, window.open() popup');
    await stopAll(page);
    await page.close();
  }

  // ── 4: HiDPI client ──
  {
    const page = await openPane(2);
    await go(page, PAGE);
    const f = await expectFit(page, 'dpr 2, opened tab');
    if (f && f.nat === `${f.fw * 2}x${f.fh * 2}`) ok(`dpr 2: JPEG carries 2x device pixels (${f.nat})`);
    else fail(`dpr 2: JPEG ${f && f.nat}, want 2x the CSS frame ${f && `${f.fw}x${f.fh}`}`);
    // headless opens a dpr-2 popup at 400x300 (384x181 CSS measured) -- the worst stretch of all
    await openPopup(page);
    await expectFit(page, 'dpr 2, window.open() popup');
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
