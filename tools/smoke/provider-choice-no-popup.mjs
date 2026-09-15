#!/usr/bin/env node
/**
 * Provider choice belongs at install time (installer/install.sh,
 * install.ps1), not as an in-app popup — Ron 2026-09-14, after an existing
 * install got ambushed by exactly that popup on a routine dashboard refresh.
 * This locks in the fix at the DOM level, real headless boot (real
 * index.html + real static/js/*.js, no network), same hermetic shape as
 * walkthrough.mjs / drag-to-hire.mjs:
 *
 *   1. Existing install (>=1 real project), no default_provider saved, 2+
 *      CLIs installed -> NO popup/toast/dialog ever. The old
 *      _maybeOfferProviderChoice toast is gone; only the walkthrough's own
 *      first-run step may ask, and that never runs when a real project
 *      already exists.
 *   2. Existing install, exactly ONE CLI installed, no default_provider
 *      saved -> silently PUT /api/config {default_provider: <that CLI>},
 *      still no popup. (walkthrough.mjs already proves the inverse: a fresh
 *      install with 2+ CLIs DOES show the walkthrough's provider-choice
 *      step — not re-tested here.)
 *
 * RUN
 *   cd tools/smoke && node provider-choice-no-popup.mjs
 * Exit 0 = both scenarios hold; 1 = a popup leaked back in or the silent
 * single-CLI save regressed.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

// A real, non-onboarding project — this is what makes realProjectCount > 0
// in index.html's boot continuation, i.e. an "existing install".
const REAL_PROJECT = {
  id: 'acme', name: 'Acme', status: 'active', domain: 'general',
  _is_onboarding_project: false, emoji: '', description: '', summary: '',
  current_task: '', next_action: '', blocked: false, blocked_reason: null,
  activity_log: [], backlog: [], project_path: '/smoke/acme',
  last_updated: '2026-09-14T00:00:00Z', last_updated_relative: 'today',
  last_completed: null, live_agent: null, display_order: 0, provider: 'claude',
  use_streaming_agent: true, roster: [],
};

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

function routeCommon(page, providersFixture, configBody, onConfigPut) {
  return page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([REAL_PROJECT]) });
    if (path === '/api/config') {
      if (req.method() === 'PUT' && onConfigPut) {
        let body = {};
        try { body = JSON.parse(req.postData() || '{}'); } catch (e) { /* ignore */ }
        onConfigPut(body);
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(configBody) });
    }
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/agent/providers') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(providersFixture) });
    return route.abort();
  });
}

// Any visible on-screen text containing the provider-choice prompt copy —
// whether it's a wt-card, a toast, or something new entirely — means a popup
// leaked back onto an existing install.
async function findProviderPopupText(page) {
  return page.evaluate(() => {
    const needle = 'which ai do you work with';
    const all = Array.from(document.querySelectorAll('body *'));
    for (const el of all) {
      if (el.children.length > 0) continue; // leaf nodes only
      const t = (el.textContent || '').trim().toLowerCase();
      if (t.includes(needle)) {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) return el.outerHTML.slice(0, 200);
      }
    }
    return null;
  });
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();

  // ── Scenario 1: existing install, 2 CLIs installed, no default saved ────
  {
    const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
    let configPutCalls = 0;
    await routeCommon(page, {
      providers: [
        { name: 'claude', display_name: 'Claude Code', installed: true, in_use: true, default: true },
        { name: 'codex', display_name: 'Codex', installed: true, in_use: false, default: false },
      ],
      default: 'claude',
    }, {}, () => { configPutCalls++; });
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card', { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(1200); // past the 600ms fresh-install timer + boot continuation

    const hasOldFn = await page.evaluate(() => typeof window._maybeOfferProviderChoice);
    const hasNewFn = await page.evaluate(() => typeof window._maybeSetSoleProviderDefault);
    if (hasOldFn !== 'undefined') fail(`old toast function _maybeOfferProviderChoice still exists (typeof ${hasOldFn}) — removal regressed`);
    else ok('_maybeOfferProviderChoice is gone');
    if (hasNewFn !== 'function') fail(`_maybeSetSoleProviderDefault missing (typeof ${hasNewFn})`);
    else ok('_maybeSetSoleProviderDefault is wired');

    const popup = await findProviderPopupText(page);
    if (popup) fail(`provider-choice popup rendered on an EXISTING install (2 CLIs, no default): ${popup}`);
    else ok('existing install, 2 CLIs installed: no popup');

    if (configPutCalls !== 0) fail(`PUT /api/config called ${configPutCalls}x with 2 CLIs installed — should stay silent, nothing to auto-pick`);
    else ok('existing install, 2 CLIs installed: no config write (ambiguous, correctly left alone)');

    await ctx.close();
    if (pageErrors.length) fail('uncaught exception(s): ' + pageErrors.join(' | '));
  }

  // ── Scenario 2: existing install, exactly ONE CLI, no default saved ─────
  {
    const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
    let savedDefault = null;
    await routeCommon(page, {
      providers: [
        { name: 'codex', display_name: 'Codex', installed: true, in_use: false, default: false },
      ],
      default: 'claude',
    }, {}, (body) => { if ('default_provider' in body) savedDefault = body.default_provider; });
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card', { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(1200);

    const popup = await findProviderPopupText(page);
    if (popup) fail(`provider-choice popup rendered on an EXISTING install (1 CLI, no default): ${popup}`);
    else ok('existing install, 1 CLI installed (codex): no popup');

    if (savedDefault !== 'codex') fail(`expected silent PUT /api/config {default_provider:'codex'}, got ${JSON.stringify(savedDefault)}`);
    else ok("existing install, 1 CLI installed (codex): silently saved as default, no dialog");

    await ctx.close();
    if (pageErrors.length) fail('uncaught exception(s): ' + pageErrors.join(' | '));
  }

  exitCode = bad === 0 ? 0 : 1;
  console.log(exitCode === 0
    ? '\n✅ PASS — no in-app provider popup on an existing install; single-CLI default still saves silently.'
    : `\n❌ FAIL — ${bad} problem(s).`);
} catch (err) {
  console.error('❌ FAIL — smoke harness error:', err && err.stack ? err.stack : err);
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
