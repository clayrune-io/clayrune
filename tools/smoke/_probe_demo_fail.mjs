// Steward probe: when the demo iframe fails to load (the ~20% 504 on
// /demo/demo-app), does the parent page fire /e/demo-fail? That beacon is the
// only way to see the failure from the visitor's side.
import { chromium } from 'playwright';
const URL = process.env.PROBE_URL || 'http://localhost:8791/demo';
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
// Make every demo-app document request fail, the way a Pages 504 does.
await ctx.route(/\/demo\/demo-app(\?|$)/, r => r.fulfill({ status: 504, contentType: 'text/html', body: '<html><body>504</body></html>' }));
const page = await ctx.newPage();
const beacons = [];
ctx.on('request', r => { if (r.url().includes('/e/')) beacons.push(r.url().split('/e/')[1]); });
await page.goto(URL, { waitUntil: 'load', timeout: 45000 });
await page.evaluate(() => document.getElementById('demoFrame')?.scrollIntoView({ block: 'center' }));
await page.waitForTimeout(1500);
await page.locator('#demoCover').click({ timeout: 8000 });
await page.waitForTimeout(3000);
console.log('at +3s  :', JSON.stringify(beacons));
await page.waitForTimeout(14000);
console.log('at +17s :', JSON.stringify(beacons));
console.log(beacons.includes('demo-launch') && beacons.includes('demo-fail') && !beacons.includes('demo-open')
  ? 'PASS - launch recorded, failure recorded, no false demo-open'
  : 'FAIL - unexpected beacon set');
await browser.close();
