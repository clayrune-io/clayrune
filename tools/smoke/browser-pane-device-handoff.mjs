// Real throwaway browser, two dashboard viewers; no saved profiles or sign-in.
import { chromium } from 'playwright';
import { spawn } from 'child_process';
import { mkdtempSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import net from 'net';
import assert from 'node:assert/strict';
import { registerBrowserSmokeProcess as register } from './browser-smoke-processes.mjs';

const repo = new URL('../../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const tmp = mkdtempSync(join(tmpdir(), 'bp-device-handoff-'));
const port = await new Promise(resolve => {
  const server = net.createServer();
  server.listen(0, '127.0.0.1', () => { const p = server.address().port; server.close(() => resolve(p)); });
});
await register(process.pid, 'Browser device handoff smoke', 'node tools/smoke/browser-pane-device-handoff.mjs');
const harness = spawn(process.env.PYTHON || 'python', [join(repo, 'tools/smoke/browser_pane_harness.py'), String(port), tmp],
  { cwd: repo, stdio: ['pipe', 'pipe', 'pipe'] });
await register(harness.pid, 'Device handoff harness', 'browser_pane_harness.py (isolated)');
let output = '';
harness.stderr.on('data', data => { output += data; });
const sitePort = await new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error(output)), 30000);
  harness.stdout.on('data', data => {
    output += data;
    const ready = /READY \d+ (\d+)/.exec(output);
    if (ready) { clearTimeout(timer); resolve(Number(ready[1])); }
  });
});
const base = `http://127.0.0.1:${port}`;
const url = `http://127.0.0.1:${sitePort}/ua.html`;
const browserServer = await chromium.launchServer();
await register(browserServer.process().pid, 'Device handoff viewers', 'Playwright Chromium (isolated)');
const browser = await chromium.connect(browserServer.wsEndpoint());
const api = async (path, body) => {
  const res = await fetch(base + path, body && { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  assert(res.ok, `${path}: ${res.status}`);
  return res.json();
};
let sid;
async function viewer(width, height) {
  const page = await browser.newPage({ viewport: { width, height } });
  await page.goto(base);
  await page.waitForFunction(() => window.attachBrowserPane);
  return page;
}
async function expectMode(mobile) {
  const deadline = Date.now() + 15000;
  let state, text;
  while (Date.now() < deadline) {
    state = await api('/_harness/session/' + sid);
    const probe = await api('/_harness/device/' + sid);
    text = JSON.stringify(probe);
    const d = probe.device;
    if (state.device_mode === (mobile ? 'mobile' : 'desktop') && d &&
        d.site === (mobile ? 'MOBILE-SITE' : 'DESKTOP-SITE') && d.mobile === mobile &&
        d.touch === (mobile ? 5 : 0) && d.width === state.view[0] && d.height === state.view[1]) {
      console.log(`PASS ${mobile ? 'phone' : 'desktop'} viewer: correct metrics mode and UA-served HTML`);
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`handoff failed: ${JSON.stringify(state)} ${text}`);
}
try {
  // Launch explicitly ephemeral, then attach by session id from each device.
  sid = (await api('/api/browser/launch', { project_id: 'smoke', url, ephemeral: true, mobile: true })).session_id;
  const phone = await viewer(412, 915);
  await phone.evaluate(s => window.attachBrowserPane(s, 'smoke'), sid);
  await expectMode(true);
  const desktop = await viewer(1440, 1000);
  await desktop.evaluate(s => window.attachBrowserPane(s, 'smoke'), sid);
  await expectMode(false);
  // The phone sheet never resized or closed while its user was on the PC.
  // Model tab visibility explicitly: headless pages need not become hidden
  // when another page is created. Dispatch only the real return event.
  await phone.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
    window.dispatchEvent(new Event('focus'));
  });
  await expectMode(false);
  await phone.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expectMode(true);
  const phoneHitsBefore = (await api('/_harness/uahits')).hits.length;
  await phone.evaluate(() => {
    document.dispatchEvent(new Event('visibilitychange'));
    window.dispatchEvent(new Event('focus'));
  });
  await new Promise(resolve => setTimeout(resolve, 700));
  assert.equal((await api('/_harness/uahits')).hits.length, phoneHitsBefore,
    'same-mode phone visibility/focus must not reload the remote page');
  console.log('PASS same-mode phone visibility/focus does not reload');
  await desktop.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expectMode(false);
  const hitsBefore = (await api('/_harness/uahits')).hits.length;
  await desktop.evaluate(() => {
    document.dispatchEvent(new Event('visibilitychange'));
    window.dispatchEvent(new Event('focus'));
  });
  await new Promise(resolve => setTimeout(resolve, 700));
  await expectMode(false);
  assert.equal((await api('/_harness/uahits')).hits.length, hitsBefore,
    'same-mode visibility/focus must not reload the remote page');
  console.log('PASS same-mode visibility/focus does not reload');
  // A hidden viewer reclaims the session at the SAME pane size. Resize-only
  // deduplication must not suppress this takeover after the other device won.
  await phone.locator('[data-bp="hide"]').click();
  await phone.locator('#chip-bp-' + sid).click();
  await expectMode(true);
  await desktop.locator('[data-bp="minimize"]').click();
  await desktop.locator('#chip-bp-' + sid).click();
  await expectMode(false);
} finally {
  if (sid) await api('/api/browser/stop', { session_id: sid });
  await browser.close();
  await browserServer.close();
  const exited = new Promise(resolve => harness.once('exit', resolve));
  harness.stdin.end();
  await exited;
  rmSync(tmp, { recursive: true, force: true });
}
