#!/usr/bin/env node
/**
 * Desk v1 — a campaign whose plan end date has passed names the fix that works.
 *
 * Renew opens a next TERM, but the plan's end date gates posting first, so on
 * such a campaign "Renew" is a dead end. Against a fake server, live ON, at
 * 1440 and 390:
 *   1. Launch, plan end passed -> the dated message and an `Extend end date`
 *      button; NO Renew button. The button opens Brief with the Ends field
 *      focused and makes 0 write requests (it extends nothing, approves nothing).
 *   2. Launch, term ended but the plan end still ahead (capped long term)
 *      -> Renew term stays, enabled; no extend button, no ended message.
 *   3. A held version whose failure.code is `campaign_ended` (review card) ->
 *      the same button; a held version with any other reason gets none.
 *
 * RUN   cd tools/smoke && node desk-v1-campaign-ended.mjs
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
const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

const MSG = 'This campaign ended on 2026-10-28. Extend its end date, then approve to keep posting.';
const SRV_APPROVAL = (c) => ({ bounds: { accounts: c.plan.accounts, cadence: c.plan.cadence, end: c.plan.end, term: c.term || {}, budget: (c.how && c.how.budget) || {} }, bounds_hash: 'h', at: '2026-10-15T00:00:00Z', by: 'human', term: 1 });

function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  const ws = workspaceFromFixtures(fx);
  const campaigns = ws.campaigns.map((c) => JSON.parse(JSON.stringify(c)));
  // camp-1: a short campaign whose plan end (and term) are 2026-10-28; the clock is 2026-10-29.
  const short = campaigns.find((c) => c.id === 'camp-1');
  short.state = 'active'; short.goal = { ...short.goal, horizon: 'short' };
  short.plan.end = { date: '2026-10-28', post_cap: null };
  short.term = { index: 1, starts: '2026-10-15', ends: '2026-10-28', post_cap: null };
  short.approval = SRV_APPROVAL(short); short.approvals = [short.approval];
  // camp-4: a capped long term that ended, with the plan end still ahead.
  const long = campaigns.find((c) => c.id === 'camp-4');
  long.state = 'active'; long.goal = { ...long.goal, horizon: 'long', deadline: '2031-12-31' };
  long.plan.end = { date: '2027-02-28', post_cap: null };
  long.term = { index: 1, starts: '2026-07-01', ends: '2026-09-29', post_cap: null };
  long.approval = SRV_APPROVAL(long); long.approvals = [long.approval];
  const acct = { id: 'ch-x', platform: 'x', identity: '@ch-x', label: 'CH-X', capability: 'direct', voice: '', connected: true, publish: { ready: true, reason: null, secret: 'x.oauth-token', unattended_ok: true } };
  const ver = (id, failure) => ({ id, channelId: 'ch-x', state: 'held', revision: 0, body: '', claimsState: {}, approved: null, failure, claims: [] });
  const pieces = [{ id: 'p-1', campaignId: 'camp-1', kind: 'post', title: 'Held piece', body: 'A plain note.', assets: [], versions: [
    ver('v-ended', { reason: MSG, code: 'campaign_ended', at: '2026-10-29T00:00:00Z' }),
    ver('v-paused', { reason: 'the campaign is paused', at: '2026-10-29T00:00:00Z' }),
  ] }];
  const srv = { log: [], fx };
  srv.workspace = () => ({ ...ws, projects, accounts: [acct], campaigns: JSON.parse(JSON.stringify(campaigns)), pieces: JSON.parse(JSON.stringify(pieces)) });
  return srv;
}

async function newPage(browser, srv, width) {
  const ctx = await browser.newContext({ viewport: { width, height: width < 600 ? 900 : 1200 } });
  const page = await ctx.newPage();
  await page.clock.setFixedTime(new Date('2026-10-29T12:00:00Z'));
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
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
    srv.log.push({ method: req.method(), path });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/pieces' && req.method() === 'GET') return J(srv.workspace().pieces);
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.waitForFunction(() => window.DeskV1Store.state().campaigns.length > 0, null, { timeout: 8000 });
  return { ctx, page, pageErrors };
}

const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });
const writes = (srv) => srv.log.filter((r) => r.method !== 'GET');
const openLaunch = async (page, id) => {
  await page.evaluate((cid) => window.deskV1Nav('campaign', { campaignId: cid }), id);
  await settle(page, () => !!document.querySelector('.desk-v1-campaign'));
  await page.evaluate((cid) => window.deskV1GotoCampaignPanel('launch', { campaignId: cid }), id);
  await settle(page, () => !!document.querySelector('[data-launch-live]'));
};
// The control is on screen and not clipped by the viewport's width.
const fits = (page, sel) => page.evaluate((s) => { const r = document.querySelector(s).getBoundingClientRect(); return r.width > 0 && r.left >= 0 && r.right <= window.innerWidth; }, sel);

async function run(browser, width) {
  console.log(`width ${width}`);
  // 1: plan end passed
  {
    const srv = makeServer();
    const { ctx, page, pageErrors } = await newPage(browser, srv, width);
    await openLaunch(page, 'camp-1');
    const note = await page.locator('[data-launch-ended]').innerText();
    note === MSG ? ok('plan end passed: Launch states the dated fix') : fail('launch note: ' + note);
    (await page.locator('[data-renew-term]').count()) === 0 ? ok('Renew term is not offered') : fail('Renew term offered on a campaign whose plan end passed');
    (await page.locator('[data-extend-end-date]').count()) === 1 && await fits(page, '[data-extend-end-date]')
      ? ok('Extend end date is offered and fits the viewport') : fail('extend button missing or clipped');
    await page.click('[data-extend-end-date]');
    await settle(page, () => document.activeElement && document.activeElement.getAttribute('data-how-limit') === 'end_date');
    ok('Extend end date opens Brief with the Ends field focused');
    writes(srv).length === 0 ? ok('and makes 0 write requests (no extend, no approval)') : fail('writes: ' + JSON.stringify(writes(srv)));
    const errs = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    errs.length === 0 ? ok('no uncaught page errors') : errs.forEach((e) => fail('page error: ' + e));
    await ctx.close();
  }
  // 2: capped term, plan end ahead
  {
    const srv = makeServer();
    const { ctx, page } = await newPage(browser, srv, width);
    await openLaunch(page, 'camp-4');
    (await page.locator('[data-renew-term]').isEnabled()) ? ok('term ended, plan end ahead: Renew term stays, enabled') : fail('Renew term not enabled');
    ((await page.locator('[data-extend-end-date]').count()) === 0 && (await page.locator('[data-launch-ended]').count()) === 0)
      ? ok('and no extend button or ended message') : fail('extend UI shown on a renewable campaign');
    await ctx.close();
  }
  // 3: held version on the review card
  {
    const srv = makeServer();
    const { ctx, page } = await newPage(browser, srv, width);
    await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-ended' }));
    await settle(page, () => !!document.querySelector('[data-review-held]'));
    const held = await page.locator('[data-review-held]').innerText();
    (held.includes(MSG) && (await page.locator('[data-review-held] [data-extend-end-date]').count()) === 1 && await fits(page, '[data-review-held] [data-extend-end-date]'))
      ? ok('review card: held for campaign_ended shows the message and Extend end date') : fail('held card: ' + held);
    await page.click('[data-review-held] [data-extend-end-date]');
    await settle(page, () => document.activeElement && document.activeElement.getAttribute('data-how-limit') === 'end_date');
    ok('the review card button opens Brief with the Ends field focused');
    await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-paused' }));
    await settle(page, () => /the campaign is paused/.test((document.querySelector('[data-review-held]') || {}).innerText || ''));
    (await page.locator('[data-extend-end-date]').count()) === 0 ? ok('a held version with another reason gets no extend button') : fail('extend button on an unrelated hold');
    writes(srv).length === 0 ? ok('0 write requests') : fail('writes: ' + JSON.stringify(writes(srv)));
    await ctx.close();
  }
}

const browser = await chromium.launch();
try {
  await run(browser, 1440);
  await run(browser, 390);
} catch (e) { fail('harness error: ' + (e && e.stack || e)); }
await browser.close();
if (bad) { console.error(`\n❌ FAIL — ${bad} case(s)`); process.exit(1); }
console.log('\n✅ PASS — an ended campaign names the end-date fix, offers a way to the Ends field, and keeps Renew only where Renew works.');
