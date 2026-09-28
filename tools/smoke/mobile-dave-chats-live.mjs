#!/usr/bin/env node
// Live-DOM regression for Ron, 2026-09-28 (phone): "why do I now see only one
// Dave conversation?" Boots the REAL :5199 dashboard at a phone viewport
// (390x844) against LIVE conversations/agent-log data (no mocked API), swaps
// in master's conversation.js vs this branch's via page.route (so no server
// restart is needed to prove both sides), and counts actual Dave rows
// rendered in the real DOM for the Chats tab.
//
// Requires a live Mission Control server on :5199.
import { chromium } from 'playwright';
import { execSync } from 'node:child_process';
import assert from 'node:assert/strict';

const FIXED_JS = execSync('git show HEAD:static/js/conversation.js', { cwd: new URL('../..', import.meta.url), encoding: 'utf8' });
const MASTER_JS = execSync('git show master:static/js/conversation.js', { cwd: new URL('../..', import.meta.url), encoding: 'utf8' });

async function countMobileDaveChats(sourceJs) {
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await ctx.newPage();
    await page.route('**/static/js/conversation.js*', route =>
      route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: sourceJs }));
    await page.goto('http://localhost:5199/', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1200);
    await page.evaluate(() => window.openProjectModal && window.openProjectModal('mission_control'));
    await page.waitForTimeout(2200);
    await page.evaluate(() => window.setRailMode && window.setRailMode('mission_control', 'chats'));
    await page.waitForTimeout(300);
    const rows = await page.evaluate(() =>
      Array.from(document.querySelectorAll('.conv-row:not(.channel-row)'))
        .map(n => (n.querySelector('.conv-sub')?.textContent || '').trim()));
    return rows.filter(sub => sub.startsWith('Dave')).length;
  } finally {
    await browser.close();
  }
}

// Ground truth: how many Dave sessions actually exist right now, per the live
// agent log (the source the fix merges in). Manual-trigger only — the
// schedule-trigger head is filtered by design (noise regex), on both sides.
const agentLogRaw = await (await fetch('http://localhost:5199/api/project/mission_control/agent/log')).json();
const agentLog = Array.isArray(agentLogRaw) ? agentLogRaw : (agentLogRaw.entries || []);
const liveDaveManualCount = new Set(
  agentLog
    .filter(e => e.character && e.character.agent_name === 'Dave' && e.trigger_type !== 'schedule')
    .map(e => e.mc_session_id || e.session_id)
).size;
assert.ok(liveDaveManualCount >= 2, `expected >=2 live manual Dave sessions to make this test meaningful, found ${liveDaveManualCount}`);

const masterCount = await countMobileDaveChats(MASTER_JS);
const fixedCount = await countMobileDaveChats(FIXED_JS);

console.log(`live manual Dave sessions: ${liveDaveManualCount}, mobile Chats tab — master: ${masterCount}, fixed: ${fixedCount}`);

assert.ok(masterCount < liveDaveManualCount, `master should UNDER-count Dave rows on mobile (bug reproduced) — got ${masterCount} of ${liveDaveManualCount}`);
assert.equal(fixedCount, liveDaveManualCount, `fixed branch should show all ${liveDaveManualCount} live manual Dave rows on mobile — got ${fixedCount}`);

console.log('PASS mobile Chats tab live DOM: master under-counts Dave rows, fixed branch shows all of them');
