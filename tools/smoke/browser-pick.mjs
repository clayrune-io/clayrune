// Element picker UI + composer chip (static/js/browser-pick.js).
//
// Loads the REAL browser-pane.js, browser-pick.js and composer-extras.js against
// a stub API and asserts, in headless Chromium:
//   desktop mouse   toolbar button -> pick mode; moving aims (hover POST in the
//                   pane's picture px, highlight box painted); click picks; ONE
//                   chip lands in the composer's `fu_<session>` list with the
//                   screenshot thumbnail; nothing reaches /api/browser/input
//                   while picking (a pick must never click the page)
//   cancel          Esc and the banner's Cancel leave pick mode with no POST
//   failure         a refused pick toasts and stays in pick mode
//   phone (touch)   the sheet's menu entry -> pick mode; press + drag aims, lift
//                   picks (a phone has no hover)
//
// The CDP half (isolated world, envelope, caps) is covered against a real
// Chromium by tests/test_browser_pick_live.py -- this file stubs the API.
import { chromium } from 'playwright';
import { readFileSync } from 'fs';

const src = f => readFileSync(new URL(`../../static/js/${f}`, import.meta.url), 'utf8');
const JS = { 'browser-pane.js': src('browser-pane.js'), 'browser-pick.js': src('browser-pick.js'),
             'composer-extras.js': src('composer-extras.js'),
             'browser-pane-stream.js': src('browser-pane-stream.js') };   // browser-pane.js imports it
const FW = 1264, FH = 649;
const browser = await chromium.launch();
const fails = [];
const fail = m => { fails.push(m); console.log(`❌ FAIL — ${m}`); };
const ok = m => console.log(`   ok — ${m}`);

const seed = await (await browser.newContext()).newPage();
const JPEG = await seed.evaluate(([w, h]) => {
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  const x = c.getContext('2d'); x.fillStyle = '#245'; x.fillRect(0, 0, w, h);
  return c.toDataURL('image/jpeg', 0.6).split(',')[1];
}, [FW, FH]);
await seed.close();

const RECT = { x: 100, y: 80, w: 300, h: 120 };           // what the stub hover route returns (picture px)

async function newPane(ctxOpts, state) {
  const page = await (await browser.newContext(ctxOpts)).newPage();
  const log = { input: [], hover: [], pick: [], pageErrors: [] };
  page.on('pageerror', e => log.pageErrors.push(String(e)));
  await page.route('**/*', async route => {
    const url = route.request().url();
    const name = url.split('/').pop().split('?')[0];
    if (JS[name]) return route.fulfill({ contentType: 'application/javascript', body: JS[name] });
    if (url.includes('/api/browser/launch'))
      return route.fulfill({ status: 201, contentType: 'application/json',
        body: JSON.stringify({ session_id: 'sid', url: 'about:blank', view: { w: 1280, h: 800 } }) });
    if (url.includes('/api/browser/input')) {
      log.input.push(JSON.parse(route.request().postData() || '{}'));
      return route.fulfill({ contentType: 'application/json', body: '{"ok":true}' });
    }
    if (url.includes('/api/browser/pick/hover')) {
      log.hover.push({ ...JSON.parse(route.request().postData() || '{}'),
                       origin: route.request().headers()['origin'] });
      return route.fulfill({ contentType: 'application/json',
        body: JSON.stringify({ ok: true, rect: RECT, label: 'div.card' }) });
    }
    if (url.includes('/api/browser/pick')) {
      log.pick.push(JSON.parse(route.request().postData() || '{}'));
      if (state.refuse)
        return route.fulfill({ status: 403, contentType: 'application/json',
          body: JSON.stringify({ ok: false, error: 'human_only', detail: 'refused for the test' }) });
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify({
        ok: true, pick_id: 'abc', label: 'div.card', tag: 'div', selector: 'body > div.card',
        context_path: 'C:\\uploads\\browser_pick_abc.json',
        screenshot_path: 'C:\\uploads\\browser_pick_abc.png', screenshot: 'attached',
        truncated: false, hidden_content_flagged: false }) });
    }
    if (url.includes('/api/serve-image'))
      return route.fulfill({ contentType: 'image/jpeg', body: Buffer.from(JPEG, 'base64') });
    if (url.includes('/browser/status'))
      return route.fulfill({ contentType: 'application/json', body: '{"sessions":[]}' });
    if (url.includes('/api/browser/stream'))
      return route.fulfill({ contentType: 'text/event-stream',
        body: `data: ${JSON.stringify({ seq: 1, img: JPEG, url: 'about:blank', w: FW, h: FH })}\n\n` });
    return route.fulfill({ contentType: 'text/html', body:
      `<head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
       <body><div id="modal-layer"></div>
       <script>
         window.nextModalZ = 100; window.__toasts = []; window.__refreshed = 0;
         window.showToast = m => window.__toasts.push(m);
         window.esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
         window.agentPendingImages = {}; window.activeAgentTab = { p1: 's1' }; window.agentConvNew = {};
         window.refreshModal = () => { window.__refreshed++; };
       </script>
       <script type="module" src="/static/js/browser-pane.js"></script>
       <script type="module" src="/static/js/browser-pick.js"></script>
       <script type="module" src="/static/js/composer-extras.js"></script></body>` });
  });
  await page.goto('http://localhost:9/');
  await page.waitForFunction(() => typeof window.openBrowserPane === 'function'
    && typeof window.bpPickInit === 'function');
  await page.evaluate(() => window.openBrowserPane('about:blank', 'p1'));
  await page.waitForFunction(() => {
    const i = document.querySelector('#mc-browser-pane [data-bp="screen"]');
    return i && i.naturalWidth > 0;
  }, null, { timeout: 5000 });
  return { page, log };
}

// picture-px point -> client px, from the live letterboxed picture rect
const toClient = (page, vx, vy) => page.evaluate(([vx, vy, FW, FH]) => {
  const r = document.querySelector('#mc-browser-pane [data-bp="screen"]').getBoundingClientRect();
  const s = Math.min(r.width / FW, r.height / FH);
  return { x: r.left + (r.width - FW * s) / 2 + vx * s, y: r.top + (r.height - FH * s) / 2 + vy * s, s };
}, [vx, vy, FW, FH]);

const overlayUp = page => page.evaluate(() => !!document.querySelector('[data-bp="pick-overlay"]'));

// ── desktop, mouse ───────────────────────────────────────────────────────────
{
  const state = { refuse: false };
  const { page, log } = await newPane({ viewport: { width: 1600, height: 1000 } }, state);

  await page.click('#mc-browser-pane [data-bp="pick"]');
  if (!(await overlayUp(page))) fail('desktop: pick button did not enter pick mode');
  else ok('toolbar button enters pick mode');

  const target = await toClient(page, 500, 300);
  await page.mouse.move(target.x, target.y);
  await page.waitForFunction(() => document.querySelector('[data-bp="pick-box"]').style.display === 'block',
    null, { timeout: 4000 }).catch(() => fail('desktop: highlight box never painted after hover'));
  const h = log.hover[0];
  if (!h) fail('desktop: no hover POST');
  else {
    if (Math.abs(h.x - 500) > 1.5 || Math.abs(h.y - 300) > 1.5)
      fail(`desktop: hover sent (${h.x.toFixed(1)}, ${h.y.toFixed(1)}), expected ~(500, 300) picture px`);
    else ok(`hover mapped to picture px (${h.x.toFixed(0)}, ${h.y.toFixed(0)})`);
    if (!h.origin) fail('desktop: hover POST carried no Origin header (the route refuses that as an agent call)');
  }
  const box = await page.evaluate(() => {
    const b = document.querySelector('[data-bp="pick-box"]');
    return { w: parseFloat(b.style.width), h: parseFloat(b.style.height) };
  });
  if (Math.abs(box.w - RECT.w * target.s) > 2 || Math.abs(box.h - RECT.h * target.s) > 2)
    fail(`desktop: highlight is ${box.w.toFixed(0)}x${box.h.toFixed(0)}, expected ${(RECT.w * target.s).toFixed(0)}x${(RECT.h * target.s).toFixed(0)}`);
  else ok('highlight box sized from the hover rect at the pane scale');

  await page.mouse.down(); await page.mouse.up();
  await page.waitForFunction(() => (window.agentPendingImages.fu_s1 || []).length === 1, null, { timeout: 4000 })
    .catch(() => fail('desktop: no chip in agentPendingImages.fu_s1 after the pick'));
  const chip = await page.evaluate(() => (window.agentPendingImages.fu_s1 || [])[0]);
  if (chip) {
    if (!/browser_pick_abc\.json$/.test(chip.serverPath || '')) fail(`chip serverPath is ${chip.serverPath}`);
    else ok('chip carries the context file path as serverPath (composer skips its upload)');
    if (!chip.pick || !/serve-image\?path=/.test(chip.pick.thumbUrl || '')) fail('chip has no screenshot thumbnail url');
  }
  if (log.pick.length !== 1) fail(`desktop: expected 1 pick POST, got ${log.pick.length}`);
  else if (Math.abs(log.pick[0].x - 500) > 1.5) fail(`pick POST x=${log.pick[0].x}`);
  if (await overlayUp(page)) fail('desktop: still in pick mode after a successful pick');
  else ok('pick mode ends after the pick');
  const clicks = log.input.filter(i => i.type === 'mouse');
  if (clicks.length) fail(`desktop: ${clicks.length} mouse event(s) reached the PAGE while picking`);
  else ok('no click reached the page');
  const html = await page.evaluate(() => window.renderAgentImagePreviews('fu_s1'));
  const chips = (html.match(/class="agent-image-preview"/g) || []).length;
  if (chips !== 1) fail(`composer renders ${chips} chips, expected exactly 1`);
  if (!/serve-image\?path=/.test(html) || !html.includes('div.card')) fail('composer chip lacks thumbnail or label');
  else ok('composer renders ONE chip with thumbnail + label');
  const toast = await page.evaluate(() => window.__toasts.join(' | '));
  if (!/Attached div\.card/.test(toast)) fail(`toast was: ${toast}`);

  // Esc cancels, no POST
  const before = log.pick.length;
  await page.click('#mc-browser-pane [data-bp="pick"]');
  await page.keyboard.press('Escape');
  if (await overlayUp(page)) fail('Esc did not leave pick mode'); else ok('Esc leaves pick mode');
  // Cancel button cancels
  await page.click('#mc-browser-pane [data-bp="pick"]');
  await page.click('[data-bp="pick-cancel"]');
  if (await overlayUp(page)) fail('Cancel did not leave pick mode'); else ok('banner Cancel leaves pick mode');
  if (log.pick.length !== before) fail('cancelling sent a pick POST');

  // a refused pick: toast, stays in pick mode, no chip added
  state.refuse = true;
  await page.click('#mc-browser-pane [data-bp="pick"]');
  const t2 = await toClient(page, 200, 200);
  await page.mouse.move(t2.x, t2.y); await page.mouse.down(); await page.mouse.up();
  await page.waitForFunction(() => window.__toasts.some(t => /Pick failed/.test(t)), null, { timeout: 4000 })
    .catch(() => fail('a refused pick did not toast'));
  if (!(await overlayUp(page))) fail('a refused pick left pick mode (user cannot retry)');
  const n = await page.evaluate(() => (window.agentPendingImages.fu_s1 || []).length);
  if (n !== 1) fail(`refused pick changed the chip list (${n})`);
  else ok('refused pick toasts, stays in pick mode, adds no chip');
  if (log.pageErrors.length) fail('page errors: ' + log.pageErrors.join(' | '));
  await page.close();
}

// ── phone, touch ─────────────────────────────────────────────────────────────
{
  const state = { refuse: false };
  const { page, log } = await newPane({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true,
                                         deviceScaleFactor: 2 }, state);
  const mobile = await page.evaluate(() => document.getElementById('mc-browser-pane').dataset.mobile);
  if (mobile !== '1') fail(`expected the mobile sheet, got data-mobile=${mobile}`);
  await page.tap('#mc-browser-pane [data-bp="menu"]');
  await page.tap('#mc-browser-pane [data-bp="mm-pick"]');
  if (!(await overlayUp(page))) fail('phone: menu entry did not enter pick mode');
  else ok('phone: ⋮ menu → Pick element enters pick mode');
  const menuOpen = await page.evaluate(() => document.querySelector('[data-bp="mobmenu"]').style.display);
  if (menuOpen !== 'none') fail('phone: the menu stayed open over the picture');

  const a = await toClient(page, 600, 200), b = await toClient(page, 640, 260);
  const cdp = await page.context().newCDPSession(page);
  const touch = (type, p) => cdp.send('Input.dispatchTouchEvent',
    { type, touchPoints: type === 'touchEnd' ? [] : [{ x: p.x, y: p.y }] });
  await touch('touchStart', a);
  await touch('touchMove', b);
  await page.waitForFunction(() => document.querySelector('[data-bp="pick-box"]').style.display === 'block',
    null, { timeout: 4000 }).catch(() => fail('phone: touch-drag never painted a highlight'));
  if (log.pick.length) fail('phone: picked before the finger lifted');
  else ok('phone: press + drag aims without picking');
  if (!log.hover.length) fail('phone: no hover POST while dragging');
  await touch('touchEnd', b);
  await page.waitForFunction(() => (window.agentPendingImages.fu_s1 || []).length === 1, null, { timeout: 4000 })
    .catch(() => fail('phone: lifting the finger did not attach a chip'));
  if (log.pick.length === 1 && Math.abs(log.pick[0].x - 640) <= 2 && Math.abs(log.pick[0].y - 260) <= 2)
    ok(`phone: lift picks at the lift point (${log.pick[0].x.toFixed(0)}, ${log.pick[0].y.toFixed(0)})`);
  else fail(`phone: pick POSTs ${JSON.stringify(log.pick)}, expected one near (640, 260)`);
  if (log.input.some(i => i.type === 'mouse' || i.type === 'wheel' || i.type === 'zoom'))
    fail('phone: touch reached the page while picking');
  if (log.pageErrors.length) fail('page errors: ' + log.pageErrors.join(' | '));
  await page.close();
}

await browser.close();
if (fails.length) { console.log(`\n${fails.length} failure(s)`); process.exit(1); }
console.log('\nbrowser-pick smoke: all checks passed');
