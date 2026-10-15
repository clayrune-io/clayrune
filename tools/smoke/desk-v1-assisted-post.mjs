#!/usr/bin/env node
/**
 * Desk v1 — assisted posting (static/js/desk-v1-assisted-post.js) against a fake server,
 * `desk_v1_live` ON. The routes are pinned by tests/test_desk_assisted_post.py and
 * tests/test_desk_assisted_accounts.py; what is under test here is what the person sees
 * and which requests the browser makes. NO real network: every /api/desk/* call is
 * answered here, every other request is aborted, and the passcode prompt is a stub that
 * forwards the call to the same fake server and records which action asked.
 *
 *   1. Piece page  -> an approved version for an assisted account offers
 *                     "Fill in on Instagram"; a manual account, a read-only one and a
 *                     version still in review do not.
 *   2. Fill in     -> goes through the passcode prompt, POSTs the assisted-post route,
 *                     shows the working state while the request is pending, then the
 *                     server's own message; a double click makes one request.
 *   3. Stopped     -> a step_failed answer shows the server's message, and the button
 *                     comes back.
 *   4. Review      -> approving an assisted version says nothing publishes, creates the
 *                     task, and offers the same button on the result screen; "Publish
 *                     now" is not offered.
 *   5. Connections -> an assisted account says "Assisted posting" and no publish gate;
 *                     a read-only Instagram account offers "Allow assisted posting",
 *                     which PATCHes capability through the passcode prompt.
 *
 * RUN   cd tools/smoke && node desk-v1-assisted-post.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const STATIC = loadStaticJsCss(REPO_ROOT);

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, bad_) => (cond ? ok(good) : fail(bad_));

const BODY = 'A short note about restore points.';

function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  const acct = (id, platform, capability, publish) => ({
    id, platform, identity: '@' + id, label: id.toUpperCase(), capability, voice: '', connected: publish.ready, publish,
  });
  const accounts = [
    acct('ch-ig', 'instagram', 'assisted', { ready: true, reason: null, secret: null, unattended_ok: null, assisted: true }),
    acct('ch-ig-ro', 'instagram', 'none', { ready: false, reason: 'read-only: the Desk reads this account and does not publish there', secret: null, unattended_ok: null }),
    acct('ch-blog', 'blog', 'manual', { ready: true, reason: null, secret: null, unattended_ok: null }),
  ];
  const ver = (id, channelId, state, extra = {}) => ({
    id, channelId, state, revision: 0, body: '', claimsState: {}, approved: null, failure: null, claims: [], ...extra,
  });
  const task = (acc) => ({ title: `Post "Restore points" on ${acc}`, platform: 'instagram', account_id: 'ch-ig', copy_text: BODY, share_url: null });
  const pieces = [
    { id: 'p-1', campaignId: 'camp-1', kind: 'post', title: 'Restore points', body: BODY, assets: [], versions: [
      ver('v-ig', 'ch-ig', 'approved', { approved: { by: 'human' }, manual: task('CH-IG') }),
      ver('v-ig-review', 'ch-ig', 'needs_review'),
      ver('v-blog', 'ch-blog', 'approved', { approved: { by: 'human' }, manual: { ...task('CH-BLOG'), platform: 'blog', account_id: 'ch-blog' } }),
    ] },
  ];
  const ws = workspaceFromFixtures(fx);
  const camp = JSON.parse(JSON.stringify(ws.campaigns.find((c) => c.id === 'camp-1')));
  const srv = { log: [], fx, pieces, accounts, projects, camp, fill: { status: 200, body: { ok: true, message: 'Filled in. Check the post in the pane, then press Share yourself.', final_button_present: true }, delayMs: 0 } };
  srv.workspace = () => ({ ...ws, projects, accounts, campaigns: [JSON.parse(JSON.stringify(camp))], pieces: JSON.parse(JSON.stringify(pieces)) });
  return srv;
}

async function newPage(browser, srv) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1400 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.addInitScript(() => { window.__deskV1ReviewPollMs = 60; });
  await page.route('**/*', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();
    const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return J(srv.fx.projects.map((p) => ({
      id: p.id, name: p.name, status: 'active', domain: 'general', emoji: '🧪', description: '', summary: '', current_task: 'Idle',
      next_action: '', blocked: false, blocked_reason: null, activity_log: [], backlog: [], project_path: '/smoke/' + p.id,
      last_updated: '2026-09-09T00:00:00Z', last_updated_relative: 'today', last_completed: null, live_agent: null, display_order: 0,
      provider: 'claude', use_streaming_agent: true, distiller_mode: 'proposed', distiller_min_recurrence: 3,
      distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3, distiller_max_explorations_per_session: 3,
      distiller_min_turns: 5, distiller_skip_errors: true, roster: [],
    })));
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true, user_timezone: '' });
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/pieces' && method === 'GET') return J(JSON.parse(JSON.stringify(srv.pieces)));
    let m = path.match(/^\/api\/desk\/pieces\/([^/]+)\/versions\/([^/]+)\/assisted-post$/);
    if (m && method === 'POST') {
      if (srv.fill.delayMs) await new Promise((r) => setTimeout(r, srv.fill.delayMs));
      return J(srv.fill.body, srv.fill.status);
    }
    m = path.match(/^\/api\/desk\/pieces\/([^/]+)\/versions\/([^/]+)\/approve$/);
    if (m && method === 'POST') {
      const p = srv.pieces.find((x) => x.id === m[1]);
      const v = p.versions.find((x) => x.id === m[2]);
      v.state = 'approved'; v.approved = { by: 'human' };
      v.manual = { title: 'Post "Restore points" on CH-IG', platform: 'instagram', account_id: 'ch-ig', copy_text: BODY, share_url: null };
      return J(p);
    }
    m = path.match(/^\/api\/desk\/accounts\/([^/]+)$/);
    if (m && method === 'PATCH') {
      const a = srv.accounts.find((x) => x.id === m[1]);
      if (body && body.capability) {
        a.capability = body.capability;
        a.publish = { ready: true, reason: null, secret: null, unattended_ok: null, assisted: true };
        a.connected = true;
      }
      return J(a);
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.evaluate(() => {
    window.__proofs = [];
    window.humanProofFetch = async (url, init, proof) => {
      window.__proofs.push({ url, title: proof && proof.title });
      const r = await fetch(url, init);
      let b = null; try { b = await r.json(); } catch (_) { /* none */ }
      return { ok: r.ok, status: r.status, body: b };
    };
  });
  return { ctx, page, pageErrors };
}

const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });
const fills = (srv) => srv.log.filter((r) => r.method === 'POST' && /\/assisted-post$/.test(r.path));
const realErrors = (e) => e.filter((x) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(x));

async function openPiece(page) {
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await page.evaluate(() => window.deskV1Nav('piece', { campaignId: 'camp-1', familyId: 'p-1' }));
  await page.waitForSelector('[data-piece-section]', { timeout: 8000 });
}

// ── 1 + 2 + 3: the piece page ───────────────────────────────────────────────
async function piecePage(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, srv);
  await openPiece(page);

  const rows = await page.$$eval('[data-copy-row]', (els) => els.map((e) => ({ id: e.dataset.copyRow, fill: !!e.querySelector('[data-assisted-fill]'), text: (e.querySelector('[data-assisted-fill]') || {}).textContent || '' })));
  const by = Object.fromEntries(rows.map((r) => [r.id, r]));
  check(by['v-ig'] && by['v-ig'].fill && by['v-ig'].text === 'Fill in on Instagram', 'an approved version for an assisted account offers "Fill in on Instagram"', 'rows: ' + JSON.stringify(rows));
  check(by['v-ig-review'] && !by['v-ig-review'].fill, 'a version still in review does not', 'review row: ' + JSON.stringify(by['v-ig-review']));
  check(by['v-blog'] && !by['v-blog'].fill, 'a manual account does not', 'blog row: ' + JSON.stringify(by['v-blog']));

  srv.fill.delayMs = 400;
  srv.log.length = 0;
  await page.click('[data-assisted-post="v-ig"] [data-assisted-fill]');
  await page.click('[data-assisted-post="v-ig"] [data-assisted-fill]', { force: true, timeout: 1000 }).catch(() => {});
  await settle(page, () => (document.querySelector('[data-assisted-post="v-ig"] [data-assisted-status]') || {}).dataset.state === 'working');
  const working = await page.locator('[data-assisted-post="v-ig"] [data-assisted-status]').innerText();
  const disabledWhile = await page.locator('[data-assisted-post="v-ig"] [data-assisted-fill]').isDisabled();
  check(/Filling in the post/.test(working) && disabledWhile, `the working state shows and the button is disabled (${working.trim()})`, `working: ${working} disabled=${disabledWhile}`);
  await settle(page, () => (document.querySelector('[data-assisted-post="v-ig"] [data-assisted-status]') || {}).dataset.state === 'ready');
  const done = await page.locator('[data-assisted-post="v-ig"] [data-assisted-status]').innerText();
  check(done.includes('press Share yourself'), `the server's own message is shown (${done.trim()})`, 'ready text: ' + done);
  const proofs = await page.evaluate(() => window.__proofs);
  check(proofs.length === 1 && /assisted-post$/.test(proofs[0].url) && proofs[0].title === 'Fill in the post', 'it went through the passcode prompt, once', 'proofs: ' + JSON.stringify(proofs));
  check(fills(srv).length === 1, 'two clicks made 1 request', 'requests: ' + fills(srv).length);
  check(await page.locator('[data-assisted-post="v-ig"] [data-assisted-fill]').isEnabled(), 'the button comes back', 'button still disabled');

  srv.fill.delayMs = 0;
  srv.fill.status = 502;   // the real route's answer for a stopped step: {error, code, step, ...}
  srv.fill.body = { error: 'stopped at step 3 (click): the page did not show what was expected. The pane is left where it is: finish this post by hand.', code: 'step_failed', step: 3 };
  await page.click('[data-assisted-post="v-ig"] [data-assisted-fill]');
  await settle(page, () => (document.querySelector('[data-assisted-post="v-ig"] [data-assisted-status]') || {}).dataset.state === 'failed');
  const failed = await page.locator('[data-assisted-post="v-ig"] [data-assisted-status]').innerText();
  check(failed.includes('stopped at step 3') && failed.includes('finish this post by hand'), `a stopped step shows the server's message (${failed.trim()})`, 'failed text: ' + failed);
  check(await page.locator('[data-assisted-post="v-ig"] [data-assisted-fill]').isEnabled(), 'and the button comes back to try again', 'button disabled after failure');

  const errs = realErrors(pageErrors);
  check(errs.length === 0, 'no uncaught page errors', 'page errors: ' + errs.join(' | '));
  await ctx.close();
}

// ── 4: review ───────────────────────────────────────────────────────────────
async function review(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, srv);
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-ig-review' }));
  await page.waitForSelector('.desk-v1-review', { timeout: 8000 });
  const notice = await page.locator('[data-review-assisted]').innerText();
  check(/You press Share/.test(notice), 'the review screen says a person presses Share', 'notice: ' + notice);
  const label = await page.locator('[data-act-primary]').innerText();
  check(/publishing task/i.test(label), `the primary action creates a task (${label.trim()})`, 'primary: ' + label);
  await page.click('[data-act-primary]');
  await page.waitForSelector('[data-review-task] [data-assisted-fill]', { timeout: 8000 });
  ok('after Approve, the result screen offers the fill-in button next to the task');
  const proofs = await page.evaluate(() => window.__proofs);
  check(proofs.length === 1 && /approve$/.test(proofs[0].url) && /publishing task/i.test(proofs[0].title), 'Approve asked for the passcode as a task, not "publish"', 'proofs: ' + JSON.stringify(proofs));
  const errs = realErrors(pageErrors);
  check(errs.length === 0, 'no uncaught page errors', 'page errors: ' + errs.join(' | '));
  await ctx.close();
}

// ── 5: connections ──────────────────────────────────────────────────────────
async function connections(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, srv);
  await settle(page, () => window.DeskV1Store.state().channels.length > 0);
  await page.evaluate(() => window.deskV1Nav('home', {}));
  await page.waitForSelector('.desk-v1-home-connections-btn', { timeout: 8000 });
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-connections] [data-conn-tile]', { timeout: 8000 });

  const tileFor = async (id) => {
    await page.evaluate((i) => { const t = [...document.querySelectorAll('[data-conn-tile]')].find((e) => (e.dataset.connTile || e.dataset.id || '') === i || e.textContent.includes(i.toUpperCase())); if (t) t.click(); }, id);
  };
  await tileFor('ch-ig');
  await page.waitForSelector('[data-assisted-account]', { timeout: 8000 });
  const line = await page.locator('[data-assisted-account]').innerText();
  check(/press the final button yourself/.test(line), `an assisted account says who presses the final button (${line.trim()})`, 'line: ' + line);
  check(await page.locator('[data-assisted-enable]').count() === 0, 'and offers no switch', 'enable button on an assisted account');

  await tileFor('ch-ig-ro');
  await page.waitForSelector('[data-assisted-enable]', { timeout: 8000 });
  srv.log.length = 0;
  await page.click('[data-assisted-enable]');
  await settle(page, () => !!document.querySelector('[data-assisted-account]') && !document.querySelector('[data-assisted-enable]'));
  const patch = srv.log.filter((r) => r.method === 'PATCH');
  check(patch.length === 1 && patch[0].path === '/api/desk/accounts/ch-ig-ro' && patch[0].body.capability === 'assisted' && Object.keys(patch[0].body).length === 1, 'the switch PATCHes only capability: assisted', 'patch: ' + JSON.stringify(patch));
  const proofs = await page.evaluate(() => window.__proofs);
  check(proofs.length === 1 && proofs[0].title === 'Allow assisted posting', 'through the passcode prompt', 'proofs: ' + JSON.stringify(proofs));
  const errs = realErrors(pageErrors);
  check(errs.length === 0, 'no uncaught page errors', 'page errors: ' + errs.join(' | '));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  console.log('piece page'); await piecePage(browser);
  console.log('review'); await review(browser);
  console.log('connections'); await connections(browser);
} catch (e) {
  fail('threw: ' + (e && e.stack || e));
} finally {
  await browser.close();
}
console.log(bad ? `\nFAIL — ${bad} check(s) failed` : '\nPASS — assisted posting');
process.exit(bad ? 1 : 0);
