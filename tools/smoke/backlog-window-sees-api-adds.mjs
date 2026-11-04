#!/usr/bin/env node
/**
 * MC-1154: an open backlog window must pick up items added OUTSIDE it.
 *
 * WHY THIS EXISTS
 * ----------------
 * A project modal (and the All Backlog view) lazy-loads the full item list once
 * and marks the project `_backlogFull`. `_preserveOpenBacklogs()` then copies
 * that cached array over every /api/projects refresh so the 30s poll doesn't
 * collapse a visible list to empty. Side effect: an item an agent adds through
 * POST /api/project/<pid>/backlog never appeared until the window was closed and
 * reopened (Ron could not find MC-1152, 2026-11-04). The fix refetches a project's
 * backlog when the fresh record's counters (total/open/done) or `backlog_seq`
 * disagree with the cached copy.
 *
 * This drives the REAL page in headless Chromium against a small STATEFUL stub
 * of the backlog API: the POST below mutates server state and the counters
 * /api/projects ships, exactly as the real routes do. "One poll" is
 * fetchProjects() - the function the 30s setInterval calls. Hermetic: no running
 * MC server, no real data (an add must never land in a real backlog).
 *
 * RUN: node backlog-window-sees-api-adds.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const rd = (...p) => readFileSync(resolve(REPO_ROOT, ...p), 'utf8');

const INDEX_HTML = rd('static', 'index.html');
// Serve any /static/** file straight from disk: the JS modules import each
// other (conversation.js alone pulls in dozens), so a hand-kept list goes stale.
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

// ── Stateful stub of the backlog API ────────────────────────────────────────
const items = [
  { id: 'it-1', key: 'MC-1', num: 1, text: 'First seeded item', status: 'open',
    priority: 'normal', source: 'user', links: [], notes: [], attachments: [] },
  { id: 'it-2', key: 'MC-2', num: 2, text: 'Second seeded item', status: 'open',
    priority: 'normal', source: 'user', links: [], notes: [], attachments: [] },
];
let seq = 2;
let backlogGets = 0;
function addItem(text, status = 'open') {
  seq += 1;
  items.push({ id: `it-${seq}`, key: `MC-${seq}`, num: seq, text, status, priority: 'normal',
    source: 'agent:smoke', links: [], notes: [], attachments: [] });
}
// The summary /api/projects ships: counters only, never the items.
function projectsPayload() {
  const closed = items.filter((i) => i.status === 'done' || i.status === 'wontdo').length;
  return BASE_PROJECTS.map((p) => p.id !== PID ? p : {
    ...p, backlog: [], backlog_total_count: items.length, backlog_done_count: closed,
    backlog_open_count: items.length - closed, backlog_seq: seq,
  });
}

const ORIGIN = 'http://mc.smoke.test';
const json = (route, body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

function handle(route) {
  const req = route.request();
  const path = new URL(req.url()).pathname;
  if (path === '/' || path === '/index.html')
    return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  const hit = staticFile(path);
  if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
  if (path === '/api/projects') return json(route, projectsPayload());
  if (path === '/api/config') return json(route, {});
  if (path === `/api/project/${PID}/backlog`) {
    if (req.method() === 'POST') {
      addItem(JSON.parse(req.postData() || '{}').text || '');
      return json(route, { item: items[items.length - 1] });
    }
    backlogGets += 1;
    return json(route, items);
  }
  if (path === `/api/project/${PID}/agent/status` || path === `/api/project/${PID}/terminal/status`)
    return json(route, { sessions: [] });
  if (path === `/api/project/${PID}/social/queue`) return json(route, []);
  return route.abort();
}

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

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

  // The same call the 30s setInterval makes.
  const poll = () => page.evaluate(() => window.fetchProjects());
  // The project modal's backlog list.
  const modalCount = (text) => page.locator(`[data-modal-id="${PID}"] .backlog-item`, { hasText: text }).count();
  const ablCount = (text) => page.locator('#abl-list .backlog-item', { hasText: text }).count();

  // ── Project modal ─────────────────────────────────────────────────────────
  await page.evaluate((pid) => window.openProjectModal(pid), PID);
  await page.evaluate((pid) => window.switchModalTab(pid, 'backlog'), PID);
  await page.waitForFunction(
    (pid) => document.querySelectorAll(`[data-modal-id="${pid}"] .backlog-item`).length >= 2,
    PID, { timeout: 20000 });
  ok('project modal shows the 2 seeded items');

  // Steady state: polls with nothing changed must NOT refetch (no loop).
  const getsBefore = backlogGets;
  await poll(); await poll();
  backlogGets === getsBefore ? ok('unchanged counters: 2 polls, 0 backlog refetches')
    : fail(`unchanged counters refetched the backlog ${backlogGets - getsBefore}x`);

  // An agent adds an item through the API while the window is open.
  const addRes = await page.evaluate(async (pid) => {
    const r = await fetch(`/api/project/${pid}/backlog`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: 'ADDED-OUTSIDE-ONE', source: 'agent:smoke' }) });
    return r.status;
  }, PID);
  addRes === 200 ? ok('POST /backlog accepted (item added outside the window)') : fail(`POST status ${addRes}`);
  await poll();
  await page.waitForFunction(
    (pid) => [...document.querySelectorAll(`[data-modal-id="${pid}"] .backlog-item`)]
      .some((e) => e.textContent.includes('ADDED-OUTSIDE-ONE')), PID, { timeout: 10000 }
  ).catch(() => {});
  (await modalCount('ADDED-OUTSIDE-ONE')) === 1 ? ok('after one poll the project modal shows the new item')
    : fail('project modal does NOT show the item added outside the window');

  // ── All Backlog view ──────────────────────────────────────────────────────
  await page.evaluate(() => window.openAllBacklog());
  await page.waitForFunction(
    () => document.querySelectorAll('#abl-list .backlog-item').length >= 3, null, { timeout: 20000 });
  (await ablCount('ADDED-OUTSIDE-ONE')) === 1 ? ok('All Backlog view lists the item on open')
    : fail('All Backlog view missing the item on open');

  // A second outside add while BOTH windows are open: both must pick it up.
  await page.evaluate(async (pid) => {
    await fetch(`/api/project/${pid}/backlog`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: 'ADDED-OUTSIDE-TWO', source: 'agent:smoke' }) });
  }, PID);
  await poll();
  await page.waitForFunction(
    () => [...document.querySelectorAll('#abl-list .backlog-item')]
      .some((e) => e.textContent.includes('ADDED-OUTSIDE-TWO')), null, { timeout: 10000 }
  ).catch(() => {});
  (await ablCount('ADDED-OUTSIDE-TWO')) === 1 ? ok('after one poll the All Backlog view shows the new item')
    : fail('All Backlog view does NOT show the item added outside the window');
  (await modalCount('ADDED-OUTSIDE-TWO')) === 1 ? ok('after the same poll the project modal shows it too')
    : fail('project modal does NOT show the second outside item');

  // A status change that moves a counter (open -> done) is caught too.
  items[0].status = 'done';
  await poll();
  await page.waitForFunction(
    (pid) => (allProjects.find((p) => p.id === pid).backlog || []).find((i) => i.id === 'it-1')?.status === 'done',
    PID, { timeout: 10000 }).then(() => ok('open->done flip (counter change) refetched'))
    .catch(() => fail('open->done flip was not picked up'));

  // ── _reloadBacklogAfterMutation still works (the in-window add path) ──────
  // (_reloadBacklogAfterMutation is module-private; addBacklogItem is its caller.)
  await page.locator(`#backlog-input-${PID}`).fill('ADDED-VIA-MUTATION');
  await page.evaluate((pid) => window.addBacklogItem(pid), PID);
  await page.waitForFunction(
    (pid) => [...document.querySelectorAll(`[data-modal-id="${pid}"] .backlog-item`)]
      .some((e) => e.textContent.includes('ADDED-VIA-MUTATION')), PID, { timeout: 10000 }
  ).catch(() => {});
  (await modalCount('ADDED-VIA-MUTATION')) === 1 ? ok('in-window add (addBacklogItem -> _reloadBacklogAfterMutation) still shows immediately')
    : fail('_reloadBacklogAfterMutation regressed');

  // ── Closed projects are not pinned in memory ──────────────────────────────
  await page.evaluate(() => window.closeModalById('__all_backlog'));
  await page.evaluate((pid) => window.closeModalById(pid), PID);
  await poll();
  const pinned = await page.evaluate((pid) => {
    const p = allProjects.find((x) => x.id === pid);
    return { full: !!p._backlogFull, len: (p.backlog || []).length };
  }, PID);
  (!pinned.full && pinned.len === 0) ? ok('closed windows: items dropped on the next poll (not pinned)')
    : fail(`closed project still pinned: ${JSON.stringify(pinned)}`);

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.length === 0 ? ok('no uncaught JS error during the whole flow')
    : uncaught.forEach((e) => fail('uncaught: ' + e));
} finally {
  await browser.close();
}

console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
