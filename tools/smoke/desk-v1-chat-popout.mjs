#!/usr/bin/env node
/**
 * Desk chat (Ron 2026-10-15, two bugs from the campaign agent chat):
 *
 *   A. The box must be EMPTY after a successful send, docked and popped out, and after the
 *      pop-out closes. (Ron sent a long, multi-line first message and its full text was still
 *      in the box in the pop-out.) A failed send still puts the text back.
 *   B. The pop-out must be resizable like every other window: it takes the shared 8-handle
 *      resizer (makeResizable in interactions.js, .mc-resize-handle). Shared chrome, so the
 *      Studio thread's pop-out is checked too.
 *
 * A fake server stands in for /api/desk/* and the model call.
 *
 *   1. Send       -> docked: box empty; pop out: empty; send from the pop-out: empty; close: empty.
 *   2. Long send  -> a pasted multi-line message (the case that was reported) also empties the box.
 *   3. Failed     -> the text comes back, docked and popped.
 *   4. Resize     -> each of the 8 handles changes the pop-out's size/position the right way; the
 *                    minimum holds; the composer and Send stay inside; a drag that ends outside the
 *                    box does not close it; closing restores the docked layout (no inline size).
 *   5. Studio     -> the Studio thread's pop-out resizes too.
 *   6. Shots      -> 1440 wide into _scratch/desk_split/.
 *
 * RUN   cd tools/smoke && node desk-v1-chat-popout.mjs
 */
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const SHOTS = resolve(REPO_ROOT, '_scratch', 'desk_split');
const ORIGIN = 'http://mc.smoke.test';
const STATIC = loadStaticJsCss(REPO_ROOT);

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, pass, failMsg) => (cond ? ok(pass) : fail(failMsg));
const near = (a, b, tol = 3) => Math.abs(a - b) <= tol;

let GID = 0;
const LONG = 'I want to being planning on full launch of ClayRune and taking it to next level. Can you help me design the campaign which elevates the features we offer?\nIdentify the right features to elaborate on which "puts us ahead of the competition". They do not have to be accurate claims but bundling all of them in single product is what mostly unique us';

function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: p.id === 'clayrune' ? 'global:claydo' : null, state: 'active' } }));
  const campaigns = fx.campaigns.map((c) => JSON.parse(JSON.stringify(c)));
  const srv = { fx, projects, campaigns, threads: {}, mode: 'ok', posts: [] };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects, campaigns: campaigns.map((c) => JSON.parse(JSON.stringify(c))) });
  return srv;
}

async function newPage(browser, srv, viewport) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
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
      distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3, distiller_max_experiments_per_session: 3,
      distiller_min_turns: 5, distiller_skip_errors: true, roster: [],
    })));
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true, user_timezone: '' });
    if (path === '/api/characters') return J([{ name: 'claydo', scope: 'global', agent_name: 'Claydo', avatar: '' }]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* no body */ }
    if (path === '/api/desk/workspace') return J(srv.workspace());
    const m = path.match(/^\/api\/desk\/campaigns\/([^/]+)\/chat$/);
    if (m) {
      const id = m[1];
      if (method === 'GET') return J({ thread: (srv.threads[id] || []).map((t) => ({ ...t })) });
      srv.posts.push(body);
      if (srv.delay) await new Promise((r) => setTimeout(r, srv.delay));
      if (srv.mode === 'fail') return J({ error: 'the agent this campaign picked no longer exists' }, 404);
      const mk = (role, text) => ({ id: 'tn' + (++GID), role, text, at: '2026-10-06T10:00:00Z', scope: null, agent: role === 'agent' ? 'Claydo' : null, suggested: null, suggest_error: null });
      const turns = [mk('user', body.message), mk('agent', 'Happy to build the launch plan. What I need from you: the feature list.')];
      (srv.threads[id] = srv.threads[id] || []).push(...turns);
      return J({ agent: { ref: 'global:claydo', name: 'Claydo' }, provider: 'claude', model: 'sonnet', turns });
    }
    if (path === '/api/desk/storyboard/chat') return method === 'GET' ? J({ thread: [] }) : J({ error: 'not used here' }, 500);
    if (path === '/api/desk/materials') return J({ library: { video: [], image: [] }, articles: [], online: { video: [], image: [] }, recent: [] });
    if (path === '/api/desk/studio/storyboards') return J({ storyboards: [] });
    return J({});
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.waitForFunction(() => window.DeskV1Store.state().campaigns.length > 0, null, { timeout: 8000 });
  return { ctx, page, pageErrors };
}

const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });
const realErrors = (errs) => errs.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
const INPUT = '#desk-v1-camp-posy-input';
const boxValue = (page) => page.$eval(INPUT, (e) => e.value);
const turnCount = (page) => page.$$eval('[data-chat-turn="agent"]:not([data-chat-thinking])', (n) => n.length);
const isPopped = (page, sel = '[data-camp-chat]') => page.$eval(sel, (e) => e.classList.contains('is-popped'));

async function openCampaign(page) {
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await settle(page, () => !!document.querySelector('.desk-v1-campaign'));
  await page.evaluate(() => window.deskV1GotoCampaignPanel('how', { campaignId: 'camp-1' }));
  await settle(page, () => !!document.querySelector('[data-camp-chat]'));
}

async function sendFrom(page, text, wantAgentTurns) {
  await page.fill(INPUT, text);
  await page.press(INPUT, 'Enter');
  await settle(page, (n) => document.querySelectorAll('[data-chat-turn="agent"]:not([data-chat-thinking])').length >= n, wantAgentTurns);
  await page.waitForTimeout(150);
}

async function sending(browser, srv) {
  console.log('\n[box empties on send]');
  const { ctx, page, pageErrors } = await newPage(browser, srv, { width: 1440, height: 950 });
  await openCampaign(page);

  // 1. docked
  await sendFrom(page, 'first question', 1);
  check((await boxValue(page)) === '', 'docked: the box is empty after a successful send', 'docked box: ' + JSON.stringify(await boxValue(page)));
  await page.click('[data-camp-chat] [data-chat-pop]');
  await settle(page, () => document.querySelector('[data-camp-chat]').classList.contains('is-popped'));
  check((await boxValue(page)) === '', 'pop-out opened after the send: the box is empty', 'popped box: ' + JSON.stringify(await boxValue(page)));

  // send from the pop-out
  await sendFrom(page, 'second question from the large view', 2);
  check((await boxValue(page)) === '', 'popped: the box is empty after a send from the pop-out', 'popped send box: ' + JSON.stringify(await boxValue(page)));
  await page.keyboard.press('Escape');
  await settle(page, () => !document.querySelector('[data-camp-chat]').classList.contains('is-popped'));
  check((await boxValue(page)) === '', 'closing the pop-out leaves the box empty', 'after close: ' + JSON.stringify(await boxValue(page)));
  // a repaint of the box (a selection change repaints it) must not bring the sent text back
  await page.evaluate(() => window.deskV1GotoCampaignPanel('what', { campaignId: 'camp-1' }));
  await settle(page, () => !!document.querySelector('[data-what-row]'));
  await page.click('[data-what-row]');
  await settle(page, () => /About: /.test(document.querySelector('[data-scope-trigger]').textContent) && !!document.querySelector('[data-camp-chat]'));
  check((await boxValue(page)) === '', 'after the box was repainted the box is still empty', 'after repaint: ' + JSON.stringify(await boxValue(page)));

  // 2. the reported case: pasted, multi-line, long
  await page.click('[data-camp-chat] [data-chat-pop]');
  await settle(page, () => document.querySelector('[data-camp-chat]').classList.contains('is-popped'));
  await page.evaluate((t) => { const ta = document.querySelector('#desk-v1-camp-posy-input'); ta.value = t; ta.dispatchEvent(new Event('input', { bubbles: true })); }, LONG);
  await page.click('[data-camp-chat] [data-posy-send]');
  await settle(page, () => document.querySelectorAll('[data-chat-turn="agent"]:not([data-chat-thinking])').length >= 3);
  await page.waitForTimeout(200);
  check((await boxValue(page)) === '', 'long multi-line message sent with the Send button: the box is empty', 'long send box: ' + JSON.stringify((await boxValue(page)).slice(0, 60)));
  await page.keyboard.press('Escape');
  await settle(page, () => !document.querySelector('[data-camp-chat]').classList.contains('is-popped'));
  check((await boxValue(page)) === '', 'and still empty once the pop-out is closed', 'after close: ' + JSON.stringify((await boxValue(page)).slice(0, 60)));

  // the sent text re-appearing while the agent is still answering (an input method committing a late
  // composition after the box was cleared) is the SAME message: it must not stay in the box afterwards
  srv.delay = 900;
  await page.click('[data-camp-chat] [data-chat-pop]');
  await settle(page, () => document.querySelector('[data-camp-chat]').classList.contains('is-popped'));
  await page.fill(INPUT, LONG);
  await page.press(INPUT, 'Enter');
  await settle(page, () => !!document.querySelector('[data-chat-pending]'));
  await page.evaluate((t) => { const ta = document.querySelector('#desk-v1-camp-posy-input'); ta.value = t; ta.dispatchEvent(new Event('input', { bubbles: true })); }, LONG);
  await settle(page, () => document.querySelectorAll('[data-chat-turn="agent"]:not([data-chat-thinking])').length >= 4);
  await page.waitForTimeout(250);
  check((await boxValue(page)) === '', 'sent text re-inserted while the agent answered: the box is empty after the answer', 'late re-insert box: ' + JSON.stringify((await boxValue(page)).slice(0, 60)));
  await page.keyboard.press('Escape');
  srv.delay = 0;

  // 3. failed send puts the text back, docked and popped
  srv.mode = 'fail';
  await page.fill(INPUT, 'will fail');
  await page.press(INPUT, 'Enter');
  await settle(page, () => /back in the box/.test(document.querySelector('[data-camp-chat-status]').textContent));
  check((await boxValue(page)) === 'will fail', 'docked: a failed send puts the text back', 'docked failed: ' + JSON.stringify(await boxValue(page)));
  await page.click('[data-camp-chat] [data-chat-pop]');
  await settle(page, () => document.querySelector('[data-camp-chat]').classList.contains('is-popped'));
  await page.fill(INPUT, 'fails in the large view');
  await page.press(INPUT, 'Enter');
  await settle(page, () => /back in the box/.test(document.querySelector('[data-camp-chat-status]').textContent) && document.querySelector("#desk-v1-camp-posy-input").value !== '');
  check((await boxValue(page)) === 'fails in the large view', 'popped: a failed send puts the text back', 'popped failed: ' + JSON.stringify(await boxValue(page)));
  srv.mode = 'ok';
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

const rect = (page, sel) => page.$eval(sel, (e) => { const r = e.getBoundingClientRect(); return { l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height }; });
const handleAt = async (page, sel, dir) => page.$eval(`${sel} > .mc-resize-${dir}`, (e) => { const r = e.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2, cursor: getComputedStyle(e).cursor, shown: getComputedStyle(e).display !== 'none' }; });
async function dragHandle(page, sel, dir, dx, dy) {
  const h = await handleAt(page, sel, dir);
  await page.mouse.move(h.x, h.y);
  await page.mouse.down();
  await page.mouse.move(h.x + dx / 2, h.y + dy / 2, { steps: 4 });
  await page.mouse.move(h.x + dx, h.y + dy, { steps: 4 });
  await page.mouse.up();
}

async function resizing(browser, srv, S) {
  console.log(`\n[pop-out resize: ${S.name}]`);
  const { ctx, page, pageErrors } = await newPage(browser, srv, { width: 1440, height: 950 });
  await S.open(page);
  const sel = S.chat;
  const docked = await rect(page, sel);
  await page.click(`${sel} [data-chat-pop]`);
  await settle(page, (s) => document.querySelector(s).classList.contains('is-popped'), sel);
  const handles = await page.$$eval(`${sel} > .mc-resize-handle`, (n) => n.map((e) => e.dataset.resizeDir).sort());
  check(handles.join() === ['e', 'n', 'ne', 'nw', 's', 'se', 'sw', 'w'].join(), 'the pop-out carries the 8 shared resize handles', 'handles: ' + handles.join());
  const cursors = {};
  for (const d of handles) cursors[d] = (await handleAt(page, sel, d)).cursor;
  check(cursors.e === 'ew-resize' && cursors.n === 'ns-resize' && cursors.se === 'nwse-resize' && cursors.ne === 'nesw-resize', 'edge and corner cursors are the shared ones', 'cursors: ' + JSON.stringify(cursors));
  const start = await rect(page, sel);

  // each handle moves its own edge, anchoring the opposite one
  await dragHandle(page, sel, 'e', -120, 0);
  let r = await rect(page, sel);
  check(near(r.w, start.w - 120) && near(r.l, start.l), `east edge -120: width ${start.w} -> ${r.w}, left unchanged`, `east: ${JSON.stringify(r)} from ${JSON.stringify(start)}`);
  await dragHandle(page, sel, 'w', 80, 0);
  const r2 = await rect(page, sel);
  check(near(r2.w, r.w - 80) && near(r2.r, r.r) && near(r2.l, r.l + 80), `west edge +80: width ${r.w} -> ${r2.w}, right edge stays`, `west: ${JSON.stringify(r2)} from ${JSON.stringify(r)}`);
  await dragHandle(page, sel, 's', 0, -100);
  const r3 = await rect(page, sel);
  check(near(r3.h, r2.h - 100) && near(r3.t, r2.t), `south edge -100: height ${r2.h} -> ${r3.h}, top unchanged`, `south: ${JSON.stringify(r3)} from ${JSON.stringify(r2)}`);
  await dragHandle(page, sel, 'n', 0, 60);
  const r4 = await rect(page, sel);
  check(near(r4.h, r3.h - 60) && near(r4.b, r3.b) && near(r4.t, r3.t + 60), `north edge +60: height ${r3.h} -> ${r4.h}, bottom stays`, `north: ${JSON.stringify(r4)} from ${JSON.stringify(r3)}`);
  await dragHandle(page, sel, 'se', 90, 70);
  const r5 = await rect(page, sel);
  check(near(r5.w, r4.w + 90) && near(r5.h, r4.h + 70), `south-east corner +90,+70: ${Math.round(r4.w)}x${Math.round(r4.h)} -> ${Math.round(r5.w)}x${Math.round(r5.h)}`, `se: ${JSON.stringify(r5)} from ${JSON.stringify(r4)}`);
  await dragHandle(page, sel, 'nw', -50, -40);
  const r6 = await rect(page, sel);
  check(near(r6.w, r5.w + 50) && near(r6.h, r5.h + 40) && near(r6.r, r5.r) && near(r6.b, r5.b), 'north-west corner grows it up and left, bottom-right stays', `nw: ${JSON.stringify(r6)} from ${JSON.stringify(r5)}`);
  await dragHandle(page, sel, 'ne', 20, 20);
  await dragHandle(page, sel, 'sw', -20, -20);
  check(await isPopped(page, sel), 'the pop-out is still open after the resizes (a drag that ends over the page does not close it)', 'the pop-out closed during a resize');

  // a drag that ENDS outside the box must not read as a click on the dimmed page
  const re = await rect(page, sel);
  const he = await handleAt(page, sel, 'e');
  await page.mouse.move(he.x, he.y); await page.mouse.down();
  await page.mouse.move(he.x - 400, he.y, { steps: 6 });   // the box shrinks under the pointer: it ends outside
  await page.mouse.up();
  check(await isPopped(page, sel), 'a drag that ends outside the box does not close the pop-out', 'closed by the click that follows a drag');

  // minimum holds
  await dragHandle(page, sel, 'w', 2000, 0);
  await dragHandle(page, sel, 's', 0, -2000);
  const rm = await rect(page, sel);
  check(rm.w >= 300 && rm.h >= 240, `shrunk as far as it goes: ${Math.round(rm.w)}x${Math.round(rm.h)} (floor 300x240)`, `below the floor: ${JSON.stringify(rm)}`);
  const inside = await page.evaluate(([s, ask]) => {
    const box = document.querySelector(s).getBoundingClientRect();
    const send = document.querySelector(s + ' [data-posy-send], ' + s + ' .btn-dispatch');
    const ta = document.querySelector(s + ' ' + ask);
    const within = (e) => { if (!e) return null; const b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0 && b.left >= box.left - 1 && b.right <= box.right + 1 && b.top >= box.top - 1 && b.bottom <= box.bottom + 1; };
    return { ta: within(ta), send: within(send) };
  }, [sel, '[data-sb-ask]']);
  check(inside.ta === true && (inside.send === true || inside.send === null), 'at the minimum size the composer (and Send) are still inside the box', 'composer at minimum: ' + JSON.stringify(inside));

  // Esc closes; the docked layout is back with no inline size left behind
  await page.keyboard.press('Escape');
  await settle(page, (s) => !document.querySelector(s).classList.contains('is-popped'), sel);
  const after = await page.$eval(sel, (e) => ({ style: e.getAttribute('style') || '', w: e.getBoundingClientRect().width, h: e.getBoundingClientRect().height, handles: [...e.querySelectorAll(':scope > .mc-resize-handle')].map((h) => getComputedStyle(h).display) }));
  check(!/width|height|left|top|position|margin/.test(after.style), 'closed: no inline size or position left on the docked chat', 'inline style after close: ' + after.style);
  check(near(after.w, docked.w, 4) && near(after.h, docked.h, 40), `closed: the docked chat is back at ${Math.round(after.w)}x${Math.round(after.h)} (was ${Math.round(docked.w)}x${Math.round(docked.h)})`, `docked after: ${JSON.stringify(after)} before ${JSON.stringify(docked)}`);
  check(after.handles.every((d) => d === 'none'), 'closed: the resize handles are not shown on the docked chat', 'handles shown while docked: ' + after.handles.join());

  // reopen: a fresh default size, and it resizes again
  await page.click(`${sel} [data-chat-pop]`);
  await settle(page, (s) => document.querySelector(s).classList.contains('is-popped'), sel);
  const again = await rect(page, sel);
  await dragHandle(page, sel, 'e', -60, 0);
  const again2 = await rect(page, sel);
  check(near(again2.w, again.w - 60), 'reopened: it resizes again', `reopen resize: ${again.w} -> ${again2.w}`);

  if (S.shot) {
    mkdirSync(SHOTS, { recursive: true });
    await dragHandle(page, sel, 'w', -140, 0);
    await dragHandle(page, sel, 's', 0, -120);
    await page.waitForTimeout(250);
    await page.screenshot({ path: resolve(SHOTS, S.shot) });
    console.log('  shot ' + resolve(SHOTS, S.shot));
  }
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

const CAMP_CHAT = {
  name: 'campaign chat', chat: '[data-camp-chat]', shot: 'campaign_1440_popout_resized.png',
  open: async (page) => { await openCampaign(page); await sendFrom(page, 'hello', 1); },
};
const STUDIO_CHAT = {
  name: 'studio thread', chat: '[data-chat]', shot: 'studio_1440_popout_resized.png',
  open: async (page) => {
    await page.evaluate(() => window.deskV1Nav('studio', {}));
    await page.waitForSelector('[data-studio-new="video"]', { timeout: 8000 });
    await page.evaluate(() => window.deskV1Nav('studio-create', { kind: 'video' }));
    await page.waitForSelector('[data-studio-create][data-kind="video"] [data-storyboard]', { timeout: 8000 });
    await page.waitForSelector('[data-chat-chrome][data-wired]', { timeout: 8000 });
  },
};

const browser = await chromium.launch();
try {
  const srv = makeServer();
  await sending(browser, srv);
  await resizing(browser, srv, CAMP_CHAT);
  await resizing(browser, srv, STUDIO_CHAT);
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) FAILED` : '\nall checks passed');
process.exit(bad ? 1 : 0);
