// Steward probe: at which PARENT viewport width does the embedded demo flip
// from its mobile layout to its desktop layout? The app keys off the IFRAME's
// width, which is always narrower than the phone, so the two boundaries do not
// line up with the 960/768 you would expect from the outside.
import { chromium } from 'playwright';
const URL = process.env.PROBE_URL || 'https://clayrune.io/demo';
const WIDTHS = [360, 390, 412, 540, 600, 700, 768, 820, 900, 960, 1000, 1024, 1100, 1200, 1280, 1366, 1400, 1440, 1600];
const browser = await chromium.launch();
console.log('parentW  frameW  appW   mq960  mq768    layout        cover  cramped');
for (const w of WIDTHS) {
  const ctx = await browser.newContext({ viewport: { width: w, height: 860 } });
  const page = await ctx.newPage();
  try {
    await page.goto(URL, { waitUntil: 'load', timeout: 45000 });
    await page.evaluate(() => document.getElementById('demoFrame')?.scrollIntoView({ block: 'center' }));
    await page.waitForTimeout(4500);
    const cover = await page.locator('#demoCover').isVisible().catch(() => false);
    if (cover) { await page.locator('#demoCover').click({ timeout: 6000 }).catch(() => {}); await page.waitForTimeout(5000); }
    const g = await page.evaluate(() => {
      const f = document.getElementById('demoFrame'), i = document.getElementById('demoEmbed');
      return { frameW: Math.round(i?.getBoundingClientRect().width || 0), cramped: !!f?.classList.contains('cramped') };
    });
    const fr = page.frames().find(f => /demo-app/.test(f.url()));
    let a = null;
    if (fr) a = await fr.evaluate(() => {
      const vis = s => { const e = document.querySelector(s); if (!e) return false; const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && getComputedStyle(e).display !== 'none'; };
      return { iw: window.innerWidth, mq960: matchMedia('(max-width: 960px)').matches,
               m768: matchMedia('(max-width: 768px)').matches,
               rail: vis('#rail'), dashGrid: vis('#dashGrid'), dashListM: vis('#dashListM') };
    }).catch(() => null);
    const layout = !a ? 'NO-IFRAME' : (a.rail || a.dashGrid) ? 'DESKTOP 3-pane' : a.dashListM ? 'mobile' : 'unknown';
    console.log(String(w).padEnd(8), String(g.frameW).padEnd(7), String(a?.iw ?? '-').padEnd(6),
      String(a?.mq960 ?? '-').padEnd(6), String(a?.m768 ?? '-').padEnd(9), layout.padEnd(14),
      String(cover).padEnd(6), String(g.cramped));
  } catch (e) { console.log(String(w).padEnd(8), 'ERROR', String(e).slice(0, 60)); }
  await ctx.close();
}
await browser.close();
