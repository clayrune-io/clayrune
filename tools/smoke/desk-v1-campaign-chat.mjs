#!/usr/bin/env node
/**
 * Desk campaign page (Ron 2026-10-06): the agent box is a CONVERSATION, not the
 * dead "Not connected to the agent yet" line. A fake server stands in for
 * /api/desk/* and the model call (mc/desk_campaign_chat.py is pinned by
 * tests/test_desk_campaign_chat.py); what is under test is what the browser sends
 * and what it paints.
 *
 *   1. Mount    -> a campaign with a project and an agent shows an empty thread and
 *                  an input, and never the "Not connected" line.
 *   2. Question -> Send POSTs {message}; both turns appear; the box empties; the status
 *                  line says nothing was changed; Shift+Enter is a newline, Enter sends.
 *   3. Suggest  -> a reply the server saved as suggestions carries a chip written from
 *                  the SERVER's summary, and the page's own copy takes the suggestions.
 *   4. Scope    -> with a card selected the POST carries {scope:{kind:'card', id, label}}.
 *   5. Refused  -> the box empties the moment Send is pressed (before the answer); a failed
 *                  call puts the text back with the error and adds no turn.
 *   6. Reload   -> a fresh page opens the campaign and the thread is back.
 *   7. Suggest button (How stop) -> goes to the chat as a message, never "Not connected".
 *   8. No agent -> a campaign with no agent anywhere shows the head and a disabled input,
 *                  and POSTs nothing.
 *   9. Chrome   -> (shared desk-v1-chat-chrome.js) A-/A+ scale the thread, the status line and the
 *                  box, and the size survives a reload; pop-out lifts the SAME chat element, a send
 *                  from it lands in the docked thread, Esc closes it; at 1440 and 390 no sideways scroll.
 *  10. Demo     -> flag OFF keeps the old box and makes 0 /api/desk/* requests.
 *
 * RUN   cd tools/smoke && node desk-v1-campaign-chat.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures, installDemoFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const STATIC = loadStaticJsCss(REPO_ROOT);

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

let GID = 0;

function makeServer({ agent = 'global:claydo' } = {}) {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: p.id === 'clayrune' ? agent : null, state: 'active' } }));
  const campaigns = fx.campaigns.map((c) => JSON.parse(JSON.stringify(c)));
  const srv = { log: [], threads: {}, campaigns, fx, reply: null };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects, campaigns: campaigns.map((c) => JSON.parse(JSON.stringify(c))) });
  return srv;
}

async function newPage(browser, { live = true, srv, viewport = { width: 1400, height: 950 } }) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  if (!live) await installDemoFixtures(page);
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
    if (path === '/api/characters') return J([{ name: 'claydo', scope: 'global', agent_name: 'Claydo', avatar: '' }]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* no body */ }
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    const m = path.match(/^\/api\/desk\/campaigns\/([^/]+)\/chat$/);
    if (m) {
      const id = m[1];
      if (method === 'GET') return J({ thread: (srv.threads[id] || []).map((t) => ({ ...t })) });
      if (srv.gate) await srv.gate;
      if (!srv.reply) return J({ error: 'no fake answer set' }, 500);
      const r = srv.reply(body);
      if (r.status) return J({ error: r.error }, r.status);
      const mk = (role, text, extra) => ({ id: 'tn' + (++GID), role, text, at: '2026-10-06T10:00:00Z', scope: body.scope || null, agent: role === 'agent' ? 'Claydo' : null, suggested: null, suggest_error: null, ...extra });
      const turns = [mk('user', body.message), mk('agent', r.reply, r.suggested ? { suggested: r.suggested } : {})];
      (srv.threads[id] = srv.threads[id] || []).push(...turns);
      const out = { agent: { ref: 'global:claydo', name: 'Claydo' }, provider: 'claude', model: 'sonnet', turns };
      if (r.campaign) out.campaign = r.campaign;
      return J(out);
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
const posts = (srv, id) => srv.log.filter((r) => r.method === 'POST' && r.path === `/api/desk/campaigns/${id}/chat`);
const openCampaign = async (page, id, panel = 'what') => {
  await page.evaluate((cid) => window.deskV1Nav('campaign', { campaignId: cid }), id);
  await settle(page, () => !!document.querySelector('.desk-v1-campaign'));
  await page.evaluate(([cid, p]) => window.deskV1GotoCampaignPanel(p, { campaignId: cid }), [id, panel]);
  await settle(page, () => !!document.querySelector('[data-camp-chat], [data-no-agent]'));
};
const turns = (page) => page.$$eval('[data-chat-turn]:not([data-chat-pending]):not([data-chat-thinking])', (n) => n.map((e) => ({ role: e.dataset.chatTurn, text: e.querySelector('.desk-v1-chat-text').textContent })));
const status = (page) => page.$eval('[data-camp-chat-status]', (e) => e.textContent);
const realErrors = (errs) => errs.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));

async function conversation(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openCampaign(page, 'camp-1');

  // 1. Mount
  (await page.$('[data-posy-not-connected]')) === null && /not connected/i.test(await page.evaluate(() => document.querySelector('.desk-v1-camp-posy').innerText)) === false
    ? ok('the box is not the "Not connected to the agent" line') : fail('the dead line is still on the page');
  (await page.$('[data-chat-empty]')) ? ok('an empty thread says what to ask') : fail('no empty-thread hint');
  const agentName = await page.$eval('.desk-v1-camp-posy .desk-thread-name', (e) => e.textContent.trim()).catch(() => '');
  agentName === 'Claydo' ? ok('the head names the campaign\'s agent (Claydo)') : fail('agent name: ' + agentName);
  srv.log.some((r) => r.method === 'GET' && r.path === '/api/desk/campaigns/camp-1/chat') ? ok('the saved thread is read on open') : fail('no GET of the thread');

  // 2. Question
  srv.reply = () => ({ reply: 'Post 2 failed because the engine refused the prompt. Shorten it and retry.' });
  await page.fill('#desk-v1-camp-posy-input', 'line one');
  await page.press('#desk-v1-camp-posy-input', 'Shift+Enter');
  const multi = await page.$eval('#desk-v1-camp-posy-input', (e) => e.value);
  multi === 'line one\n' || multi === 'line one\r\n' ? ok('Shift+Enter is a newline and sends nothing') : fail('shift+enter: ' + JSON.stringify(multi));
  posts(srv, 'camp-1').length === 0 || fail('Shift+Enter sent');
  await page.fill('#desk-v1-camp-posy-input', 'why did post 2 fail?');
  await page.press('#desk-v1-camp-posy-input', 'Enter');
  await settle(page, () => document.querySelectorAll('[data-chat-turn="agent"]:not([data-chat-thinking])').length === 1);
  const p1 = posts(srv, 'camp-1');
  (p1.length === 1 && p1[0].body.message === 'why did post 2 fail?' && !('scope' in p1[0].body))
    ? ok('Enter POSTs {message} for the whole campaign') : fail('POST: ' + JSON.stringify(p1.map((r) => r.body)));
  const t = await turns(page);
  (t.length === 2 && t[0].role === 'user' && t[0].text === 'why did post 2 fail?' && /engine refused/.test(t[1].text))
    ? ok('both turns are on screen, the agent\'s answer as prose') : fail('turns: ' + JSON.stringify(t));
  (await page.$eval('#desk-v1-camp-posy-input', (e) => e.value)) === '' ? ok('the box is empty after a good send') : fail('box not cleared');
  /Claydo answered\. Nothing was changed\./.test(await status(page)) ? ok('the status line says nothing was changed') : fail('status: ' + await status(page));

  // 3. Suggested
  const suggested = { summary: 'Claydo suggested 2 pieces and a time. Accept them on What and When.' };
  srv.reply = () => ({
    reply: 'Two ideas: a behind-the-scenes post and a Thursday teaser.', suggested,
    campaign: { how: { suggested: { what: [{ title: 'Behind the scenes' }, { title: 'Thursday teaser' }] } }, when: { slots: [] }, suggestBlocker: null },
  });
  await page.fill('#desk-v1-camp-posy-input', 'give me two ideas');
  await page.press('#desk-v1-camp-posy-input', 'Enter');
  await settle(page, () => document.querySelector('[data-chat-suggested]'));
  (await page.$eval('[data-chat-suggested]', (e) => e.textContent)) === suggested.summary ? ok("the chip is the server's own summary") : fail('chip text');
  (await status(page)) === suggested.summary ? ok("the status line is the server's summary too") : fail('status after suggest: ' + await status(page));
  const sug = await page.evaluate(() => window.DeskV1Store.state().campaigns.find((c) => c.id === 'camp-1').how.suggested.what.map((w) => w.title));
  sug.join('|') === 'Behind the scenes|Thursday teaser' ? ok('the page\'s campaign takes the saved suggestions') : fail('suggested: ' + JSON.stringify(sug));

  // 4. Scope
  srv.reply = () => ({ reply: 'That card reads fine.' });
  await page.evaluate(() => window.deskV1GotoCampaignPanel('what', { campaignId: 'camp-1' }));
  await page.evaluate(() => window.deskV1GotoCampaignPanel('what', { campaignId: 'camp-1' }));
  await settle(page, () => !!document.querySelector('[data-what-row]'));
  const fam = await page.$eval('[data-what-row]', (e) => e.dataset.familyId);
  await page.click('[data-what-row]');
  await settle(page, () => /About: /.test(document.querySelector('[data-scope-trigger]').textContent) && !!document.querySelector('[data-camp-chat]'));
  await page.fill('#desk-v1-camp-posy-input', 'is this card ok?');
  await page.press('#desk-v1-camp-posy-input', 'Enter');
  await settle(page, () => document.querySelectorAll('[data-chat-turn="agent"]:not([data-chat-thinking])').length === 3);
  const last = posts(srv, 'camp-1').at(-1).body;
  (last.scope && last.scope.kind === 'card' && last.scope.id === fam && last.scope.label)
    ? ok('with a card selected the POST names it: ' + JSON.stringify(last.scope)) : fail('scope: ' + JSON.stringify(last));
  (await turns(page)).length === 6 ? ok('the thread survived the box repaint on selection (6 turns)') : fail('turns after selection: ' + (await turns(page)).length);

  // 5. Refused
  const before = (await turns(page)).length;
  let release; srv.gate = new Promise((r) => { release = r; });
  srv.reply = () => ({ status: 404, error: 'the agent this campaign picked no longer exists' });
  await page.fill('#desk-v1-camp-posy-input', 'will this fail?');
  await page.press('#desk-v1-camp-posy-input', 'Enter');
  await settle(page, () => !!document.querySelector('[data-chat-thinking]'));
  (await page.$eval('#desk-v1-camp-posy-input', (e) => e.value)) === '' ? ok('the box is empty while the answer is still pending') : fail('box not cleared on send');
  (await page.$eval('[data-chat-pending] .desk-v1-chat-text', (e) => e.textContent)) === 'will this fail?' ? ok('the pending "You" turn shows the message') : fail('no pending turn');
  release(); srv.gate = null;
  await settle(page, () => /back in the box/.test(document.querySelector('[data-camp-chat-status]').textContent));
  const st5 = await status(page);
  (/no longer exists/.test(st5) && /Nothing was changed/.test(st5)) ? ok("a failed send shows the server's reason and says nothing changed") : fail('status: ' + st5);
  (await page.$eval('#desk-v1-camp-posy-input', (e) => e.value)) === 'will this fail?' ? ok('the text is back in the box') : fail('typed text lost');
  (await turns(page)).length === before ? ok('no turn was added') : fail('a turn was added by a failed send');

  const err = realErrors(pageErrors);
  err.length ? err.forEach((e) => fail('page error: ' + e)) : ok('no uncaught page errors');
  const saved = srv.threads['camp-1'].map((x) => x.text);
  await ctx.close();
  return { srv, saved };
}

async function reload(browser, prior) {
  const srv = makeServer();
  srv.threads['camp-1'] = prior.srv.threads['camp-1'];
  const { ctx, page } = await newPage(browser, { srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openCampaign(page, 'camp-1');
  await settle(page, () => document.querySelectorAll('[data-chat-turn]').length > 0);
  const t = await turns(page);
  (t.length === prior.saved.length && t.every((x, i) => x.text === prior.saved[i]))
    ? ok(`after a fresh page the ${t.length} saved turns are back`) : fail('reloaded turns: ' + JSON.stringify(t));
  await ctx.close();
}

async function suggestButton(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  srv.reply = (b) => ({ reply: 'Here is what I would do.' });
  await openCampaign(page, 'camp-1', 'how');
  await settle(page, () => !!document.querySelector('[data-how-suggest]'));
  await page.click('[data-how-suggest]');
  await settle(page, () => document.querySelectorAll('[data-chat-turn="agent"]:not([data-chat-thinking])').length === 1);
  const p = posts(srv, 'camp-1');
  (p.length === 1 && p[0].body.message === 'Suggest What / When / Where') ? ok('the Suggest button sends its line to the agent as a message') : fail('suggest POST: ' + JSON.stringify(p.map((r) => r.body)));
  (await page.evaluate(() => /not connected/i.test(document.body.innerText))) ? fail('"not connected" is on screen after Suggest') : ok('nothing says "not connected" after Suggest');
  await ctx.close();
}

async function noAgent(browser) {
  const srv = makeServer({ agent: null });
  const { ctx, page } = await newPage(browser, { srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openCampaign(page, 'camp-1');
  const disabled = await page.$eval('#desk-v1-camp-posy-input', (e) => e.disabled);
  disabled ? ok('with no agent anywhere the input is disabled') : fail('input enabled with no agent');
  await page.$('[data-goto-campaign-agent]') ? ok('the head still links to the agent picker on Brief') : fail('no link to the picker');
  posts(srv, 'camp-1').length === 0 ? ok('nothing was POSTed') : fail('POST without an agent');
  await ctx.close();
}

const fontPx = (page, sel) => page.$eval(sel, (e) => parseFloat(getComputedStyle(e).fontSize));
const hOverflow = (page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
const popState = (page) => page.evaluate(() => {
  const el = document.querySelector('[data-camp-chat]'); const r = el.getBoundingClientRect();
  const ae = document.activeElement;
  return {
    same: el === window.__chat, popped: el.classList.contains('is-popped'), open: el.matches(':popover-open'),
    l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, vw: innerWidth, vh: innerHeight,
    threads: document.querySelectorAll('[data-chat-thread]').length,
    focus: ae && (ae.matches('[data-chat-pop]') ? 'pop' : ae.matches('#desk-v1-camp-posy-input') ? 'ask' : ae.tagName),
  };
});

async function chrome(browser) {
  const srv = makeServer();
  srv.reply = () => ({ reply: 'A reply long enough to have a size: the first post should lead with the benefit.' });
  const { ctx, page, pageErrors } = await newPage(browser, { srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openCampaign(page, 'camp-1');
  await page.fill('#desk-v1-camp-posy-input', 'first question');
  await page.press('#desk-v1-camp-posy-input', 'Enter');
  await settle(page, () => document.querySelectorAll('[data-chat-turn="agent"]:not([data-chat-thinking])').length === 1);
  (await page.$('[data-camp-chat] [data-chat-chrome][data-wired]')) ? ok('the shared A-/A+/pop-out strip is in the box and wired') : fail('no chrome strip');

  const TXT = '[data-chat-turn="agent"] .desk-v1-chat-text';
  const t0 = await fontPx(page, TXT), a0 = await fontPx(page, '#desk-v1-camp-posy-input'), s0 = await fontPx(page, '[data-camp-chat-status]');
  await page.click('[data-chat-text-more]');
  await page.click('[data-chat-text-more]');
  const t1 = await fontPx(page, TXT), a1 = await fontPx(page, '#desk-v1-camp-posy-input'), s1 = await fontPx(page, '[data-camp-chat-status]');
  (t1 > t0 * 1.25 && a1 > a0 * 1.25 && s1 > s0 * 1.25 && (await page.textContent('[data-chat-text-read]')) === '130%')
    ? ok(`A+ twice scales the thread ${t0}->${t1}px, the box ${a0}->${a1}px and the status line ${s0}->${s1}px (130%)`) : fail('scale: ' + JSON.stringify({ t0, t1, a0, a1, s0, s1 }));
  await page.waitForTimeout(300);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openCampaign(page, 'camp-1');
  await settle(page, () => document.querySelectorAll('[data-chat-turn]').length > 0);
  const back = await fontPx(page, TXT);
  ((await page.textContent('[data-chat-text-read]')) === '130%' && back === t1) ? ok(`after a reload the size is still 130% (${back}px)`) : fail('persist: ' + back);

  // pop out
  await page.evaluate(() => { window.__chat = document.querySelector('[data-camp-chat]'); });
  const n0 = (await turns(page)).length;
  await page.click('[data-chat-pop]');
  let ps = await popState(page);
  (ps.same && ps.popped && ps.open && ps.w >= 800 && ps.l >= 0 && ps.r <= ps.vw && ps.b <= ps.vh && ps.threads === 1 && ps.focus === 'ask')
    ? ok(`the large view is the same chat element (one thread), ${Math.round(ps.w)}px wide, focus in the box`) : fail('pop: ' + JSON.stringify(ps));
  (await hOverflow(page)) <= 1 ? ok('no sideways scroll with the large view open (1440)') : fail('overflow at 1440 popped');
  await page.fill('#desk-v1-camp-posy-input', 'sent from the large view');
  await page.press('#desk-v1-camp-posy-input', 'Enter');
  await settle(page, ([n]) => !document.querySelector('[data-chat-thinking]') && document.querySelectorAll('[data-chat-turn]').length === n + 2, [n0]);
  await page.keyboard.press('Escape');
  ps = await popState(page);
  const docked = await turns(page);
  (!ps.popped && ps.same && ps.focus === 'pop' && docked.length === n0 + 2 && docked.slice(-2)[0].text === 'sent from the large view')
    ? ok('after Esc the docked thread already has the exchange sent from the large view; focus is back on the button') : fail('docked: ' + JSON.stringify({ ps, docked }));
  posts(srv, 'camp-1').filter((r) => r.body.message === 'sent from the large view').length === 1 ? ok('the popped send POSTed once') : fail('popped send count');
  (await hOverflow(page)) <= 1 ? ok('no sideways scroll docked (1440)') : fail('overflow at 1440');
  const err = realErrors(pageErrors);
  err.length ? err.forEach((e) => fail('page error: ' + e)) : ok('no uncaught page errors');
  await ctx.close();

  // phone width
  const m = await newPage(browser, { srv, viewport: { width: 390, height: 844 } });
  await settle(m.page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openCampaign(m.page, 'camp-1');
  await settle(m.page, () => document.querySelectorAll('[data-chat-turn]').length > 0);
  (await hOverflow(m.page)) <= 1 ? ok('no sideways scroll docked at 390') : fail('overflow at 390: ' + await hOverflow(m.page));
  const bar = await m.page.$eval('[data-chat-chrome]', (e) => { const r = e.getBoundingClientRect(); return { r: r.right, w: innerWidth, h: Math.min(...[...e.querySelectorAll('button')].map((b) => b.getBoundingClientRect().height)) }; });
  (bar.r <= bar.w && bar.h >= 32) ? ok('the strip fits at 390 with 32px+ targets') : fail('strip at 390: ' + JSON.stringify(bar));
  await m.page.evaluate(() => { window.__chat = document.querySelector('[data-camp-chat]'); });
  await m.page.click('[data-chat-pop]');
  const mp = await popState(m.page);
  (mp.same && mp.popped && mp.open && mp.l >= 0 && mp.r <= mp.vw + 0.5 && mp.t >= 0 && mp.b <= mp.vh + 0.5 && (await hOverflow(m.page)) <= 1)
    ? ok(`at 390 the large view fills the screen (${Math.round(mp.w)}px) with no sideways scroll`) : fail('popped at 390: ' + JSON.stringify(mp));
  await m.page.keyboard.press('Escape');
  await m.ctx.close();
}

async function demo(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: false, srv });
  await settle(page, () => !!(window.DeskV1Fixtures && window.DeskV1Fixtures.campaigns));
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await settle(page, () => !!document.querySelector('.desk-v1-camp-posy'));
  (await page.$('[data-camp-chat]')) === null ? ok('demo mode keeps the old box (no conversation mounted)') : fail('chat mounted in demo mode');
  srv.log.length === 0 ? ok('flag OFF: 0 /api/desk/* requests') : fail('demo called the server: ' + JSON.stringify(srv.log.map((r) => r.method + ' ' + r.path)));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  console.log('live ON: conversation'); const prior = await conversation(browser);
  console.log('live ON: reload'); await reload(browser, prior);
  console.log('live ON: Suggest button'); await suggestButton(browser);
  console.log('live ON: no agent'); await noAgent(browser);
  console.log('live ON: text size + pop-out'); await chrome(browser);
  console.log('live OFF: demo'); await demo(browser);
} catch (e) { fail('harness error: ' + (e && e.stack || e)); }
await browser.close();
if (bad) { console.error(`\n❌ FAIL — ${bad} case(s)`); process.exit(1); }
console.log('\n✅ PASS — the campaign agent box is a saved conversation with the campaign\'s agent: turns, suggestions chip from the server, scope, refused sends, reload, Suggest, no-agent, demo.');
