#!/usr/bin/env node
/**
 * Split view ACROSS PROJECTS, hermetic (no live server; pairs with
 * split-view-idle-conversation.mjs, which only covers two sessions of the
 * SAME project).
 *
 * WHY THIS EXISTS
 * ----------------
 * Backlog 321d8efc, Ron: "Split view should be allowed between different
 * agents and not just single agent." dd80266 (2026-09-14) already let the
 * 2nd pane be any conversation of the CURRENT project. The gap this pins:
 * the "View another project's chat beside this…" picker (rail header, desktop only)
 * must open a conversation OWNED by a different project, and every action
 * in that pane — render, Send — must target that project, not the primary's.
 * A regression here would either fail to render pane 2's own history, or
 * (worse, silently) POST a pane-2 send to the PRIMARY project's endpoint.
 *
 * RUN: cd tools/smoke && node split-view-cross-project.mjs
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const PROJECTS_JSON = readFileSync(resolve(__dirname, 'fixtures', 'projects.json'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const PID_A = 'smoke_alpha';
const PID_B = 'smoke_beta';
const SHOT = process.env.SPLIT_SHOT || '';

const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, pass, failMsg) => (cond ? ok(pass) : fail(failMsg));

// mcA belongs to smoke_alpha (the primary project); mcC belongs to smoke_beta
// (a DIFFERENT project) — reconstruct is keyed by projectId+mcSessionId, so
// the same mcSessionId space is reused across projects on purpose, to prove
// the pane resolves through the right project, not by session id alone.
const RECON = {
  [`${PID_A}/mcA`]: { task: 'Alpha thread', claude_session_id: 'csidA', log_lines: ['> Ron: alpha question', 'ALPHA-ANSWER'] },
  [`${PID_B}/mcC`]: { task: 'Beta thread', claude_session_id: 'csidC', log_lines: ['> Ron: beta question', 'BETA-ANSWER'] },
};

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  const sends = [];

  await page.route('**/*', (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const json = (o) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(o) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') {
      const patched = JSON.parse(PROJECTS_JSON);
      patched[0].project_path = '/smoke/alpha';
      patched[1].project_path = '/smoke/beta';
      return json(patched);
    }
    if (path === '/api/config') return json({});
    if (/\/agent\/status$/.test(path)) return json({ sessions: [] });  // reconstruct seeds the cache instead
    const m = path.match(/\/api\/project\/([^/]+)\/session\/([^/]+)\/reconstruct$/);
    if (m) {
      const rec = RECON[`${m[1]}/${m[2]}`];
      if (rec) return json({ started_at: '2026-09-14T00:00:00Z', ...rec });
      return route.fulfill({ status: 404, body: '{}' });
    }
    if (/\/agent\/send$/.test(path)) {
      const body = JSON.parse(req.postData() || '{}');
      const pm = path.match(/\/api\/project\/([^/]+)\/agent\/send$/);
      sends.push({ projectId: pm ? pm[1] : null, ...body });
      return json({ ok: true, route: 'followup', session_id: body.session_id });
    }
    return route.abort();
  });

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });

  await page.evaluate(({ pidA, pidB }) => {
    conversationsCache[pidA] = [
      { claude_session_id: 'csidA', mc_session_id: 'mcA', live: false, status: 'completed', label: 'Alpha thread', turns: 1 },
    ];
    agentLogCache[pidA] = [];
    // Pre-seed the cross-project picker's source of truth: a recent
    // conversation the client already knows about from smoke_beta, exactly
    // as agentHistory would hold it after that project's own status poll —
    // the picker must not need a fresh fetch to list it.
    agentHistory.push({ projectId: pidB, sessionId: 'mcC', projectName: 'Smoke Test Beta', task: 'Beta thread', status: 'completed', startedAt: '2026-09-14T00:00:00Z' });
    openProjectModal(pidA);
  }, { pidA: PID_A, pidB: PID_B });
  await page.waitForSelector('.conv-row[data-csid="csidA"]', { timeout: 10000 });

  // Open A as the primary pane.
  await page.click('.conv-row[data-csid="csidA"]');
  await page.waitForFunction((pid) => activeAgentTab[pid] === 'mcA', PID_A, { timeout: 5000 }).catch(() => {});
  check(await page.evaluate((pid) => activeAgentTab[pid], PID_A) === 'mcA',
    'chat A (project alpha) opened as the primary pane', 'chat A did not become the primary pane');

  // The cross-project picker button only shows once a primary pane is open.
  const btnVisible = await page.evaluate(() => {
    const b = document.querySelector('.agent-rail-crossproj-btn');
    return b ? parseFloat(getComputedStyle(b).opacity) > 0 : false;
  });
  check(btnVisible, `"View another project's chat beside this…" button visible`, 'cross-project split button missing or hidden');

  await page.click('.agent-rail-crossproj-btn');
  await page.waitForSelector('.agent-rail-crossproj-row', { timeout: 5000 });
  const rowText = await page.evaluate(() => document.querySelector('.agent-rail-crossproj-row')?.innerText || '');
  check(/smoke test beta/i.test(rowText), `picker lists the other project (row: ${JSON.stringify(rowText)})`,
    `picker row does not name the source project: ${JSON.stringify(rowText)}`);

  await page.click('.agent-rail-crossproj-row');
  await page.waitForSelector('.agent-split-pane[data-sid="mcC"]', { timeout: 5000 }).catch(() => {});

  const panes = await page.evaluate(() => Object.fromEntries([...document.querySelectorAll('.agent-split-pane')].map((p) => [p.dataset.sid, {
    text: p.querySelector('.agent-output')?.innerText || '',
    hasCompose: !!p.querySelector('.agent-task-input'),
  }])));
  check(!!panes.mcA && !!panes.mcC, 'two panes open: alpha/mcA primary, beta/mcC split', `panes open: ${Object.keys(panes).join(', ') || 'none'}`);
  if (!panes.mcA || !panes.mcC) throw new Error('cross-project split did not open');

  check(panes.mcC.text.includes('BETA-ANSWER') && !panes.mcC.text.includes('ALPHA-ANSWER'),
    'pane 2 shows project beta’s conversation and none of alpha’s', `pane 2 text wrong: ${JSON.stringify(panes.mcC.text.slice(0, 120))}`);
  check(panes.mcA.text.includes('ALPHA-ANSWER') && !panes.mcA.text.includes('BETA-ANSWER'),
    'pane 1 still shows project alpha’s conversation', `pane 1 text wrong: ${JSON.stringify(panes.mcA.text.slice(0, 120))}`);

  // The regression this test exists to catch: a send from the cross-project
  // pane must POST to /api/project/smoke_beta/... (the session's OWN
  // project), not /api/project/smoke_alpha/... (the primary's project).
  await page.fill('#agent-followup-mcC', 'pane two cross-project message');
  await page.click('.agent-split-pane[data-sid="mcC"] .btn-dispatch');
  await page.waitForFunction(() => document.querySelector('#agent-output-mcC')?.innerText.includes('pane two cross-project message'), null, { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(300);
  check(sends.length === 1 && sends[0].projectId === PID_B && sends[0].session_id === 'mcC',
    `cross-project pane Send POSTed to project ${PID_B}, session_id=mcC`,
    `cross-project pane Send targeted ${JSON.stringify(sends.map((s) => [s.projectId, s.session_id]))}`);
  const echo = await page.evaluate(() => ({
    c: document.querySelector('#agent-output-mcC')?.innerText.includes('pane two cross-project message'),
    a: document.querySelector('#agent-output-mcA')?.innerText.includes('pane two cross-project message'),
  }));
  check(echo.c && !echo.a, 'pane 2’s message appears in pane 2 (beta), not pane 1 (alpha)', `echo landed wrong (pane1=${echo.a}, pane2=${echo.c})`);

  // The ✕ on the CROSS-PROJECT pane must close it. It used to call
  // closeSplitPane with the pane's own project (beta), but split state is
  // keyed by the host (alpha), so the button did nothing.
  await page.click('.agent-split-pane[data-sid="mcC"] .agent-split-close');
  await page.waitForFunction(() => !document.querySelector('.agent-split-pane[data-sid="mcC"]'), null, { timeout: 5000 }).catch(() => {});
  const afterClose = await page.evaluate((pid) => ({
    splitGone: !document.querySelector('.agent-split-pane'),
    split: splitAgentTab[pid] || null,
    active: activeAgentTab[pid] || null,
  }), PID_A);
  check(afterClose.splitGone && !afterClose.split && afterClose.active === 'mcA',
    'cross-project pane ✕ closes it; alpha/mcA stays as the single chat',
    `close did not work: ${JSON.stringify(afterClose)}`);

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
