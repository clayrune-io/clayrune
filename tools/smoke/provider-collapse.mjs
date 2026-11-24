#!/usr/bin/env node
/**
 * Settings > Connections: one collapsible card per vendor (provider-collapse.js).
 *
 * Pins, at 1440 and 390px:
 *   1. Every vendor is a card; a healthy vendor starts COLLAPSED and its body
 *      is not visible; the header shows name, status pill, Default badge (only
 *      on the default) and the model a new run launches with.
 *   2. A default vendor that is not signed in starts EXPANDED (needs attention).
 *   3. Clicking the header expands the unchanged row; Claude's API-credits card
 *      lives inside Claude's body, not as a sibling.
 *   4. Open/closed survives a full Settings re-render (the status-poll path),
 *      is stored in sessionStorage, and an explicit collapse of an
 *      attention vendor sticks.
 *   5. Live search finds a vendor by name, expands a collapsed match without
 *      storing it, hides non-matching cards, and clears when search ends.
 *   6. The batch-install tick is on the header of an uninstalled vendor, so it
 *      works while the card is collapsed.
 *   7. First-run setup rows are not wrapped (no .prov-card) — its look is unchanged.
 *
 * Hermetic: page + static assets served from THIS checkout, /api/* canned.
 * RUN: node tools/smoke/provider-collapse.mjs
 * Screenshots: MC_SMOKE_SHOT_DIR=<dir> writes before/after-style captures.
 */
import { readFileSync, existsSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const STATIC_ROOT = resolve(__dirname, '..', '..', 'static');
const ORIGIN = 'http://mc.smoke.test';
const SHOT_DIR = process.env.MC_SMOKE_SHOT_DIR || '';
if (SHOT_DIR) mkdirSync(SHOT_DIR, { recursive: true });

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg));

const mk = (name, display_name, over = {}) => ({ name, display_name, installed: true, version: '2.1.0', auth_status: 'ok',
  remote_login: true, install_hint: '', capabilities: {}, ...over });
const providers = [
  mk('claude', 'Claude Code'),
  mk('codex', 'Codex CLI'),
  mk('gemini', 'Gemini CLI', { version: '0.9.0', auth_status: 'not_logged_in' }),
  mk('qwen', 'Qwen Code', { installed: false, version: null, auth_status: 'unknown', install_hint: 'npm install -g @qwen-code/qwen-code' }),
];
const models = { claude: 'opus', codex: 'tier:balanced', gemini: '', qwen: 'qwen-code' };
const prefView = () => ({ default_provider: config.default_provider, project_provider: '',
  providers: providers.map((p) => ({ name: p.name, display_name: p.display_name, models: [], tiers: {}, efforts: [],
    defaults: {}, overrides: {}, automatic_background_model: '',
    effective: { provider: p.name, model: models[p.name], model_source: 'global', effort: '' } })) });
let config = { default_provider: 'claude' };

const browser = await chromium.launch();

async function session(width, defaultProvider) {
  config = { default_provider: defaultProvider };
  const ctx = await browser.newContext({ viewport: { width, height: 1100 } });
  const page = await ctx.newPage();
  await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message || String(e)));
  await page.route('**/*', (route) => {
    const req = route.request(); const url = new URL(req.url()); const path = url.pathname;
    const json = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
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
    if (path === '/api/projects') return json([]);
    if (path === '/api/local-auth/status') return json({ configured: true });
    if (path === '/api/config') return json(config);
    if (path === '/api/engine/preferences') return json(prefView());
    if (path === '/api/agent/providers') return json({ providers: providers.map((p) => ({ ...p, default: p.name === config.default_provider })), default: config.default_provider });
    if (path === '/api/api-credits/status') return json({ settings: { mode: 'off', secret_name: '', stop_pct: 90, reset_day: 1 }, plan: { status: 'detected', label: 'Max 20x', credit_usd: 200 }, cycle: {}, ledger: {} });
    if (path === '/api/secrets') return json({ secrets: [], locked: false });
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openSettings === 'function' && !!window.ProviderCollapse && !!window.ApiCredits, { timeout: 20000 });
  return { page, errors, ctx };
}

const openProviders = async (page) => {
  await page.evaluate(() => window.closeModalById && window.closeModalById('__settings'));
  await page.evaluate(() => window.openSettings());
  await page.waitForSelector('#settings-providers-section', { state: 'attached', timeout: 10000 });
  await page.evaluate(() => {
    window.drillSettings('providers');
    const pane = document.querySelector('#settings-detail .settings-detail-pane[data-cat="providers"]');
    const i = [...pane.children].filter((el) => el.classList.contains('settings-section')).findIndex((s) => s.id === 'settings-providers-section');
    if (i >= 0) window.drillSettingsSub(i);
  });
  await page.waitForFunction(() => { const s = document.getElementById('settings-providers-section'); return s && s.offsetParent !== null; }, { timeout: 5000 });
  await page.waitForSelector('.prov-card .prov-card-model', { timeout: 10000 }); // preferences loaded
};
const cardState = (page) => page.evaluate(() => Object.fromEntries([...document.querySelectorAll('.prov-card')].map((c) => [c.dataset.provCard, {
  open: c.classList.contains('is-open'), bodyVisible: c.querySelector('.prov-card-body').offsetParent !== null,
  name: c.querySelector('.prov-card-name').textContent, state: c.querySelector('.prov-card-state').textContent,
  badge: !!c.querySelector('.prov-card-badge'), model: (c.querySelector('.prov-card-model') || {}).textContent || '',
  aria: c.querySelector('.prov-card-toggle').getAttribute('aria-expanded') }])));
const shot = async (page, name) => { if (SHOT_DIR) await page.screenshot({ path: join(SHOT_DIR, name) }); };

try {
  for (const width of [1440, 390]) {
    console.log(`── ${width}px, default = claude (healthy)`);
    const { page, errors, ctx } = await session(width, 'claude');
    await openProviders(page);
    let s = await cardState(page);
    check(Object.keys(s).length === providers.length, `one card per vendor (${Object.keys(s).length})`, `cards: ${Object.keys(s)}`);
    check(['claude', 'codex', 'gemini', 'qwen'].every((n) => !s[n].open && !s[n].bodyVisible && s[n].aria === 'false'),
      'healthy vendors start collapsed, bodies not visible', `states: ${JSON.stringify(s)}`);
    check(s.claude.name === 'Claude Code' && s.claude.state === 'Signed in' && s.claude.badge && s.claude.model === 'opus',
      'Claude header: name, "Signed in", Default badge, model "opus"', `claude header: ${JSON.stringify(s.claude)}`);
    check(!s.codex.badge && s.codex.model === 'balanced', 'Codex: no Default badge, tier prefix stripped from the model', `codex: ${JSON.stringify(s.codex)}`);
    check(s.gemini.state === 'Installed, not signed in' && s.qwen.state === 'Not installed', 'pills read "Installed, not signed in" / "Not installed"', `pills: ${s.gemini.state} / ${s.qwen.state}`);
    check(await page.locator('.prov-card[data-prov-card="qwen"] .prov-card-head .settings-prov-install-sel').isVisible(),
      'uninstalled vendor keeps its batch-install tick on the collapsed header', 'no header tick for qwen');
    check(await page.locator('.settings-prov-install-sel').count() === 1, 'exactly one batch-install tick (not duplicated in the body)', 'duplicate install ticks');
    await shot(page, `collapsed-${width}.png`);

    await page.click('#prov-card-toggle-claude');
    s = await cardState(page);
    check(s.claude.open && s.claude.bodyVisible && s.claude.aria === 'true', 'clicking the header expands Claude', `claude: ${JSON.stringify(s.claude)}`);
    const inside = await page.evaluate(() => {
      const body = document.querySelector('.prov-card[data-prov-card="claude"] .prov-card-body');
      return { row: !!body.querySelector('.prov-row[data-provider="claude"] .prov-row-actions input[type=radio]'),
        signIn: !!body.querySelector('.prov-sign-in'), check: !!body.querySelector('.prov-check'),
        model: !!body.querySelector('#engine-pref-global-claude-model, select[data-engine-field="model"]'),
        credits: !!body.querySelector('#api-credits-card'), siblingCredits: !!document.querySelector('.prov-rows > #api-credits-card') };
    });
    check(inside.row && inside.signIn && inside.check && inside.credits && !inside.siblingCredits,
      'Claude body holds the row (Default, Sign in, Check status) and the API-credits card; no sibling card', `body: ${JSON.stringify(inside)}`);
    await page.waitForTimeout(300);
    await shot(page, `claude-open-${width}.png`);

    // The status-poll path: a full rebuild of the Settings body.
    await page.evaluate(() => window._renderSettings());
    await page.waitForSelector('.prov-card .prov-card-model');
    s = await cardState(page);
    check(s.claude.open && s.claude.bodyVisible && !s.codex.open, 'open set survives a full Settings re-render', `after render: ${JSON.stringify(s)}`);
    const stored = await page.evaluate(() => JSON.parse(sessionStorage.getItem('mc.providerCardsOpen.v1') || '{}'));
    check(stored.claude === true && !('codex' in stored), 'choice is kept in sessionStorage (explicit picks only)', `stored: ${JSON.stringify(stored)}`);

    await page.click('#prov-card-toggle-claude');
    await page.evaluate(() => window._renderSettings());
    await page.waitForSelector('.prov-card .prov-card-model');
    s = await cardState(page);
    check(!s.claude.open, 'collapsing also survives a re-render', `claude: ${JSON.stringify(s.claude)}`);

    // Search lives on the category list; a fresh open always lands there.
    await page.evaluate(() => window.closeModalById('__settings'));
    await page.evaluate(() => window.openSettings());
    await page.waitForSelector('#settings-search', { state: 'visible' });
    // Search: name match expands a collapsed vendor; clearing restores it.
    await page.fill('#settings-search', 'codex');
    await page.waitForFunction(() => document.getElementById('settings-body').dataset.view === 'search');
    s = await page.evaluate(() => Object.fromEntries([...document.querySelectorAll('.prov-card')].map((c) => [c.dataset.provCard,
      { hidden: c.offsetParent === null, open: c.classList.contains('is-open'), body: c.querySelector('.prov-card-body').offsetParent !== null }])));
    check(!s.codex.hidden && s.codex.open && s.codex.body, 'search "codex" shows the Codex card expanded', `codex: ${JSON.stringify(s.codex)}`);
    check(s.claude.hidden && s.gemini.hidden && s.qwen.hidden, 'search hides the non-matching vendor cards', `others: ${JSON.stringify(s)}`);
    check((await page.evaluate(() => JSON.parse(sessionStorage.getItem('mc.providerCardsOpen.v1') || '{}'))).codex === undefined,
      'search expansion is transient, not stored', 'search wrote codex into the store');
    // A match that lives only INSIDE a collapsed body (the install hint of Qwen).
    await page.fill('#settings-search', '@qwen-code/qwen-code');
    s = await page.evaluate(() => ({ qwenOpen: document.querySelector('.prov-card[data-prov-card="qwen"]').classList.contains('is-open'),
      qwenShown: document.querySelector('.prov-card[data-prov-card="qwen"]').offsetParent !== null,
      none: !document.getElementById('settings-no-results').classList.contains('settings-hidden') }));
    check(s.qwenOpen && s.qwenShown && !s.none, 'a match inside a collapsed body reveals (auto-expands) that vendor', `inner match: ${JSON.stringify(s)}`);
    await page.fill('#settings-search', '');
    await page.evaluate(() => window.drillSettings('providers'));
    await page.evaluate(() => {
      const pane = document.querySelector('#settings-detail .settings-detail-pane[data-cat="providers"]');
      const i = [...pane.children].filter((el) => el.classList.contains('settings-section')).findIndex((s) => s.id === 'settings-providers-section');
      if (i >= 0) window.drillSettingsSub(i);
    });
    s = await cardState(page);
    check(Object.values(s).every((c) => !c.open), 'leaving search collapses what search opened', `after search: ${JSON.stringify(s)}`);
    check(await page.evaluate(() => [...document.querySelectorAll('.prov-card')].every((c) => c.offsetParent !== null)), 'all cards visible again after search', 'a card stayed hidden after search');
    check(errors.length === 0, 'no uncaught page errors', `errors: ${errors}`);
    await ctx.close();

    console.log(`── ${width}px, default = gemini (not signed in)`);
    const b = await session(width, 'gemini');
    await openProviders(b.page);
    s = await cardState(b.page);
    check(s.gemini.open && s.gemini.bodyVisible && !s.claude.open && !s.codex.open,
      'a not-signed-in DEFAULT vendor starts expanded; the others stay collapsed', `states: ${JSON.stringify(s)}`);
    await b.page.click('#prov-card-toggle-gemini');
    await b.page.evaluate(() => window._renderSettings());
    await b.page.waitForSelector('.prov-card .prov-card-model');
    s = await cardState(b.page);
    check(!s.gemini.open, 'collapsing an attention vendor sticks across a re-render', `gemini: ${JSON.stringify(s.gemini)}`);
    await shot(b.page, `attention-collapsed-${width}.png`);
    check(b.errors.length === 0, 'no uncaught page errors', `errors: ${b.errors}`);
    await b.ctx.close();
  }
} catch (e) {
  fail('smoke aborted: ' + (e && e.message ? e.message : e));
} finally {
  await browser.close();
}
console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
