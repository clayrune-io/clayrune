// Browser pane on a phone (Ron, 2026-10-02, real Android phone over the tunnel):
//
//  1. MOBILE SITE BY DEFAULT. A session an agent launched at 1280x800 and the
//     phone then flipped to mobile kept the DESKTOP html it had already loaded
//     (Google: laid out 980px, drawn at 0.42). Switching device mode swaps the
//     UA but never re-asks the site, so entering mobile on a live desktop session
//     must RELOAD. A pane opened FROM the phone is mobile before its first
//     navigation (the server sees ONE request, a mobile one).
//  2. "DESKTOP SITE" in the pane's mobile menu, like Chrome's: swaps the UA,
//     reloads, and is remembered on the SESSION (survives the pane closing).
//  3. PINCH TO ZOOM is a real page zoom (Emulation.setPageScaleFactor), anchored
//     at the fingers, and a tap afterwards still lands on what is under the finger.
//  4. TYPING LATENCY: keystrokes that pile up behind an in-flight POST are merged
//     into one request (order preserved) instead of one round trip each.
//
// All of it runs against the REAL browser_routes blueprint + real headless
// Chromium (tools/smoke/browser_pane_harness.py, which serves /ua.html by
// User-Agent like Google does). Not a real phone: the pane runs in a Playwright
// Chromium with an Android UA, touch and a 412px viewport.
import { chromium } from 'playwright';
import { spawn } from 'child_process';
import { mkdtempSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import net from 'net';

const ANDROID_UA = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36';
const browser = await chromium.launch();
const fails = [];
const fail = m => { fails.push(m); console.log(`❌ FAIL — ${m}`); };
const ok = m => console.log(`✅ ${m}`);

const freePort = () => new Promise((res, rej) => {
  const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); s.on('error', rej);
});

async function startHarness() {
  const REPO = new URL('../../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
  const tmp = mkdtempSync(join(tmpdir(), 'bp-mobile-site-'));
  const port = await freePort();
  const harness = spawn(process.env.PYTHON || 'python',
    [join(REPO, 'tools/smoke/browser_pane_harness.py'), String(port), tmp],
    { cwd: REPO, stdio: ['pipe', 'pipe', 'pipe'] });
  let out = '';
  harness.stderr.on('data', d => { out += d; });
  const pagePort = await new Promise((res, rej) => {
    const t = setTimeout(() => rej(new Error('harness did not start:\n' + out)), 30000);
    harness.stdout.on('data', d => { out += d; const m = /READY \d+ (\d+)/.exec(out); if (m) { clearTimeout(t); res(Number(m[1])); } });
    harness.on('exit', c => rej(new Error(`harness exited ${c}:\n${out}`)));
  });
  const stop = () => {
    try { harness.stdin.end(); } catch (e) {}
    setTimeout(() => { try { harness.kill(); } catch (e) {} rmSync(tmp, { recursive: true, force: true }); }, 2000).unref();
  };
  return { BASE: `http://127.0.0.1:${port}`, pagePort, stop };
}

const { BASE, pagePort, stop } = await startHarness();
const PAGES = `http://127.0.0.1:${pagePort}`;

async function phone() {
  const context = await browser.newContext({ userAgent: ANDROID_UA, viewport: { width: 412, height: 915 }, hasTouch: true });
  const page = await context.newPage();
  page.on('pageerror', e => fail(`page error: ${e.message}`));
  await page.goto(BASE + '/');
  await page.waitForFunction(() => typeof window.openBrowserPane === 'function');
  const api = (path, body) => page.evaluate(([p, b]) => fetch(p, b ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) } : undefined).then(r => r.json()), [path, body]);
  return { context, page, api };
}

async function waitFor(fn, what, tries = 100, ms = 150) {
  for (let i = 0; i < tries; i++) { const v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, ms)); }
  throw new Error('timed out waiting for ' + what);
}
const pageText = async (api, sid) => JSON.stringify(await api('/api/browser/read', { session_id: sid, selector: 'body' }));
const hits = api => api('/_harness/uahits').then(d => d.hits);
const siteIs = (api, sid, word) => waitFor(async () => (await pageText(api, sid)).includes(word), `page to say ${word}`);

// ── 1b. a pane opened from the phone is mobile BEFORE its first navigation ──
async function testOpenedFromPhone() {
  const { context, page, api } = await phone();
  try {
    const before = (await hits(api)).length;
    await page.evaluate(u => window.openBrowserPane(u, 'smoke'), `${PAGES}/ua.html`);
    const sid = await waitFor(async () => ((await api('/api/project/smoke/browser/status')).sessions || [])[0]?.session_id, 'a session');
    await siteIs(api, sid, 'MOBILE-SITE');
    await page.waitForTimeout(1500);                     // a late reload would show up as a second hit
    const mine = (await hits(api)).slice(before);
    if (mine.length !== 1 || mine[0] !== 'mobile') fail(`a pane opened from the phone must load the site once, as mobile; the site saw ${JSON.stringify(mine)}`);
    else ok('opened from the phone: the site is asked ONCE, as a phone (no desktop load first)');
    await api('/api/browser/stop', { session_id: sid });
  } finally { await context.close(); }
}

// ── 1a. a desktop-launched live session flipped to mobile by the phone ──
async function testAgentLaunchedSessionReloadsAsMobile() {
  const { context, page, api } = await phone();
  try {
    const before = (await hits(api)).length;
    const launched = await api('/api/browser/launch', { project_id: 'smoke2', url: `${PAGES}/ua.html`, w: 1280, h: 800 });
    const sid = launched.session_id;
    await siteIs(api, sid, 'DESKTOP-SITE');
    await page.evaluate(([u, s]) => window.openBrowserPane(u, 'smoke2', s), [`${PAGES}/ua.html`, sid]);
    await siteIs(api, sid, 'MOBILE-SITE');
    const mine = (await hits(api)).slice(before);
    if (mine[0] !== 'desktop' || mine[mine.length - 1] !== 'mobile') fail(`expected a desktop load then a mobile reload, the site saw ${JSON.stringify(mine)}`);
    else ok(`an agent-launched desktop session reloads under the mobile UA when the phone attaches (site saw ${mine.join(' -> ')})`);
    await page.waitForTimeout(1200);
    const after = (await hits(api)).slice(before).length;
    if (after !== mine.length) fail(`attaching reloaded the page again and again (${mine.length} -> ${after} loads)`);
    await api('/api/browser/stop', { session_id: sid });
  } finally { await context.close(); }
}

// ── 2. the Desktop site switch ──
async function testDesktopSiteToggle() {
  const { context, page, api } = await phone();
  try {
    await page.evaluate(u => window.openBrowserPane(u, 'smoke3'), `${PAGES}/ua.html`);
    const sid = await waitFor(async () => ((await api('/api/project/smoke3/browser/status')).sessions || [])[0]?.session_id, 'a session');
    await siteIs(api, sid, 'MOBILE-SITE');
    const item = page.locator('#mc-browser-pane [data-bp="mm-desktop"]');
    await page.locator('#mc-browser-pane [data-bp="menu"]').tap();
    if ((await item.getAttribute('aria-checked')) !== 'false') fail('Desktop site starts checked on a fresh phone session');
    await item.tap();
    await siteIs(api, sid, 'DESKTOP-SITE');
    const st = await api('/_harness/session/' + sid);
    if (!st.desktop_site) fail('the Desktop site choice was not kept on the session');
    else ok('Desktop site: menu item reloads the page under the desktop UA and the session remembers it');
    // Persist per SESSION: drop the pane view, attach again.
    await page.locator('#mc-browser-pane [data-bp="hide"]').tap();   // 'back to chat': the browser keeps running
    await page.waitForTimeout(400);
    await page.evaluate(([u, s]) => window.openBrowserPane(u, 'smoke3', s), [`${PAGES}/ua.html`, sid]);
    await page.locator('#mc-browser-pane [data-bp="menu"]').waitFor();
    await page.locator('#mc-browser-pane [data-bp="menu"]').tap();
    const checked = await page.locator('#mc-browser-pane [data-bp="mm-desktop"]').getAttribute('aria-checked');
    if (checked !== 'true') fail(`reopening the pane lost the Desktop site choice (aria-checked=${checked})`);
    else if (!(await pageText(api, sid)).includes('DESKTOP-SITE')) fail('reattaching flipped the page back to mobile although Desktop site is on');
    else ok('Desktop site persists with the session: reattached pane shows it checked and the page stays desktop');
    await page.locator('#mc-browser-pane [data-bp="mm-desktop"]').tap();
    await siteIs(api, sid, 'MOBILE-SITE');
    ok('Desktop site off: back to the mobile site');
    await api('/api/browser/stop', { session_id: sid });
  } finally { await context.close(); }
}

// ── 3. pinch to zoom ──
async function testPinchZoom() {
  const { context, page, api } = await phone();
  const cdp = await context.newCDPSession(page);
  try {
    await page.evaluate(u => window.openBrowserPane(u, 'smoke4'), `${PAGES}/scale.html`);   // no viewport meta: drawn at ~0.42
    const sid = await waitFor(async () => ((await api('/api/project/smoke4/browser/status')).sessions || [])[0]?.session_id, 'a session');
    let st = await waitFor(async () => { const s = await api('/_harness/session/' + sid); return s.page_scale && s.page_scale < 0.6 && s.frame[0] ? s : null; }, 'a zoomed-out frame');
    const s0 = st.page_scale;
    const r = await page.evaluate(() => { const b = document.querySelector('#mc-browser-pane [data-bp="screen"]').getBoundingClientRect(); return { l: b.left, t: b.top, w: b.width, h: b.height }; });
    // Pinch ABOUT the textarea (layout 650,350): the point under the fingers must
    // stay under them, so it is still on screen afterwards for the tap check.
    const toScreen = (px, py, fw, fh) => { const sc = Math.min(r.w / fw, r.h / fh); return [r.l + (r.w - fw * sc) / 2 + px * sc, r.t + (r.h - fh * sc) / 2 + py * sc]; };
    const [cx, cy] = toScreen(650 * s0, 350 * s0, st.frame[0], st.frame[1]);
    const touches = d => [{ x: cx - d, y: cy, id: 1 }, { x: cx + d, y: cy, id: 2 }];
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: touches(15) });
    for (const d of [24, 32, 40, 48, 56, 60]) {
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: touches(d) });
      await page.waitForTimeout(90);
    }
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    st = await waitFor(async () => { const s = await api('/_harness/session/' + sid); return s.page_scale > s0 * 1.5 ? s : null; }, 'the page scale to grow');
    ok(`pinch out zooms the page itself: pageScaleFactor ${s0.toFixed(2)} -> ${st.page_scale.toFixed(2)}`);
    // Taps stay accurate on the zoomed page: put the picture point of the textarea's
    // centre (layout 650,350) under the finger and type.
    await page.waitForTimeout(600);
    st = await api('/_harness/session/' + sid);
    // Where the page says its visual viewport is (scale.html prints it): the
    // screencast metadata only carries the LAYOUT scroll, which stays 0 here.
    const vvLine = (await pageText(api, sid)).match(/vv:([-\d.,]+)/)[1].split(',').map(Number);
    const [fw, fh] = st.frame, [ox, oy] = vvLine, ps = st.page_scale;
    const px = (650 - ox) * ps, py = (350 - oy) * ps;
    if (px < 0 || px > fw || py < 0 || py > fh) { fail(`pinching about the textarea lost it: it is at picture ${px.toFixed(0)},${py.toFixed(0)} of ${fw}x${fh} (scroll ${ox},${oy}, scale ${ps})`); }
    else {
      const sc = Math.min(r.w / fw, r.h / fh);
      const tx = r.l + (r.w - fw * sc) / 2 + px * sc, ty = r.t + (r.h - fh * sc) / 2 + py * sc;
      await page.touchscreen.tap(tx, ty);
      await page.waitForTimeout(400);
      await api('/api/browser/input', { session_id: sid, type: 'text', text: 'zoomed' });
      const typed = await waitFor(async () => (await pageText(api, sid)).includes('typed:zoomed'), 'typing after zoom', 40, 200).catch(() => false);
      if (!typed) fail(`after zooming to ${ps.toFixed(2)} a tap on the textarea (picture ${px.toFixed(0)},${py.toFixed(0)}) did not focus it`);
      else ok('after the zoom a tap still lands on what is under the finger');
    }
    // Pinch back in.
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: touches(60) });
    for (const d of [50, 40, 30, 22, 16, 14]) {
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: touches(d) });
      await page.waitForTimeout(90);
    }
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    st = await waitFor(async () => { const s = await api('/_harness/session/' + sid); return s.page_scale < ps * 0.7 ? s : null; }, 'the page scale to shrink');
    ok(`pinch in zooms back out: ${ps.toFixed(2)} -> ${st.page_scale.toFixed(2)}`);
    await api('/api/browser/stop', { session_id: sid });
  } finally { await context.close(); }
}

// ── 4. typing latency: queued keystrokes merge, in order ──
async function testTypingCoalescing() {
  const { context, page } = await phone();
  try {
    const r = await page.evaluate(() => {
      const m = window._bpMergeTextBodies, S = { session_id: 's' };
      return {
        text: m({ ...S, type: 'text', text: 'ab' }, { ...S, type: 'text', text: 'cd' }),
        edit: m({ ...S, type: 'edit', delete: 1, text: 'x' }, { ...S, type: 'edit', delete: 0, text: 'yz' }),
        eatTail: m({ ...S, type: 'edit', delete: 0, text: 'abc' }, { ...S, type: 'edit', delete: 2, text: 'Q' }),
        eatPast: m({ ...S, type: 'edit', delete: 1, text: 'a' }, { ...S, type: 'edit', delete: 3, text: '' }),
        emoji: m({ ...S, type: 'edit', delete: 0, text: 'a\u{1F600}' }, { ...S, type: 'edit', delete: 1, text: 'b' }),
        crossKinds: m({ ...S, type: 'text', text: 'a' }, { ...S, type: 'edit', delete: 0, text: 'b' }),
        otherSession: m({ ...S, type: 'text', text: 'a' }, { session_id: 't', type: 'text', text: 'b' }),
        zoom: m({ ...S, type: 'zoom', scale: 1.5 }, { ...S, type: 'zoom', scale: 2 }),
      };
    });
    const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
    if (r.text?.text !== 'abcd') fail(`text+text should concatenate, got ${JSON.stringify(r.text)}`);
    else if (!eq([r.edit?.delete, r.edit?.text], [1, 'xyz'])) fail(`edit+edit: ${JSON.stringify(r.edit)}`);
    else if (!eq([r.eatTail?.delete, r.eatTail?.text], [0, 'aQ'])) fail(`b's Backspaces must eat a's tail first: ${JSON.stringify(r.eatTail)}`);
    else if (!eq([r.eatPast?.delete, r.eatPast?.text], [3, ''])) fail(`b's Backspaces past a's text must reach the page: ${JSON.stringify(r.eatPast)}`);
    else if (!eq([r.emoji?.delete, r.emoji?.text], [0, 'ab'])) fail(`an emoji is ONE Backspace: ${JSON.stringify(r.emoji)}`);
    else if (r.crossKinds !== null || r.otherSession !== null) fail('text and edit, or two sessions, must never merge');
    else if (r.zoom?.scale !== 2) fail(`zoom+zoom should keep the newest target: ${JSON.stringify(r.zoom)}`);
    else ok('merge rules: text+text, edit+edit (Backspaces eat the earlier tail, emoji = 1), no cross-kind/session merge, zoom keeps the newest');
  } finally { await context.close(); }
}

// Over a slow link a burst of keystrokes must cost a few POSTs, not one per
// character, and arrive in order.
async function testTypingBurstOverSlowLink() {
  const { context, page, api } = await phone();
  try {
    await page.evaluate(u => window.openBrowserPane(u, 'smoke5'), `${PAGES}/type.html`);
    const sid = await waitFor(async () => ((await api('/api/project/smoke5/browser/status')).sessions || [])[0]?.session_id, 'a session');
    await waitFor(async () => (await api('/_harness/session/' + sid)).frame[0], 'a frame');
    await page.waitForTimeout(500);
    const posts = [];
    await page.route('**/api/browser/input', async route => {
      posts.push(JSON.parse(route.request().postData() || '{}'));
      await new Promise(r => setTimeout(r, 200));                    // a ~200ms round trip
      await route.continue();
    });
    const cdp = await context.newCDPSession(page);
    await page.locator('#mc-browser-pane [data-bp="screen"]').tap();
    await page.waitForTimeout(300);
    const word = 'abcdefghijkl';
    for (const ch of word) {
      await cdp.send('Input.insertText', { text: ch });
      await page.waitForTimeout(25);
    }
    await page.waitForTimeout(1500);
    const typed = posts.filter(p => p.type === 'edit' || p.type === 'text');
    let s = [];
    for (const p of typed) {
      if (p.type === 'edit') { s = s.slice(0, Math.max(0, s.length - (p.delete || 0))); s.push(...Array.from(p.text || '')); } else s.push(...Array.from(p.text));
    }
    if (s.join('') !== word) fail(`the merged requests must replay to "${word}" in order, got "${s.join('')}" over ${typed.length} POSTs`);
    else if (typed.length > word.length / 2) fail(`${word.length} keystrokes at 200ms RTT still cost ${typed.length} POSTs; queued keystrokes are not merging`);
    else ok(`${word.length} keystrokes over a 200ms link: ${typed.length} POSTs, replayed in order`);
    await api('/api/browser/stop', { session_id: sid });
  } finally { await context.close(); }
}

try {
  await testOpenedFromPhone();
  await testAgentLaunchedSessionReloadsAsMobile();
  await testDesktopSiteToggle();
  await testPinchZoom();
  await testTypingCoalescing();
  await testTypingBurstOverSlowLink();
} catch (e) { fail(String(e && e.stack || e)); }
finally { await browser.close(); stop(); }

if (!fails.length) console.log('✅ PASS — mobile site by default, Desktop site toggle (persisted per session), pinch zoom and merged typing verified against the real backend.');
else process.exitCode = 1;
