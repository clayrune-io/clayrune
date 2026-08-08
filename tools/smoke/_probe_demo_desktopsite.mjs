// Steward probe: a phone in Chrome's "Desktop site" mode reports a ~980px
// layout viewport, so a width-only breakpoint hands it the desktop 3-pane on a
// handheld. Asserts the coarse-pointer arm catches it WITHOUT dragging real
// desktops or touch laptops into the phone shell.
import { chromium } from 'playwright';
const URL = process.env.PROBE_URL || 'http://localhost:8791/demo';
const UA_A = 'Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Mobile Safari/537.36';
const UA_D = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36';
const CASES = [
  ['phone desktop-site 980  ', { viewport:{width:980,height:1743}, userAgent:UA_A, hasTouch:true },  'mobile'],
  ['phone portrait 412      ', { viewport:{width:412,height:839},  userAgent:UA_A, hasTouch:true },  'mobile'],
  ['phone landscape 839     ', { viewport:{width:839,height:412},  userAgent:UA_A, hasTouch:true },  'mobile'],
  ['tablet landscape 1024   ', { viewport:{width:1024,height:768}, userAgent:UA_A, hasTouch:true },  'mobile'],
  ['touch laptop 1366       ', { viewport:{width:1366,height:768}, userAgent:UA_D, hasTouch:true },  'desktop'],
  ['desktop 1280 no touch   ', { viewport:{width:1280,height:800}, userAgent:UA_D, hasTouch:false }, 'desktop'],
  ['desktop 1600 no touch   ', { viewport:{width:1600,height:900}, userAgent:UA_D, hasTouch:false }, 'desktop'],
];
const browser = await chromium.launch();
let fail = 0;
for (const [label, opts, want] of CASES) {
  const ctx = await browser.newContext(opts);
  const page = await ctx.newPage();
  await page.goto(URL, { waitUntil:'load', timeout:45000 });
  await page.evaluate(() => document.getElementById('demoFrame')?.scrollIntoView({block:'center'}));
  await page.waitForTimeout(3500);
  if (await page.locator('#demoCover').isVisible().catch(()=>false)) {
    await page.locator('#demoCover').click({timeout:6000}).catch(()=>{});
    await page.waitForTimeout(5500);
  }
  const fr = page.frames().find(f => /demo-app/.test(f.url()));
  const a = fr ? await fr.evaluate(() => {
    const vis = s => { const e=document.querySelector(s); if(!e) return false; const r=e.getBoundingClientRect();
      return r.width>0 && r.height>0 && getComputedStyle(e).display!=='none'; };
    return { iw: window.innerWidth, grid: vis('#dashGrid'), list: vis('#dashListM'), tab: vis('.demo-tabbar,#tabClaydo') };
  }).catch(()=>null) : null;
  const got = !a ? 'NO-IFRAME' : a.grid ? 'desktop' : (a.list && a.tab) ? 'mobile' : 'broken';
  const ok = got === want;
  if (!ok) fail++;
  console.log(`${ok?'PASS':'FAIL'}  ${label} appW=${String(a?.iw??'-').padEnd(5)} want=${want.padEnd(8)} got=${got}`);
  await ctx.close();
}
await browser.close();
console.log(fail ? `\n${fail} FAILURE(S)` : '\nALL PASS');
