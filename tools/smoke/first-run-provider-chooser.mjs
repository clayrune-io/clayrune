#!/usr/bin/env node
/**
 * First-run provider chooser (static/js/walkthrough.js's 'provider-choice'
 * step) at a genuinely fresh data dir: zero real projects, no
 * default_provider saved — the exact state a downloaded macOS .app is in,
 * since it never runs install.sh/install.ps1's own provider prompt (that
 * prompt only exists on the shell/PowerShell installer path).
 *
 * MC fresh-install report 2026-09-15: the chooser showed only Claude and
 * Codex (no Gemini) and let a user pick Codex when the CLI wasn't actually
 * installed, with no install offer and no "not installed" label. Root cause
 * (real headless boot (real index.html + real static/js/*.js, no network),
 * same hermetic shape as walkthrough.mjs):
 *
 *   1. The step must list EVERY provider /api/agent/providers returns, not
 *      only installed ones — a not-installed provider (gemini here) must
 *      still appear, labeled "not installed", with an Install button.
 *   2. Installed providers sort first.
 *   3. The step must NOT skip just because 0 or 1 CLI is installed (that
 *      used to hide the install offer on exactly the fresh-Mac-.app case) —
 *      only an already-set default_provider skips it.
 *   4. Clicking Install calls POST /api/agent/provider/<name>/install-launch
 *      and reflects the server's response inline instead of silently no-op.
 *
 * REWRITTEN 2026-09-18 (W6): this smoke originally read each provider row
 * as a single `<input type=radio>` whose click both selected the provider
 * AND set it as default — the pre-multi-select chooser design. Ron's
 * 2026-09-18 decision ("user should be able to choose more than one vendor
 * on initial installation") replaced that with a `<input type=checkbox
 * name="wt-provider">` per row (selection) plus a separate, only-rendered-
 * when-selected `<input type=radio name="wt-provider-default">` with NO
 * `value` attribute (it sets the default via its onchange handler, not its
 * value). The old selector `label querySelector('input[type=radio]')` found
 * either nothing (unselected rows have no radio at all) or a valueless
 * radio, so `row.name` was always '' — every `byName[...]` lookup failed,
 * which is the "gemini is missing" / "Install button called 0 times"
 * failures this smoke was throwing on unmodified `c14c2fd`. That is a STALE
 * ASSERTION about a UI shape that no longer exists, not a real chooser bug —
 * `tools/smoke/onboarding-multiselect.mjs` and
 * `tools/smoke/onboarding-multiselect-browser.mjs` already cover the
 * multi-select checkbox/default-radio/install/sign-in behavior end to end
 * and both pass unmodified. This rewrite updates the DOM reads to the
 * checkbox shape and keeps every other assertion (every-provider listing,
 * installed-first sort, state labels, live install-launch wiring) intact.
 *
 * RUN
 *   cd tools/smoke && node first-run-provider-chooser.mjs
 * Exit 0 = all assertions hold; 1 = the chooser regressed or a page error fired.
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

// Mirrors seed_onboarding_on_startup() — present on every fresh install but
// excluded from "real project count" by isOnboardingProject().
const CLAYRUNE_PROJECT = {
  id: 'clayrune', name: 'Clayrune', status: 'active', domain: 'general',
  _is_onboarding_project: true, emoji: '', description: '', summary: 'tour',
  current_task: 'Tour Clayrune', next_action: '', blocked: false, blocked_reason: null,
  activity_log: [], backlog: [], project_path: '/smoke/clayrune',
  last_updated: '2026-09-14T00:00:00Z', last_updated_relative: 'today',
  last_completed: null, live_agent: null, display_order: 0, provider: 'claude',
  use_streaming_agent: true, roster: [],
};

// The reported bug's shape: 2 installed (one of them NOT claude, so a
// naive "skip when <=1 installed" guard wouldn't even be the reason gemini
// vanished) + 1 registered-but-not-installed. install_hint mirrors the exact
// command install.sh runs — never invented here or in the app.
const PROVIDERS_FIXTURE = {
  providers: [
    { name: 'gemini', display_name: 'Gemini CLI', installed: false, in_use: false,
      default: false, install_hint: 'npm install -g @google/gemini-cli' },
    { name: 'claude', display_name: 'Claude Code', installed: true, in_use: true,
      default: true, auth_status: 'ok' },
    { name: 'codex', display_name: 'Codex', installed: true, in_use: false,
      default: false, auth_status: 'not_logged_in' },
  ],
  default: 'claude',
};

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

async function readProviderChoiceStep(page) {
  return page.evaluate(() => {
    const overlay = document.getElementById('wt-overlay');
    if (!overlay) return null;
    const title = (overlay.querySelector('.wt-title') || {}).textContent || '';
    // Anchor on the selection checkbox itself — one per provider, guaranteed
    // unique by `value` — rather than a positional container selector. The
    // checkbox's `<label>` holds it + the display/state spans + an optional
    // Install button; the label's parent `<div>` (walkthrough.js's per-
    // provider wrapper) additionally holds the default radio + Sign in
    // button once the row is selected (both absent otherwise).
    const rows = Array.from(overlay.querySelectorAll('input[type=checkbox][name="wt-provider"]')).map((cb) => {
      const label = cb.closest('label');
      const row = label ? label.parentElement : cb.parentElement;
      const spans = label ? Array.from(label.querySelectorAll('span')) : [];
      const defaultRadio = row.querySelector('input[type=radio][name="wt-provider-default"]');
      return {
        name: cb.value,
        selected: cb.checked,
        displayText: (spans[0] || {}).textContent || '',
        stateText: (spans[1] || {}).textContent || '',
        hasInstallBtn: !!(label && label.querySelector('button[onclick*="wtInstallProvider"]')),
        hasDefaultRadio: !!defaultRadio,
        defaultChecked: !!(defaultRadio && defaultRadio.checked),
        hasSignInBtn: !!row.querySelector('button[onclick*="settingsProviderTerminalLogin"]'),
      };
    });
    return { title, rows };
  });
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

  let installLaunchCalls = 0;
  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    // Fresh install: only the onboarding project exists (realProjectCount === 0).
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([CLAYRUNE_PROJECT]) });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }); // no default_provider saved
    if (path === '/api/walkthrough/sample-project') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, id: 'clayrune', existed: true }) });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/agent/providers') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(PROVIDERS_FIXTURE) });
    if (path === '/api/agent/provider/gemini/install-launch' && req.method() === 'POST') {
      installLaunchCalls++;
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, command: 'npm install -g @google/gemini-cli' }) });
    }
    return route.abort();
  });

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 }).catch(() => {});
  // Fresh install auto-starts the tour ~600ms after boot continuation.
  await page.waitForSelector('#wt-overlay', { timeout: 5000 }).catch(() => {});

  const step0 = await readProviderChoiceStep(page);
  if (!step0) {
    fail('walkthrough overlay never appeared on a fresh install (0 real projects, no default_provider)');
  } else if (step0.title !== 'Which AI do you work with?') {
    // First step is 'welcome' — advance once.
    await page.evaluate(() => window.wtNext());
    await page.waitForTimeout(200);
  }

  const step = await readProviderChoiceStep(page);
  if (!step || step.title !== 'Which AI do you work with?') {
    fail(`provider-choice step did not render (got title: ${step && step.title})`);
  } else {
    ok('provider-choice step rendered on a fresh install with 2 CLIs installed (not just <=1)');

    const byName = Object.fromEntries(step.rows.map(r => [r.name, r]));
    if (!byName.gemini) fail('gemini (not installed) is MISSING from the chooser — the exact reported bug');
    else ok('gemini appears in the chooser even though it is not installed');

    if (byName.gemini && !/not installed/i.test(byName.gemini.stateText))
      fail(`gemini's state text should say "not installed", got: "${byName.gemini && byName.gemini.stateText}"`);
    else ok('gemini is labeled "not installed"');

    if (byName.gemini && !byName.gemini.hasInstallBtn)
      fail('gemini row has no Install button');
    else ok('gemini row offers an Install button');

    if (byName.codex && !/not signed in/i.test(byName.codex.stateText))
      fail(`codex (installed, not_logged_in) should read "not signed in", got: "${byName.codex && byName.codex.stateText}"`);
    else ok('codex (installed, not signed in) is labeled correctly');

    if (byName.claude && !/signed in/i.test(byName.claude.stateText))
      fail(`claude (installed, ok) should read "signed in", got: "${byName.claude && byName.claude.stateText}"`);
    else ok('claude (installed, signed in) is labeled correctly');

    // Installed-first ordering: gemini (not installed) must sort AFTER both
    // claude and codex, regardless of its position in the fixture/API response.
    const order = step.rows.map(r => r.name);
    const geminiIdx = order.indexOf('gemini');
    const installedIdx = order.map((n, i) => (byName[n] || {}).stateText).findIndex(t => /not installed/i.test(t || ''));
    if (geminiIdx !== -1 && geminiIdx !== order.length - 1)
      fail(`not-installed provider should sort last, got order: ${order.join(', ')}`);
    else ok(`installed providers sort first: ${order.join(', ')}`);

    // Click gemini's Install button and confirm it hits the real endpoint.
    await page.evaluate(() => {
      const overlay = document.getElementById('wt-overlay');
      const cb = overlay.querySelector('input[type=checkbox][name="wt-provider"][value="gemini"]');
      const label = cb && cb.closest('label');
      const btn = label && label.querySelector('button[onclick*="wtInstallProvider"]');
      if (btn) btn.click();
    });
    await page.waitForTimeout(300);
    if (installLaunchCalls !== 1) fail(`Install button should call /install-launch once, got ${installLaunchCalls}`);
    else ok('Install button calls POST /api/agent/provider/gemini/install-launch');

    // Multi-select: no default_provider saved (this fixture's whole premise)
    // means nothing is pre-selected — the user picks. Select claude, make it
    // default, then select a SECOND vendor and confirm claude keeps its
    // selection+default instead of being displaced — the actual multi-select
    // behavior Ron's 2026-09-18 decision requires, which this smoke's old
    // single-radio reads could never have exercised (a radio group allows
    // exactly one checked member by construction).
    await page.locator('#wt-overlay input[name="wt-provider"][value="claude"]').check();
    await page.waitForTimeout(30);
    await page.locator('#wt-overlay input[name="wt-provider-default"]').first().check();
    await page.waitForTimeout(30);
    const afterClaude = await readProviderChoiceStep(page);
    const claudeSolo = afterClaude.rows.find(r => r.name === 'claude');
    if (!claudeSolo || !claudeSolo.selected || !claudeSolo.defaultChecked)
      fail(`claude should be selected+default after checking both, got: ${JSON.stringify(claudeSolo)}`);
    else ok('claude selected and set as default');

    await page.locator('#wt-overlay input[name="wt-provider"][value="codex"]').check();
    await page.waitForTimeout(30);
    const afterSelect = await readProviderChoiceStep(page);
    const claudeAfter = afterSelect.rows.find(r => r.name === 'claude');
    const codexAfter = afterSelect.rows.find(r => r.name === 'codex');
    if (!claudeAfter || !claudeAfter.selected || !claudeAfter.defaultChecked)
      fail(`selecting a second vendor (codex) must not clear claude's selection/default, got claude: ${JSON.stringify(claudeAfter)}`);
    else if (!codexAfter || !codexAfter.selected || !codexAfter.hasDefaultRadio || codexAfter.defaultChecked)
      fail(`codex should be selected with its own (unchecked) default radio after checking it, got: ${JSON.stringify(codexAfter)}`);
    else ok('multi-select: two vendors selected simultaneously, claude keeps default, codex gets its own unchecked default radio');
  }

  if (pageErrors.length) fail('uncaught exception(s): ' + pageErrors.join(' | '));

  exitCode = bad === 0 ? 0 : 1;
  console.log(exitCode === 0
    ? '\n✅ PASS — first-run chooser lists every provider with correct state, installed-first, with a working install offer.'
    : `\n❌ FAIL — ${bad} problem(s).`);
} catch (err) {
  console.error('❌ FAIL — smoke harness error:', err && err.stack ? err.stack : err);
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
