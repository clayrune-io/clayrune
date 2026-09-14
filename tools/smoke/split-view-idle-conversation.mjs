#!/usr/bin/env node
/**
 * Split view, hermetic half (the real-server half is split-view-real.mjs).
 *
 * WHY THIS EXISTS
 * ----------------
 * Ron, 2026-09-14: "split view never worked". Measured against the real app:
 *   1. The ◫ button rendered only for LIVE rows (`c.live &&`) and sat at
 *      opacity:0 until hover, so on most rows it did not exist and on the rest
 *      it was invisible. openInSplit() also refused any non-live chat.
 *   2. Each pane's title measured 0px wide (max-width:40% + flex-shrink), so
 *      two open panes could not be told apart.
 *   3. sendFollowup() moved activeAgentTab to the server's new session id even
 *      when the send came from the SPLIT pane, so a send that the server
 *      answered under a fresh id replaced the primary pane with pane 2's chat.
 * This test pins all three with canned server responses, so it runs anywhere
 * with no live agents. It asserts behaviour (which conversation's messages
 * are in which pane, which session a pane's send targets), not DOM presence.
 *
 * RUN: cd tools/smoke && node split-view-idle-conversation.mjs
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
const SHOT = process.env.SPLIT_SHOT || '';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, pass, failMsg) => (cond ? ok(pass) : fail(failMsg));

const RECON = {
  mcA: { task: 'Alpha thread', claude_session_id: 'csidA', log_lines: ['> Ron: alpha question', 'ALPHA-ANSWER'] },
  mcB: { task: 'Bravo thread', claude_session_id: 'csidB', log_lines: ['> Ron: bravo question', 'BRAVO-ANSWER'] },
};

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  const sends = [];
  let sendReply = (body) => ({ ok: true, route: 'followup', session_id: body.session_id });

  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const json = (o) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(o) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') {
      const patched = JSON.parse(PROJECTS_JSON);
      patched[0].project_path = '/smoke/alpha';   // empty path renders "Set project_path", not the rail
      return json(patched);
    }
    if (path === '/api/config') return json({});
    if (/\/agent\/status$/.test(path)) return json({ sessions: [] });
    const m = path.match(/\/session\/(mc[AB])\/reconstruct$/);
    if (m) return json({ started_at: '2026-09-14T00:00:00Z', ...RECON[m[1]] });
    if (/\/agent\/send$/.test(path)) {
      const body = JSON.parse(req.postData() || '{}');
      sends.push(body);
      return json(sendReply(body));
    }
    return route.abort();
  });

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });

  await page.evaluate((pid) => {
    conversationsCache[pid] = [
      { claude_session_id: 'csidA', mc_session_id: 'mcA', live: false, status: 'completed', label: 'Alpha thread', turns: 1 },
      { claude_session_id: 'csidB', mc_session_id: 'mcB', live: false, status: 'completed', label: 'Bravo thread', turns: 1 },
    ];
    agentLogCache[pid] = [];
    openProjectModal(pid);
  }, PID);
  await page.waitForSelector('.conv-row[data-csid="csidB"]', { timeout: 10000 });

  // Open A (finished, not live) as the primary pane.
  await page.click('.conv-row[data-csid="csidA"]');
  await page.waitForFunction((pid) => activeAgentTab[pid] === 'mcA', PID, { timeout: 5000 }).catch(() => {});
  check(await page.evaluate((pid) => activeAgentTab[pid], PID) === 'mcA',
    'finished chat A opened as the primary pane', 'chat A did not become the primary pane');

  // 1. Discoverability: B is finished (not live) and must still offer a visible button.
  const btn = await page.evaluate(() => {
    const b = document.querySelector('.conv-row[data-csid="csidB"] .conv-split');
    return b ? { opacity: parseFloat(getComputedStyle(b).opacity), w: b.getBoundingClientRect().width } : null;
  });
  check(!!btn, 'finished row B offers the split button', 'finished row B has no split button (live-only gate is back)');
  check(btn && btn.opacity > 0 && btn.w > 0, `split button visible without hover (opacity ${btn && btn.opacity})`,
    `split button invisible without hover (opacity ${btn && btn.opacity}, width ${btn && btn.w})`);

  await page.click('.conv-row[data-csid="csidB"] .conv-split');
  await page.waitForSelector('.agent-split-pane[data-sid="mcB"]', { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(400);
  // Real split panes are usually idle chats, whose header carries a Stop button
  // too. That is the crowded header where the title collapsed to 0px, so
  // measure it in that state and at the modal's default size.
  await page.evaluate(() => {
    for (const sid of ['mcA', 'mcB']) agentStatusCache[sid].status = 'idle';
    refreshModal();
  });
  await page.waitForTimeout(300);

  const panes = await page.evaluate(() => Object.fromEntries([...document.querySelectorAll('.agent-split-pane')].map((p) => [p.dataset.sid, {
    text: p.querySelector('.agent-output')?.innerText || '',
    labelW: p.querySelector('.agent-split-label')?.getBoundingClientRect().width || 0,
    label: p.querySelector('.agent-split-label')?.textContent || '',
  }])));
  check(panes.mcA && panes.mcB, 'two panes open: A primary, B split', `panes open: ${Object.keys(panes).join(', ') || 'none'}`);
  if (!panes.mcA || !panes.mcB) throw new Error('split did not open');

  // Content must come from the RIGHT conversation, not just exist.
  check(panes.mcB.text.includes('BRAVO-ANSWER') && !panes.mcB.text.includes('ALPHA-ANSWER'),
    'pane 2 shows conversation B’s history and none of A’s', `pane 2 text wrong: ${JSON.stringify(panes.mcB.text.slice(0, 120))}`);
  check(panes.mcA.text.includes('ALPHA-ANSWER') && !panes.mcA.text.includes('BRAVO-ANSWER'),
    'pane 1 still shows conversation A’s history', `pane 1 text wrong: ${JSON.stringify(panes.mcA.text.slice(0, 120))}`);

  // 2. The two panes must be tell-apart-able.
  const ph = await page.evaluate(() => [...document.querySelectorAll('.agent-split-pane textarea')].map((t) => t.placeholder));
  check(ph.length === 2 && ph.every((p) => p === 'Reply…'), 'both reply boxes use the split placeholder',
    `reply placeholders: ${JSON.stringify(ph)} (primary kept its single-view text?)`);
  check(panes.mcA.labelW > 0 && panes.mcB.labelW > 0,
    `pane titles visible (${Math.round(panes.mcA.labelW)}px / ${Math.round(panes.mcB.labelW)}px)`,
    `pane title collapsed to 0px (A ${panes.mcA.labelW}px, B ${panes.mcB.labelW}px)`);

  // Send from pane 2 must target B and echo into pane 2 only.
  await page.fill('#agent-followup-mcB', 'pane two message');
  await page.click('.agent-split-pane[data-sid="mcB"] .btn-dispatch');
  await page.waitForFunction(() => document.querySelector('#agent-output-mcB')?.innerText.includes('pane two message'), null, { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(300);
  check(sends.length === 1 && sends[0].session_id === 'mcB', 'pane 2 Send POSTs session_id=mcB',
    `pane 2 Send targeted ${JSON.stringify(sends.map((s) => s.session_id))}`);
  const echo = await page.evaluate(() => ({
    b: document.querySelector('#agent-output-mcB')?.innerText.includes('pane two message'),
    a: document.querySelector('#agent-output-mcA')?.innerText.includes('pane two message'),
  }));
  check(echo.b && !echo.a, 'pane 2’s message appears in pane 2, not pane 1', `echo landed wrong (pane1=${echo.a}, pane2=${echo.b})`);

  // 3. Server answers a pane-2 send under a NEW session id (revive-non-claude /
  //    fresh dispatch routes). Pane 2 must follow it; pane 1 must not move.
  sendReply = () => ({ ok: true, route: 'dispatch', session_id: 'mcB2' });
  await page.fill('#agent-followup-mcB', 'second pane two message');
  await page.click('.agent-split-pane[data-sid="mcB"] .btn-dispatch');
  await page.waitForFunction((pid) => splitAgentTab[pid] === 'mcB2' || activeAgentTab[pid] === 'mcB2', PID, { timeout: 5000 }).catch(() => {});
  const after = await page.evaluate((pid) => ({ active: activeAgentTab[pid], split: splitAgentTab[pid],
    sids: [...document.querySelectorAll('.agent-split-pane')].map((p) => p.dataset.sid) }), PID);
  check(after.active === 'mcA' && after.split === 'mcB2',
    'new session id from a pane-2 send retargets pane 2; pane 1 stays on A',
    `pane-2 send hijacked the layout: active=${after.active} split=${after.split} panes=${after.sids.join(',')}`);

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
