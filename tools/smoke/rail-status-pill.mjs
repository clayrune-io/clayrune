#!/usr/bin/env node
/**
 * MC-940 regression: the conversation rail's live-status pill must repaint in
 * place, WITHOUT a full modal rebuild.
 *
 * WHY THIS EXISTS
 * ---------------
 * The Working…/Waiting pill was produced only inside modalContentHTML(), and
 * turn_start/turn_complete deliberately skip refreshModal() (it recreates the
 * chat textarea every turn — the measured cause of the mobile IME death,
 * ~205ms/keystroke). Nothing else repainted a rail row, so a row you were
 * sitting on never showed that its agent had started, and never cleared once
 * the turn ended. updateRailRowStatus() is the in-place fix; this test drives
 * the REAL running app so the shipped renderer + updater are exercised
 * together (the data-* attributes it depends on live in the row template).
 *
 * RUN: node rail-status-pill.mjs      (needs MC on localhost:5199)
 */
import { chromium } from 'playwright';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const ok = (m) => console.log('  \u2713 ' + m);
let bad = 0;
const fail = (m) => { console.error('  \u2717 ' + m); bad++; };

const ctx = await chromium.launchPersistentContext(mkdtempSync(join(tmpdir(), 'mc940-')), {
  args: ['--remote-debugging-port=9773'], viewport: { width: 1400, height: 900 },
});
try {
  const page = ctx.pages()[0] || await ctx.newPage();
  await page.goto('http://localhost:5199/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 20000 });

  const bridged = await page.evaluate(() => typeof window.updateRailRowStatus === 'function');
  bridged ? ok('window.updateRailRowStatus is bridged') : fail('bridge missing — is the page serving stale JS?');
  if (!bridged) throw new Error('no bridge');

  // Open a project so the rail renders real rows.
  await page.click('#projects-col .card');
  await page.waitForSelector('.conv-row[data-csid]', { timeout: 20000 });

  const before = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('.conv-row[data-csid]')];
    // Pick a row that is NOT already live — a genuinely working row starts with
    // the badge showing, so it cannot prove "appears" or "restores to resting".
    const idle = rows.filter(r => !r.querySelector('.conv-live-badge'));
    const target = idle[0];
    return { order: rows.map(r => r.dataset.csid), n: rows.length, idleN: idle.length,
             target: target?.dataset.csid,
             attrs: [{ ts: target?.dataset.tsRelative, rest: target?.dataset.restStatus }] };
  });
  before.n >= 2 ? ok(`rail rendered ${before.n} rows (${before.idleN} idle)`) : fail('need >= 2 rows to prove order is preserved');
  if (!before.target) { fail('no idle row to test against — every row is live'); throw new Error('no idle row'); }
  const a = before.attrs[0];
  (a.ts !== undefined && a.rest !== undefined) ? ok(`row carries resting state (ts="${a.ts}" status="${a.rest}")`)
    : fail('row is missing data-ts-relative / data-rest-status — updater cannot restore it');

  // Drive the updater exactly as the SSE handlers do: mutate the cache, call it.
  const res = await page.evaluate((csid) => {
    const row = () => document.querySelector(`.conv-row[data-csid="${csid}"]`);
    const snap = () => ({
      cls: row().className,
      time: row().querySelector('.conv-time').textContent.trim(),
      dot: row().querySelector('.conv-name .agent-status-dot')?.className || '',
      display: row().style.display,
      order: [...document.querySelectorAll('.conv-row[data-csid]')].map(r => r.dataset.csid).join(','),
    });
    const rest = snap();
    const SID = '__mc940_probe__';
    // `agentStatusCache` is a top-level `let` in a classic script: a global
    // BINDING but not a window property (the ES-module/global gotcha). Poke it
    // by bare name, exactly as the SSE handlers do.
    const pid = Object.values(agentStatusCache)[0]?.projectId || null;
    agentStatusCache[SID] = { projectId: pid, claudeSessionId: csid, status: 'running',
                              waitingForQuestion: false, waitingForPlanApproval: false };
    window.updateRailRowStatus(SID);
    const working = snap();
    agentStatusCache[SID].status = 'idle';
    window.updateRailRowStatus(SID);
    const done = snap();
    delete agentStatusCache[SID];
    return { rest, working, done };
  }, before.target);

  res.working.time.includes('Working') ? ok('turn_start: pill appeared in place, no rebuild')
    : fail(`turn_start: no pill — .conv-time is "${res.working.time}"`);
  res.working.cls.includes('conv-live-working') ? ok('turn_start: row carries conv-live-working')
    : fail('turn_start: conv-live-working class not applied');
  res.working.dot.includes('running') ? ok('turn_start: status dot flipped to running')
    : fail(`turn_start: dot is "${res.working.dot}"`);

  !res.done.time.includes('Working') ? ok('turn_complete: pill CLEARED (the stale-Working half)')
    : fail('turn_complete: pill did not clear — the more misleading half of MC-940');
  res.done.time === res.rest.time ? ok(`turn_complete: timestamp restored ("${res.done.time}")`)
    : fail(`turn_complete: timestamp not restored — "${res.done.time}" vs "${res.rest.time}"`);
  !res.done.cls.includes('conv-live-working') ? ok('turn_complete: conv-live-working removed')
    : fail('turn_complete: conv-live-working still present');
  res.done.dot === res.rest.dot ? ok('turn_complete: dot restored to resting status')
    : fail(`turn_complete: dot "${res.done.dot}" vs resting "${res.rest.dot}"`);

  (res.working.order === res.rest.order && res.done.order === res.rest.order)
    ? ok('rail order UNCHANGED across both transitions (no re-sort under the cursor)')
    : fail('rail re-sorted — rows moved under the user mid-turn');
  (res.working.display === res.rest.display && res.done.display === res.rest.display)
    ? ok('row.style.display never written (stays _applyRailFilter\u2019s property)')
    : fail('updater wrote style.display — breaks search filtering');
} finally {
  await ctx.close();
}
console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
