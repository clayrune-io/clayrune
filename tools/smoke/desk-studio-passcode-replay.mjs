#!/usr/bin/env node
/**
 * Studio's real engine caller + real shared passcode prompt, with slow mocked
 * 201 creation and 200 replay replies. No vendor, live API or real passcode.
 * desk-v1-live-render replaces humanProofFetch; this smoke covers their seam.
 * Optional --proof-source=<path> serves a historical prompt for regression QA.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const assets = loadStaticJsCss(root);
const source = process.argv.find(a => a.startsWith('--proof-source='));
if (source) assets['/static/js/human-proof-modal.js'][1] = readFileSync(source.split('=').slice(1).join('='), 'utf8');
const html = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const passcode = 'smoke-only-passcode';
let bad = 0;
function check(cond, message) {
  console[cond ? 'log' : 'error'](`  ${cond ? 'PASS' : 'FAIL'} ${message}`);
  if (!cond) bad++;
}
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    for (const status of [201, 200]) {
      const tag = `${width}px HTTP ${status}`;
      const ctx = await browser.newContext({ viewport: { width, height: 900 } });
      const page = await ctx.newPage();
      const errors = [];
      const posts = [];
      const releases = [];
      let deny = true;
      page.on('pageerror', e => errors.push(e.message));
      await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
      const render = { render_id: 'rnd-replay', status: 'rendering', progress: { ready: 0, total: 1 }, outputs: [], scenes: [] };
      await page.route('**/*', async route => {
        const req = route.request();
        const path = new URL(req.url()).pathname;
        const json = (body, code = 200) => route.fulfill({ status: code, contentType: 'application/json', body: JSON.stringify(body) });
        if (path === '/') return route.fulfill({ contentType: 'text/html', body: html });
        if (assets[path]) return route.fulfill({ contentType: assets[path][0], body: assets[path][1] });
        if (path === '/api/projects') return json([]);
        if (path === '/api/local-auth/status') return json({ configured: true });
        if (path === '/api/desk/engines') return json({ engines: [{ id: 'google', label: 'Google', connected: { ready: true }, job_limit_usd: 2,
          models: [{ model_id: 'veo', label: 'Veo', kind: 'video', aspect_ratios: ['16:9'] }] }] });
        if (path === '/api/desk/engines/render/estimate') return json({ estimate: { usd: 0.4 }, plan: { clips: 1 }, refusal: null });
        if (path === '/api/desk/engines/renders' && req.method() === 'GET') return json({ render: null });
        if (path === '/api/desk/engines/renders/rnd-replay') return json({ render: { ...render, status: 'ready', progress: { ready: 1, total: 1 } } });
        if (path === '/api/desk/engines/renders' && req.method() === 'POST') {
          posts.push(req.postDataJSON());
          if (deny) { deny = false; return json({ error: 'bad_passcode' }, 403); }
          // Before the busy fix, retyping while the first render is held sent
          // this same idempotency key again. A replay answers before the 201.
          if (posts.length > 2) return json({ render, replay: true }, 200);
          await new Promise(r => releases.push(r));
          return json({ render, replay: status === 200 }, status);
        }
        return route.abort();
      });
      try {
        await page.goto('http://mc.smoke.test/');
        await page.waitForFunction(() => window.DeskV1Engines && window.humanProofFetch);
        await page.evaluate(() => {
          window.deskV1Open();
          const host = document.createElement('div');
          document.getElementById('desk-v1-body').replaceChildren(host);
          window.DeskV1Engines.mountVideoRender(host, { owner: { kind: 'studio', id: 'smoke-replay' } });
        });
        await page.waitForSelector('[data-eng-render-btn]:not([disabled])');
        await page.click('[data-eng-render-btn]');
        await page.waitForSelector('[data-modal-id^="__human-proof-"]');
        const mid = await page.locator('[data-modal-id^="__human-proof-"]').getAttribute('data-modal-id');
        const prompt = page.locator(`[data-modal-id="${mid}"]`);
        await prompt.locator('input').fill('wrong-smoke-code');
        await prompt.locator('.btn-add').click();
        await page.waitForFunction(id => document.querySelector(`[data-modal-id="${id}"]`).textContent.includes('Wrong dashboard passcode.'), mid);
        check(await prompt.locator('input').inputValue() === '' && !await prompt.locator('.btn-add').isDisabled(), `${tag}: wrong passcode clears the field and permits correction`);
        await prompt.locator('input').fill(passcode);
        await prompt.locator('.btn-add').click();
        await page.waitForFunction(() => document.querySelector('[data-modal-id^="__human-proof-"] input').value === '');
        const deadline = Date.now() + 5000;
        while (!releases.length && Date.now() < deadline) await page.waitForTimeout(20);
        if (!releases.length) throw new Error(`${tag}: accepted render POST never reached the mock`);
        check(await prompt.locator('.btn-add').isDisabled() && /Working/.test(await prompt.textContent()), `${tag}: slow request shows busy feedback`);
        // Same re-entry that was possible before 3f7b689c. Use the real exported
        // handler, even though the new disabled input prevents typing in the UI.
        await page.evaluate(async ({ id, value }) => {
          document.querySelector(`[data-modal-id="${id}"] input`).value = value;
          await window._hpSubmit(id);
        }, { id: mid, value: passcode });
        check(posts.length === 2, `${tag}: only one accepted POST after correcting the passcode (got ${posts.length - 1})`);
        const remainingPrompts = await page.locator('[data-modal-id^="__human-proof-"]').count();
        check(remainingPrompts <= 1, `${tag}: re-entry does not stack prompts`);
        if (source) console.log(`  HISTORICAL ${tag}: immediate duplicate 200 leaves ${remainingPrompts} prompt(s) before slow original reply`);
        check(posts[1].passcode === passcode && posts[1].owner.kind === 'studio' && posts[1].shown_total === 0.4 && !!posts[1].idempotency_key,
          `${tag}: accepted request retains the Studio body and typed proof`);
        releases.forEach(r => r());
        await page.waitForSelector(`[data-modal-id="${mid}"]`, { state: 'detached' });
        await page.waitForSelector('[data-eng-render-status][data-status="rendering"]');
        check(await page.locator('[data-modal-id^="__human-proof-"]').count() === 0 && !await page.evaluate(() => document.body.classList.contains('human-proof-active')),
          `${tag}: reply closes all proof UI and clears its overlay class`);
        check(await page.locator('[data-eng-render-btn]').isDisabled(), `${tag}: caller displays the render and blocks another paid start`);
        await page.waitForSelector('[data-eng-render-status][data-status="ready"]', { timeout: 6000 });
        check(true, `${tag}: same render polls to ready through the real caller`);
        check(errors.length === 0, `${tag}: no runtime exceptions (${errors.join('; ')})`);
      } finally {
        releases.forEach(r => r());
        await ctx.close();
      }
    }
  }
} finally {
  await browser.close();
}
console.log(`desk-studio-passcode-replay: ${bad} failure(s)`);
process.exitCode = bad ? 1 : 0;
