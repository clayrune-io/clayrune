#!/usr/bin/env node
/**
 * Image viewer — narrow-image toolbar-clipping regression test.
 *
 * WHY THIS EXISTS
 * ----------------
 * The viewer window sizes itself to the picture's natural width
 * (_ivFitBox in mermaid.js), and the toolbar is a child of that window. For a
 * portrait/narrow image (Ron's report: ~330px wide) the window shrank to the
 * old flat 320px floor while the toolbar's 8 fixed buttons (-, 100%, +, reset,
 * bg, save, open, close) needed ~450px in a single un-wrapping flex row. The
 * result: buttons past "save" rendered PAST the toolbar's clipped right edge
 * — present in the DOM, invisible and unreachable, with no wrap, no scroll,
 * no overflow menu to get to them. Even the close button landed out there.
 *
 * `node --check` and the boot smoke test both miss this class of bug entirely
 * — the viewer boots fine, every button exists in the DOM, it just renders
 * outside its own container. This test asserts GEOMETRY, not presence: every
 * toolbar control's bounding box must sit fully inside both its toolbar and
 * the window's content box, at a deliberately narrow image size, on both a
 * desktop-width viewport and a 390px mobile viewport (with the added
 * constraint there that the fix must not scroll the page horizontally).
 *
 * RUN   node tools/smoke/image-viewer-narrow-toolbar.mjs
 * Exit 0 = every control is reachable; 1 = one is clipped/off-container.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..', '..');
const APP_CSS = readFileSync(resolve(ROOT, 'static', 'css', 'app.css'), 'utf8');
const MERMAID_JS = readFileSync(resolve(ROOT, 'static', 'js', 'mermaid.js'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';

const PAGE = `<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="/static/css/app.css"></head>
<body><script>
  window.esc = s => String(s);
  window.API_BASE = '';
</script>
<script type="module" src="/static/js/mermaid.js"></script></body></html>`;

const fails = [];
const check = (name, ok, detail) => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
  if (!ok) fails.push(name);
};

async function openNarrowViewer(page, imgW, imgH) {
  await page.evaluate(({ imgW, imgH }) => {
    document.querySelectorAll('.mermaid-viewer-overlay').forEach(el => el.remove());
    const c = document.createElement('canvas');
    c.width = imgW; c.height = imgH;
    const x = c.getContext('2d');
    x.fillStyle = '#4a7'; x.fillRect(0, 0, imgW, imgH);
    window._openImageViewer(c.toDataURL('image/png'));
  }, { imgW, imgH });
  await page.waitForSelector('.mermaid-viewer-overlay img');
  await page.waitForFunction(() => document.querySelector('.mermaid-viewer-overlay img').complete);
  await page.waitForTimeout(200);
}

// Measures every toolbar child's geometry against the toolbar's own box AND
// the window's content box — a control can be "present" in the DOM and still
// render past a clipped edge, which is exactly the bug being guarded here.
async function measureToolbar(page) {
  return page.evaluate(() => {
    const content = document.querySelector('.mermaid-viewer-content');
    const toolbar = document.querySelector('.mermaid-viewer-toolbar');
    const contentRect = content.getBoundingClientRect();
    const toolbarRect = toolbar.getBoundingClientRect();
    const kids = [...toolbar.children];
    const controls = kids.map(el => {
      const r = el.getBoundingClientRect();
      const visible = r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden';
      const insideToolbar = r.left >= toolbarRect.left - 0.5 && r.right <= toolbarRect.right + 0.5;
      const insideContent = r.left >= contentRect.left - 0.5 && r.right <= contentRect.right + 0.5;
      return {
        label: el.className.replace(/\bmermaid-viewer-btn\b/, '').trim() || el.tagName,
        visible, insideToolbar, insideContent,
      };
    });
    return {
      controlCount: kids.length,
      controls,
      contentWidth: contentRect.width,
      toolbarWidth: toolbarRect.width,
      docScrollWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
    };
  });
}

// ── Desktop: deliberately narrow (portrait) image ──
{
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  page.on('pageerror', e => check('no page error (desktop)', false, e.message));
  await page.route('**/*', route => {
    const p = new URL(route.request().url()).pathname;
    if (p === '/') return route.fulfill({ contentType: 'text/html', body: PAGE });
    if (p === '/static/css/app.css') return route.fulfill({ contentType: 'text/css', body: APP_CSS });
    if (p === '/static/js/mermaid.js') return route.fulfill({ contentType: 'text/javascript', body: MERMAID_JS });
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window._openImageViewer === 'function');

  // ~220x400: narrower than Ron's ~330px report, on purpose — the toolbar's
  // width requirement doesn't depend on the image at all, so this has to hold
  // at ANY narrow size, not just the one in the screenshot.
  await openNarrowViewer(page, 220, 400);
  const geo = await measureToolbar(page);

  check('desktop: expected 8 image-viewer controls all present',
    geo.controlCount === 8, `${geo.controlCount} controls`);
  for (const c of geo.controls) {
    check(`desktop: "${c.label}" is visible`, c.visible);
    check(`desktop: "${c.label}" sits inside the toolbar box`, c.insideToolbar);
    check(`desktop: "${c.label}" sits inside the window's content box`, c.insideContent);
  }
  // The window itself must have widened past the old flat 320px floor to fit
  // its own toolbar — this is the actual fix, not a side effect of it.
  check('desktop: window widened past the old 320px floor to fit its toolbar',
    geo.contentWidth > 320, `content width ${geo.contentWidth}px (toolbar needs ${geo.toolbarWidth}px)`);

  await browser.close();
}

// ── Mobile: same narrow image at a 390px viewport ──
// Constraint: at 390px the toolbar cannot be widened past the viewport (that
// would scroll the whole PAGE horizontally, not just clip inside the modal) —
// so here the controls must stay reachable via WRAP, not extra width.
{
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true });
  const page = await ctx.newPage();
  page.on('pageerror', e => check('no page error (mobile)', false, e.message));
  await page.route('**/*', route => {
    const p = new URL(route.request().url()).pathname;
    if (p === '/') return route.fulfill({ contentType: 'text/html', body: PAGE });
    if (p === '/static/css/app.css') return route.fulfill({ contentType: 'text/css', body: APP_CSS });
    if (p === '/static/js/mermaid.js') return route.fulfill({ contentType: 'text/javascript', body: MERMAID_JS });
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window._openImageViewer === 'function');

  await openNarrowViewer(page, 150, 400);
  const geo = await measureToolbar(page);

  check('mobile 390px: expected 8 image-viewer controls all present',
    geo.controlCount === 8, `${geo.controlCount} controls`);
  for (const c of geo.controls) {
    check(`mobile 390px: "${c.label}" is visible`, c.visible);
    check(`mobile 390px: "${c.label}" sits inside the window's content box`, c.insideContent);
  }
  check('mobile 390px: page does not scroll horizontally',
    geo.docScrollWidth <= geo.viewportWidth, `doc ${geo.docScrollWidth}px vs viewport ${geo.viewportWidth}px`);

  await browser.close();
}

if (fails.length) {
  console.error(`\n${fails.length} check(s) failed: ${fails.join(', ')}`);
  process.exit(1);
}
console.log('\nAll narrow-image toolbar checks OK.');
