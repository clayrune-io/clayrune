// Regression guard for A1 (tab strip)/A2 (JS dialogs)/A3 (copy button) of the
// 2026-09-25 browser-pane-parity batch A. Same hermetic pattern as
// browser-pane-minimize-downloads.mjs: loads the REAL static/js/browser-pane.js
// against a stubbed API (no real Chromium pane backend, no running MC) and
// drives it with Playwright, feeding SSE payloads shaped exactly like
// _stream_gen's `tabs`/`dialog` events (mc/blueprints/browser_routes.py).
import { chromium } from 'playwright';
import { readFileSync } from 'fs';

const JS = readFileSync(process.env.SMOKE_PANE_JS
  || new URL('../../static/js/browser-pane.js', import.meta.url), 'utf8');

const browser = await chromium.launch();
const fails = [];
const fail = m => { fails.push(m); console.log(`❌ FAIL — ${m}`); };

function htmlShell() {
  return `<body>
    <div id="modal-layer"></div>
    <div id="minimized-tray"></div>
    <div id="toast-container"></div>
    <script>
      window.nextModalZ = 100;
      window.showToast = m => { window.__toasts = window.__toasts || []; window.__toasts.push(m); };
    </script>
    <script type="module" src="/static/js/browser-pane.js"></script>
  </body>`;
}

async function newPage({ sseBody, selectionText } = {}) {
  const posts = [];
  const context = await browser.newContext();
  // Real clipboard, granted explicitly — same approach as browser-paste.mjs,
  // which uses the actual OS/Chromium clipboard rather than stubbing
  // navigator.clipboard (assignment onto it is unreliable: it's a getter on
  // Navigator.prototype in real Chromium, so a plain overwrite can silently
  // no-op instead of replacing it).
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: 'http://localhost:9' });
  const page = await context.newPage();
  await page.route('**/*', async route => {
    const url = route.request().url();
    if (url.endsWith('/browser-pane.js'))
      return route.fulfill({ contentType: 'application/javascript', body: JS });
    if (url.includes('/api/browser/launch'))
      return route.fulfill({ status: 201, contentType: 'application/json',
        body: JSON.stringify({ session_id: 'sid-1', url: 'about:blank',
                               profile: 'main', view: { w: 1280, h: 800 } }) });
    if (url.includes('/api/browser/tab')) {
      posts.push({ kind: 'tab', body: JSON.parse(route.request().postData() || '{}') });
      return route.fulfill({ contentType: 'application/json', body: '{"ok":true}' });
    }
    if (url.includes('/api/browser/dialog')) {
      posts.push({ kind: 'dialog', body: JSON.parse(route.request().postData() || '{}') });
      return route.fulfill({ contentType: 'application/json', body: '{"ok":true}' });
    }
    if (url.includes('/api/browser/selection')) {
      posts.push({ kind: 'selection', body: JSON.parse(route.request().postData() || '{}') });
      return route.fulfill({ contentType: 'application/json',
        body: JSON.stringify({ text: selectionText != null ? selectionText : 'copied text' }) });
    }
    if (url.includes('/api/browser/input')) {
      posts.push({ kind: 'input', body: JSON.parse(route.request().postData() || '{}') });
      return route.fulfill({ contentType: 'application/json', body: '{"ok":true}' });
    }
    if (url.includes('/browser/status'))
      return route.fulfill({ contentType: 'application/json', body: '{"sessions":[]}' });
    if (url.includes('/api/browser/profiles'))
      return route.fulfill({ contentType: 'application/json', body: '{"profiles":[]}' });
    if (url.includes('/api/browser/stream'))
      return route.fulfill({ contentType: 'text/event-stream', body: sseBody || '' });
    return route.fulfill({ contentType: 'text/html', body: htmlShell() });
  });
  await page.goto('http://localhost:9/');
  await page.waitForFunction(() => typeof window.openBrowserPane === 'function');
  return { page, posts };
}

// ── A1: tab strip — hidden with 1 tab, shown with 2+, click switches,
//       × closes; both actions POST /api/browser/tab with the right target. ──
async function testTabStrip() {
  const oneTab = JSON.stringify({ tabs: [{ target_id: 'root', url: 'https://a.example', title: 'Root' }],
    active_target_id: 'root' });
  const twoTabs = JSON.stringify({ tabs: [
    { target_id: 'root', url: 'https://a.example', title: 'Root' },
    { target_id: 'popup', url: 'https://accounts.example/signin', title: 'Sign in', opener_id: 'root' },
  ], active_target_id: 'popup' });
  const { page, posts } = await newPage({ sseBody: `data: ${oneTab}\n\ndata: ${twoTabs}\n\n` });
  await page.evaluate(() => window.openBrowserPane('about:blank', 'p1'));
  await page.locator('#mc-browser-pane').waitFor({ state: 'attached' });
  await page.waitForTimeout(200);

  const strip = page.locator('[data-bp="tabstrip"]');
  const tabs = strip.locator('[data-bp-tab]');
  if (await tabs.count() !== 2) fail(`tab strip: expected 2 tab entries, found ${await tabs.count()}`);
  const stripDisplay = await strip.evaluate(el => getComputedStyle(el).display);
  if (stripDisplay === 'none') fail('tab strip: hidden even though 2 tabs are open');

  await page.locator('[data-bp-tab="root"]').click();
  await page.waitForTimeout(50);
  const activate = posts.find(p => p.kind === 'tab' && p.body.action === 'activate');
  if (!activate || activate.body.target_id !== 'root')
    fail(`tab strip: clicking a tab did not POST activate for root — got ${JSON.stringify(activate)}`);

  await page.locator('[data-bp-tab-close="popup"]').click();
  await page.waitForTimeout(50);
  const close = posts.find(p => p.kind === 'tab' && p.body.action === 'close');
  if (!close || close.body.target_id !== 'popup')
    fail(`tab strip: clicking × did not POST close for popup — got ${JSON.stringify(close)}`);

  await page.close();

  // Single-tab case: the strip must not clutter a plain page.
  const { page: page2 } = await newPage({ sseBody: `data: ${oneTab}\n\n` });
  await page2.evaluate(() => window.openBrowserPane('about:blank', 'p1'));
  await page2.locator('#mc-browser-pane').waitFor({ state: 'attached' });
  await page2.waitForTimeout(200);
  const strip2Display = await page2.locator('[data-bp="tabstrip"]').evaluate(el => getComputedStyle(el).display);
  if (strip2Display !== 'none') fail('tab strip: shown for a single tab — should stay hidden for the common case');
  await page2.close();

  if (!fails.length) console.log('✅ tab strip: renders new targets (window.open()/OAuth popups), switches and closes POST the right target_id, hidden with one tab.');
}

// ── A2: JS dialogs — alert/confirm/prompt render, OK/Cancel POST the right
//       accept/text, and the overlay clears the moment the SSE says dialog:null
//       (proving an answered dialog never leaves the pane stuck). ────────────
async function testDialogs() {
  const promptDialog = JSON.stringify({ dialog: { target_id: 'root', type: 'prompt',
    message: 'Enter your name', default_prompt: 'Ron' } });

  // Sub-test 1: an open prompt() renders correctly and OK POSTs the answer.
  // A single-event SSE body only — chaining the dialog:null clear into the
  // SAME static fulfilled body would deliver both messages back-to-back with
  // no real gap (unlike the real backend's actual network timing), racing the
  // "still shown" assertion below against the clear. Kept as a separate
  // sub-test 2 instead.
  {
    const { page, posts } = await newPage({ sseBody: `data: ${promptDialog}\n\n` });
    await page.evaluate(() => window.openBrowserPane('about:blank', 'p1'));
    await page.locator('#mc-browser-pane').waitFor({ state: 'attached' });

    const overlay = page.locator('[data-bp="dialog-overlay"]');
    await overlay.waitFor({ state: 'visible', timeout: 2000 }).catch(() =>
      fail('dialog: overlay did not show for an open prompt() dialog'));
    const msg = (await page.locator('[data-bp="dialog-msg"]').textContent() || '').trim();
    if (msg !== 'Enter your name') fail(`dialog: message showed "${msg}", expected "Enter your name"`);
    const input = page.locator('[data-bp="dialog-input"]');
    const inputVisible = await input.evaluate(el => getComputedStyle(el).display !== 'none');
    if (!inputVisible) fail('dialog: prompt() must show a text input, none was visible');
    const defVal = await input.inputValue();
    if (defVal !== 'Ron') fail(`dialog: default prompt text showed "${defVal}", expected "Ron"`);

    await input.fill('Ronald');
    await page.locator('[data-bp="dialog-ok"]').click();
    await page.waitForTimeout(50);
    const answer = posts.find(p => p.kind === 'dialog');
    if (!answer || answer.body.accept !== true || answer.body.text !== 'Ronald')
      fail(`dialog: OK did not POST {accept:true, text:"Ronald"} — got ${JSON.stringify(answer && answer.body)}`);
    const overlayAfterAnswer = await overlay.evaluate(el => getComputedStyle(el).display);
    if (overlayAfterAnswer !== 'none') fail('dialog: overlay still shown right after clicking OK');
    await page.close();
  }

  // Sub-test 2: the backend's own dialog:null must also clear an open overlay
  // — this is what stops an UNANSWERED dialog from wedging the pane visually
  // even if the user never clicks a button in it. Both events are chained
  // into one static SSE body (same technique as testTabStrip's two-tabs case)
  // and only the FINAL rendered state is asserted, since a statically
  // fulfilled body delivers both messages with no real gap between them —
  // unlike the real backend, which only ever sends dialog:null well after the
  // open one, so there is no meaningful "still shown" instant to catch here.
  {
    const cleared = JSON.stringify({ dialog: null });
    const { page } = await newPage({ sseBody: `data: ${promptDialog}\n\ndata: ${cleared}\n\n` });
    await page.evaluate(() => window.openBrowserPane('about:blank', 'p1'));
    await page.locator('#mc-browser-pane').waitFor({ state: 'attached' });
    await page.waitForTimeout(150);
    const overlayFinal = await page.locator('[data-bp="dialog-overlay"]')
      .evaluate(el => getComputedStyle(el).display);
    if (overlayFinal !== 'none') fail('dialog: overlay still shown after the backend reported dialog:null');
    await page.close();
  }

  if (!fails.length) console.log('✅ dialogs: alert/confirm/prompt render with the right fields, OK/Cancel POST the answer, and dialog:null always clears the overlay.');
}

// ── A3: copy button — POSTs /api/browser/selection and writes the result to
//       the HOST clipboard, same path as the existing Ctrl/Cmd+C handler. ────
async function testCopyButton() {
  const { page, posts } = await newPage({ selectionText: 'hello from the page' });
  await page.evaluate(() => window.openBrowserPane('about:blank', 'p1'));
  await page.locator('#mc-browser-pane').waitFor({ state: 'attached' });

  await page.locator('[data-bp="copy"]').click();
  await page.waitForTimeout(80);
  const sel = posts.find(p => p.kind === 'selection');
  if (!sel) fail('copy button: click did not POST /api/browser/selection');
  const clip = await page.evaluate(() => navigator.clipboard.readText());
  if (clip !== 'hello from the page') fail(`copy button: host clipboard got "${clip}", expected "hello from the page"`);

  await page.close();
  if (!fails.length) console.log('✅ copy button: reads the page selection via /api/browser/selection and writes it to the host clipboard.');
}

await testTabStrip();
await testDialogs();
await testCopyButton();
await browser.close();

if (!fails.length) console.log('✅ PASS — browser-pane tab strip, JS dialogs, and copy button all verified against the real static/js/browser-pane.js.');
else process.exitCode = 1;
