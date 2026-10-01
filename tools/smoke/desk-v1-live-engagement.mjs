#!/usr/bin/env node
/**
 * Desk v1 (MC-1021 R1-W S8) — Conversations, Engagement and Retro against a fake
 * server, `desk_v1_live` ON, plus the flag-OFF contract.
 *
 * What is under test is which requests the browser makes and what it paints from
 * the answers. Every row, thread and finding below is one the fixtures do not
 * hold, so "the page painted the server's data" cannot be satisfied by demo data.
 *   1. Feed        -> opening Conversations GETs the engagement feed and each
 *                     project's coverage; rows are the server's, a coverage gap is
 *                     shown, the fixture conversations are not.
 *   2. Failure     -> a failed feed read says why (Try again) and paints no rows.
 *   3. Thread/writes -> opening a row reads it (the post it answers); Ignore, edit
 *                     the draft and Take over are PATCHes with an inverse on Undo; a
 *                     refused write rolls back and says why.
 *   4. Send        -> needs the retyped dashboard passcode (wrong one refused,
 *                     nothing sent), posts the draft text, the row becomes Sent; a
 *                     LinkedIn row has no send path and says so.
 *   5. Suggest     -> asking the agent POSTs suggest-reply and sends nothing.
 *   6. Dashboard   -> the Engagement page shows the same feed in lanes; Check now
 *                     POSTs a poll for ONE project and is disabled for "all".
 *   7. Retro       -> GET retro, Confirm / Reject POST the finding routes, Run retro
 *                     now POSTs the propose route, pasted per-post numbers POST
 *                     outcomes; no inverse is offered.
 *   8. Flag OFF    -> opening both surfaces makes 0 /api/desk/* requests.
 *
 * RUN   cd tools/smoke && node desk-v1-live-engagement.mjs
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
const PASSCODE = 'smoke-pass';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

const T0 = '2026-09-30T10:00:00Z';
function feedRows() {
  return [
    { id: 'eng-a1', platform: 'x', account: '@ron', project_id: 'clayrune', campaign_id: 'camp-1', source: 'our_posts', state: 'needs_reply',
      author: '@alice_live', excerpt: 'Does the Windows beta need admin rights?', created_at: T0, external_id: 'x-1', post_id: 'p-1',
      url: 'https://x.test/alice/1', draft: { text: 'No admin rights needed.', by: 'agent', at: T0 }, taken_over: false, assigned_to: null },
    { id: 'eng-a2', platform: 'x', account: '@ron', project_id: 'clayrune', campaign_id: 'camp-1', source: 'our_posts', state: 'needs_you',
      author: '@bob_live', excerpt: 'Any plan for a Linux build?', created_at: T0, external_id: 'x-2', post_id: 'p-1',
      url: 'https://x.test/bob/2', draft: null, taken_over: false, assigned_to: null },
    { id: 'eng-a3', platform: 'linkedin', account: 'Clayrune page', project_id: 'clayrune', campaign_id: 'camp-1', source: 'our_posts', state: 'needs_you',
      author: 'Carol Live', excerpt: 'Congrats on the launch.', created_at: T0, external_id: 'li-1', post_id: null,
      url: 'https://li.test/c/1', draft: { text: 'Thank you, Carol.', by: 'agent', at: T0 }, taken_over: false, assigned_to: null },
  ];
}

function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  const campaigns = fx.campaigns.map((c) => JSON.parse(JSON.stringify(c)));
  const srv = { log: [], next: {}, campaigns, fx, rows: feedRows(), refusedProof: [], findings: [], ledger: [], retro: null };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects, campaigns: campaigns.map((c) => JSON.parse(JSON.stringify(c))) });
  srv.coverage = (pid) => (pid === 'clayrune'
    ? [{ platform: 'linkedin', state: 'not_connected', label: 'LinkedIn', message: 'Not connected (LinkedIn is not read)', reason: 'LinkedIn has no read access' }]
    : []);
  // A closed term with one proposed finding and the numbers behind it.
  srv.ledger = [1, 2, 3].map((n) => ({ id: `led-${n}`, project_id: 'clayrune', campaign_id: 'camp-1', term: 1, platform: 'x', format: 'text',
    published_at: `2026-09-0${n}T09:00:00Z`, outcomes: [] }));
  srv.findings = [{ id: 'F-live-1', project_id: 'clayrune', dimension: 'format', state: 'proposed', origin: 'unattended', metric: 'clicks',
    arms: { a: 'video', b: 'text' }, effect: { direction: 'a>b', ratio: 2.4 }, n_total: 24, confidence: 'medium',
    evidence: [{ campaign_id: 'camp-1', term: 1, n_a: 12, n_b: 12 }], account: null }];
  srv.retroBody = () => ({
    campaign_id: 'camp-1', project_id: 'clayrune', term: 1, status: 'closed', metric: 'clicks', computed_at: T0,
    goal: { metric: 'tester signups', target: 30, actual: 21, baseline: 0 },
    spend: { publishing: 0.5, media_cost: null, total: 0.5, ceiling: 60, cost_per_outcome: 0.0238 },
    dimensions: [{ dimension: 'format', verdict: 'finding', arms: { a: 'video', b: 'text' }, effect: { direction: 'a>b', ratio: 2.4 }, n_total: 24, confidence: 'medium' },
      { dimension: 'slot', verdict: 'too_few_posts', text: 'Too few posts to tell (3 and 0; need 10 each)' }],
    summary: 'Judged on clicks per post. Term closed: 21 of 30 tester signups, $0.50 spent.',
    findings: srv.findings.filter((f) => f.state === 'proposed').map((f) => f.id),
    finding_rows: srv.findings.filter((f) => f.state === 'proposed'),
  });
  return srv;
}

async function newPage(browser, { live, srv }) {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();
  if (!live) await installDemoFixtures(page);   // demo mode is the harness's: the page ships no fixtures (S10)
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
      distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3, distiller_max_explorations_per_session: 3,
      distiller_min_turns: 5, distiller_skip_errors: true, roster: [],
    })));
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: live, user_timezone: '' });
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* no body */ }
    if (method === 'POST' && /\/reply$/.test(path) && !(body && body.passcode === PASSCODE)) {
      srv.refusedProof.push({ path, body });
      return J({ error: 'bad_passcode' }, 403);
    }
    srv.log.push({ method, path, search: url.search, body });
    const key = `${method} ${path}`;
    if (srv.stick && srv.stick[key]) return J({ error: srv.stick[key] }, 409);
    const refuse = Object.keys(srv.next).find((k) => key === k || key.startsWith(k));
    if (refuse) { const msg = srv.next[refuse]; delete srv.next[refuse]; return J({ error: msg }, 409); }
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/engagement' && method === 'GET') return J(srv.rows);
    let m = path.match(/^\/api\/desk\/engagement\/coverage\/([^/]+)$/);
    if (m) return J({ project_id: m[1], coverage: srv.coverage(m[1]) });
    if (path === '/api/desk/engagement/poll' && method === 'POST') {
      return J({ project_id: body.project_id, platforms: { x: { state: 'ok', new_items: 2, spent: 0.01, via: 'api' } } });
    }
    m = path.match(/^\/api\/desk\/engagement\/([^/]+)(\/reply|\/suggest-reply)?$/);
    if (m) {
      const row = srv.rows.find((r) => r.id === m[1]);
      if (!row) return J({ error: 'engagement item not found' }, 404);
      if (m[2] === '/reply' && method === 'POST') {
        row.state = 'sent'; row.draft = null; row.reply = { text: body.text, post_id: 'x-reply-1' };
        return J(row);
      }
      if (m[2] === '/suggest-reply' && method === 'POST') return J({ ok: true, session_id: 's-1' }, 202);
      if (method === 'GET') return J({ ...row, parent_post: row.post_id ? { id: row.post_id, body: 'The Windows beta is open: grab it here.', url: 'https://x.test/ron/p1', published_at: T0, platform: 'x', account: '@ron' } : null });
      if (method === 'PATCH') {
        if ('state' in body) row.state = body.state;
        if ('assigned_to' in body) row.assigned_to = body.assigned_to;
        if ('taken_over' in body) { row.taken_over = body.taken_over; if (body.taken_over && row.draft) { row.draft = null; if (row.state === 'needs_reply') row.state = 'needs_you'; } }
        if ('draft' in body) {
          row.draft = body.draft && body.draft.text ? { text: body.draft.text, by: 'human', at: new Date().toISOString() } : null;
          if (!('state' in body)) { if (row.draft && row.state === 'needs_you') row.state = 'needs_reply'; else if (!row.draft && row.state === 'needs_reply') row.state = 'needs_you'; }
        }
        return J(row);
      }
    }
    m = path.match(/^\/api\/desk\/campaigns\/([^/]+)\/retro$/);
    if (m) {
      if (method === 'POST') { srv.proposed = true; return J(srv.retroBody()); }
      return J(srv.retroBody());
    }
    if (path === '/api/desk/ledger' && method === 'GET') return J(srv.ledger);
    m = path.match(/^\/api\/desk\/ledger\/([^/]+)\/outcome$/);
    if (m && method === 'POST') {
      const row = srv.ledger.find((r) => r.id === m[1]);
      row.outcomes.push({ metric: body.metric, value: body.value, at: T0, source: body.source });
      return J(row);
    }
    m = path.match(/^\/api\/desk\/findings\/([^/]+)\/(confirm|reject|dont-suggest-again)$/);
    if (m && method === 'POST') {
      const f = srv.findings.find((x) => x.id === m[1]);
      f.state = m[2] === 'confirm' ? 'confirmed' : 'rejected';
      if (body.edited_text) f.edited_text = body.edited_text;
      return J(f);
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
const text = (page, sel) => page.textContent(sel).then((t) => (t || '').trim()).catch(() => '');
const realErrors = (pageErrors) => pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
const rowIds = (page, sel) => page.$$eval(sel, (els) => els.map((e) => e.dataset.convId || e.dataset.engConv));
const enterPasscode = async (page, code) => {
  await page.waitForSelector('[id^="hp-passcode-"]', { timeout: 8000 });
  await page.fill('[id^="hp-passcode-"]', code);
  await page.press('[id^="hp-passcode-"]', 'Enter');
};
const openConversations = async (page, srvReady = true) => {
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await page.evaluate(() => window.deskV1Nav('conversations', { campaignId: 'camp-1' }));
  if (srvReady) await settle(page, () => !!document.querySelector('.desk-v1-conv-layout'));
};
const clickToast = (page, label) => page.evaluate((l) => {
  const b = [...document.querySelectorAll('button')].filter((x) => x.textContent.trim() === l && x.offsetParent).pop();   // newest toast
  if (!b) return false; b.click(); return true;
}, label);

// ── 1: feed ────────────────────────────────────────────────────────────────
async function feed(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await openConversations(page);
  const ids = await rowIds(page, '[data-conv-id]');
  (JSON.stringify(ids.sort()) === JSON.stringify(['eng-a1', 'eng-a2', 'eng-a3']))
    ? ok(`the rows are the server's feed rows: ${JSON.stringify(ids)} (no fixture conv-N)`) : fail('rows: ' + JSON.stringify(ids));
  const feedGets = calls(srv, 'GET', /^\/api\/desk\/engagement$/);
  const covGets = calls(srv, 'GET', /^\/api\/desk\/engagement\/coverage\//).map((r) => r.path.split('/').pop()).sort();
  (feedGets.length >= 1 && JSON.stringify(covGets) === JSON.stringify(['clayrune', 'engulfing_scanner']))
    ? ok('one feed GET and one coverage GET per project') : fail('reads: ' + JSON.stringify({ feed: feedGets.length, covGets }));
  const gap = await text(page, '.desk-v1-conv-gaps');
  /Not connected \(LinkedIn is not read\)/.test(gap) ? ok(`the coverage gap is shown: "${gap}"`) : fail('coverage gap: ' + JSON.stringify(gap));
  const snippet = await text(page, '[data-conv-id="eng-a1"] .desk-v1-conv-snippet');
  snippet === 'Does the Windows beta need admin rights?' ? ok('a row shows the server excerpt') : fail('snippet: ' + snippet);
  const demoBanner = await page.evaluate(() => /Demo data/.test(document.body.innerText));
  !demoBanner ? ok('no demo banner in live mode') : fail('demo banner shown in live mode');
  realErrors(pageErrors).length ? realErrors(pageErrors).forEach((e) => fail('page error: ' + e)) : ok('no uncaught page errors');
  await ctx.close();
}

// ── 2: failure ─────────────────────────────────────────────────────────────
async function failure(browser) {
  const srv = makeServer();
  srv.stick = { 'GET /api/desk/engagement': 'the feed is unavailable' };   // sticky: Home's count reads the feed first
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await page.evaluate(() => window.deskV1Nav('conversations', { campaignId: 'camp-1' }));
  await settle(page, () => !!document.querySelector('[data-eng-error]'));
  await page.waitForTimeout(400);
  const feedReads = calls(srv, 'GET', /^\/api\/desk\/engagement$/).length;
  feedReads <= 2 ? ok(`a failing feed is not re-read in a loop (${feedReads} reads)`) : fail('feed re-read ' + feedReads + ' times');
  const err = await text(page, '[data-eng-error]');
  const rows = await page.$$('[data-conv-id]');
  (/Could not load conversations: the feed is unavailable/.test(err) && rows.length === 0)
    ? ok(`a failed feed read says why and paints no rows: "${err.replace(/\s+/g, ' ')}"`) : fail('error state: ' + JSON.stringify({ err, rows: rows.length }));
  srv.stick = {};
  await page.click('[data-eng-retry]');
  await settle(page, () => !!document.querySelector('[data-conv-id="eng-a1"]'));
  ok('Try again reads the feed again and paints the rows');
  await ctx.close();
}

// ── 3: thread + writes ─────────────────────────────────────────────────────
async function threadAndWrites(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await openConversations(page);
  srv.log.length = 0;
  await page.click('[data-conv-id="eng-a1"]');
  await settle(page, () => /The Windows beta is open/.test(document.querySelector('.desk-v1-conv-thread')?.innerText || ''));
  calls(srv, 'GET', /^\/api\/desk\/engagement\/eng-a1$/).length === 1
    ? ok('opening a row reads it, and the thread shows the post it answers') : fail('thread GET: ' + JSON.stringify(srv.log));
  const reply = await text(page, '[data-conv-reply-text]');
  reply === 'No admin rights needed.' ? ok('the saved draft is the proposed reply') : fail('reply text: ' + reply);

  // Edit the draft: PATCH {draft}.
  srv.log.length = 0;
  await page.click('[data-conv-reply-text]');
  await page.fill('.desk-v1-conv-reply-editor', 'No admin rights, it installs per user.');
  await page.press('.desk-v1-conv-reply-editor', 'Enter');
  await settle(page, () => document.querySelector('[data-conv-reply-text]')?.textContent.trim() === 'No admin rights, it installs per user.');
  const p1 = calls(srv, 'PATCH', /^\/api\/desk\/engagement\/eng-a1$/);
  (p1.length === 1 && p1[0].body.draft && p1[0].body.draft.text === 'No admin rights, it installs per user.')
    ? ok('an edited reply is one PATCH {draft:{text}} and the thread shows the server row') : fail('draft PATCH: ' + JSON.stringify(p1));

  // Ignore, then Undo -> inverse PATCH with the prior state.
  srv.log.length = 0;
  await page.click('[data-conv-ignore]');
  await settle(page, () => document.querySelector('[data-conv-id="eng-a1"] .desk-v1-conv-reason')?.textContent.includes('Ignored'));
  const ign = calls(srv, 'PATCH', /^\/api\/desk\/engagement\/eng-a1$/);
  (ign.length === 1 && ign[0].body.state === 'ignored') ? ok('Ignore is PATCH {state:"ignored"} and the row reads Ignored') : fail('ignore PATCH: ' + JSON.stringify(ign));
  await page.click('#desk-v1-undo'); // quiet Undo (Ron 2026-10-01): Ignore is routine, no toast
  await settle(page, () => !document.querySelector('[data-conv-id="eng-a1"] .desk-v1-conv-reason')?.textContent.includes('Ignored'));
  const undone = calls(srv, 'PATCH', /^\/api\/desk\/engagement\/eng-a1$/);
  (undone.length === 2 && undone[1].body.state === 'needs_reply')
    ? ok('Undo sends the inverse PATCH {state:"needs_reply"} (the state it had)') : fail('undo PATCH: ' + JSON.stringify(undone.map((r) => r.body)));

  // A refused write rolls back and says why.
  srv.next['PATCH /api/desk/engagement/eng-a1'] = 'this reply was already sent';
  await page.click('[data-conv-ignore]');
  await settle(page, () => /was not saved: this reply was already sent/.test(document.body.innerText));
  const stillThere = await text(page, '[data-conv-id="eng-a1"] .desk-v1-conv-reason');
  !/Ignored/.test(stillThere) ? ok('a refused Ignore leaves the row as it was and says why') : fail('row after refusal: ' + stillThere);

  // Take over drops the draft server-side; the page shows what the server returned.
  srv.log.length = 0;
  await page.click('[data-conv-takeover]');
  await settle(page, () => !!document.querySelector('.desk-v1-conv-takeover-banner'));
  const tk = calls(srv, 'PATCH', /^\/api\/desk\/engagement\/eng-a1$/);
  (tk.length === 1 && tk[0].body.taken_over === true) ? ok('Take over is PATCH {taken_over:true} and the banner shows') : fail('take over PATCH: ' + JSON.stringify(tk));
  const noDraft = await page.$('[data-conv-reply-text]');
  !noDraft ? ok("the queued reply is gone (the server's row has no draft)") : fail('the draft is still shown after take over');
  await ctx.close();
}

// ── 4: send ────────────────────────────────────────────────────────────────
async function send(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await openConversations(page);
  await page.click('[data-conv-id="eng-a1"]');
  await settle(page, () => !!document.querySelector('[data-conv-send]:not([disabled])'));
  srv.log.length = 0;
  await page.click('[data-conv-send]');
  await enterPasscode(page, 'not-the-passcode');
  await settle(page, () => /Wrong dashboard passcode/.test(document.body.innerText));
  (srv.refusedProof.length === 1 && srv.rows[0].state === 'needs_reply')
    ? ok('a wrong passcode is refused by the server and nothing is sent') : fail('after wrong passcode: ' + JSON.stringify({ r: srv.refusedProof.length, s: srv.rows[0].state }));
  await enterPasscode(page, PASSCODE);
  await settle(page, () => /Sent/.test(document.querySelector('[data-conv-id="eng-a1"] .desk-v1-conv-reason')?.textContent || ''));
  const post = calls(srv, 'POST', /^\/api\/desk\/engagement\/eng-a1\/reply$/);
  (post.length === 1 && post[0].body.text === 'No admin rights needed.' && post[0].body.passcode === PASSCODE)
    ? ok('the right passcode sends the draft text in one POST and the row reads Sent') : fail('reply POST: ' + JSON.stringify(post));
  // Quiet Undo (Ron 2026-10-01): the header always has an Undo button, so test the toast and what the button would undo.
  const noUndo = await page.evaluate(() => !/Undo/.test([...document.querySelectorAll('.toast')].map((t) => t.innerText).join(' ')) && !/sent|reply/i.test(document.getElementById('desk-v1-undo').title.replace(/^Undo: Edited.*/i, '')));
  noUndo ? ok('a sent reply offers no Undo (it cannot be unsent)') : fail('Undo offered on a sent reply');
  await page.waitForSelector('[data-conv-send][disabled]', { timeout: 3000 }).then(() => ok('Send is disabled once sent')).catch(() => fail('Send still enabled after sent'));

  // LinkedIn: no send path from here, said before the click.
  await page.click('[data-conv-id="eng-a3"]');
  await settle(page, () => !!document.querySelector('.desk-v1-conv-thread [data-conv-send]'));
  const title = await page.getAttribute('[data-conv-send]', 'title');
  const dis = await page.$('[data-conv-send][disabled]');
  (dis && /only be sent to X/.test(title || '')) ? ok(`a LinkedIn row's Send is disabled: "${title}"`) : fail('linkedin send: ' + JSON.stringify({ dis: !!dis, title }));
  await ctx.close();
}

// ── 5: suggest-reply ───────────────────────────────────────────────────────
async function suggest(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await openConversations(page);
  await page.click('[data-conv-id="eng-a2"]');
  await settle(page, () => !!document.querySelector('[data-conv-revise]:not([disabled])'));
  const label = await text(page, '[data-conv-revise]');
  /to draft$/.test(label) ? ok(`with no draft the button asks for one: "${label}"`) : fail('revise label: ' + label);
  srv.log.length = 0;
  await page.click('[data-conv-revise]');
  await settle(page, () => /is drafting\. Nothing is sent/.test(document.body.innerText));
  const sug = calls(srv, 'POST', /\/suggest-reply$/);
  const sends = calls(srv, 'POST', /\/reply$/);
  (sug.length === 1 && sends.length === 0) ? ok('asking POSTs suggest-reply and sends nothing') : fail('suggest: ' + JSON.stringify(srv.log));
  await ctx.close();
}

// ── 6: dashboard ───────────────────────────────────────────────────────────
async function dashboard(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await page.evaluate(() => window.deskV1Nav('engagement', {}));
  await settle(page, () => !!document.querySelector('.desk-v1-engagement'));
  const incoming = await rowIds(page, '[data-eng-conv]');
  JSON.stringify(incoming.sort()) === JSON.stringify(['eng-a2', 'eng-a3']) ? ok(`the Incoming lane lists the server's needs_you rows: ${JSON.stringify(incoming)}`) : fail('incoming: ' + JSON.stringify(incoming));
  await page.click('[data-eng-lane="suggested"]');
  const sugg = await rowIds(page, '[data-eng-conv]');
  JSON.stringify(sugg) === JSON.stringify(['eng-a1']) ? ok('the Suggested lane lists eng-a1') : fail('suggested: ' + JSON.stringify(sugg));
  const gap = await text(page, '.desk-v1-eng-gaps');
  /Clayrune: Not connected \(LinkedIn is not read\)/.test(gap) ? ok(`what is not being read is listed under the rows: "${gap}"`) : fail('gaps: ' + JSON.stringify(gap));

  const disabledAll = await page.$('[data-eng-check][disabled]');
  disabledAll ? ok('Check now is disabled while "All projects" is in scope') : fail('Check now enabled for all projects');
  await page.selectOption('[data-eng-filter="project"]', 'clayrune');
  srv.log.length = 0;
  await page.click('[data-eng-check]');
  await settle(page, () => /x: 2 new, spent \$0\.010/.test(document.querySelector('[data-eng-check-line]')?.textContent || ''));
  const poll = calls(srv, 'POST', /^\/api\/desk\/engagement\/poll$/);
  (poll.length === 1 && poll[0].body.project_id === 'clayrune') ? ok('Check now POSTs one poll for the project in scope and reports what it read and spent') : fail('poll: ' + JSON.stringify(poll));
  calls(srv, 'GET', /^\/api\/desk\/engagement$/).length >= 1 ? ok('the feed is read again after the poll') : fail('no feed re-read after the poll');
  await ctx.close();
}

// ── 7: retro ───────────────────────────────────────────────────────────────
async function retro(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await settle(page, () => !!document.querySelector('.desk-v1-campaign'));
  await page.evaluate(() => window.deskV1Nav('results', { campaignId: 'camp-1' }));
  await settle(page, () => !!document.querySelector('[data-retro-section] .desk-v1-retro-goal'));
  const goal = await text(page, '.desk-v1-retro-goal');
  /^21 of 30 tester signups/.test(goal) ? ok(`the retro is the server's: "${goal}"`) : fail('retro goal: ' + goal);
  calls(srv, 'GET', /^\/api\/desk\/campaigns\/camp-1\/retro$/).length >= 1 && calls(srv, 'GET', /^\/api\/desk\/ledger$/).length >= 1
    ? ok('a closed retro reads the retro and the ledger rows for its grid') : fail('reads: ' + JSON.stringify(srv.log.map((r) => r.method + ' ' + r.path)));
  const rowsInGrid = await page.$$('.desk-v1-retro-grid-row');
  rowsInGrid.length === 3 ? ok('the grid has the 3 ledger rows') : fail('grid rows: ' + rowsInGrid.length);
  const card = await text(page, '[data-finding-id="F-live-1"] [data-finding-text]');
  /video got 2\.4× the clicks per post of text/.test(card) ? ok(`the proposed finding is the server's: "${card}"`) : fail('finding: ' + card);

  // Pasted numbers -> one outcome POST each, then the retro is read again.
  srv.log.length = 0;
  await page.fill('[data-retro-paste]', '5, 6, 7');
  await page.click('[data-retro-paste-fill]');
  await settle(page, () => /^5$/.test(document.querySelector('.desk-v1-retro-grid-value')?.textContent.trim() || ''));
  const outs = calls(srv, 'POST', /^\/api\/desk\/ledger\/led-\d\/outcome$/);
  (outs.length === 3 && outs.map((o) => o.body.value).join() === '5,6,7' && outs.every((o) => o.body.metric === 'clicks'))
    ? ok('three pasted numbers are three outcome POSTs, and the grid shows them') : fail('outcome POSTs: ' + JSON.stringify(outs.map((o) => o.body)));
  calls(srv, 'GET', /\/retro$/).length >= 1 ? ok('the retro is read again afterwards') : fail('no retro re-read');
  await settle(page, () => !/Undo/.test(document.body.innerText) || true);

  // Run retro now: the POST that proposes. Never a GET.
  srv.log.length = 0;
  await page.click('[data-retro-propose]');
  await settle(page, () => /Retro run: 1 finding to confirm/.test(document.body.innerText));
  calls(srv, 'POST', /^\/api\/desk\/campaigns\/camp-1\/retro$/).length === 1 ? ok('Run retro now is one POST to the retro route') : fail('propose: ' + JSON.stringify(srv.log));

  // Confirm with edited wording, from the card.
  srv.log.length = 0;
  await page.click('[data-finding-edit]');
  await page.fill('[data-finding-editarea]', 'Video wins on clicks.');
  await page.click('[data-finding-edit-save]');
  await settle(page, () => !document.querySelector('[data-finding-id="F-live-1"]'));
  const conf = calls(srv, 'POST', /^\/api\/desk\/findings\/F-live-1\/confirm$/);
  (conf.length === 1 && conf[0].body.edited_text === 'Video wins on clicks.' && !('passcode' in conf[0].body))
    ? ok('Save & Confirm is one POST /confirm carrying the edited wording; the card leaves the list') : fail('confirm: ' + JSON.stringify(conf));
  const noUndo = await page.evaluate(() => !/Undo/.test([...document.querySelectorAll('.toast')].map((t) => t.innerText).join(' ')) && !/confirm|finding/i.test(document.getElementById('desk-v1-undo').title));
  noUndo ? ok('a live finding decision offers no Undo (the route has no inverse)') : fail('Undo offered on a live decision');
  await ctx.close();

  // A refused decision leaves the card and says why.
  const srv2 = makeServer();
  const second = await newPage(browser, { live: true, srv: srv2 });
  await settle(second.page, () => window.DeskV1Store.state().campaigns.length > 0);
  await second.page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await settle(second.page, () => !!document.querySelector('.desk-v1-campaign'));
  await second.page.evaluate(() => window.deskV1Nav('results', { campaignId: 'camp-1' }));
  await settle(second.page, () => !!document.querySelector('[data-finding-id="F-live-1"]'));
  srv2.next['POST /api/desk/findings/F-live-1/reject'] = 'this action needs a human';
  await second.page.click('[data-finding-reject]');
  await settle(second.page, () => /was not saved: this action needs a human/.test(document.body.innerText));
  (await second.page.$('[data-finding-id="F-live-1"]')) ? ok('a refused Reject leaves the finding on the card and says why') : fail('card vanished after a refusal');
  await second.ctx.close();
}

// ── 8: flag off ────────────────────────────────────────────────────────────
async function flagOff(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: false, srv });
  await settle(page, () => !!window.DeskV1Fixtures);
  await page.evaluate(() => window.deskV1Nav('conversations', { campaignId: 'camp-1' }));
  await settle(page, () => !!document.querySelector('.desk-v1-conv-layout'));
  await page.click('.desk-v1-conv-row');
  await page.click('[data-conv-ignore]').catch(() => {});
  await page.evaluate(() => window.deskV1Nav('engagement', {}));
  await settle(page, () => !!document.querySelector('.desk-v1-engagement'));
  const noCheck = await page.$('[data-eng-check]');
  const reqs = srv.log.filter((r) => r.path.startsWith('/api/desk/'));
  (reqs.length === 0 && !noCheck) ? ok('flag OFF: conversations + engagement make 0 /api/desk/* requests and show no Check now') : fail('flag-off requests: ' + JSON.stringify(reqs.map((r) => r.method + ' ' + r.path)));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  console.log('live ON: feed'); await feed(browser);
  console.log('live ON: failure'); await failure(browser);
  console.log('live ON: thread + writes'); await threadAndWrites(browser);
  console.log('live ON: send'); await send(browser);
  console.log('live ON: suggest'); await suggest(browser);
  console.log('live ON: dashboard'); await dashboard(browser);
  console.log('live ON: retro'); await retro(browser);
  console.log('live OFF'); await flagOff(browser);
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed.` : '\nAll checks passed.');
process.exit(bad ? 1 : 0);
