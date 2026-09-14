#!/usr/bin/env node
/**
 * Chat fork guard (2026-09-14).
 *
 * WHY THIS EXISTS
 * ---------------
 * One chat ran as four live `claude -r` processes. Replies vanished from the
 * screen and came back only after another message: a refresh or stream reset
 * handed the view a SHORTER history and the view adopted it. Two safeguards:
 *   1. the chat never replaces rendered history with a shorter copy
 *      (status poll, reconcile, SSE reset replay), and logs the mismatch;
 *   2. a conversation with other live copies shows a notice linking to them.
 *
 * Serves static/js + static/css from THIS checkout over the live page, so a
 * worktree tests its own code. Every non-GET request is aborted, and the
 * status route for a fake project is canned, so nothing touches real sessions.
 *
 * RUN: node chat-fork-guard.mjs      (needs MC on localhost:5199)
 */
import { chromium } from 'playwright';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const ok = (m) => console.log('  \u2713 ' + m);
let bad = 0;
const fail = (m) => { console.error('  \u2717 ' + m); bad++; };
const check = (cond, m) => (cond ? ok(m) : fail(m));

const PID = '__forksmoke__';
const SID = 'forksmoke0001';
let status = { sessions: [] };
const row = (lines, extra = {}) => ({
  session_id: SID, claude_session_id: 'csid-forksmoke', status: 'idle', task: 'smoke',
  log_lines: lines, started_at: '', process_alive: true, live_copies: [], cwd_moved_from: '',
  ...extra,
});
const L6 = ['> Ron: first question', 'answer one', '> Ron: second', 'answer two',
            '> Ron: third', 'answer three'];

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message || String(e)));
  await page.route('**/*', (route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.pathname === `/api/project/${PID}/agent/status`)
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(status) });
    if (req.method() !== 'GET') return route.abort();
    if (url.hostname === 'localhost' && /^\/static\/(js|css)\//.test(url.pathname)) {
      const f = join(ROOT, url.pathname);
      if (existsSync(f)) {
        return route.fulfill({ status: 200,
          contentType: url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript; charset=utf-8',
          body: readFileSync(f, 'utf8') });
      }
    }
    return route.continue();
  });
  await page.goto('http://localhost:5199/', { waitUntil: 'domcontentloaded' });
  const bridged = await page.waitForFunction(
    () => typeof window._mergeShorterHistory === 'function'
      && typeof window.fetchAgentStatus === 'function'
      && typeof window._renderForkNotice === 'function',
    null, { timeout: 20000 }).then(() => true).catch(() => false);
  check(bridged, 'checkout JS loaded (history guard, fork notice, fetchAgentStatus bridged)');
  if (!bridged) throw new Error('no bridge');

  await page.evaluate((sid) => {
    const wrap = document.createElement('div');
    wrap.className = 'agent-chat';
    const out = document.createElement('div');
    out.className = 'agent-output';
    out.id = `agent-output-${sid}`;
    wrap.appendChild(out);
    document.body.appendChild(wrap);
  }, SID);
  const snap = () => page.evaluate((sid) => ({
    buf: (agentOutputBuffers[sid] || []).length,
    dom: document.getElementById(`agent-output-${sid}`)?.textContent || '',
    log: (window._historyShrinkLog || []).map((r) => r.source),
    notice: document.getElementById(`fork-notice-${sid}`)?.textContent || null,
    link: document.querySelector(`#fork-notice-${sid} a.fork-notice-link`)?.dataset.sid || null,
  }), SID);
  const poll = async (s) => { status = { sessions: [s] }; await page.evaluate((pid) => window.fetchAgentStatus(pid), PID); return snap(); };

  // ── 1. Status poll never shrinks the rendered history.
  let s = await poll(row(L6));
  check(s.buf === 6 && s.dom.includes('answer three'), `poll renders the 6-line history (buf=${s.buf})`);
  s = await poll(row(L6.slice(0, 3)));
  check(s.buf === 6 && s.dom.includes('answer three'), `shorter poll (3 lines) keeps all 6 on screen (buf=${s.buf})`);
  check(s.log.includes('status-poll'), 'the shrink was logged (status-poll)');
  s = await poll(row(['answer three', '> Ron: fourth', 'answer four']));
  check(s.buf === 8 && s.dom.includes('answer four'), `shorter poll with new lines past the shared anchor appends them (buf=${s.buf})`);
  const L10 = [...L6, '> Ron: fourth', 'answer four', '> Ron: fifth', 'answer five'];
  s = await poll(row(L10));
  check(s.buf === 10 && s.dom.includes('answer five'), `longer poll is adopted normally (buf=${s.buf})`);

  // ── 2. SSE `reset` never wipes the screen for a shorter replay.
  await page.evaluate(({ pid, sid }) => {
    window.EventSource = class {
      // The live page opens streams for its own sessions too, so key by the
      // session in the URL; emitting on "the latest" instance hits a real chat.
      constructor(u) {
        this.url = u;
        const m = /[?&]session=([^&]+)/.exec(u);
        if (m) (window.__forkSmokeESBySid ||= {})[decodeURIComponent(m[1])] = this;
        window.__forkSmokeESCount = (window.__forkSmokeESCount || 0) + 1;
      }
      close() {}
    };
    window.connectAgentStream(pid, sid);
  }, { pid: PID, sid: SID });
  const emit = (m) => page.evaluate(({ msg, sid }) =>
    window.__forkSmokeESBySid[sid].onmessage({ data: JSON.stringify(msg) }), { msg: m, sid: SID });
  await emit({ type: 'reset' });
  await emit({ type: 'output', text: 'answer one' });
  await emit({ type: 'output', text: 'answer two' });
  s = await snap();
  check(s.buf === 10 && s.dom.includes('answer five'), `during a held replay the screen is untouched (buf=${s.buf})`);
  await page.waitForTimeout(700);
  s = await snap();
  check(s.buf === 10 && s.dom.includes('answer five'), `shorter replay after reset keeps the 10 rendered lines (buf=${s.buf})`);
  check(s.log.includes('stream-reset'), 'the shrink was logged (stream-reset)');
  await emit({ type: 'reset' });
  for (const t of [...L10, '> Ron: sixth', 'answer six']) await emit({ type: 'output', text: t });
  await page.waitForTimeout(700);
  s = await snap();
  check(s.buf === 12 && s.dom.includes('answer six'), `longer replay after reset is adopted (buf=${s.buf})`);
  if (s.buf !== 12) {
    console.error('    diag:', JSON.stringify(await page.evaluate((sid) => ({
      eventSources: window.__forkSmokeESCount, shrinkLog: window._historyShrinkLog,
      tail: (agentOutputBuffers[sid] || []).slice(-3), cursor: agentServerLines[sid],
    }), SID)));
  }

  // ── 3. Fork notice.
  s = await poll(row(L10, { live_copies: ['othercopy0002'] }));
  check(!!s.notice && s.notice.includes('running in 2 copies'), `notice shows for a second live copy: "${(s.notice || '').slice(0, 70)}"`);
  check(s.link === 'othercopy0002', 'notice links to the other copy');
  s = await poll(row(L10, { live_copies: ['othercopy0002'], process_alive: false }));
  check(!!s.notice && s.notice.includes('running in another copy'), 'a session without a process points at the live copy');
  s = await poll(row(L10, { live_copies: [] }));
  check(s.notice === null, 'notice is removed once there is one copy');
  s = await poll(row(L10, { cwd_moved_from: 'C:/gone/tree' }));
  check(!!s.notice && s.notice.includes('moved to a new working directory'), 'a worktree move shows a notice');

  const ours = errors.filter((e) => /history|fork|_merge|_settle|_render/i.test(e));
  check(ours.length === 0, `no page errors from the guard/notice code (${errors.length} page errors total)`);
  if (ours.length) console.error(ours.join('\n'));
} catch (e) {
  fail(`smoke aborted: ${e.message}`);
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(bad ? 1 : 0);
