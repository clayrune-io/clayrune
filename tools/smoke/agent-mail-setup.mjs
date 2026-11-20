#!/usr/bin/env node
/**
 * MC-1166 U2: real first-run hooks, setup component and human-passcode modal,
 * served from this worktree with mocked HTTP at 1000px and 390px.
 * Pull DTOs match agent_mail_enrollment_routes.py (U1). Settings discovery is
 * still legacy until U3: retain that real mount regression separately with a
 * legacy passive fixture; the setup POST and first-run fixtures are pull.
 * No provider, identity or real email is used. Every request is intercepted.
 * RUN: node tools/smoke/agent-mail-setup.mjs
 */
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';
import { mailLayout, mailWizardFooter } from './_agent-mail-layout.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const STATIC = loadStaticJsCss(REPO_ROOT);
const SHOTS = resolve(REPO_ROOT, '_scratch', '7e3', 'setup');
mkdirSync(SHOTS, { recursive: true });
const ORIGIN = 'http://mc.smoke.test';
const PASS = 'correct horse 42';
const OWNER = 'Owner.Person@Example.test';
const OWNER_SENT = OWNER.toLowerCase();

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
let passed = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg = good) => { if (cond) { passed++; ok(good); } else fail(badMsg); };
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const NOW = 1794249600000;
const ready = { tunnel: true, claude_extraction: true, machine_custody: true };
const base = { enabled: false, revocations_pending: 0, now_ms: NOW, prerequisites: ready,
  mail: { blocked: 0, uncertain: 0, retryable: 0, outbound_uncertain: 0, hold_reasons: {} }, recovery: { state: 'not_running' } };
const NOT_ENROLLED = { ...base, stage: 'not_enrolled' };
const CLAYRUNE_PROJECT = {
  id: 'clayrune', name: 'Clayrune', status: 'active', domain: 'general', _is_onboarding_project: true, emoji: '', description: '',
  summary: 'tour', current_task: 'Tour Clayrune', next_action: '', blocked: false, blocked_reason: null, activity_log: [], backlog: [],
  project_path: '/smoke/clayrune', last_updated: '2026-09-14T00:00:00Z', last_updated_relative: 'today', last_completed: null,
  live_agent: null, display_order: 0, provider: 'claude', use_streaming_agent: true, roster: [],
};
const ONE_PROVIDER_OK = { providers: [{ name: 'claude', display_name: 'Claude Code', installed: true, in_use: true, default: true, auth_status: 'ok' }], default: 'claude' };

// U1 passive DTO: no deployment config or identity is needed for not_enrolled.
const pull = (state) => ({ protocol: 'pull',
  pull_status: { v: 1, state, enabled: state === 'active', owner: '', address: '',
    connection: state === 'active' ? 'connected' : 'not_running', vault: { state: 'unknown' } },
  signup: { state, reason: '', enabled: state === 'active', install_id: state === 'not_enrolled' ? null : 'install',
    flow_id: state === 'not_enrolled' ? null : 'flow', address: '', expires_ms: null, verification_state: null } });
const PULL_NEW = pull('not_enrolled');
const PULL_PENDING = pull('pending');
let enr = { status: 200, body: PULL_NEW };
let startReply = { status: 200, body: { protocol: 'pull', ...PULL_PENDING.signup }, next: PULL_PENDING };
let wizard = true;                                         // config.setup_completed is false while true
let reqs = [];                                             // every non-static request: { method, path, body }
let pageErrors = [];

const browser = await chromium.launch();
const fresh = async (width, settingsOnly) => {
  const ctx = await browser.newContext({ viewport: { width, height: width === 390 ? 800 : 900 } });
  const page = await ctx.newPage();
  if (settingsOnly) await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    // mobile.js posts /api/diag/viewport on its own timer; it is not part of the flow under test
    if (path.startsWith('/api/diag/')) return json({ ok: true });
    // update-power.js seeds its restart check with one heartbeat 1.5 s after
    // boot, and the 15 s completion poll repeats it. Both are page timers, so
    // one landing inside a "sends no request" window says nothing about the
    // click under test (failed "@390 invalid owner sends no request" 5 runs in 7).
    if (path === '/api/system/heartbeat') return json({ ok: true, started_at: 'smoke' });
    reqs.push({ method: req.method(), path, search: url.search, body: req.postData() || '' });
    if (path === '/api/agent-mail/enrollment') {
      if (req.method() === 'GET') return json(enr.body, enr.status);
      return route.abort();
    }
    if (path === '/api/agent-mail/enrollment/manifest') {
      return json({ error: 'legacy_manifest_forbidden' }, 410);
    }
    if (path === '/api/agent-mail/enrollment/start') {
      const body = JSON.parse(req.postData() || '{}');
      if (body.passcode !== PASS) return json({ error: 'bad_passcode' }, 403);
      if (startReply === 'abort') return route.abort();
      if (startReply.next) enr = { status: 200, body: startReply.next };
      return json(startReply.body || {}, startReply.status || 200);
    }
    if (path === '/api/projects') return json([CLAYRUNE_PROJECT]);
    if (path === '/api/config') return json(wizard ? { setup_completed: false, default_provider: 'claude' } : { setup_completed: true });
    if (path === '/api/characters') return json([]);
    if (path === '/api/agent/providers') return json(ONE_PROVIDER_OK);
    if (path === '/api/local-auth/status') return json({ configured: true });
    if (path === '/api/update-ping/status') return json({ enabled: true, disclosed: false, env_disabled: false, update_available: false });
    if (path === '/api/update-ping/choice') return json({ ok: true });
    if (path === '/api/system/update/status') return json({ is_git_repo: true, behind: 0, ahead: 0, has_local_changes: false, update_available: false, projects_in_install_dir: [] });
    return route.abort();
  });
  return { ctx, page };
};
const mail = () => reqs.filter((r) => r.path.startsWith('/api/agent-mail'));
const posts = () => reqs.filter((r) => r.method !== 'GET');
const title = (page) => page.evaluate(() => document.querySelector('#setup-overlay .wt-title')?.textContent || '');
const overflow = (page) => page.evaluate(() => ({ doc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  card: (() => { const c = document.querySelector('#setup-overlay .wt-card'); return c ? c.scrollWidth - c.clientWidth : 0; })() }));
const typePrompt = async (page, pw = PASS) => {
  const input = page.locator('input[id^="hp-passcode-"]');
  await input.waitFor({ state: 'visible', timeout: 5000 });
  await input.fill(pw);
  // Shared passcode styling is outside this unit; still assert every control is reachable.
  await mailLayout(page, '[data-modal-id^="__human-proof-"] .modal-content', `passcode-${page.viewportSize().width}-${reqs.length}`, check, SHOTS, false);
  await page.locator('[data-modal-id^="__human-proof-"] .btn-add').click();
};
const manifestGets = () => reqs.filter((r) => r.path === '/api/agent-mail/enrollment/manifest');
const starts = () => reqs.filter((r) => r.path === '/api/agent-mail/enrollment/start');
const cancelPrompt = async (page) => {
  await page.locator('input[id^="hp-passcode-"]').waitFor({ state: 'visible' });
  await page.locator('[data-modal-id^="__human-proof-"] .btn-secondary').click();
  await page.locator('[data-modal-id^="__human-proof-"]').waitFor({ state: 'detached' });
};
const settled = (page, selector) => page.waitForFunction((sel) => {
  const button = document.querySelector(sel + ' [data-am-setup="continue"]');
  return button && !button.disabled;
}, selector);

// Boots the wizard and walks to the step after the update-ping one.
async function toAgentMailStep(page) {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#setup-overlay .wt-title', { timeout: 15000 });
  await page.click('#setup-overlay .wt-btn-primary');                        // Get started
  await page.waitForFunction(() => document.querySelector('#setup-overlay .wt-title')?.textContent === 'Help count Clayrune installs');
  await page.click('#setup-overlay .wt-btn-primary');                        // Next (saves the ping answer)
  await page.waitForFunction(() => /Agent mail|Make it yours/.test(document.querySelector('#setup-overlay .wt-title')?.textContent || ''), { timeout: 8000 });
}

try {
  for (const width of [1000, 390]) {
    const W = `@${width}`;
    console.log(`\n== ${W} ==`);
    wizard = true;
    // Existing installations (including in-progress signup) do not get a fresh setup.
    for (const state of ['pending', 'verified', 'active', 'held', 'expired']) {
      enr = { status: 200, body: pull(state) }; reqs = [];
      const { ctx, page } = await fresh(width);
      await toAgentMailStep(page);
      check((await title(page)) === 'Make it yours', `${W} ${state}: fresh setup skipped`);
      check(starts().length === 0 && manifestGets().length === 0, `${W} ${state}: mount sends no start or manifest request`);
      await ctx.close();
    }
    // An unreadable/off route is distinct from a pristine unconfigured install.
    for (const response of [
      { status: 404, body: { error: 'not_found' } },
      { status: 503, body: { error: 'storage_unavailable' } },
      { status: 200, body: { protocol: 'pull', signup: { state: 'not_enrolled' } } },
      { status: 200, body: { ...PULL_NEW, pull_status: { state: 'held' } } },
    ]) {
      enr = response; reqs = [];
      const { ctx, page } = await fresh(width);
      await toAgentMailStep(page);
      check((await title(page)) === 'Make it yours' && starts().length === 0,
        `${W} unavailable/malformed/held passive answer cannot offer signup`);
      await ctx.close();
    }

    // Pristine U1 response, with no manifest source and no deployment config.
    enr = { status: 200, body: PULL_NEW }; reqs = [];
    const { ctx, page } = await fresh(width);
    await toAgentMailStep(page);
    check((await title(page)) === 'Agent mail (optional)', `${W} unconfigured new install keeps first-run setup`);
    check(mail().every((r) => r.method === 'GET' && r.path === '/api/agent-mail/enrollment'),
      `${W} mount is passive local status only; no verification, identity or email start`);
    const intro = await page.locator('#setup-overlay .am-setup').innerText();
    check(/verified owner/.test(intro) && /@clayrune.io/.test(intro) && /verification link/.test(intro)
      && /unlocked Secrets vault/.test(intro) && /dashboard passcode/.test(intro),
      `${W} disclosure names verified owner, service, link, vault and human gate`);
    await mailLayout(page, '#setup-overlay .am-setup', `intro ${W}`, check, SHOTS);
    await mailWizardFooter(page, `intro ${W}`, check);
    const beforeDecline = JSON.stringify(reqs);
    const beforeStore = await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]));
    await page.click('#setup-overlay [data-am-setup="decline"]');
    await page.waitForFunction(() => document.querySelector('#setup-overlay .wt-title')?.textContent === 'Make it yours');
    check(JSON.stringify(reqs) === beforeDecline, `${W} decline makes zero requests (no config, ping, identity or mail)`);
    check(await page.evaluate(() => JSON.stringify([localStorage, sessionStorage])) === beforeStore,
      `${W} decline persists nothing`);
    await page.click('#setup-overlay .wt-actions button:has-text("Back")');
    await page.waitForSelector('#setup-overlay [data-am-setup="declined"]');
    await page.evaluate(() => { delete window.AgentMailManifestSource; });
    const beforeForm = JSON.stringify(reqs);
    await page.click('#setup-overlay [data-am-setup="begin"]');
    await page.fill('#setup-overlay [data-am-setup-input="owner"]', OWNER);
    check(JSON.stringify(reqs) === beforeForm, `${W} opening form and typing send nothing`);
    check(await page.locator('#setup-overlay [data-am-setup="continue"]').isEnabled(),
      `${W} browser manifest source is not a signup prerequisite`);
    await mailLayout(page, '#setup-overlay .am-setup', `form ${W}`, check, SHOTS);
    await page.setViewportSize({ width, height: 400 });
    await mailWizardFooter(page, `short-height ${W}`, check);
    await mailLayout(page, '#setup-overlay .am-setup', `short-height ${W}`, check, SHOTS);
    await page.setViewportSize({ width, height: width === 390 ? 800 : 900 });
    await page.fill('#setup-overlay [data-am-setup-input="owner"]', 'not an address');
    const beforeInvalid = JSON.stringify(reqs);
    await page.click('#setup-overlay [data-am-setup="continue"]');
    // Wait for the error to render instead of reading it on the click tick (flaked ~1 in 7).
    const invalidShown = await page.waitForFunction(
      () => /valid email/.test(document.querySelector('#setup-overlay [data-am-setup="error"]')?.innerText || ''),
      null, { timeout: 3000 }).then(() => true, () => false);
    check(invalidShown, `${W} invalid owner refused locally`);
    check(JSON.stringify(reqs) === beforeInvalid, `${W} invalid owner sends no request`);
    await page.fill('#setup-overlay [data-am-setup-input="owner"]', OWNER);
    await page.click('#setup-overlay [data-am-setup="continue"]');
    await cancelPrompt(page);
    await settled(page, '#setup-overlay');
    check(starts().length === 0 && manifestGets().length === 0, `${W} cancelled passcode creates no request or identity`);

    await page.click('#setup-overlay [data-am-setup="continue"]');
    const countBeforeBad = starts().length;
    await typePrompt(page, 'wrong passcode');
    await page.waitForSelector('[data-modal-id^="__human-proof-"] :text("Wrong dashboard passcode.")');
    check(starts().length === countBeforeBad + 1 && enr.body.signup.state === 'not_enrolled',
      `${W} failed passcode is refused; no enrollment transition`);
    await cancelPrompt(page);
    await settled(page, '#setup-overlay');
    check(await page.locator('#setup-overlay [data-am-setup-input="owner"]').count() === 1,
      `${W} failed/cancelled passcode leaves setup form available`);

    for (const [label, reply] of [
      ['locked vault', { status: 409, body: { error: 'vault_locked' } }],
      ['service not released', { status: 409, body: { error: 'deployment_unconfigured' } }],
      ['storage unavailable', { status: 503, body: { error: 'storage_unavailable' } }],
    ]) {
      startReply = reply;
      await page.click('#setup-overlay [data-am-setup="continue"]');
      await typePrompt(page);
      await page.waitForSelector('#setup-overlay [data-am-setup="message"][data-tone="bad"], #setup-overlay [data-am-setup="message"][data-tone="warn"]');
      check(await page.locator('#setup-overlay [data-am-setup-input="owner"]').count() === 1,
        `${W} ${label}: refusal stays on the form`);
    }
    // Signup._run can return a held result with HTTP 200 (e.g. locked vault).
    startReply = { status: 200, body: { protocol: 'pull', state: 'held', reason: 'vault_locked' } };
    await page.click('#setup-overlay [data-am-setup="continue"]');
    await typePrompt(page);
    await page.waitForSelector('#setup-overlay [data-am-setup="error"]');
    check(!await page.locator('#setup-overlay [data-am-setup="started"]').count()
      && /has not completed/.test(await page.locator('#setup-overlay [data-am-setup="error"]').innerText()),
      `${W} HTTP 200 held signup cannot announce setup started`);
    reqs = [];
    startReply = { status: 200, body: { protocol: 'pull', ...PULL_PENDING.signup }, next: PULL_PENDING };
    await page.click('#setup-overlay [data-am-setup="continue"]');
    // Clicking Continue opens only the local passcode check, not signup.
    await page.locator('input[id^="hp-passcode-"]').waitFor({ state: 'visible' });
    check(starts().length === 0, `${W} Continue cannot start before typed human proof`);
    await typePrompt(page);
    await page.waitForSelector('#setup-overlay [data-am-setup="started"]');
    const sent = starts();
    check(sent.length === 1 && same(JSON.parse(sent[0].body), { owner: OWNER_SENT, consent_version: 'v1', passcode: PASS }),
      `${W} one start POST contains only normalized owner, fixed consent v1 and human proof`, JSON.stringify(sent));
    check(manifestGets().length === 0 && posts().length === 1, `${W} no legacy manifest request or extra POST`);
    const forgotten = await page.evaluate(([p, o]) => !document.documentElement.innerHTML.includes(p)
      && !document.documentElement.innerHTML.toLowerCase().includes(o)
      && !JSON.stringify([localStorage, sessionStorage]).includes(p), [PASS, OWNER_SENT]);
    check(forgotten, `${W} passcode and owner are cleared from page after start`);
    const over = await overflow(page);
    check(over.doc <= 1 && over.card <= 1, `${W} started state has no horizontal overflow`);
    await mailLayout(page, '#setup-overlay .am-setup', `started ${W}`, check, SHOTS);
    await mailWizardFooter(page, `started ${W}`, check);
    await page.screenshot({ path: join(SHOTS, `setup-pull-started-${width}.png`) });
    await ctx.close();

    // Existing Settings hook regression; U3 owns discovery with the pull DTO.
    wizard = false; enr = { status: 200, body: NOT_ENROLLED }; reqs = [];
    const settings = await fresh(width, true);
    await settings.page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await settings.page.waitForFunction(() => typeof window.openSettings === 'function' && !!window.AgentMailSetup);
    await settings.page.evaluate(() => window.openSettings());
    await settings.page.waitForSelector('#agent-mail-status-section', { state: 'attached' });
    await settings.page.evaluate(() => {
      window.drillSettings('connect');
      const secs = [...document.querySelectorAll('#settings-detail .settings-detail-pane[data-cat="connect"] .settings-section')];
      window.drillSettingsSub(secs.findIndex((s) => s.id === 'agent-mail-status-section'));
    });
    await settings.page.waitForSelector('#agent-mail-setup-slot .am-setup', { state: 'visible' });
    check(starts().length === 0 && manifestGets().length === 0, `${W} existing Settings mount remains passive`);
    const settingsBefore = JSON.stringify(reqs);
    await settings.page.click('#agent-mail-setup-slot [data-am-setup="decline"]');
    await settings.page.waitForSelector('#agent-mail-setup-slot [data-am-setup="declined"]');
    check(JSON.stringify(reqs) === settingsBefore, `${W} Settings decline sends nothing`);
    // U3 owns pull discovery/repaint. Keep its current legacy passive answer
    // for this real mount regression, while asserting U2 sends the pull POST.
    await settings.page.click('#agent-mail-setup-slot [data-am-setup="begin"]');
    await settings.page.fill('#agent-mail-setup-slot [data-am-setup-input="owner"]', OWNER);
    await mailLayout(settings.page, '#agent-mail-controls-host', `settings-form ${W}`, check, SHOTS);
    reqs = [];
    await settings.page.click('#agent-mail-setup-slot [data-am-setup="continue"]');
    await cancelPrompt(settings.page);
    await settled(settings.page, '#agent-mail-setup-slot');
    check(starts().length === 0, `${W} Settings cancelled proof sends no start`);
    await settings.page.click('#agent-mail-setup-slot [data-am-setup="continue"]');
    await typePrompt(settings.page);
    await settings.page.waitForFunction(() => !document.querySelector('[data-modal-id^="__human-proof-"]'));
    check(starts().length === 1 && same(JSON.parse(starts()[0].body), { owner: OWNER_SENT, consent_version: 'v1', passcode: PASS }),
      `${W} Settings component uses the same human-only pull start body`);
    check(mail().some((r) => r.method === 'GET' && r.path === '/api/agent-mail/enrollment') && manifestGets().length === 0,
      `${W} Settings start refreshes status without browser manifest fetch`);
    await settings.ctx.close();
  }
  check(pageErrors.length === 0, 'no page errors', `page errors: ${pageErrors.join(' | ')}`);
} catch (e) {
  fail('smoke threw: ' + (e && e.stack || e));
}
await browser.close();
console.log(`\nAgent-mail setup: ${passed} passed, ${bad} failed`);
process.exit(bad ? 1 : 0);
