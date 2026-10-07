#!/usr/bin/env node
// MC-1065: real SPA/modules with hermetic vault and engine responses. No vendor calls.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';
import { loadFixtures } from './desk-v1-fixture-api.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const index = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const statics = loadStaticJsCss(root), fx = loadFixtures();
const shots = resolve(root, '_scratch/engine-vault-recovery');
mkdirSync(shots, { recursive: true });
const popup = '.vault-unlock-popup[open]';
const lockError = { code: 'not_connected', error: 'Your vault is locked. Unlock it; your sign-in is still saved.', vault_locked: true };
const browser = await chromium.launch({ headless: true });
try {
  for (const width of [1440, 390]) {
    const ctx = await browser.newContext({ viewport: { width, height: 844 } });
    const page = await ctx.newPage();
    const errors = [], calls = [];
    let locked = true, connected = true, priceMode = 'error', engineFails = false;
    let holdNext = false, held = null;
    page.on('pageerror', e => errors.push(e.message));
    const engine = () => ({ id: 'higgsfield_mcp', label: 'Higgsfield (sign in)', currency: 'credits', job_limit_credits: 100,
      connected: locked ? { ready: false, state: 'vault_locked', reason: lockError.error }
        : { ready: connected, state: connected ? 'connected' : 'not_connected' },
      models: [
        { model_id: 'video', kind: 'video', label: 'Video', aspect_ratios: ['16:9', '9:16'] },
        { model_id: 'image', kind: 'image', label: 'Image', aspect_ratios: ['1:1', '9:16'] },
      ] });
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), path = url.pathname;
      const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: index });
      if (statics[path]) return route.fulfill({ contentType: statics[path][0], body: statics[path][1] });
      if (path === '/api/projects') return J(fx.projects.map(p => ({ ...p, status: 'active', display_order: 0, backlog: [], activity_log: [], live_agent: null })));
      if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true });
      if (path === '/api/config/models') return J({ models: [] });
      if (path === '/api/features') return J({});
      if (path === '/api/characters') return J([]);
      if (path === '/api/local-auth/status') return J({ configured: true });
      if (path.startsWith('/api/desk/engines') || path.startsWith('/api/secrets/vault-lock')) {
        calls.push({ path, method: req.method(), body: req.postData() ? req.postDataJSON() : null, project: url.searchParams.get('project_id') });
      }
      if (path === '/api/secrets/vault-lock') return J({ state: locked ? 'locked' : 'unlocked' });
      if (path === '/api/secrets/vault-lock/unlock') {
        const body = req.postDataJSON();
        if (body.passphrase !== 'fixture-pass' || body.passcode !== 'fixture-code') return J({ error: 'bad_passcode' }, 403);
        locked = false; return J({ ok: true, state: 'unlocked' });
      }
      if (path === '/api/desk/engines') return engineFails ? J({ error: 'Engine status unavailable' }, 503) : J({ engines: [engine()] });
      if (path.endsWith('/estimate')) {
        if (holdNext) { holdNext = false; held = route; return; }
        if (priceMode === 'other') return J({ error: 'Storyboard has no scenes' }, 409);
        if (locked) return priceMode === 'refusal' ? J({ estimate: { credits: 7 }, refusal: lockError }) : J(lockError, 409);
        return J({ estimate: { credits: 7 }, job_limit_credits: 100 });
      }
      if (path === '/api/desk/engines/renders' && req.method() === 'GET') return J({ render: null });
      return route.abort();
    });
    await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => !!window.DeskV1Engines && !!window.DeskV1VaultGate && !!window.VaultUnlock);
    await page.evaluate(() => {
      const area = document.createElement('div'); area.id = 'recovery-test';
      area.style.cssText = 'position:fixed;inset:0;overflow:auto;z-index:100;background:white;padding:8px;';
      document.body.appendChild(area);
      for (const id of ['video-a', 'video-b', 'image']) {
        const host = document.createElement('div'); host.id = id; area.appendChild(host);
        if (id === 'image') window.DeskV1Engines.mountImageGenerate(host, { key: 'recovery', projectId: 'fixture' });
        else window.DeskV1Engines.mountVideoRender(host, { owner: { kind: 'studio', id }, projectId: 'fixture' });
      }
      window.__unlockEvents = 0;
      window.addEventListener('vault-unlocked', () => window.__unlockEvents++);
    });
    await page.waitForSelector('#video-a [data-eng-estimate][data-state="refused"]', { state: 'attached' });
    await page.fill('#image [data-eng-prompt]', 'Keep this picture description');
    await page.locator('#image [data-eng-prompt]').dispatchEvent('change');
    await page.waitForSelector('#image [data-eng-estimate][data-state="refused"]', { state: 'attached' });
    const assertLocked = async () => {
      for (const id of ['video-a', 'video-b', 'image']) {
        assert.equal(await page.locator(`#${id} [data-open-vault-unlock]`).count(), 1, id + ' has exactly one unlock action');
        assert.equal(await page.locator(`#${id} [data-eng-vault-message]`).count(), 1, id + ' has exactly one lock message');
        assert.match(await page.locator(`#${id} [data-eng-engine] option:checked`).innerText(), /not connected/);
        assert(await page.locator(`#${id} [data-eng-render-btn]`).isDisabled());
      }
    };
    const assertReady = async () => {
      for (const id of ['video-a', 'video-b', 'image']) {
        await page.waitForSelector(`#${id} [data-eng-render-btn]:not([disabled])`);
        assert.equal(await page.locator(`#${id} [data-open-vault-unlock]`).count(), 0);
        assert.doesNotMatch(await page.locator(`#${id}`).innerText(), /vault is locked|not connected/);
        assert.match(await page.locator(`#${id} [data-eng-render-btn]`).innerText(), /7 credits/);
      }
      assert.equal(await page.inputValue('#image [data-eng-prompt]'), 'Keep this picture description');
      assert.equal(await page.inputValue('#video-a [data-eng-ratio]'), '9:16');
    };
    await page.selectOption('#video-a [data-eng-ratio]', '9:16');
    await page.waitForSelector('#video-a [data-eng-estimate][data-state="refused"]', { state: 'attached' });
    await assertLocked();
    // Both the HTTP-error and successful-quote-with-refusal paths coalesce.
    priceMode = 'refusal';
    await page.click('#video-a [data-eng-reprice]');
    await page.waitForSelector('#video-a [data-eng-estimate][data-state="refused"] [data-eng-usd]');
    await assertLocked();
    priceMode = 'other';
    await page.click('#video-a [data-eng-reprice]');
    await page.waitForFunction(() => document.querySelector('#video-a').textContent.includes('Storyboard has no scenes'));
    assert.equal(await page.locator('#video-a [data-open-vault-unlock]').count(), 1, 'unrelated refusal remains alongside the single lock prompt');
    priceMode = 'error';
    await page.click('#video-a [data-eng-reprice]');
    await page.waitForFunction(() => !document.querySelector('#video-a').textContent.includes('Storyboard has no scenes') && document.querySelector('#video-a [data-eng-estimate]').dataset.state === 'refused');
    await page.screenshot({ path: resolve(shots, 'locked_' + width + '.png') });
    // An old locked quote arriving after recovery must not overwrite the fresh price.
    holdNext = true;
    await page.click('#video-a [data-eng-reprice]');
    await page.waitForFunction(() => document.querySelector('#video-a [data-eng-estimate]').dataset.state === 'pricing');
    for (let i = 0; !held && i < 500; i++) await new Promise(resolve => setTimeout(resolve, 10));
    assert(held, 'the old price request reached the server');
    await page.click('#video-a [data-open-vault-unlock]');
    await page.waitForSelector(popup + ' [data-vg-pass]');
    const beforeWrong = calls.filter(c => c.path === '/api/desk/engines').length;
    await page.fill(popup + ' [data-vg-pass]', 'fixture-pass');
    await page.fill(popup + ' [data-vg-passcode]', 'wrong');
    await page.click(popup + ' [data-vg-unlock]');
    await page.waitForFunction(() => document.querySelector('.vault-unlock-popup [data-vg-status]').textContent.includes('Wrong dashboard passcode'));
    assert.equal(await page.evaluate(() => window.__unlockEvents), 0, 'failed unlock emits no success');
    assert.equal(calls.filter(c => c.path === '/api/desk/engines').length, beforeWrong);
    await page.fill(popup + ' [data-vg-pass]', 'fixture-pass');
    await page.fill(popup + ' [data-vg-passcode]', 'fixture-code');
    await page.click(popup + ' [data-vg-unlock]');
    await page.waitForSelector(popup, { state: 'detached' });
    await assertReady();
    assert.equal(await page.evaluate(() => window.__unlockEvents), 1, 'popup success emits exactly once');
    assert.deepEqual(calls.filter(c => c.path.endsWith('/unlock')).at(-1).body, { passphrase: 'fixture-pass', passcode: 'fixture-code' });
    await held.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify(lockError) }); held = null;
    await page.waitForTimeout(250);
    await assertReady();
    // Same-origin events from the inline gate and Secrets helper refresh every card.
    locked = true;
    await page.evaluate(() => window.dispatchEvent(new Event('vault-unlocked')));
    await page.waitForSelector('#video-a [data-eng-estimate][data-state="refused"]', { state: 'attached' });
    await assertLocked();
    await page.evaluate(() => {
      const host = document.createElement('div'); host.id = 'inline'; document.querySelector('#recovery-test').appendChild(host);
      window.DeskV1VaultGate.mount(host);
    });
    await page.fill('#inline [data-vg-pass]', 'fixture-pass');
    await page.fill('#inline [data-vg-passcode]', 'fixture-code');
    await page.click('#inline [data-vg-unlock]');
    await assertReady();
    await page.evaluate(() => document.querySelector('#inline').remove());
    // Vault unlocked elsewhere: status says already unlocked, no credential POST.
    locked = true;
    await page.evaluate(() => window.dispatchEvent(new Event('vault-unlocked')));
    await page.waitForSelector('#video-a [data-eng-estimate][data-state="refused"]', { state: 'attached' });
    await assertLocked();
    locked = false;
    const unlockCount = calls.filter(c => c.path.endsWith('/unlock')).length;
    await page.click('#video-a [data-open-vault-unlock]');
    await assertReady();
    assert.equal(await page.locator(popup).count(), 0);
    assert.equal(calls.filter(c => c.path.endsWith('/unlock')).length, unlockCount, 'already unlocked sends no credentials');
    // External unlock also recovers on returning focus, without a polling loop.
    locked = true;
    await page.evaluate(() => window.dispatchEvent(new Event('vault-unlocked')));
    await page.waitForSelector('#video-a [data-eng-estimate][data-state="refused"]', { state: 'attached' });
    locked = false;
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await assertReady();
    // Re-read failure cannot enable Render on an old quote; retry can recover.
    engineFails = true;
    await page.evaluate(() => window.openVaultUnlock());
    await page.waitForFunction(() => document.querySelector('#video-a').textContent.includes('Engine status unavailable'));
    assert(await page.locator('#video-a [data-eng-render-btn]').isDisabled());
    engineFails = false;
    await page.evaluate(() => window.openVaultUnlock());
    await assertReady();
    connected = false;
    await page.evaluate(() => window.openVaultUnlock());
    await page.waitForSelector('#video-a [data-eng-estimate][data-state="ok"]');
    await page.waitForFunction(() => document.querySelector('#video-a [data-eng-engine] option:checked').textContent.includes('not connected'));
    assert(await page.locator('#video-a [data-eng-render-btn]').isDisabled(), 'a price alone cannot enable an unready engine');
    connected = true;
    await page.evaluate(() => window.openVaultUnlock());
    await assertReady();
    await page.evaluate(() => {
      document.querySelector('#toast-container')?.replaceChildren();
      document.querySelector('#recovery-test').scrollTop = 0;
    });
    await page.screenshot({ path: resolve(shots, 'ready_' + width + '.png') });
    const reads = calls.filter(c => c.path === '/api/desk/engines').length;
    await page.waitForTimeout(500);
    assert.equal(calls.filter(c => c.path === '/api/desk/engines').length, reads, 'no new polling loop');
    assert(calls.filter(c => c.path === '/api/desk/engines').every(c => c.project === 'fixture'), 'refresh preserves project scope');
    assert.equal(calls.filter(c => c.method === 'POST' && /\/(renders|jobs)$/.test(c.path)).length, 0, 'recovery never submits paid work');
    assert.deepEqual(errors.filter(e => !/Failed to fetch|net::ERR|aborted|EventSource/i.test(e)), []);
    console.log(`PASS ${width}: one prompt, popup/inline/already-unlocked/focus recovery, all cards, late quote, failed status, readiness, no paid retry/poll`);
    await ctx.close();
  }
} finally { await browser.close(); }
