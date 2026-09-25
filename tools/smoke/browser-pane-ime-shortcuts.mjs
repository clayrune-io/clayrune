// Regression guard for B3 (gap #7) of the 2026-09-25 browser-pane-parity
// batch B: page-level shortcuts (Ctrl+F, Ctrl+A, …) forward to the PAGE
// instead of the host, and IME composition (CJK input) reaches the page via
// Input.imeSetComposition/insertText instead of being silently dropped.
//
// Same hermetic pattern as browser-pane-tabs-dialog.mjs: loads the REAL
// static/js/browser-pane.js against a stubbed API and drives it with
// Playwright — no real Chromium pane backend, no running MC.
import { chromium } from 'playwright';
import { readFileSync } from 'fs';

const JS = readFileSync(process.env.SMOKE_PANE_JS
  || new URL('../../static/js/browser-pane.js', import.meta.url), 'utf8');

const browser = await chromium.launch();
const fails = [];
const fail = m => { fails.push(m); console.log(`❌ FAIL — ${m}`); };

async function newPage() {
  const posts = [];
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.route('**/*', async route => {
    const url = route.request().url();
    if (url.endsWith('/browser-pane.js'))
      return route.fulfill({ contentType: 'application/javascript', body: JS });
    if (url.includes('/api/browser/launch'))
      return route.fulfill({ status: 201, contentType: 'application/json',
        body: JSON.stringify({ session_id: 'sid-1', url: 'about:blank',
                               profile: 'main', view: { w: 1280, h: 800 } }) });
    if (url.includes('/api/browser/input')) {
      posts.push(JSON.parse(route.request().postData() || '{}'));
      return route.fulfill({ contentType: 'application/json', body: '{"ok":true}' });
    }
    if (url.includes('/browser/status'))
      return route.fulfill({ contentType: 'application/json', body: '{"sessions":[]}' });
    if (url.includes('/api/browser/profiles'))
      return route.fulfill({ contentType: 'application/json', body: '{"profiles":[]}' });
    if (url.includes('/api/browser/stream'))
      return route.fulfill({ contentType: 'text/event-stream', body: ': ping\n\n' });
    return route.fulfill({ contentType: 'text/html', body:
      `<body><div id="modal-layer"></div><div id="minimized-tray"></div><div id="toast-container"></div>
       <script>window.nextModalZ=100;window.showToast=m=>window.__toast=m;</script>
       <script type="module" src="/static/js/browser-pane.js"></script></body>` });
  });
  await page.goto('http://localhost:9/');
  await page.waitForFunction(() => typeof window.openBrowserPane === 'function');
  await page.evaluate(() => window.openBrowserPane('about:blank', 'p1'));
  await page.locator('#mc-browser-pane [data-bp="ime-shadow"]').waitFor({ state: 'attached' });
  return { page, posts };
}

// ── page-level shortcuts forward to the page, not the host ─────────────────
async function testShortcutsForwardToPage() {
  const { page, posts } = await newPage();
  // Prove the HOST never sees a live shortcut too — a document-level listener
  // records whether Ctrl+F's keydown was ever left unprevented (which is what
  // let it reach the host's own browser chrome before this fix).
  await page.evaluate(() => {
    window.__hostSaw = null;
    document.addEventListener('keydown', e => {
      if (e.key === 'f' && e.ctrlKey) window.__hostSaw = e.defaultPrevented;
    });
  });
  await page.evaluate(() => document.querySelector('#mc-browser-pane [data-bp="ime-shadow"]').focus());
  await page.keyboard.press('Control+f');
  await page.waitForTimeout(80);

  const sent = posts.find(p => p.type === 'key' && p.key === 'Ctrl+f');
  if (!sent) fail(`shortcuts: Ctrl+F did not POST {type:key, key:"Ctrl+f"} — got ${JSON.stringify(posts)}`);
  const hostSaw = await page.evaluate(() => window.__hostSaw);
  if (hostSaw !== true) fail(`shortcuts: Ctrl+F reached the host with defaultPrevented=${hostSaw} — the host's own Find could still fire`);

  posts.length = 0;
  await page.keyboard.press('Control+a');
  await page.waitForTimeout(80);
  const selAll = posts.find(p => p.type === 'key' && p.key === 'Ctrl+a');
  if (!selAll) fail(`shortcuts: Ctrl+A did not POST {type:key, key:"Ctrl+a"} — got ${JSON.stringify(posts)}`);

  await page.close();
  if (!fails.length) console.log('✅ shortcuts: Ctrl+F/Ctrl+A forward to the page as key combos and never reach the host page unprevented.');
}

// ── IME composition reaches the page via the ime-shadow input ──────────────
async function testImeComposition() {
  const { page, posts } = await newPage();
  await page.evaluate(() => {
    const el = document.querySelector('#mc-browser-pane [data-bp="ime-shadow"]');
    el.focus();
    el.dispatchEvent(new CompositionEvent('compositionupdate', { data: 'n' }));
  });
  await page.waitForTimeout(50);
  const update = posts.find(p => p.type === 'ime' && p.phase === 'update' && p.text === 'n');
  if (!update) fail(`IME: compositionupdate did not POST {type:ime, phase:update, text:"n"} — got ${JSON.stringify(posts)}`);

  posts.length = 0;
  await page.evaluate(() => {
    const el = document.querySelector('#mc-browser-pane [data-bp="ime-shadow"]');
    el.dispatchEvent(new CompositionEvent('compositionend', { data: '你好' }));
  });
  await page.waitForTimeout(50);
  const end = posts.find(p => p.type === 'ime' && p.phase === 'end' && p.text === '你好');
  if (!end) fail(`IME: compositionend did not POST {type:ime, phase:end, text:"你好"} — got ${JSON.stringify(posts)}`);
  const valAfter = await page.locator('#mc-browser-pane [data-bp="ime-shadow"]').inputValue();
  if (valAfter !== '') fail(`IME: ime-shadow still held "${valAfter}" after commit — would leak into the next composition`);

  await page.close();
  if (!fails.length) console.log('✅ IME: compositionupdate/end forward as {type:ime}, and the shadow input clears after commit.');
}

await testShortcutsForwardToPage();
await testImeComposition();
await browser.close();

if (!fails.length) console.log('✅ PASS — browser-pane page-level shortcuts and IME composition both verified against the real static/js/browser-pane.js.');
else process.exitCode = 1;
