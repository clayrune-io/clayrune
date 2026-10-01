#!/usr/bin/env node
/**
 * MC-1027 — the chat header's "Pop Out" opens the conversation in a REAL
 * separate window (chat-only view), capped at 4, one window per conversation.
 *
 * WHY THIS EXISTS
 * ----------------
 * "Pop Out" used to open an in-app viewer. It now loads the SPA itself as
 * `/?popout=1&p=<project>&s=<session>` in its own window (static/js/
 * modal-manager.js `popOutChat` / `bootPopoutChat`, the early script in
 * index.html, html.mc-popout rules in app.css). Four things can silently
 * regress and none show up in a unit test:
 *   - the popped window boots as a full dashboard (sidebar/floor) instead of
 *     one conversation, or fails to render the thread/composer at all;
 *   - clicking Pop Out twice opens a duplicate instead of focusing;
 *   - the cap/slot accounting stops reaching the server registry, so the 6-
 *     connection Chromium origin cap is starved by popped windows;
 *   - the desktop app's pywebview bridge (WKWebView swallows window.open) is
 *     no longer preferred when present.
 *
 * Hermetic: real index.html + real static/js + static/css served verbatim, a
 * canned API, no server, no network. /api/popout/* is mocked with the same
 * contract as mc/blueprints/popout_routes.py (that module has its own
 * pytest: tests/test_popout_routes.py).
 *
 * RUN   node tools/smoke/popout-window.mjs
 * Exit  0 = all checks hold; 1 = a regression.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_popout';
const SID = 'sess-popout-1';
const OTHER_SID = 'sess-popout-other';
const CAP = 4;

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

function fixtureProject(id, name) {
  return {
    id, name, status: 'active', domain: 'general', emoji: '🧪',
    description: '', summary: '', current_task: 'Idle', next_action: '',
    blocked: false, blocked_reason: null, activity_log: [], backlog: [],
    project_path: '/smoke/' + id, last_updated: '2026-09-02T00:00:00Z',
    last_updated_relative: 'today', last_completed: null, live_agent: null,
    display_order: 0, provider: 'claude', use_streaming_agent: true,
    distiller_mode: 'proposed', distiller_min_recurrence: 3,
    distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
    distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
    distiller_skip_errors: true,
  };
}
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Popout Smoke')]);
const STATUS_JSON = JSON.stringify({
  sessions: [{
    session_id: SID, status: 'idle', task: 'popout smoke chat', started_at: '2026-10-01T00:00:00Z',
    claude_session_id: 'csid-popout-1', log_lines: ['> earlier message', 'earlier reply from the agent'],
    provider: 'claude', character: null,
  }],
});

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg));

// Server-registry stand-in: same contract as mc/blueprints/popout_routes.py.
function makeRegistry() {
  const slots = new Set();
  return {
    slots,
    claims: [], releases: [], sends: [],
  };
}

async function installRoutes(ctx, reg) {
  await ctx.route('**/*', (route) => {
    const req = route.request();
    const u = new URL(req.url());
    const path = u.pathname;
    const json = (status, body) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return json(200, {});
    if (path === '/api/characters') return json(200, []);
    if (path === `/api/project/${PID}/agent/status`) return route.fulfill({ status: 200, contentType: 'application/json', body: STATUS_JSON });
    if (path === '/api/popout/claim') {
      const sid = JSON.parse(req.postData() || '{}').session_id;
      reg.claims.push(sid);
      const existing = reg.slots.has(sid);
      if (!existing && reg.slots.size >= CAP) {
        return json(409, { ok: false, count: reg.slots.size, max: CAP, message: `${CAP} conversations are already popped out. Close one of those windows to pop out another.` });
      }
      reg.slots.add(sid);
      return json(200, { ok: true, existing, count: reg.slots.size, max: CAP });
    }
    if (path === '/api/popout/release') {
      const sid = JSON.parse(req.postData() || '{}').session_id;
      reg.releases.push(sid);
      reg.slots.delete(sid);
      return json(200, { ok: true, count: reg.slots.size, max: CAP });
    }
    if (path === `/api/project/${PID}/agent/send`) {
      reg.sends.push(JSON.parse(req.postData() || '{}'));
      return json(200, { ok: true, session_id: SID });
    }
    if (path === `/api/project/${PID}/agent/stream`) {
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': hold\n\n' });
    }
    return route.abort();
  });
}

async function openMainChat(page) {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(({ pid, sid }) => {
    agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Popout Smoke', task: 'popout smoke chat', status: 'idle', startedAt: new Date().toISOString() });
    agentStatusCache[sid] = { status: 'idle', task: 'popout smoke chat', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: 'csid-popout-1', character: null };
    agentOutputBuffers[sid] = ['> earlier message', 'earlier reply from the agent'];
    conversationsCache[pid] = [{ claude_session_id: 'csid-popout-1', mc_session_id: sid, character: null, mtime: 1000, ts_relative: '2h ago', status: 'idle', turns: 1, label: 'popout smoke chat', first_user: 'earlier message', last_user: 'earlier message' }];
    openProjectModal(pid);
    openConversation(pid, 'csid-popout-1', sid, true);
  }, { pid: PID, sid: SID });
  await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });
}

let browser;
let exitCode = 1;
try {
  browser = await chromium.launch();

  // ── 1. Desktop browser path: window.open to the chat-only view ─────────────
  console.log('browser path (window.open)');
  {
    const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
    const reg = makeRegistry();
    await installRoutes(ctx, reg);
    const page = await ctx.newPage();
    const errs = [];
    page.on('pageerror', (e) => errs.push(e.message || String(e)));
    await openMainChat(page);

    const btn = page.locator('.btn-popout-window');
    check(await btn.count() === 1 && await btn.isVisible(), 'chat header shows one Pop Out button', 'Pop Out button missing or hidden at 1400px');
    check(await page.evaluate(() => !document.querySelector('button[onclick^="openPlanViewer"][title="Open output in viewer window"]')),
      'the in-app viewer button is gone from the chat header', 'the old openPlanViewer header button is still rendered');

    const popupP = ctx.waitForEvent('page');
    await btn.click();
    const popup = await popupP;
    const perrs = [];
    popup.on('pageerror', (e) => perrs.push(e.message || String(e)));
    await popup.waitForSelector(`#agent-followup-${SID}`, { timeout: 15000 });
    const url = new URL(popup.url());
    check(url.searchParams.get('popout') === '1' && url.searchParams.get('p') === PID && url.searchParams.get('s') === SID,
      `popup loads /?popout=1&p=…&s=… (${url.search})`, `popup URL wrong: ${popup.url()}`);
    check(await popup.evaluate(() => document.documentElement.classList.contains('mc-popout')), 'popup page is in chat-only mode (html.mc-popout)', 'popup lacks html.mc-popout');
    check(await popup.evaluate((s) => (document.getElementById('agent-chat-' + s) || document.body).innerText.includes('earlier reply from the agent'), SID)
      || await popup.evaluate(() => document.body.innerText.includes('earlier reply from the agent')),
      'popup renders the conversation thread', 'popup did not render the thread');
    const chrome = await popup.evaluate(() => {
      const vis = (sel) => { const e = document.querySelector(sel); if (!e) return false; const r = e.getBoundingClientRect(); return getComputedStyle(e).display !== 'none' && r.width > 0 && r.height > 0; };
      return { sidebar: vis('.sidebar'), main: vis('.main-area'), modalHeader: vis('.modal-header'), rail: vis('.agent-rail'), composer: vis('#agent-followup-' + 'x') };
    });
    check(!chrome.sidebar && !chrome.main && !chrome.modalHeader && !chrome.rail,
      'popup shows no sidebar / floor / modal header / rail', `dashboard chrome visible in popup: ${JSON.stringify(chrome)}`);
    check(await popup.evaluate((s) => { const e = document.getElementById('agent-followup-' + s); const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.bottom <= innerHeight + 1; }, SID),
      'composer is visible inside the popup viewport', 'composer is not visible in the popup');
    check(reg.slots.has(SID) && reg.claims.includes(SID), 'server registry holds the slot for the conversation', 'no slot claimed for the popped conversation');

    // Can send from the popup.
    await popup.fill(`#agent-followup-${SID}`, 'hello from the popup');
    await popup.evaluate(({ pid, sid }) => window.sendFollowup(pid, sid), { pid: PID, sid: SID });
    await popup.waitForTimeout(400);
    check(reg.sends.length === 1 && String(reg.sends[0].message || reg.sends[0].text || JSON.stringify(reg.sends[0])).includes('hello from the popup'),
      'popup sends a follow-up (POST /agent/send carries the typed text)', `popup send not received: ${JSON.stringify(reg.sends)}`);

    // One live stream only: other sessions must not open a stream in a popup.
    const streams = await popup.evaluate((other) => { connectAgentStream('smoke_popout', other); return Object.keys(agentEventSources); }, OTHER_SID);
    check(!streams.includes(OTHER_SID), 'popup refuses a stream for a conversation that is not its own', `popup opened a second stream: ${streams}`);

    // Pop Out again → focuses, no duplicate window.
    const pagesBefore = ctx.pages().length;
    await btn.click();
    await page.waitForTimeout(600);
    check(ctx.pages().length === pagesBefore, `second Pop Out click opens no duplicate window (${pagesBefore} pages)`, `duplicate window opened: ${pagesBefore} -> ${ctx.pages().length}`);

    // Closing releases the slot. Playwright's router does not see a beacon fired
    // while the page is being torn down, so drive the same `pagehide` the browser
    // fires on close (page still alive, beacon goes through the route), then close.
    await popup.evaluate(() => window.dispatchEvent(new Event('pagehide')));
    await page.waitForTimeout(500);
    check(reg.releases.includes(SID) && !reg.slots.has(SID), 'pagehide in the popup releases its slot (sendBeacon -> /api/popout/release)', `slot not released on pagehide: releases=${JSON.stringify(reg.releases)}`);
    await popup.close();

    // Cap: with 4 other conversations popped, a fifth is refused with a plain message.
    for (const s of ['c1', 'c2', 'c3', 'c4']) reg.slots.add(s);
    const pages0 = ctx.pages().length;
    await btn.click();
    await page.waitForFunction(() => document.body.innerText.includes('conversations are already popped out'), null, { timeout: 5000 })
      .then(() => ok(`at the cap (${CAP}) a plain message is shown`), () => fail('no cap message shown at the cap'));
    await page.waitForTimeout(400);
    check(ctx.pages().length === pages0, 'the blank window opened for the click is closed again at the cap', `a window was left open at the cap (${pages0} -> ${ctx.pages().length})`);
    check(errs.concat(perrs).filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e)).length === 0,
      'no uncaught page errors', 'uncaught page errors: ' + errs.concat(perrs).join(' | '));
    await ctx.close();
  }

  // ── 2. Desktop app path: the pywebview bridge is preferred over window.open ──
  console.log('desktop app path (pywebview bridge)');
  {
    const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
    const reg = makeRegistry();
    await installRoutes(ctx, reg);
    await ctx.addInitScript(() => {
      window.__bridgeCalls = []; window.__windowOpens = 0;
      const wo = window.open.bind(window);
      window.open = (...a) => { window.__windowOpens++; return wo(...a); };
      window.pywebview = { api: { open_chat_window: async (p, s, t) => { window.__bridgeCalls.push([p, s, t]); return { ok: true, existing: false }; } } };
    });
    const page = await ctx.newPage();
    await openMainChat(page);
    await page.locator('.btn-popout-window').click();
    await page.waitForTimeout(500);
    const r = await page.evaluate(() => ({ calls: window.__bridgeCalls, opens: window.__windowOpens }));
    check(r.calls.length === 1 && r.calls[0][0] === PID && r.calls[0][1] === SID, 'bridge open_chat_window(project, session, title) called once', `bridge not called correctly: ${JSON.stringify(r)}`);
    check(r.opens === 0, 'window.open is NOT used when the bridge exists', `window.open used ${r.opens}x despite the bridge`);
    check(reg.slots.has(SID), 'slot claimed server-side before the bridge opens the window', 'no slot claimed on the bridge path');
    await ctx.close();
  }

  // ── 3. Mobile: the button stays hidden ──────────────────────────────────────
  console.log('mobile (<=960px)');
  {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true });
    const reg = makeRegistry();
    await installRoutes(ctx, reg);
    const page = await ctx.newPage();
    await openMainChat(page);
    const state = await page.evaluate(() => { const e = document.querySelector('.btn-popout-window'); return e ? getComputedStyle(e).display : 'absent'; });
    check(state === 'none' || state === 'absent', `Pop Out is hidden at 390px (display: ${state})`, `Pop Out visible on mobile (display: ${state})`);
    await ctx.close();
  }

  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error(e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(exitCode);
