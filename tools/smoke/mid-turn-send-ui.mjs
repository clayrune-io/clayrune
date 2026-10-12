#!/usr/bin/env node
/**
 * MC-1086 — mid-turn Send steers/queues by default; Interrupt is explicit.
 *
 * Hermetic (real index.html + static/js served verbatim, canned API, no server).
 * Checks, for a RUNNING chat:
 *   - Claude: composer shows an Interrupt button beside Send and a "steers"
 *     placeholder; plain Send posts NO send_mode; Interrupt posts
 *     send_mode:'interrupt'.
 *   - Vendor with no live process (mode_b_kind 'none'): "queued" placeholder.
 *   - A steer/queue response lifts the _sendInFlight gate (no turn_start is
 *     coming, so the gate would otherwise swallow turn_complete for 8s).
 *   - An IDLE chat has no Interrupt button.
 *
 * RUN   node tools/smoke/mid-turn-send-ui.mjs      Exit 0 = pass, 1 = regression
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_mid_turn';
const STATIC = loadStaticJsCss(REPO_ROOT);

const project = {
  id: PID, name: 'Mid Turn Smoke', status: 'active', domain: 'general', emoji: '🧪',
  description: '', summary: '', current_task: 'Idle', next_action: '', blocked: false,
  blocked_reason: null, activity_log: [], backlog: [], project_path: '/smoke/' + PID,
  last_updated: '2026-10-12T00:00:00Z', last_updated_relative: 'today', last_completed: null,
  live_agent: null, display_order: 0, provider: 'claude', use_streaming_agent: true,
  distiller_mode: 'proposed', distiller_min_recurrence: 3, distiller_max_topics_per_session: 3,
  distiller_max_preferences_per_session: 3, distiller_max_explorations_per_session: 3,
  distiller_min_turns: 5, distiller_skip_errors: true,
};

let bad = 0;
const check = (name, cond, detail = '') => {
  console.log(`${cond ? '✅' : '❌'} ${name}${cond ? '' : ' — ' + detail}`);
  if (!cond) bad++;
};

const browser = await chromium.launch();
let exitCode = 1;
try {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  const posts = [];
  let sendReply = { ok: true, route: 'steer' };
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    const json = (b) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(b) });
    if (path === '/api/projects') return json([project]);
    if (path === '/api/config') return json({});
    if (path === '/api/characters') return json([]);
    if (path === `/api/project/${PID}/agent/send`) {
      posts.push(JSON.parse(route.request().postData() || '{}'));
      return json(sendReply);
    }
    if (path === `/api/project/${PID}/agent/stream`) {
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' });
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });

  const open = (sid, status, provider) => page.evaluate(({ pid, sid, status, provider }) => {
    if (provider !== 'claude') {
      _agentProviders = [{ name: provider, display_name: provider, capabilities: { mode_b_kind: 'none' } }];
    }
    agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Mid Turn Smoke', task: 't', status, provider, startedAt: new Date().toISOString() });
    agentStatusCache[sid] = { status, task: 't', projectId: pid, provider, startedAt: new Date().toISOString(), claudeSessionId: 'csid-' + sid, character: null };
    agentOutputBuffers[sid] = ['> earlier', 'reply'];
    conversationsCache[pid] = [{ claude_session_id: 'csid-' + sid, mc_session_id: sid, character: null, mtime: 1000, ts_relative: '1m ago', status, turns: 1, label: 't', first_user: 'earlier', last_user: 'earlier', provider }];
    openProjectModal(pid);
    openConversation(pid, 'csid-' + sid, sid, true);
  }, { pid: PID, sid, status, provider });
  const probe = (sid) => page.evaluate((sid) => {
    const ta = document.getElementById(`agent-followup-${sid}`);
    const row = ta && ta.closest('.agent-chat-input-row');
    const ib = row && row.querySelector('.btn-interrupt-send');
    return { placeholder: ta ? ta.placeholder : null, interrupt: !!ib, interruptText: ib ? ib.textContent : null };
  }, sid);

  // ── Claude, running ──
  await open('s-claude', 'running', 'claude');
  await page.waitForSelector('#agent-followup-s-claude', { timeout: 5000 });
  let pr = await probe('s-claude');
  check('claude running: Interrupt button rendered', pr.interrupt && pr.interruptText === 'Interrupt', JSON.stringify(pr));
  check('claude running: placeholder says it steers', /steers/i.test(pr.placeholder || ''), pr.placeholder);

  await page.fill('#agent-followup-s-claude', 'plain send');
  await page.evaluate(() => { _sendInFlight['s-claude'] = Date.now(); });
  await page.click('.agent-chat-input-row .btn-dispatch');
  await page.waitForTimeout(400);
  check('plain Send posts without send_mode', posts.length === 1 && posts[0].message === 'plain send' && !('send_mode' in posts[0]), JSON.stringify(posts));
  const gate = await page.evaluate(() => !!_sendInFlight['s-claude']);
  check('steer response lifts the in-flight gate', !gate);

  await page.fill('#agent-followup-s-claude', 'stop and do this');
  await page.click('.agent-chat-input-row .btn-interrupt-send');
  await page.waitForTimeout(400);
  check('Interrupt posts send_mode:interrupt', posts.length === 2 && posts[1].send_mode === 'interrupt' && posts[1].message === 'stop and do this', JSON.stringify(posts));

  // ── Queue response also lifts the gate ──
  sendReply = { ok: true, queued: true, route: 'queue' };
  await page.fill('#agent-followup-s-claude', 'queued one');
  await page.evaluate(() => { _sendInFlight['s-claude'] = Date.now(); });
  await page.evaluate(() => window.sendFollowup('smoke_mid_turn', 's-claude'));
  await page.waitForTimeout(400);
  check('queue response lifts the in-flight gate', !(await page.evaluate(() => !!_sendInFlight['s-claude'])));

  // ── Vendor with no live process, running ──
  await open('s-codex', 'running', 'codex');
  await page.waitForSelector('#agent-followup-s-codex', { timeout: 5000 });
  pr = await probe('s-codex');
  check('vendor running: placeholder says queued for next turn', /queued/i.test(pr.placeholder || ''), pr.placeholder);
  check('vendor running: Interrupt button still offered', pr.interrupt, JSON.stringify(pr));

  // ── Idle chat: no Interrupt ──
  await open('s-idle', 'idle', 'claude');
  await page.waitForSelector('#agent-followup-s-idle', { timeout: 5000 });
  pr = await probe('s-idle');
  check('idle: no Interrupt button', !pr.interrupt, JSON.stringify(pr));

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  check('no uncaught page errors', uncaught.length === 0, uncaught.join(' | '));
  await ctx.close();
  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error(e);
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nPASS');
process.exit(exitCode);
