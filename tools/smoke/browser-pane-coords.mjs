// Regression guard for browser-pane click alignment.
//
// The pane used to map every click into a hardcoded 1280x800 page coordinate
// space. CDP actually renders at the real content viewport -- measured
// 1264x649, because Emulation.setDeviceMetricsOverride does not take effect and
// a 1280x800 window loses ~150px to browser chrome and ~16px to the scrollbar.
// So a click at the bottom of the pane sent y~800 into a 649-tall viewport and
// landed ~150px below where the user aimed, which read as "the pane ignores my
// mouse". The <img> also declared aspect-ratio:1280/800 against a 1.95-ratio
// frame, stretching the picture.
//
// Loads the REAL static/js/browser-pane.js against a stub API, feeds it one
// frame at a NON-1280x800 size, clicks the bottom-right corner and asserts the
// forwarded coordinates land inside the real viewport. Runs twice: once with
// the server reporting w/h, once WITHOUT (so the naturalWidth fallback that
// keeps this working against an older server is covered too).
import { chromium } from 'playwright';
import { readFileSync } from 'fs';

// SMOKE_PANE_JS lets this be pointed at an older copy of the pane, which is how
// the guard itself is verified: against the pre-fix file it must FAIL.
const JS = readFileSync(process.env.SMOKE_PANE_JS
  || new URL('../../static/js/browser-pane.js', import.meta.url), 'utf8');
const FW = 1264, FH = 649;               // the real measured viewport
const browser = await chromium.launch();
const fails = [];
const fail = m => { fails.push(m); console.log(`❌ FAIL — ${m}`); };

// A genuine JPEG at FWxFH, so img.naturalWidth/Height are real.
const seed = await (await browser.newContext()).newPage();
const JPEG = await seed.evaluate(([w, h]) => {
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  const x = c.getContext('2d');
  x.fillStyle = '#123'; x.fillRect(0, 0, w, h);
  x.fillStyle = '#fff'; x.fillRect(w - 40, h - 40, 40, 40);
  return c.toDataURL('image/jpeg', 0.6).split(',')[1];
}, [FW, FH]);
await seed.close();

async function run(sendWH) {
  const posts = [];
  const page = await (await browser.newContext()).newPage();
  await page.route('**/*', async route => {
    const url = route.request().url();
    if (url.endsWith('/browser-pane.js'))
      return route.fulfill({ contentType: 'application/javascript', body: JS });
    if (url.includes('/api/browser/launch'))
      return route.fulfill({ status: 201, contentType: 'application/json',
        body: JSON.stringify({ session_id: 'sid', url: 'about:blank', view: { w: 1280, h: 800 } }) });
    if (url.includes('/api/browser/input')) {
      posts.push(JSON.parse(route.request().postData() || '{}'));
      return route.fulfill({ contentType: 'application/json', body: '{"ok":true}' });
    }
    if (url.includes('/browser/status'))
      return route.fulfill({ contentType: 'application/json', body: '{"sessions":[]}' });
    if (url.includes('/api/browser/stream')) {
      const f = { seq: 1, img: JPEG, url: 'about:blank' };
      if (sendWH) { f.w = FW; f.h = FH; }
      return route.fulfill({ contentType: 'text/event-stream',
        body: `data: ${JSON.stringify(f)}\n\n` });
    }
    return route.fulfill({ contentType: 'text/html', body:
      `<body><div id="modal-layer"></div><div id="toast-container"></div>
       <script>window.nextModalZ=100;window.showToast=m=>window.__toast=m;</script>
       <script type="module" src="/static/js/browser-pane.js"></script></body>` });
  });
  await page.goto('http://localhost:9/');
  await page.waitForFunction(() => typeof window.openBrowserPane === 'function');
  await page.evaluate(() => window.openBrowserPane('about:blank', 'p1'));
  const img = page.locator('#mc-browser-pane [data-bp="screen"]');
  await img.waitFor({ state: 'attached' });
  // wait for the frame to decode
  await page.waitForFunction(() => {
    const i = document.querySelector('#mc-browser-pane [data-bp="screen"]');
    return i && i.naturalWidth > 0;
  }, null, { timeout: 5000 });

  const label = sendWH ? 'server sends w/h' : 'older server, no w/h';
  const ar = await img.evaluate(i => i.style.aspectRatio);
  if (!/1264\s*\/\s*649/.test(ar))
    fail(`[${label}] image aspect-ratio is "${ar}", not the frame's 1264/649 — the picture is still stretched`);

  // click the very bottom-right of the rendered image
  const box = await img.boundingBox();
  posts.length = 0;
  await page.mouse.move(box.x + box.width - 1, box.y + box.height - 1);
  await page.mouse.down();
  await page.mouse.up();
  await page.waitForTimeout(200);

  const press = posts.find(p => p.action === 'mousePressed');
  if (!press) { fail(`[${label}] no mousePressed reached the API`); await page.close(); return; }
  if (press.y > FH) fail(`[${label}] y=${press.y.toFixed(0)} exceeds the real viewport height ${FH} — this is the original bug`);
  if (press.x > FW) fail(`[${label}] x=${press.x.toFixed(0)} exceeds the real viewport width ${FW}`);
  // bottom-right corner must map near the far corner, not 80% of the way down
  if (press.y < FH * 0.9) fail(`[${label}] bottom-edge click mapped to y=${press.y.toFixed(0)}, expected ~${FH}`);
  if (!fails.length) console.log(`   [${label}] bottom-right click -> x=${press.x.toFixed(0)} y=${press.y.toFixed(0)} (viewport ${FW}x${FH}), aspect-ratio ${ar}`);
  await page.close();
}

await run(true);
await run(false);
await browser.close();
if (!process.exitCode && !fails.length)
  console.log('✅ browser pane coords: clicks map into the real frame viewport, via server w/h AND the naturalWidth fallback.');
else process.exitCode = 1;
