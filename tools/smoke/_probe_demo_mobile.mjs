// Steward probe: on a phone, does /demo render the demo app's MOBILE layout or
// its DESKTOP layout? The app keys mobile off its OWN width (CSS @media
// max-width:960px, JS isMobile() max-width:768px) — and inside an iframe that
// is the IFRAME's width, not the phone's. Measures both, on both sides of the
// boundary, before and after the full-screen launch. Screenshots each state.
//
// Run: cd tools/smoke && node _probe_demo_mobile.mjs   (PROBE_URL to retarget)
import { chromium, devices } from 'playwright';
import fs from 'fs';

const URL = process.env.PROBE_URL || 'https://clayrune.io/demo';
const OUT = process.env.SHOT_DIR || '_scratch/demoshots';
fs.mkdirSync(OUT, { recursive: true });

// What the demo app looks like from inside the iframe.
const INTROSPECT = () => {
  const q = s => document.querySelector(s);
  const vis = s => { const e = q(s); if (!e) return 'absent'; const r = e.getBoundingClientRect();
    return (r.width > 0 && r.height > 0 && getComputedStyle(e).display !== 'none') ? 'VISIBLE' : 'hidden'; };
  return {
    innerWidth: window.innerWidth,
    docClientWidth: document.documentElement.clientWidth,
    mq960: window.matchMedia('(max-width: 960px)').matches,
    mq768_isMobile: window.matchMedia('(max-width: 768px)').matches,
    bodyClass: document.body.className,
    // desktop-only furniture vs mobile-only furniture
    rail: vis('#rail'), surfaces: vis('#surfaces'), sidebar: vis('.side, #sidebar'),
    dashGrid: vis('#dashGrid'), dashListM: vis('#dashListM'),
    tabBar: vis('.demo-tabbar, #tabClaydo, .bottom-tab-bar'),
    hamburger: vis('.demo-hamburger'),
  };
};

async function run(label, dev) {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ ...dev });
  const page = await ctx.newPage();
  const out = { label, device: { w: dev.viewport.width, h: dev.viewport.height, dsf: dev.deviceScaleFactor } };

  await page.goto(URL, { waitUntil: 'load', timeout: 60000 });
  await page.evaluate(() => document.getElementById('demoFrame')?.scrollIntoView({ block: 'center' }));
  await page.waitForTimeout(6000);

  const geom = async () => page.evaluate(() => {
    const f = document.getElementById('demoFrame'), i = document.getElementById('demoEmbed');
    const fr = f?.getBoundingClientRect(), ir = i?.getBoundingClientRect();
    return {
      parentInnerWidth: window.innerWidth,
      frameW: fr ? Math.round(fr.width) : null, frameH: fr ? Math.round(fr.height) : null,
      iframeW: ir ? Math.round(ir.width) : null, iframeH: ir ? Math.round(ir.height) : null,
      isFull: !!f?.classList.contains('is-full'), cramped: !!f?.classList.contains('cramped'),
      docScrollW: document.documentElement.scrollWidth,
    };
  });

  const inner = async () => {
    const fr = page.frames().find(f => /demo-app/.test(f.url()));
    if (!fr) return 'NO IFRAME LOADED';
    try { return await fr.evaluate(INTROSPECT); } catch (e) { return 'ERR ' + String(e).slice(0, 80); }
  };

  out.bounded = { geom: await geom(), app: await inner() };
  await page.screenshot({ path: `${OUT}/${label}-1-bounded.png`, fullPage: false });

  const cover = page.locator('#demoCover');
  out.coverVisible = await cover.isVisible().catch(() => false);
  if (out.coverVisible) {
    await cover.click({ timeout: 8000 }).catch(e => { out.coverErr = String(e).slice(0, 100); });
    await page.waitForTimeout(7000);
  }
  out.fullscreen = { geom: await geom(), app: await inner() };
  await page.screenshot({ path: `${OUT}/${label}-2-fullscreen.png`, fullPage: false });

  await browser.close();
  return out;
}

const targets = [
  ['pixel7', devices['Pixel 7']],
  ['iphone14', devices['iPhone 14']],
  ['galaxy-s9', devices['Galaxy S9+']],
  ['ipad-mini', devices['iPad Mini']],
];
const res = [];
for (const [l, d] of targets) res.push(await run(l, d));
console.log(JSON.stringify(res, null, 2));
