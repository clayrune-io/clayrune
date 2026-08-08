// Steward probe: the phone's Back button must step back THROUGH the demo, not
// leave clayrune.io. Drives the real history stack with page.goBack().
// Expected ladder from inside a project with a sheet open:
//   back -> closes sheet | back -> dashboard | back -> exits full screen
//   back -> finally leaves /demo
import { chromium } from 'playwright';
const TARGET = process.env.PROBE_URL || 'http://localhost:8791/demo';
const UA_A = 'Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Mobile Safari/537.36';

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport:{width:412,height:839}, userAgent:UA_A, hasTouch:true, isMobile:true });
const page = await ctx.newPage();
// A prior page so "leaving the demo" has somewhere to go — like arriving from the home page.
await page.goto(TARGET.replace(/\/demo\/?$/, '/'), { waitUntil:'load' }).catch(()=>{});
await page.goto(TARGET, { waitUntil:'load', timeout:45000 });
await page.evaluate(() => document.getElementById('demoFrame')?.scrollIntoView({block:'center'}));
await page.waitForTimeout(3000);
await page.locator('#demoCover').click({ timeout:8000 });
await page.waitForTimeout(6000);

const state = async () => {
  const isFull = await page.evaluate(() => !!document.getElementById('demoFrame')?.classList.contains('is-full'));
  const onDemo = /\/demo\/?$/.test(page.url());
  const fr = page.frames().find(f => /demo-app/.test(f.url()));
  let view = null, sheet = null;
  if (fr) try {
    view = await fr.evaluate(() => document.body.classList.contains('view-project') ? 'project' : 'dashboard');
    sheet = await fr.evaluate(() => ['sheetOverlay','settingsOverlay','drawerOverlay','palOverlay','claydoOverlay']
      .some(id => document.getElementById(id)?.classList.contains('on')));
  } catch {}
  return { onDemo, isFull, view, sheet };
};
const show = (t, s) => console.log(`${t.padEnd(22)} onDemoPage=${String(s.onDemo).padEnd(5)} fullScreen=${String(s.isFull).padEnd(5)} view=${String(s.view).padEnd(9)} overlayOpen=${s.sheet}`);

const fr = page.frames().find(f => /demo-app/.test(f.url()));
await fr.locator('.coach-next, .demo-coach button').first().click({ timeout:6000 }).catch(()=>{});
await page.waitForTimeout(2500);
show('in project', await state());
await fr.evaluate(() => document.getElementById('drawerOverlay')?.classList.add('on'));
await page.waitForTimeout(400);
show('overlay opened', await state());

for (const label of ['back #1 (overlay)','back #2 (project)','back #3 (fullscreen)','back #4 (leave)']) {
  await page.goBack({ waitUntil:'commit' }).catch(()=>{});
  await page.waitForTimeout(1400);
  show(label, await state());
}
await browser.close();
