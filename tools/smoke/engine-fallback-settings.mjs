#!/usr/bin/env node
/**
 * MC-961 opt-in engine fallback — the Settings control + the blocked-run
 * deep-link, the two UI halves of the feature (backend swap logic is covered
 * by tests/test_engine_fallback.py).
 *
 * WHY THIS EXISTS
 * ----------------
 * Ron's placement refinement (2026-09-26): the fallback-order editor lives in
 * Settings > Agent > Advanced — collapsed by default, never in first-run, and
 * shows a plain hint instead of an empty list editor when fewer than 2
 * providers are installed+signed-in. The ONLY discovery path besides hand-
 * browsing Agent > Advanced is the blocked-run card's "Set up a fallback"
 * button, which must deep-link straight into this control, expanded.
 *
 * Pins:
 *   1. <2 signed-in providers -> a one-line hint, no add/reorder/remove UI.
 *   2. >=2 signed-in providers -> the editor renders saved entries, reorders
 *      and removes them, and offers only NOT-yet-added signed-in vendors in
 *      "Add vendor" — every mutation persists via PUT /api/config.
 *   3. openSettingsToEngineFallback() (what the blocked-run card's button
 *      calls) opens Settings, drills to Agent, and lands directly on the
 *      Advanced section expanded — not the category's sub-list.
 *   4. The move/remove/add/deep-link handlers are reachable from a plain
 *      onclick="" attribute (i.e. actually exported on window) — settings-
 *      drill.js is loaded `type="module"`, so a handler only reachable as a
 *      bare module-scope function throws ReferenceError on click.
 *
 * Hermetic like settings-providers.mjs: page + static assets from THIS
 * checkout, /api/* canned, everything else aborted.
 *
 * RUN: node tools/smoke/engine-fallback-settings.mjs
 */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const STATIC_ROOT = resolve(REPO_ROOT, 'static');
const ORIGIN = 'http://mc.smoke.test';

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg));

const ONE_PROVIDER = [
  { name: 'claude', display_name: 'Claude Code', installed: true, auth_status: 'ok',
    remote_login: true, install_hint: '', capabilities: {}, default: true, in_use: true },
  { name: 'gemini', display_name: 'Gemini CLI', installed: false, auth_status: 'unknown',
    remote_login: false, install_hint: '', capabilities: {}, default: false, in_use: false },
];
const TWO_PROVIDERS = [
  { name: 'claude', display_name: 'Claude Code', installed: true, auth_status: 'ok',
    remote_login: true, install_hint: '', capabilities: {}, default: true, in_use: true },
  { name: 'gemini', display_name: 'Gemini CLI', installed: true, auth_status: 'ok',
    remote_login: false, install_hint: '', capabilities: {}, default: false, in_use: false },
  { name: 'codex', display_name: 'Codex CLI', installed: true, auth_status: 'ok',
    remote_login: true, install_hint: '', capabilities: {}, default: false, in_use: false },
];

let providers = ONE_PROVIDER;
let config = { default_provider: 'claude', engine_fallback_order: [] };
const puts = [];

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1000, height: 900 } });
const page = await ctx.newPage();
await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

await page.route('**/*', (route) => {
  const req = route.request();
  const url = new URL(req.url());
  const path = url.pathname;
  const json = (body, status = 200) => route.fulfill({
    status, contentType: 'application/json', body: JSON.stringify(body) });
  if (path === '/' || path === '/index.html')
    return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8',
      body: readFileSync(resolve(STATIC_ROOT, 'index.html'), 'utf8') });
  if (path.startsWith('/static/')) {
    const file = normalize(join(STATIC_ROOT, path.slice('/static/'.length)));
    if (file.startsWith(STATIC_ROOT) && existsSync(file)) {
      const type = file.endsWith('.js') ? 'text/javascript; charset=utf-8'
        : file.endsWith('.css') ? 'text/css; charset=utf-8' : 'application/octet-stream';
      return route.fulfill({ status: 200, contentType: type, body: readFileSync(file, 'utf8') });
    }
    return route.abort();
  }
  if (path === '/api/projects') return json([]);
  if (path === '/api/local-auth/status') return json({ configured: true });
  if (path === '/api/config' && req.method() === 'PUT') {
    const body = JSON.parse(req.postData() || '{}');
    puts.push(body);
    config = { ...config, ...body };
    return json({ ok: true });
  }
  if (path === '/api/config') return json(config);
  if (path === '/api/agent/providers')
    return json({ providers: providers.map(p => ({ ...p, default: p.name === config.default_provider })),
                  default: config.default_provider });
  return route.abort();
});

// MC-995: move/remove/add now route through saveSetting() -> humanProofFetch()
// instead of firing PUT /api/config directly — answer the passcode modal.
const answerHumanProofModal = async (timeout = 5000) => {
  await page.waitForSelector('[data-modal-id^="__human-proof-"]', { timeout });
  await page.evaluate(() => {
    const win = document.querySelector('[data-modal-id^="__human-proof-"]');
    const modalId = win.dataset.modalId;
    document.getElementById(`hp-passcode-${modalId}`).value = 'smoke-dash-passcode';
    window._hpSubmit(modalId);
  });
};

const openAgentSubs = async () => {
  await page.evaluate(() => { window.closeModalById && window.closeModalById('__settings'); });
  await page.evaluate(() => window.openSettings());
  await page.waitForSelector('#engine-fallback-section', { state: 'attached', timeout: 10000 });
  await page.evaluate(() => window.drillSettings('agent'));
};

const drillToAdvanced = async () => {
  await openAgentSubs();
  await page.evaluate(() => {
    const idx = [...document.querySelectorAll('[data-cat="agent"] .settings-section-title')]
      .findIndex(t => t.textContent.trim() === 'Advanced');
    window.drillSettingsSub(idx);
  });
  await page.waitForFunction(() => {
    const s = document.getElementById('engine-fallback-section');
    return s && s.offsetParent !== null;
  }, { timeout: 5000 });
};

const readEF = () => page.evaluate(() => {
  const sec = document.getElementById('engine-fallback-section');
  return {
    hint: (sec.querySelector('.settings-hint') || {}).textContent || '',
    rows: [...sec.querySelectorAll('[data-ef-idx]')].map(r => r.querySelector('.settings-label').textContent),
    addSelect: !!document.getElementById('engine-fallback-add-provider'),
    addOptions: [...(document.getElementById('engine-fallback-add-provider')?.options || [])].map(o => o.value),
  };
});

try {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openSettings === 'function'
    && typeof window.openSettingsToEngineFallback === 'function'
    && typeof window.moveEngineFallback === 'function'
    && typeof window.removeEngineFallback === 'function'
    && typeof window.addEngineFallback === 'function', { timeout: 20000 });
  ok('openSettingsToEngineFallback / moveEngineFallback / removeEngineFallback / addEngineFallback are all reachable from onclick="" (exported on window)');

  // ── 1. Not in first-run ──────────────────────────────────────────────────
  const firstRunHTML = readFileSync(resolve(STATIC_ROOT, 'index.html'), 'utf8');
  check(!/setup-overlay[\s\S]*?engine[_-]fallback/i.test(firstRunHTML.split('id="setup-overlay"')[1] || ''),
    '"engine fallback" does not appear inside the first-run setup overlay markup',
    'first-run overlay markup mentions engine fallback');

  // ── 2. <2 signed-in providers -> hint only ──────────────────────────────
  providers = ONE_PROVIDER;
  await drillToAdvanced();
  let st = await readEF();
  check(st.hint === 'Fallback needs a second AI provider signed in.' && st.rows.length === 0 && !st.addSelect,
    '<2 signed-in providers: plain hint, no list editor, no Add-vendor select',
    `<2-provider state wrong: ${JSON.stringify(st)}`);

  // ── 3. Advanced starts collapsed (sub-list view, not auto-expanded) ─────
  await openAgentSubs();
  const subsView = await page.evaluate(() => !!document.querySelector('#settings-view-subs, .settings-subs-list')
    || document.getElementById('engine-fallback-section')?.offsetParent === null);
  check(subsView, 'landing on Agent shows the sub-section list — Advanced is not auto-expanded',
    'Advanced rendered expanded on a cold Agent-category open');

  // ── 4. >=2 signed-in providers -> full editor, reorder/remove/add wired ─
  providers = TWO_PROVIDERS;
  config = { default_provider: 'claude', engine_fallback_order: [{ provider: 'gemini', model: '' }] };
  await page.evaluate(() => window._ensureAgentProviders(true)); // _agentProviders is cached; force-refresh past the stale ONE_PROVIDER list
  await drillToAdvanced();
  st = await readEF();
  check(st.rows.length === 1 && st.rows[0].includes('Gemini CLI'),
    'saved fallback entry renders (1. Gemini CLI)',
    `rows: ${JSON.stringify(st.rows)}`);
  // claude (the active default) is a legitimate fallback TARGET too --
  // resolve_fallback() skips an entry matching whichever vendor is currently
  // blocked, so a self-referencing entry is inert, never wrong. Only
  // gemini (already in the list) is excluded.
  check(st.addSelect && st.addOptions.sort().join(',') === 'claude,codex',
    'Add-vendor excludes only the signed-in provider already in the list (gemini)',
    `add options: ${JSON.stringify(st.addOptions)}`);

  puts.length = 0;
  await page.evaluate(() => {
    document.getElementById('engine-fallback-add-provider').value = 'codex';
    window.addEngineFallback();
  });
  await answerHumanProofModal();
  await page.waitForFunction(() => document.querySelectorAll('#engine-fallback-section [data-ef-idx]').length === 2);
  check(puts.length === 1 && JSON.stringify(puts[0].engine_fallback_order) ===
    JSON.stringify([{ provider: 'gemini', model: '' }, { provider: 'codex', model: '' }]),
    'Add persists the new order via PUT /api/config',
    `PUT bodies after add: ${JSON.stringify(puts)}`);

  puts.length = 0;
  await page.evaluate(() => window.moveEngineFallback(1, -1));
  await answerHumanProofModal();
  await page.waitForFunction(() => document.querySelector('#engine-fallback-section [data-ef-idx="0"] .settings-label')
    ?.textContent.includes('Codex'));
  check(puts.length === 1 && puts[0].engine_fallback_order[0].provider === 'codex',
    'Move-up swaps order and persists',
    `PUT bodies after move: ${JSON.stringify(puts)}`);

  puts.length = 0;
  await page.evaluate(() => window.removeEngineFallback(0));
  await answerHumanProofModal();
  await page.waitForFunction(() => document.querySelectorAll('#engine-fallback-section [data-ef-idx]').length === 1);
  check(puts.length === 1 && puts[0].engine_fallback_order.length === 1
    && puts[0].engine_fallback_order[0].provider === 'gemini',
    'Remove drops the entry and persists',
    `PUT bodies after remove: ${JSON.stringify(puts)}`);

  // ── 5. Blocked-run deep-link lands directly on Advanced, expanded ──────
  await page.evaluate(() => window.closeModalById('__settings'));
  await page.waitForFunction(() => document.getElementById('__settings') === null
    || document.getElementById('__settings')?.style.display === 'none', { timeout: 5000 }).catch(() => {});
  await page.evaluate(() => window.openSettingsToEngineFallback());
  await page.waitForFunction(() => {
    const s = document.getElementById('engine-fallback-section');
    return s && s.offsetParent !== null;
  }, { timeout: 5000 });
  ok('openSettingsToEngineFallback() from a cold close lands directly on the expanded Advanced section');

  pageErrors.length === 0 ? ok('no uncaught page errors throughout')
    : pageErrors.forEach(e => fail('uncaught: ' + e));
} finally {
  await ctx.close();
  await browser.close();
}

console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
