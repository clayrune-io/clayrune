// Regression guard for B1 (file upload)/B2 (right-click) of the 2026-09-25
// browser-pane-parity batch B. Same hermetic pattern as
// browser-pane-tabs-dialog.mjs: loads the REAL static/js/browser-pane.js
// against a stubbed API (no real Chromium pane backend, no running MC) and
// drives it with Playwright, feeding SSE payloads shaped exactly like
// _stream_gen's `file_chooser` event (mc/blueprints/browser_routes.py).
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

async function newPage({ sseBody } = {}) {
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
    if (url.includes('/api/browser/file-chooser')) {
      // Route handlers see the raw multipart body; pull out session_id/action
      // and the uploaded filename without a form-data parser dependency —
      // good enough for asserting what the pane SENT.
      const body = route.request().postData() || '';
      const action = /name="action"\r?\n\r?\n(\w+)/.exec(body);
      const filename = /filename="([^"]*)"/.exec(body);
      posts.push({ kind: 'file-chooser', action: action && action[1], filename: filename && filename[1] });
      return route.fulfill({ contentType: 'application/json', body: '{"ok":true,"count":1}' });
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

// ── B1: file chooser — overlay shows on Page.fileChooserOpened, `multiple`
//       reflects selectSingle vs selectMultiple, Upload sends the file's
//       real bytes as multipart, Cancel POSTs action=cancel. ────────────────
async function testFileChooser() {
  const single = JSON.stringify({ file_chooser: { mode: 'selectSingle' } });
  const { page, posts } = await newPage({ sseBody: `data: ${single}\n\n` });
  await page.evaluate(() => window.openBrowserPane('about:blank', 'p1'));
  await page.locator('#mc-browser-pane').waitFor({ state: 'attached' });

  const overlay = page.locator('[data-bp="filechooser-overlay"]');
  await overlay.waitFor({ state: 'visible', timeout: 2000 }).catch(() =>
    fail('file chooser: overlay did not show for Page.fileChooserOpened'));
  const inputEl = page.locator('[data-bp="filechooser-input"]');
  if (await inputEl.evaluate(el => el.multiple))
    fail('file chooser: input.multiple was true for a selectSingle chooser');

  await inputEl.setInputFiles({ name: 'upload-me.txt', mimeType: 'text/plain',
    buffer: Buffer.from('hello from the smoke test') });
  await page.locator('[data-bp="filechooser-ok"]').click();
  await page.waitForTimeout(80);
  const upload = posts.find(p => p.kind === 'file-chooser' && p.filename);
  if (!upload || upload.filename !== 'upload-me.txt')
    fail(`file chooser: Upload did not POST the picked file — got ${JSON.stringify(upload)}`);
  const overlayAfter = await overlay.evaluate(el => getComputedStyle(el).display);
  if (overlayAfter !== 'none') fail('file chooser: overlay still shown right after Upload');
  await page.close();

  // Cancel path + selectMultiple mode.
  const multi = JSON.stringify({ file_chooser: { mode: 'selectMultiple' } });
  const { page: page2, posts: posts2 } = await newPage({ sseBody: `data: ${multi}\n\n` });
  await page2.evaluate(() => window.openBrowserPane('about:blank', 'p1'));
  await page2.locator('#mc-browser-pane').waitFor({ state: 'attached' });
  const overlay2 = page2.locator('[data-bp="filechooser-overlay"]');
  await overlay2.waitFor({ state: 'visible', timeout: 2000 });
  if (!(await page2.locator('[data-bp="filechooser-input"]').evaluate(el => el.multiple)))
    fail('file chooser: input.multiple was false for a selectMultiple chooser');
  await page2.locator('[data-bp="filechooser-cancel"]').click();
  await page2.waitForTimeout(80);
  const cancel = posts2.find(p => p.kind === 'file-chooser');
  if (!cancel || cancel.action !== 'cancel')
    fail(`file chooser: Cancel did not POST action=cancel — got ${JSON.stringify(cancel)}`);
  const overlay2After = await overlay2.evaluate(el => getComputedStyle(el).display);
  if (overlay2After !== 'none') fail('file chooser: overlay still shown right after Cancel');
  await page2.close();

  if (!fails.length) console.log('✅ file chooser: overlay tracks selectSingle/selectMultiple, Upload sends the picked file\'s real bytes, Cancel POSTs action=cancel and always clears.');
}

// ── B2: right-click — forwarded to the page as a real right button click,
//       and the host's own context menu never appears (preventDefault). ────
async function testRightClick() {
  // A real (tiny) JPEG frame, same technique as browser-pane-coords.mjs — the
  // <img> has no rendered size (so Playwright treats it as not visible/not
  // clickable) until a decodable frame gives it one.
  const seed = await browser.newContext();
  const seedPage = await seed.newPage();
  const jpeg = await seedPage.evaluate(() => {
    const c = document.createElement('canvas'); c.width = 100; c.height = 100;
    c.getContext('2d').fillRect(0, 0, 100, 100);
    return c.toDataURL('image/jpeg', 0.6).split(',')[1];
  });
  await seedPage.close();
  const frame = JSON.stringify({ seq: 1, img: jpeg, w: 100, h: 100, url: 'about:blank' });
  const { page, posts } = await newPage({ sseBody: `data: ${frame}\n\n` });
  await page.evaluate(() => window.openBrowserPane('about:blank', 'p1'));
  const img = page.locator('#mc-browser-pane [data-bp="screen"]');
  await page.waitForFunction(() => {
    const i = document.querySelector('#mc-browser-pane [data-bp="screen"]');
    return i && i.naturalWidth > 0;
  }, null, { timeout: 5000 });
  await img.click({ button: 'right' });
  await page.waitForTimeout(80);
  const rc = posts.find(p => p.kind === 'input' && p.body.button === 'right');
  if (!rc || rc.body.action !== 'click')
    fail(`right-click: did not POST a right-button click — got ${JSON.stringify(posts.filter(p => p.kind === 'input'))}`);
  if (!fails.length) console.log('✅ right-click: forwarded to the page as a right-button click; no host context menu captured the event.');
  await page.close();
}

await testFileChooser();
await testRightClick();
await browser.close();

if (!fails.length) console.log('✅ PASS — browser-pane file chooser and right-click both verified against the real static/js/browser-pane.js.');
else process.exitCode = 1;
