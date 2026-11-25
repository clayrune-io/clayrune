#!/usr/bin/env node
/**
 * Who is working right now, without opening the Floor.
 *
 * 2026-11-24: a Tobin dispatched from the Clayrune website chat ran in the
 * Mission Control project, and nothing on screen said it was working.
 *
 * Pins, at 1440 and 390px:
 *   A. Top-bar pill (working-now.js)
 *      1. Nothing at all when nobody is working; "N working" counts only
 *         `working` figures; appearing moves no existing header control and
 *         does not change the bar's height.
 *      2. Popover lists name, project, activity, age and task; inside the
 *         viewport; full width under the bar at 390.
 *      3. Escape and an outside click close it; N=0 and a failed read hide it.
 *      4. A row opens that agent's chat in its own project.
 *      5. While the Floor window is open the pill adds no request of its own.
 *   B. Dispatching chat (children-running.js)
 *      6. "Tobin is working in Mission Control, 10m, 94 steps" above the
 *         transcript, with Open going to the child's chat.
 *      7. Several children collapse into one line that expands, and stay
 *         expanded across polls and a modal rebuild.
 *      8. It leaves when the list empties, or the parent leaves the payload.
 *      9. The 15s fallback poll alone paints it on an idle, open parent chat.
 *
 * Hermetic: page + static assets served from THIS checkout, /api/* canned.
 * RUN: node tools/smoke/working-now.mjs
 * Screenshots: MC_SMOKE_SHOT_DIR=<dir>
 */
import { readFileSync, existsSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const STATIC_ROOT = resolve(__dirname, '..', '..', 'static');
const ASSETS_ROOT = resolve(__dirname, '..', '..', 'assets');
const ORIGIN = 'http://mc.smoke.test';
const SHOT_DIR = process.env.MC_SMOKE_SHOT_DIR || '';
if (SHOT_DIR) mkdirSync(SHOT_DIR, { recursive: true });

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));

// No leading "__": session-discovery.js skips those windows as pseudo-modals.
const WEB = 'wn_web', MC = 'wn_mc', PARENT = 'wnparent0001', CHILD = 'wnchild00001';
const base = JSON.parse(readFileSync(join(__dirname, 'fixtures', 'projects.json'), 'utf8'))[0];
const PROJECTS = [[WEB, 'Clayrune website'], [MC, 'Mission Control']]
  .map(([id, name]) => ({ ...base, id, name, project_path: '/smoke/' + id, backlog: [] }));

const figure = (sid, name, over = {}) => ({ session_id: sid, claude_session_id: 'csid-' + sid, state: 'working',
  reason: null, activity: 'tool', bg_wait: '', report_waiting: '', character: null, name, name_from: 'character',
  avatar: 'fig:guard', provider: 'claude', model: '', model_from: 'project', started_at: '', age: '10m ago',
  trigger_type: 'manual', hivemind_id: '', subagents: [], allowance_exhausted: '',
  task: 'From the project lead: a parent chat showed nothing when a dispatched child’s report woke it. Diagnosed…', ...over });
const floorBody = (webFigs, mcFigs) => {
  const rooms = [[WEB, 'Clayrune website', webFigs], [MC, 'Mission Control', mcFigs]]
    .filter((r) => r[2].length).map(([id, name, figures]) => ({ id, name, emoji: '', color: '', figures }));
  return { rooms, quiet: [], bench: [], poll_seconds: 3, activity_states: true,
    counts: { rooms: rooms.length, figures: webFigs.length + mcFigs.length, quiet: 0, bench: 0 } };
};
const idleFloor = () => floorBody([figure(PARENT, 'Dave', { state: 'idle', activity: '' })], []);
const busyFloor = () => floorBody(
  [figure(PARENT, 'Dave', { state: 'idle', activity: '' }), figure('wnask000001', 'Quill', { state: 'asking', activity: '' })],
  [figure(CHILD, 'Tobin'), figure('wnfenn000001', 'Fenn', { activity: 'writing', age: '2m ago', task: 'Review the diff' })]);

const child = (sid, name, over = {}) => ({ session_id: sid, claude_session_id: 'csid-' + sid, project_id: MC,
  project_name: 'Mission Control', name, state: 'working',
  started_at: new Date(Date.now() - 10 * 60000 - 5000).toISOString(), steps: 94, ...over });
const session = (sid, status, lines, over = {}) => ({ session_id: sid, claude_session_id: 'csid-' + sid, status,
  task: 'smoke', log_lines: lines, started_at: new Date().toISOString(), process_alive: true, mode: 'B',
  live_copies: [], cwd_moved_from: '', reports_waiting: [], children_running: [], active_subagents: [], ...over });

const browser = await chromium.launch();

async function open(width) {
  const world = { floor: idleFloor(), floorStatus: 200, floorHits: 0, children: [], parentListed: true };
  const ctx = await browser.newContext({ viewport: { width, height: width > 500 ? 900 : 844 } });
  const page = await ctx.newPage();
  await page.addInitScript(() => { localStorage.setItem('walkthrough_done', '1'); localStorage.setItem('mc_walkthrough_done', '1'); });
  const errors = [];
  // Off-origin requests are aborted here, so the lazy mermaid CDN import fails.
  page.on('pageerror', (e) => { if (!/cdn\.jsdelivr\.net\/npm\/mermaid/.test(e.message || '')) errors.push(e.message || String(e)); });
  await page.route('**/*', (route) => {
    const req = route.request(); const url = new URL(req.url()); const path = url.pathname;
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.origin !== ORIGIN) return route.abort();
    if (path === '/' || path === '/index.html')
      return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: readFileSync(resolve(STATIC_ROOT, 'index.html'), 'utf8') });
    const roots = { '/static/': STATIC_ROOT, '/assets/': ASSETS_ROOT };
    for (const [prefix, root] of Object.entries(roots)) {
      if (!path.startsWith(prefix)) continue;
      const file = normalize(join(root, path.slice(prefix.length)));
      if (file.startsWith(root) && existsSync(file)) {
        const type = file.endsWith('.js') ? 'text/javascript; charset=utf-8' : file.endsWith('.css') ? 'text/css; charset=utf-8'
          : file.endsWith('.png') ? 'image/png' : file.endsWith('.svg') ? 'image/svg+xml' : 'application/octet-stream';
        return route.fulfill({ status: 200, contentType: type, body: readFileSync(file) });
      }
      return route.abort();
    }
    if (req.method() !== 'GET') return route.abort();
    if (path === '/api/avatars/guard')
      return route.fulfill({ status: 200, contentType: 'image/webp', body: readFileSync(join(ASSETS_ROOT, 'avatars', 'guard.webp')) });
    if (path === '/api/projects') return json(PROJECTS);
    if (path === '/api/local-auth/status') return json({ configured: true });
    if (path === '/api/config') return json({});
    if (path === '/api/floor') { world.floorHits++; return json(world.floor, world.floorStatus); }
    if (path === `/api/project/${WEB}/agent/status`)
      return json({ sessions: world.parentListed ? [session(PARENT, 'idle',
        ['> User: dispatch the builder', 'Dispatched Tobin into Mission Control. The report comes back here.'],
        { children_running: world.children })] : [] });
    if (path === `/api/project/${MC}/agent/status`)
      return json({ sessions: [session(CHILD, 'running', ['> Lead: fix the parent chat', 'CHILD TRANSCRIPT MARKER'])] });
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => ['floorOpenFigure', 'openProjectModal', 'fetchAgentStatus', 'paintChildrenRunning',
    'workingNowFeed', 'openFloor', 'avatarHTML'].every((n) => typeof window[n] === 'function'), null, { timeout: 20000 });
  await page.waitForSelector('.wn-pill', { state: 'attached', timeout: 10000 });
  return { page, ctx, errors, world };
}

const shot = async (page, name) => { if (SHOT_DIR) await page.screenshot({ path: join(SHOT_DIR, name) }); };

try {
  for (const width of [1440, 390]) {
    console.log(`── ${width}px: top-bar pill`);
    const { page, ctx, errors, world } = await open(width);
    const mobile = width <= 960;
    const pill = page.locator(mobile ? '#mobile-app-bar .wn-pill' : '.header .wn-pill');
    const geometry = () => page.evaluate((m) => {
      const bar = document.querySelector(m ? '#mobile-app-bar' : '.header');
      const pick = m ? ['#mc-hamburger-btn'] : ['.header-search', '.live-badge', '.header-tour-btn', '.header-breadcrumb'];
      return { height: bar.getBoundingClientRect().height, bottom: bar.getBoundingClientRect().bottom,
        // The search box sits right of the pill's slot, so none of these may move.
        at: pick.map((s) => { const r = bar.querySelector(s).getBoundingClientRect(); return `${Math.round(r.left)},${Math.round(r.top)}`; }) };
    }, mobile);

    await page.waitForTimeout(400);
    check(!(await pill.isVisible()), 'nobody working: no pill at all');
    check(await page.locator('.wn-pill:visible').count() === 0, 'nobody working: no pill in either bar');
    const before = await geometry();
    await shot(page, `working-now-idle-${width}.png`);

    world.floor = busyFloor();
    await pill.waitFor({ state: 'visible', timeout: 8000 });
    check((await pill.textContent()).trim() === '2 working', 'pill reads "2 working" (idle and asking figures not counted)',
      `pill reads "${await pill.textContent()}"`);
    const after = await geometry();
    check(after.height === before.height, `bar height unchanged (${after.height}px)`, `bar height ${before.height} -> ${after.height}`);
    check(JSON.stringify(after.at) === JSON.stringify(before.at), 'existing bar controls did not move',
      `moved: ${before.at} -> ${after.at}`);
    const pillBox = await pill.boundingBox();
    check(pillBox.x >= 0 && pillBox.x + pillBox.width <= width, 'pill is inside the viewport', `pill ${JSON.stringify(pillBox)}`);

    await pill.click();
    const pop = page.locator('#working-now-popover');
    await pop.waitFor();
    check(await pill.getAttribute('aria-expanded') === 'true', 'pill reports expanded');
    check(await pop.locator('.wn-row').count() === 2, 'one row per working figure');
    const first = await pop.locator('.wn-row').first().textContent();
    check(['Tobin', 'Mission Control', 'running a tool…', '10m ago', 'From the project lead'].every((t) => first.includes(t)),
      'row shows name, project, activity, age and task', `row: ${first}`);
    await page.waitForFunction(() => [...document.querySelectorAll('.wn-row .wn-avatar img')].length === 2
      && [...document.querySelectorAll('.wn-row .wn-avatar img')].every((i) => i.complete && i.naturalWidth > 0), null, { timeout: 5000 });
    ok('each row has a loaded avatar');
    const box = await pop.boundingBox();
    const bar = await geometry();
    check(box.x >= 0 && box.x + box.width <= width + 0.5, 'popover is inside the viewport', `popover ${JSON.stringify(box)}`);
    check(Math.abs(box.y - bar.bottom) <= 1, 'popover hangs from the bottom of the bar', `popover y=${box.y}, bar bottom=${bar.bottom}`);
    if (mobile) check(box.x === 0 && Math.round(box.width) === width, `popover is full width at ${width}px`, `popover ${JSON.stringify(box)}`);
    const oneLine = await pop.locator('.wn-task').first().evaluate((el) =>
      el.getBoundingClientRect().height <= parseFloat(getComputedStyle(el).lineHeight || '20') + 3 || getComputedStyle(el).whiteSpace === 'nowrap');
    check(oneLine, 'task is truncated to one line');
    await shot(page, `working-now-popover-${width}.png`);

    await page.keyboard.press('Escape');
    check(await pop.count() === 0 && await pill.getAttribute('aria-expanded') === 'false', 'Escape closes the popover');
    await pill.click();
    await page.mouse.click(8, (width > 500 ? 880 : 700));
    check(await pop.count() === 0, 'an outside click closes the popover');
    await pill.click();
    await pill.click();
    check(await pop.count() === 0, 'clicking the pill again closes it');

    await pill.click();
    world.floor = idleFloor();
    await pill.waitFor({ state: 'hidden', timeout: 8000 });
    check(await pop.count() === 0, 'N=0 hides the pill and closes the popover');
    world.floor = busyFloor();
    await pill.waitFor({ state: 'visible', timeout: 8000 });
    world.floorStatus = 500;
    await pill.waitFor({ state: 'hidden', timeout: 8000 });
    ok('a failed read hides the pill instead of keeping a stale count');
    world.floorStatus = 200;
    await pill.waitFor({ state: 'visible', timeout: 8000 });

    await pill.click();
    await pop.locator('.wn-row').first().click();
    await page.waitForFunction(({ pid, sid }) => {
      const out = document.getElementById(`agent-output-${sid}`);
      return !!out && out.closest('.modal-window') && out.textContent.includes('CHILD TRANSCRIPT MARKER');
    }, { pid: MC, sid: CHILD }, { timeout: 8000 });
    ok("a row opens that agent's chat in its own project");
    check(await pop.count() === 0, 'popover closes after opening a chat');
    await page.evaluate((pid) => window.closeModalById(pid), MC);

    // Sharing: with the Floor polling every 3s, ~9.5s must show only its reads.
    await page.evaluate(() => window.openFloor());
    await page.waitForSelector('#floor-body .fl-rooms');
    await page.waitForTimeout(3500);
    world.floorHits = 0;
    await page.waitForTimeout(9500);
    check(world.floorHits >= 2 && world.floorHits <= 4,
      `Floor open: ${world.floorHits} reads in 9.5s at a 3s poll (one poller, not two)`, `Floor open: ${world.floorHits} reads in 9.5s`);
    world.floor = floorBody([], [figure(CHILD, 'Tobin')]);
    await page.waitForFunction(() => [...document.querySelectorAll('.wn-pill .wn-count')].every((e) => e.textContent === '1 working'), null, { timeout: 8000 });
    ok("the pill follows the Floor's payload");
    const threw = await page.evaluate(() => {
      try { window.workingNowFeed({ rooms: {} }); return false; } catch (e) { return true; }
    });
    check(!threw, "a payload the pill cannot read does not throw into the Floor's refresh");
    await page.evaluate(() => window.closeFloor());
    world.floor = idleFloor();
    await pill.waitFor({ state: 'hidden', timeout: 12000 });
    ok('after the Floor closes the pill polls on its own again');

    console.log(`── ${width}px: dispatching chat`);
    await page.evaluate((pid) => window.openProjectModal(pid), WEB);
    const paint = async (children, listed = true) => {
      world.children = children; world.parentListed = listed;
      await page.evaluate((pid) => window.fetchAgentStatus(pid), WEB);
      await page.evaluate(({ pid, sid }) => { activeAgentTab[pid] = sid; refreshModalById(pid); }, { pid: WEB, sid: PARENT });
    };
    const notice = page.locator(`#children-running-${PARENT}`);
    await paint([]);
    await page.waitForSelector(`#agent-output-${PARENT}`);
    check(await notice.count() === 0, 'no children: nothing in the chat');
    const threadTop = () => page.evaluate((sid) => document.getElementById(`agent-output-${sid}`).getBoundingClientRect().bottom, PARENT);
    const bottomBefore = await threadTop();

    await paint([child(CHILD, 'Tobin')]);
    await notice.waitFor();
    const text = await notice.locator('.children-running-text').textContent();
    check(text === 'Tobin is working in Mission Control, 10m, 94 steps', `line reads "${text}"`);
    const placed = await page.evaluate((sid) => {
      const el = document.getElementById(`children-running-${sid}`); const r = el.getBoundingClientRect();
      const out = document.getElementById(`agent-output-${sid}`);
      return { above: !!(el.compareDocumentPosition(out) & Node.DOCUMENT_POSITION_FOLLOWING), left: r.left, right: r.right, height: r.height, vw: innerWidth };
    }, PARENT);
    check(placed.above && placed.left >= 0 && placed.right <= placed.vw + 1, 'line sits above the transcript, inside the viewport', JSON.stringify(placed));
    check(placed.height <= (mobile ? 62 : 36), `line is compact (${Math.round(placed.height)}px)`, `line height ${placed.height}`);
    await page.waitForTimeout(250);
    check(Math.abs((await threadTop()) - bottomBefore) <= 2, 'the thread is re-sized, so the composer does not move',
      `thread bottom ${bottomBefore} -> ${await threadTop()}`);
    await shot(page, `working-now-chat-${width}.png`);

    await paint([child(CHILD, 'Tobin', { state: 'asking', steps: 1 })]);
    check((await notice.textContent()).includes('Tobin is waiting on a question in Mission Control, 10m, 1 step'), 'a child waiting on a question says so');
    await paint([child(CHILD, 'Tobin', { state: 'background' })]);
    check((await notice.textContent()).includes('waiting on a background job in'), 'a child held on a background job says so');

    const three = [child(CHILD, 'Tobin'), child('wnfenn000001', 'Fenn', { steps: 12 }), child('wnquill00001', 'Quill', { steps: 3 })];
    await paint(three);
    const summary = notice.locator('.children-running-summary');
    check((await summary.textContent()) === '3 agents working: Tobin, Fenn, Quill', `summary reads "${await summary.textContent()}"`);
    check(await summary.getAttribute('aria-expanded') === 'false' && await notice.locator('.children-running-rows').isHidden(), 'several children start collapsed');
    // Measured in one step: a modal redraw between locating and measuring gives null.
    const collapsedHeight = await page.waitForFunction((sid) =>
      document.getElementById(`children-running-${sid}`)?.getBoundingClientRect().height || 0, PARENT, { timeout: 3000 })
      .then((h) => h.jsonValue());
    check(collapsedHeight <= 36, `collapsed summary is one line (${Math.round(collapsedHeight)}px)`, `height ${collapsedHeight}`);
    await shot(page, `working-now-chat-collapsed-${width}.png`);
    await summary.click();
    check(await notice.locator('.children-running-row:visible').count() === 3, 'expands to one row per child');
    await paint(three);
    check(await summary.getAttribute('aria-expanded') === 'true', 'stays expanded across a poll and a modal rebuild');
    check(await notice.count() === 1, 'exactly one notice after repeated polls');
    const kept = await page.evaluate(({ sid, list }) => {
      const el = document.getElementById(`children-running-${sid}`);
      window.paintChildrenRunning(sid, list);
      window.paintChildrenRunning(sid, list);
      return el === document.getElementById(`children-running-${sid}`);
    }, { sid: PARENT, list: three });
    check(kept, 'an unchanged poll leaves the line in place (a tap is not swallowed)');
    await shot(page, `working-now-chat-expanded-${width}.png`);

    await page.evaluate(({ pid, sid }) => { closeModalById(pid); openProjectModal(pid); switchAgentTab(pid, sid); }, { pid: WEB, sid: PARENT });
    await notice.waitFor({ timeout: 5000 });
    ok('line survives closing and reopening the chat window');
    await paint([]);
    check(await notice.count() === 0, 'line leaves when the last child ends');

    await paint([child(CHILD, 'Tobin')]);
    await notice.waitFor();
    await page.evaluate((pid) => window.syncChildrenRunning(pid, []), WEB);
    check(await notice.count() === 0, 'a parent missing from the payload loses its line');

    if (width === 1440) {
      // No fetchAgentStatus call from here on: only the app's own timers.
      await paint([]);
      world.children = [child(CHILD, 'Tobin', { steps: 7 })];
      await notice.waitFor({ timeout: 18000 });
      check((await notice.textContent()).includes('7 steps'), 'the 15s fallback poll paints a new child on an idle open chat');
    }

    await paint([child(CHILD, 'Tobin')]);
    await notice.locator('.children-running-open').click();
    await page.waitForFunction((sid) => (document.getElementById(`agent-output-${sid}`)?.textContent || '').includes('CHILD TRANSCRIPT MARKER'), CHILD, { timeout: 8000 });
    ok("Open goes to the child's chat in the other project");

    check(errors.length === 0, 'no uncaught page errors', `errors: ${errors}`);
    await ctx.close();
  }
} catch (e) {
  fail('smoke aborted: ' + (e && e.message ? e.message : e));
} finally {
  await browser.close();
}
console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
