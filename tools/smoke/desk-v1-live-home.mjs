#!/usr/bin/env node
/**
 * Desk v1 — Desk Home shows only live campaigns (Ron 2026-10-01), against a
 * fake server, `desk_v1_live` ON.
 *
 * Before: Home listed EVERY Clayrune project as a block, each empty one with
 * "No campaigns yet in this project" and a "Pick who plans" chip. Now:
 *   1. 3 projects, only one with a running + a draft campaign   -> ONE block,
 *      2 rows, no empty-project text, ONE planner chip.
 *   2. a project whose only campaign ended (completed/archived)  -> no block;
 *      a quiet "Ended (N)" link expands them (no chip, no New campaign link).
 *   3. zero live campaigns anywhere                              -> ONE page
 *      empty state with the New campaign action; no per-project box, no
 *      legend/column header; no Ended link when nothing ended.
 *   4. an empty project is still reachable: the Projects picker opens its
 *      project page, which carries New campaign and the planner card.
 *
 * RUN   cd tools/smoke && node desk-v1-live-home.mjs
 *   DESK_HOME_SHOTS=<dir> also writes 1440px screenshots there.
 *   DESK_HOME_PROJECTS=<json file of [{id,name}]> adds a shot of those projects
 *   with no campaigns (the real-workspace shape) and with a few campaigns.
 */
import { readFileSync, readdirSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const SHOTS = process.env.DESK_HOME_SHOTS;
if (SHOTS) mkdirSync(SHOTS, { recursive: true });

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

const PROJECT_NAMES = { alpha: 'Alpha app', beta: 'Beta tool', gamma: 'Gamma site' };

// `projects`: [{id, name}]; `camps`: [{id, projectId, state, title}]. Campaign
// bodies are cloned from the fixture file so they carry every field the
// surfaces read; only id / project / state / title are overridden.
function workspace(projects, camps) {
  const fx = loadFixtures();
  const base = fx.campaigns.find((c) => c.id === 'camp-1');
  return {
    ...workspaceFromFixtures(fx),
    projects: projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })),
    campaigns: camps.map((c) => {
      const copy = JSON.parse(JSON.stringify(base));
      copy.id = c.id; copy.projectId = c.projectId; copy.state = c.state;
      copy.plan.title = c.title; delete copy.map; delete copy.subject;
      return copy;
    }),
    pieces: [],
  };
}

async function newPage(browser, ws, viewport = { width: 1440, height: 900 }) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return J(ws.projects.map((p) => ({
      id: p.id, name: p.name, status: 'active', domain: 'general', emoji: '🧪', description: '', summary: '', current_task: 'Idle',
      next_action: '', blocked: false, blocked_reason: null, activity_log: [], backlog: [], project_path: '/smoke/' + p.id,
      last_updated: '2026-09-09T00:00:00Z', last_updated_relative: 'today', last_completed: null, live_agent: null, display_order: 0,
      provider: 'claude', use_streaming_agent: true, distiller_mode: 'proposed', distiller_min_recurrence: 3,
      distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3, distiller_max_explorations_per_session: 3,
      distiller_min_turns: 5, distiller_skip_errors: true, roster: [],
    })));
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true, user_timezone: '' });
    if (path === '/api/characters') return J([{ name: 'claydo', scope: 'global', agent_name: 'Claydo', avatar: '' }]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (path === '/api/desk/workspace') return J(workspaceBody(ws));
    if (path.startsWith('/api/desk/')) return J({});
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.waitForSelector('#desk-v1-home-board .desk-v1-home-board-blocks, #desk-v1-home-board .desk-v1-home-empty', { timeout: 8000 });
  return { ctx, page, pageErrors };
}
const workspaceBody = (ws) => ws.body;

const board = (page) => page.evaluate(() => {
  const host = document.getElementById('desk-v1-home-board');
  const blocks = Array.from(host.querySelectorAll('.desk-v1-home-board-blocks > .desk-v1-home-block'));
  const toggle = host.querySelector('.desk-v1-home-ended-toggle');
  return {
    blocks: blocks.map((b) => ({
      id: b.dataset.projectId,
      rows: Array.from(b.querySelectorAll('.desk-v1-home-row-title')).map((t) => t.textContent.trim()),
      chip: !!b.querySelector('.desk-v1-home-block-agent'),
    })),
    emptyText: (host.querySelector('.desk-v1-home-empty') || {}).textContent?.replace(/\s+/g, ' ').trim() || null,
    emptyNewcamp: !!host.querySelector('.desk-v1-home-empty-newcamp'),
    legend: !!host.querySelector('.desk-v1-home-legend'),
    colHead: !!host.querySelector('.desk-v1-home-board-head'),
    oldText: /No campaigns yet in this project/.test(host.textContent),
    chips: host.querySelectorAll('.desk-v1-home-block-agent').length,
    toggle: toggle ? toggle.textContent.trim() : null,
    endedBlocks: Array.from(host.querySelectorAll('.desk-v1-home-ended-block')).map((b) => ({
      name: b.querySelector('.desk-v1-home-block-name').textContent.trim(),
      rows: Array.from(b.querySelectorAll('.desk-v1-home-row-title')).map((t) => t.textContent.trim()),
      chip: !!b.querySelector('.desk-v1-home-block-agent'), newcamp: !!b.querySelector('.desk-v1-home-block-newcamp'),
    })),
  };
});

const projs = (...ids) => ids.map((id) => ({ id, name: PROJECT_NAMES[id] || id }));
const mk = (ws, projects, camps) => { ws.projects = projects; ws.body = workspace(projects, camps); return ws; };

// 1: three projects, only Alpha has live campaigns (a running + a draft); Beta's
// only campaign completed; Gamma has none.
async function mixed(browser) {
  const ws = mk({}, projs('alpha', 'beta', 'gamma'), [
    { id: 'a1', projectId: 'alpha', state: 'active', title: 'Alpha launch' },
    { id: 'a2', projectId: 'alpha', state: 'draft', title: 'Alpha draft' },
    { id: 'b1', projectId: 'beta', state: 'completed', title: 'Beta done' },
  ]);
  const { ctx, page, pageErrors } = await newPage(browser, ws);
  let b = await board(page);
  (b.blocks.length === 1 && b.blocks[0].id === 'alpha' && b.blocks[0].rows.length === 2)
    ? ok('3 projects, one with a running + a draft campaign -> ONE group with 2 rows')
    : fail('groups: ' + JSON.stringify(b.blocks));
  (!b.oldText && b.chips === 1 && b.blocks[0].chip) ? ok('no "No campaigns yet" box; the planner chip shows only on the displayed group') : fail('chips/old text: ' + JSON.stringify({ old: b.oldText, chips: b.chips }));
  (b.legend && b.colHead) ? ok('legend + column header render when there are rows') : fail('legend/colHead missing with rows');
  (b.toggle === 'Ended (1)' && b.endedBlocks.length === 0) ? ok('"Ended (1)" link is present and collapsed') : fail('toggle: ' + JSON.stringify({ t: b.toggle, e: b.endedBlocks }));
  await page.click('.desk-v1-home-ended-toggle');
  b = await board(page);
  (b.endedBlocks.length === 1 && b.endedBlocks[0].name === 'Beta tool' && b.endedBlocks[0].rows[0] === 'Beta done' && !b.endedBlocks[0].chip && !b.endedBlocks[0].newcamp)
    ? ok('expanding Ended shows the completed campaign under its project, with no planner chip or New campaign link')
    : fail('ended expanded: ' + JSON.stringify(b.endedBlocks));
  (b.blocks.length === 1) ? ok('expanding Ended adds nothing to the live board') : fail('live board changed on expand: ' + JSON.stringify(b.blocks));
  if (SHOTS) await page.screenshot({ path: resolve(SHOTS, 'home-with-campaigns.png') });
  await page.click('.desk-v1-home-ended-toggle');
  b = await board(page);
  b.endedBlocks.length === 0 ? ok('the Ended link collapses again') : fail('did not collapse');
  pageErrors.length ? fail('page errors: ' + pageErrors.join(' | ')) : ok('no uncaught page errors');
  await ctx.close();
}

// 2: one project whose ONLY campaign is archived (stored `dropped`).
async function onlyEnded(browser) {
  const ws = mk({}, projs('alpha', 'gamma'), [{ id: 'a1', projectId: 'alpha', state: 'archived', title: 'Alpha dropped' }]);
  const { ctx, page, pageErrors } = await newPage(browser, ws);
  const b = await board(page);
  (b.blocks.length === 0 && b.toggle === 'Ended (1)') ? ok('a project with only an ended campaign is not shown; "Ended (1)" is') : fail('only-ended: ' + JSON.stringify(b));
  (b.emptyText === 'No active or draft campaigns' || /No active or draft campaigns/.test(b.emptyText || '')) ? ok('with nothing live the page shows its single empty state') : fail('empty text: ' + b.emptyText);
  pageErrors.length ? fail('page errors: ' + pageErrors.join(' | ')) : ok('no uncaught page errors');
  await ctx.close();
}

// 3: zero campaigns (Ron's real shape: projects, no campaigns).
async function none(browser) {
  const ws = mk({}, projs('alpha', 'beta', 'gamma'), []);
  const { ctx, page, pageErrors } = await newPage(browser, ws);
  const b = await board(page);
  (b.blocks.length === 0 && /No active or draft campaigns/.test(b.emptyText || '') && b.emptyNewcamp)
    ? ok('zero campaigns -> ONE empty state "No active or draft campaigns" + New campaign action') : fail('empty state: ' + JSON.stringify(b));
  (!b.oldText && b.chips === 0 && !b.legend && !b.colHead && b.toggle === null)
    ? ok('no per-project boxes, no planner chips, no legend/column header, no Ended link') : fail('leftovers: ' + JSON.stringify(b));
  const n = await page.$$eval('.desk-v1-home-empty', (e) => e.length);
  n === 1 ? ok('exactly one empty-state element on the page') : fail('empty-state count ' + n);
  if (SHOTS) await page.screenshot({ path: resolve(SHOTS, 'home-no-campaigns.png') });
  // (4) an empty project is still reachable: Projects picker -> its project page.
  await page.click('.desk-v1-projects-picker');
  await page.locator('.desk-v1-addto-menu [role="menuitem"]', { hasText: 'Gamma site' }).click({ timeout: 4000 });
  const picked = true;
  await page.waitForSelector('.desk-v1-project-newcamp-btn', { timeout: 6000 });
  const pg = await page.evaluate(() => ({ newcamp: !!document.querySelector('.desk-v1-project-newcamp-btn'), text: document.body.innerText }));
  (picked && pg.newcamp && /Gamma site/.test(pg.text)) ? ok('an empty project stays reachable: its project page offers New campaign') : fail('project page: ' + JSON.stringify(pg).slice(0, 200));
  pageErrors.length ? fail('page errors: ' + pageErrors.join(' | ')) : ok('no uncaught page errors');
  await ctx.close();
}

// Screenshot-only: a real workspace's project list, with and without campaigns.
async function realShape(browser) {
  const list = JSON.parse(readFileSync(process.env.DESK_HOME_PROJECTS, 'utf8'));
  for (const [file, camps] of [
    ['home-real-no-campaigns.png', []],
    ['home-real-with-campaigns.png', [
      { id: 'r1', projectId: list[0].id, state: 'active', title: 'Launch week' },
      { id: 'r2', projectId: list[0].id, state: 'draft', title: 'Restore points explainer' },
      { id: 'r3', projectId: list[2].id, state: 'paused', title: 'Site relaunch' },
      { id: 'r4', projectId: list[1].id, state: 'completed', title: 'Cloud beta' },
    ]],
  ]) {
    const { ctx, page } = await newPage(browser, mk({}, list, camps));
    if (SHOTS) await page.screenshot({ path: resolve(SHOTS, file) });
    await ctx.close();
  }
}

const browser = await chromium.launch();
try {
  console.log('mixed: live + ended + empty projects'); await mixed(browser);
  console.log('only an ended campaign'); await onlyEnded(browser);
  console.log('no campaigns at all'); await none(browser);
  if (process.env.DESK_HOME_PROJECTS) { console.log('real-workspace shots'); await realShape(browser); }
} catch (e) { fail('harness error: ' + (e && e.stack || e)); }
await browser.close();
if (bad) { console.error(`\n❌ FAIL — ${bad} case(s)`); process.exit(1); }
console.log('\n✅ PASS — Home lists only draft/proposed/running/paused campaigns; empty projects and ended campaigns never get a box; one empty state; Ended (N) expands the rest.');
