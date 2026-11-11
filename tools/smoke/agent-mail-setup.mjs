#!/usr/bin/env node
/**
 * Agent-mail SETUP step (MC-1166 unit 7e-2): static/js/agent-mail-setup.js, in the
 * first-run wizard and in Settings > Connectivity > Agent mail (nothing enrolled).
 *
 * `agent_mail_enrollment_enabled` is off on a real install, so the enrollment route is a
 * route-mocked synthetic fixture served to the real static/ files. 1000px and 390px.
 *
 * Pins:
 *  - the wizard step is skipped when the route answers 404 (feature off) or the install is
 *    already enrolled, and shown when it answers "not enrolled";
 *  - DECLINE sends nothing: no POST to /api/agent-mail, no /api/config write, no extra
 *    update-ping call, nothing stored, and the typed address never appears in any request;
 *  - typing an address sends nothing until Continue; with no signed-manifest source,
 *    Continue is disabled and says why (no signing path exists in this install);
 *  - with a manifest source, a bad address is refused locally, a closed passcode prompt
 *    sends nothing, a good Continue sends ONE passcode-gated POST /start with
 *    { owner, manifest, signature, passcode }, and a 409/503 is a clear state;
 *  - in Settings the same step mounts under Controls, declines for free, and a started
 *    enrollment hands over to the status and its next control.
 *  - layout/reachability for each state, including every manifest failure; short-height
 *    wizard Back/Next stay pinned while the form scrolls. Screenshots: _scratch/7e3/.
 *
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
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const NOW = 1794249600000;
const ready = { tunnel: true, claude_extraction: true, machine_custody: true };
const base = { enabled: false, revocations_pending: 0, now_ms: NOW, prerequisites: ready,
  mail: { blocked: 0, uncertain: 0, retryable: 0, outbound_uncertain: 0, hold_reasons: {} }, recovery: { state: 'not_running' } };
const NOT_ENROLLED = { ...base, stage: 'not_enrolled' };
const STARTED = { ...base, stage: 'pending', generation: 1, public_key: 'pk', service_fingerprint: 'fp', owner_hint: 'o***@example.test',
  assigned: false, remote_started: true, confirmed: false, ownership: false, ownership_expires_ms: 0,
  proof: { state: 'none', expires_ms: 0, activation: 'none' } };
const ENROLLED = { ...STARTED, assigned: true };

const CLAYRUNE_PROJECT = {
  id: 'clayrune', name: 'Clayrune', status: 'active', domain: 'general', _is_onboarding_project: true, emoji: '', description: '',
  summary: 'tour', current_task: 'Tour Clayrune', next_action: '', blocked: false, blocked_reason: null, activity_log: [], backlog: [],
  project_path: '/smoke/clayrune', last_updated: '2026-09-14T00:00:00Z', last_updated_relative: 'today', last_completed: null,
  live_agent: null, display_order: 0, provider: 'claude', use_streaming_agent: true, roster: [],
};
const ONE_PROVIDER_OK = { providers: [{ name: 'claude', display_name: 'Claude Code', installed: true, in_use: true, default: true, auth_status: 'ok' }], default: 'claude' };

const MANIFEST = [1, 2, 3];
const SIGNATURE = 'c2lnbmF0dXJl';

// ── the fake server (shared by every scenario; reset between them) ───────────
let enr = { status: 404, body: { error: 'not_found' } };   // GET /api/agent-mail/enrollment
let startReply = { status: 200, body: { ok: true }, next: STARTED };
const GOOD_MANIFEST = { status: 200, body: { manifest: MANIFEST, signature: SIGNATURE, sequence: 1, expires_at_ms: NOW + 100000 } };
let manifestReply = GOOD_MANIFEST;                         // GET /api/agent-mail/enrollment/manifest (the server's verified fetch)
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
    reqs.push({ method: req.method(), path, search: url.search, body: req.postData() || '' });
    if (path === '/api/agent-mail/enrollment') {
      if (req.method() === 'GET') return json(enr.body, enr.status);
      return route.abort();
    }
    if (path === '/api/agent-mail/enrollment/manifest') {
      if (manifestReply === 'abort') return route.abort();
      return json(manifestReply.body, manifestReply.status);
    }
    if (path === '/api/agent-mail/enrollment/start') {
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
// 7e-2m: the REAL static/js/agent-mail-manifest-source.js loads with the page and asks the (mocked)
// /api/agent-mail/enrollment/manifest route. dropSource/useSource take it away and put it back so the
// "no source at all" fail-closed branch of the setup step stays covered.
const dropSource = (page) => page.evaluate(() => { window._amSrc = window.AgentMailManifestSource; delete window.AgentMailManifestSource; });
const useSource = (page) => page.evaluate(() => { window.AgentMailManifestSource = window._amSrc; });
const manifestGets = () => reqs.filter((r) => r.method === 'GET' && r.path === '/api/agent-mail/enrollment/manifest');

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

    // ── 1. first-run: feature off (404) -> no step ──
    {
      const { ctx, page } = await fresh(width);
      wizard = true; reqs = []; enr = { status: 404, body: { error: 'not_found' } };
      await toAgentMailStep(page);
      check((await title(page)) === 'Make it yours', `first-run ${W}: feature off -> the step is skipped`, `first-run ${W}: landed on "${await title(page)}"`);
      check(!posts().some((r) => r.path.startsWith('/api/agent-mail')), `first-run ${W}: nothing posted to agent-mail`, `first-run ${W}: ${JSON.stringify(mail())}`);
      await ctx.close();
    }

    // ── 2. first-run: already enrolled -> no step ──
    {
      const { ctx, page } = await fresh(width);
      reqs = []; enr = { status: 200, body: ENROLLED };
      await toAgentMailStep(page);
      check((await title(page)) === 'Make it yours', `first-run ${W}: already enrolled -> the step is skipped`, `first-run ${W}: landed on "${await title(page)}"`);
      await ctx.close();
    }

    // ── 3. first-run: not enrolled -> step shown; DECLINE is free ──
    {
      const { ctx, page } = await fresh(width);
      reqs = []; enr = { status: 200, body: NOT_ENROLLED };
      await toAgentMailStep(page);
      check((await title(page)) === 'Agent mail (optional)', `decline ${W}: the step is shown`, `decline ${W}: landed on "${await title(page)}"`);
      const intro = await page.evaluate(() => ({ text: document.querySelector('#setup-overlay .am-setup')?.textContent || '',
        buttons: [...document.querySelectorAll('#setup-overlay .am-setup [data-am-setup]')].map((b) => b.dataset.amSetup) }));
      check(same(intro.buttons, ['begin', 'decline']) && /Declining sends nothing/.test(intro.text), `decline ${W}: says plainly that declining sends nothing`, `decline ${W}: ${JSON.stringify(intro)}`);
      const o = await overflow(page);
      check(o.doc <= 1 && o.card <= 1, `decline ${W}: no horizontal overflow`, `decline ${W}: overflow ${JSON.stringify(o)}`);
      const prim = await page.evaluate(() => ({ accent: [...document.querySelectorAll('#setup-overlay .wt-card .wt-btn-primary, #setup-overlay .wt-card .am-primary')].length,
        text: document.querySelector('#setup-overlay .wt-card')?.textContent.includes('—') }));
      check(prim.accent === 1 && !prim.text, `decline ${W}: exactly one accent button on the step and no em-dash`, `decline ${W}: ${JSON.stringify(prim)}`);
      await page.screenshot({ path: join(SHOTS, `setup-firstrun-intro-${width}.png`) });
      await mailLayout(page, '#setup-overlay .am-setup', `intro ${W}`, check, SHOTS);
      await mailWizardFooter(page, `intro ${W}`, check);
      const before = reqs.length;
      const pingBefore = reqs.filter((r) => r.path === '/api/update-ping/choice').length;
      await page.click('#setup-overlay [data-am-setup="decline"]');
      await page.waitForFunction(() => document.querySelector('#setup-overlay .wt-title')?.textContent === 'Make it yours', { timeout: 5000 });
      check(reqs.length === before, `decline ${W}: No thanks made NO request at all (${reqs.length - before} new)`, `decline ${W}: new requests ${JSON.stringify(reqs.slice(before))}`);
      check(reqs.filter((r) => r.path === '/api/update-ping/choice').length === pingBefore && !reqs.some((r) => r.path === '/api/config' && r.method !== 'GET'),
        `decline ${W}: no ping or config change`, `decline ${W}: ${JSON.stringify(reqs.slice(before))}`);
      check(!posts().some((r) => r.path.startsWith('/api/agent-mail')), `decline ${W}: no POST to agent-mail`, `decline ${W}: ${JSON.stringify(mail())}`);
      const stored = await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]));
      check(!/agent[-_]?mail/i.test(stored.replace('agent-mail-status', '')), `decline ${W}: nothing about it stored`, `decline ${W}: storage ${stored.slice(0, 200)}`);

      // back to it: it remembers it was declined, offers a way back in, and still sends nothing
      await page.click('#setup-overlay .wt-actions button:has-text("Back")');
      await page.waitForFunction(() => document.querySelector('#setup-overlay .wt-title')?.textContent === 'Agent mail (optional)');
      const st = await page.evaluate(() => ({ ph: document.querySelector('#setup-overlay .am-setup')?.dataset.amSetupPhase,
        b: [...document.querySelectorAll('#setup-overlay .am-setup [data-am-setup]')].map((x) => x.dataset.amSetup) }));
      check(st.ph === 'declined' && st.b.includes('begin'), `decline ${W}: coming back shows it stayed declined, with a way in`, `decline ${W}: ${JSON.stringify(st)}`);
      await mailLayout(page, '#setup-overlay .am-setup', `declined ${W}`, check, SHOTS);
      await mailWizardFooter(page, `declined ${W}`, check);

      // ── 4. form: no source -> blocked; typing sends nothing ──
      check(manifestGets().length === 0, `decline ${W}: showing and declining the step never asked for the manifest`, `decline ${W}: ${JSON.stringify(manifestGets())}`);
      check(await page.evaluate(() => typeof window.AgentMailManifestSource?.get === 'function'), `form ${W}: the real manifest source is installed by index.html`, `form ${W}: no AgentMailManifestSource on window`);
      await dropSource(page);
      await page.click('#setup-overlay [data-am-setup="begin"]');
      await page.fill('#setup-overlay [data-am-setup-input="owner"]', OWNER);
      await page.waitForTimeout(150);
      const f = await page.evaluate(() => ({ cont: document.querySelector('#setup-overlay [data-am-setup="continue"]')?.disabled,
        blocked: document.querySelector('#setup-overlay [data-am-setup="blocked"]')?.textContent || '' }));
      check(f.cont === true && /Setup cannot begin yet/.test(f.blocked), `form ${W}: no manifest source -> Continue disabled and says why`, `form ${W}: ${JSON.stringify(f)}`);
      check(!reqs.some((r) => (r.body + r.path + r.search).toLowerCase().includes(OWNER_SENT)), `form ${W}: the typed address went nowhere`, `form ${W}: leaked in ${JSON.stringify(reqs.filter((r) => (r.body + r.path).toLowerCase().includes(OWNER_SENT)))}`);
      await page.screenshot({ path: join(SHOTS, `setup-firstrun-blocked-${width}.png`) });
      await mailLayout(page, '#setup-overlay .am-setup', `blocked ${W}`, check, SHOTS);
      await mailWizardFooter(page, `blocked ${W}`, check);
      await page.evaluate(() => document.querySelector('#setup-overlay [data-am-setup="continue"]').removeAttribute('disabled'));
      await page.click('#setup-overlay [data-am-setup="continue"]');
      await page.waitForTimeout(150);
      const e1 = await page.evaluate(() => document.querySelector('#setup-overlay [data-am-setup="error"]')?.textContent || '');
      check(/could not be obtained/.test(e1) && !posts().some((r) => r.path.startsWith('/api/agent-mail')) && !(await page.locator('input[id^="hp-passcode-"]').count()),
        `form ${W}: even forced, Continue without a signed manifest sends nothing and asks no passcode`, `form ${W}: error "${e1}" posts ${JSON.stringify(posts())}`);

      // ── 5. with a manifest source ──
      await useSource(page);
      await page.click('#setup-overlay [data-am-setup="back"]'); await page.click('#setup-overlay [data-am-setup="begin"]');
      const cont = await page.evaluate(() => ({ off: document.querySelector('#setup-overlay [data-am-setup="continue"]')?.disabled,
        blocked: !!document.querySelector('#setup-overlay [data-am-setup="blocked"]') }));
      check(cont.off === false && !cont.blocked, `form ${W}: with the manifest source Continue is enabled`, `form ${W}: ${JSON.stringify(cont)}`);
      await page.fill('#setup-overlay [data-am-setup-input="owner"]', 'not an address');
      await page.click('#setup-overlay [data-am-setup="continue"]');
      await page.waitForTimeout(150);
      check(manifestGets().length === 0, `form ${W}: a bad address is refused before the manifest is asked for`, `form ${W}: ${JSON.stringify(manifestGets())}`);
      const e2 =await page.evaluate(() => document.querySelector('#setup-overlay [data-am-setup="error"]')?.textContent || '');
      await mailLayout(page, '#setup-overlay .am-setup', `invalid-address ${W}`, check, SHOTS);
      await mailWizardFooter(page, `invalid-address ${W}`, check);
      check(/valid email/.test(e2) && !(await page.locator('input[id^="hp-passcode-"]').count()) && !posts().some((r) => r.path.startsWith('/api/agent-mail')),
        `form ${W}: a bad address is refused locally, no prompt, no request`, `form ${W}: "${e2}"`);

      await page.fill('#setup-overlay [data-am-setup-input="owner"]', OWNER);
      await page.click('#setup-overlay [data-am-setup="continue"]');
      await page.locator('input[id^="hp-passcode-"]').waitFor({ state: 'visible' });
      await page.evaluate(() => { const w = document.querySelector('[data-modal-id^="__human-proof-"]'); window._hpCancel(w.dataset.modalId); });
      await page.waitForTimeout(200);
      check(!posts().some((r) => r.path.startsWith('/api/agent-mail')), `form ${W}: closing the passcode prompt sent nothing`, `form ${W}: ${JSON.stringify(posts())}`);
      check(await page.evaluate(() => !!document.querySelector('#setup-overlay [data-am-setup-input="owner"]')), `form ${W}: still on the form`, `form ${W}: form gone`);

      // 7e-2m: every way the server's verified manifest fetch can fail ends in plain words, with no
      // passcode prompt and no POST (nothing is ever sent without a verified manifest)
      for (const [label, reply, has] of [
        ['bad_signature (409)', { status: 409, body: { error: 'bad_signature', detail: 'manifest_signature' } }, 'signature did not verify'],
        ['stale (409)', { status: 409, body: { error: 'stale', detail: 'manifest_time' } }, 'out of date or not valid yet'],
        ['refused (409)', { status: 409, body: { error: 'refused', detail: 'oversize' } }, 'not in the expected form'],
        ['not_configured (409)', { status: 409, body: { error: 'not_configured' } }, 'no agent mail service address'],
        ['unreachable (502)', { status: 502, body: { error: 'unreachable', detail: 'http_404' } }, 'could not be reached'],
        ['feature off (404)', { status: 404, body: { error: 'not_found' } }, 'not turned on'],
        ['unreadable answer (200)', { status: 200, body: { nonsense: true } }, 'could not be read'],
        ['server unreachable', 'abort', 'Could not reach this computer'],
      ]) {
        manifestReply = reply; reqs.length = 0;
        // drop the previous case's message so the wait below can only see this click's answer
        await page.evaluate(() => document.querySelector('#setup-overlay [data-am-setup="error"]')?.remove());
        await page.click('#setup-overlay [data-am-setup="continue"]');
        await page.waitForFunction(() => !!document.querySelector('#setup-overlay [data-am-setup="error"]'), { timeout: 5000 });
        const t = await page.evaluate(() => document.querySelector('#setup-overlay [data-am-setup="error"]').textContent);
        await mailLayout(page, '#setup-overlay .am-setup', `manifest-${label} ${W}`, check, SHOTS);
        await mailWizardFooter(page, `manifest-${label} ${W}`, check);
        check(t.includes(has) && /Nothing was sent/.test(t) && manifestGets().length === 1
          && !posts().length && !(await page.locator('input[id^="hp-passcode-"]').count()),
          `manifest ${W}: ${label} -> plain words, one GET, no prompt, no POST`, `manifest ${W}: ${label}: "${t}" gets=${manifestGets().length} posts=${JSON.stringify(posts())}`);
        check(!/bad_signature|manifest_|http_404|oversize/.test(t), `manifest ${W}: ${label} shows words, not codes`, `manifest ${W}: ${label} leaked a code: "${t}"`);
        check(await page.evaluate(() => !document.querySelector('#setup-overlay [data-am-setup="continue"]').disabled), `manifest ${W}: ${label} leaves Continue available to retry`, `manifest ${W}: ${label}: Continue stuck disabled`);
      }
      await page.screenshot({ path: join(SHOTS, `setup-firstrun-manifest-error-${width}.png`) });
      // Short phone/desktop height (including room lost to a software keyboard).
      await page.setViewportSize({ width, height: 400 });
      await mailWizardFooter(page, `short-height ${W}`, check);
      await mailLayout(page, '#setup-overlay .am-setup', `short-height ${W}`, check, SHOTS);
      await page.setViewportSize({ width, height: width === 390 ? 800 : 900 });
      manifestReply = GOOD_MANIFEST;

      // refusals stay on the form with a clear message
      for (const [label, reply, has] of [
        ['manifest_signature (409)', { status: 409, body: { error: 'manifest_signature' } }, 'signature did not verify'],
        ['storage_unavailable (503)', { status: 503, body: { error: 'storage_unavailable' } }, 'mail store could not be read or written'],
        ['operational_adapter_unavailable (503)', { status: 503, body: { error: 'operational_adapter_unavailable' } }, 'service adapter is not installed'],
        ['human_required (403)', { status: 403, body: { error: 'human_required', blocking_sessions: [{ session_id: 's-1', trigger: 'steward' }] } }, 'unattended agent sessions are running'],
      ]) {
        startReply = reply; reqs.length = 0;
        await page.click('#setup-overlay [data-am-setup="continue"]');
        await typePrompt(page);
        await page.waitForFunction(() => !!document.querySelector('#setup-overlay [data-am-setup="message"]'), { timeout: 8000 });
        const m = await page.evaluate(() => document.querySelector('#setup-overlay [data-am-setup="message"]').textContent);
        await mailLayout(page, '#setup-overlay .am-setup', `start-${label} ${W}`, check, SHOTS);
        await mailWizardFooter(page, `start-${label} ${W}`, check);
        check(m.includes(has) && (await title(page)) === 'Agent mail (optional)', `start ${W}: ${label} is shown and the step stays put`, `start ${W}: ${label}: "${m}"`);
      }

      // the good one: ONE passcode-gated POST with the right body
      startReply = { status: 200, body: { ok: true }, next: STARTED }; reqs.length = 0;
      await page.click('#setup-overlay [data-am-setup="continue"]');
      await typePrompt(page);
      await page.waitForSelector('#setup-overlay [data-am-setup="started"]', { timeout: 8000 });
      const sp = posts().filter((r) => r.path === '/api/agent-mail/enrollment/start');
      const sb = sp[0] ? JSON.parse(sp[0].body) : {};
      check(sp.length === 1 && posts().length === 1, `start ${W}: exactly one POST, to /start`, `start ${W}: ${JSON.stringify(posts())}`);
      check(sb.passcode === PASS && sb.owner === OWNER_SENT && same(sb.manifest, MANIFEST) && sb.signature === SIGNATURE && Object.keys(sb).length === 4,
        `start ${W}: body is {owner (lower-cased), manifest, signature, passcode}`, `start ${W}: body ${JSON.stringify(sb)}`);
      const gone = await page.evaluate(([p, o]) => ({ dom: document.documentElement.innerHTML.includes(p), owner: document.documentElement.innerHTML.toLowerCase().includes(o),
        store: JSON.stringify(localStorage).includes(p) + JSON.stringify(sessionStorage).includes(p) }), [PASS, OWNER_SENT]);
      check(!gone.dom && !gone.owner && !gone.store, `start ${W}: neither passcode nor address is left in the page`, `start ${W}: ${JSON.stringify(gone)}`);
      const o2 = await overflow(page);
      check(o2.doc <= 1 && o2.card <= 1, `start ${W}: no horizontal overflow`, `start ${W}: overflow ${JSON.stringify(o2)}`);
      await page.screenshot({ path: join(SHOTS, `setup-firstrun-started-${width}.png`) });
      await mailLayout(page, '#setup-overlay .am-setup', `started ${W}`, check, SHOTS);
      await mailWizardFooter(page, `started ${W}`, check);
      await page.click('#setup-overlay .wt-btn-primary');
      await page.waitForFunction(() => document.querySelector('#setup-overlay .wt-title')?.textContent === 'Make it yours', { timeout: 5000 });
      ok(`start ${W}: Next moves on to "Make it yours"`);
      await ctx.close();
    }

    // ── 6. Settings: nothing enrolled -> the step is under Controls ──
    {
      const { ctx, page } = await fresh(width, true);
      wizard = false; reqs = []; enr = { status: 200, body: NOT_ENROLLED }; startReply = { status: 200, body: { ok: true }, next: STARTED };
      await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => typeof window.openSettings === 'function' && !!window.AgentMailSetup, { timeout: 20000 });
      const open = async () => {
        await page.evaluate(() => window.closeModalById && window.closeModalById('__settings'));
        await page.evaluate(() => window.openSettings());
        await page.waitForSelector('#agent-mail-status-section', { state: 'attached', timeout: 10000 });
        await page.evaluate(() => window.drillSettings('connect'));
        await page.evaluate(() => {
          const secs = [...document.querySelectorAll('#settings-detail .settings-detail-pane[data-cat="connect"] .settings-section')];
          window.drillSettingsSub(secs.findIndex((s) => s.id === 'agent-mail-status-section'));
        });
        await page.waitForSelector('#agent-mail-setup-slot .am-setup', { state: 'visible', timeout: 8000 });
      };
      await open();
      const before = reqs.length;
      await page.click('#agent-mail-setup-slot [data-am-setup="decline"]');
      await page.waitForSelector('#agent-mail-setup-slot [data-am-setup="declined"]');
      check(reqs.length === before && !posts().length, `settings ${W}: No thanks sent nothing`, `settings ${W}: ${JSON.stringify(reqs.slice(before))}`);
      await dropSource(page);
      await page.click('#agent-mail-setup-slot [data-am-setup="begin"]');
      await page.fill('#agent-mail-setup-slot [data-am-setup-input="owner"]', OWNER);
      check(!reqs.some((r) => (r.body + r.path + r.search).toLowerCase().includes(OWNER_SENT)), `settings ${W}: typing the address sent nothing`, `settings ${W}: leaked`);
      const b = await page.evaluate(() => document.querySelector('#agent-mail-setup-slot [data-am-setup="continue"]')?.disabled);
      check(b === true, `settings ${W}: no manifest source -> Continue disabled`, `settings ${W}: Continue enabled`);
      await useSource(page);
      await page.click('#agent-mail-setup-slot [data-am-setup="back"]'); await page.click('#agent-mail-setup-slot [data-am-setup="begin"]');
      await page.fill('#agent-mail-setup-slot [data-am-setup-input="owner"]', OWNER);
      const l0 = await page.evaluate(() => ({ over: document.documentElement.scrollWidth - document.documentElement.clientWidth }));
      check(l0.over <= 1, `settings ${W}: form has no horizontal overflow`, `settings ${W}: overflow ${l0.over}`);
      await mailLayout(page, '#agent-mail-controls-host', `settings-form ${W}`, check, SHOTS);
      reqs.length = 0;
      await page.click('#agent-mail-setup-slot [data-am-setup="continue"]');
      await typePrompt(page);
      await page.waitForSelector('#agent-mail-actions [data-am="outcome"][data-action="start"]', { timeout: 8000 });
      const oc = await page.evaluate(() => ({ tone: document.querySelector('#agent-mail-actions [data-am="outcome"]').dataset.tone,
        controls: [...document.querySelectorAll('#agent-mail-actions [data-am-control]')].map((c) => c.dataset.amControl),
        setup: !!document.querySelector('#agent-mail-setup-slot') }));
      check(oc.tone === 'good' && same(oc.controls, ['resume', 'disable']) && !oc.setup, `settings ${W}: started -> good banner, status re-read, next controls ["resume","disable"]`, `settings ${W}: ${JSON.stringify(oc)}`);
      check(posts().length === 1 && posts()[0].path === '/api/agent-mail/enrollment/start', `settings ${W}: exactly one POST, to /start`, `settings ${W}: ${JSON.stringify(posts())}`);
      await ctx.close();
    }
  }
  check(pageErrors.length === 0, 'no page errors', `page errors: ${pageErrors.join(' | ')}`);
} catch (e) {
  fail('smoke threw: ' + (e && e.stack || e));
}
await browser.close();
console.log(bad ? `\n${bad} check(s) FAILED` : '\nAll agent-mail setup checks passed');
process.exit(bad ? 1 : 0);
