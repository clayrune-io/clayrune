// Steward probe (2026-08-08): does the "Launch the interactive demo" cover
// RELOAD the demo iframe, and do the downstream funnel beacons ever fire?
//
// Hypothesis under test — from reading the DEPLOYED bundle:
//   demo.html openFull() does `document.body.appendChild(f)` to escape ancestor
//   stacking contexts. Moving an iframe in the DOM re-loads it (per spec), so
//   the ONE deliberate click we ask a visitor for throws the loaded demo away
//   and re-fetches /demo/demo-app over the path that 504s ~20% of the time.
//   If true: (a) demo-app is fetched TWICE per engaged visitor, (b) demo-open
//   double-counts, (c) the coach tour restarts from step 0.
//
// Run: cd tools/smoke && node _probe_demo_cover.mjs
import { chromium, devices } from 'playwright';

const URL = process.env.PROBE_URL || 'https://clayrune.io/demo';

async function run(label, ctxOpts) {
  const browser = await chromium.launch();
  const ctx = await browser.newContext(ctxOpts);
  const page = await ctx.newPage();

  const appFetches = [];   // every /demo/demo-app document fetch
  const beacons = [];      // every /e/<event>
  ctx.on('request', r => {
    const u = r.url();
    if (/\/demo\/demo-app(\?|$|\.html)/.test(u)) appFetches.push({ u, t: Date.now() });
    if (u.includes('/e/')) beacons.push({ e: u.split('/e/')[1], t: Date.now() });
  });
  const bad = [];
  ctx.on('response', r => { if (r.status() >= 400) bad.push(r.status() + ' ' + r.url()); });

  const snap = () => ({ appFetches: appFetches.length, beacons: beacons.map(b => b.e) });
  const out = { label, phases: {} };

  await page.goto(URL, { waitUntil: 'load', timeout: 60000 });
  await page.waitForTimeout(2500);
  out.phases.A_load = snap();

  // Scroll the demo stage into view — this is what trips loading="lazy".
  await page.evaluate(() => document.getElementById('demoFrame')?.scrollIntoView({ block: 'center' }));
  await page.waitForTimeout(6000);
  out.phases.B_scrolled_into_view = snap();

  out.coverVisible = await page.locator('#demoCover').isVisible().catch(() => false);
  out.frameCramped = await page.evaluate(() =>
    document.getElementById('demoFrame')?.classList.contains('cramped') ?? null);

  // The single deliberate click the page asks for.
  if (out.coverVisible) {
    await page.locator('#demoCover').click({ timeout: 8000 }).catch(e => { out.coverClickErr = String(e).slice(0, 140); });
    await page.waitForTimeout(7000);
  }
  out.phases.C_after_cover_click = snap();

  // Now try to actually advance the funnel inside the iframe.
  const frame = page.frames().find(f => /demo-app/.test(f.url()));
  out.frameFound = !!frame;
  if (frame) {
    out.frameUrl = frame.url();
    // Did the app boot at all after the (re)load?
    out.appBooted = await frame.evaluate(() => !!document.getElementById('demo-root')).catch(() => 'ERR');
    // Coach step 0 offers an "Open Orchard" button.
    const nextBtn = frame.locator('.coach-next, [data-coach-next], .demo-coach button').first();
    out.coachBtnText = await nextBtn.textContent({ timeout: 5000 }).catch(() => null);
    await nextBtn.click({ timeout: 5000 }).catch(e => { out.coachClickErr = String(e).slice(0, 100); });
    await page.waitForTimeout(2500);
    out.phases.D_after_coach_next = snap();

    // Click a conversation row -> conv-switch
    await frame.locator('#railList .conv-row').nth(1).click({ timeout: 5000 })
      .catch(e => { out.convClickErr = String(e).slice(0, 100); });
    await page.waitForTimeout(2000);
    out.phases.E_after_conv_click = snap();
  }

  out.appFetchUrls = appFetches.map(f => f.u);
  out.badStatuses = bad.slice(0, 10);
  await browser.close();
  return out;
}

const results = [];
results.push(await run('laptop-1280x800', { viewport: { width: 1280, height: 800 } }));
results.push(await run('desktop-1600x900', { viewport: { width: 1600, height: 900 } }));
results.push(await run('pixel7', { ...devices['Pixel 7'] }));
console.log(JSON.stringify(results, null, 2));
