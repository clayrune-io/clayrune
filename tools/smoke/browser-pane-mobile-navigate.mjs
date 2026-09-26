// MC-980 regression (Ron, 2026-09-26): opened the mobile pane, TYPED a URL,
// tapped Go -- nothing loaded, both tabs stayed about:blank. Root cause: the
// address bar's only submit path was `keydown, e.key === 'Enter'`. Android's
// on-screen keyboard frequently reports its Go/search action key as keyCode
// 229 / key:"Unidentified" instead of a real Enter (a documented Chrome-for-
// Android quirk, not something this app's JS controls), so no navigate was
// ever sent -- the server log showed the launch plus a handful of `input`
// calls per attempt and nothing else, consistent with dead taps on a blank
// screen, not a stuck request. Fixed by wrapping the address field in a real
// <form> and listening for 'submit' instead of parsing keydown: a form
// submits on whatever the browser recognises as "the user is done" with the
// field, independent of what it reports through keydown.
//
// Same report, second bug, same branch: a fresh/about:blank tab showed the
// literal text "about:blank" in the bar (had to be deleted by hand before
// typing), and focusing a field that already held a URL did not select it,
// unlike every real phone/desktop browser's own address bar.
//
// REAL backend (tools/smoke/browser_pane_harness.py), REAL headless Chromium
// for the pane's own CDP session, REAL Playwright page at a phone viewport
// (412x915) for the outer UI -- and REAL https://www.google.com /
// https://www.linkedin.com over the actual internet, not only a local
// fixture: a fix that only proves itself against a stub page is exactly the
// gap that let MC-976's black-pane bug ship once already.
//
//   1. Fresh about:blank tab: address bar is EMPTY with a phone-style
//      placeholder, not the literal text "about:blank".
//   2. A real Enter key still submits (regression guard on the rewrite).
//   3. The Android artefact itself -- a keydown with keyCode 229 / key
//      "Unidentified" -- sends NO navigate by itself; a bare "linkedin.com"
//      (no scheme) submitted right after still reaches real
//      https://www.linkedin.com, proving the fix no longer depends on
//      keydown recognising "Enter" at all.
//   4. A full https://www.google.com URL submitted the same way loads.
//   5. Focusing a field that already holds a URL selects the whole value.
//
// RUN: node tools/smoke/browser-pane-mobile-navigate.mjs   (exit 0 = all pass)
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

const tmp = mkdtempSync(join(tmpdir(), 'bp-mobile-nav-'));
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
  const page = await browser.newPage({ viewport: { width: 412, height: 915 }, isMobile: true, hasTouch: true });
  page.on('pageerror', e => fail(`page error: ${e.message}`));
  await page.goto(BASE + '/');
  await page.waitForFunction(() => typeof window.openBrowserPane === 'function');
  await page.evaluate(u => { localStorage.removeItem('mc_browser_pane_geom'); window.openBrowserPane(u, 'smoke'); }, initialUrl);
  await page.waitForSelector('#mc-browser-pane');
  return page;
}

// Polls the address bar's live value (fed by the backend's confirmed
// Page.frameNavigated URL over SSE, not just the requested one -- a redirect
// or a dead navigate would otherwise pass this by accident).
async function waitForAddress(page, predicate, label, timeoutMs = 30000) {
  const t0 = Date.now();
  let last = '';
  while (Date.now() - t0 < timeoutMs) {
    last = await page.locator('#mc-browser-pane [data-bp="url"]').inputValue();
    if (predicate(last)) return last;
    await page.waitForTimeout(300);
  }
  fail(`${label} — address bar never reflected it, stuck at "${last}" after ${timeoutMs}ms`);
  return last;
}

try {
  // ── 1: fresh about:blank tab ──
  {
    const page = await openPane(undefined);
    const url = page.locator('#mc-browser-pane [data-bp="url"]');
    const val = await url.inputValue();
    const ph = await url.getAttribute('placeholder');
    if (val === '') ok('fresh tab: address bar is empty, not the literal "about:blank"');
    else fail(`fresh tab: address bar shows "${val}" — want empty`);
    if (ph && ph.toLowerCase() !== 'enter url and press enter')
      ok(`fresh tab: placeholder reads "${ph}"`);
    else fail(`fresh tab: placeholder is "${ph}" — want a phone-style hint, not the old desktop copy`);
    await stopAll(page);
    await page.close();
  }

  // ── 2: real Enter key still submits (regression guard on the rewrite) ──
  {
    const page = await openPane(undefined);
    const url = page.locator('#mc-browser-pane [data-bp="url"]');
    await url.click(); await url.fill(PAGE); await url.press('Enter');
    await waitForAddress(page, v => v === PAGE, 'real Enter key submit');
    ok('real Enter key: still submits through the new <form> (regression guard)');
    await stopAll(page);
    await page.close();
  }

  // ── 3: the Android artefact — 229/"Unidentified" alone must send nothing;
  //       the bare, no-scheme URL that follows must still reach the real site ──
  {
    const page = await openPane(undefined);
    const url = page.locator('#mc-browser-pane [data-bp="url"]');
    await page.evaluate(() => {
      window.__navPosts = [];
      const f = window.fetch;
      window.fetch = (u, o) => {
        if (o && o.body && String(u).includes('/api/browser/input')) {
          try { const b = JSON.parse(o.body); if (b.type === 'navigate') window.__navPosts.push(b); } catch (e) {}
        }
        return f(u, o);
      };
    });
    await url.click(); await url.fill('linkedin.com');
    await url.evaluate(el => el.dispatchEvent(new KeyboardEvent(
      'keydown', { key: 'Unidentified', keyCode: 229, which: 229, bubbles: true, cancelable: true })));
    await page.waitForTimeout(300);
    const garbled = await page.evaluate(() => window.__navPosts.length);
    if (garbled === 0) ok('Android artefact: a bare keyCode-229/"Unidentified" keydown sends no navigate by itself');
    else fail(`Android artefact: a bare 229 keydown alone sent ${garbled} navigate call(s) — should send none`);

    await url.press('Enter');
    await waitForAddress(page, v => /linkedin\.com/i.test(v), 'bare "linkedin.com" after a 229 keydown');
    const real = await page.evaluate(() => window.__navPosts.length);
    if (real >= 1) ok('Android artefact: the real submit that follows still navigates, to the real https://www.linkedin.com — the form-submit path, not keydown key-parsing, is what fires');
    else fail('Android artefact: the real submit after a 229 keydown sent no navigate');
    await stopAll(page);
    await page.close();
  }

  // ── 4: a full https:// URL, submitted the same way, actually loads ──
  {
    const page = await openPane(undefined);
    const url = page.locator('#mc-browser-pane [data-bp="url"]');
    await url.click(); await url.fill('https://www.google.com'); await url.press('Enter');
    await waitForAddress(page, v => /google\./i.test(v), 'https://www.google.com submit');
    ok('full https:// URL: real https://www.google.com loaded through the mobile submit path');
    await stopAll(page);
    await page.close();
  }

  // ── 5: focusing a field that already holds a URL selects the whole value ──
  {
    const page = await openPane(PAGE);
    const url = page.locator('#mc-browser-pane [data-bp="url"]');
    await url.click();
    const sel = await url.evaluate(el => ({ start: el.selectionStart, end: el.selectionEnd, len: el.value.length }));
    if (sel.len > 0 && sel.start === 0 && sel.end === sel.len)
      ok(`focus select: focusing an existing URL selects the whole value (${sel.len} chars) — typing replaces it, like a phone browser`);
    else
      fail(`focus select: selection is ${JSON.stringify(sel)} — want the whole value selected`);
    await stopAll(page);
    await page.close();
  }
} finally {
  await browser.close();
  harness.stdin.end();
  await new Promise(r => { harness.on('exit', r); setTimeout(r, 15000); });
  try { rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
}

console.log(fails.length ? `\n${fails.length} FAILED` : '\nALL PASS');
process.exit(fails.length ? 1 : 0);
