// Regression guard for the browser-pane maximize/restore button (Ron: a
// square button between Minimize and Close that grows the pane to the full
// Clayrune viewport and toggles back — mirrors the project modal's
// .modal-maximize / toggleModalMaximize glyph and geometry contract).
//
// Same hermetic pattern as browser-pane-coords.mjs / browser-pane-minimize-
// downloads.mjs: loads the REAL static/js/browser-pane.js against a stubbed
// API (no real Chromium pane backend, no running MC) and drives it with
// Playwright.
import { chromium } from 'playwright';
import { readFileSync } from 'fs';

const JS = readFileSync(process.env.SMOKE_PANE_JS
  || new URL('../../static/js/browser-pane.js', import.meta.url), 'utf8');
const FW = 800, FH = 500;   // a fake frame's CSS-px size, reported via SSE w/h
const browser = await chromium.launch();
const fails = [];
const fail = m => { fails.push(m); console.log(`❌ FAIL — ${m}`); };

// A genuine JPEG at FWxFH so img.naturalWidth/Height are real (needed for the
// post-maximize click-mapping check).
const seed = await (await browser.newContext()).newPage();
const JPEG = await seed.evaluate(([w, h]) => {
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  const x = c.getContext('2d');
  x.fillStyle = '#123'; x.fillRect(0, 0, w, h);
  return c.toDataURL('image/jpeg', 0.6).split(',')[1];
}, [FW, FH]);
await seed.close();

function htmlShell() {
  return `<body>
    <div id="modal-layer"></div>
    <div id="minimized-tray"></div>
    <div id="toast-container"></div>
    <script>
      window.nextModalZ = 100;
      window.showToast = m => { window.__toasts = window.__toasts || []; window.__toasts.push(m); };
      // Stands in for interactions.js's real _maxBtnInner (loaded after
      // browser-pane.js in the real app) so the initial-HTML call site and the
      // toggle's icon-swap both exercise the "reuse the modal's glyph" path
      // instead of always falling back to the pane's own inline SVG.
      window._maxBtnInner = (isFull) => '<svg data-testicon="' + (isFull ? 'restore' : 'max') + '"></svg>';
    </script>
    <script type="module" src="/static/js/browser-pane.js"></script>
  </body>`;
}

async function newPage() {
  const posts = [];
  const page = await (await browser.newContext({ viewport: { width: 1300, height: 900 } })).newPage();
  await page.route('**/*', async route => {
    const url = route.request().url();
    if (url.endsWith('/browser-pane.js'))
      return route.fulfill({ contentType: 'application/javascript', body: JS });
    if (url.includes('/api/browser/launch'))
      return route.fulfill({ status: 201, contentType: 'application/json',
        body: JSON.stringify({ session_id: 'sid-1', url: 'about:blank', profile: 'main',
                               view: { w: 1280, h: 800 } }) });
    if (url.includes('/api/browser/input')) {
      posts.push(JSON.parse(route.request().postData() || '{}'));
      return route.fulfill({ contentType: 'application/json', body: '{"ok":true}' });
    }
    if (url.includes('/browser/status'))
      return route.fulfill({ contentType: 'application/json', body: '{"sessions":[]}' });
    if (url.includes('/api/browser/profiles'))
      return route.fulfill({ contentType: 'application/json', body: '{"profiles":[]}' });
    if (url.includes('/api/browser/stream')) {
      const f = { seq: 1, img: JPEG, url: 'about:blank', w: FW, h: FH };
      return route.fulfill({ contentType: 'text/event-stream', body: `data: ${JSON.stringify(f)}\n\n` });
    }
    return route.fulfill({ contentType: 'text/html', body: htmlShell() });
  });
  await page.goto('http://localhost:9/');
  await page.waitForFunction(() => typeof window.openBrowserPane === 'function');
  await page.evaluate(() => {
    localStorage.removeItem('mc_browser_pane_geom');
    window.openBrowserPane('about:blank', 'p1');
  });
  const img = page.locator('#mc-browser-pane [data-bp="screen"]');
  await img.waitFor({ state: 'attached' });
  await page.waitForFunction(() => {
    const i = document.querySelector('#mc-browser-pane [data-bp="screen"]');
    return i && i.naturalWidth > 0;
  }, null, { timeout: 5000 });
  return { page, posts };
}

const geom = async page => page.evaluate(() => {
  const w = document.getElementById('mc-browser-pane');
  return { l: w.offsetLeft, t: w.offsetTop, w: w.offsetWidth, h: w.offsetHeight };
});
const maxIcon = page => page.locator('[data-bp="maximize"] [data-testicon]').getAttribute('data-testicon');
const gripVisible = page => page.locator('[data-bp="grip"]').evaluate(el => getComputedStyle(el).display !== 'none');
const lastViewportPost = posts => posts.slice().reverse().find(p => p.type === 'viewport');
// The re-fit target is the SCREEN BOX (the picture area, below the header
// bar/tab strip) -- not the whole pane -- same box sendView() (browser-
// pane.js) itself measures.
const screenBoxSize = page => page.evaluate(() => {
  const box = document.querySelector('#mc-browser-pane [data-bp="screen"]').parentElement;
  return { w: Math.floor(box.clientWidth), h: Math.floor(box.clientHeight) };
});

// ── 1. Toggle geometry: maximize fills the viewport, re-fit fires, restore
//       returns to the EXACT prior rect, and the grip hides/reappears. ──────
async function testToggleGeometryAndRefit() {
  const { page, posts } = await newPage();
  const before = await geom(page);
  const btn = page.locator('[data-bp="maximize"]');

  if ((await btn.getAttribute('title')) !== 'Maximize')
    fail(`initial title "${await btn.getAttribute('title')}", expected "Maximize"`);
  if ((await maxIcon(page)) !== 'max') fail('initial icon is not the "Maximize" (square) glyph');

  posts.length = 0;
  await btn.click();
  await page.waitForTimeout(50);
  const maxed = await geom(page);
  if (maxed.l !== 0 || maxed.t !== 0 || maxed.w !== 1300 || maxed.h !== 900)
    fail(`maximize: geometry ${JSON.stringify(maxed)}, expected {l:0,t:0,w:1300,h:900} (the full pane viewport)`);
  if ((await btn.getAttribute('title')) !== 'Restore')
    fail(`maximize: title "${await btn.getAttribute('title')}", expected "Restore"`);
  if ((await btn.getAttribute('aria-label')) !== 'Restore') fail('maximize: aria-label did not flip to "Restore"');
  if ((await maxIcon(page)) !== 'restore') fail('maximize: icon did not flip to the "Restore" glyph');
  if (await gripVisible(page)) fail('maximize: corner resize grip is still visible on a viewport-filling pane');
  const vp1 = lastViewportPost(posts);
  const sb1 = await screenBoxSize(page);
  if (!vp1) fail('maximize: no {type:"viewport"} POST reached /api/browser/input — the remote page was never told to re-fit');
  else if (vp1.w !== sb1.w || vp1.h !== sb1.h) fail(`maximize: re-fit viewport post was ${vp1.w}x${vp1.h}, expected the screen box's actual ${sb1.w}x${sb1.h}`);

  posts.length = 0;
  await btn.click();
  await page.waitForTimeout(50);
  const restored = await geom(page);
  if (restored.l !== before.l || restored.t !== before.t || restored.w !== before.w || restored.h !== before.h)
    fail(`restore: geometry ${JSON.stringify(restored)}, expected the exact pre-maximize rect ${JSON.stringify(before)}`);
  if ((await btn.getAttribute('title')) !== 'Maximize') fail('restore: title did not flip back to "Maximize"');
  if ((await maxIcon(page)) !== 'max') fail('restore: icon did not flip back to the "Maximize" glyph');
  if (!(await gripVisible(page))) fail('restore: corner resize grip did not reappear');
  const vp2 = lastViewportPost(posts);
  if (!vp2) fail('restore: no re-fit {type:"viewport"} POST after restoring down');

  await page.close();
  if (!fails.length) console.log('✅ toggle geometry: maximize fills the pane viewport and re-fits, restore returns to the exact prior rect and re-fits again, grip hides/reappears.');
}

// ── 2. Double-clicking the title bar toggles maximize too. ─────────────────
async function testDoubleClickTitleBar() {
  const { page } = await newPage();
  const bar = page.locator('#mc-browser-pane [data-bp="bar"]');
  // dblclick away from any button/input, matching the drag-guard's own check.
  await bar.dblclick({ position: { x: 300, y: 10 } });
  await page.waitForTimeout(50);
  let g = await geom(page);
  if (g.l !== 0 || g.t !== 0) fail(`title-bar dblclick: did not maximize — geometry ${JSON.stringify(g)}`);

  await bar.dblclick({ position: { x: 300, y: 10 } });
  await page.waitForTimeout(50);
  g = await geom(page);
  if (g.l === 0 && g.t === 0) fail('title-bar dblclick: second dblclick did not restore down');

  await page.close();
  if (!fails.length) console.log('✅ title-bar double-click: toggles maximize on and off, same as the button.');
}

// ── 3. Minimize while maximized, then restore from the dock chip, comes back
//       maximized (geometry untouched by minimize/restore). ───────────────
async function testMinimizeWhileMaximized() {
  const { page, posts } = await newPage();
  await page.locator('[data-bp="maximize"]').click();
  await page.waitForTimeout(50);
  const maxedGeom = await geom(page);

  await page.locator('[data-bp="minimize"]').click();
  await page.waitForTimeout(50);
  const win = page.locator('#mc-browser-pane');
  if ((await win.evaluate(el => getComputedStyle(el).display)) !== 'none')
    fail('minimize-while-maximized: pane did not hide');

  posts.length = 0;
  await page.locator('#minimized-tray .minimized-chip').click();
  await page.waitForTimeout(50);
  if ((await win.evaluate(el => getComputedStyle(el).display)) === 'none')
    fail('restore-from-dock: pane still hidden');
  const g = await geom(page);
  if (g.l !== maxedGeom.l || g.t !== maxedGeom.t || g.w !== maxedGeom.w || g.h !== maxedGeom.h)
    fail(`restore-from-dock: geometry ${JSON.stringify(g)}, expected to come back still maximized (${JSON.stringify(maxedGeom)})`);
  if ((await page.locator('[data-bp="maximize"]').getAttribute('title')) !== 'Restore')
    fail('restore-from-dock: maximize button did not stay in "Restore" state');
  if (!lastViewportPost(posts)) fail('restore-from-dock: no re-fit {type:"viewport"} POST after restoring from the dock chip');

  await page.close();
  if (!fails.length) console.log('✅ minimize/restore round trip while maximized: dock-chip restore comes back filling the viewport, still re-fits.');
}

// ── 4. Resizing the browser window while maximized keeps the pane filling
//       it (item 4 of the spec). ────────────────────────────────────────────
async function testWindowResizeWhileMaximized() {
  const { page } = await newPage();
  await page.locator('[data-bp="maximize"]').click();
  await page.waitForTimeout(50);

  await page.setViewportSize({ width: 1000, height: 700 });
  await page.waitForTimeout(150);
  const g = await geom(page);
  if (g.w !== 1000 || g.h !== 700 || g.l !== 0 || g.t !== 0)
    fail(`window resize while maximized: geometry ${JSON.stringify(g)}, expected {l:0,t:0,w:1000,h:700}`);

  await page.close();
  if (!fails.length) console.log('✅ window resize while maximized: the pane keeps filling the (new) viewport.');
}

// ── 5. Clicks still land on the right coordinates after maximizing — the
//       existing content-rect mapping (HiDPI click mapping, 81df69a) is
//       untouched by geometry changes, since it always reads the img's live
//       bounding box. ───────────────────────────────────────────────────────
async function testClickMappingAfterMaximize() {
  const { page, posts } = await newPage();
  await page.locator('[data-bp="maximize"]').click();
  await page.waitForTimeout(100);

  const img = page.locator('#mc-browser-pane [data-bp="screen"]');
  const box = await img.boundingBox();
  // Frame is FWxFH (800x500) inside a 1300x900 maximized pane -- object-fit:
  // contain letterboxes it. Click near the frame's true bottom-right corner
  // using the same scale/offset math _bpContentRect uses.
  const scale = Math.min(box.width / FW, box.height / FH);
  const cw = FW * scale, ch = FH * scale;
  const cx = box.x + (box.width - cw) / 2, cy = box.y + (box.height - ch) / 2;

  posts.length = 0;
  await page.mouse.move(cx + cw - 2, cy + ch - 2);
  await page.mouse.down(); await page.mouse.up();
  await page.waitForTimeout(150);
  const press = posts.find(p => p.action === 'mousePressed');
  if (!press) fail('click after maximize: no mousePressed reached the API');
  else if (press.x < FW * 0.9 || press.y < FH * 0.9 || press.x > FW || press.y > FH)
    fail(`click after maximize: bottom-right click mapped to x=${press.x.toFixed(0)} y=${press.y.toFixed(0)}, expected ~${FW},${FH}`);
  else console.log(`   click after maximize: bottom-right -> x=${press.x.toFixed(0)} y=${press.y.toFixed(0)} (want ~${FW},${FH})`);

  await page.close();
  if (!fails.length) console.log('✅ click mapping after maximize: clicks still land on the right page coordinates.');
}

await testToggleGeometryAndRefit();
await testDoubleClickTitleBar();
await testMinimizeWhileMaximized();
await testWindowResizeWhileMaximized();
await testClickMappingAfterMaximize();
await browser.close();

if (!fails.length) console.log('\n✅ PASS — browser-pane maximize/restore verified against the real static/js/browser-pane.js.');
else { console.log(`\n${fails.length} FAILED`); process.exitCode = 1; }
