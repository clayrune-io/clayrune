#!/usr/bin/env node
/**
 * Regression: a session dispatched AFTER the chat list was drawn must show up
 * as working without a page reload (Ron, 2026-10-03, phone: "Tobin is
 * inactive" ... "I shouldn't be refreshing all the time to see who's active").
 *
 * Mechanism (before the fix): the only recurring /agent/status read for an
 * open project was the 15s fallback poll in index.html. It fetched EVERY
 * session the server had, then looped only over sessions already in
 * agentHistory and threw the rest away. A builder Dave dispatched server-side
 * (no client action, so no optimistic agentStatusCache entry) was in that
 * response every 15s and never entered agentStatusCache, which is the only
 * source the Chats rows (_liveConvStates) and the Channel roster
 * (_channelRoster) read live state from. The poll also returned early when the
 * client knew of no running/idle session at all, so a quiet project never
 * polled. Only openProjectModal/reload ran fetchAgentStatus.
 *
 * Hermetic: a bare Node http server plays the API (so SSE can stay open
 * instead of erroring into the stopped-cascade), the SPA comes from this
 * checkout. Runs at 390 (phone) and 1440 (desktop), with the parent known to
 * be idle and with no live session known at all.
 *
 * RUN: cd tools/smoke && node chat-list-discovers-new-session.mjs   (~70s)
 */
import { readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const STATIC = loadStaticJsCss(REPO_ROOT);
const PID = 'smoke_discover';
const WAIT_MS = 24000;   // one 15s poll tick + slack

const DAVE = { name: 'dave', scope: 'global', display_name: 'dave', agent_name: 'Dave', avatar: 'fig:guard' };
const TOBIN = { name: 'builder', scope: 'global', display_name: 'builder', agent_name: 'Tobin', avatar: 'fig:smith' };

function project(liveAgent) {
  return {
    id: PID, name: 'Discover', status: 'active', domain: 'general', emoji: '🧪',
    description: '', summary: '', current_task: 'Idle', next_action: '',
    blocked: false, blocked_reason: null, activity_log: [], backlog: [],
    project_path: '/smoke/' + PID, last_updated: '2026-10-03T00:00:00Z',
    last_updated_relative: 'today', last_completed: null, live_agent: liveAgent,
    display_order: 0, provider: 'claude', use_streaming_agent: true,
    distiller_mode: 'proposed', distiller_min_recurrence: 3,
    distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
    distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
    distiller_skip_errors: true,
  };
}
function session(sid, csid, status, ch, task) {
  return {
    session_id: sid, claude_session_id: csid, status, task, character: ch,
    identity: { key: 'global:' + ch.name, name: ch.agent_name, avatar: ch.avatar },
    started_at: '2026-10-04T00:36:54Z', provider: 'claude', log_lines: ['> ' + task, 'ok'],
    log_line_ts: [null, null], active_subagents: [], live_copies: [],
  };
}

// Server state, flipped by the test. `daveStatus` picks the scenario.
const state = { phase: 1, daveStatus: 'idle' };
function sessions() {
  const out = [session('mc-dave', 'csid-dave', state.daveStatus, DAVE, 'plan the work')];
  if (state.phase === 2) out.push(session('mc-tobin', 'csid-tobin', 'running', TOBIN, 'build slice 1'));
  return out;
}
function liveAgent() {
  if (state.phase === 2) return { state: 'working', reason: null, task: 'build slice 1' };
  return state.daveStatus === 'idle' ? { state: 'idle', reason: null, task: 'plan the work' } : null;
}
const CONVERSATIONS = [{
  claude_session_id: 'csid-dave', mc_session_id: 'mc-dave', character: DAVE,
  identity: { key: 'global:dave', name: 'Dave', avatar: DAVE.avatar },
  mtime: 1000, ts_relative: '2m ago', status: 'completed', turns: 2,
  label: 'plan the work', first_user: 'plan the work', last_user: 'plan the work',
}];

const openStreams = new Set();
const server = createServer((req, res) => {
  const path = new URL(req.url, 'http://x').pathname;
  const json = (body) => { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
  if (path === '/' || path === '/index.html') { res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); return res.end(INDEX_HTML); }
  const hit = STATIC[path];
  if (hit) { res.writeHead(200, { 'Content-Type': hit[0] }); return res.end(hit[1]); }
  if (path === '/api/projects') return json([project(liveAgent())]);
  if (path === '/api/config') return json({});
  if (path === '/api/characters') return json([]);
  if (path === '/api/system/heartbeat') return json({ ok: true, started_at: 'boot-1' });
  if (path === `/api/project/${PID}/agent/status`) return json({ sessions: sessions() });
  if (path === `/api/project/${PID}/conversations`) return json(CONVERSATIONS);
  if (path === `/api/project/${PID}/agent/log`) return json([]);
  if (path === `/api/project/${PID}/agent/stream`) {
    // Hold it open: an aborted stream would run the onerror retry cascade
    // and stamp the session 'stopped', which is a different bug.
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
    res.write(': open\n\n');
    openStreams.add(res);
    req.on('close', () => openStreams.delete(res));
    return;
  }
  res.writeHead(404); res.end();
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const ORIGIN = `http://127.0.0.1:${server.address().port}`;

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

async function scenario(browser, width, height, daveStatus) {
  const label = `${width}px, parent ${daveStatus}`;
  console.log(`\n${label}`);
  state.phase = 1; state.daveStatus = daveStatus;
  const ctx = await browser.newContext({ viewport: { width, height }, hasTouch: width < 960, isMobile: width < 960 });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message || String(e)));
  try {
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => Array.isArray(window.allProjects || allProjects) && allProjects.length > 0, null, { timeout: 15000 });
    await page.evaluate((pid) => openProjectModal(pid), PID);
    await page.waitForSelector('.conv-row[data-csid="csid-dave"]', { timeout: 10000 });
    ok('chat list drawn with Dave only');

    // Dave dispatches Tobin server-side. Nothing happens in this client.
    state.phase = 2;
    const t0 = Date.now();
    const seen = await page.waitForFunction(() => {
      const row = document.querySelector('.conv-row[data-csid="csid-tobin"], .conv-row[data-mcsid="mc-tobin"]');
      return !!(row && row.classList.contains('conv-live-working'));
    }, null, { timeout: WAIT_MS, polling: 500 }).then(() => true).catch(() => false);
    seen ? ok(`Chats: Tobin's row appears as Working in ${((Date.now() - t0) / 1000).toFixed(1)}s, no reload`)
         : fail(`Chats: no Working row for Tobin within ${WAIT_MS / 1000}s (cache knows mc-tobin: ${await page.evaluate(() => !!agentStatusCache['mc-tobin'])})`);

    // Channel view of the same fact: Tobin "In the room", not on the Bench.
    await page.evaluate((pid) => setRailMode(pid, 'channel'), PID);
    await page.waitForTimeout(400);
    const where = await page.evaluate(() => {
      const row = document.querySelector('.channel-row[data-char-key="global:builder"]');
      if (!row) return 'absent';
      let n = row.previousElementSibling;
      while (n && !n.classList.contains('channel-section-header')) n = n.previousElementSibling;
      return n ? n.textContent.replace(/\d+$/, '').trim() : 'no-section';
    });
    where === 'In the room' ? ok('Channel: Tobin is In the room') : fail(`Channel: Tobin is "${where}", expected "In the room"`);
  } finally {
    const real = errors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource|404/i.test(e));
    real.forEach((e) => fail('page error: ' + e));
    await ctx.close();
  }
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  for (const [w, h] of [[390, 844], [1440, 900]]) {
    for (const dave of ['idle', 'completed']) await scenario(browser, w, h, dave);
  }
  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0 ? '\n✅ PASS: a newly dispatched session shows as working without a reload.' : `\n❌ FAIL: ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
} finally {
  for (const r of openStreams) r.end();
  if (browser) await browser.close().catch(() => {});
  server.close();
  process.exit(exitCode);
}
