#!/usr/bin/env node
/**
 * Settings > Connectivity > Agent mail: HELD MESSAGES (Release / Reject),
 * static/js/agent-mail-holds.js, under the status card and the controls.
 *
 * Route-mocked: a synthetic /api/agent-mail/holds served to the real static/ files.
 * Run at 1000px and 390px.
 *
 * Pins:
 *  - a hold is listed with its reason in plain words and how many replies wait behind it;
 *    an unknown reason code is shown as the code, hostile ids/reasons never reach the DOM;
 *  - Release and Reject each ask for the passcode through the shared prompt and send exactly
 *    one POST to /holds/<id>/<action> with the typed passcode in the body, then re-read the
 *    list and the status; the hold is gone afterwards;
 *  - a closed prompt sends nothing; a refusal (403 human_required, 409 not_held) says so and
 *    leaves the hold listed; a failing list draws nothing and no empty card is shown;
 *  - no clipping, buttons reachable, mobile targets >=40px; the status card stays read-only.
 *
 * RUN: node tools/smoke/agent-mail-holds.mjs
 */
import { readFileSync, existsSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { chromium } from 'playwright';
import { mailLayout } from './_agent-mail-layout.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const STATIC_ROOT = resolve(REPO_ROOT, 'static');
const SHOTS = resolve(REPO_ROOT, '_scratch', 'holds');
mkdirSync(SHOTS, { recursive: true });
const ORIGIN = 'http://mc.smoke.test';
const PASS = 'correct horse 42';

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg));

const NOW = 1794249600000;
const status = { stage: 'not_enrolled', enabled: false, revocations_pending: 0, now_ms: NOW,
  prerequisites: { tunnel: true, claude_extraction: true, machine_custody: true },
  mail: { blocked: 0, uncertain: 0, retryable: 0, outbound_uncertain: 0, hold_reasons: {} }, recovery: { state: 'not_running' } };
const hold = (id, reason, behind, extra = {}) => ({ event_id: id, project_id: 'proj1', session_id: 'sess-1', reason,
  received_at: NOW / 1000 - 600, state_at: NOW / 1000 - 300, expires_at: NOW / 1000 + 86400, body_len: 120, queued_behind: behind, ...extra });
const freshHolds = () => [hold('ev1', 'conversation_identity_changed', 1), hold('ev2', 'some_new_reason', 0)];

let list = { status: 200, holds: freshHolds() };
let reply = null;            // { status, body, remove? } for the next review POST
const posts = [];
let listCount = 0, statusCount = 0;
const otherMethods = [];
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
  const json = (body, code = 200) => route.fulfill({ status: code, contentType: 'application/json', body: JSON.stringify(body) });
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
  if (path === '/api/agent-mail/holds') {
    if (req.method() !== 'GET') otherMethods.push(req.method() + ' ' + path);
    listCount++;
    return json(list.status === 200 ? { holds: list.holds } : { error: 'storage_unavailable' }, list.status);
  }
  if (path.startsWith('/api/agent-mail/holds/')) {
    const [, , , , id, action] = path.split('/');
    if (req.method() !== 'POST') otherMethods.push(req.method() + ' ' + path);
    posts.push({ id, action, body: JSON.parse(req.postData() || '{}') });
    const r = reply || { status: 200, body: { ok: true }, remove: true };
    if (r.remove) list = { status: 200, holds: list.holds.filter((h) => h.event_id !== id) };
    return json(r.body, r.status);
  }
  if (path === '/api/agent-mail/enrollment') { statusCount++; return json(status); }
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
  await page.waitForFunction(() => !!document.getElementById('agent-mail-holds-host'), { timeout: 5000 });
  await page.waitForTimeout(250);   // the list GET answers after the status paint
};
const prompt = async (pw = PASS) => {
  const input = page.locator('input[id^="hp-passcode-"]');
  await input.waitFor({ state: 'visible', timeout: 5000 });
  await input.fill(pw);
  await page.locator('[data-modal-id^="__human-proof-"] .btn-add').click();
};
const ids = () => page.evaluate(() => [...document.querySelectorAll('#agent-mail-holds [data-am-hold]')].map((e) => e.dataset.amHold));
const outcomeText = async () => {
  await page.waitForSelector('#agent-mail-holds [data-am="hold-outcome"]', { timeout: 8000 });
  return page.evaluate(() => { const o = document.querySelector('#agent-mail-holds [data-am="hold-outcome"]'); return { tone: o.dataset.tone, text: o.textContent }; });
};
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

try {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openSettings === 'function' && !!window.AgentMailHolds && !!window.AgentMailStatusModel, { timeout: 20000 });

  for (const width of [1000, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 800 : 900 });
    await page.waitForTimeout(300);
    const W = `@${width}`;
    console.log(`\n== ${W} ==`);

    // ── the list ──
    list = { status: 200, holds: [...freshHolds(), hold('<img src=x onerror=window.__pwned=1>', 'conversation_identity_changed', 0),
      hold('ev3', '<script>window.__pwned=1</script>', 0)] };
    posts.length = 0;
    await openPanel();
    check(same(await ids(), ['ev1', 'ev2', 'ev3']), `list ${W}: the two well-formed holds and the one with a bad reason are listed; the hostile id is dropped`, `list ${W}: ${JSON.stringify(await ids())}`);
    const txt = await page.evaluate(() => ({
      r1: document.querySelector('[data-am-hold="ev1"] [data-am="hold-reason"]').textContent,
      b1: document.querySelector('[data-am-hold="ev1"] [data-am="hold-behind"]').textContent,
      r2: document.querySelector('[data-am-hold="ev2"] [data-am="hold-reason"]').textContent,
      b2: document.querySelector('[data-am-hold="ev2"] [data-am="hold-behind"]').textContent,
      r3: document.querySelector('[data-am-hold="ev3"] [data-am="hold-reason"]').textContent,
      pwned: !!window.__pwned, injected: document.querySelectorAll('#agent-mail-holds img, #agent-mail-holds script').length }));
    check(/different conversation/.test(txt.r1) && /1 later reply is waiting/.test(txt.b1), `list ${W}: reason in plain words, 1 reply waiting behind`, `list ${W}: ${JSON.stringify(txt)}`);
    check(txt.r2 === 'some new reason' && /Nothing else/.test(txt.b2), `list ${W}: an unknown code is shown as words, nothing behind`, `list ${W}: ${JSON.stringify(txt)}`);
    check(txt.r3 === 'unknown reason' && !txt.pwned && txt.injected === 0, `list ${W}: a hostile reason is inert`, `list ${W}: ${JSON.stringify(txt)}`);
    await mailLayout(page, '#agent-mail-holds', `holds-list ${W}`, check, SHOTS);

    // ── release: prompt, one POST with the passcode, hold gone, list + status re-read ──
    list = { status: 200, holds: freshHolds() }; reply = null; posts.length = 0;
    await openPanel();
    let l0 = listCount, s0 = statusCount;
    await page.click('#agent-mail-holds [data-am-hold="ev1"] [data-am-hold-action="release"]');
    await prompt();
    let o = await outcomeText();
    check(posts.length === 1 && posts[0].id === 'ev1' && posts[0].action === 'release' && posts[0].body.passcode === PASS && Object.keys(posts[0].body).length === 1,
      `release ${W}: one POST to /holds/ev1/release with only the passcode`, `release ${W}: ${JSON.stringify(posts)}`);
    check(o.tone === 'good' && /Released/.test(o.text) && same(await ids(), ['ev2']), `release ${W}: good outcome, ev1 gone`, `release ${W}: ${JSON.stringify(o)} ${JSON.stringify(await ids())}`);
    check(listCount > l0 && statusCount > s0, `release ${W}: list and status re-read`, `release ${W}: list ${l0}->${listCount} status ${s0}->${statusCount}`);

    // ── reject ──
    posts.length = 0;
    await page.click('#agent-mail-holds [data-am-hold="ev2"] [data-am-hold-action="reject"]');
    await prompt();
    o = await outcomeText();
    check(posts.length === 1 && posts[0].id === 'ev2' && posts[0].action === 'reject' && posts[0].body.passcode === PASS,
      `reject ${W}: one POST to /holds/ev2/reject with the passcode`, `reject ${W}: ${JSON.stringify(posts)}`);
    check(o.tone === 'good' && /Rejected/.test(o.text) && same(await ids(), []), `reject ${W}: good outcome, list empty`, `reject ${W}: ${JSON.stringify(o)}`);

    // ── a closed prompt sends nothing ──
    list = { status: 200, holds: freshHolds() }; posts.length = 0;
    await openPanel();
    await page.click('#agent-mail-holds [data-am-hold="ev1"] [data-am-hold-action="reject"]');
    await page.locator('[data-modal-id^="__human-proof-"] .btn-secondary').click();
    await page.waitForTimeout(300);
    check(posts.length === 0 && same(await ids(), ['ev1', 'ev2']), `cancel ${W}: closing the prompt sent nothing and kept both holds`, `cancel ${W}: ${JSON.stringify(posts)}`);

    // ── refusals keep the hold and say why ──
    for (const [name, r, want] of [
      ['human_required', { status: 403, body: { error: 'human_required' } }, /A human must do this/],
      ['not_held', { status: 409, body: { error: 'not_held', state: 'ready' } }, /no longer held/],
      ['nothing_to_release', { status: 409, body: { error: 'nothing_to_release' } }, /Reject it instead/],
      ['storage_unavailable', { status: 503, body: { error: 'storage_unavailable' } }, /unavailable/],
    ]) {
      reply = r; posts.length = 0;
      await page.click('#agent-mail-holds [data-am-hold="ev1"] [data-am-hold-action="release"]');
      await prompt();
      o = await outcomeText();
      check(o.tone === 'bad' && want.test(o.text) && same(await ids(), ['ev1', 'ev2']), `refusal ${name} ${W}: said so, hold kept`, `refusal ${name} ${W}: ${JSON.stringify(o)} ${JSON.stringify(await ids())}`);
      const btns = await page.evaluate(() => [...document.querySelectorAll('#agent-mail-holds [data-am-hold-action]')].every((b) => !b.disabled));
      check(btns, `refusal ${name} ${W}: buttons usable again`, `refusal ${name} ${W}: buttons stuck`);
    }
    reply = null;

    // ── nothing held / list failing: no card at all ──
    for (const [name, l] of [['empty', { status: 200, holds: [] }], ['503', { status: 503, holds: [] }]]) {
      list = l;
      await openPanel();
      const shown = await page.evaluate(() => !!document.getElementById('agent-mail-holds'));
      check(!shown, `${name} ${W}: no held-messages card`, `${name} ${W}: a card is drawn`);
    }

    // ── the status card itself is still read-only ──
    const card = await page.evaluate(() => [...document.querySelectorAll('#agent-mail-status button, #agent-mail-status input')].map((e) => e.tagName + ':' + (e.dataset.am || '')));
    check(same(card, ['BUTTON:refresh']), `status card ${W}: still only Refresh`, `status card ${W}: ${JSON.stringify(card)}`);
  }

  check(otherMethods.length === 0, 'the only non-GET requests were POSTs to /holds/<id>/<action>', `unexpected requests: ${JSON.stringify(otherMethods)}`);
  check(pageErrors.length === 0, 'no page errors', `page errors: ${pageErrors.join(' | ')}`);
} catch (e) {
  fail('smoke threw: ' + (e && e.stack || e));
}

await browser.close();
console.log(bad ? `\n${bad} check(s) FAILED` : '\nAll agent-mail holds checks passed');
process.exit(bad ? 1 : 0);
