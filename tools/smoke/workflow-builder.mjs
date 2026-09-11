#!/usr/bin/env node
/**
 * MC-871 R2-D7 — the workflow CANVAS (static/js/workflow-builder.js).
 * Real headless boot (real index.html + real static/js/*.js, no network),
 * same hermetic shape as channel-mode-roster.mjs / drag-to-hire.mjs.
 *
 * Replaces the old spine-driven smoke (Phase 2, drag-to-reorder-a-list):
 * that suite drove a UI this file deletes. This suite drives the free
 * canvas instead: drag a block from the palette onto the canvas, connect two
 * nodes port-to-port, a refused cycle, a refused slot break, save
 * round-tripping to `format: 2`, and a touch-context case that would catch
 * the mobile scroll-lock trap (`touch-action: none` applied permanently
 * instead of only during an active drag).
 *
 * RUN
 *   cd tools/smoke && node workflow-builder.mjs
 * Exit 0 = all cases behave; 1 = a case regressed / harness error.
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
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_wf';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

function fixtureProject(id, name) {
  return {
    id, name, status: 'active', domain: 'general', emoji: '🧪',
    description: '', summary: '', current_task: 'Idle', next_action: '',
    blocked: false, blocked_reason: null, activity_log: [], backlog: [],
    project_path: '/smoke/' + id, last_updated: '2026-09-11T00:00:00Z',
    last_updated_relative: 'today', last_completed: null, live_agent: null,
    display_order: 0, provider: 'claude', use_streaming_agent: true,
    distiller_mode: 'proposed', distiller_min_recurrence: 3,
    distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
    distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
    distiller_skip_errors: true,
  };
}
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Workflow Smoke')]);
const CHARACTERS_JSON = JSON.stringify([
  { name: 'builder', display_name: 'builder', agent_name: 'Tobin', scope: 'global',
    description: '', engine: { provider: 'claude', model: 'claude-sonnet-5' } },
]);

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

async function setValue(page, selector, value) {
  await page.evaluate(({ selector, value }) => {
    const el = document.querySelector(selector);
    if (!el) throw new Error('setValue: not found ' + selector);
    el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }, { selector, value });
}

// A palette-drag → canvas drop, expressed as raw mouse events so it exercises
// the SAME pointerdown/pointermove/pointerup handlers a real drag fires
// (Playwright's page.mouse.* dispatches real pointer events, unlike
// page.dragAndDrop which is HTML5 DnD — the wrong gesture family per the
// spec's explicit "never HTML5 drag-and-drop" precedent, floor.js).
async function dragPaletteBlockTo(page, blockText, targetX, targetY) {
  const block = await page.evaluateHandle((text) => {
    return [...document.querySelectorAll('.wfb-palette-block')].find(b => b.textContent.trim() === text);
  }, blockText);
  const box = await block.asElement().boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 40, box.y + box.height / 2 + 40, { steps: 4 }); // clear the 8px slop
  await page.mouse.move(targetX, targetY, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(120);
}

async function portCenter(page, selector) {
  const el = await page.$(selector);
  if (!el) return null;
  const box = await el.boundingBox();
  if (!box) return null;
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

async function dragPortTo(page, fromSel, toSel) {
  const p1 = await portCenter(page, fromSel);
  const p2 = await portCenter(page, toSel);
  if (!p1 || !p2) throw new Error(`dragPortTo: missing endpoint (${fromSel}=${!!p1}, ${toSel}=${!!p2})`);
  await page.mouse.move(p1.x, p1.y);
  await page.mouse.down();
  await page.mouse.move((p1.x + p2.x) / 2, (p1.y + p2.y) / 2, { steps: 6 });
  await page.mouse.move(p2.x, p2.y, { steps: 6 });
  await page.mouse.up();
  await page.waitForTimeout(120);
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

  const workflowPosts = [];
  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/workflows' && req.method() === 'POST') {
      const body = JSON.parse(req.postData() || '{}');
      workflowPosts.push(body);
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        ok: true, workflow: { ...body, id: 'wf-smoke1', format: 2, created: '2026-09-11T00:00:00Z', updated: '2026-09-11T00:00:00Z' },
      }) });
    }
    return route.abort();
  });

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });
  if (pageErrors.length) pageErrors.forEach((e) => fail('uncaught page error during boot: ' + e));
  else ok('app booted clean, no uncaught exceptions');

  await page.evaluate(() => {
    window.__toasts = [];
    const orig = window.showToast;
    window.showToast = (msg, ms) => { window.__toasts.push(msg); if (orig) orig(msg, ms); };
  });

  await page.evaluate(() => { window.openWorkflowBuilder(); });
  await page.waitForSelector('#wfb-canvas-viewport', { timeout: 5000 });
  ok('builder modal opened blank (new workflow) — canvas viewport present');

  // ── Drag a palette block onto the canvas ─────────────────────────────────
  const vpBox = await (await page.$('#wfb-canvas-viewport')).boundingBox();
  await dragPaletteBlockTo(page, 'Agent step', vpBox.x + 140, vpBox.y + 120);
  let nodeCount = await page.$$eval('.wfb-node', els => els.length);
  nodeCount === 1 ? ok('drag from palette placed one node on the canvas')
                  : fail(`expected 1 node after the palette drag, got ${nodeCount}`);
  // Field edits sync into the model (and `data-name` with them) only at the
  // moment of the NEXT structural action, not on every keystroke (file
  // header: "typing in one node's prompt is never clobbered by placing a new
  // block or dragging an edge elsewhere") — so the rename below won't be
  // reflected in `data-name` until the connect drag triggers a sync+render.
  // Capture the auto-generated name now, for that first drag's selector.
  const autoName1 = await page.$eval('.wfb-node', el => el.dataset.name);
  await setValue(page, '.wfb-node .wfb-name', 'triage');
  await setValue(page, '.wfb-node .wfb-prompt', 'Decide whether this is worth drafting.');

  await dragPaletteBlockTo(page, 'Agent step', vpBox.x + 460, vpBox.y + 120);
  nodeCount = await page.$$eval('.wfb-node', els => els.length);
  nodeCount === 2 ? ok('a second palette drag placed a second, independent node')
                  : fail(`expected 2 nodes, got ${nodeCount}`);
  // Placing the second block synced the model (per `_wfPlaceNodeAt`), so the
  // first node's typed rename has already landed and `data-name` reflects it.
  const renamedOk = await page.$eval(`.wfb-node[data-name="triage"]`, () => true).catch(() => false);
  renamedOk ? ok('a typed rename in one node survives placing a second, independent block (no clobber)')
            : fail('placing a second block clobbered an unsynced rename in the first node');
  const autoName2 = await page.$eval('.wfb-node:not([data-name="triage"])', el => el.dataset.name);
  await setValue(page, `.wfb-node[data-name="${autoName2}"] .wfb-name`, 'draft');
  await setValue(page, `.wfb-node[data-name="${autoName2}"] .wfb-prompt`, 'Draft a post from {{steps.triage.output}}.');

  // ── Connect: drag triage's (single, unconditional) output port to draft's
  // input port. "draft" hasn't synced yet at this exact moment, so address it
  // by its still-current auto-generated name — the drag's own sync (inside
  // _wfTryAddEdge) is what commits the rename and repoints the new edge to
  // the post-rename name. ──────────────────────────────────────────────────
  await dragPortTo(page,
    '.wfb-node[data-name="triage"] .wfb-port-out',
    `.wfb-node[data-name="${autoName2}"] .wfb-port-in`);
  let edgeCount = await page.$$eval('.wfb-edge-path', els => els.length);
  edgeCount === 1 ? ok('drag from an output port to an input port drew one edge')
                  : fail(`expected 1 edge after connecting, got ${edgeCount}`);
  const draftRenamedOk = await page.$eval('.wfb-node[data-name="draft"]', () => true).catch(() => false);
  draftRenamedOk ? ok('the second node\'s rename landed too, and the new edge points at its post-rename name')
                 : fail('the connect drag did not sync/repoint the second node\'s rename');

  // ── A refused cycle: draft → triage would close a loop ───────────────────
  const toastsBeforeCycle = (await page.evaluate(() => window.__toasts)).length;
  await dragPortTo(page,
    '.wfb-node[data-name="draft"] .wfb-port-out',
    '.wfb-node[data-name="triage"] .wfb-port-in');
  edgeCount = await page.$$eval('.wfb-edge-path', els => els.length);
  edgeCount === 1 ? ok('the cycle-closing connection was REFUSED — edge count still 1')
                  : fail(`a cyclic connect should have been refused, edge count is now ${edgeCount}`);
  const cycleToasts = (await page.evaluate(() => window.__toasts)).slice(toastsBeforeCycle);
  cycleToasts.some(t => /loop/i.test(t)) ? ok(`a toast named the refused cycle: "${cycleToasts.find(t => /loop/i.test(t))}"`)
                                         : fail(`expected a toast naming the refused loop, got ${JSON.stringify(cycleToasts)}`);

  // ── A refused slot break: disconnect triage → draft, which "draft"'s
  // prompt depends on via {{steps.triage.output}} ─────────────────────────
  await page.click('.wfb-edge-path'); // select the (only) real edge
  await page.keyboard.press('Delete');
  await page.waitForTimeout(120);
  edgeCount = await page.$$eval('.wfb-edge-path', els => els.length);
  edgeCount === 1 ? ok('the slot-break guard REFUSED deleting the edge {{steps.triage.output}} depends on')
                  : fail(`expected the guard to keep the edge (count 1), got ${edgeCount}`);
  const breakToasts = await page.evaluate(() => window.__toasts);
  breakToasts.some(t => /steps\.triage/.test(t)) ? ok(`a toast named the broken reference: "${breakToasts.find(t => /steps\.triage/.test(t))}"`)
                                                  : fail(`expected a toast naming {{steps.triage...}}, got ${JSON.stringify(breakToasts)}`);

  // ── A Clayrune action block, dragged in separately (not wired to
  // anything) — exercises the third palette block + the unconnected-port
  // "stop stub" render path (R2-D6). ───────────────────────────────────────
  await dragPaletteBlockTo(page, 'Clayrune action', vpBox.x + 300, vpBox.y + 320);
  nodeCount = await page.$$eval('.wfb-node', els => els.length);
  nodeCount === 3 ? ok('Clayrune action block placed from the palette')
                  : fail(`expected 3 nodes after placing the action block, got ${nodeCount}`);
  const stubCount = await page.$$eval('.wfb-port-row.wfb-port-unconnected', els => els.length);
  stubCount > 0 ? ok(`${stubCount} unconnected port(s) render a stop stub`)
                : fail('expected at least one unconnected port to render a stop stub');

  // ── Touch-context: the drag handles must not carry touch-action:none
  // PERMANENTLY (the mobile scroll-lock trap) — only while a drag is
  // actually active, via a dynamically-applied class. ─────────────────────
  const touchActionAtRest = await page.evaluate(() => {
    const head = document.querySelector('.wfb-node-head');
    const block = document.querySelector('.wfb-palette-block');
    return {
      head: getComputedStyle(head).touchAction,
      block: getComputedStyle(block).touchAction,
    };
  });
  (touchActionAtRest.head !== 'none' && touchActionAtRest.block !== 'none')
    ? ok(`at rest, drag handles stay scrollable (node-head: ${touchActionAtRest.head}, palette-block: ${touchActionAtRest.block}) — no permanent scroll lock`)
    : fail(`a drag handle is touch-action:none at rest — this is the mobile scroll-lock trap: ${JSON.stringify(touchActionAtRest)}`);
  const touchActionDuringDrag = await page.evaluate(() => {
    document.querySelector('.wfb-node').classList.add('wfb-node-dragging');
    const v = getComputedStyle(document.querySelector('.wfb-node-head')).touchAction;
    document.querySelector('.wfb-node').classList.remove('wfb-node-dragging');
    return v;
  });
  touchActionDuringDrag === 'none' ? ok('DURING an active drag, the node head correctly switches to touch-action:none')
                                   : fail(`expected touch-action:none while .wfb-node-dragging is active, got "${touchActionDuringDrag}"`);
  const portTouchAction = await page.evaluate(() => getComputedStyle(document.querySelector('.wfb-port')).touchAction);
  portTouchAction === 'none' ? ok('a port (a dedicated control, not a scrollable list item) is touch-action:none unconditionally')
                             : fail(`expected a port to be touch-action:none always, got "${portTouchAction}"`);

  // ── Mobile viewport: palette becomes a bottom sheet, canvas still present ─
  await ctx.close();
  const mctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const mpage = await mctx.newPage();
  const mPageErrors = [];
  mpage.on('pageerror', (e) => mPageErrors.push(e.message || String(e)));
  await mpage.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    return route.abort();
  });
  await mpage.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await mpage.waitForSelector('#projects-col .card, .mc-chat-row', { timeout: 15000 });
  await mpage.evaluate(() => { window.openWorkflowBuilder(); });
  await mpage.waitForSelector('#wfb-canvas-viewport', { timeout: 5000 });
  const paletteFlow = await mpage.evaluate(() => {
    const builder = document.querySelector('.wfb-builder');
    const palette = document.querySelector('.wfb-palette');
    return { builderDir: getComputedStyle(builder).flexDirection, paletteDir: getComputedStyle(palette).flexDirection };
  });
  (paletteFlow.builderDir === 'column-reverse' && paletteFlow.paletteDir === 'row')
    ? ok(`at a phone width, the palette lays out as a bottom sheet (builder: ${paletteFlow.builderDir}, palette row: ${paletteFlow.paletteDir})`)
    : fail(`expected the palette to become a horizontal bottom sheet at 390px, got ${JSON.stringify(paletteFlow)}`);
  if (mPageErrors.length) mPageErrors.forEach((e) => fail('uncaught page error on mobile boot: ' + e));
  else ok('mobile viewport booted the builder clean, no uncaught exceptions');
  await mctx.close();

  // Reopen a desktop context for the save round-trip (mobile context above
  // was closed to keep viewport switching hermetic).
  const ctx2 = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page2 = await ctx2.newPage();
  const page2Errors = [];
  page2.on('pageerror', (e) => page2Errors.push(e.message || String(e)));
  const workflowPosts2 = [];
  await page2.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/workflows' && req.method() === 'POST') {
      const body = JSON.parse(req.postData() || '{}');
      workflowPosts2.push(body);
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        ok: true, workflow: { ...body, id: 'wf-smoke2', format: 2, created: '2026-09-11T00:00:00Z', updated: '2026-09-11T00:00:00Z' },
      }) });
    }
    return route.abort();
  });
  await page2.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page2.waitForSelector('#projects-col .card', { timeout: 15000 });
  await page2.evaluate(() => { window.openWorkflowBuilder(); });
  await page2.waitForSelector('#wfb-canvas-viewport', { timeout: 5000 });
  const vpBox2 = await (await page2.$('#wfb-canvas-viewport')).boundingBox();
  await dragPaletteBlockTo(page2, 'Agent step', vpBox2.x + 140, vpBox2.y + 120);
  await setValue(page2, '.wfb-node .wfb-name', 'harvest-triage');
  await setValue(page2, '.wfb-node .wfb-prompt', 'Score the signals.');
  await setValue(page2, '#wfb-name', 'Smoke test workflow');
  await setValue(page2, '#wfb-desc', 'Exercises the canvas end to end.');
  await page2.click('.wfb-actions .btn-sched-save');
  await page2.waitForTimeout(250);
  workflowPosts2.length === 1 ? ok('POST /api/workflows fired exactly once on Save')
                              : fail(`expected exactly 1 POST /api/workflows, got ${workflowPosts2.length}`);
  const posted = workflowPosts2[0] || {};
  posted.name === 'Smoke test workflow' ? ok('posted body carries the name field')
                                        : fail(`posted name should be "Smoke test workflow", got ${JSON.stringify(posted.name)}`);
  (Array.isArray(posted.nodes) && posted.nodes.length === 1 && posted.nodes[0].name === 'harvest-triage'
    && typeof posted.nodes[0].x === 'number' && typeof posted.nodes[0].y === 'number')
    ? ok(`posted body is format-2 shaped: one node ("harvest-triage") carrying x/y (${posted.nodes[0].x},${posted.nodes[0].y})`)
    : fail(`posted nodes malformed: ${JSON.stringify(posted.nodes)}`);
  Array.isArray(posted.edges) ? ok('posted body carries an edges array (format 2)')
                              : fail(`expected an edges array in the posted body, got ${JSON.stringify(posted.edges)}`);
  await page2.waitForFunction(() => {
    const btn = document.querySelector('.wfb-actions .btn-sched-save');
    return btn && btn.textContent.trim() === 'Update';
  }, { timeout: 3000 }).then(() => ok('after a successful save, the button relabels to "Update" (workflowId adopted)'),
                            () => fail('save button never relabeled to "Update" after a successful save'));

  const uncaught = page2Errors.filter(e => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail('uncaught exception during interaction: ' + e));
  await ctx2.close();

  // ── Trigger card: schedule cadence ───────────────────────────────────────
  // This is the coverage gap that shipped two defects unnoticed: clicking a
  // cadence type button threw a ReferenceError because _wfSetSchedType was
  // never bridged onto window (inline handlers resolve against the global
  // object — see inline-handler-scope-check.mjs), and 'weekly' was absent
  // from the type list, so its day picker had no way to render. Exercises
  // "On a schedule", every cadence type, weekly day-picking, the
  // cannot-save-empty guard, and a save/close/reopen round-trip.
  const ctx3 = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page3 = await ctx3.newPage();
  const page3Errors = [];
  page3.on('pageerror', (e) => page3Errors.push(e.message || String(e)));
  let savedWorkflow3 = null;
  let savedSchedule3 = null;
  await page3.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/workflows' && req.method() === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(savedWorkflow3 ? [savedWorkflow3] : []) });
    }
    if (path === '/api/workflows' && req.method() === 'POST') {
      const body = JSON.parse(req.postData() || '{}');
      savedWorkflow3 = { ...body, id: 'wf-smoke3', format: 2, created: '2026-09-11T00:00:00Z', updated: '2026-09-11T00:00:00Z' };
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, workflow: savedWorkflow3 }) });
    }
    if (path === '/api/schedules' && req.method() === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(savedSchedule3 ? [savedSchedule3] : []) });
    }
    if (path === '/api/schedules' && req.method() === 'POST') {
      const body = JSON.parse(req.postData() || '{}');
      savedSchedule3 = { ...body, id: 'sched-smoke3' };
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(savedSchedule3) });
    }
    if (path.startsWith('/api/schedules/') && req.method() === 'PUT') {
      const body = JSON.parse(req.postData() || '{}');
      savedSchedule3 = { ...savedSchedule3, ...body };
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(savedSchedule3) });
    }
    return route.abort();
  });
  await page3.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page3.waitForSelector('#projects-col .card', { timeout: 15000 });
  await page3.evaluate(() => {
    window.__toasts = [];
    const orig = window.showToast;
    window.showToast = (msg, ms) => { window.__toasts.push(msg); if (orig) orig(msg, ms); };
  });
  await page3.evaluate(() => { window.openWorkflowBuilder(); });
  await page3.waitForSelector('#wfb-canvas-viewport', { timeout: 5000 });

  await setValue(page3, '#wfb-name', 'Cadence smoke workflow');
  const vpBox3 = await (await page3.$('#wfb-canvas-viewport')).boundingBox();
  await dragPaletteBlockTo(page3, 'Agent step', vpBox3.x + 140, vpBox3.y + 120);
  await setValue(page3, '.wfb-node .wfb-name', 'step-one');
  await setValue(page3, '.wfb-node .wfb-prompt', 'Do the thing.');

  await page3.click('input[name="wfb-trigger"][value="schedule"]');
  await page3.waitForSelector('.wfb-sched-cadence', { timeout: 3000 });
  ok('selecting "On a schedule" renders the cadence sub-form');

  const defaultType = await page3.$eval('.sched-type-btn.active', el => el.textContent.trim());
  defaultType === 'Daily' ? ok('cadence defaults to Daily')
                          : fail(`expected Daily to default-active, got "${defaultType}"`);
  const dailyDayCount = await page3.$$eval('.sched-day-btn', els => els.length);
  dailyDayCount === 0 ? ok('Daily renders no day picker (days belong to Weekly only)')
                      : fail(`expected no day buttons under Daily, got ${dailyDayCount}`);

  // Click through every cadence type. This is exactly what defect 1 broke:
  // the click fired an inline handler naming a module-scoped function that
  // was never bridged onto window, threw a silent ReferenceError, and the
  // sub-form never re-rendered.
  const typeChecks = [
    ['Weekly', async () => {
      const days = await page3.$$eval('.sched-day-btn', els => els.length);
      return days === 7 && !!(await page3.$('#wfb-sched-time'));
    }],
    ['Interval', async () => !!(await page3.$('#wfb-sched-interval'))],
    ['Once', async () => !!(await page3.$('#wfb-sched-runat'))],
    ['Cron', async () => !!(await page3.$('#wfb-sched-cron'))],
  ];
  for (const [label, assertFields] of typeChecks) {
    await page3.click(`.sched-type-btn:text-is("${label}")`);
    await page3.waitForTimeout(80);
    const nowActive = await page3.$eval('.sched-type-btn.active', el => el.textContent.trim()).catch(() => null);
    nowActive === label ? ok(`clicking "${label}" activates it (handler reachable, no ReferenceError)`)
                         : fail(`clicking "${label}" did not activate it — got "${nowActive}"`);
    const fieldsOk = await assertFields();
    fieldsOk ? ok(`"${label}"'s sub-fields rendered correctly`)
             : fail(`"${label}"'s sub-fields did not render as expected`);
  }
  if (page3Errors.length) { page3Errors.forEach((e) => fail('uncaught page error while switching cadence type: ' + e)); page3Errors.length = 0; }

  // Weekly with no day picked must refuse to save — before this fix the day
  // row was unreachable at all, so "saved empty" wasn't even the failure
  // mode; now that it's reachable, confirm the empty set is still refused.
  await page3.click('.sched-type-btn:text-is("Weekly")');
  await page3.waitForTimeout(80);
  const toastsBeforeEmpty = (await page3.evaluate(() => (window.__toasts || []).length));
  await page3.click('.wfb-actions .btn-sched-save');
  await page3.waitForTimeout(150);
  const newToasts = (await page3.evaluate(() => window.__toasts || [])).slice(toastsBeforeEmpty);
  newToasts.some((t) => /day/i.test(t)) ? ok(`saving Weekly with no days picked was refused: "${newToasts.find((t) => /day/i.test(t))}"`)
                                        : fail(`expected a toast refusing an empty weekly day set, got ${JSON.stringify(newToasts)}`);
  savedWorkflow3 === null ? ok('the empty-days guard fired before the workflow POST — nothing saved')
                          : fail('a workflow was posted despite an empty weekly day set');

  // Pick Wednesday and save for real.
  await page3.click('.sched-day-btn[data-day="3"]');
  await page3.click('.wfb-actions .btn-sched-save');
  await page3.waitForFunction(() => {
    const btn = document.querySelector('.wfb-actions .btn-sched-save');
    return btn && btn.textContent.trim() === 'Update';
  }, { timeout: 3000 }).catch(() => {});
  await page3.waitForTimeout(200);

  savedWorkflow3 ? ok('POST /api/workflows fired once a day was picked')
                 : fail('workflow was not saved even after picking a day');
  savedSchedule3 ? ok('the linked schedule was created via POST /api/schedules')
                 : fail('no schedule was created for the "on a schedule" trigger');
  (savedSchedule3 && savedSchedule3.schedule_type === 'weekly')
    ? ok('the saved schedule carries schedule_type "weekly"')
    : fail(`expected schedule_type "weekly", got ${JSON.stringify(savedSchedule3 && savedSchedule3.schedule_type)}`);
  (savedSchedule3 && Array.isArray(savedSchedule3.days) && savedSchedule3.days.length === 1 && savedSchedule3.days[0] === 3)
    ? ok('the saved schedule carries days:[3] (Wednesday)')
    : fail(`expected days [3], got ${JSON.stringify(savedSchedule3 && savedSchedule3.days)}`);

  // Close and reopen the SAME workflow — confirm the cadence round-trips.
  await page3.click('.wfb-actions button:text-is("Close")');
  await page3.waitForTimeout(80);
  await page3.evaluate((id) => { window.openWorkflowBuilder(id); }, savedWorkflow3.id);
  await page3.waitForSelector('#wfb-canvas-viewport', { timeout: 5000 });
  await page3.waitForSelector('.sched-type-btn.active', { timeout: 3000 });

  const reopenActiveType = await page3.$eval('.sched-type-btn.active', el => el.textContent.trim());
  reopenActiveType === 'Weekly' ? ok('reopening the workflow shows Weekly as the active cadence type')
                                : fail(`expected Weekly active on reopen, got "${reopenActiveType}"`);
  const reopenActiveDays = await page3.$$eval('.sched-day-btn.active', els => els.map((e) => e.dataset.day));
  (reopenActiveDays.length === 1 && reopenActiveDays[0] === '3')
    ? ok('reopening highlights the previously-picked day (Wed) and no others')
    : fail(`expected only day 3 highlighted on reopen, got ${JSON.stringify(reopenActiveDays)}`);

  const uncaught3 = page3Errors.filter(e => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught3.length) uncaught3.forEach((e) => fail('uncaught exception in the cadence trigger flow: ' + e));
  await ctx3.close();

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0
    ? '\n✅ PASS — palette drag-to-place, port-to-port connect, a refused cycle, a refused slot break, an unconnected-port stop stub, mobile bottom-sheet layout, touch-action scroll-lock guard, save (format 2), and the schedule-trigger cadence form (type switching, weekly day-picking, empty-day guard, save/reopen round-trip) all behave correctly.'
    : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
