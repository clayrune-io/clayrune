// Regression guard for three browser-pane fixes (Ron, 2026-09-24: "the
// overall experience is not yet close to that of a real browser and feels
// crippled"): minimize/restore, the default-persistent-profile for
// human-opened panes, and the download progress/completion UI.
//
// Same hermetic pattern as browser-pane-coords.mjs: loads the REAL
// static/js/browser-pane.js against a stubbed API (no real Chromium pane
// backend, no running MC) and drives it with Playwright.
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

async function newPage({ launchProfile = 'main', sseBody } = {}) {
  const posts = [];
  const page = await (await browser.newContext()).newPage();
  await page.route('**/*', async route => {
    const url = route.request().url();
    if (url.endsWith('/browser-pane.js'))
      return route.fulfill({ contentType: 'application/javascript', body: JS });
    if (url.includes('/api/browser/launch')) {
      const body = JSON.parse(route.request().postData() || '{}');
      posts.push({ kind: 'launch', body });
      return route.fulfill({ status: 201, contentType: 'application/json',
        body: JSON.stringify({ session_id: 'sid-1', url: 'about:blank',
                               profile: launchProfile, view: { w: 1280, h: 800 } }) });
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

// ── 1. Minimize hides the pane, docks a chip, and pauses the screencast;
//       restore shows it again and resumes the screencast. ──────────────────
async function testMinimizeRestore() {
  const { page, posts } = await newPage();
  await page.evaluate(() => window.openBrowserPane('about:blank', 'p1'));
  const win = page.locator('#mc-browser-pane');
  await win.waitFor({ state: 'attached' });

  await page.locator('[data-bp="minimize"]').click();
  await page.waitForTimeout(100);
  const displayAfterMin = await win.evaluate(el => getComputedStyle(el).display);
  if (displayAfterMin !== 'none') fail(`minimize: pane still displayed (${displayAfterMin}), expected none`);
  const chip = page.locator('#minimized-tray .minimized-chip');
  if (await chip.count() !== 1) fail(`minimize: expected exactly 1 dock chip, found ${await chip.count()}`);
  const stopSent = posts.some(p => p.kind === 'input' && p.body.type === 'screencast' && p.body.action === 'stop');
  if (!stopSent) fail('minimize: no {type:screencast, action:stop} reached /api/browser/input — screencast still costs frames while hidden');

  await chip.click();
  await page.waitForTimeout(100);
  const displayAfterRestore = await win.evaluate(el => getComputedStyle(el).display);
  if (displayAfterRestore === 'none') fail('restore: pane still hidden after clicking its dock chip');
  if (await page.locator('#minimized-tray .minimized-chip').count() !== 0) fail('restore: dock chip was not removed');
  const startSent = posts.some(p => p.kind === 'input' && p.body.type === 'screencast' && p.body.action === 'start');
  if (!startSent) fail('restore: no {type:screencast, action:start} reached /api/browser/input — the pane would stay frozen after restore');

  await page.close();
  if (!fails.length) console.log('✅ minimize/restore: pane hides into the dock, pauses the screencast, and resumes it on restore.');
}

// ── 2. A fresh human launch defaults to a persistent profile, not throwaway,
//       and the header badge reflects whichever profile is actually live. ──
async function testDefaultProfileAndBadge() {
  const { page, posts } = await newPage({ launchProfile: 'main' });
  await page.evaluate(() => window.openBrowserPane('about:blank', 'p1'));
  await page.locator('#mc-browser-pane').waitFor({ state: 'attached' });

  const launch = posts.find(p => p.kind === 'launch');
  if (!launch || launch.body.profile !== 'main')
    fail(`default profile: launch body carried profile=${launch && launch.body.profile} — a hand-opened pane with no explicit profile must default to a persistent one, not stay throwaway`);

  const badge = page.locator('[data-bp="profile"]');
  const badgeText = (await badge.textContent() || '').trim();
  if (badgeText !== 'main') fail(`profile badge: showed "${badgeText}", expected "main"`);
  const badgeVisible = await badge.evaluate(el => getComputedStyle(el).display !== 'none');
  if (!badgeVisible) fail('profile badge: not visible — no way to see which profile a pane is on');

  await page.close();

  // A throwaway (agent-style) session should render the badge as visibly
  // "temp", not silently look identical to a signed-in one.
  const { page: page2 } = await newPage({ launchProfile: null });
  await page2.evaluate(() => window.openBrowserPane('about:blank', 'p1'));
  await page2.locator('#mc-browser-pane').waitFor({ state: 'attached' });
  const badge2Text = (await page2.locator('[data-bp="profile"]').textContent() || '').trim();
  if (badge2Text !== 'temp') fail(`profile badge (throwaway): showed "${badge2Text}", expected "temp"`);
  await page2.close();

  if (!fails.length) console.log('✅ default profile: hand-opened panes launch on a persistent profile by default, and the header badge shows which one (or "temp" for a throwaway session).');
}

// ── 3. Download progress/completion reaches the pane over the SAME SSE
//       channel as frames (a download never produces a new frame), renders
//       a progress bar, and shows a link + toast on completion. ────────────
async function testDownloadsUI() {
  const inProgress = JSON.stringify({ downloads: [{ guid: 'g1', filename: 'report.pdf',
    state: 'in_progress', received_bytes: 40, total_bytes: 100 }] });
  const completed = JSON.stringify({ downloads: [{ guid: 'g1', filename: 'report.pdf',
    state: 'completed', received_bytes: 100, total_bytes: 100,
    serve_url: '/api/serve-file?path=x&inline=0' }] });
  const sseBody = `data: ${inProgress}\n\ndata: ${completed}\n\n`;
  const { page } = await newPage({ sseBody });
  await page.evaluate(() => window.openBrowserPane('about:blank', 'p1'));
  await page.locator('#mc-browser-pane').waitFor({ state: 'attached' });
  await page.waitForTimeout(200);

  const box = page.locator('[data-bp="downloads"]');
  const html = await box.innerHTML();
  if (!html.includes('report.pdf')) fail(`downloads: filename never rendered into the pane — full box HTML: ${html.slice(0, 200)}`);
  const link = page.locator('[data-bp="downloads"] a');
  if (await link.count() !== 1) fail('downloads: no "Open / download" link rendered once the download completed');
  else {
    const href = await link.getAttribute('href');
    if (!href || !href.includes('/api/serve-file')) fail(`downloads: completion link href "${href}" does not point at /api/serve-file`);
  }
  const toasts = await page.evaluate(() => window.__toasts || []);
  if (!toasts.some(t => /Downloaded/.test(t))) fail(`downloads: no completion toast fired — got ${JSON.stringify(toasts)}`);

  await page.close();
  if (!fails.length) console.log('✅ downloads: progress renders in the pane, completion adds a working link and fires a toast — the ONLY signal a download happened, since it never repaints the page.');
}

await testMinimizeRestore();
await testDefaultProfileAndBadge();
await testDownloadsUI();
await browser.close();

if (!fails.length) console.log('✅ PASS — browser-pane minimize, default profile, and downloads UI all verified against the real static/js/browser-pane.js.');
else process.exitCode = 1;
