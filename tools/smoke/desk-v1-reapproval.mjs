#!/usr/bin/env node
/**
 * Desk v1 (MC-1116) — re-approving a widened campaign is findable.
 *
 * A running campaign whose limits were widened after the human approved them
 * cannot publish until they approve again on Launch. Against a fake server,
 * `desk_v1_live` ON, with camp-1 (active, approved, budget $0):
 *
 *   1. Warn at the edit -> raising the Budget to $1 on How (after the existing
 *                          confirm) shows the inline note + a link to Launch;
 *                          lowering a limit shows none; the summary bar gets the
 *                          "Awaiting approval" badge.
 *   2. Launch names it  -> Awaiting approval lists "Budget: $0 → $1", and the ONLY
 *                          approve control is still `data-approve-bounds`.
 *   3. Review refusal   -> the server's 409 carries `code`; Review shows
 *                          "Go to Launch to approve", which lands on Launch. A
 *                          refusal WITHOUT the code shows no button. Nothing in
 *                          Review approves the campaign (0 POSTs to /campaigns/*).
 *   4. Badge            -> campaign summary and piece page, linking to Launch;
 *                          absent when the campaign is not awaiting.
 *
 * RUN   cd tools/smoke && node desk-v1-reapproval.mjs
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

const BODY = 'Every run now gets a restore point.';

function makeServer() {
  const fx = loadFixtures();
  const ws = workspaceFromFixtures(fx);
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  const accounts = [{ id: 'ch-x', platform: 'x', identity: '@ch-x', label: 'CH-X', capability: 'direct', voice: '', connected: true, publish: { ready: true, reason: null, secret: 'x.oauth-token', unattended_ok: true } }];
  const camp = JSON.parse(JSON.stringify(ws.campaigns.find((c) => c.id === 'camp-1')));
  const pieces = [{ id: 'p-1', campaignId: 'camp-1', kind: 'post', title: 'Restore points', body: BODY, assets: [], versions: [
    { id: 'v-1', channelId: 'ch-x', state: 'needs_review', revision: 0, body: '', claimsState: {}, approved: null, failure: null, claims: [] },
    { id: 'v-2', channelId: 'ch-x', state: 'needs_review', revision: 0, body: '', claimsState: {}, approved: null, failure: null, claims: [] },
  ] }];
  // `awaiting` is the server's verdict; `refuseCode` lets a case answer a refusal with no code.
  const srv = { log: [], fx, camp, pieces, accounts, projects, awaiting: false, refuseCode: true, proofs: [] };
  srv.workspace = () => ({ ...ws, projects, accounts, campaigns: [JSON.parse(JSON.stringify(camp))], pieces: JSON.parse(JSON.stringify(pieces)) });
  return srv;
}

async function newPage(browser, srv) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1400 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.addInitScript(() => { window.__deskV1ReviewPollMs = 600000; });
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
    if (path === '/api/desk/campaigns/camp-1' && method === 'PATCH') {
      // The server snapshot of the approval is NOT touched by an ordinary PATCH.
      const { approval, approved, ...rest } = body || {};
      Object.assign(srv.camp, rest);
      srv.awaiting = true;
      return J(srv.camp);
    }
    const m = path.match(/^\/api\/desk\/pieces\/([^/]+)\/versions\/([^/]+)\/approve$/);
    if (m && method === 'POST') {
      if (srv.awaiting) {
        const refusal = { error: 'cannot approve: the campaign\'s limits changed since you approved them: approve them on Launch first', problems: ['the campaign\'s limits changed since you approved them: approve them on Launch first'] };
        if (srv.refuseCode) { refusal.code = 'campaign_awaiting_approval'; refusal.campaign_id = 'camp-1'; }
        return J(refusal, 409);
      }
      return J(srv.pieces[0]);
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.evaluate(() => {
    window.humanProofFetch = async (url, init) => {
      const r = await fetch(url, init);
      let b = null; try { b = await r.json(); } catch (_) { /* none */ }
      return { ok: r.ok, status: r.status, body: b };
    };
  });
  await page.waitForFunction(() => window.DeskV1Store.state().campaigns.length > 0, null, { timeout: 8000 });
  return { ctx, page, pageErrors };
}

const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });
const realErrors = (e) => e.filter((x) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(x));
const writes = (srv, re) => srv.log.filter((r) => r.method !== 'GET' && re.test(r.path));

async function warnAtTheEdit(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, srv);
  await page.evaluate(() => window.deskV1GotoCampaignPanel('how', { campaignId: 'camp-1' }));
  await settle(page, () => !!document.querySelector('[data-how-budget-btn]'));
  (await page.locator('[data-reapproval-note]').count()) === 0 ? ok('before any edit there is no note') : fail('note shown before any edit');
  (await page.locator('[data-reapproval-badge]').count()) === 0 ? ok('and no Awaiting approval badge') : fail('badge shown before any edit');

  // Narrowing never warns: lower the weekly ceiling.
  await page.fill('[data-how-limit="per_week"]', '2');
  await page.press('[data-how-limit="per_week"]', 'Tab');
  await settle(page, () => window.DeskV1Store.state().campaigns.find((c) => c.id === 'camp-1').plan.cadence.per_week === 2);
  (await page.locator('[data-reapproval-note]').count()) === 0 ? ok('lowering a limit shows no note') : fail('note shown for a narrowing edit');

  // Budget $0 -> $1 widens; the existing confirm comes first, then the note stays on the page.
  await page.click('[data-how-budget-btn="own"]');
  await settle(page, () => !!document.querySelector('[data-how-budget-amount]'));
  await page.fill('[data-how-budget-amount]', '1');
  await page.press('[data-how-budget-amount]', 'Tab');
  await settle(page, () => /widens what/.test(document.body.innerText));
  await page.click('[data-confirm-accept]');
  await settle(page, () => !!document.querySelector('[data-reapproval-slot="budget"] [data-reapproval-note]'));
  const note = await page.locator('[data-reapproval-slot="budget"] [data-reapproval-note]').innerText();
  (/This widens the campaign; it won't post until you approve it on Launch\./.test(note) && /Go to Launch to approve/.test(note))
    ? ok(`the Budget field carries the note and the link: "${note.replace(/\s+/g, ' ').trim()}"`) : fail('budget note: ' + note);
  (await page.locator('[data-reapproval-slot="per_week"] [data-reapproval-note]').count()) === 0
    ? ok('the lowered ceiling keeps no note (the note is per field)') : fail('per_week note shown for a narrowing');
  (/—/.test(note)) ? fail('em-dash in the note copy') : ok('no em-dash in the note copy');
  await settle(page, () => !!document.querySelector('#desk-v1-camp-summary [data-reapproval-badge]'));
  ok('the summary bar gained the Awaiting approval badge without a navigation');

  await page.click('[data-reapproval-slot="budget"] [data-reapproval-launch]');
  await settle(page, () => !!document.querySelector('[data-approve-bounds]'));
  ok('the note\'s link lands on Launch, on the Awaiting approval state');
  const changes = await page.locator('[data-reapproval-change]').allInnerTexts();
  (changes.length === 1 && changes[0].replace(/\s+/g, ' ') === 'Budget: $0 → $1')
    ? ok('Launch names the change: "Budget: $0 → $1" (and not the lowered ceiling)') : fail('changes: ' + JSON.stringify(changes));
  (await page.locator('[data-approve-bounds]').count() === 1 && await page.locator('button:has-text("Approve")').count() === 1)
    ? ok('the only approve control on Launch is still "Approve the new bounds"') : fail('approve controls changed');
  writes(srv, /\/approve$/).length === 0 ? ok('nothing was approved by any of this (0 approve POSTs)') : fail('approve POST sent');
  const errs = realErrors(pageErrors);
  errs.length === 0 ? ok('no uncaught page errors') : errs.forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

async function reviewRefusal(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, srv);
  // The server holds the campaign as awaiting (budget raised through the API).
  srv.camp.how.budget = { source: 'own', amount: 1 };
  srv.camp.plan.how = srv.camp.how;
  srv.awaiting = true;
  // The browser holds the same edit (a human raised it), so Launch can list it.
  await page.evaluate(() => { const c = window.DeskV1Store.state().campaigns.find((x) => x.id === 'camp-1'); c.how.budget = { source: 'own', amount: 1 }; c.plan.how.budget = { source: 'own', amount: 1 }; });
  await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-1' }));
  await settle(page, () => !!document.querySelector('.desk-v1-review [data-act-primary]'));
  (await page.locator('[data-reapproval-refusal]').count()) === 0 ? ok('Review shows no link before a refusal') : fail('link before any refusal');

  await page.click('[data-act-primary]');
  await settle(page, () => !!document.querySelector('[data-reapproval-refusal]'));
  const block = await page.locator('[data-reapproval-refusal]').innerText();
  (/Go to Launch to approve/.test(block) && !/—/.test(block)) ? ok(`a refusal for a widened campaign shows "${block.replace(/\s+/g, ' ').trim()}"`) : fail('block: ' + block);
  writes(srv, /^\/api\/desk\/campaigns\//).length === 0
    ? ok('Review made 0 writes to /api/desk/campaigns (no inline approve)') : fail('Review wrote to a campaign route');

  await page.click('[data-reapproval-goto]');
  await settle(page, () => !!document.querySelector('[data-approve-bounds]'));
  ok('"Go to Launch to approve" lands on that campaign\'s Launch stop, with its Approve button');

  // The link does not appear for a refusal without the code (a different reason).
  srv.refuseCode = false;
  await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-2' }));
  await settle(page, () => !!document.querySelector('.desk-v1-review [data-act-primary]'));
  await page.click('[data-act-primary]');
  await settle(page, () => /Not approved/.test([...document.querySelectorAll('.toast')].map((t) => t.innerText).join(' ')));
  (await page.locator('[data-reapproval-refusal]').count()) === 0
    ? ok('a refusal with no code shows no Launch link (never matched on the text)') : fail('link shown for an uncoded refusal');
  const errs = realErrors(pageErrors);
  errs.length === 0 ? ok('no uncaught page errors') : errs.forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

async function badges(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, srv);
  await page.evaluate(() => window.deskV1GotoCampaignPanel('launch', { campaignId: 'camp-1' }));
  await settle(page, () => !!document.querySelector('#desk-v1-camp-summary [data-status-control]'));
  (await page.locator('[data-reapproval-badge]').count()) === 0 ? ok('an approved, unchanged campaign shows no badge') : fail('badge on an unchanged campaign');

  await page.evaluate(() => {
    const c = window.DeskV1Store.state().campaigns.find((x) => x.id === 'camp-1');
    c.how.budget = { source: 'own', amount: 1 };
    c.plan.how.budget = { source: 'own', amount: 1 };
    window.deskV1FillCampaignSummary(document.getElementById('desk-v1-camp-summary'), { campaignId: 'camp-1' });
  });
  await settle(page, () => !!document.querySelector('#desk-v1-camp-summary [data-reapproval-badge]'));
  ok('the campaign summary shows "Awaiting approval" once a limit is widened');
  await page.click('#desk-v1-camp-summary [data-reapproval-badge]');
  await settle(page, () => !!document.querySelector('[data-approve-bounds]'));
  ok('the badge links to Launch');

  await page.evaluate(() => window.deskV1Nav('piece', { campaignId: 'camp-1', familyId: 'p-1' }));
  await settle(page, () => !!document.querySelector('#desk-v1-piece-header .desk-v1-piece-title, .desk-v1-piece-title'));
  await settle(page, () => !!document.querySelector('.desk-v1-piece-header-top [data-reapproval-badge]'));
  ok('the piece page header shows the badge too');
  await page.click('.desk-v1-piece-header-top [data-reapproval-badge]');
  await settle(page, () => !!document.querySelector('[data-approve-bounds]'));
  ok('and it links to the campaign\'s Launch');
  await ctx.close();
}

const browser = await chromium.launch();
try {
  console.log('1. warn at the edit, Launch names the change');
  await warnAtTheEdit(browser);
  console.log('2. Review refusal -> Launch');
  await reviewRefusal(browser);
  console.log('3. badges');
  await badges(browser);
} catch (e) {
  fail('harness error: ' + (e && e.stack || e));
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) FAILED` : '\nAll checks passed.');
process.exit(bad ? 1 : 0);
