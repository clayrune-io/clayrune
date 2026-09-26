#!/usr/bin/env node
/**
 * Regression for: clicking a finished conversation's row opened a DIFFERENT
 * agent's chat (Ron clicked a Dave chat and got Bram's).
 *
 * ROOT CAUSE (diagnosed by Bram, confirmed against the code)
 * ------------------------------------------------------------------------
 * A scheduled/hivemind-worker session that stops being listed by
 * /agent/status keeps its client-side tab entry — fetchAgentStatus DETACHES
 * it (status -> 'stopped') rather than deleting it, so the transcript stays
 * resumable ([keep-window-open] in conversation.js). But
 * getProjectTabSessions() hides any TERMINAL schedule/hivemind_worker
 * session from the tab list. openConversation()'s fast path
 * (switchAgentTab) sets activeAgentTab[pid] to that hidden session BEFORE
 * the next agentPanelHTML render — and that render's stale-selection guard
 * (`!sessions.some(s => s.sessionId === activeAgentTab[p.id])`) sees the
 * session is not in the (filtered) tab list, deletes the selection, and the
 * very next line auto-selects the newest running/idle session instead —
 * someone else's chat. A page reload "fixes" it because the reload starts
 * with no activeAgentTab at all, so nothing gets clobbered.
 *
 * FIX: getProjectTabSessions() never filters out the session the user has
 * it explicitly selected (activeAgentTab[pid] or splitAgentTab[pid]) —
 * conversation.js's getProjectTabSessions, `pinned` set.
 *
 * This test stubs the server entirely (page.route) — no real MC server, no
 * real agent sessions — since the point is the CLIENT-side stale-selection
 * race, not anything server-timed. Two identities, Dave (schedule-triggered,
 * about to go stale) and Bram (a manual/live chat), each with one
 * conversation. Sequence: open Dave's chat, open Bram's chat, Dave's session
 * drops out of /agent/status (simulating fetchAgentStatus's detach pass),
 * then click Dave's (now-stale) row again — it must reopen DAVE's
 * conversation, never Bram's.
 *
 * RUN: cd tools/smoke && node conv-stale-scheduled-tab.mjs
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
const PROJECTS_JSON = readFileSync(resolve(__dirname, 'fixtures', 'projects.json'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_alpha';
const SHOT = process.env.STALE_SHOT || '';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, pass, failMsg) => (cond ? ok(pass) : fail(failMsg));

const DAVE = { name: 'dave', agent_name: 'Dave', scope: 'global', avatar: 'fig:guard' };
const BRAM = { name: 'bram', agent_name: 'Bram', scope: 'global', avatar: 'fig:gardener' };

// /agent/status truth. 'both' = Dave's scheduled run still live; 'other-only'
// = it has dropped off the server's list (simulating a restart / stale-purge)
// while Bram's chat stays live — the exact moment the bug fires.
let phase = 'both';
const SESSIONS = {
  mcSched: { session_id: 'mcSched', status: 'idle', trigger_type: 'schedule', claude_session_id: 'csidSched',
    started_at: '2026-09-26T21:00:00Z', task: '[Scheduled run] [Backlog run] You are Dave, running the mission_control backlog on a timer.',
    character: DAVE, log_lines: ['> Ron: run the backlog', 'SCHED-ANSWER'] },
  mcOther: { session_id: 'mcOther', status: 'idle', trigger_type: 'manual', claude_session_id: 'csidOther',
    started_at: '2026-09-26T21:05:00Z', task: 'quick question', character: BRAM, log_lines: ['> Ron: quick question', 'OTHER-ANSWER'] },
};

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const json = (o) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(o) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') {
      const patched = JSON.parse(PROJECTS_JSON);
      patched[0].project_path = '/smoke/alpha';
      return json(patched);
    }
    if (path === '/api/config') return json({});
    if (/\/agent\/status$/.test(path)) {
      const sessions = phase === 'both' ? [SESSIONS.mcSched, SESSIONS.mcOther] : [SESSIONS.mcOther];
      return json({ sessions });
    }
    return route.abort();
  });

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });

  // Seed the two identities as conversation-list rows (what /conversations
  // would normally supply) and open the Channel rail, where an automated
  // schedule run with a persona surfaces (the Chats tab hides it).
  await page.evaluate((pid) => {
    conversationsCache[pid] = [
      { claude_session_id: 'csidSched', mc_session_id: 'mcSched', live: true, status: 'idle', label: 'Backlog run',
        character: { name: 'dave', agent_name: 'Dave', scope: 'global', avatar: 'fig:guard' }, trigger_type: 'schedule', turns: 2, mtime: 100 },
      { claude_session_id: 'csidOther', mc_session_id: 'mcOther', live: true, status: 'idle', label: 'Quick question',
        character: { name: 'bram', agent_name: 'Bram', scope: 'global', avatar: 'fig:gardener' }, turns: 2, mtime: 200 },
    ];
    agentLogCache[pid] = [];
    openProjectModal(pid);
    setRailMode(pid, 'channel');
  }, PID);
  const sc = `.modal-window[data-modal-id="${PID}"] `;
  await page.waitForSelector(sc + '.channel-row', { timeout: 10000 });

  const rowFor = (name) => page.locator(`${sc}.channel-row`).filter({ hasText: name }).first();
  const activeInfo = () => page.evaluate((pid) => {
    const sid = activeAgentTab[pid] || null;
    return {
      sid,
      badge: document.querySelector('.character-badge')?.textContent || '',
      outputId: document.querySelector('.agent-output')?.id || '',
      text: document.querySelector('.agent-output')?.innerText || '',
    };
  }, PID);

  // 1. Open Bram's chat first (this is the poll that seeds full agentStatusCache
  //    for BOTH sessions, including Dave's — same as a live app where the
  //    periodic poll already knows about every running session).
  await rowFor('Bram').click();
  await page.waitForFunction((pid) => agentStatusCache['mcOther'] && agentStatusCache['mcOther'].task, PID, { timeout: 5000 });
  await page.waitForFunction(() => document.querySelector('.agent-output')?.innerText.includes('OTHER-ANSWER'), null, { timeout: 5000 });
  let info = await activeInfo();
  check(info.sid === 'mcOther' && info.text.includes('OTHER-ANSWER'),
    'Bram\'s chat opens first', `expected mcOther/OTHER-ANSWER, got ${JSON.stringify(info)}`);

  // 2. Open Dave's chat (his scheduled run, still "live" per /agent/status).
  await rowFor('Dave').click();
  await page.waitForFunction(() => document.querySelector('.agent-output')?.innerText.includes('SCHED-ANSWER'), null, { timeout: 5000 });
  info = await activeInfo();
  check(info.sid === 'mcSched' && info.badge.includes('Dave') && info.text.includes('SCHED-ANSWER'),
    'Dave\'s scheduled chat opens correctly', `expected mcSched/Dave/SCHED-ANSWER, got ${JSON.stringify(info)}`);

  // 3. Switch away to Bram again (Dave's tab is now the "old row" in the background).
  await rowFor('Bram').click();
  await page.waitForFunction(() => document.querySelector('.agent-output')?.innerText.includes('OTHER-ANSWER'), null, { timeout: 5000 });
  info = await activeInfo();
  check(info.sid === 'mcOther', 'switched to Bram\'s chat', `expected mcOther active, got ${JSON.stringify(info)}`);

  // 4. Dave's scheduled run drops off /agent/status (server restart / stale-purge)
  //    while Bram's stays live. Drive the exact client mechanism: a poll tick.
  phase = 'other-only';
  await page.evaluate((pid) => fetchAgentStatus(pid), PID);
  await page.waitForFunction((pid) => agentStatusCache['mcSched']?.status === 'stopped', PID, { timeout: 5000 });
  await page.waitForTimeout(300);

  // 5. Click Dave's row again — still present in the Channel roster (its
  //    conversation history didn't go anywhere). This must reopen DAVE's
  //    chat, not silently re-select whatever else is currently active.
  await rowFor('Dave').click();
  await page.waitForTimeout(500);
  info = await activeInfo();
  check(info.sid === 'mcSched', `clicking Dave's stale row re-selects mcSched (got ${info.sid})`,
    `clicking Dave's stale row opened session '${info.sid}' instead of mcSched — stale-selection guard evicted it`);
  check(info.badge.includes('Dave') && !info.badge.includes('Bram'),
    'header identity shows Dave, not Bram', `header identity wrong: ${JSON.stringify(info.badge)}`);
  check(info.text.includes('SCHED-ANSWER') && !info.text.includes('OTHER-ANSWER'),
    'pane shows Dave\'s transcript, not Bram\'s', `pane content wrong: ${JSON.stringify(info.text.slice(-160))}`);

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.length ? uncaught.forEach((e) => fail('uncaught page error: ' + e)) : ok('no uncaught page errors');
  if (SHOT) { await page.screenshot({ path: SHOT }); ok('screenshot: ' + SHOT); }
  exitCode = bad ? 1 : 0;
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
}
console.log(exitCode ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(exitCode);
