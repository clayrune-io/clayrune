#!/usr/bin/env node
/**
 * First-run "Help count Clayrune installs" step (static/js/setup-update-ping-step.js).
 * Regression for the Fenn review 2026-11-05: setupNext used to fire updatePingChoose
 * and advance without looking at the result, so a failed OFF save (HTTP 500) left the
 * server ON while the UI moved on to "Make it yours".
 *
 *   1. Toggle OFF, /choice answers 500 -> stays on the step, error shown, buttons
 *      usable again, exactly one POST, enabled:false.
 *   2. Retry after the failure, /choice answers 200 -> advances to "Make it yours".
 *   3. Next clicked twice while the save is in flight -> ONE POST, buttons disabled
 *      meanwhile, advances once.
 *   4. A network failure (route aborted) behaves like a 500.
 *
 * Hermetic: real index.html + static/js, /api/* canned. RUN: node tools/smoke/update-ping-setup-step.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const STATIC = loadStaticJsCss(REPO_ROOT);
const ORIGIN = 'http://mc.smoke.test';
const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

const CLAYRUNE_PROJECT = {
  id: 'clayrune', name: 'Clayrune', status: 'active', domain: 'general',
  _is_onboarding_project: true, emoji: '', description: '', summary: 'tour',
  current_task: 'Tour Clayrune', next_action: '', blocked: false, blocked_reason: null,
  activity_log: [], backlog: [], project_path: '/smoke/clayrune',
  last_updated: '2026-09-14T00:00:00Z', last_updated_relative: 'today',
  last_completed: null, live_agent: null, display_order: 0, provider: 'claude',
  use_streaming_agent: true, roster: [],
};
const ONE_PROVIDER_OK = {
  providers: [{ name: 'claude', display_name: 'Claude Code', installed: true, in_use: true, default: true, auth_status: 'ok' }],
  default: 'claude',
};

let choiceMode;      // 'ok' | '500' | 'abort' | 'hold'
let choiceCalls;     // enabled values POSTed
let held;            // resolver for a held response

const browser = await chromium.launch();
try {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return json([CLAYRUNE_PROJECT]);
    if (path === '/api/config') return json({ setup_completed: false, default_provider: 'claude' });
    if (path === '/api/characters') return json([]);
    if (path === '/api/agent/providers') return json(ONE_PROVIDER_OK);
    if (path === '/api/local-auth/status') return json({ configured: false });
    if (path === '/api/update-ping/status') return json({ enabled: true, disclosed: false, env_disabled: false, update_available: false });
    if (path === '/api/update-ping/choice') {
      choiceCalls.push(JSON.parse(req.postData() || '{}').enabled);
      if (choiceMode === 'abort') return route.abort();
      if (choiceMode === '500') return json({ error: 'failed to save config' }, 500);
      if (choiceMode === 'hold') await new Promise((r) => { held = r; });
      return json({ ok: true });
    }
    return route.abort();
  });
  const title = () => page.evaluate(() => document.querySelector('#setup-overlay .wt-title')?.textContent || '');
  const statusText = () => page.evaluate(() => document.getElementById('setup-ping-status')?.textContent || '');
  const buttonsDisabled = () => page.evaluate(() => [...document.querySelectorAll('#setup-overlay .wt-actions button')].map((b) => b.disabled));

  choiceMode = '500'; choiceCalls = [];
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#setup-overlay .wt-title', { timeout: 15000 });
  await page.click('#setup-overlay .wt-btn-primary');                 // Get started (connections skipped)
  await page.waitForFunction(() => document.querySelector('#setup-overlay .wt-title')?.textContent === 'Help count Clayrune installs');

  // 1. failed OFF save
  await page.click('#setup-ping-toggle');                              // ON -> OFF
  await page.click('#setup-overlay .wt-btn-primary');                  // Next
  await page.waitForFunction(() => /Could not save/.test(document.getElementById('setup-ping-status')?.textContent || ''), { timeout: 5000 })
    .then(() => ok('failed OFF save shows an error on the step')).catch(() => fail('no error shown after a failed save: ' + choiceCalls));
  (await title()) === 'Help count Clayrune installs' ? ok('stays on the step after a failed save') : fail('advanced past a failed save: ' + (await title()));
  JSON.stringify(choiceCalls) === '[false]' ? ok('exactly one POST, enabled:false') : fail('choice calls: ' + JSON.stringify(choiceCalls));
  (await buttonsDisabled()).some(Boolean) ? fail('buttons left disabled after failure') : ok('buttons usable again for a retry');
  (await page.locator('#setup-ping-toggle.on').count()) === 0 ? ok('toggle still shows OFF (the person\'s answer is kept)') : fail('toggle reset');

  // 2. retry succeeds
  choiceMode = 'ok';
  await page.click('#setup-overlay .wt-btn-primary');
  await page.waitForFunction(() => document.querySelector('#setup-overlay .wt-title')?.textContent === 'Make it yours', { timeout: 5000 })
    .then(() => ok('retry after the failure advances to Make it yours')).catch(async () => fail('did not advance after a good save: ' + (await title())));
  JSON.stringify(choiceCalls) === '[false,false]' ? ok('retry POSTed enabled:false again') : fail('choice calls: ' + JSON.stringify(choiceCalls));

  // 3. double submit while in flight; 4. network failure
  await page.click('#setup-overlay .wt-actions button:has-text("Back")');
  await page.waitForFunction(() => document.querySelector('#setup-overlay .wt-title')?.textContent === 'Help count Clayrune installs');
  choiceMode = 'abort'; choiceCalls = [];
  await page.click('#setup-overlay .wt-btn-primary');
  await page.waitForFunction(() => /Could not save/.test(document.getElementById('setup-ping-status')?.textContent || ''), { timeout: 5000 })
    .then(() => ok('a network failure is treated like a failed save')).catch(() => fail('network failure advanced or showed nothing'));
  (await title()) === 'Help count Clayrune installs' ? ok('network failure: stays on the step') : fail('advanced on network failure');

  choiceMode = 'hold'; choiceCalls = []; held = null;
  await page.click('#setup-overlay .wt-btn-primary');
  await page.waitForFunction(() => document.getElementById('setup-ping-status')?.textContent === 'Saving…');
  (await buttonsDisabled()).every(Boolean) ? ok('buttons disabled while the save is in flight') : fail('buttons not disabled in flight');
  await page.evaluate(() => window.setupNext());                       // a second submit, bypassing the disabled button
  await page.waitForTimeout(150);
  choiceCalls.length === 1 ? ok('second submit while in flight sent no second POST') : fail('double POST: ' + JSON.stringify(choiceCalls));
  held();
  await page.waitForFunction(() => document.querySelector('#setup-overlay .wt-title')?.textContent === 'Make it yours', { timeout: 5000 })
    .then(() => ok('advances exactly once after the held save succeeds')).catch(async () => fail('did not advance: ' + (await title())));
  pageErrors.length ? fail('page errors: ' + pageErrors.join(' | ')) : ok('no page errors');
} catch (e) { fail('smoke threw: ' + e.message); }
finally { await browser.close(); }
if (bad) { console.error(`\n${bad} failure(s)`); process.exit(1); }
console.log('\nall assertions passed');
