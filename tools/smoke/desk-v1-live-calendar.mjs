#!/usr/bin/env node
/**
 * Desk v1 (MC-1021 R1-W S6) — the ⑤ When stop (calendar) against a fake
 * server, `desk_v1_live` ON, plus the flag-OFF contract.
 *
 * What is under test is which requests the browser makes and what it paints from
 * the answers (the routes are pinned by tests/test_desk_pieces.py and
 * tests/test_desk_when.py):
 *   1. Tray drop   -> a version with no time gets `{scheduled_at}` through M18 and NOTHING
 *                     else (no `state`: setting a time is not approval); Undo PATCHes null.
 *   2. Same-day    -> a scheduled version moved within its approved day sends only
 *                     `{scheduled_at}` and stays scheduled, approval intact.
 *   3. Other day   -> a scheduled version moved to another day sends `needs_review` with the
 *                     time, the server withdraws the approval, and the toast has NO Undo
 *                     (nothing can restore an approval but a human).
 *   4. Refused     -> a 409 rolls the chip back and shows the server's reason.
 *   5. Sent        -> a version already handed to a platform is refused up front, 0 requests.
 *   6. Slots       -> a new own slot, accepting a suggested slot and a Suggest fill each PATCH
 *                     the campaign's `when`; a refusal rolls each back.
 *   7. Flag OFF    -> the same drags make 0 /api/desk/* requests.
 *
 * The page clock and zone are pinned (see desk-v1-calendar.mjs, b7535ff6): the grid
 * anchors on `new Date()`, so an unpinned run would pass or fail by the calendar date.
 *
 * RUN   cd tools/smoke && node desk-v1-live-calendar.mjs
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
const SMOKE_TZ = 'America/Los_Angeles';
const SMOKE_NOW = new Date('2026-09-30T12:00:00-07:00');

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

const SENT = ['sending', 'submitted', 'verified_published', 'you_reported', 'unknown_outcome', 'failed'];
const WRITABLE = ['drafting', 'needs_review', 'planned', 'skipped', 'archived'];

function makeServer({ agentSlot = false } = {}) {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  const v = (id, channelId, state, extra = {}) => ({ id, channelId, state, revision: 0, body: '', claimsState: {}, approved: null, failure: null, ...extra });
  // The pieces are this smoke's own (dates relative to the pinned 2026-09-30, a Wednesday),
  // so no fixture date can drift out from under it. Campaign + accounts are the fixtures'.
  const pieces = [
    { id: 'p-a', campaignId: 'camp-1', kind: 'post', title: 'Piece A', assets: [], versions: [
      v('v-unsched', 'ch-x-ron', 'planned'),
      v('v-sched', 'ch-blog', 'scheduled', { publishAt: '2026-09-30T15:00:00-07:00', approved: { at: '2026-09-29T10:00:00Z', by: 'ron' } }),
    ] },
    { id: 'p-b', campaignId: 'camp-1', kind: 'post', title: 'Piece B', assets: [], versions: [
      v('v-sent', 'ch-x-ron', 'verified_published', { publishedAt: '2026-09-22T09:00:00-07:00', receipt: { post_id: '1', posted_at: '2026-09-22T09:00:00-07:00' } }),
      v('v-review', 'ch-blog', 'needs_review', { publishAt: '2026-10-01T10:00:00-07:00' }),
    ] },
  ];
  const ws = workspaceFromFixtures(fx);
  const camp = JSON.parse(JSON.stringify(ws.campaigns.find((c) => c.id === 'camp-1')));
  camp.when = { slots: [] };
  if (agentSlot) {
    camp.when.slots.push({ id: 'slot-agent-1', at: '2026-10-03T09:00:00-07:00', origin: 'agent', state: 'suggested' });
    camp.how = { ...(camp.how || {}), suggested: { what: [], when: { label: '3x/week', slotId: 'slot-agent-1' }, where: null } };
  }
  const srv = { log: [], next: {}, fx, pieces, camp, projects, ws };
  srv.workspace = () => ({
    ...ws, projects, campaigns: [JSON.parse(JSON.stringify(camp))], pieces: JSON.parse(JSON.stringify(pieces)),
  });
  return srv;
}

async function newPage(browser, { live, srv }) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1400 }, timezoneId: SMOKE_TZ });
  await ctx.clock.install({ time: SMOKE_NOW });
  const page = await ctx.newPage();
  if (!live) await installDemoFixtures(page);   // demo mode is the harness's: the page ships no fixtures (S10)
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.addInitScript(() => { window.__confirms = 0; window.confirm = () => { window.__confirms++; return true; }; });
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
    const refuse = Object.keys(srv.next).find((k) => key === k || key.startsWith(k));
    if (refuse) { const msg = srv.next[refuse]; delete srv.next[refuse]; return J({ error: msg }, 409); }
    if (path === '/api/desk/workspace') return J(srv.workspace());
    const m = path.match(/^\/api\/desk\/pieces\/([^/]+)\/versions\/([^/]+)$/);
    if (m && method === 'PATCH') {
      const p = srv.pieces.find((x) => x.id === m[1]);
      const ver = p && p.versions.find((x) => x.id === m[2]);
      if (!ver) return J({ error: 'version not found' }, 404);
      if ('state' in body && !WRITABLE.includes(body.state)) return J({ error: 'state can only be set to ' + WRITABLE.join(', ') + ' here; approval is its own human action' }, 400);
      if (SENT.includes(ver.state)) return J({ error: `this version is ${ver.state}; it cannot be changed` }, 409);
      if ('scheduled_at' in body) { if (body.scheduled_at) ver.publishAt = body.scheduled_at; else delete ver.publishAt; }
      if (body.state) {
        if (body.state !== ver.state && ['approved', 'scheduled'].includes(ver.state)) ver.approved = null;
        ver.state = body.state;
      }
      return J(p);
    }
    const c = path.match(/^\/api\/desk\/campaigns\/([^/]+)$/);
    if (c && method === 'PATCH') {
      if (c[1] !== srv.camp.id) return J({ error: 'campaign not found' }, 404);
      if ('when' in body) srv.camp.when = body.when;
      return J(srv.camp);
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors };
}

const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });
const calls = (srv, method, pathRe) => srv.log.filter((r) => r.method === method && pathRe.test(r.path));
const deskCalls = (srv) => srv.log.filter((r) => r.path !== '/api/desk/workspace');
const realErrors = (pageErrors) => pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
const sv = (srv, pid, vid) => srv.pieces.find((p) => p.id === pid).versions.find((x) => x.id === vid);
const clearToasts = (page) => page.evaluate(() => document.querySelectorAll('.toast').forEach((t) => t.remove()));
async function until(pred, ms = 8000) {
  const t0 = Date.now();
  while (!pred()) { if (Date.now() - t0 > ms) throw new Error('timed out waiting for the request'); await new Promise((r) => setTimeout(r, 25)); }
}
const toastText = (page) => page.evaluate(() => [...document.querySelectorAll('.toast')].map((t) => t.innerText).join(' | '));

async function openCalendar(page, { live }) {
  if (live) await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.evaluate(() => window.deskV1Nav('calendar', { campaignId: 'camp-1' }));
  await page.waitForSelector('.desk-v1-calendar .desk-v1-cal-row-slots', { timeout: 8000 });
}

async function dragTo(page, fromSel, toSel, draggingSel) {
  // An earlier write's toast can sit over a drop cell and swallow the pointer.
  await clearToasts(page);
  const a = await page.locator(fromSel).first().boundingBox();
  const b = await page.locator(toSel).first().boundingBox();
  const cx = a.x + a.width / 2, cy = a.y + a.height / 2;
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  await page.mouse.move(cx + 15, cy + 5, { steps: 3 });
  await page.waitForSelector(draggingSel, { timeout: 2000 }).catch(() => {});
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 8 });
  await page.mouse.up();
}

// The keyboard path (Enter on a chip -> datetime input -> Move) is the same command as a drag.
async function keyboardMove(page, versionId, localValue) {
  await page.focus(`[data-chip-version="${versionId}"]`);
  await page.keyboard.press('Enter');
  await page.waitForSelector('.desk-v1-cal-kbd-reschedule [data-kbd-when]', { timeout: 3000 });
  await page.fill('[data-kbd-when]', localValue);
  await page.click('[data-kbd-move]');
}

// ── 1: tray drop sends a time and nothing else ─────────────────────────────
async function trayDrop(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await openCalendar(page, { live: true });
  srv.log.length = 0;

  const dayKey = await page.locator('.desk-v1-cal-cell[data-channel-id="ch-x-ron"]').first().getAttribute('data-day-key');
  await dragTo(page, '[data-unsched-version="v-unsched"]', '.desk-v1-cal-cell[data-channel-id="ch-x-ron"]', '.desk-v1-cal-unscheduled-dragging');
  await settle(page, () => !!document.querySelector('[data-chip-version="v-unsched"]'));
  const patches = calls(srv, 'PATCH', /\/versions\/v-unsched$/);
  const want = await page.evaluate((k) => { const [y, m, d] = k.split('-').map(Number); return new Date(y, m - 1, d, 12).toISOString(); }, dayKey);
  (patches.length === 1 && JSON.stringify(Object.keys(patches[0].body)) === '["scheduled_at"]' && patches[0].body.scheduled_at === want)
    ? ok(`a tray drop PATCHes M18 with {scheduled_at} only (${want}); no state is sent`) : fail('tray PATCH: ' + JSON.stringify(patches.map((r) => r.body)));
  const stored = sv(srv, 'p-a', 'v-unsched');
  (new Date(stored.publishAt).toISOString() === want && stored.state === 'planned')
    ? ok('the server holds the time and the version is still `planned`: scheduling is not approval') : fail('server version: ' + JSON.stringify(stored));

  await page.click('#desk-v1-undo'); // quiet Undo (Ron 2026-10-01): the header button, not a toast
  await settle(page, () => !document.querySelector('[data-chip-version="v-unsched"]'));
  await page.waitForTimeout(100);
  const undo = calls(srv, 'PATCH', /\/versions\/v-unsched$/);
  (undo.length === 2 && undo[1].body.scheduled_at === null && !sv(srv, 'p-a', 'v-unsched').publishAt)
    ? ok('Undo PATCHes {scheduled_at:null} and the server has no time again') : fail('undo PATCH: ' + JSON.stringify(undo.map((r) => r.body)));
  const errs = realErrors(pageErrors);
  errs.length === 0 ? ok('no uncaught page errors') : errs.forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

// ── 2/3/4/5: moving versions ───────────────────────────────────────────────
async function moves(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await openCalendar(page, { live: true });
  srv.log.length = 0;

  // 2: same approved day, 15:00 -> 17:30 (Wed 2026-09-30 in the pinned zone)
  await keyboardMove(page, 'v-sched', '2026-09-30T17:30');
  await until(() => calls(srv, 'PATCH', /\/versions\/v-sched$/).length === 1);
  await page.waitForSelector('#desk-v1-undo:not([disabled])'); // quiet Undo: no toast to wait for
  let p = calls(srv, 'PATCH', /\/versions\/v-sched$/);
  const s1 = sv(srv, 'p-a', 'v-sched');
  (p.length === 1 && JSON.stringify(Object.keys(p[0].body)) === '["scheduled_at"]' && s1.state === 'scheduled' && s1.approved)
    ? ok('a same-day shift sends {scheduled_at} only; the server keeps `scheduled` and the approval') : fail('same-day: ' + JSON.stringify({ p: p.map((r) => r.body), s1 }));
  const asked = await page.evaluate(() => window.__confirms);
  asked === 0 ? ok('a same-day shift asks nothing') : fail('same-day shift prompted for approval');

  // 3: another day -> needs_review + approval withdrawn, no Undo offered
  srv.log.length = 0;
  await dragTo(page, '[data-chip-version="v-sched"]', '.desk-v1-cal-cell[data-channel-id="ch-blog"][data-day-key="2026-10-02"]', '.desk-v1-cal-chip-dragging');
  await settle(page, () => window.DeskV1Store.state().families[0].versions[1].state === 'needs_review');
  await page.waitForTimeout(100);
  p = calls(srv, 'PATCH', /\/versions\/v-sched$/);
  const s2 = sv(srv, 'p-a', 'v-sched');
  (p.length === 1 && p[0].body.state === 'needs_review' && !!p[0].body.scheduled_at && !('approved' in p[0].body)
    && s2.state === 'needs_review' && s2.approved === null)
    ? ok('a move to another day sends {scheduled_at, state:needs_review}; the server withdrew the approval') : fail('other-day: ' + JSON.stringify({ p: p.map((r) => r.body), s2 }));
  (await page.evaluate(() => window.__confirms)) === 1 ? ok('the move asked for approval again first (window.confirm)') : fail('no approval prompt on an out-of-window move');
  await page.waitForFunction(() => /approval was withdrawn/.test(document.body.innerText), null, { timeout: 8000 });
  const t3 = await toastText(page);
  const undoBtns = await page.locator('.toast .toast-btn', { hasText: 'Undo' }).count();
  (/approval was withdrawn/.test(t3) && undoBtns === 0)
    ? ok('the toast says the approval was withdrawn and offers NO Undo (only a human can re-approve)') : fail(`toast: ${t3} / undo buttons: ${undoBtns}`);
  const local = await page.evaluate(() => { const v = window.DeskV1Store.state().families[0].versions[1]; return { state: v.state, approved: v.approved }; });
  (local.state === 'needs_review' && local.approved === null) ? ok('the page agrees: needs_review, no approval stamp') : fail('local: ' + JSON.stringify(local));

  // 4: refused -> rolled back + reason
  srv.log.length = 0;
  const before = await page.evaluate(() => window.DeskV1Store.state().families[1].versions[1].publishAt);
  srv.next['PATCH /api/desk/pieces/p-b/versions/v-review'] = 'the cadence for that week is full';
  await keyboardMove(page, 'v-review', '2026-10-02T09:00');
  await page.waitForFunction(() => /was not saved: the cadence for that week is full/.test(document.body.innerText), null, { timeout: 8000 });
  const after = await page.evaluate(() => window.DeskV1Store.state().families[1].versions[1].publishAt);
  (after === before && sv(srv, 'p-b', 'v-review').publishAt === '2026-10-01T10:00:00-07:00')
    ? ok("a refused move puts the time back and shows the server's reason") : fail(`refused: before ${before} after ${after}`);

  // 5: a sent version is refused before any request
  srv.log.length = 0;
  const res = await page.evaluate(() => window.deskV1CalendarRescheduleVersion('v-sent', new Date('2026-10-03T10:00:00-07:00')));
  const t5 = await toastText(page);
  (res === false && deskCalls(srv).length === 0 && /already verified published/.test(t5))
    ? ok('a published version is refused up front: returns false, 0 requests, the toast says why') : fail(`sent: res=${res} calls=${deskCalls(srv).length} toast=${t5}`);

  const errs = realErrors(pageErrors);
  errs.length === 0 ? ok('no uncaught page errors') : errs.forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

// ── 6: slots ───────────────────────────────────────────────────────────────
async function slots(browser) {
  const srv = makeServer({ agentSlot: true });
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await openCalendar(page, { live: true });
  srv.log.length = 0;

  // New own slot (Fri 2026-10-02 14:00 default)
  await dragTo(page, '[data-slot-handle]', '.desk-v1-cal-row-slots .desk-v1-cal-slotcell[data-day-key="2026-10-02"]', '.desk-v1-cal-slot-handle-dragging');
  await settle(page, () => window.DeskV1Store.state().campaigns[0].when.slots.length === 2);
  await page.waitForSelector('#desk-v1-undo:not([disabled])'); // quiet Undo: no toast to wait for
  let p = calls(srv, 'PATCH', /^\/api\/desk\/campaigns\/camp-1$/);
  const mine = p.length === 1 && p[0].search === '?shape=v1' && p[0].body.when.slots.find((s) => s.origin === 'user');
  (mine && p[0].body.when.slots.length === 2 && srv.camp.when.slots.length === 2)
    ? ok('a new own slot PATCHes the campaign {when} (v1 shape) and the server holds both slots') : fail('slot create: ' + JSON.stringify(p.map((r) => r.body)));

  // Refused slot create -> rolled back
  srv.log.length = 0;
  srv.next['PATCH /api/desk/campaigns/camp-1'] = 'this campaign is archived';
  await dragTo(page, '[data-slot-handle]', '.desk-v1-cal-row-slots .desk-v1-cal-slotcell[data-day-key="2026-10-01"]', '.desk-v1-cal-slot-handle-dragging');
  await page.waitForFunction(() => /was not saved: this campaign is archived/.test(document.body.innerText), null, { timeout: 8000 });
  const n = await page.evaluate(() => window.DeskV1Store.state().campaigns[0].when.slots.length);
  n === 2 ? ok('a refused slot is taken off the grid and the server\'s reason is shown') : fail('refused slot left ' + n + ' slots');

  // Accept the agent's suggested slot
  srv.log.length = 0;
  await page.click('[data-suggested-slot-accept]');
  await settle(page, () => window.DeskV1Store.state().campaigns[0].when.slots.find((s) => s.id === 'slot-agent-1').state === 'accepted');
  await page.waitForTimeout(100);
  p = calls(srv, 'PATCH', /^\/api\/desk\/campaigns\/camp-1$/);
  const acc = srv.camp.when.slots.find((s) => s.id === 'slot-agent-1');
  (p.length === 1 && acc.state === 'accepted' && p[0].body.when.slots.some((s) => s.id === 'slot-agent-1' && s.state === 'accepted') && !('state' in p[0].body))
    ? ok('accepting a suggested slot PATCHes {when} with that slot `accepted`, and no campaign state') : fail('accept: ' + JSON.stringify(p.map((r) => r.body)));

  // Suggest fill: persisted, and a refusal removes it
  srv.log.length = 0;
  const userSlotId = await page.evaluate(() => window.DeskV1Store.state().campaigns[0].when.slots.find((s) => s.origin === 'user').id);
  await page.evaluate((id) => window.deskV1CalendarSuggestFill('camp-1', { slotId: id, versionId: 'v-unsched' }), userSlotId);
  await page.waitForTimeout(150);
  p = calls(srv, 'PATCH', /^\/api\/desk\/campaigns\/camp-1$/);
  const filled = srv.camp.when.slots.find((s) => s.id === userSlotId).filled;
  (p.length === 1 && filled && filled.versionId === 'v-unsched' && filled.channelId === 'ch-x-ron')
    ? ok('a Suggest fill is saved with the placed version and its OWN account (it does not choose one)') : fail('fill: ' + JSON.stringify({ p: p.map((r) => r.body), filled }));
  const verAcct = sv(srv, 'p-a', 'v-unsched').channelId;
  verAcct === 'ch-x-ron' ? ok('no version was created or moved to another account') : fail('account changed: ' + verAcct);

  srv.log.length = 0;
  srv.next['PATCH /api/desk/campaigns/camp-1'] = 'nope';
  await page.evaluate((id) => {
    const s = window.DeskV1Store.state().campaigns[0].when.slots.find((x) => x.id === id);
    delete s.filled;
    window.deskV1CalendarSuggestFill('camp-1', { slotId: id, versionId: 'v-unsched' });
  }, userSlotId);
  await page.waitForFunction((id) => !window.DeskV1Store.state().campaigns[0].when.slots.find((x) => x.id === id).filled, userSlotId, { timeout: 8000 });
  ok('a refused Suggest fill is taken back off the slot');

  const errs = realErrors(pageErrors);
  errs.length === 0 ? ok('no uncaught page errors') : errs.forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

// ── 7: flag OFF -> demo, 0 /api/desk requests ──────────────────────────────
async function flagOff(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: false, srv });
  await openCalendar(page, { live: false });
  srv.log.length = 0;
  await dragTo(page, '[data-slot-handle]', '.desk-v1-cal-row-slots .desk-v1-cal-slotcell[data-day-key="2026-10-02"]', '.desk-v1-cal-slot-handle-dragging');
  await settle(page, () => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-1').when.slots.length >= 1);
  await page.evaluate(() => window.deskV1CalendarRescheduleVersion('v-install-li', new Date('2026-10-02T12:00:00-07:00')));
  await page.waitForTimeout(150);
  srv.log.length === 0 ? ok('flag OFF: a slot drag and a reschedule make 0 /api/desk/* requests') : fail('demo mode hit the backend: ' + JSON.stringify(srv.log.map((r) => r.method + ' ' + r.path)));
  const errs = realErrors(pageErrors);
  errs.length === 0 ? ok('no uncaught page errors') : errs.forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  for (const [name, fn] of [['tray drop', trayDrop], ['moves', moves], ['slots', slots], ['flag off', flagOff]]) {
    console.log(`\n${name}`);
    try { await fn(browser); } catch (e) { fail(`${name}: ${e && e.message ? e.message : e}`); }
  }
} finally {
  await browser.close();
}
console.log(bad ? `\n❌ FAIL — ${bad} check(s) failed` : '\n✅ PASS — Desk v1 R1-W S6: When saves times and slots through M18 and PATCH when; scheduling never approves; flag OFF makes no requests.');
process.exit(bad ? 1 : 0);
