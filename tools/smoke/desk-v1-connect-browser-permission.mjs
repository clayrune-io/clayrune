#!/usr/bin/env node
/**
 * Desk v1 — the browser side of Read (MC-1062 ticket 07, docs/desk_v1/connect_flow_tickets/07-browser-permission.md).
 * Since 2026-10-28 it has NO screen of its own: the Permissions step shows ONE Read row for every account
 * (desk-v1-connect-permissions-step.mjs covers the row). This smoke covers the module behind that row: the grant of the
 * service's OWN domain on a saved browser sign-in's agent-read list. Hermetic: the real index.html + static/, every
 * /api route mocked (the real human-proof passcode modal is used).
 *
 *   1. Grant / revoke   the site is added to / removed from the profile's list with ONE PUT carrying the passcode;
 *                       every other site on the list is preserved. A switched-off profile's leftover domains are
 *                       not revived. Turning Read off removes only the exact site, never a covering parent or a site another connection on the sign-in reads.
 *   2. Shared           two connections on one profile see the same grant; `load` names the other pane connection.
 *   3. No write         cancelling the prompt, a wrong passcode, a refused PUT and a full list change nothing.
 *   4. Not saved yet    a profile that does not exist is never written to: `apply` answers `deferred`, and runs
 *                       once the profile is saved.
 *   5. Partial          outcome(): saved connection + failed/cancelled/deferred permission is "partial" or
 *                       "pending", never plain success.
 *   6. Retired          no per-site editor ships: `bind`/`html` are gone, the old stylesheet is not linked, and
 *                       only GET agent-read, GET profiles and PUT agent-read are ever called.
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

const apply = (page, profile, site, want, opts) => page.evaluate(([a, b, c, d]) => window.DeskV1ConnectBrowserPermission.apply(a, b, c, d), [profile, site, want, opts || {}]);
const load = (page, profile, site, opts) => page.evaluate(([a, b, c]) => window.DeskV1ConnectBrowserPermission.load(a, b, c), [profile, site, opts || {}]);
const factsOf = (page, st, wish) => page.evaluate(([s, w]) => window.DeskV1ConnectBrowserPermission.facts(s, w).join(' '), [st, wish]);
// apply() with the passcode prompt answered the way `opts` says; returns the apply result and the prompt's words.
async function applyAnswering(page, profile, site, want, passcode, opts, applyOpts) {
  const done = apply(page, profile, site, want, applyOpts);
  const said = await answerPrompt(page, passcode, opts);
  return { res: await done, said };
}
// apply() that must send nothing: if a passcode prompt opens anyway it is cancelled and the result says `prompted`, so a regression is a failed check, not a hang.
async function applyExpectingNone(page, profile, site, want, opts) {
  const done = apply(page, profile, site, want, opts);
  const win = await Promise.race([done.then(() => null), page.waitForSelector('[data-modal-id^="__human-proof-"]', { timeout: 3000 }).catch(() => null)]);
  if (win) { await answerPrompt(page, '', { cancel: true }); return { ...(await done), prompted: true }; }
  return done;
}
const stored = (p) => JSON.stringify(srv.policy[p] || null);
const noPrompt = async (page) => (await page.locator('[data-modal-id^="__human-proof-"]').count()) === 0;

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const { ctx, page, pageErrors } = await newPage(browser, { width: 1280, height: 900 });

  console.log('Dark by default');
  check(await page.evaluate(() => window.DeskV1ConnectWizard.enabled() === false), 'the wizard is still off');
  check(await page.evaluate(() => typeof window.DeskV1ConnectBrowserPermission === 'object' && typeof window.DeskV1ConnectAgentRead.put === 'function'), 'the module is loaded and reuses DeskV1ConnectAgentRead.put');

  console.log('6 — the per-site editor is retired');
  check(await page.evaluate(() => { const B = window.DeskV1ConnectBrowserPermission; return !('bind' in B) && !('html' in B) && typeof B.apply === 'function' && typeof B.load === 'function' && typeof B.facts === 'function'; }), 'bind/html are gone; load/apply/facts remain');
  check(await page.evaluate(() => ![...document.querySelectorAll('link[rel=stylesheet]')].some((l) => /connect-browser-permission/.test(l.href))), 'the old stylesheet is not linked');
  check(await page.locator('[data-bperm]').count() === 0, 'no per-site editor is on screen');

  console.log('1 — grant and revoke keep every other site');
  setPolicy({ main: { enabled: true, domains: ['reddit.com'] } });
  const channels = [{ id: 'ch-a', label: 'X (Ron)', browser_profile: 'main', read_via: 'pane' }, { id: 'ch-b', label: 'X (Studio)', browser_profile: 'main', read_via: 'pane' },
    { id: 'ch-c', label: 'X (API)', browser_profile: 'main', read_via: 'api' }, { id: 'ch-d', label: 'Elsewhere', browser_profile: 'work', read_via: 'pane' }];
  let st = await load(page, 'main', 'x.com', { channels, selfId: 'ch-a' });
  check(st.exists && !st.granted && st.others.join() === 'reddit.com', 'x.com starts off while reddit.com is on the list');
  const fa = await factsOf(page, st, false);
  check(/sign-in "main" on x\.com and its subdomains/.test(fa), 'the facts name the sign-in and the site it applies to');
  check(/Also read through this sign-in: X \(Studio\)\./.test(fa) && !/X \(API\)|Elsewhere/.test(fa), 'they name the other pane connection on the same profile, not an API one or another profile');
  check(/Other sites on it stay as they are: reddit\.com/.test(fa), 'they show the other site that is not touched');
  let r = await applyAnswering(page, 'main', 'x.com', true, 'smoke-passcode');
  check(/let agents read x\.com through "main"/.test(r.said || '') && /reddit\.com/.test(r.said || ''), 'its own passcode prompt names the site, the profile and what is kept');
  check(r.res.status === 'saved' && puts.length === 1 && puts[0].body.passcode === 'smoke-passcode' && puts[0].body.enabled === true
    && JSON.stringify(puts[0].body.domains) === JSON.stringify(['reddit.com', 'x.com']), `ONE PUT with the passcode: reddit.com kept, x.com added (${JSON.stringify(puts[0] && puts[0].body.domains)})`);

  console.log('2 — two connections on one profile see the same grant');
  st = await load(page, 'main', 'x.com', { channels, selfId: 'ch-b' });
  check(st.granted, 'the second connection sees x.com granted too');
  check(st.sharedWith.join() === 'X (Ron)', 'and names the first connection');
  r = await applyAnswering(page, 'main', 'x.com', false, 'smoke-passcode');
  check(/stop agents reading x\.com through "main"/.test(r.said || ''), 'revoking says what stops');
  check(r.res.status === 'saved' && puts.length === 2 && JSON.stringify(puts[1].body.domains) === JSON.stringify(['reddit.com']) && puts[1].body.enabled === true, 'revoke removes only x.com; reddit.com stays and stays enabled');
  check(!(await load(page, 'main', 'x.com', { channels, selfId: 'ch-a' })).granted, 'the first connection now shows it off as well');

  console.log('1b — a switched-off profile is not revived; a covering parent is disclosed');
  setPolicy({ main: { enabled: false, domains: ['reddit.com', 'x.com'] } });
  check(!(await load(page, 'main', 'x.com')).granted, 'leftover domains on an off profile are not a grant');
  r = await applyAnswering(page, 'main', 'x.com', true, 'smoke-passcode');
  check(r.res.status === 'saved' && puts.length === 1 && JSON.stringify(puts[0].body.domains) === JSON.stringify(['x.com']) && puts[0].body.enabled === true, 'granting writes only x.com: the stale reddit.com is not revived');

  // Unticking Read removes ONLY the exact service domain (Dave 2026-10-28): never a covering parent, never a domain another
  // saved connection on the same sign-in reads.
  setPolicy({ main: { enabled: true, domains: ['linkedin.com', 'reddit.com'] } });
  st = await load(page, 'main', 'www.linkedin.com');
  const fd = await factsOf(page, st, false);
  check(st.granted && /Allowed through linkedin\.com, which covers www\.linkedin\.com\. Turning Read off does not remove linkedin\.com\./.test(fd), 'a covering parent shows as granted and says turning Read off does not remove it');
  check(/does not read your LinkedIn feed or notifications by itself/.test(fd), 'LinkedIn facts deny automatic feed coverage');
  r = await applyExpectingNone(page, 'main', 'www.linkedin.com', false);
  check(r.status === 'unchanged' && !r.prompted && puts.length === 0 && r.stays && r.stays.kind === 'parent' && r.stays.by === 'linkedin.com',
    'unticking www.linkedin.com under a linkedin.com grant sends nothing (no prompt, no PUT) and names the parent that keeps it');
  check(/www\.linkedin\.com stays readable because this sign-in allows all of linkedin\.com\./.test(await page.evaluate((x) => window.DeskV1ConnectBrowserPermission.outcome(true, x).text, r)), 'the result says plainly why it stays readable');

  setPolicy({ main: { enabled: true, domains: ['linkedin.com', 'www.linkedin.com', 'reddit.com'] } });
  r = await applyAnswering(page, 'main', 'www.linkedin.com', false, 'smoke-passcode');
  check(r.res.status === 'saved' && puts.length === 1 && JSON.stringify(puts[0].body.domains) === JSON.stringify(['linkedin.com', 'reddit.com']) && r.res.stays && r.res.stays.by === 'linkedin.com',
    `the exact domain leaves while the parent stays (${JSON.stringify(puts[0] && puts[0].body.domains)})`);

  setPolicy({ main: { enabled: true, domains: ['x.com', 'reddit.com'] } });
  const sharers = [{ id: 'ch-a', label: 'X (Ron)', browser_profile: 'main', read_via: 'pane' }, { id: 'ch-b', label: 'X (Studio)', browser_profile: 'main', read_via: 'pane' }];
  r = await applyExpectingNone(page, 'main', 'x.com', false, { channels: sharers, selfId: 'ch-a' });
  check(r.status === 'unchanged' && !r.prompted && puts.length === 0 && r.stays && r.stays.kind === 'shared' && r.stays.by === 'X (Studio)',
    'unticking a site another saved connection on the same sign-in reads sends nothing and names that connection');
  check(/x\.com stays readable for X \(Studio\), which uses the same sign-in\./.test(await page.evaluate((x) => window.DeskV1ConnectBrowserPermission.outcome(true, x).text, r)), 'the result says it stays readable for that connection');
  r = await applyAnswering(page, 'main', 'x.com', false, 'smoke-passcode', undefined, { channels: [sharers[0]], selfId: 'ch-a' });
  check(r.res.status === 'saved' && JSON.stringify(puts[0].body.domains) === JSON.stringify(['reddit.com']), 'with nobody else on the sign-in the same untick removes the exact domain');

  console.log('3 — cancel, wrong passcode, refusal and a full list change nothing');
  setPolicy({ main: { enabled: true, domains: ['reddit.com'] } });
  r = await applyAnswering(page, 'main', 'x.com', true, '', { cancel: true });
  check(r.res.status === 'cancelled' && puts.length === 0 && stored('main') === JSON.stringify({ enabled: true, domains: ['reddit.com'] }), 'cancelling the prompt sent nothing and changed nothing');

  const wrong = apply(page, 'main', 'x.com', true);
  await answerPrompt(page, 'wrong');
  await page.waitForFunction(() => /Wrong dashboard passcode/i.test((document.querySelector('[data-modal-id^="__human-proof-"]') || {}).textContent || ''), null, { timeout: 4000 }).catch(() => {});
  check(/Wrong dashboard passcode/i.test(await page.locator('[data-modal-id^="__human-proof-"]').innerText()), 'a wrong passcode is refused in the prompt');
  check(stored('main') === JSON.stringify({ enabled: true, domains: ['reddit.com'] }), 'the stored policy is unchanged after the wrong passcode');
  await answerPrompt(page, '', { cancel: true });
  check((await wrong).status === 'cancelled' && stored('main') === JSON.stringify({ enabled: true, domains: ['reddit.com'] }), 'backing out after it leaves the policy unchanged');

  srv.refusePut = true;
  r = await applyAnswering(page, 'main', 'x.com', true, 'smoke-passcode');
  check(r.res.status === 'failed' && /this action needs a human/.test(r.res.error || '') && stored('main') === JSON.stringify({ enabled: true, domains: ['reddit.com'] }), 'a refused PUT says why and leaves the policy as it was');
  srv.refusePut = false;

  const twenty = Array.from({ length: 20 }, (_, i) => `site${i}.com`);
  setPolicy({ main: { enabled: true, domains: twenty } });
  r = await apply(page, 'main', 'x.com', true);
  check(r.status === 'failed' && /most it can/.test(r.error || '') && puts.length === 0 && await noPrompt(page), 'a full list is refused up front: no prompt, no PUT');

  console.log('4 — a profile that is not saved yet');
  setPolicy({});
  srv.profiles = ['main'];
  calls.length = 0;
  st = await load(page, 'linkedin-new', 'linkedin.com');
  check(!st.exists && /"linkedin-new" is not saved yet\. Nothing changes until you sign in/.test(await factsOf(page, st, true)), 'facts say the sign-in is not saved and nothing changes yet');
  const deferred = await apply(page, 'linkedin-new', 'linkedin.com', true);
  check(deferred.status === 'deferred' && puts.length === 0 && await noPrompt(page), 'apply() on an unsaved profile is deferred: no PUT, no passcode prompt');
  check(calls.every((c) => c.method === 'GET'), 'only GETs were made while deferred');
  srv.profiles = ['main', 'linkedin-new'];
  r = await applyAnswering(page, 'linkedin-new', 'linkedin.com', true, 'smoke-passcode');
  check(r.res.status === 'saved' && puts.length === 1 && puts[0].name === 'linkedin-new' && JSON.stringify(puts[0].body.domains) === JSON.stringify(['linkedin.com']), 'once the profile is saved, apply() writes the held grant');

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

  console.log('6b — scope');
  const paths = [...new Set(calls.map((c) => `${c.method} ${c.path.replace(/profiles\/[^/]+\//, 'profiles/<p>/')}`))];
  check(paths.every((p) => ['GET /api/browser/agent-read', 'GET /api/browser/profiles', 'PUT /api/browser/profiles/<p>/agent-read'].includes(p)), `only the existing read/PUT routes were used (${paths.join(', ')})`);
  const copy = await factsOf(page, await load(page, 'main', 'x.com'), false);
  check(/answer about a page, never the page\. They cannot click, type or post\./.test(copy), 'the facts say answers not pages and no click, type or post');
  check(!/feed/i.test(copy), 'no feed claim on a non-LinkedIn site');

  pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e)).forEach((e) => fail('uncaught: ' + e));
  await ctx.close();
  exitCode = bad === 0 ? 0 : 1;
  console.log(exitCode === 0 ? '\n✅ PASS — browser side of Read.' : `\n❌ FAIL — ${bad} check(s).`);
} catch (err) {
  console.error('❌ FAIL — smoke harness error:', err && err.stack ? err.stack : err);
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
