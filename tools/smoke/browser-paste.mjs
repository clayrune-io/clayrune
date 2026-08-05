// Regression guard for the browser pane's clipboard bridge.
//
// The pane used to preventDefault() the Ctrl+V keydown and then rely on
// navigator.clipboard.readText(). Cancelling that keydown also cancels the
// native `paste` event — the one clipboard path that needs no permission,
// survives a non-secure context (plain http over the LAN), and exists in every
// browser. So pasting into the pane failed with "Paste blocked …" anywhere
// readText() was unavailable or had been denied, which is most places.
//
// Loads the REAL static/js/browser-pane.js against a stub API, fires a genuine
// Ctrl+V, and asserts the clipboard text reaches /api/browser/input exactly
// once (twice would mean the API fallback double-fired).
import { chromium } from 'playwright';
import { readFileSync } from 'fs';

const JS = readFileSync(new URL('../../static/js/browser-pane.js', import.meta.url), 'utf8');
const SECRET = 'hunter2-from-clipboard';
const posts = [];

const browser = await chromium.launch();
const page = await (await browser.newContext()).newPage();

await page.route('**/*', async route => {
  const url = route.request().url();
  if (url.endsWith('/browser-pane.js'))
    return route.fulfill({ contentType: 'application/javascript', body: JS });
  if (url.includes('/api/browser/launch'))
    return route.fulfill({ status: 201, contentType: 'application/json',
      body: JSON.stringify({ session_id: 'sid-test', url: 'about:blank', view: { w: 1280, h: 800 } }) });
  if (url.includes('/api/browser/input')) {
    posts.push(JSON.parse(route.request().postData() || '{}'));
    return route.fulfill({ contentType: 'application/json', body: '{"ok":true}' });
  }
  if (url.includes('/browser/status'))
    return route.fulfill({ contentType: 'application/json', body: '{"sessions":[]}' });
  if (url.includes('/api/browser/stream'))
    return route.fulfill({ contentType: 'text/event-stream', body: ': ping\n\n' });
  return route.fulfill({ contentType: 'text/html', body:
    `<body><div id="modal-layer"></div><div id="toast-container"></div>
     <script>window.nextModalZ=100;window.showToast=m=>window.__toast=m;</script>
     <script type="module" src="/static/js/browser-pane.js"></script></body>` });
});

await page.goto('http://localhost:9/');
await page.waitForFunction(() => typeof window.openBrowserPane === 'function');
await page.evaluate(() => window.openBrowserPane('about:blank', 'p1'));
// `attached`, not `visible`: the <img> gets no frames from the stub stream, so
// it has zero size and Playwright would call it hidden.
await page.waitForSelector('#mc-browser-pane [data-bp="screen"]', { state: 'attached' });

// Seed the real clipboard, then focus the pane image and press Ctrl+V.
await page.evaluate(secret => {
  const ta = document.createElement('textarea');
  ta.value = secret; document.body.appendChild(ta);
  ta.select(); document.execCommand('copy'); ta.remove();
  document.querySelector('#mc-browser-pane [data-bp="screen"]').focus();
}, SECRET);
posts.length = 0;
await page.keyboard.press('Control+V');
await page.waitForTimeout(600);   // outlast the 200ms clipboard-API fallback

const pasted = posts.filter(p => p.type === 'text' && p.text === SECRET);
const toast = await page.evaluate(() => window.__toast);
const hasButton = await page.locator('#mc-browser-pane [data-bp="paste"]').count() === 1;
await browser.close();

const fail = m => { console.log(`❌ FAIL — ${m}`); process.exitCode = 1; };
if (pasted.length === 0) fail(`clipboard text never reached the page (toast: ${toast})`);
else if (pasted.length > 1) fail(`pasted ${pasted.length}x — the API fallback double-fired`);
// Touch has no Ctrl+V, so losing this button silently strands every phone user.
if (!hasButton) fail('the toolbar paste button is gone');
if (!process.exitCode) console.log('✅ browser pane paste: Ctrl+V delivered the clipboard text exactly once.');
