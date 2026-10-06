#!/usr/bin/env node
/**
 * A queued child report is visible on its parent (MC-1063).
 *
 * WHY THIS EXISTS
 * ---------------
 * 2026-10-06: a finished child's report was queued behind its busy parent for
 * ~5 minutes and the parent's chat showed nothing, so a person looking at the
 * child's "done" chat and the parent's quiet one could not tell the report from
 * a lost one. /agent/status now carries `reports_waiting` per session and
 * /api/floor carries `report_waiting` per figure. This pins that
 *   1. the chat shows "report waiting from <child>" above the transcript,
 *   2. the wording changes for a parked report (held / needs review),
 *   3. the marker is removed once the list is empty (report handed over),
 *   4. the Floor card shows the same fact,
 * at 1440 and 390 wide, with the marker inside the viewport at both.
 *
 * Serves index.html + static/js + static/css from THIS checkout over the live
 * page, so a worktree tests its own code. Every non-GET request is aborted and
 * the status/floor routes are canned, so nothing touches real sessions.
 *
 * RUN: node report-waiting-marker.mjs   (needs MC on localhost:5199, or set MC_SMOKE_BASE)
 */
import { chromium } from 'playwright';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, m) => (cond ? ok(m) : fail(m));

const PID = '__reportsmoke__';
const SID = 'reportsmoke001';
const LINES = ['> Ron: dispatch the builder', 'dispatched; waiting for its report'];
let status = { sessions: [] };
const row = (reports) => ({
  session_id: SID, claude_session_id: 'csid-reportsmoke', status: 'running', task: 'smoke',
  log_lines: LINES, started_at: '', process_alive: true, live_copies: [], cwd_moved_from: '',
  reports_waiting: reports,
});
const waiting = { event_id: 'child1:turn:1', child_session_id: 'child1', who: 'Sol_Tobin',
  stage: 'waiting', label: 'report waiting from Sol_Tobin',
  detail: 'Queued. It is delivered into this chat when the current turn ends.', since: 1 };
const held = { ...waiting, stage: 'held', label: 'report from Sol_Tobin is held, needs recovery',
  detail: 'This chat cannot take it right now (stopped, errored or quota-blocked). It is not retried automatically.' };
const floorBody = (label) => ({
  poll_seconds: 600, activity_states: true, counts: { figures: 1, rooms: 1, quiet: 0 },
  rooms: [{ id: PID, name: 'Report smoke', emoji: '', color: '', figures: [{
    session_id: SID, claude_session_id: 'csid-reportsmoke', state: 'working', reason: '', activity: '',
    bg_wait: '', task: 'smoke', character: null, name: 'Dave', name_from: 'default', avatar: '',
    provider: 'claude', model: '', model_from: 'project', started_at: '', age: '1m',
    trigger_type: 'manual', hivemind_id: '', subagents: [], allowance_exhausted: '',
    report_waiting: label }] }],
  bench: [], quiet: [],
});

const browser = await chromium.launch();
try {
  for (const [w, h] of [[1440, 900], [390, 844]]) {
    console.log(`\n== viewport ${w}x${h}`);
    const page = await browser.newPage({ viewport: { width: w, height: h } });
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message || String(e)));
    let floor = floorBody('report waiting from Sol_Tobin');
    await page.route('**/*', (route) => {
      const req = route.request();
      const url = new URL(req.url());
      if (url.pathname === `/api/project/${PID}/agent/status`)
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(status) });
      if (url.pathname === '/api/floor')
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(floor) });
      if (req.method() !== 'GET') return route.abort();
      if (url.hostname === 'localhost' && url.pathname === '/') {
        return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8',
          body: readFileSync(join(ROOT, 'static', 'index.html'), 'utf8') });
      }
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
    await page.goto(process.env.MC_SMOKE_BASE || 'http://localhost:5199/', { waitUntil: 'domcontentloaded' });
    const bridged = await page.waitForFunction(
      () => typeof window.paintReportsWaiting === 'function'
        && typeof window.fetchAgentStatus === 'function'
        && typeof window.openFloor === 'function',
      null, { timeout: 20000 }).then(() => true).catch(() => false);
    check(bridged, 'checkout JS loaded (paintReportsWaiting, fetchAgentStatus, openFloor bridged)');
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
    const snap = () => page.evaluate((sid) => {
      const el = document.getElementById(`report-waiting-${sid}`);
      if (!el) return { present: false };
      const r = el.getBoundingClientRect();
      return { present: true, text: el.textContent, stage: el.querySelector('[data-stage]')?.dataset.stage,
        left: r.left, right: r.right, width: r.width, height: r.height, vw: window.innerWidth,
        above: !!(el.compareDocumentPosition(document.getElementById(`agent-output-${sid}`))
          & Node.DOCUMENT_POSITION_FOLLOWING) };
    }, SID);
    const poll = async (reports) => {
      status = { sessions: [row(reports)] };
      await page.evaluate((pid) => window.fetchAgentStatus(pid), PID);
      return snap();
    };

    // 1. A queued report shows, above the transcript, inside the viewport.
    let s = await poll([waiting]);
    check(s.present && s.text.includes('report waiting from Sol_Tobin'),
      `marker shows "${(s.text || '').slice(0, 60)}"`);
    check(s.present && s.stage === 'waiting' && s.above, 'marker is a waiting-stage row placed above the transcript');
    check(s.present && s.width > 0 && s.height > 0 && s.left >= 0 && s.right <= s.vw + 1,
      `marker is visible and inside the ${w}px viewport (left=${Math.round(s.left)} right=${Math.round(s.right)})`);

    // 2. A parked report reads differently.
    s = await poll([held]);
    check(s.present && s.stage === 'held' && s.text.includes('is held'), 'a parked report says held, not waiting');

    // 3. Same list again leaves one notice; empty list removes it.
    s = await poll([held]);
    check((await page.$$(`#report-waiting-${SID}`)).length === 1, 'repeat poll keeps exactly one marker');
    s = await poll([]);
    check(!s.present, 'marker is removed once the report has been handed over');

    // 4. The Floor card carries the same line.
    await page.evaluate(() => { try { window.openFloor(); } catch (e) { /* surfaced by the wait below */ } });
    const card = await page.waitForSelector('.fl-report', { timeout: 10000 }).catch(() => null);
    check(!!card, 'Floor card shows a report line');
    if (card) {
      const f = await page.evaluate(() => {
        const r = document.querySelector('.fl-report').getBoundingClientRect();
        return { text: document.querySelector('.fl-report').textContent, left: r.left, right: r.right,
          w: r.width, vw: window.innerWidth };
      });
      check(f.text === 'report waiting from Sol_Tobin', `Floor line reads "${f.text}"`);
      check(f.w > 0 && f.right <= f.vw + 1, `Floor line is inside the ${w}px viewport (right=${Math.round(f.right)})`);
    }
    floor = floorBody('');
    await page.evaluate(() => window.refreshFloor());
    await page.waitForTimeout(300);
    check((await page.$$('.fl-report')).length === 0, 'Floor line disappears when nothing is waiting');

    const ours = errors.filter((e) => /report|waiting|floor|fork|_render/i.test(e));
    check(ours.length === 0, `no page errors from the marker/Floor code (${errors.length} page errors total)`);
    if (ours.length) console.error(ours.join('\n'));
    await page.close();
  }
} catch (e) {
  fail(`smoke aborted: ${e.message}`);
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(bad ? 1 : 0);
