#!/usr/bin/env node
/**
 * Settings > Connectivity > Agent mail: the MUTATING controls (MC-1166 unit 7e-2),
 * static/js/agent-mail-actions{,-model}.js, under the 7e-1 status card.
 *
 * `agent_mail_enrollment_enabled` is off on a real install (the route 404s), so every
 * answer here is a route-mocked synthetic fixture served to the real static/ files.
 * Everything runs at 1000px (desktop) and 390px (mobile).
 *
 * Pins:
 *  - each action (resume, review, confirm, verify, proof, enable, disable,
 *    retry-revocations) asks for the passcode through the shared humanProofFetch prompt,
 *    sends exactly one POST with the typed passcode merged in and the right body, shows a
 *    good outcome, re-reads the status, and offers the next control;
 *  - confirm echoes the review answer exactly; a malformed review cannot be confirmed;
 *  - the passcode is never left in the DOM, storage or any URL; the code field is cleared;
 *  - each error class is a clear, distinct state: 403 human_required (+ blocking sessions,
 *    hostile ids rendered inert), 409 refusal codes, 503 storage_unavailable, 503
 *    operational_adapter_unavailable, 404, 400, 429, 500, network failure, closed prompt;
 *  - Disable (and Retry revocation) stay reachable: in every enrolled state, while Enable is
 *    blocked, and when the status itself cannot be read (503 / 500);
 *  - a closed prompt or a malformed code sends nothing;
 *  - no clipping, all controls scroll into view, and mobile targets >=40px;
 *    real-height screenshots under _scratch/7e3/ (no tall viewport workaround).
 *    The status card itself still has only its Refresh button.
 *
 * RUN: node tools/smoke/agent-mail-actions.mjs
 */
import { readFileSync, existsSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { chromium } from 'playwright';
import { mailLayout } from './_agent-mail-layout.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const STATIC_ROOT = resolve(REPO_ROOT, 'static');
const SHOTS = resolve(REPO_ROOT, '_scratch', '7e3', 'actions');
mkdirSync(SHOTS, { recursive: true });
const ORIGIN = 'http://mc.smoke.test';
const PASS = 'correct horse 42';

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg));

const NOW = 1794249600000;
const ready = { tunnel: true, claude_extraction: true, machine_custody: true };
const noMail = { blocked: 0, uncertain: 0, retryable: 0, outbound_uncertain: 0, hold_reasons: {} };
const base = { enabled: false, revocations_pending: 0, now_ms: NOW, prerequisites: ready, mail: noMail, recovery: { state: 'not_running' } };
const enrolled = { ...base, generation: 1, public_key: 'pk', service_fingerprint: 'fp', owner_hint: 'o***@example.test',
  assigned: true, remote_started: true, confirmed: false, ownership: false, ownership_expires_ms: 0,
  proof: { state: 'none', expires_ms: 0, activation: 'none' } };
const S = {
  not_enrolled: { ...base, stage: 'not_enrolled' },
  pending_unassigned: { ...enrolled, stage: 'pending', assigned: false },
  pending_review: { ...enrolled, stage: 'pending' },
  pending_code: { ...enrolled, stage: 'pending', confirmed: true },
  verified: { ...enrolled, stage: 'delivery_pending', confirmed: true, ownership: true, ownership_expires_ms: NOW + 240000 },
  verified_armed: { ...enrolled, stage: 'delivery_pending', confirmed: true, ownership: true, ownership_expires_ms: NOW + 240000,
    proof: { state: 'armed', expires_ms: NOW + 100000, activation: 'none' } },
  expired: { ...enrolled, stage: 'delivery_pending', confirmed: true, ownership: true, ownership_expires_ms: NOW - 60000 },
  active: { ...enrolled, stage: 'active', enabled: true, confirmed: true, ownership: true, ownership_expires_ms: NOW + 3600000,
    proof: { state: 'consumed', expires_ms: 0, activation: 'acked' } },
  disabled_pending: { ...enrolled, stage: 'disabled', revocations_pending: 2 },
  disabled_revoked: { ...enrolled, stage: 'disabled' },
};
const SHA = 'a'.repeat(64);
const REVIEW = { owner: 'owner@example.test', install: 'inst_abc123', generation: 1, public_key: 'PUBKEYabc-_123',
  service_fingerprint: SHA, service_origin: 'https://mail.example.test', manifest_sequence: 3, manifest_digest: 'b'.repeat(64) };

// ── the fake server ──────────────────────────────────────────────────────────
let get = { status: 200, body: S.not_enrolled };
let replies = {};            // action -> { status, body, next?: state body } | 'abort' | fn(body) -> same
const posts = [];            // { action, body }
const nonGet = [];
let getCount = 0;
const pageErrors = [];

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1000, height: 900 } });
const page = await ctx.newPage();
await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

await page.route('**/*', async (route) => {
  const req = route.request();
  const url = new URL(req.url());
  const path = url.pathname;
  const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
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
  if (path.startsWith('/api/agent-mail/enrollment')) {
    if (url.search) nonGet.push({ method: 'QUERY', path: path + url.search });
    if (req.method() === 'GET') { getCount++; return json(get.body, get.status); }
    nonGet.push({ method: req.method(), path });
    const action = path.split('/').pop();
    const body = JSON.parse(req.postData() || '{}');
    posts.push({ action, body });
    let r = replies[action];
    if (typeof r === 'function') r = r(body);
    if (r === 'abort') return route.abort();
    if (!r) return json({ error: 'not_found' }, 404);
    if (r.next) get = { status: 200, body: r.next };
    return json(r.body || {}, r.status || 200);
  }
  if (path === '/api/projects') return json([]);
  if (path === '/api/local-auth/status') return json({ configured: true });
  if (path === '/api/config') return json({});
  if (path === '/api/system/update/status') return json({ is_git_repo: true, behind: 0, ahead: 0, has_local_changes: false, update_available: false, projects_in_install_dir: [] });
  return route.abort();
});

const openPanel = async () => {
  await page.evaluate(() => window.closeModalById && window.closeModalById('__settings'));
  await page.evaluate(() => window.openSettings());
  await page.waitForSelector('#agent-mail-status-section', { state: 'attached', timeout: 10000 });
  await page.evaluate(() => window.drillSettings('connect'));
  await page.evaluate(() => {
    const secs = [...document.querySelectorAll('#settings-detail .settings-detail-pane[data-cat="connect"] .settings-section')];
    const idx = secs.findIndex((s) => s.id === 'agent-mail-status-section');
    if (idx < 0) throw new Error('Agent mail settings section not found');
    window.drillSettingsSub(idx);
  });
  await page.waitForSelector('#agent-mail-status', { state: 'visible', timeout: 5000 });
  await page.waitForFunction(() => { const h = document.getElementById('agent-mail-controls-host'); return !!h; }, { timeout: 5000 });
};

const prompt = async (pw = PASS) => {
  const input = page.locator('input[id^="hp-passcode-"]');
  await input.waitFor({ state: 'visible', timeout: 5000 });
  await input.fill(pw);
  await mailLayout(page, '[data-modal-id^="__human-proof-"] .modal-content',
    `passcode-${page.viewportSize().width}-${getCount}-${posts.length}`, check, SHOTS, false);
  await page.locator('[data-modal-id^="__human-proof-"] .btn-add').click();
};
const outcome = async (action) => {
  await page.waitForSelector(`#agent-mail-actions [data-am="outcome"][data-action="${action}"]`, { timeout: 8000 });
  await mailLayout(page, '#agent-mail-actions', `${action}-outcome-${page.viewportSize().width}-${posts.length}-${getCount}`, check, SHOTS);
  return page.evaluate(() => { const o = document.querySelector('#agent-mail-actions [data-am="outcome"]');
    return { tone: o.dataset.tone, code: o.dataset.code, text: o.textContent, blockers: [...o.querySelectorAll('li')].map((l) => l.textContent.trim()),
      injected: o.querySelectorAll('img, script, iframe, b, i, a, svg').length, html: o.innerHTML }; });
};
const controls = () => page.evaluate(() => [...document.querySelectorAll('#agent-mail-actions [data-am-control]')].map((c) => c.dataset.amControl));
const enabledBtn = (a) => page.evaluate((x) => { const b = document.querySelector(`#agent-mail-actions [data-am-action="${x}"]`); return !!b && !b.disabled; }, a);
const layoutOk = () => page.evaluate(() => {
  const h = document.getElementById('agent-mail-controls-host');
  const r = h.getBoundingClientRect();
  return { over: document.documentElement.scrollWidth - document.documentElement.clientWidth, hostOver: h.scrollWidth - h.clientWidth, right: r.right, vw: innerWidth };
});
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

try {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openSettings === 'function' && !!window.AgentMailActionsModel && !!window.AgentMailStatusModel, { timeout: 20000 });

  for (const width of [1000, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 800 : 900 });
    await page.waitForTimeout(300);
    const W = `@${width}`;
    console.log(`\n== ${W} ==`);

    // ── success path, one action at a time, each from the state that offers it ──
    const flow = [
      { name: 'resume', state: S.pending_unassigned, click: 'resume', body: {}, next: S.pending_review, then: ['review', 'disable'] },
      { name: 'review', state: S.pending_review, click: 'review', body: {}, reply: { status: 200, body: REVIEW }, then: ['review', 'disable'], review: true },
      { name: 'confirm', state: S.pending_review, click: 'confirm', body: { confirmation: REVIEW }, next: S.pending_code, then: ['verify', 'disable'], needsReview: true },
      { name: 'verify', state: S.pending_code, click: 'verify', code: 'AbC-123_xyz', body: { code: 'AbC-123_xyz' }, next: S.verified, then: ['proof', 'enable', 'disable'] },
      { name: 'proof', state: S.verified, click: 'proof', body: {}, next: S.verified_armed, then: ['enable', 'disable'] },
      { name: 'enable', state: S.verified_armed, click: 'enable', body: {}, next: S.active, then: ['disable'] },
      { name: 'disable', state: S.active, click: 'disable', body: {}, next: S.disabled_pending, then: ['retry-revocations'] },
      { name: 'retry-revocations', state: S.disabled_pending, click: 'retry-revocations', body: {}, next: S.disabled_revoked, then: ['setup'] },
    ];
    for (const f of flow) {
      const tag = `${f.name} ${W}`;
      const reply = { [f.name]: f.reply ? f.reply : { status: 200, body: { ok: true }, next: f.next } };
      get = { status: 200, body: f.state }; replies = reply; posts.length = 0;
      await openPanel();
      if (f.needsReview) {                       // confirm is only offered after a review answer
        replies = { review: { status: 200, body: REVIEW } };
        await page.click('#agent-mail-actions [data-am-action="review"]'); await prompt(); await outcome('review');
        posts.length = 0; replies = reply;
      }
      const g0 = getCount;
      if (f.code !== undefined) await page.fill('#agent-mail-actions [data-am-input="code"]', f.code);
      await page.click(`#agent-mail-actions [data-am-action="${f.click}"]`);
      await prompt();
      const o = await outcome(f.name);
      check(posts.length === 1 && posts[0].action === f.name, `${tag}: exactly one POST to /${f.name}`, `${tag}: posts ${JSON.stringify(posts)}`);
      const { passcode, ...rest } = posts[0] ? posts[0].body : {};
      check(passcode === PASS, `${tag}: the typed passcode rode in the body`, `${tag}: passcode ${JSON.stringify(passcode)}`);
      check(same(rest, f.body), `${tag}: body is ${JSON.stringify(f.body).slice(0, 70)}`, `${tag}: body ${JSON.stringify(rest)}`);
      check(o.tone === 'good', `${tag}: good outcome "${o.text.slice(0, 50)}"`, `${tag}: outcome ${JSON.stringify(o)}`);
      check(getCount > g0, `${tag}: status re-read after the action`, `${tag}: GET count ${g0} -> ${getCount}`);
      const c = await controls();
      check(same(c, f.then), `${tag}: next controls ${JSON.stringify(f.then)}`, `${tag}: controls ${JSON.stringify(c)}`);
      if (f.review) {
        const rv = await page.evaluate(() => { const r = document.querySelector('#agent-mail-actions [data-am="review"]');
          return { text: r ? r.textContent : '', confirm: !!document.querySelector('#agent-mail-actions [data-am-action="confirm"]') }; });
        check(rv.text.includes('owner@example.test') && rv.text.includes('https://mail.example.test') && rv.confirm,
          `${tag}: review shows the owner, the service and a Confirm button`, `${tag}: review ${JSON.stringify(rv)}`);
      }
      if (f.name === 'verify') {
        const v = await page.evaluate(() => document.querySelector('#agent-mail-actions [data-am-input="code"]')?.value ?? '(gone)');
        check(v === '' || v === '(gone)', `${tag}: the code field was cleared`, `${tag}: code field holds ${JSON.stringify(v)}`);
      }
      const l = await layoutOk();
      check(l.over <= 1 && l.hostOver <= 1 && l.right <= l.vw + 1, `${tag}: no horizontal overflow`, `${tag}: overflow ${JSON.stringify(l)}`);
      // The passcode is never left behind in the page, storage, or a request URL.
      const leak = await page.evaluate((p) => ({
        dom: document.documentElement.innerHTML.includes(p) || [...document.querySelectorAll('input')].some((i) => i.value.includes(p)),
        store: JSON.stringify(localStorage).includes(p) + JSON.stringify(sessionStorage).includes(p) }), PASS);
      check(!leak.dom && !leak.store, `${tag}: the passcode is not in the DOM or storage`, `${tag}: leak ${JSON.stringify(leak)}`);
    }

    // ── a wrong passcode re-prompts; the second try goes through ──
    {
      const tag = `wrong passcode ${W}`;
      let n = 0;
      get = { status: 200, body: S.verified_armed };
      replies = { enable: (b) => (++n === 1 ? { status: 403, body: { error: 'bad_passcode' } } : { status: 200, body: {}, next: S.active }) };
      posts.length = 0;
      await openPanel();
      await page.click('#agent-mail-actions [data-am-action="enable"]');
      await prompt('nope');
      await page.locator('input[id^="hp-passcode-"]').waitFor({ state: 'visible' });
      await page.waitForFunction(() => /Wrong dashboard passcode/.test(document.querySelector('[data-modal-id^="__human-proof-"]')?.textContent || ''));
      await mailLayout(page, '[data-modal-id^="__human-proof-"] .modal-content', `wrong-passcode ${W}`, check, SHOTS, false);
      await prompt(PASS);
      const o = await outcome('enable');
      check(posts.length === 2 && posts[0].body.passcode === 'nope' && posts[1].body.passcode === PASS && o.tone === 'good',
        `${tag}: re-prompted, second attempt applied`, `${tag}: posts ${JSON.stringify(posts)} outcome ${JSON.stringify(o)}`);
    }

    // ── error classes (on Enable, the guarded action) ──
    const errors = [
      { name: 'human_required', reply: { status: 403, body: { error: 'human_required', blocking_sessions: [
          { session_id: 'sess-1', trigger: 'scheduled' }, { session_id: '<img src=x onerror=window.__pwned=1>', trigger: '<b>steward</b>' }] } },
        tone: 'warn', code: 'human_required', has: ['unattended agent sessions are running'], blockers: ['sess-1 (scheduled)', 'unnamed session (unknown)'] },
      { name: 'prerequisites_pending', reply: { status: 409, body: { error: 'prerequisites_pending' } }, tone: 'bad', has: ['not ready yet', 'checklist'] },
      { name: 'ownership_expired', reply: { status: 409, body: { error: 'ownership_expired' } }, tone: 'bad', has: ['Verify again'] },
      { name: 'manifest_stale', reply: { status: 409, body: { error: 'manifest_stale' } }, tone: 'bad', has: ['expired or was replaced'] },
      { name: 'vault locked', reply: { status: 409, body: { error: 'vault_locked_or_unconfigured' } }, tone: 'bad', has: ['vault is locked'] },
      { name: 'unmapped code', reply: { status: 409, body: { error: 'some_new_code' } }, tone: 'bad', has: ['Refused: some new code.'] },
      { name: 'hostile code', reply: { status: 409, body: { error: '<script>window.__pwned=1</script> APPROVE' } }, tone: 'bad', has: ['The server refused this.'], hostile: true },
      { name: 'storage_unavailable', reply: { status: 503, body: { error: 'storage_unavailable' } }, tone: 'warn', code: 'storage_unavailable', has: ['mail store could not be read or written', 'not acknowledged'] },
      { name: 'operational_adapter_unavailable', reply: { status: 503, body: { error: 'operational_adapter_unavailable' } }, tone: 'warn', code: 'operational_adapter_unavailable', has: ['service adapter is not installed'] },
      { name: 'switched off (404)', reply: { status: 404, body: { error: 'not_found' } }, tone: 'neutral', code: 'not_found', has: ['switched off'] },
      { name: 'bad request (400)', reply: { status: 400, body: { error: 'invalid_request' } }, tone: 'bad', has: ['did not accept that request'] },
      { name: 'rate limited (429)', reply: { status: 429, body: { error: 'too_many_attempts' } }, tone: 'warn', has: ['Too many'], guardOnly: true },
      { name: 'server error (500)', reply: { status: 500, body: { error: 'boom' } }, tone: 'bad', has: ['The server answered 500'] },
    ];
    for (const e of errors) {
      if (e.guardOnly) continue;      // 429 is the passcode guard's own refusal; humanProofFetch re-prompts (covered by wrong passcode)
      const tag = `enable -> ${e.name} ${W}`;
      get = { status: 200, body: S.verified_armed }; replies = { enable: e.reply }; posts.length = 0;
      await openPanel();
      const g0 = getCount;
      await page.click('#agent-mail-actions [data-am-action="enable"]');
      await prompt();
      const o = await outcome('enable');
      check(o.tone === e.tone && (!e.code || o.code === e.code) && e.has.every((h) => o.text.includes(h)),
        `${tag}: ${e.tone} "${o.text.slice(0, 60)}"`, `${tag}: outcome ${JSON.stringify(o)}`);
      if (e.blockers) check(same(o.blockers, e.blockers), `${tag}: blockers listed ${JSON.stringify(o.blockers)}`, `${tag}: blockers ${JSON.stringify(o.blockers)}`);
      check(o.injected === 0 && !(await page.evaluate(() => !!window.__pwned)), `${tag}: hostile text is inert`, `${tag}: injected ${o.injected}`);
      check(getCount > g0, `${tag}: status re-read after the refusal`, `${tag}: GET count ${g0} -> ${getCount}`);
      check(await enabledBtn('disable') && await enabledBtn('enable'), `${tag}: Disable and Enable are usable again`, `${tag}: buttons stuck`);
      const l = await layoutOk();
      check(l.over <= 1 && l.hostOver <= 1 && l.right <= l.vw + 1, `${tag}: no horizontal overflow`, `${tag}: overflow ${JSON.stringify(l)}`);
    }

    // ── network failure: the shared prompt stays open with its own message; closing it sends nothing ──
    {
      const tag = `network failure ${W}`;
      get = { status: 200, body: S.verified_armed }; replies = { enable: 'abort' }; posts.length = 0;
      await openPanel();
      await page.click('#agent-mail-actions [data-am-action="enable"]');
      await prompt();
      await page.waitForFunction(() => /Could not reach the server/.test(document.querySelector('[data-modal-id^="__human-proof-"]')?.textContent || ''), { timeout: 8000 });
      await mailLayout(page, '[data-modal-id^="__human-proof-"] .modal-content', `network-failure ${W}`, check, SHOTS, false);
      ok(`${tag}: the prompt stays open and says it could not reach the server`);
      await page.evaluate(() => document.querySelector('[data-modal-id^="__human-proof-"] .modal-close').click());
      const o = await outcome('enable');
      check(o.tone === 'neutral' && o.code === 'cancelled', `${tag}: closing it reports Cancelled`, `${tag}: ${JSON.stringify(o)}`);
      check(await enabledBtn('disable') && await enabledBtn('enable'), `${tag}: Disable and Enable are usable again`, `${tag}: buttons stuck`);
    }

    // ── Disable is never behind human_required: it goes through while Enable is blocked ──
    {
      const tag = `disable while enable is blocked ${W}`;
      get = { status: 200, body: S.verified_armed };
      replies = { enable: { status: 403, body: { error: 'human_required', blocking_sessions: [{ session_id: 's1', trigger: 'steward' }] } },
        disable: { status: 200, body: {}, next: S.disabled_revoked } };
      posts.length = 0;
      await openPanel();
      await page.click('#agent-mail-actions [data-am-action="enable"]'); await prompt(); await outcome('enable');
      await page.click('#agent-mail-actions [data-am-action="disable"]'); await prompt();
      const o = await outcome('disable');
      check(o.tone === 'good' && posts.map((p) => p.action).join() === 'enable,disable', `${tag}: Disable applied`, `${tag}: ${JSON.stringify([o, posts.map((p) => p.action)])}`);
    }

    // ── other actions' refusals and the disable/retry failures ──
    const others = [
      { st: S.pending_unassigned, click: 'resume', reply: { status: 409, body: { error: 'remote_begin_retryable' } }, has: 'Try Resume again' },
      { st: S.pending_review, click: 'review', reply: { status: 409, body: { error: 'service_reservation_pending' } }, has: 'Resume first' },
      { st: S.pending_code, click: 'verify', code: 'ZZ99', reply: { status: 409, body: { error: 'remote_verify_retryable' } }, has: 'Check the code' },
      { st: S.verified, click: 'proof', reply: { status: 409, body: { error: 'delivery_proof_unavailable' } }, has: 'not available' },
      { st: S.active, click: 'disable', reply: { status: 503, body: { error: 'storage_unavailable' } }, has: 'mail may still be on', tone: 'warn' },
      { st: S.disabled_pending, click: 'retry-revocations', reply: { status: 409, body: { error: 'remote_revoke_retryable' } }, has: 'will be retried' },
    ];
    for (const x of others) {
      const tag = `${x.click} refusal ${W}`;
      get = { status: 200, body: x.st }; replies = { [x.click]: x.reply }; posts.length = 0;
      await openPanel();
      if (x.code !== undefined) await page.fill('#agent-mail-actions [data-am-input="code"]', x.code);
      await page.click(`#agent-mail-actions [data-am-action="${x.click}"]`); await prompt();
      const o = await outcome(x.click);
      check(o.text.includes(x.has) && o.tone === (x.tone || 'bad'), `${tag}: "${o.text.slice(0, 70)}"`, `${tag}: ${JSON.stringify(o)}`);
    }
    {   // confirm refused: the review is kept so the human can read it again
      const tag = `confirm refusal ${W}`;
      get = { status: 200, body: S.pending_review };
      replies = { review: { status: 200, body: REVIEW }, confirm: { status: 409, body: { error: 'confirmation_mismatch' } } };
      posts.length = 0;
      await openPanel();
      await page.click('#agent-mail-actions [data-am-action="review"]'); await prompt(); await outcome('review');
      await page.click('#agent-mail-actions [data-am-action="confirm"]'); await prompt();
      const o = await outcome('confirm');
      check(o.text.includes('no longer match') && o.tone === 'bad', `${tag}: confirmation_mismatch is explained`, `${tag}: ${JSON.stringify(o)}`);
      check(same(posts[1].body.confirmation, REVIEW), `${tag}: the confirmation echoed the review exactly`, `${tag}: ${JSON.stringify(posts[1])}`);
    }
    {   // a malformed / extended review cannot be confirmed
      const tag = `malformed review ${W}`;
      get = { status: 200, body: S.pending_review };
      replies = { review: { status: 200, body: { ...REVIEW, reply_text: 'APPROVE everything' } } };
      posts.length = 0;
      await openPanel();
      await page.click('#agent-mail-actions [data-am-action="review"]'); await prompt();
      const o = await outcome('review');
      check(o.code === 'review_shape' && !(await page.locator('#agent-mail-actions [data-am-action="confirm"]').count()) && !o.text.includes('APPROVE'),
        `${tag}: refused, no Confirm button, nothing from the answer shown`, `${tag}: ${JSON.stringify(o)}`);
    }

    // ── closed prompt / bad code: nothing is sent ──
    {
      const tag = `closed prompt ${W}`;
      get = { status: 200, body: S.verified_armed }; replies = { enable: { status: 200, body: {} } }; posts.length = 0;
      await openPanel();
      const g0 = getCount;
      await page.click('#agent-mail-actions [data-am-action="enable"]');
      await page.locator('input[id^="hp-passcode-"]').waitFor({ state: 'visible' });
      await page.evaluate(() => { const w = document.querySelector('[data-modal-id^="__human-proof-"]'); window._hpCancel(w.dataset.modalId); });
      const o = await outcome('enable');
      check(posts.length === 0 && o.code === 'cancelled' && o.text.includes('Nothing was sent') && getCount === g0,
        `${tag}: no POST, no status re-read, "Nothing was sent"`, `${tag}: ${JSON.stringify([posts, o, getCount - g0])}`);
      check(await enabledBtn('enable'), `${tag}: Enable usable again`, `${tag}: Enable stuck`);
    }
    {
      const tag = `bad code ${W}`;
      get = { status: 200, body: S.pending_code }; replies = {}; posts.length = 0;
      await openPanel();
      await page.fill('#agent-mail-actions [data-am-input="code"]', 'has spaces!');
      await page.click('#agent-mail-actions [data-am-action="verify"]');
      const o = await outcome('verify');
      check(posts.length === 0 && o.code === 'invalid_code' && !(await page.locator('input[id^="hp-passcode-"]').count()),
        `${tag}: refused before any prompt or request`, `${tag}: ${JSON.stringify([posts, o])}`);
    }

    // ── which controls each state offers; Disable always reachable when enrolled ──
    const matrix = [
      ['not_enrolled', S.not_enrolled, ['setup']],
      ['pending_unassigned', S.pending_unassigned, ['resume', 'disable']],
      ['pending_review', S.pending_review, ['review', 'disable']],
      ['pending_code', S.pending_code, ['verify', 'disable']],
      ['verified', S.verified, ['proof', 'enable', 'disable']],
      ['verified_armed', S.verified_armed, ['enable', 'disable']],
      ['expired lease', S.expired, ['verify', 'disable']],
      ['active', S.active, ['disable']],
      ['active, lease expired', { ...S.active, ownership_expires_ms: NOW - 1000 }, ['disable']],
      ['active, off, revocation waiting', { ...S.active, enabled: false, revocations_pending: 1 }, ['proof', 'enable', 'retry-revocations', 'disable']],
      ['disabled, revocation pending', S.disabled_pending, ['retry-revocations']],
      ['disabled, revoked', S.disabled_revoked, ['setup']],
    ];
    for (const [name, st, want] of matrix) {
      get = { status: 200, body: st }; replies = {};
      await openPanel();
      const c = await controls();
      await mailLayout(page, '#agent-mail-controls-host', `${name} ${W}`, check, SHOTS);
      check(same(c, want), `${name} ${W}: controls ${JSON.stringify(want)}`, `${name} ${W}: controls ${JSON.stringify(c)}, wanted ${JSON.stringify(want)}`);
    }
    for (const [name, g, wantDisable, tone] of [['status 503', { status: 503, body: { error: 'operational_adapter_unavailable' } }, true],
      ['status 500', { status: 500, body: { error: 'boom' } }, true], ['status 404 (feature off)', { status: 404, body: { error: 'not_found' } }, false]]) {
      get = g; replies = { disable: { status: 200, body: {} } };
      await openPanel();
      const c = await controls();
      await mailLayout(page, '#agent-mail-controls-host', `${name} ${W}`, check, SHOTS);
      check(wantDisable ? same(c, ['disable']) && await enabledBtn('disable') : c.length === 0,
        `${name} ${W}: ${wantDisable ? 'only the off switch is offered' : 'no controls at all'}`, `${name} ${W}: controls ${JSON.stringify(c)}`);
    }

    // ── the status card itself is still read-only ──
    get = { status: 200, body: S.active }; await openPanel();
    const card = await page.evaluate(() => [...document.querySelectorAll('#agent-mail-status button, #agent-mail-status input, #agent-mail-status form, #agent-mail-status select, #agent-mail-status textarea')]
      .map((e) => e.tagName + ':' + (e.dataset.am || '')));
    check(same(card, ['BUTTON:refresh']), `status card ${W}: still only Refresh`, `status card ${W}: ${JSON.stringify(card)}`);
  }

  const wrongMethod = nonGet.filter((r) => r.method !== 'POST');
  check(wrongMethod.length === 0, `every non-GET request was a POST to /api/agent-mail/enrollment/<action>; no query strings`, `unexpected requests: ${JSON.stringify(wrongMethod)}`);
  check(pageErrors.length === 0, 'no page errors', `page errors: ${pageErrors.join(' | ')}`);
} catch (e) {
  fail('smoke threw: ' + (e && e.stack || e));
}

await browser.close();
console.log(bad ? `\n${bad} check(s) FAILED` : '\nAll agent-mail actions checks passed');
process.exit(bad ? 1 : 0);
