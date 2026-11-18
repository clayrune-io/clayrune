#!/usr/bin/env node
/**
 * Agent-proposed backlog items: the chip, Accept, Dismiss and the sort (Ron, 2026-11-17).
 *
 * WHY THIS EXISTS
 * ----------------
 * An item an agent adds is stored `status: 'proposed'` and waits for a person
 * (mc/backlog_proposals.py). Both backlog surfaces (the project Backlog tab in
 * render-core.js and the cross-project list in cross-backlog.js) must show an
 * "Agent proposed" chip with the agent's name and Accept / Dismiss buttons on a
 * proposal and on nothing else, sort it below accepted items, and POST to the
 * human-only accept / dismiss routes. The Python tests pin the server half; this
 * drives the REAL page in headless Chromium against a small STATEFUL stub of the
 * routes (the stub applies accept/dismiss the way the real routes do), so a
 * regression in either renderer or in static/js/backlog-proposals.js shows here.
 * Hermetic: no running MC server, no real data.
 *
 * RUN: node backlog-proposals.mjs   (Playwright from tools/smoke/node_modules)
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const rd = (...p) => readFileSync(resolve(REPO_ROOT, ...p), 'utf8');

const INDEX_HTML = rd('static', 'index.html');
const MIME = { '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.json': 'application/json', '.svg': 'image/svg+xml' };
function staticFile(path) {
  if (!path.startsWith('/static/') || path.includes('..')) return null;
  const ext = path.slice(path.lastIndexOf('.'));
  if (!MIME[ext]) return null;
  try { return [MIME[ext], readFileSync(resolve(REPO_ROOT, path.slice(1)))]; } catch { return null; }
}

const PID = 'smoke_alpha';
const BASE_PROJECTS = JSON.parse(rd('tools', 'smoke', 'fixtures', 'projects.json'));

// Stored order puts the proposals FIRST: the sort, not the storage, must put them last.
const item = (id, n, text, status, extra = {}) => ({ id, key: `MC-${n}`, num: n, text, status,
  priority: 'normal', source: 'dashboard', links: [], notes: [], attachments: [], ...extra });
const BY = { kind: 'agent', session_id: 's1', agent_name: 'Tobin', attended: false };
const items = [
  item('p-1', 1, 'PROPOSAL-ONE', 'proposed', { source: 'agent', proposed_by: BY }),
  item('p-2', 2, 'PROPOSAL-TWO', 'proposed', { source: 'agent', proposed_by: { ...BY, agent_name: 'Dave', attended: true } }),
  item('o-1', 3, 'ACCEPTED-ONE', 'open'),
  item('o-2', 4, 'ACCEPTED-TWO', 'open'),
];
const posts = [];
function projectsPayload() {
  const closed = items.filter((i) => i.status === 'done' || i.status === 'wontdo').length;
  const proposed = items.filter((i) => i.status === 'proposed').length;
  return BASE_PROJECTS.map((p) => p.id !== PID ? p : {
    ...p, backlog: [], backlog_total_count: items.length, backlog_done_count: closed,
    backlog_proposed_count: proposed, backlog_open_count: items.length - closed - proposed, backlog_seq: 4,
  });
}

const ORIGIN = 'http://mc.smoke.test';
const json = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

function handle(route) {
  const req = route.request();
  const path = new URL(req.url()).pathname;
  if (path === '/' || path === '/index.html')
    return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  const hit = staticFile(path);
  if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
  if (path === '/api/projects') return json(route, projectsPayload());
  if (path === '/api/config') return json(route, {});
  const m = path.match(new RegExp(`^/api/project/${PID}/backlog/([^/]+)/(accept|dismiss)$`));
  if (m && req.method() === 'POST') {
    posts.push(`${m[2]} ${m[1]}`);
    const it = items.find((i) => i.id === m[1]);
    if (!it || it.status !== 'proposed') return json(route, { error: 'not a proposal' }, 409);
    if (m[2] === 'accept') it.status = 'open';
    else { it.status = 'done'; it.done_reason = 'dismissed'; }
    return json(route, { ok: true, item: it });
  }
  if (path === `/api/project/${PID}/backlog`) return json(route, items);
  if (path === `/api/project/${PID}/agent/status` || path === `/api/project/${PID}/terminal/status`)
    return json(route, { sessions: [] });
  if (path === `/api/project/${PID}/social/queue`) return json(route, []);
  return route.abort();
}

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, bad_) => (cond ? ok(good) : fail(bad_));

const browser = await chromium.launch();
try {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', handle);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 20000 });
  ok('dashboard booted');

  // Rows in DOM order for one surface, as [text-fragment, hasChip, hasAccept, hasDismiss].
  const rowsOf = (sel) => page.$$eval(sel, (els) => els.map((e) => ({
    text: (e.textContent.match(/(PROPOSAL|ACCEPTED)-(ONE|TWO)/) || [''])[0],
    chip: e.querySelector('[data-proposal-chip]')?.textContent.trim() || '',
    accept: !!e.querySelector('[data-proposal-accept]'),
    dismiss: !!e.querySelector('[data-proposal-dismiss]'),
    dispatch: !!e.querySelector('.backlog-dispatch'),
  })));

  // ── Project Backlog tab ───────────────────────────────────────────────────
  await page.evaluate((pid) => window.openProjectModal(pid), PID);
  await page.evaluate((pid) => window.switchModalTab(pid, 'backlog'), PID);
  const MODAL = `[data-modal-id="${PID}"] .backlog-item`;
  await page.waitForFunction((s) => document.querySelectorAll(s).length >= 4, MODAL, { timeout: 20000 });
  let rows = await rowsOf(MODAL);
  check(rows.map((r) => r.text).join() === 'ACCEPTED-ONE,ACCEPTED-TWO,PROPOSAL-ONE,PROPOSAL-TWO',
    'project tab: proposals sort below accepted items', `project tab order: ${rows.map((r) => r.text)}`);
  const prop = rows.filter((r) => r.text.startsWith('PROPOSAL'));
  check(prop.length === 2 && prop.every((r) => r.accept && r.dismiss && /^Agent proposed/.test(r.chip)),
    'project tab: each proposal has the chip, Accept and Dismiss', `proposal rows: ${JSON.stringify(prop)}`);
  check(prop[0].chip.includes('Tobin') && prop[1].chip.includes('Dave'),
    'project tab: the chip names the agent', `chips: ${prop.map((r) => r.chip)}`);
  const acc = rows.filter((r) => r.text.startsWith('ACCEPTED'));
  check(acc.every((r) => !r.chip && !r.accept && !r.dismiss),
    'project tab: an accepted item has no chip and no buttons', `accepted rows: ${JSON.stringify(acc)}`);
  check(prop.every((r) => !r.dispatch), 'project tab: a proposal has no Send-to-agent button',
    'a proposal row offers Send to agent');

  // ── Cross-project view ────────────────────────────────────────────────────
  await page.evaluate(() => window.openAllBacklog());
  await page.waitForFunction(() => document.querySelectorAll('#abl-list .backlog-item').length >= 4,
    null, { timeout: 20000 });
  rows = (await rowsOf('#abl-list .backlog-item')).filter((r) => r.text);
  check(rows.map((r) => r.text).slice(-2).every((t) => t.startsWith('PROPOSAL')),
    'cross view: proposals sort below accepted items of the same priority', `cross order: ${rows.map((r) => r.text)}`);
  const cprop = rows.filter((r) => r.text.startsWith('PROPOSAL'));
  check(cprop.length === 2 && cprop.every((r) => r.accept && r.dismiss && /^Agent proposed/.test(r.chip)),
    'cross view: each proposal has the chip, Accept and Dismiss', `cross proposal rows: ${JSON.stringify(cprop)}`);
  check(rows.filter((r) => r.text.startsWith('ACCEPTED')).every((r) => !r.chip && !r.accept),
    'cross view: accepted items carry no chip or buttons', 'cross view: accepted row has proposal controls');

  // ── Accept: POSTs to the accept route, the chip goes, the row is ordinary ──
  await page.locator('#abl-list .backlog-item', { hasText: 'PROPOSAL-ONE' }).locator('[data-proposal-accept]').click();
  await page.waitForFunction(() => ![...document.querySelectorAll('#abl-list .backlog-item')]
    .some((e) => e.textContent.includes('PROPOSAL-ONE') && e.querySelector('[data-proposal-chip]')), null, { timeout: 10000 })
    .catch(() => {});
  check(posts.includes('accept p-1'), 'Accept posts to /backlog/<id>/accept', `posts: ${posts}`);
  rows = await rowsOf('#abl-list .backlog-item');
  const one = rows.find((r) => r.text === 'PROPOSAL-ONE');
  check(one && !one.chip && !one.accept && !one.dismiss, 'after Accept the item is an ordinary row (no chip, no buttons)',
    `accepted row: ${JSON.stringify(one)}`);

  // ── Dismiss: POSTs to the dismiss route, the item leaves the open list ─────
  await page.locator('#abl-list .backlog-item', { hasText: 'PROPOSAL-TWO' }).locator('[data-proposal-dismiss]').click();
  await page.waitForFunction(() => ![...document.querySelectorAll('#abl-list .backlog-item')]
    .some((e) => e.textContent.includes('PROPOSAL-TWO')), null, { timeout: 10000 }).catch(() => {});
  check(posts.includes('dismiss p-2'), 'Dismiss posts to /backlog/<id>/dismiss', `posts: ${posts}`);
  check((await rowsOf('#abl-list .backlog-item')).every((r) => r.text !== 'PROPOSAL-TWO'),
    'after Dismiss the item leaves the open list', 'dismissed item is still in the open list');
  check(items.find((i) => i.id === 'p-2').done_reason === 'dismissed', 'the stub recorded it as dismissed, not deleted', 'item vanished');

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  check(uncaught.length === 0, 'no uncaught JS error during the whole flow', `uncaught: ${uncaught}`);
} finally {
  await browser.close();
}

console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
