#!/usr/bin/env node
// Real SPA, hermetic API: never uses the operator's vault or credentials.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';
import { loadFixtures } from './desk-v1-fixture-api.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const index = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const statics = loadStaticJsCss(root);
const fx = loadFixtures();
const origin = 'http://mc.smoke.test';
const shots = resolve(root, '_scratch/vault-unlock-link');
mkdirSync(shots, { recursive: true });
const engine = { id: 'mock', label: 'Fixture engine', currency: 'usd', job_limit_usd: 5,
  connected: { ready: true }, models: [
    { model_id: 'image', kind: 'image', label: 'Image', aspect_ratios: ['1:1'] },
    { model_id: 'video', kind: 'video', label: 'Video', aspect_ratios: ['16:9'] },
  ] };
const popup = '.vault-unlock-popup[open]';
const browser = await chromium.launch({ headless: true });
try {
  for (const width of [1440, 390]) {
    const ctx = await browser.newContext({ viewport: { width, height: 844 } });
    const page = await ctx.newPage();
    const errors = [], unlocks = [];
    let locked = true, statusFails = false, priceLocked = true;
    page.on('pageerror', (e) => errors.push(e.message));
    await page.route('**/*', async (route) => {
      const req = route.request(), path = new URL(req.url()).pathname;
      const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: index });
      if (statics[path]) return route.fulfill({ contentType: statics[path][0], body: statics[path][1] });
      if (path === '/api/projects') return J(fx.projects.map(p => ({ ...p, status: 'active', display_order: 0, backlog: [], activity_log: [], live_agent: null })));
      if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true });
      if (path === '/api/config/models') return J({ models: [] });
      if (path === '/api/features') return J({});
      if (path === '/api/characters') return J([]);
      if (path === '/api/local-auth/status') return J({ configured: true });
      if (path === '/api/secrets/vault-lock') return J({ state: locked ? 'locked' : 'unlocked' }, statusFails ? 503 : 200);
      if (path === '/api/secrets/vault-lock/unlock') {
        const body = req.postDataJSON(); unlocks.push(body);
        if (body.passcode !== 'fixture-code') return J({ error: 'bad_passcode' }, 403);
        if (body.passphrase !== 'fixture-pass') return J({ error: 'wrong_passphrase' }, 403);
        locked = false; return J({ ok: true, state: 'unlocked' });
      }
      if (path === '/api/desk/engines') return J({ engines: [engine] });
      if (path.endsWith('/estimate')) return priceLocked
        ? J({ code: 'not_connected', error: 'Your vault is locked. Unlock it; your saved key is still there.', vault_locked: true }, 409)
        : J({ estimate: { usd: .1 }, job_limit_usd: 5 });
      if (path === '/api/desk/engines/renders') return J({ code: 'not_connected', error: 'Saved credentials unavailable', vault_locked: true }, 409);
      if (path === '/api/desk/engagement/poll') return J({ platforms: { x: { state: 'not_connected', message: 'Saved credentials unavailable', vault_locked: true } } });
      return route.abort();
    });
    await page.goto(origin + '/#unlock-vault', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector(popup + ' [data-vg-pass]');
    assert.equal(await page.locator(popup).count(), 1, 'cold load opens exactly one popup');
    await page.evaluate(() => { window.openVaultUnlock(); window.openVaultUnlock(); });
    assert.equal(await page.locator(popup).count(), 1);
    assert.equal(await page.locator(popup + ' [data-vg-passcode]').count(), 1);
    const fits = await page.locator(popup).evaluate(d => {
      const b = d.getBoundingClientRect();
      return b.left >= 0 && b.right <= innerWidth && b.top >= 0 && b.bottom <= innerHeight && d.scrollWidth <= d.clientWidth;
    });
    assert(fits, width + ' dialog fits without horizontal overflow');
    await page.screenshot({ path: resolve(shots, 'unlock_' + width + '.png') });
    await page.fill(popup + ' [data-vg-pass]', 'fixture-pass');
    await page.click(popup + ' [data-vg-unlock]');
    assert.equal(unlocks.length, 0, 'no request without passcode');
    await page.fill(popup + ' [data-vg-passcode]', 'wrong');
    await page.click(popup + ' [data-vg-unlock]');
    await page.waitForFunction(() => document.querySelector('.vault-unlock-popup [data-vg-status]').textContent.includes('Wrong dashboard passcode'));
    assert.equal(await page.inputValue(popup + ' [data-vg-pass]'), '');
    assert.equal(await page.inputValue(popup + ' [data-vg-passcode]'), '');
    await page.fill(popup + ' [data-vg-pass]', 'fixture-pass');
    await page.fill(popup + ' [data-vg-passcode]', 'fixture-code');
    await page.click(popup + ' [data-vg-unlock]');
    await page.waitForSelector(popup, { state: 'detached' });
    assert.deepEqual(unlocks.at(-1), { passphrase: 'fixture-pass', passcode: 'fixture-code' });
    assert.equal(new URL(page.url()).hash, '');
    assert.match(await page.locator('#toast-container').innerText(), /Vault unlocked/);
    await page.evaluate(() => window.openVaultUnlock());
    await page.waitForFunction(() => document.querySelector('#toast-container').textContent.includes('already unlocked'));
    assert.equal(await page.locator(popup).count(), 0);
    locked = true;
    await page.evaluate(() => { location.hash = 'unlock-vault'; });
    await page.waitForSelector(popup + ' [data-vg-pass]');
    await page.keyboard.press('Escape');
    await page.waitForSelector(popup, { state: 'detached' });
    // A localhost link printed by an agent is rebased to this dashboard's host.
    await page.evaluate(() => {
      const host = document.createElement('div'); host.id = 'link-test';
      host.innerHTML = window.formatAgentText('Unlock here: http://localhost:5199/#unlock-vault.');
      document.body.appendChild(host);
    });
    assert.equal(await page.locator('#link-test [data-open-vault-unlock]').getAttribute('href'), '#unlock-vault');
    assert.match(await page.locator('#link-test').innerText(), /Unlock vault\./);
    await page.click('#link-test [data-open-vault-unlock]');
    await page.waitForSelector(popup + ' [data-vg-pass]');
    await page.click(popup + ' [data-vault-close]');
    assert(await page.locator('#link-test [data-open-vault-unlock]').evaluate(e => e === document.activeElement), 'focus returns to trigger');
    await page.evaluate(() => window.showToast('Your vault is locked. Unlock it.'));
    await page.click('#toast-container .toast-actions button');
    await page.waitForSelector(popup + ' [data-vg-pass]');
    await page.click(popup + ' [data-vault-close]');
    statusFails = true;
    await page.evaluate(() => window.openVaultUnlock());
    await page.waitForFunction(() => document.querySelector('.vault-unlock-popup').textContent.includes('Could not check'));
    assert.equal(await page.locator(popup + ' [data-vg-pass]').count(), 0, 'unreachable status never claims unlocked');
    await page.click(popup + ' [data-vault-close]'); statusFails = false;
    // Studio uses the real mount, shared API and structured flag (even without vault words).
    await page.evaluate(() => {
      const host = document.createElement('div'); host.id = 'studio-test'; document.body.appendChild(host);
      window.DeskV1Engines.mountVideoRender(host, { owner: { kind: 'studio', id: 'fixture' } });
    });
    await page.waitForSelector('#studio-test [data-eng-reason] [data-open-vault-unlock]');
    await page.click('#studio-test [data-eng-reason] [data-open-vault-unlock]');
    await page.waitForSelector(popup + ' [data-vg-pass]');
    await page.click(popup + ' [data-vault-close]');
    priceLocked = false;
    await page.click('#studio-test [data-eng-reprice]');
    await page.waitForSelector('#studio-test [data-eng-render-btn]:not([disabled])');
    await page.evaluate(() => { window.humanProofFetch = async (url, init) => { const r = await fetch(url, init); return { ok: r.ok, status: r.status, body: await r.json() }; }; });
    await page.click('#studio-test [data-eng-render-btn]');
    await page.waitForSelector('#studio-test [data-eng-error] [data-open-vault-unlock]');
    await page.evaluate(() => {
      const host = document.createElement('div'); host.id = 'connection-test'; document.body.appendChild(host);
      host.innerHTML = window.DeskV1ConnectionStatus.accountHTML({ id: 'x', platform: 'x', capability: 'direct', read_via: 'api', publish: { vault_locked: true } });
    });
    assert.equal(await page.locator('#connection-test [data-open-vault-unlock]').count(), 1);
    assert.match(await page.evaluate(() => window.DeskV1ConnectCopy.body(window.VaultUnlockUI.buttonHTML({ vault_locked: true }))), />Unlock vault</);
    await page.evaluate(() => {
      const state = { projects: [{ id: 'fixture', name: 'Fixture' }], campaigns: [], channels: [], conversations: [], engagementCoverage: { fixture: [{ label: 'Reading held', detail: 'Saved credentials unavailable', vault_locked: true }] } };
      window.DeskV1Store.state = () => state;
      window.DeskV1Store.live = () => true;
      window.deskV1EngagementGate = () => false;
      window.deskV1LoadEngagement = async () => {};
      const host = document.createElement('div'); host.id = 'engagement-test'; document.body.appendChild(host);
      window.deskV1RenderEngagement(host, { projectId: 'fixture' });
    });
    assert.equal(await page.locator('#engagement-test .desk-v1-eng-gap [data-open-vault-unlock]').count(), 1);
    await page.click('#engagement-test [data-eng-check]');
    await page.waitForSelector('#engagement-test [data-eng-check-line] [data-open-vault-unlock]');
    await page.click('#engagement-test [data-eng-check-line] [data-open-vault-unlock]');
    await page.waitForSelector(popup + ' [data-vg-pass]');
    await page.click(popup + ' [data-vault-close]');
    assert.deepEqual(errors.filter(e => !/Failed to fetch|net::ERR|aborted|EventSource/i.test(e)), []);
    console.log(`PASS ${width}: cold load, hashchange, chat, toast, credentials, failure, Studio price/Render, Connections, engagement, layout`);
    await ctx.close();
  }
} finally { await browser.close(); }
