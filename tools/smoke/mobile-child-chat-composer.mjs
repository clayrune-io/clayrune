#!/usr/bin/env node
/**
 * Dispatched child chat on mobile keeps its composer on screen (f6cf05fd).
 *
 * WHY THIS EXISTS
 * ----------------
 * Ron, 2026-10-02, Galaxy Z Fold: opening a DISPATCHED child's chat (spawned by
 * another session) showed header, status row, the amber "Dispatched by X ...
 * Open its chat" notice, then the thread running off the bottom of the screen
 * - no composer, and the thread could not be scrolled to its last line.
 *
 * sizeAgentChat() (index.html) hand-sizes .agent-output on the explicit-height
 * layout mobile uses: chatHeight - separator - composer - search bar. The
 * dispatched-by notice (and the fork notice) are siblings ahead of
 * .agent-output that the formula never subtracted, so the output was sized for
 * space the notice was already occupying and pushed the composer past the
 * clipped bottom of .agent-chat.
 *
 * Measured on the REAL layout, long thread, at phone widths, for a running and
 * a completed child - plus a no-banner control (an ordinary chat must stay
 * exactly as it was).
 *
 * Hermetic: real index.html + real static/*, no server (same shape as
 * mobile-send-height-restore.mjs).
 *
 * RUN   node tools/smoke/mobile-child-chat-composer.mjs
 *       SHOT_DIR=_scratch/child-shots node tools/smoke/mobile-child-chat-composer.mjs
 * Exit  0 = composer visible + thread reaches its last line everywhere.
 */
import { readFileSync, readdirSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const SHOT_DIR = process.env.SHOT_DIR || '';
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_child_chat';
const SID = 'sess-child';
const PARENT = 'sess-parent-vance';

const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));

const project = {
  id: PID, name: 'Child Chat Smoke', status: 'active', domain: 'general', emoji: '🧪',
  description: '', summary: '', current_task: 'Idle', next_action: '',
  blocked: false, blocked_reason: null, activity_log: [], backlog: [],
  project_path: '/smoke/' + PID, last_updated: '2026-10-02T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null,
  display_order: 0, provider: 'claude', use_streaming_agent: true,
  distiller_mode: 'proposed', distiller_min_recurrence: 3,
  distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
  distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
  distiller_skip_errors: true,
};
const PROJECTS_JSON = JSON.stringify([project]);

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

const SCENARIOS = [
  { vw: 390, vh: 844, status: 'running',   child: true,  label: '390x844 child running' },
  { vw: 390, vh: 844, status: 'completed', child: true,  label: '390x844 child completed' },
  { vw: 344, vh: 882, status: 'running',   child: true,  label: '344x882 child running (Fold cover)' },
  { vw: 344, vh: 882, status: 'completed', child: true,  label: '344x882 child completed (Fold cover)' },
  { vw: 390, vh: 844, status: 'completed', child: false, label: '390x844 ordinary chat (control)' },
];

let browser;
let exitCode = 1;
try {
  browser = await chromium.launch();
  if (SHOT_DIR) mkdirSync(SHOT_DIR, { recursive: true });
  for (const sc of SCENARIOS) {
    console.log(`\n${sc.label}`);
    const ctx = await browser.newContext({ viewport: { width: sc.vw, height: sc.vh }, hasTouch: true });
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
    await page.route('**/*', (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
      const hit = STATIC[path];
      if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
      if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
      if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      return route.abort();
    });
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });

    await page.evaluate(({ pid, sid, parent, status, child }) => {
      const now = new Date().toISOString();
      const lines = [];
      for (let i = 1; i <= 80; i++) lines.push(i % 7 === 0 ? `> user line ${i}` : `assistant line ${i}: ` + 'lorem ipsum dolor sit amet '.repeat(4));
      lines.push('LAST-LINE-MARKER end of the thread');
      agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Child Chat Smoke', task: 'child task', status, startedAt: now });
      agentStatusCache[sid] = { status, task: 'child task', projectId: pid, startedAt: now, claudeSessionId: 'csid-child',
        character: { name: 'rusk', agent_name: 'Rusk' }, provider: 'qwen',
        spawnedBySessionId: child ? parent : '' };
      if (child) {
        agentStatusCache[parent] = { status: 'idle', task: 'parent', projectId: pid, startedAt: now, claudeSessionId: 'csid-parent',
          character: { name: 'vance', agent_name: 'Vance' }, provider: 'claude' };
      }
      agentOutputBuffers[sid] = lines;
      conversationsCache[pid] = [{ claude_session_id: 'csid-child', mc_session_id: sid, character: null, mtime: 1000, ts_relative: 'just now', status, turns: 1, label: 'child task', first_user: 'child task', last_user: 'child task' }];
      openProjectModal(pid);
      openConversation(pid, 'csid-child', sid, status === 'running');
    }, { pid: PID, sid: SID, parent: PARENT, status: sc.status, child: sc.child });

    await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });
    await page.waitForTimeout(900);   // let the rAF re-runs + any refresh settle

    const m = await page.evaluate((sid) => {
      const rect = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, h: r.height }; };
      const out = document.getElementById(`agent-output-${sid}`);
      const ta = document.getElementById(`agent-followup-${sid}`);
      const send = ta && ta.closest('.agent-chat-input')?.querySelector('.btn-send-arrow, .btn-dispatch');
      const chat = out && out.closest('.agent-chat');
      const notice = document.getElementById(`dispatched-by-${sid}`);
      // Scroll the thread as far as it will go, then find the last line.
      out.scrollTop = out.scrollHeight;
      const lines = [...out.querySelectorAll('*')].filter((e) => e.children.length === 0 && /LAST-LINE-MARKER/.test(e.textContent));
      const last = lines[lines.length - 1] || null;
      return {
        vh: window.innerHeight,
        notice: !!notice, noticeRect: rect(notice),
        chat: rect(chat), out: rect(out), ta: rect(ta), send: rect(send),
        last: rect(last),
        outScrollable: out.scrollHeight > out.clientHeight + 2,
        atBottom: out.scrollHeight - out.scrollTop - out.clientHeight <= 2,
      };
    }, SID);

    if (SHOT_DIR) await page.screenshot({ path: resolve(SHOT_DIR, `${sc.label.replace(/[^a-z0-9]+/gi, '_')}.png`) });

    (sc.child ? m.notice : !m.notice)
      ? ok(sc.child ? `dispatched-by notice rendered (${Math.round(m.noticeRect.h)}px tall)` : 'no notice (control)')
      : fail(sc.child ? 'dispatched-by notice did not render - fixture is not a child chat' : 'unexpected notice on an ordinary chat');
    const tol = 1;
    (m.ta && m.ta.bottom <= m.vh + tol && m.ta.top >= 0)
      ? ok(`composer textarea on screen (bottom ${Math.round(m.ta.bottom)} <= viewport ${m.vh})`)
      : fail(`composer textarea OFF screen (bottom ${m.ta && Math.round(m.ta.bottom)} vs viewport ${m.vh})`);
    (m.send && m.send.bottom <= m.vh + tol)
      ? ok(`Send button on screen (bottom ${Math.round(m.send.bottom)})`)
      : fail(`Send button OFF screen (bottom ${m.send && Math.round(m.send.bottom)} vs viewport ${m.vh})`);
    (m.chat && m.out && m.out.bottom <= m.chat.bottom + tol)
      ? ok(`thread stays inside its chat box (out bottom ${Math.round(m.out.bottom)} <= chat bottom ${Math.round(m.chat.bottom)})`)
      : fail(`thread overflows its chat box (out bottom ${m.out && Math.round(m.out.bottom)} > chat bottom ${m.chat && Math.round(m.chat.bottom)})`);
    (m.out && m.ta && m.out.bottom <= m.ta.top + tol)
      ? ok('thread does not overlap the composer')
      : fail(`thread overlaps the composer (out bottom ${m.out && Math.round(m.out.bottom)} vs composer top ${m.ta && Math.round(m.ta.top)})`);
    (m.last && m.out && m.last.bottom <= m.out.bottom + tol && m.last.top >= m.out.top - tol)
      ? ok('scrolled to the end, the last line is visible')
      : fail(`last line is not visible after scrolling to the end (last ${m.last && Math.round(m.last.top)}..${m.last && Math.round(m.last.bottom)}, out ${m.out && Math.round(m.out.top)}..${m.out && Math.round(m.out.bottom)})`);

    pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e)).forEach((e) => fail('uncaught page error: ' + e));
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
