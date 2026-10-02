// Probe (not part of the suite): keystroke -> screencast-frame latency in the
// browser pane, through the REAL browser_routes blueprint + headless Chromium
// (tools/smoke/browser_pane_harness.py). type.html tints its page by how many
// characters it holds, so each frame the pane receives says how many keystrokes
// it contains; latency of key k = first frame showing >= k chars minus the moment
// the key was pressed. A one-way delay is put on every /api/browser/input POST to
// stand in for the tunnel.
//
//   node tools/smoke/_probe_typing_latency.mjs [oneWayDelayMs=120] [keys=24] [gapMs=60]
import { chromium } from 'playwright';
import { spawn } from 'child_process';
import { mkdtempSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import net from 'net';

const DELAY = Number(process.argv[2] ?? 120), KEYS = Number(process.argv[3] ?? 24), GAP = Number(process.argv[4] ?? 60);
const ANDROID_UA = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36';
const freePort = () => new Promise((res, rej) => { const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); s.on('error', rej); });
const REPO = new URL('../../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const tmp = mkdtempSync(join(tmpdir(), 'bp-lat-'));
const port = await freePort();
const harness = spawn(process.env.PYTHON || 'python', [join(REPO, 'tools/smoke/browser_pane_harness.py'), String(port), tmp], { cwd: REPO, stdio: ['pipe', 'pipe', 'pipe'] });
let out = ''; harness.stderr.on('data', d => { out += d; });
const pagePort = await new Promise((res, rej) => {
  const t = setTimeout(() => rej(new Error('harness did not start:\n' + out)), 30000);
  harness.stdout.on('data', d => { out += d; const m = /READY \d+ (\d+)/.exec(out); if (m) { clearTimeout(t); res(Number(m[1])); } });
});
const browser = await chromium.launch();
const context = await browser.newContext({ userAgent: ANDROID_UA, viewport: { width: 412, height: 915 }, hasTouch: true });
const page = await context.newPage();
await page.goto(`http://127.0.0.1:${port}/`);
await page.waitForFunction(() => typeof window.openBrowserPane === 'function');
await page.evaluate(u => window.openBrowserPane(u, 'lat'), `http://127.0.0.1:${pagePort}/type.html`);
await page.waitForFunction(() => document.querySelector('#mc-browser-pane img') && document.querySelector('#mc-browser-pane img').naturalWidth > 0);
await page.waitForTimeout(800);

// Decode every frame the pane paints into a char count.
await page.evaluate(() => {
  window.__frames = [];
  const img = document.querySelector('#mc-browser-pane img');
  const cv = document.createElement('canvas'), cx = cv.getContext('2d', { willReadFrequently: true });
  const table = []; for (let n = 0; n <= 400; n++) table.push([(n * 37) % 256, (n * 91) % 256, (n * 53) % 256]);
  const read = () => {
    if (!img.naturalWidth) return;
    cv.width = 8; cv.height = 8; cx.drawImage(img, 0, 0, 8, 8);
    const d = cx.getImageData(1, 1, 1, 1).data;
    let best = -1, bd = 1e9;
    for (let n = 0; n < 100; n++) { const t = table[n], e = Math.abs(t[0] - d[0]) + Math.abs(t[1] - d[1]) + Math.abs(t[2] - d[2]); if (e < bd) { bd = e; best = n; } }
    if (bd < 40) window.__frames.push({ t: Date.now(), n: best });
  };
  img.addEventListener('load', read);
  new MutationObserver(read).observe(img, { attributes: true, attributeFilter: ['src'] });
});
await page.route('**/api/browser/input', async route => { await new Promise(r => setTimeout(r, DELAY)); await route.continue(); });
const cdp = await context.newCDPSession(page);
await page.locator('#mc-browser-pane [data-bp="screen"]').tap();
await page.waitForTimeout(400);
const pressed = [];
for (let i = 0; i < KEYS; i++) {
  pressed.push(Date.now());
  await cdp.send('Input.insertText', { text: String.fromCharCode(97 + (i % 26)) });
  await new Promise(r => setTimeout(r, GAP));
}
await page.waitForTimeout(2500);
const frames = await page.evaluate(() => window.__frames);
const lat = pressed.map((t0, i) => { const f = frames.find(f => f.n >= i + 1 && f.t >= t0); return f ? f.t - t0 : null; });
const ok = lat.filter(x => x !== null).sort((a, b) => a - b);
const q = p => ok[Math.min(ok.length - 1, Math.floor(ok.length * p))];
const last = Math.max(...frames.map(f => f.n));
console.log(JSON.stringify({ oneWayDelayMs: DELAY, keys: KEYS, gapMs: GAP, framesSeen: frames.length, finalCount: last,
  measured: ok.length, p50: q(0.5), p90: q(0.9), max: ok[ok.length - 1], firstKey: lat[0], lastKey: lat[KEYS - 1] }));
await browser.close();
try { harness.stdin.end(); } catch (e) {}
setTimeout(() => { try { harness.kill(); } catch (e) {} rmSync(tmp, { recursive: true, force: true }); process.exit(0); }, 2000);
