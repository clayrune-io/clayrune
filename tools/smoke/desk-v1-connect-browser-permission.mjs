#!/usr/bin/env node
/**
 * Desk v1 — the browser Read permission editor (MC-1062 ticket 07,
 * docs/desk_v1/connect_flow_tickets/07-browser-permission.md). Hermetic: the real index.html + static/,
 * every /api route mocked (the real human-proof passcode modal is used).
 *
 *   1. Grant / revoke   the target site is added to / removed from the profile's list with ONE PUT carrying
 *                       the passcode; every other site on the list is preserved. A switched-off profile's
 *                       leftover domains are not revived. A parent domain that covers the site is shown and
 *                       its removal is said in the prompt.
 *   2. Shared           two connections on one profile (two mounts) show the same grant and each names the
 *                       other; other sites on the list are shown as untouched.
 *   3. No write         cancelling the prompt, a wrong passcode, a refused PUT and a full list send no
 *                       change: the stored policy is identical afterwards and the box shows it.
 *   4. Not saved yet    a profile that does not exist is never written to and never launched: the choice
 *                       is held (onWish), says so, and `apply` runs it only once the profile is saved.
 *   5. Partial          outcome(): saved connection + failed/cancelled/deferred permission is "partial" or
 *                       "pending", never plain success.
 *   6. Scope            only GET agent-read, GET profiles and PUT agent-read are ever called (no launch,
 *                       read, input); the copy says answers not pages, no click/type/post, and makes no
 *                       claim of automatic LinkedIn feed coverage. The wizard is still dark by default.
 *   7. Fit              no horizontal scroll at 390 wide.
 *
 * RUN   cd tools/smoke && node desk-v1-connect-browser-permission.mjs
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { seedDeskV1Fixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const ASSETS_DIR = resolve(REPO_ROOT, 'assets');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const MIME = { '.webp': 'image/webp', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml' };
const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT, { isolateConnectScreens: true }));
for (const f of readdirSync(ASSETS_DIR)) {
  const ext = f.slice(f.lastIndexOf('.'));
  if (MIME[ext]) STATIC[`/assets/${f}`] = [MIME[ext], readFileSync(resolve(ASSETS_DIR, f))];
}

const PROJECT = {
  id: 'smoke_bperm', name: 'Browser permission smoke', status: 'active', domain: 'general', emoji: '🧪',
  description: '', summary: '', current_task: 'Idle', next_action: '', blocked: false, blocked_reason: null,
  activity_log: [], backlog: [], project_path: '/smoke/bperm', last_updated: '2026-10-06T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null, display_order: 0,
  provider: 'claude', use_streaming_agent: true, roster: [],
  distiller_mode: 'proposed', distiller_min_recurrence: 3, distiller_max_topics_per_session: 3,
  distiller_max_preferences_per_session: 3, distiller_max_explorations_per_session: 3,
  distiller_min_turns: 5, distiller_skip_errors: true,
};

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, m) => (cond ? ok(m) : fail(m));

const calls = [];            // every /api/browser* request: {method, path}
const puts = [];             // {name, body}
const srv = { policy: {}, profiles: ['main'], refusePut: false };
const setPolicy = (p) => { srv.policy = JSON.parse(JSON.stringify(p)); puts.length = 0; };

async function fulfill(route) {
  const req = route.request();
  const path = new URL(req.url()).pathname;
  const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  const hit = STATIC[path];
  if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
  if (path === '/api/projects') return J([PROJECT]);
  if (path === '/api/config') return J({ desk_v1: true, user_timezone: '' });
  if (path === '/api/characters') return J([]);
  if (path === '/api/local-auth/status') return J({ configured: true });
  if (path.startsWith('/api/browser')) calls.push({ method: req.method(), path });
  if (path === '/api/browser/agent-read') return J({ profiles: srv.policy });
  if (path === '/api/browser/profiles') return J({ profiles: srv.profiles.map((name) => ({ name, size_mb: 1, last_used: null, in_use_by: null })) });
  const m = path.match(/^\/api\/browser\/profiles\/([^/]+)\/agent-read$/);
  if (m && req.method() === 'PUT') {
    const body = JSON.parse(req.postData() || '{}');
    puts.push({ name: m[1], body });
    if (srv.refusePut) return J({ error: 'this action needs a human' }, 403);
    if (body.passcode !== 'smoke-passcode') return J({ error: 'bad_passcode' }, 403);
    srv.policy[m[1]] = { enabled: !!body.enabled, domains: body.domains };
    return J({ ok: true, profile: m[1], enabled: !!body.enabled, domains: body.domains });
  }
  return route.abort();
}

async function promptText(page) {
  const win = await page.waitForSelector('[data-modal-id^="__human-proof-"]', { timeout: 5000 }).catch(() => null);
  return win ? (await win.innerText()) : null;
}
async function answerPrompt(page, passcode, { cancel = false } = {}) {
  const text = await promptText(page);
  if (text === null) return null;
  await page.evaluate(({ passcode, cancel }) => {
    const modalId = document.querySelector('[data-modal-id^="__human-proof-"]').dataset.modalId;
    if (cancel) { window._hpCancel(modalId); return; }
    document.getElementById(`hp-passcode-${modalId}`).value = passcode;
    window._hpSubmit(modalId);
  }, { passcode, cancel });
  return text;
}

async function newPage(browser, viewport) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
  await page.route('**/*', fulfill);
  await seedDeskV1Fixtures(page);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  return { ctx, page, pageErrors };
}

// A mount in the page, bound the way a Permissions screen would.
async function mountEditor(page, id, opts) {
  await page.evaluate(async ({ id, opts }) => {
    let host = document.getElementById('bperm-host');
    if (!host) { host = document.createElement('div'); host.id = 'bperm-host'; host.style.cssText = 'position:fixed;top:80px;left:300px;width:560px;max-width:calc(100vw - 310px);z-index:50;background:var(--surface);padding:8px'; document.body.appendChild(host); }
    let m = document.getElementById(id);
    if (!m) { m = document.createElement('div'); m.id = id; host.appendChild(m); }
    window.__wish = window.__wish || {};
    window.__result = window.__result || {};
    await window.DeskV1ConnectBrowserPermission.bind(m, { ...opts,
      onWish: (v) => { window.__wish[id] = v; },
      onResult: (r) => { window.__result[id] = r; } });
  }, { id, opts });
  await page.waitForSelector(`#${id} [data-bperm]`, { timeout: 4000 });
}
const box = (page, id) => page.locator(`#${id} [data-bperm-read]`);
const say = (page, id) => page.locator(`#${id} [data-bperm-status]`).innerText();
const facts = (page, id) => page.locator(`#${id} [data-bperm-facts]`).innerText();
const settle = (page) => page.waitForTimeout(250);
const stored = (p) => JSON.stringify(srv.policy[p] || null);

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const { ctx, page, pageErrors } = await newPage(browser, { width: 1280, height: 900 });

  console.log('Dark by default');
  check(await page.evaluate(() => window.DeskV1ConnectWizard.enabled() === false), 'the wizard is still off');
  check(await page.locator('[data-bperm]').count() === 0, 'no permission editor is on screen until a screen mounts one');
  check(await page.evaluate(() => typeof window.DeskV1ConnectBrowserPermission === 'object' && typeof window.DeskV1ConnectAgentRead.put === 'function'), 'the editor is loaded and reuses DeskV1ConnectAgentRead.put');

  console.log('1 — grant and revoke keep every other site');
  setPolicy({ main: { enabled: true, domains: ['reddit.com'] } });
  const channels = [{ id: 'ch-a', label: 'X (Ron)', browser_profile: 'main', read_via: 'pane' }, { id: 'ch-b', label: 'X (Studio)', browser_profile: 'main', read_via: 'pane' },
    { id: 'ch-c', label: 'X (API)', browser_profile: 'main', read_via: 'api' }, { id: 'ch-d', label: 'Elsewhere', browser_profile: 'work', read_via: 'pane' }];
  await mountEditor(page, 'ed-a', { profile: 'main', site: 'x.com', channels, selfId: 'ch-a' });
  check(!(await box(page, 'ed-a').isChecked()), 'x.com starts off while reddit.com is on the list');
  const fa = await facts(page, 'ed-a');
  check(/sign-in "main" on x\.com and its subdomains/.test(fa), 'it names the sign-in and the site it applies to');
  check(/Also read through this sign-in: X \(Studio\)\./.test(fa) && !/X \(API\)|Elsewhere/.test(fa), 'it names the other pane connection on the same profile, not an API one or another profile');
  check(/Other sites on it stay as they are: reddit\.com/.test(fa), 'it shows the other site that is not touched');
  await box(page, 'ed-a').check();
  const grantPrompt = await answerPrompt(page, 'smoke-passcode');
  check(/let agents read x\.com through "main"/.test(grantPrompt || '') && /reddit\.com/.test(grantPrompt || ''), 'its own passcode prompt names the site, the profile and what is kept');
  await page.waitForSelector('#ed-a [data-bperm-status]:not(:empty)', { timeout: 4000 });
  check(puts.length === 1 && puts[0].body.passcode === 'smoke-passcode' && puts[0].body.enabled === true
    && JSON.stringify(puts[0].body.domains) === JSON.stringify(['reddit.com', 'x.com']), `ONE PUT with the passcode: reddit.com kept, x.com added (${JSON.stringify(puts[0] && puts[0].body.domains)})`);
  check(await box(page, 'ed-a').isChecked() && /Saved/.test(await say(page, 'ed-a')), 'the box shows the saved grant');

  console.log('2 — two connections on one profile show the same grant');
  await mountEditor(page, 'ed-b', { profile: 'main', site: 'x.com', channels, selfId: 'ch-b' });
  check(await box(page, 'ed-b').isChecked(), 'the second connection shows x.com granted too');
  check(/Also read through this sign-in: X \(Ron\)\./.test(await facts(page, 'ed-b')), 'and names the first connection');

  await box(page, 'ed-b').uncheck();
  const revokePrompt = await answerPrompt(page, 'smoke-passcode');
  check(/stop agents reading x\.com through "main"/.test(revokePrompt || ''), 'revoking says what stops');
  await page.waitForFunction(() => /Saved/.test(document.querySelector('#ed-b [data-bperm-status]').textContent), null, { timeout: 4000 });
  check(puts.length === 2 && JSON.stringify(puts[1].body.domains) === JSON.stringify(['reddit.com']) && puts[1].body.enabled === true, 'revoke removes only x.com; reddit.com stays and stays enabled');
  await mountEditor(page, 'ed-a', { profile: 'main', site: 'x.com', channels, selfId: 'ch-a' });
  check(!(await box(page, 'ed-a').isChecked()), 'the first connection now shows it off as well');

  console.log('1b — a switched-off profile is not revived; a covering parent is disclosed');
  setPolicy({ main: { enabled: false, domains: ['reddit.com', 'x.com'] } });
  await mountEditor(page, 'ed-c', { profile: 'main', site: 'x.com', channels: [], selfId: 'z' });
  check(!(await box(page, 'ed-c').isChecked()), 'leftover domains on an off profile are not a grant');
  await box(page, 'ed-c').check();
  await answerPrompt(page, 'smoke-passcode');
  await page.waitForFunction(() => /Saved/.test(document.querySelector('#ed-c [data-bperm-status]').textContent), null, { timeout: 4000 });
  check(puts.length === 1 && JSON.stringify(puts[0].body.domains) === JSON.stringify(['x.com']) && puts[0].body.enabled === true, 'granting writes only x.com: the stale reddit.com is not revived');

  setPolicy({ main: { enabled: true, domains: ['linkedin.com', 'reddit.com'] } });
  await mountEditor(page, 'ed-d', { profile: 'main', site: 'www.linkedin.com', channels: [], selfId: 'z' });
  check(await box(page, 'ed-d').isChecked() && /Allowed through linkedin\.com, which covers www\.linkedin\.com\. Turning Read off removes linkedin\.com\./.test(await facts(page, 'ed-d')), 'a covering parent shows as granted and says revoking removes the parent');
  check(/does not read your LinkedIn feed or notifications by itself/.test(await facts(page, 'ed-d')), 'LinkedIn copy denies automatic feed coverage');
  await box(page, 'ed-d').uncheck();
  const widerPrompt = await answerPrompt(page, 'smoke-passcode');
  check(/stop agents reading linkedin\.com through "main"/.test(widerPrompt || ''), 'the prompt names the parent that will be removed');
  await page.waitForFunction(() => /Saved/.test(document.querySelector('#ed-d [data-bperm-status]').textContent), null, { timeout: 4000 });
  check(JSON.stringify(puts[0].body.domains) === JSON.stringify(['reddit.com']), 'only the covering parent left the list');

  console.log('3 — cancel, wrong passcode, refusal and a full list change nothing');
  setPolicy({ main: { enabled: true, domains: ['reddit.com'] } });
  await mountEditor(page, 'ed-e', { profile: 'main', site: 'x.com', channels: [], selfId: 'z' });
  await box(page, 'ed-e').check();
  await answerPrompt(page, '', { cancel: true });
  await page.waitForFunction(() => /passcode was not entered/.test(document.querySelector('#ed-e [data-bperm-status]').textContent), null, { timeout: 4000 });
  check(puts.length === 0 && stored('main') === JSON.stringify({ enabled: true, domains: ['reddit.com'] }), 'cancelling the prompt sent nothing and changed nothing');
  check(!(await box(page, 'ed-e').isChecked()), 'the box went back to off');

  await box(page, 'ed-e').check();
  await answerPrompt(page, 'wrong');
  await page.waitForFunction(() => /Wrong dashboard passcode/i.test((document.querySelector('[data-modal-id^="__human-proof-"]') || {}).textContent || ''), null, { timeout: 4000 }).catch(() => {});
  check(/Wrong dashboard passcode/i.test(await page.locator('[data-modal-id^="__human-proof-"]').innerText()), 'a wrong passcode is refused in the prompt');
  check(stored('main') === JSON.stringify({ enabled: true, domains: ['reddit.com'] }), 'the stored policy is unchanged after the wrong passcode');
  await answerPrompt(page, '', { cancel: true });
  await page.waitForFunction(() => /passcode was not entered/.test(document.querySelector('#ed-e [data-bperm-status]').textContent), null, { timeout: 4000 });
  check(!(await box(page, 'ed-e').isChecked()) && stored('main') === JSON.stringify({ enabled: true, domains: ['reddit.com'] }), 'backing out after it leaves the box off and the policy unchanged');

  srv.refusePut = true;
  await box(page, 'ed-e').check();
  await answerPrompt(page, 'smoke-passcode');
  await page.waitForFunction(() => /Could not save it/.test(document.querySelector('#ed-e [data-bperm-status]').textContent), null, { timeout: 4000 });
  check(/this action needs a human/.test(await say(page, 'ed-e')) && !(await box(page, 'ed-e').isChecked()), 'a refused PUT says why in place and leaves the box off');
  srv.refusePut = false;

  const twenty = Array.from({ length: 20 }, (_, i) => `site${i}.com`);
  setPolicy({ main: { enabled: true, domains: twenty } });
  await mountEditor(page, 'ed-f', { profile: 'main', site: 'x.com', channels: [], selfId: 'z' });
  await box(page, 'ed-f').check();
  await page.waitForFunction(() => /most it can/.test(document.querySelector('#ed-f [data-bperm-status]').textContent), null, { timeout: 4000 });
  check(puts.length === 0 && (await promptText(page)) === null, 'a full list is refused up front: no prompt, no PUT');

  console.log('4 — a profile that is not saved yet');
  setPolicy({});
  srv.profiles = ['main'];
  calls.length = 0;
  await mountEditor(page, 'ed-g', { profile: 'linkedin-new', site: 'linkedin.com', channels: [], selfId: 'z' });
  check(/"linkedin-new" is not saved yet\. Nothing changes until you sign in/.test(await facts(page, 'ed-g')), 'it says the sign-in is not saved and nothing changes yet');
  await box(page, 'ed-g').check();
  await settle(page);
  check(puts.length === 0 && (await promptText(page)) === null, 'choosing Read sent no PUT and asked for no passcode');
  check(await page.evaluate(() => window.__wish['ed-g'] === true), 'the choice is held (onWish true)');
  check(/Kept\. Reading is allowed once you have signed in/.test(await say(page, 'ed-g')), 'it says it is kept for sign-in');
  check(calls.every((c) => c.method === 'GET'), 'only GETs were made while deferred');
  const deferred = await page.evaluate(() => window.DeskV1ConnectBrowserPermission.apply('linkedin-new', 'linkedin.com', true));
  check(deferred.status === 'deferred' && puts.length === 0, 'apply() on an unsaved profile is deferred, nothing written');
  srv.profiles = ['main', 'linkedin-new'];
  const applied = page.evaluate(() => window.DeskV1ConnectBrowserPermission.apply('linkedin-new', 'linkedin.com', true));
  await answerPrompt(page, 'smoke-passcode');
  const done = await applied;
  check(done.status === 'saved' && puts.length === 1 && puts[0].name === 'linkedin-new' && JSON.stringify(puts[0].body.domains) === JSON.stringify(['linkedin.com']), 'once the profile is saved, apply() writes the held grant');

  console.log('5 — partial success is never plain success');
  const out = await page.evaluate(() => {
    const o = window.DeskV1ConnectBrowserPermission.outcome;
    return { failed: o(true, { status: 'failed', error: 'this action needs a human' }), cancelled: o(true, { status: 'cancelled' }), deferred: o(true, { status: 'deferred' }),
      good: o(true, { status: 'saved' }), none: o(true, null), reverse: o(false, { status: 'saved' }) };
  });
  check(out.failed.kind === 'partial' && /connection is saved\. Browser reading was not changed: this action needs a human/.test(out.failed.text), 'saved connection + failed permission is partial and says why');
  check(out.cancelled.kind === 'partial' && out.deferred.kind === 'pending', 'cancelled is partial; deferred is pending');
  check(out.good.kind === 'ok' && out.none.kind === 'ok', 'a saved permission or none is ok');
  check(out.reverse.kind === 'partial', 'a saved permission with an unsaved connection is partial');

  console.log('6 — scope');
  const paths = [...new Set(calls.map((c) => `${c.method} ${c.path.replace(/profiles\/[^/]+\//, 'profiles/<p>/')}`))];
  check(paths.every((p) => ['GET /api/browser/agent-read', 'GET /api/browser/profiles', 'PUT /api/browser/profiles/<p>/agent-read'].includes(p)), `only the existing read/PUT routes were used (${paths.join(', ')})`);
  const copy = await facts(page, 'ed-a');
  check(/answer about a page, never the page\. They cannot click, type or post\./.test(copy), 'the copy says answers not pages and no click, type or post');
  check(!/feed/i.test(await facts(page, 'ed-e')), 'no feed claim on a non-LinkedIn site');

  console.log('7 — fit at 390');
  await page.setViewportSize({ width: 390, height: 800 });
  await page.evaluate(() => { const h = document.getElementById('bperm-host'); h.style.left = '0'; h.style.maxWidth = '100vw'; h.style.width = 'auto'; h.style.right = '0'; });
  await mountEditor(page, 'ed-h', { profile: 'main', site: 'x.com', channels, selfId: 'ch-a' });
  const fit = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, r: document.getElementById('bperm-host').getBoundingClientRect().right }));
  check(fit.sw <= fit.cw && fit.r <= fit.cw, `no horizontal scroll at 390 (scrollWidth ${fit.sw}, client ${fit.cw}, right edge ${Math.round(fit.r)})`);

  pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e)).forEach((e) => fail('uncaught: ' + e));
  await ctx.close();
  exitCode = bad === 0 ? 0 : 1;
  console.log(exitCode === 0 ? '\n✅ PASS — browser Read permission editor.' : `\n❌ FAIL — ${bad} check(s).`);
} catch (err) {
  console.error('❌ FAIL — smoke harness error:', err && err.stack ? err.stack : err);
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
