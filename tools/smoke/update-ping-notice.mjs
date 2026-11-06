#!/usr/bin/env node
/**
 * Update-check ping UI (static/js/update-ping-notice.js, Ron 2026-11-05).
 *
 *   1. Existing install, disclosure not yet shown -> one-time notice with the
 *      exact-what-is-sent text and the toggle pre-ON; the page records "seen".
 *   2. Flipping the toggle in the notice POSTs /api/update-ping/choice {enabled:false}.
 *   3. "Got it" (dismiss) closes it and sends NO choice call (dismiss keeps ON).
 *   4. Disclosed already -> no notice at all.
 *   5. Environment hard-off (DO_NOT_TRACK / CLAYRUNE_NO_PING) -> no notice.
 *   6. Settings -> System shows the "Update check" row with the toggle ON.
 *
 * Hermetic like settings-update-row.mjs: static assets from this checkout,
 * /api/update-ping/* canned, everything else aborted. RUN: node tools/smoke/update-ping-notice.mjs
 */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const STATIC_ROOT = resolve(__dirname, '..', '..', 'static');
const ORIGIN = 'http://mc.smoke.test';
const ok = (m) => console.log('  \u2713 ' + m);
let bad = 0;
const fail = (m) => { console.error('  \u2717 ' + m); bad++; };

let pingStatus, calls;
const reset = (s) => { pingStatus = { enabled: true, disclosed: false, env_disabled: false,
  update_available: false, latest_version: '', current_version: 'v2.3.0', ...s }; calls = []; };

const browser = await chromium.launch();
try {
  const ctx = await browser.newContext({ viewport: { width: 1000, height: 800 } });
  const page = await ctx.newPage();
  await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html')
      return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: readFileSync(resolve(STATIC_ROOT, 'index.html'), 'utf8') });
    if (path.startsWith('/static/')) {
      const file = normalize(join(STATIC_ROOT, path.slice('/static/'.length)));
      if (file.startsWith(STATIC_ROOT) && existsSync(file)) {
        const type = file.endsWith('.js') ? 'text/javascript; charset=utf-8' : file.endsWith('.css') ? 'text/css; charset=utf-8' : 'application/octet-stream';
        return route.fulfill({ status: 200, contentType: type, body: readFileSync(file, 'utf8') });
      }
      return route.abort();
    }
    if (path === '/api/projects') return json([{ id: 'p1', name: 'Existing', path: '/tmp/existing', status: 'active' }]);
    if (path === '/api/config') return json({ setup_completed: true });
    if (path === '/api/local-auth/status') return json({ configured: true });
    if (path === '/api/update-ping/status') return json(pingStatus);
    if (path === '/api/update-ping/seen') { calls.push('seen'); pingStatus.disclosed = true; return json({ ok: true }); }
    if (path === '/api/update-ping/choice') { calls.push('choice:' + JSON.parse(req.postData() || '{}').enabled); return json({ ok: true }); }
    if (path === '/api/system/update/cached') return json({ update_available: false });
    return route.abort();
  });
  const boot = async () => {
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => typeof window.checkUpdatePingNotice === 'function' && typeof window.openSettings === 'function', { timeout: 20000 });
  };

  // 1 + 3
  reset({});
  await boot();
  await page.waitForSelector('#upn-card', { timeout: 8000 });
  const txt = await page.textContent('#upn-card');
  /random install ID/.test(txt) && /Nothing else/.test(txt) && /DO_NOT_TRACK=1/.test(txt) ? ok('notice states exactly what is sent and how to turn it off') : fail('notice text wrong: ' + txt);
  (await page.locator('#upn-toggle.on').count()) ? ok('notice toggle is pre-ON') : fail('notice toggle not pre-ON');
  await page.waitForFunction(() => true); await page.waitForTimeout(200);
  calls.includes('seen') ? ok('display recorded via POST /api/update-ping/seen') : fail('seen not recorded: ' + calls);
  await page.click('#upn-dismiss');
  !(await page.locator('#upn-card').count()) ? ok('Got it closes the notice') : fail('notice still open after dismiss');
  !calls.some((c) => c.startsWith('choice')) ? ok('dismissing sends no choice (stays ON)') : fail('dismiss changed the choice: ' + calls);

  // 2
  reset({});
  await boot();
  await page.waitForSelector('#upn-card', { timeout: 8000 });
  await page.click('#upn-toggle');
  await page.waitForTimeout(200);
  calls.includes('choice:false') && !(await page.locator('#upn-toggle.on').count()) ? ok('toggling off POSTs choice {enabled:false} and shows OFF') : fail('toggle off not recorded: ' + calls);

  // 4
  reset({ disclosed: true });
  await boot();
  await page.waitForTimeout(3500);
  !(await page.locator('#upn-card').count()) ? ok('already disclosed: no notice') : fail('notice shown after disclosure');

  // 5
  reset({ env_disabled: true, enabled: false });
  await boot();
  await page.waitForTimeout(3500);
  !(await page.locator('#upn-card').count()) && !calls.includes('seen') ? ok('env hard-off: no notice, nothing recorded') : fail('env hard-off still showed notice / recorded seen');

  // 6
  reset({ disclosed: true });
  await boot();
  await page.evaluate(() => window.openSettings());
  await page.waitForSelector('#update-ping-toggle', { state: 'attached', timeout: 10000 });
  (await page.locator('#update-ping-toggle.on').count()) ? ok('Settings row present, toggle ON by default') : fail('Settings toggle not ON');
  const cat = await page.evaluate(() => document.getElementById('update-ping-toggle').closest('.settings-detail-pane')?.dataset.cat);
  cat === 'system' ? ok('row lives under Settings -> System') : fail('row under wrong category: ' + cat);

  pageErrors.length ? fail('page errors: ' + pageErrors.join(' | ')) : ok('no page errors');
} catch (e) { fail('smoke threw: ' + e.message); }
finally { await browser.close(); }
if (bad) { console.error(`\n${bad} failure(s)`); process.exit(1); }
console.log('\nall assertions passed');
