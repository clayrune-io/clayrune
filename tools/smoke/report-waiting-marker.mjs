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
    const actions = [];
    await page.route('**/*', (route) => {
      const req = route.request();
      const url = new URL(req.url());
      if (req.method() === 'POST' && /\/agent\/delegation\/(retry|mark-handled)$/.test(url.pathname)) {
        const body = req.postDataJSON();
        actions.push({ action: url.pathname.split('/').pop(), ...body });
        status = { sessions: [row(status.sessions[0].reports_waiting.filter(r => r.event_id !== body.event_id))] };
        return route.fulfill({ contentType: 'application/json', body: '{"ok":true}' });
      }
      if (url.pathname === `/api/project/${PID}/agent/status`)
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(status) });
      if (url.pathname === '/api/floor')
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(floor) });
      if (url.pathname === '/api/projects') {
        const p = JSON.parse(readFileSync(join(ROOT, 'tools/smoke/fixtures/projects.json'), 'utf8'))[0];
        return route.fulfill({contentType: 'application/json', body: JSON.stringify([{...p, id: PID, name: 'Report smoke', project_path: '/smoke'}])});
      }
      if (url.pathname.endsWith('/agent/stream')) return route.abort();
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

    // Open before a dispatch/report is visible, then feed the server status.
    await page.evaluate(pid => window.openProjectModal(pid), PID);
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
      await page.evaluate(({pid, sid}) => {
        activeAgentTab[pid] = sid;
        refreshModalById(pid);
      }, {pid: PID, sid: SID});
      return snap();
    };

    // 1. A queued report shows, above the transcript, inside the viewport.
    let s = await poll([waiting]);
    check(s.present && s.text.includes('report waiting from Sol_Tobin'),
      `marker shows "${(s.text || '').slice(0, 60)}"`);
    check(s.present && s.stage === 'waiting' && s.above, 'marker is a waiting-stage row placed above the transcript');
    await page.evaluate(({pid,sid}) => { closeModalById(pid); openProjectModal(pid); switchAgentTab(pid,sid); }, {pid:PID,sid:SID});
    await page.waitForSelector(`#report-waiting-${SID}`);
    check((await snap()).present, 'open after dispatch: cached notice survives modal close/reopen');
    check(s.present && s.width > 0 && s.height > 0 && s.left >= 0 && s.right <= s.vw + 1,
      `marker is visible and inside the ${w}px viewport (left=${Math.round(s.left)} right=${Math.round(s.right)})`);

    // 2. A parked report reads differently.
    s = await poll([held]);
    check(s.present && s.stage === 'held' && s.text.includes('is held'), 'a parked report says held, not waiting');

    // 3. Same list again leaves one notice; empty list removes it.
    s = await poll([held]);
    check((await page.$$(`#report-waiting-${SID}`)).length === 1, 'repeat poll keeps exactly one marker');

    // Multiple parked rows occupy one line until reviewed, even after a modal rebuild.
    const reviews = Array.from({ length: 13 }, (_, n) => ({ ...held,
      event_id: `child${n}:turn:1`, table: 'inbox', stage: 'review',
      label: `report from Agent ${n} may not have arrived, needs review` }));
    s = await poll(reviews);
    const notice = page.locator(`#report-waiting-${SID}`);
    const summary = notice.locator('.report-waiting-summary');
    check(await summary.getAttribute('aria-expanded') === 'false', '13 reports start collapsed');
    check(await notice.locator('.report-waiting-rows').isHidden(), 'collapsed report rows are hidden');
    // A status refresh can replace the notice between locator resolution and
    // evaluation. Measure a connected, laid-out node in the same browser task.
    const collapsedHandle = await page.waitForFunction(sid => {
      const el = document.querySelector(`#report-waiting-${sid} .report-waiting-summary`);
      if (!el || !el.isConnected) return null;
      const height = el.getBoundingClientRect().height;
      const line = parseFloat(getComputedStyle(el).lineHeight);
      if (!height || !Number.isFinite(line)) return null;
      return { height, line, notice: el.closest('.report-waiting-notice').getBoundingClientRect().height,
        nowrap: getComputedStyle(el).whiteSpace === 'nowrap' };
    }, SID);
    const collapsed = await collapsedHandle.jsonValue();
    check(collapsed.nowrap && collapsed.height <= collapsed.line + 1 && collapsed.notice <= collapsed.line + 19,
      `collapsed notice is one line (text=${collapsed.height}px line=${collapsed.line}px notice=${collapsed.notice}px)`);
    await summary.click();
    check(await notice.locator('.report-waiting-row').count() === 13, 'review expands all 13 rows');
    await page.evaluate(pid => refreshModalById(pid), PID);
    check(await summary.getAttribute('aria-expanded') === 'true', 'expanded state survives refreshModalById');
    await poll(reviews);
    check(await summary.getAttribute('aria-expanded') === 'true', 'poll preserves expanded state');
    await notice.locator('[data-action="mark-handled"]').first().click();
    await page.waitForFunction(() => document.querySelector('.report-waiting-summary')?.textContent.startsWith('12 reports'));
    check(actions[0]?.action === 'mark-handled' && !actions[0]?.reviewed_resolution,
      'Mark handled posts the event without requesting re-delivery');
    page.once('dialog', d => d.dismiss());
    await notice.locator('[data-action="retry"]').first().click();
    check(actions.length === 1, 'cancelled Retry sends nothing');
    page.once('dialog', d => d.accept());
    await notice.locator('[data-action="retry"]').first().click();
    await page.waitForFunction(() => document.querySelector('.report-waiting-summary')?.textContent.startsWith('11 reports'));
    check(actions[1]?.action === 'retry' && actions[1]?.table === 'inbox' && actions[1]?.reviewed_resolution,
      'Retry sends reviewed resolution only after confirmation');
    await summary.click();
    await page.evaluate(({pid,sid}) => { closeModalById(pid); openProjectModal(pid); switchAgentTab(pid,sid); }, {pid:PID,sid:SID});
    check(await summary.getAttribute('aria-expanded') === 'false', 'collapsed notice survives modal close/reopen');
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
