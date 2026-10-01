#!/usr/bin/env node
/**
 * Desk v1 (MC-1021 R1-W S7) — the Review surface against a fake server,
 * `desk_v1_live` ON, plus the flag-OFF contract.
 *
 * Review is where a human approves something that can PUBLISH, so what is under
 * test is which requests the browser makes, and what it paints from the answers
 * (the routes are pinned by tests/test_desk_review_routes.py, test_desk_tick.py
 * and test_desk_publish*.py). NO real network: every /api/desk/* call is answered
 * here, every other request is aborted, and the passcode prompt is replaced by a
 * stub that forwards the call to the same fake server.
 *
 *   1. Read       -> the body, claims and account come from the stored version.
 *   2. Gates      -> Approve is disabled, with the server's reason, for an account
 *                    that cannot publish (LinkedIn pending), a time already past, and
 *                    an unknown outcome; a disabled button makes 0 requests.
 *   3. Claims     -> Add source / Accept as written / Remove each PATCH the right
 *                    thing, name nothing as "verified", and Undo PATCHes it back.
 *   4. Approve    -> human-only: goes through the passcode prompt, POSTs M19, and
 *                    paints what the SERVER says happened (sent, scheduled, held,
 *                    refused). A scheduled post on an account that forbids
 *                    unattended use says it will be held.
 *   5. Manual     -> a manual account gets the publishing task and "I posted it".
 *   6. Held / unknown_outcome versions are listed and say why.
 *   7. Skip       -> PATCH state, Undo PATCHes it back.
 *   8. Ask agent  -> M20 with the note / style; the revision the agent saves repaints.
 *   9. Edit text  -> the composed body is PATCHed.
 *  10. Flag OFF   -> Approve makes 0 /api/desk/* requests and never opens the prompt.
 *
 * RUN   cd tools/smoke && node desk-v1-live-review.mjs
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures, installDemoFixtures } from './desk-v1-fixture-api.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

const FUTURE = '2099-01-15T15:00:00Z';
const PAST = '2020-01-15T15:00:00Z';
const CLAIM = 'We cut restore time by 40%.';
const BODY = `## Why restore points\n\nEvery run now gets a restore point.\n\n${CLAIM}\n\nTry it on a branch first.`;

function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  const acct = (id, platform, capability, publish) => ({
    id, platform, identity: '@' + id, label: id.toUpperCase(), capability, voice: '', connected: publish.ready, publish,
  });
  const accounts = [
    acct('ch-x', 'x', 'direct', { ready: true, reason: null, secret: 'x.oauth-token', unattended_ok: true }),
    acct('ch-xlock', 'x', 'direct', { ready: true, reason: null, secret: 'x.oauth-token', unattended_ok: false }),
    acct('ch-li', 'linkedin', 'direct', { ready: false, reason: 'LinkedIn app review pending (w_organization_social)', secret: null, unattended_ok: null }),
    acct('ch-blog', 'blog', 'manual', { ready: true, reason: null, secret: null, unattended_ok: null }),
    acct('ch-xman', 'x', 'manual', { ready: true, reason: null, secret: null, unattended_ok: null }),
  ];
  const claim = (src) => [{ id: 'c1', text: CLAIM, original: CLAIM, source: src, verdict: src ? 'ok' : 'blocked' }];
  const ver = (id, channelId, state, extra = {}) => ({
    id, channelId, state, revision: 0, body: '', claimsState: {}, approved: null, failure: null, claims: claim(null), ...extra,
  });
  const pieces = [
    { id: 'p-1', campaignId: 'camp-1', kind: 'post', title: 'Restore points', body: BODY, assets: [], versions: [
      ver('v-x', 'ch-x', 'needs_review'),
      ver('v-li', 'ch-li', 'needs_review'),
      ver('v-blog', 'ch-blog', 'needs_review'),
      ver('v-xman', 'ch-xman', 'needs_review'),
    ] },
    { id: 'p-2', campaignId: 'camp-1', kind: 'post', title: 'Second piece', body: 'A plain note with no claims.', assets: [], versions: [
      ver('v-sched', 'ch-x', 'needs_review', { publishAt: FUTURE, claims: [] }),
      ver('v-lock', 'ch-xlock', 'needs_review', { publishAt: FUTURE, claims: [] }),
      ver('v-past', 'ch-x', 'needs_review', { publishAt: PAST, claims: [] }),
      ver('v-held', 'ch-x', 'held', { claims: [], failure: { reason: 'the campaign is paused', at: '2026-10-01T00:00:00Z' } }),
      ver('v-unk', 'ch-x', 'unknown_outcome', { claims: [], failure: { reason: 'the connection dropped after the request', at: '2026-10-01T00:00:00Z' } }),
      ver('v-skip', 'ch-x', 'needs_review', { claims: [] }),
    ] },
  ];
  const ws = workspaceFromFixtures(fx);
  const camp = JSON.parse(JSON.stringify(ws.campaigns.find((c) => c.id === 'camp-1')));
  const srv = { log: [], next: {}, fx, pieces, accounts, projects, camp, approves: [], revises: [], proofs: [], reviseApplies: null };
  srv.workspace = () => ({ ...ws, projects, accounts, campaigns: [JSON.parse(JSON.stringify(camp))], pieces: JSON.parse(JSON.stringify(pieces)) });
  return srv;
}

const find = (srv, pid, vid) => {
  const p = srv.pieces.find((x) => x.id === pid);
  return { p, v: p && p.versions.find((x) => x.id === vid) };
};

async function newPage(browser, { live, srv }) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1400 } });
  const page = await ctx.newPage();
  if (!live) await installDemoFixtures(page);   // demo mode is the harness's: the page ships no fixtures (S10)
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.addInitScript(() => { window.__deskV1ReviewPollMs = 60; window.prompt = (_m, d) => (window.__promptAnswer !== undefined ? window.__promptAnswer : d); });
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
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: live, user_timezone: '' });
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    srv.log.push({ method, path, search: url.search, body });
    const key = `${method} ${path}`;
    const refuse = Object.keys(srv.next).find((k) => key === k);
    if (refuse) { const msg = srv.next[refuse]; delete srv.next[refuse]; return J({ error: msg }, 409); }
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/pieces' && method === 'GET') {
      if (srv.reviseApplies) { srv.reviseApplies(); srv.reviseApplies = null; }
      return J(JSON.parse(JSON.stringify(srv.pieces)));
    }
    let m = path.match(/^\/api\/desk\/pieces\/([^/]+)$/);
    if (m && method === 'PATCH') {
      const p = srv.pieces.find((x) => x.id === m[1]);
      if (!p) return J({ error: 'piece not found' }, 404);
      if (body.claims) {
        for (const v of p.versions) for (const c of v.claims || []) {
          const nc = body.claims.find((x) => x.id === c.id);
          if (nc) { c.source = nc.source; c.verdict = nc.source ? 'ok' : (['accepted', 'edited', 'removed'].includes((v.claimsState[c.id] || {}).status) ? 'ok' : 'blocked'); }
        }
      }
      return J(p);
    }
    m = path.match(/^\/api\/desk\/pieces\/([^/]+)\/versions\/([^/]+)$/);
    if (m && method === 'PATCH') {
      const { p, v } = find(srv, m[1], m[2]);
      if (!v) return J({ error: 'version not found' }, 404);
      if ('body' in body) v.body = body.body;
      if ('revision' in body) v.revision = body.revision;
      if ('claims_state' in body) {
        v.claimsState = body.claims_state;
        for (const c of v.claims) { const e = v.claimsState[c.id]; c.text = (e && e.revised_text) || c.original; c.verdict = c.source || (e && ['accepted', 'edited', 'removed'].includes(e.status)) ? 'ok' : 'blocked'; }
      }
      if (body.state) v.state = body.state;
      return J(p);
    }
    m = path.match(/^\/api\/desk\/pieces\/([^/]+)\/versions\/([^/]+)\/approve$/);
    if (m && method === 'POST') {
      const { p, v } = find(srv, m[1], m[2]);
      const acc = srv.accounts.find((a) => a.id === v.channelId);
      srv.approves.push({ vid: v.id, body });
      const future = !('scheduled_at' in body) && v.publishAt && Date.parse(v.publishAt) > Date.now();
      if (future) { v.state = 'scheduled'; v.approved = { by: 'human' }; }
      else if (acc.capability === 'manual') {
        v.state = 'approved'; v.approved = { by: 'human' };
        v.manual = { title: `Post "${p.title}" on ${acc.label}`, platform: acc.platform, account_id: acc.id, copy_text: v.body || p.body, share_url: acc.platform === 'x' ? 'https://x.com/intent/post?text=hi' : null };
      } else { v.state = 'submitted'; v.approved = { by: 'human' }; v.receipt = { post_id: '123', permalink: 'https://x.com/i/status/123' }; }
      return J(p);
    }
    m = path.match(/^\/api\/desk\/pieces\/([^/]+)\/versions\/([^/]+)\/posted$/);
    if (m && method === 'POST') {
      const { p, v } = find(srv, m[1], m[2]);
      v.state = 'you_reported'; v.receipt = { permalink: (body && body.url) || null, reported: true };
      return J(p);
    }
    m = path.match(/^\/api\/desk\/pieces\/([^/]+)\/versions\/([^/]+)\/revise$/);
    if (m && method === 'POST') {
      srv.revises.push({ vid: m[2], body });
      return J({ ok: true, session_id: 'sess-1' }, 202);
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  // The passcode prompt is a modal a human types into. Here it forwards the call to the
  // fake server and records which action asked, so "human-only" is observable.
  await page.evaluate(() => {
    window.__proofs = [];
    window.humanProofFetch = async (url, init, proof) => {
      window.__proofs.push({ url, title: proof && proof.title, description: proof && proof.description });
      const r = await fetch(url, init);
      let b = null; try { b = await r.json(); } catch (_) { /* none */ }
      return { ok: r.ok, status: r.status, body: b };
    };
  });
  return { ctx, page, pageErrors };
}

const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });
const calls = (srv, method, re) => srv.log.filter((r) => r.method === method && re.test(r.path));
const realErrors = (pageErrors) => pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
const clearToasts = (page) => page.evaluate(() => document.querySelectorAll('.toast').forEach((t) => t.remove()));
const toastText = (page) => page.evaluate(() => [...document.querySelectorAll('.toast')].map((t) => t.innerText).join(' | '));
const primary = (page) => page.locator('[data-act-primary]');
async function until(pred, ms = 8000) {
  const t0 = Date.now();
  while (!pred()) { if (Date.now() - t0 > ms) throw new Error('timed out waiting for the request'); await new Promise((r) => setTimeout(r, 25)); }
}

async function openReview(page, versionId, { live = true } = {}) {
  if (live) await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await page.evaluate((v) => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: v }), versionId);
  await page.waitForSelector('.desk-v1-review, [data-review-result]', { timeout: 8000 });
  await settle(page, (v) => !!document.querySelector('.desk-v1-review') && true, versionId);
}

// ── 1 + 2: read, and the gates ──────────────────────────────────────────────
async function readAndGates(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await openReview(page, 'v-x');
  srv.log.length = 0;

  (await page.locator('.desk-v1-review-title').innerText()) === 'Restore points'
    ? ok('the title is the stored piece\'s') : fail('title: ' + await page.locator('.desk-v1-review-title').innerText());
  const article = await page.locator('#desk-v1-review-article').innerText();
  (article.includes('Every run now gets a restore point.') && article.includes(CLAIM) && article.includes('Why restore points'))
    ? ok('body paragraphs and the heading come from the stored version, not the fixtures') : fail('article: ' + article);
  (await page.locator('[data-claim-bar="c1"]').innerText()).includes('No source for this')
    ? ok('the unsourced claim shows its block') : fail('claim bar missing');
  (await primary(page).isDisabled())
    ? ok('Approve is disabled while the claim has no source') : fail('Approve enabled with a blocked claim');
  const nav = await page.locator('.desk-v1-review-kind').innerText();
  /of 10 to review/.test(nav) ? ok(`the list holds held / failed / unknown versions too (${nav.trim()})`) : fail('list size: ' + nav);

  await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-li' }));
  await settle(page, () => !!document.querySelector('[data-act-primary]') && document.querySelector('.desk-v1-review-reason'));
  const li = await page.locator('.desk-v1-review-reason').innerText();
  (li.includes('LinkedIn app review pending (w_organization_social)') && await primary(page).isDisabled())
    ? ok('LinkedIn: Approve is disabled with the server\'s own reason (w_organization_social pending)') : fail('linkedin reason: ' + li);

  await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-past' }));
  await settle(page, () => /scheduled time has passed/.test((document.querySelector('.desk-v1-review-reason') || {}).innerText || ''));
  (await primary(page).isDisabled()) ? ok('a scheduled time that has passed disables Approve and says what to do') : fail('past time not gated');

  await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-unk' }));
  await settle(page, () => !!document.querySelector('[data-review-unknown]'));
  const unk = await page.locator('[data-review-unknown]').innerText();
  (/may already be live/.test(unk) && await primary(page).isDisabled() && await page.locator('[data-act-posted]').count() === 1)
    ? ok('unknown outcome: says it may be live, Approve disabled, "mark as posted" offered') : fail('unknown: ' + unk);

  await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-held' }));
  await settle(page, () => !!document.querySelector('[data-review-held]'));
  const held = await page.locator('[data-review-held]').innerText();
  (held.includes('the campaign is paused') && /Approve again/.test(await primary(page).innerText()))
    ? ok('a held version shows its reason and offers "Approve again"') : fail('held: ' + held);

  srv.log.filter((r) => r.method !== 'GET').length === 0
    ? ok('reading and the disabled buttons made 0 write requests') : fail('writes: ' + JSON.stringify(srv.log.filter((r) => r.method !== 'GET')));
  const errs = realErrors(pageErrors);
  errs.length === 0 ? ok('no uncaught page errors') : errs.forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

// ── 3: claims ───────────────────────────────────────────────────────────────
async function claims(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await openReview(page, 'v-x');
  srv.log.length = 0;

  await page.click('[data-claim-addsource="c1"]');
  await page.fill('[data-source-input="c1"]', 'restore-bench-sep.md');
  await page.click('[data-source-run="c1"]');
  await settle(page, () => /Source on file/.test((document.querySelector('[data-claim-bar="c1"]') || {}).innerText || ''));
  const pp = calls(srv, 'PATCH', /^\/api\/desk\/pieces\/p-1$/);
  (pp.length === 1 && pp[0].body.claims[0].id === 'c1' && pp[0].body.claims[0].source === 'restore-bench-sep.md' && pp[0].body.claims[0].text === CLAIM)
    ? ok('Add source PATCHes the piece\'s claim with the typed source and its ORIGINAL text') : fail('source PATCH: ' + JSON.stringify(pp.map((r) => r.body)));
  const bar = await page.locator('[data-claim-bar="c1"]').innerText();
  (/Source on file/.test(bar) && !/supports/i.test(bar) && !/verified/i.test(bar))
    ? ok('it says "Source on file", never "supports" or "verified": nobody read the source') : fail('bar: ' + bar);
  (await primary(page).isEnabled()) ? ok('Approve is enabled once the claim has a source') : fail('Approve still disabled');

  await page.click('#desk-v1-undo'); // quiet Undo (Ron 2026-10-01): adding a source is routine, no toast
  await settle(page, () => /No source for this/.test((document.querySelector('[data-claim-bar="c1"]') || {}).innerText || ''));
  await page.waitForTimeout(100);
  const pp2 = calls(srv, 'PATCH', /^\/api\/desk\/pieces\/p-1$/);
  (pp2.length === 2 && pp2[1].body.claims[0].source === null)
    ? ok('Undo PATCHes the source back to null and the block returns') : fail('undo: ' + JSON.stringify(pp2.map((r) => r.body)));

  await clearToasts(page);
  await page.click('[data-claim-asis="c1"]');
  await settle(page, () => /accepted it as written/.test((document.querySelector('[data-claim-bar="c1"]') || {}).innerText || ''));
  const vp = calls(srv, 'PATCH', /\/versions\/v-x$/);
  (vp.length === 1 && vp[0].body.claims_state.c1.status === 'accepted' && vp[0].body.revision === 1 && !('body' in vp[0].body) && !('state' in vp[0].body))
    ? ok('Accept as written PATCHes claims_state accepted + revision 1, and no state') : fail('accept: ' + JSON.stringify(vp.map((r) => r.body)));
  await page.click('#desk-v1-undo'); // quiet Undo (Ron 2026-10-01): accepting a claim is routine, no toast
  await settle(page, () => /No source for this/.test((document.querySelector('[data-claim-bar="c1"]') || {}).innerText || ''));
  await page.waitForTimeout(100);
  const vp2 = calls(srv, 'PATCH', /\/versions\/v-x$/);
  (vp2.length === 2 && JSON.stringify(vp2[1].body.claims_state) === '{}' && vp2[1].body.revision === 0)
    ? ok('Undo PATCHes the previous claims_state and revision back') : fail('undo accept: ' + JSON.stringify(vp2[1] && vp2[1].body));

  await clearToasts(page);
  await page.click('[data-claim-remove="c1"]');
  await settle(page, () => !document.querySelector('[data-claim-bar="c1"]'));
  const rp = calls(srv, 'PATCH', /\/versions\/v-x$/).pop();
  (rp.body.claims_state.c1.status === 'removed' && !rp.body.body.includes(CLAIM) && rp.body.body.includes('Every run now gets a restore point.') && rp.body.body.includes('Try it on a branch first.'))
    ? ok('Remove drops that sentence from the saved body and marks the claim removed') : fail('remove: ' + JSON.stringify(rp.body));

  // A refused write rolls back and shows the server's reason.
  await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-blog' }));
  await settle(page, () => !!document.querySelector('[data-claim-asis="c1"]'));
  srv.next['PATCH /api/desk/pieces/p-1/versions/v-blog'] = 'this version is approved; it cannot be changed';
  await clearToasts(page);
  await page.click('[data-claim-asis="c1"]');
  await settle(page, () => /was not saved/.test([...document.querySelectorAll('.toast')].map((t) => t.innerText).join(' ')));
  const t = await toastText(page);
  (t.includes('cannot be changed') && /No source for this/.test(await page.locator('[data-claim-bar="c1"]').innerText()))
    ? ok('a refused claim write rolls back and the toast carries the server\'s reason') : fail('refused: ' + t);
  await ctx.close();
}

// ── 4: approve ──────────────────────────────────────────────────────────────
async function approve(browser) {
  const srv = makeServer();
  // Give v-x its source BEFORE the page loads, so it is approvable from the first read.
  for (const c of srv.pieces[0].versions[0].claims) { c.source = 'bench.md'; c.verdict = 'ok'; }
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await openReview(page, 'v-x');
  srv.log.length = 0;

  (await primary(page).innerText()).trim() === 'Approve' ? ok('no schedule: the button reads "Approve"') : fail('label: ' + await primary(page).innerText());
  await primary(page).click();
  await settle(page, () => !!document.querySelector('[data-review-result]'));
  const proofs = await page.evaluate(() => window.__proofs);
  (proofs.length === 1 && /\/versions\/v-x\/approve$/.test(proofs[0].url) && /post this version to CH-X now/.test(proofs[0].description))
    ? ok('Approve went through the passcode prompt, naming the account and that it posts now') : fail('proofs: ' + JSON.stringify(proofs));
  const ap = calls(srv, 'POST', /\/approve$/);
  (ap.length === 1 && JSON.stringify(ap[0].body) === '{}')
    ? ok('exactly one POST /approve, with no scheduled_at (the version\'s own time is used)') : fail('approve POST: ' + JSON.stringify(ap.map((r) => r.body)));
  const head = await page.locator('[data-review-result]').innerText();
  (/Sent to CH-X/.test(head) && head.includes('https://x.com/i/status/123'))
    ? ok('the result is what the server stored: "Sent to CH-X" with the permalink') : fail('result: ' + head);

  // A refusal is shown, not hidden, and leaves the version where it was.
  await page.click('[data-result-continue]');
  await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-skip' }));
  await settle(page, () => !!document.querySelector('[data-act-primary]'));
  srv.next['POST /api/desk/pieces/p-2/versions/v-skip/approve'] = 'cannot approve: the campaign is not active (it is paused): start it first';
  await clearToasts(page);
  await primary(page).click();
  await settle(page, () => /Not approved/.test([...document.querySelectorAll('.toast')].map((t) => t.innerText).join(' ')));
  const t = await toastText(page);
  (t.includes('the campaign is not active') && await page.locator('[data-review-result]').count() === 0)
    ? ok('a refused approval toasts the server\'s reasons and stays on the version') : fail('refusal: ' + t);

  // Publish now: scheduled_at null.
  await clearToasts(page);
  await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-sched' }));
  await settle(page, () => /Approve and schedule/.test((document.querySelector('[data-act-primary]') || {}).innerText || ''));
  await page.click('[data-more-btn]');
  await page.click('[data-publish-now]');
  await settle(page, () => !!document.querySelector('[data-review-result]'));
  const pn = calls(srv, 'POST', /v-sched\/approve$/);
  (pn.length === 1 && pn[0].body.scheduled_at === null)
    ? ok('Publish now POSTs {scheduled_at:null}: "now" is explicit, never inferred') : fail('publish now: ' + JSON.stringify(pn.map((r) => r.body)));
  const errs = realErrors(pageErrors);
  errs.length === 0 ? ok('no uncaught page errors') : errs.forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

// ── 4b: scheduled, and the unattended consequence ───────────────────────────
async function scheduled(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await openReview(page, 'v-sched');
  srv.log.length = 0;
  await primary(page).click();
  await settle(page, () => !!document.querySelector('[data-review-result]'));
  const proofs = await page.evaluate(() => window.__proofs);
  (/at .* It posts from the scheduler/.test(proofs[0].description) || /scheduler without asking again/.test(proofs[0].description))
    ? ok('the passcode prompt says a scheduled post goes out without asking again') : fail('proof: ' + JSON.stringify(proofs));
  const ap = calls(srv, 'POST', /v-sched\/approve$/);
  JSON.stringify(ap[0].body) === '{}' ? ok('a future time is NOT sent: the server uses the one When saved') : fail('body: ' + JSON.stringify(ap[0].body));
  const r = await page.locator('[data-review-result]').innerText();
  (/Approved\. It goes out/.test(r) && !/HELD/.test(r)) ? ok('scheduled on an account that allows unattended use: no warning') : fail('result: ' + r);

  await page.click('[data-result-continue]');
  await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-lock' }));
  await settle(page, () => !!document.querySelector('[data-act-primary]'));
  await primary(page).click();
  await settle(page, () => !!document.querySelector('[data-review-result]'));
  const w = await page.locator('[data-review-unattended]').innerText();
  (/x\.oauth-token/.test(w) && /HELD/.test(w))
    ? ok('an account whose vault entry forbids unattended use says the scheduled post will be HELD') : fail('unattended: ' + w);
  await ctx.close();
}

// ── 5: manual ───────────────────────────────────────────────────────────────
async function manual(browser) {
  const srv = makeServer();
  for (const v of ['v-blog', 'v-xman']) for (const c of find(srv, 'p-1', v).v.claims) { c.source = 'bench.md'; c.verdict = 'ok'; }
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await openReview(page, 'v-xman');
  (/Approve and create publishing task/.test(await primary(page).innerText()))
    ? ok('a manual account\'s button reads "Approve and create publishing task"') : fail('label: ' + await primary(page).innerText());
  await primary(page).click();
  await settle(page, () => !!document.querySelector('[data-review-task]'));
  (await page.locator('[data-task-text]').inputValue()).includes('Every run now gets a restore point.')
    ? ok('the task carries the text to paste') : fail('task text missing');
  (await page.locator('[data-task-share]').count() === 1 && (await page.locator('[data-task-share]').getAttribute('href')).startsWith('https://x.com/intent/post'))
    ? ok('X gets its share-intent link (opened by the person, in their own browser)') : fail('no share link');
  await page.fill('[data-task-url]', 'https://x.com/ron/status/9');
  await page.click('[data-task-posted]');
  await settle(page, () => /Recorded as posted by you/.test((document.querySelector('[data-review-result]') || {}).innerText || ''));
  const pr = calls(srv, 'POST', /\/posted$/);
  (pr.length === 1 && pr[0].body.url === 'https://x.com/ron/status/9')
    ? ok('"I posted it" POSTs the link and goes through the passcode prompt too') : fail('posted: ' + JSON.stringify(pr.map((r) => r.body)));
  ((await page.evaluate(() => window.__proofs)).length === 2) ? ok('2 passcode prompts in all: approve + posted') : fail('proof count');

  await page.click('[data-result-continue]');
  await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-blog' }));
  await settle(page, () => !!document.querySelector('[data-act-primary]'));
  await primary(page).click();
  await settle(page, () => !!document.querySelector('[data-review-task]'));
  (await page.locator('[data-task-share]').count() === 0 && /has no link that carries text/.test(await page.locator('[data-review-task]').innerText()))
    ? ok('a blog gets no invented share link, and the card says to paste it') : fail('blog task');
  await ctx.close();
}

// ── 7: skip + 8: ask the agent + 9: edit ────────────────────────────────────
async function skipAskEdit(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await openReview(page, 'v-skip');
  srv.log.length = 0;
  await page.click('[data-act-skip]');
  await settle(page, () => !document.querySelector('.desk-v1-review') || !/v-skip/.test('') );
  await page.waitForTimeout(100);
  const sp = calls(srv, 'PATCH', /v-skip$/);
  (sp.length === 1 && JSON.stringify(sp[0].body) === '{"state":"skipped"}') ? ok('Skip PATCHes {state:"skipped"}') : fail('skip: ' + JSON.stringify(sp.map((r) => r.body)));
  await page.locator('.toast-btn', { hasText: 'Undo' }).first().click();
  await page.waitForTimeout(150);
  const sp2 = calls(srv, 'PATCH', /v-skip$/);
  (sp2.length === 2 && sp2[1].body.state === 'needs_review' && find(srv, 'p-2', 'v-skip').v.state === 'needs_review')
    ? ok('Undo PATCHes it back to needs_review') : fail('skip undo: ' + JSON.stringify(sp2.map((r) => r.body)));

  // Ask the agent: the box is NOT the simulated task.
  await openReview(page, 'v-x');
  (await page.locator('.desk-v1-posy-footer').count() === 0) ? ok('live: the Ask box is not the "Simulated reply (R0)" box') : fail('simulated footer present');
  srv.log.length = 0;
  await page.fill('#desk-v1-review-posy-input', 'make the first line punchier');
  await page.click('[data-posy-send="desk-v1-review-posy-input"]');
  await until(() => calls(srv, 'POST', /\/revise$/).length === 1);
  const rv = srv.revises[0];
  (rv.vid === 'v-x' && rv.body.note === 'make the first line punchier') ? ok('the note POSTs M20 for this version') : fail('revise: ' + JSON.stringify(srv.revises));
  srv.reviseApplies = () => { const { v } = find(srv, 'p-1', 'v-x'); v.body = 'Every restore point is now automatic.\n\n' + CLAIM; v.revision = 1; };
  await settle(page, () => /Every restore point is now automatic/.test((document.querySelector('#desk-v1-review-article') || {}).innerText || ''));
  ok('when the agent saves its revision the surface repaints with it (no manual refresh)');

  // Edit text
  srv.log.length = 0;
  await page.click('[data-mode-btn="edit"]');
  await page.locator('[data-edit-para="p0"]').fill('Every restore point is now automatic, and instant.');
  await until(() => calls(srv, 'PATCH', /\/versions\/v-x$/).length === 1);
  const ep = calls(srv, 'PATCH', /\/versions\/v-x$/)[0];
  (JSON.stringify(Object.keys(ep.body)) === '["body"]' && ep.body.body.startsWith('Every restore point is now automatic, and instant.') && ep.body.body.includes(CLAIM))
    ? ok('Edit text PATCHes the composed body (claim paragraph untouched) and nothing else') : fail('edit: ' + JSON.stringify(ep.body));
  await settle(page, () => (document.getElementById('desk-v1-review-savestatus') || {}).textContent === 'Saved');
  ok('the status reads "Saved" only after the server accepted it');
  await ctx.close();
}

// ── 10: flag off ────────────────────────────────────────────────────────────
async function flagOff(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: false, srv });
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1' }));
  await page.waitForSelector('.desk-v1-review', { timeout: 8000 });
  srv.log.length = 0;
  const btn = primary(page);
  if (await btn.isEnabled()) await btn.click();
  await page.waitForTimeout(300);
  const desk = srv.log.filter((r) => r.path !== '/api/desk/workspace');
  desk.length === 0 ? ok('demo mode: Approve made 0 /api/desk/* requests') : fail('demo requests: ' + JSON.stringify(desk));
  ((await page.evaluate(() => window.__proofs)).length === 0) ? ok('demo mode never opens the passcode prompt') : fail('demo opened the prompt');
  await ctx.close();
}

const browser = await chromium.launch();
try {
  console.log('1-2. read + gates'); await readAndGates(browser);
  console.log('3. claims'); await claims(browser);
  console.log('4. approve'); await approve(browser);
  console.log('4b. scheduled + unattended'); await scheduled(browser);
  console.log('5. manual'); await manual(browser);
  console.log('7-9. skip, ask the agent, edit'); await skipAskEdit(browser);
  console.log('10. flag off'); await flagOff(browser);
} finally {
  await browser.close();
}
if (bad) { console.error(`\n✗ ${bad} check(s) failed`); process.exit(1); }
console.log('\nAll checks passed.');
