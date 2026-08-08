// Visual proof: what the live demo renders on a phone in LANDSCAPE and in
// Chrome's "Desktop site" mode — the two everyday cases that land in the
// 769px+ band where the demo flips to its desktop 3-pane layout.
import { chromium, devices } from 'playwright';
const URL = process.env.PROBE_URL || 'https://clayrune.io/demo';
const OUT = process.env.SHOT_DIR || '_scratch/demoshots';
const UA_ANDROID = devices['Pixel 7'].userAgent;
const CASES = [
  ['phone-landscape-839', { viewport:{width:839,height:412}, userAgent:UA_ANDROID, isMobile:true, hasTouch:true, deviceScaleFactor:2.6 }],
  ['phone-desktop-site-980', { viewport:{width:980,height:1743}, userAgent:UA_ANDROID, hasTouch:true, deviceScaleFactor:1 }],
  ['tablet-portrait-834', { viewport:{width:834,height:1112}, userAgent:devices['iPad Mini'].userAgent, hasTouch:true, deviceScaleFactor:2 }],
  ['foldable-open-884', { viewport:{width:884,height:1104}, userAgent:UA_ANDROID, hasTouch:true, deviceScaleFactor:2.2 }],
];
const browser = await chromium.launch();
for (const [name, opts] of CASES) {
  const ctx = await browser.newContext(opts);
  const page = await ctx.newPage();
  await page.goto(URL, { waitUntil:'load', timeout:45000 });
  await page.evaluate(() => document.getElementById('demoFrame')?.scrollIntoView({block:'center'}));
  await page.waitForTimeout(4000);
  if (await page.locator('#demoCover').isVisible().catch(()=>false)) {
    await page.locator('#demoCover').click({timeout:6000}).catch(()=>{});
    await page.waitForTimeout(6000);
  }
  const fr = page.frames().find(f => /demo-app/.test(f.url()));
  const info = fr ? await fr.evaluate(() => {
    const vis = s => { const e=document.querySelector(s); if(!e) return false; const r=e.getBoundingClientRect();
      return r.width>0 && r.height>0 && getComputedStyle(e).display!=='none'; };
    return { iw: window.innerWidth, rail: vis('#rail'), surfaces: vis('#surfaces'),
             dashGrid: vis('#dashGrid'), dashListM: vis('#dashListM'), tab: vis('.demo-tabbar,#tabClaydo') };
  }).catch(()=>null) : null;
  await page.screenshot({ path: `${OUT}/${name}.png` });
  console.log(name, JSON.stringify(info));
  await ctx.close();
}
await browser.close();
