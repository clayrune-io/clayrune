#!/usr/bin/env node
/**
 * First-run SETUP / TOUR decoupling (d3d5eb6) — the gate itself, and the end
 * of the flow. Plan: ~/.claude/plans/first-run-setup-decoupled-from-tour.md,
 * section 4 "Verification" listed a MANUAL check only ("declining the tour
 * at step 4 leaves a configured install") — never automated. Every existing
 * smoke (first-run-provider-chooser, first-run-signin-flow, walkthrough,
 * boot-smoke) covers a middle step or the tour in isolation; none covers the
 * gate function (`firstRunNeeded`) or what happens when setup finishes.
 *
 * This smoke drives static/js/first-run.js's boot gate and its two exits
 * ("Not now" / "Take the tour") end to end, plus the migration path (an old
 * browser that already finished the combined tour), the fail-closed hydration
 * case, and Settings -> "Run setup again". Hermetic, same shape as
 * first-run-provider-chooser.mjs: route-mocked /api/*, fresh data per
 * scenario (a new browser context — real index.html + real static/js/*.js,
 * no network, no running MC server), 0 real projects unless a scenario says
 * otherwise.
 *
 * RUN
 *   cd tools/smoke && node first-run-setup-gate.mjs
 * Exit 0 = every assertion holds; 1 = a regression or a page error fired.
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
// excluded from "real project count" by isOnboardingProject(), same fixture
// first-run-provider-chooser.mjs / walkthrough.mjs use.
const CLAYRUNE_PROJECT = {
  id: 'clayrune', name: 'Clayrune', status: 'active', domain: 'general',
  _is_onboarding_project: true, emoji: '', description: '', summary: 'tour',
  current_task: 'Tour Clayrune', next_action: '', blocked: false, blocked_reason: null,
  activity_log: [], backlog: [], project_path: '/smoke/clayrune',
  last_updated: '2026-09-14T00:00:00Z', last_updated_relative: 'today',
  last_completed: null, live_agent: null, display_order: 0, provider: 'claude',
  use_streaming_agent: true, roster: [],
};

// A single vendor, installed AND signed in — lets the 'connections' step
// SKIP itself on a normal (non-forced) run, so scenarios that just need to
// reach the tour offer don't have to fight the provider picker's own
// validation. Scenario H (Settings -> Run setup again) uses the exact same
// fixture to prove the FORCED run does NOT skip it despite this.
const ONE_PROVIDER_OK = {
  providers: [{ name: 'claude', display_name: 'Claude Code', installed: true, in_use: true, default: true, auth_status: 'ok' }],
  default: 'claude',
};
// No default chosen, one vendor not even installed — the 'connections' step
// must render (not skip) so a walk from step 1 actually passes through it.
const TWO_PROVIDERS_UNSET = {
  providers: [
    { name: 'claude', display_name: 'Claude Code', installed: true, in_use: true, default: false, auth_status: 'not_logged_in' },
    { name: 'codex', display_name: 'Codex', installed: false, in_use: false, default: false, install_hint: 'npm install -g codex' },
  ],
  default: '',
};

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

let browser;
let scenarioIdx = 0;

// Builds a fresh, isolated context (own localStorage/cookies) wired with the
// route table every scenario shares, plus per-scenario overrides.
async function scenario(name, { config, configStatus = 200, providers = ONE_PROVIDER_OK, projects = [CLAYRUNE_PROJECT], initScript = null }, run) {
  scenarioIdx++;
  console.log(`\n[${scenarioIdx}] ${name}`);
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  if (initScript) await ctx.addInitScript(initScript);
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  const configPuts = [];

  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(projects) });
    if (path === '/api/config') {
      if (req.method() === 'PUT') {
        try { configPuts.push(JSON.parse(req.postData() || '{}')); } catch (_) {}
        return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      }
      if (configStatus !== 200) return route.fulfill({ status: configStatus, contentType: 'application/json', body: '{}' });
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(config) });
    }
    if (path === '/api/walkthrough/sample-project') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, id: 'clayrune', existed: true }) });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/agent/providers') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(providers) });
    if (path === '/api/local-auth/status') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ configured: false }) });
    if (path === '/api/system/update/status') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ is_git_repo: true, behind: 0, ahead: 0, has_local_changes: false, update_available: false, projects_in_install_dir: [] }) });
    return route.abort();
  });

  try {
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await run(page, { configPuts });
  } finally {
    if (pageErrors.length) fail(`[${name}] uncaught exception(s): ${pageErrors.join(' | ')}`);
    await ctx.close();
  }
}

async function overlayState(page) {
  return page.evaluate(() => {
    const setup = document.getElementById('setup-overlay');
    const tour = document.getElementById('wt-overlay');
    const card = setup ? setup.querySelector('.wt-card') : null;
    return {
      setupVisible: !!(setup && setup.style.display !== 'none'),
      tourVisible: !!(tour && tour.style.display !== 'none'),
      title: setup ? (setup.querySelector('.wt-title') || {}).textContent : null,
      progress: setup ? (setup.querySelector('.setup-band-progress') || {}).textContent : null,
      isMarkedAsSetup: !!(card && card.classList.contains('wt-card-setup')),
      cardText: card ? card.textContent : '',
    };
  });
}

try {
  browser = await chromium.launch();

  // ── 1. Fresh install: setup appears, tour does NOT auto-start ───────────
  await scenario('boot gate: setup_completed unset, 0 real projects', {
    config: { setup_completed: false },
  }, async (page) => {
    await page.waitForSelector('#setup-overlay', { timeout: 5000 }).catch(() => {});
    const st = await overlayState(page);
    if (!st.setupVisible) fail('setup overlay did not appear on a fresh install');
    else ok('setup overlay appears on a fresh install');
    if (st.title !== 'Welcome to Clayrune') fail(`first step should be Welcome, got: ${st.title}`);
    else ok('first step is Welcome');
    await page.waitForTimeout(400); // give an errant auto-start time to fire
    const st2 = await overlayState(page);
    if (st2.tourVisible) fail('the tour auto-started alongside/after setup — it must only be offered from setup’s last step');
    else ok('the tour does NOT auto-start');
  });

  // ── 2 & 3. Walk to the last step, both exits ─────────────────────────────
  const walkToTourOffer = async (page) => {
    await page.waitForSelector('#setup-overlay', { timeout: 5000 }).catch(() => {});
    let st = await overlayState(page);
    if (st.title !== 'Welcome to Clayrune') { fail(`expected Welcome first, got: ${st.title}`); return null; }
    await page.click('#setup-overlay .wt-btn-primary'); // "Get started"
    await page.waitForTimeout(150);
    st = await overlayState(page);
    if (st.title !== 'A few essentials') { fail(`connections step should have auto-skipped (provider already installed+signed-in); expected Essentials, got: ${st.title}`); return null; }
    ok('connections step skipped (default provider already installed + signed in)');
    await page.click('#setup-overlay .wt-btn-primary'); // "Next"
    await page.waitForTimeout(150);
    st = await overlayState(page);
    if (st.title !== 'Take the tour?') { fail(`expected the tour-offer step last, got: ${st.title}`); return null; }
    ok(`reached the tour offer (progress: ${st.progress})`);
    return st;
  };

  await scenario('walk to tour offer, decline ("Not now")', {
    config: { setup_completed: false, default_provider: 'claude', agent_model: 'tier:balanced' },
    providers: ONE_PROVIDER_OK,
  }, async (page, { configPuts }) => {
    if (!(await walkToTourOffer(page))) return;
    await page.click('#setup-overlay .setup-btn-secondary'); // "Not now"
    await page.waitForTimeout(200);
    const st = await overlayState(page);
    if (st.setupVisible) fail('setup overlay still open after "Not now"');
    else ok('setup overlay closes on "Not now"');
    if (st.tourVisible) fail('the tour started after declining it');
    else ok('the tour did NOT start after "Not now"');
    const completes = configPuts.filter((p) => p.setup_completed === true);
    if (completes.length !== 1) fail(`expected exactly one PUT /api/config {setup_completed:true}, got ${completes.length}: ${JSON.stringify(configPuts)}`);
    else ok('PUT /api/config {setup_completed:true} sent on decline');
  });

  await scenario('walk to tour offer, accept ("Take the tour")', {
    config: { setup_completed: false, default_provider: 'claude', agent_model: 'tier:balanced' },
    providers: ONE_PROVIDER_OK,
  }, async (page, { configPuts }) => {
    if (!(await walkToTourOffer(page))) return;
    await page.click('#setup-overlay .wt-btn-primary'); // "Take the tour"
    await page.waitForTimeout(300);
    const st = await overlayState(page);
    if (st.setupVisible) fail('setup overlay still open after "Take the tour"');
    else ok('setup overlay closes on "Take the tour"');
    if (!st.tourVisible) fail('the tour did NOT start after "Take the tour"');
    else ok('the tour (walkthrough overlay) starts after "Take the tour"');
    const completes = configPuts.filter((p) => p.setup_completed === true);
    if (completes.length !== 1) fail(`expected exactly one PUT /api/config {setup_completed:true}, got ${completes.length}: ${JSON.stringify(configPuts)}`);
    else ok('PUT /api/config {setup_completed:true} sent before handing off to the tour');
  });

  // ── 4. First run cannot be skipped: no skip control on any step ─────────
  // Clean-VM incident (2026-09-23): "Skip setup" sat on the very first card,
  // styled identically to the tour's own skip control, so one click ended
  // setup for good. First run now offers no skip anywhere; setup_completed
  // is written only from the final step's own two buttons.
  await scenario('first run: no skip control on any step, gated connections blocks Next', {
    config: { setup_completed: false },
    providers: TWO_PROVIDERS_UNSET,
  }, async (page, { configPuts }) => {
    await page.waitForSelector('#setup-overlay', { timeout: 5000 }).catch(() => {});
    let st = await overlayState(page);
    if (st.title !== 'Welcome to Clayrune') { fail(`expected Welcome first, got: ${st.title}`); return; }
    if (await page.locator('#setup-overlay .wt-btn-skip, #setup-overlay .setup-btn-secondary').count()) fail('a skip-style control is present on step 1 of a first run');
    else ok('no skip control on step 1 (Welcome)');
    if (!st.isMarkedAsSetup) fail('setup card does not carry the wt-card-setup marker (would render visually identical to the tour)');
    else ok('setup card carries the wt-card-setup marker, distinguishing it from the tour');
    if (/\btour\b/i.test(st.cardText)) fail(`the word "tour" appears on a non-final setup card: "${st.cardText}"`);
    else ok('no "tour" text on the Welcome step');

    await page.click('#setup-overlay .wt-btn-primary'); // "Get started"
    await page.waitForTimeout(150);
    st = await overlayState(page);
    if (st.title !== 'Which AI do you work with?') { fail(`expected the connections step (no vendor installed+signed-in), got: ${st.title}`); return; }
    if (await page.locator('#setup-overlay .wt-btn-skip:has-text("Skip setup"), #setup-overlay .setup-btn-secondary:has-text("Skip setup")').count()) fail('"Skip setup" is present on the connections step of a first run');
    else ok('no "Skip setup" control on the connections step');
    if (/\btour\b/i.test(st.cardText)) fail(`the word "tour" appears on the connections step: "${st.cardText}"`);
    else ok('no "tour" text on the connections step');

    // Nothing selected yet: Next must be disabled with a visible one-line reason.
    const nextBtn = page.locator('#setup-overlay .wt-btn-primary');
    if (!(await nextBtn.isDisabled())) fail('Next is not disabled on the connections step before a vendor is selected+installed+signed in');
    else ok('Next is disabled on the connections step until a vendor qualifies');
    const reasonTxt = (await page.locator('#setup-overlay .wt-next-reason').textContent().catch(() => '')) || '';
    if (!reasonTxt.trim()) fail('no visible reason shown while Next is disabled on the connections step');
    else ok(`reason shown while blocked: "${reasonTxt.trim()}"`);

    // The escape link is present while blocked, confirms before acting, and
    // declining the confirm leaves setup open and uncompleted.
    const laterLink = page.locator('#setup-overlay .setup-btn-secondary:has-text("I\'ll connect one later")');
    if (!(await laterLink.count())) { fail('no "I\'ll connect one later" escape link while Next is blocked'); return; }
    ok('"I\'ll connect one later" escape link is present while blocked');
    await page.evaluate(() => { window.__confirmCalls = []; window.confirm = (msg) => { window.__confirmCalls.push(msg); return false; }; });
    await laterLink.click();
    await page.waitForTimeout(150);
    st = await overlayState(page);
    if (!st.setupVisible) fail('setup overlay closed after declining the "connect one later" confirm');
    else ok('declining the confirm leaves setup open');
    if ((await page.evaluate(() => window.__confirmCalls.length)) !== 1) fail('"I\'ll connect one later" did not show a confirm dialog');
    else ok('"I\'ll connect one later" requires an explicit confirm');
    if (configPuts.some((p) => p.setup_completed === true)) fail('setup_completed was written despite declining the confirm');
    else ok('setup_completed not written while declined');

    // Accepting the confirm ends setup and DOES persist setup_completed —
    // same effect the old blanket "Skip setup" had, but explicit and gated.
    await page.evaluate(() => { window.confirm = () => true; });
    await laterLink.click();
    await page.waitForTimeout(200);
    st = await overlayState(page);
    if (st.setupVisible) fail('setup overlay still open after accepting "connect one later"');
    else ok('setup overlay closes after accepting "connect one later"');
    const completes = configPuts.filter((p) => p.setup_completed === true);
    if (completes.length !== 1) fail(`"connect one later" (accepted) should record setup_completed:true, got PUTs: ${JSON.stringify(configPuts)}`);
    else ok('accepting "connect one later" records setup_completed:true');
  });

  // ── 4b. Essentials step (middle, non-last): no skip control either ──────
  await scenario('first run: no skip control on the essentials (middle) step', {
    config: { setup_completed: false, default_provider: 'claude', agent_model: 'tier:balanced' },
    providers: ONE_PROVIDER_OK, // connections step auto-skips
  }, async (page) => {
    await page.waitForSelector('#setup-overlay', { timeout: 5000 }).catch(() => {});
    await page.click('#setup-overlay .wt-btn-primary'); // Get started -> essentials (connections skipped)
    await page.waitForTimeout(150);
    const st = await overlayState(page);
    if (st.title !== 'A few essentials') { fail(`expected Essentials, got: ${st.title}`); return; }
    if (await page.locator('#setup-overlay .wt-btn-skip, #setup-overlay .setup-btn-secondary').count()) fail('a skip-style control is present on the essentials step of a first run');
    else ok('no skip control on the essentials step');
    if (/\btour\b/i.test(st.cardText)) fail(`the word "tour" appears on the essentials step: "${st.cardText}"`);
    else ok('no "tour" text on the essentials step');
  });

  // ── 4c. Reload mid-setup resumes setup, not the dashboard ───────────────
  await scenario('reload mid-setup (essentials step, not yet completed) resumes setup', {
    config: { setup_completed: false, default_provider: 'claude', agent_model: 'tier:balanced' },
    providers: ONE_PROVIDER_OK,
  }, async (page) => {
    await page.waitForSelector('#setup-overlay', { timeout: 5000 }).catch(() => {});
    await page.click('#setup-overlay .wt-btn-primary'); // Get started -> essentials
    await page.waitForTimeout(150);
    let st = await overlayState(page);
    if (st.title !== 'A few essentials') { fail(`expected Essentials before reload, got: ${st.title}`); return; }
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#setup-overlay', { timeout: 5000 }).catch(() => {});
    st = await overlayState(page);
    if (!st.setupVisible) fail('setup did not resume after a reload mid-setup — setup_completed was never written, so the boot gate should re-trigger it');
    else ok('reload mid-setup brings setup back (setup_completed still false server-side)');
  });

  // ── 4d. The tour cannot start before setup completes ─────────────────────
  await scenario('startTourOrSetup() redirects into setup while first-run is not yet completed', {
    config: { setup_completed: false },
  }, async (page) => {
    await page.waitForSelector('#setup-overlay', { timeout: 5000 }).catch(() => {});
    await page.evaluate(() => { document.getElementById('setup-overlay').remove(); window.setupActive = false; });
    // Simulates the header '?' button / command-palette "Take Tour" entry.
    await page.evaluate(() => window.startTourOrSetup());
    await page.waitForTimeout(200);
    const st = await overlayState(page);
    if (st.tourVisible) fail('the tour started via startTourOrSetup() before first-run setup completed');
    else ok('startTourOrSetup() does not start the tour before setup completes');
    if (!st.setupVisible) fail('startTourOrSetup() did not fall back to opening setup');
    else ok('startTourOrSetup() opens setup instead');
  });

  // ── 5. setup_completed already true: nothing auto-starts ────────────────
  await scenario('boot gate: setup_completed already true', {
    config: { setup_completed: true },
  }, async (page) => {
    await page.waitForTimeout(900); // past the 600ms auto-start delay
    const st = await overlayState(page);
    if (st.setupVisible) fail('setup overlay appeared even though setup_completed is already true');
    else ok('no setup overlay when setup_completed is already true');
    if (st.tourVisible) fail('the tour auto-started even though setup_completed is already true');
    else ok('the tour does not auto-start either');
  });

  // ── 6. Migration: old browser finished the combined tour ────────────────
  await scenario('boot gate: setup_completed false but walkthrough_done set (old-tour browser)', {
    config: { setup_completed: false },
    initScript: () => localStorage.setItem('walkthrough_done', '1'),
  }, async (page, { configPuts }) => {
    await page.waitForTimeout(900);
    const st = await overlayState(page);
    if (st.setupVisible) fail('setup overlay appeared for a browser that already finished the old combined tour');
    else ok('no setup overlay for a browser with walkthrough_done already set');
    const completes = configPuts.filter((p) => p.setup_completed === true);
    if (completes.length !== 1) fail(`expected exactly one PUT /api/config {setup_completed:true} (firstRunNeeded's migration write), got ${completes.length}: ${JSON.stringify(configPuts)}`);
    else ok('setup_completed:true is persisted once for the migrated browser');
  });

  // ── 7. /api/config hydration failure: fails closed ───────────────────────
  await scenario('boot gate: /api/config returns 500', {
    config: null, configStatus: 500,
  }, async (page) => {
    await page.waitForTimeout(900);
    const st = await overlayState(page);
    if (st.setupVisible) fail('setup overlay appeared despite a failed /api/config hydration (500) — should fail closed');
    else ok('no setup overlay when /api/config hydration fails with 500 (fails closed)');
  });

  await scenario('boot gate: /api/config returns 200 {} (no setup_completed key)', {
    config: {},
  }, async (page) => {
    await page.waitForTimeout(900);
    const st = await overlayState(page);
    if (st.setupVisible) fail('setup overlay appeared for a 200 {} config body missing setup_completed — should fail closed');
    else ok('no setup overlay when /api/config returns {} without setup_completed (fails closed)');
  });

  // ── 8. Settings -> "Run setup again": every step shown ──────────────────
  await scenario('Settings -> "Run setup again" shows every step, even when already set up', {
    config: { setup_completed: true, default_provider: 'claude', agent_model: 'tier:balanced' },
    providers: ONE_PROVIDER_OK, // installed + signed in: would normally skip 'connections'
    initScript: () => localStorage.setItem('walkthrough_done', '1'), // would normally skip 'tour'
  }, async (page) => {
    await page.waitForTimeout(900);
    let st = await overlayState(page);
    if (st.setupVisible) { fail('setup overlay should not auto-open when setup_completed is already true'); return; }
    ok('no overlay on boot (already set up)');

    await page.evaluate(() => window.openSettings());
    await page.waitForSelector('#update-status-hint', { state: 'attached', timeout: 10000 });
    // 'system' has multiple sections -> drillSettings lands on the layer-2
    // sub-list. Find "Help"'s index by its own title text, not a hardcoded
    // index — section order is UI content, not a contract.
    await page.evaluate(() => window.drillSettings('system'));
    const helpIdx = await page.evaluate(() => {
      const secs = [...document.querySelectorAll('#settings-detail .settings-detail-pane[data-cat="system"] .settings-section')];
      return secs.findIndex((s) => (s.querySelector('.settings-section-title')?.textContent || '').trim() === 'Help');
    });
    if (helpIdx < 0) { fail('Help settings section not found under System'); return; }
    await page.evaluate((idx) => window.drillSettingsSub(idx), helpIdx);
    await page.waitForFunction(() => {
      const btns = [...document.querySelectorAll('#settings-detail button')];
      return btns.some((b) => b.textContent.trim() === 'Run setup again' && getComputedStyle(b.closest('.settings-detail-pane')).display !== 'none');
    }, { timeout: 5000 });

    await page.click('#settings-detail button:has-text("Run setup again")');
    await page.waitForSelector('#setup-overlay', { timeout: 5000 });
    st = await overlayState(page);
    if (st.title !== 'Welcome to Clayrune') { fail(`"Run setup again" should start at Welcome, got: ${st.title}`); return; }
    if (st.progress !== 'Step 1 of 4') fail(`forced re-run should show all 4 steps (none skipped), got progress "${st.progress}"`);
    else ok('forced re-run shows all 4 steps (progress "Step 1 of 4") despite an already-configured install');
    if (!st.isMarkedAsSetup) fail('forced re-run card does not carry the wt-card-setup marker');
    else ok('forced re-run card also carries the wt-card-setup marker');

    await page.click('#setup-overlay .wt-btn-primary'); // Get started
    await page.waitForTimeout(150);
    st = await overlayState(page);
    if (st.title !== 'Which AI do you work with?') fail(`connections step should NOT be skipped on a forced re-run, got: ${st.title}`);
    else ok('connections step is shown on a forced re-run despite the provider already being installed + signed in');

    await page.click('#setup-overlay .wt-btn-primary'); // Next (validation passes: claude selected+default+ok)
    await page.waitForTimeout(150);
    st = await overlayState(page);
    if (st.title !== 'A few essentials') { fail(`expected Essentials, got: ${st.title}`); return; }

    await page.click('#setup-overlay .wt-btn-primary'); // Next
    await page.waitForTimeout(150);
    st = await overlayState(page);
    if (st.title !== 'Take the tour?') fail(`tour-offer step should NOT be skipped on a forced re-run, got: ${st.title}`);
    else ok('tour-offer step is shown on a forced re-run despite walkthrough_done already being set');
  });

  console.log(bad === 0
    ? '\n✅ PASS — first-run SETUP/TOUR gate: boot trigger, both tour-offer exits, skip-setup, already-set-up, migration, fail-closed hydration, and Settings re-run all hold.'
    : `\n❌ FAIL — ${bad} problem(s).`);
} catch (err) {
  console.error('❌ FAIL — smoke harness error:', err && err.stack ? err.stack : err);
  bad = bad || 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(bad ? 1 : 0);
}
