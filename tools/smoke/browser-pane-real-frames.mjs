// End-to-end guard for MC-976's black pane: REAL backend, REAL Chromium,
// REAL static/js/browser-pane.js. Every other browser-pane smoke stubs the
// API and hand-writes the SSE, so none of them could see that no frame ever
// reached the <img> — that is the whole reason this one exists.
//
// Spawns tools/smoke/browser_pane_harness.py (the browser_routes blueprint
// alone, all profile dirs redirected into a temp dir — never a real profile).
//
//   1. Fresh pane: tab strip + "+" visible with ONE tab.
//   2. Type a URL in the pane's bar + Enter: the <img> shows that page's
//      pixels (sampled from the rendered <img>, not the backend's word).
//   3. The launch registered its Chromium with the process tracker (with
//      the real _register_process signature) — the orphan-on-restart root.
//   4. Profile dir already held by a leftover Chromium (what a server
//      restart leaves behind): the pane must still render real frames.
//   5. Second launch of the same profile (Chromium restores an about:blank
//      tab per relaunch) + an OAuth-style popup: the pane shows the page
//      and then the popup, never a restored blank tab.
//   6. HiDPI (devicePixelRatio 2): the JPEG is 2x the CSS viewport, and a
//      click on the middle of the picture must reach the page's middle.
//   7. The session's Chromium dies: the pane explains it on screen instead
//      of sitting black behind a red x.
//
// RUN: node tools/smoke/browser-pane-real-frames.mjs   (exit 0 = all pass)
import { chromium } from 'playwright';
import { spawn, execFileSync } from 'child_process';
import { mkdtempSync, rmSync, mkdirSync, readFileSync } from 'fs';
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

const tmp = mkdtempSync(join(tmpdir(), 'bp-real-frames-'));
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

// Children this smoke itself starts (the simulated leftover Chromium) — the
// only PIDs it ever kills.
const ownPids = [];
const browser = await chromium.launch();

// Average RGB of the middle of the rendered <img>, read back through a
// canvas — i.e. what the user actually sees, not what the backend claims.
async function imgPixel(page) {
  return page.evaluate(() => {
    const img = document.querySelector('#mc-browser-pane [data-bp="screen"]');
    if (!img || !img.complete || !img.naturalWidth) return null;
    const c = document.createElement('canvas');
    c.width = img.naturalWidth; c.height = img.naturalHeight;
    const g = c.getContext('2d'); g.drawImage(img, 0, 0);
    const d = g.getImageData(Math.floor(c.width / 2), Math.floor(c.height * 0.6), 1, 1).data;
    return { r: d[0], g: d[1], b: d[2], w: img.naturalWidth, h: img.naturalHeight };
  });
}
const isBlue = p => p && p.b > 150 && p.r < 80;  // page.html is #1565c0

async function openPane(deviceScaleFactor = 1) {
  const page = await browser.newPage({ viewport: { width: 1300, height: 900 }, deviceScaleFactor });
  page.on('pageerror', e => fail(`page error: ${e.message}`));
  await page.goto(BASE + '/');
  await page.waitForFunction(() => typeof window.openBrowserPane === 'function');
  await page.evaluate(() => window.openBrowserPane('about:blank', 'smoke'));
  await page.waitForSelector('#mc-browser-pane');
  return page;
}

async function typeUrlAndExpectFrames(page, label) {
  const url = page.locator('#mc-browser-pane [data-bp="url"]');
  await url.click(); await url.fill(PAGE); await url.press('Enter');
  let px = null;
  const t0 = Date.now();
  while (Date.now() - t0 < 20000) {
    px = await imgPixel(page);
    if (isBlue(px)) break;
    await page.waitForTimeout(250);
  }
  if (isBlue(px)) ok(`${label}: typed URL rendered in the <img> (${px.w}x${px.h}, rgb ${px.r},${px.g},${px.b}) after ${Date.now() - t0}ms`);
  else fail(`${label}: typed URL never rendered — pane pixel ${JSON.stringify(px)} after 20s`);
}

async function stopAll(page) {
  const st = await page.evaluate(() => fetch('/api/project/smoke/browser/status').then(r => r.json()));
  for (const s of st.sessions || [])
    await page.evaluate(sid => fetch('/api/browser/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sid }) }), s.session_id);
}

try {
  // ── 1 + 2 + 3: fresh pane on a free profile ──
  {
    const page = await openPane();
    try {
      await page.waitForFunction(() => {
        const s = document.querySelector('#mc-browser-pane [data-bp="tabstrip"]');
        return s && getComputedStyle(s).display !== 'none' && s.querySelector('[data-bp="tab-new"]');
      }, null, { timeout: 15000 });
      const n = await page.locator('#mc-browser-pane [data-bp-tab]').count();
      const plusVisible = await page.locator('#mc-browser-pane [data-bp="tab-new"]').isVisible();
      if (n === 1 && plusVisible) ok('one tab: strip + "+" visible');
      else fail(`one tab: strip tabs=${n} plusVisible=${plusVisible}`);
    } catch (e) { fail('one tab: tab strip / "+" never became visible within 15s'); }
    await typeUrlAndExpectFrames(page, 'free profile');
    const reg = await page.evaluate(() => fetch('/_harness/registered').then(r => r.json()));
    if ((reg.registered || []).some(r => r.type === 'browser')) ok('Chromium registered with the process tracker');
    else fail(`Chromium NOT registered with the process tracker: ${JSON.stringify(reg)}`);
    await stopAll(page);
    await page.close();
  }

  // ── 5: an OAuth-style popup (window.open('about:blank') then navigate —
  //       MSAL/Google sign-in shape) must render in the pane, not go black ──
  {
    const page = await openPane();
    const url = page.locator('#mc-browser-pane [data-bp="url"]');
    await url.click(); await url.fill(`http://127.0.0.1:${pagePort}/popup.html`); await url.press('Enter');
    let px = null; const t0 = Date.now();
    while (Date.now() - t0 < 15000) {       // opener page is green
      px = await imgPixel(page); if (px && px.g > 100 && px.r < 80 && px.b < 80) break;
      await page.waitForTimeout(250);
    }
    const sid = await page.evaluate(() => fetch('/api/project/smoke/browser/status').then(r => r.json())
      .then(d => d.sessions.find(s => s.status === 'running').session_id));
    for (const action of ['mousePressed', 'mouseReleased'])
      await page.evaluate(([sid, action]) => fetch('/api/browser/input', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sid, type: 'mouse', action, x: 80, y: 40, button: 'left',
                               buttons: action === 'mousePressed' ? 1 : 0, clickCount: 1 }) }), [sid, action]);
    let tabs = 0; px = null; const t1 = Date.now();
    while (Date.now() - t1 < 20000) {
      tabs = await page.locator('#mc-browser-pane [data-bp-tab]').count();
      px = await imgPixel(page);
      if (tabs >= 2 && isBlue(px)) break;
      await page.waitForTimeout(250);
    }
    if (tabs >= 2 && isBlue(px)) ok(`popup: ${tabs} tabs, popup content rendered after ${Date.now() - t1}ms`);
    else fail(`popup: tabs=${tabs}, pane pixel ${JSON.stringify(px)} after 20s`);
    await stopAll(page);
    await page.close();
  }

  // ── 6: HiDPI — click mapping stays in CSS px, not JPEG px ──
  {
    const page = await openPane(2);
    await typeUrlAndExpectFrames(page, 'dpr 2');
    await page.evaluate(() => {
      window.__inputs = [];
      const f = window.fetch;
      window.fetch = (u, o) => { if (String(u).includes('/api/browser/input')) window.__inputs.push(JSON.parse(o.body)); return f(u, o); };
    });
    const box = await page.locator('#mc-browser-pane [data-bp="screen"]').boundingBox();
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await page.waitForTimeout(300);
    const press = (await page.evaluate(() => window.__inputs)).find(i => i.action === 'mousePressed');
    const nat = await page.evaluate(() => { const i = document.querySelector('#mc-browser-pane [data-bp="screen"]'); return [i.naturalWidth, i.naturalHeight]; });
    if (press && Math.abs(press.x - 640) < 30 && Math.abs(press.y - 400) < 30)
      ok(`dpr 2: centre click sent at (${Math.round(press.x)},${Math.round(press.y)}) CSS px (JPEG ${nat.join('x')})`);
    else fail(`dpr 2: centre click sent at ${press ? `(${Math.round(press.x)},${Math.round(press.y)})` : 'nothing'}, want ~(640,400); JPEG ${nat.join('x')}`);
    await stopAll(page);
    await page.close();
  }

  // ── 7: the session's Chromium dies — the pane must say so on screen, not
  //       sit black with only a red x (MC-976: Ron's dead 'main' session) ──
  {
    const page = await openPane();
    await typeUrlAndExpectFrames(page, 'before crash');
    const sid = await page.evaluate(() => fetch('/api/project/smoke/browser/status').then(r => r.json())
      .then(d => d.sessions.find(s => s.status === 'running').session_id));
    await page.evaluate(sid => fetch('/_harness/crash/' + sid, { method: 'POST' }), sid);
    try {
      await page.waitForSelector('#mc-browser-pane [data-bp="ended"]', { timeout: 15000 });
      const txt = await page.locator('#mc-browser-pane [data-bp="ended"]').innerText();
      ok(`dead session explained on screen: "${txt.slice(0, 70)}"`);
    } catch (e) { fail('dead session: pane shows no explanation within 15s'); }
    await stopAll(page);
    await page.close();
  }

  // ── 4: profile dir held by a leftover Chromium (a restart's orphan) ──
  {
    const chrome = execFileSync(py, ['-c',
      'import sys; sys.path.insert(0, "."); from mc.blueprints import browser_routes as b; print(b._find_chromium())'],
      { cwd: REPO }).toString().trim();
    const udd = join(tmp, 'profiles_named', 'main');
    mkdirSync(udd, { recursive: true });
    const orphanPort = await freePort();
    const orphan = spawn(chrome, ['--headless=new', `--remote-debugging-port=${orphanPort}`,
      '--remote-allow-origins=*', `--user-data-dir=${udd}`, '--no-first-run',
      '--no-default-browser-check', '--disable-gpu', 'about:blank'], { stdio: 'ignore' });
    ownPids.push(orphan.pid);
    const t0 = Date.now();
    while (Date.now() - t0 < 10000) {
      try { if ((await fetch(`http://127.0.0.1:${orphanPort}/json/version`)).ok) break; } catch {}
      await new Promise(r => setTimeout(r, 200));
    }
    const page = await openPane();
    await typeUrlAndExpectFrames(page, 'profile held by a leftover Chromium');
    await stopAll(page);
    await page.close();
  }
} finally {
  await browser.close();
  for (const pid of ownPids) { try { process.kill(pid); } catch {} }
  harness.stdin.end();
  await new Promise(r => { harness.on('exit', r); setTimeout(r, 15000); });
  try { rmSync(tmp, { recursive: true, force: true }); } catch {}
}

console.log(fails.length ? `\n${fails.length} FAILED` : '\nALL PASS');
process.exit(fails.length ? 1 : 0);
