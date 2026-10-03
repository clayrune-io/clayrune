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
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_wf';

const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));

function fixtureProject(id, name) {
  return {
    id, name, status: 'active', domain: 'general', emoji: '🧪',
    description: '', summary: '', current_task: 'Idle', next_action: '',
    blocked: false, blocked_reason: null, activity_log: [], backlog: [],
    project_path: '/smoke/' + id, last_updated: '2026-09-11T00:00:00Z',
    last_updated_relative: 'today', last_completed: null, live_agent: null,
    display_order: 0, provider: 'claude', use_streaming_agent: true,
    // MC-871 engine tooltip: the project's own fallback, one rung above the
    // global default in the resolution chain nothing here previously tested.
    agent_model: 'claude-sonnet-5', agent_effort: 'medium',
    distiller_mode: 'proposed', distiller_min_recurrence: 3,
    distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
    distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
    distiller_skip_errors: true,
  };
}
const PROJECTS_JSON = JSON.stringify([
  fixtureProject('smoke_other', 'Some Other Project'),
  fixtureProject(PID, 'Workflow Smoke'),
]);
const CHARACTERS_JSON = JSON.stringify([
  { name: 'builder', display_name: 'builder', agent_name: 'Tobin', scope: 'global',
    avatar: 'fig:smith', description: '', engine: { provider: 'claude', model: 'claude-sonnet-5' } },
  { name: 'homed', display_name: 'homed', agent_name: 'Homer', scope: 'project',
    avatar: '', description: '', engine: { provider: 'claude', model: 'claude-sonnet-5' } },
]);

// The palette is the BENCH (UI brief §2), so the builder reads /api/floor —
// the same payload the Floor's bench renders. The faces below are the
// avatar-resolution cases Ron called out: a real figure, a real emoji, a
// genuinely faceless type, and a `??` mangled by a Windows console codepage
// (which must fall THROUGH to the initial, never be echoed as text).
const BENCH = [
  { name: 'builder', scope: 'global', display: 'Tobin', avatar: 'fig:smith',
    description: 'builds things', skills: [], provider: 'claude', model: '', effort: '',
    project_id: '', project_name: '', rooms: [] },
  { name: 'code-reviewer', scope: 'global', display: 'Fenn', avatar: '\u{1F50D}',
    description: 'reviews diffs', skills: [], provider: 'claude', model: '', effort: '',
    project_id: '', project_name: '', rooms: [] },
  { name: 'faceless', scope: 'global', display: 'Nomask', avatar: '',
    description: 'has no face at all', skills: [], provider: 'claude', model: '', effort: '',
    project_id: '', project_name: '', rooms: [] },
  { name: 'mangled', scope: 'global', display: 'Qmark', avatar: '??',
    description: 'face flattened by a console codepage', skills: [], provider: 'claude', model: '', effort: '',
    project_id: '', project_name: '', rooms: [] },
  { name: 'homed', scope: 'project', display: 'Homer', avatar: '',
    description: 'lives in one project', skills: [], provider: 'claude', model: '', effort: '',
    project_id: PID, project_name: 'Workflow Smoke', rooms: [] },
];
const FLOOR_JSON = JSON.stringify({ rooms: [], quiet: [], bench: BENCH, counts: {} });
// 1x1 transparent PNG — figure avatars are real <img> requests now.
const PNG_1PX = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
  'base64');

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

// Workflow Save now routes through the human-proof passcode modal (MC-995)
// instead of firing POST /api/workflows directly. Every page fixture in this
// file mocks /api/local-auth/status -> {configured:true}, so the modal
// always shows in plain-passcode mode; answer it with the fixture passcode
// if it appears (a no-op where the action being driven doesn't save).
const HP_PASSCODE = 'smoke-dash-passcode';
async function answerHumanProofModalIfShown(page, timeout = 1500) {
  const win = await page.waitForSelector('[data-modal-id^="__human-proof-"]', { timeout })
    .catch(() => null);
  if (!win) return false;
  await page.evaluate((passcode) => {
    const el = document.querySelector('[data-modal-id^="__human-proof-"]');
    const modalId = el.dataset.modalId;
    document.getElementById(`hp-passcode-${modalId}`).value = passcode;
    window._hpSubmit(modalId);
  }, HP_PASSCODE);
  return true;
}

async function setValue(page, selector, value) {
  await page.evaluate(({ selector, value }) => {
    const el = document.querySelector(selector);
    if (!el) throw new Error('setValue: not found ' + selector);
    el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }, { selector, value });
}

// MC-963 box view: the full editor (project/persona/prompt/outcomes/etc, the
// old `.wfb-node .wfb-node-own`) only ever exists inside `#wfb-inspector` now
// -- the canvas box itself carries no editable field. Every case below that
// used to read/type directly into a card now opens that node's inspector
// first via the real entry point (_wfOpenInspector), then addresses fields
// under `#wfb-inspector` (only ever one open at a time, so no name-scoping
// needed there).
async function openInspector(page, nodeName) {
  await page.evaluate((n) => window._wfOpenInspector(n), nodeName);
  await page.waitForSelector('#wfb-inspector.wfb-inspector-open', { timeout: 5000 });
}
async function closeInspector(page) {
  await page.evaluate(() => window._wfCloseInspector());
  await page.waitForTimeout(60);
}

// MC-963 added an "Edit" item at the top of the "..." node menu, ahead of
// Duplicate/Disconnect/Delete step -- so a menu item's POSITION is no longer
// a stable enough handle for the older cases here that click it by index.
// Select by its actual label instead.
async function clickMenuItem(page, label) {
  const handle = await page.evaluateHandle((text) => [...document.querySelectorAll('#wfb-node-menu > div')]
    .find(d => d.textContent.trim() === text), label);
  const el = handle.asElement();
  if (!el) throw new Error('clickMenuItem: menu item not found: ' + label);
  await el.click();
}

// A palette-drag → canvas drop, expressed as raw mouse events so it exercises
// the SAME pointerdown/pointermove/pointerup handlers a real drag fires
// (Playwright's page.mouse.* dispatches real pointer events, unlike
// page.dragAndDrop which is HTML5 DnD — the wrong gesture family per the
// spec's explicit "never HTML5 drag-and-drop" precedent, floor.js).
// `target` is {x, y}, or a function returning one — resolved AFTER the row is
// scrolled into view, because that scroll can move the canvas too.
//
// The palette is its own overflow:auto column and the tool tiles sit below the
// people, so with a real-sized bench they start below the fold. boundingBox()
// still reports coordinates for a row scrolled out of its container, and a
// mouse.down() there lands on whatever is actually painted at that point —
// the drag silently never starts. Scroll first, measure second.
async function dragFromPalette(page, handle, target) {
  const el = handle.asElement();
  if (!el) throw new Error('dragFromPalette: palette row not found');
  await el.scrollIntoViewIfNeeded();
  const { x: targetX, y: targetY } = typeof target === 'function' ? await target() : target;
  const box = await el.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 40, box.y + box.height / 2 + 40, { steps: 4 }); // clear the 8px slop
  await page.mouse.move(targetX, targetY, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(120);
}

// A PERSON row — the palette is the Bench, so this is how an agent step is
// created now (there is no generic "Agent step" tile any more).
async function dragPalettePersonTo(page, display, targetX, targetY) {
  const handle = await page.evaluateHandle((name) => [...document.querySelectorAll('.wfb-palette-person')]
    .find(r => (r.querySelector('.wfb-palette-person-name') || {}).textContent === name), display);
  return dragFromPalette(page, handle, targetY === undefined ? targetX : { x: targetX, y: targetY });
}

// One of the TOOLBAR tools ('Action' / 'Approval gate' / 'Wait') — moved out
// of the palette into `.wfb-toolbar-tools` (MC-871 follow-up: "these should
// go to the top of the canvas... to the left of Create/Run now").
async function dragPaletteToolTo(page, label, targetX, targetY) {
  const handle = await page.evaluateHandle((text) => [...document.querySelectorAll('.wfb-toolbar-tool')]
    .find(b => b.textContent.includes(text)), label);
  return dragFromPalette(page, handle, targetY === undefined ? targetX : { x: targetX, y: targetY });
}

async function toolbarToolBox(page, label) {
  const handle = await page.evaluateHandle((text) => [...document.querySelectorAll('.wfb-toolbar-tool')]
    .find(b => b.textContent.includes(text)), label);
  const el = handle.asElement();
  if (!el) throw new Error('toolbarToolBox: toolbar tool not found: ' + label);
  await el.scrollIntoViewIfNeeded();
  return el.boundingBox();
}

// A plain click/tap on a toolbar tool button — no drag at all. Exercises the
// `_wfPlaceUp` no-drag-happened branch (a toolbar button that only responds
// to dragging reads as broken).
async function clickToolbarTool(page, label) {
  const handle = await page.evaluateHandle((text) => [...document.querySelectorAll('.wfb-toolbar-tool')]
    .find(b => b.textContent.includes(text)), label);
  const el = handle.asElement();
  if (!el) throw new Error('clickToolbarTool: toolbar tool not found: ' + label);
  await el.scrollIntoViewIfNeeded();
  const box = await el.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.up();
  await page.waitForTimeout(120);
}

// MC-962: the "Check workflow" button (and Undo/Redo/Reset) share the plain
// `.wfb-toolbar-btn` class with no other hook — select by visible text.
async function clickToolbarBtn(page, label) {
  const handle = await page.evaluateHandle((text) => [...document.querySelectorAll('.wfb-toolbar-btn')]
    .find(b => b.textContent.trim().startsWith(text)), label);
  const el = handle.asElement();
  if (!el) throw new Error('clickToolbarBtn: toolbar button not found: ' + label);
  await el.click();
}

// Free canvas, measured NOW. Both halves matter: the modal body is a scroller
// and Playwright scrolls elements into view on click, so a box captured
// earlier in the run has moved; and cards accumulate, so a fixed offset
// eventually lands on one (which would silently exercise drop-onto-card
// instead of a plain placement).
async function emptyCanvasPoint(page) {
  const pt = await page.evaluate(() => {
    const vp = document.getElementById('wfb-canvas-viewport');
    if (!vp) return null;
    const r = vp.getBoundingClientRect();
    for (let y = r.bottom - 30; y > r.top + 25; y -= 20) {
      for (let x = r.right - 30; x > r.left + 25; x -= 20) {
        const el = document.elementFromPoint(x, y);
        // MC-871 Change 12a: also excludes the trigger tile — a "plain drop"
        // used for the no-auto-wire test must land somewhere that resolves
        // to the ordinary free-placement branch, not the trigger drop target.
        if (el && vp.contains(el) && !el.closest('.wfb-node') && !el.closest('.wfb-trigger-box')) return { x, y };
      }
    }
    return null;
  });
  if (!pt) throw new Error('emptyCanvasPoint: no free canvas left to drop onto');
  return pt;
}

// A safe drop point for the very FIRST node on a brand-new canvas. Unlike
// `emptyCanvasPoint` (which hunts near the viewport's corners -- fine once
// the viewport is its full, tall self with existing cards to dodge), MC-962's
// empty-canvas describe box now sits in normal flow ABOVE the viewport and
// shortens it considerably. A corner point there can leave most of a freshly
// dropped card clipped past the visible edge by `.wfb-canvas-viewport`'s own
// `overflow:hidden` -- including its OUTPUT port, which a later port-drag
// test needs to actually grab. This instead picks a point with margin on
// every side for the full card, centered under `_wfPlaceNodeAt`'s own math
// (`x - 130, y - 24`ish for a ~260x140 card), and clear of the trigger box
// (top-left, ~260x140) horizontally so vertical overlap with it never
// matters.
async function firstDropPoint(page) {
  return page.evaluate(() => {
    const vp = document.getElementById('wfb-canvas-viewport');
    const r = vp.getBoundingClientRect();
    const x = Math.min(r.left + 500, r.right - 140);
    const yMin = r.top + 30, yMax = Math.max(yMin, r.bottom - 120);
    const y = Math.min(Math.max(r.top + r.height / 2, yMin), yMax);
    return { x, y };
  });
}

async function portCenter(page, selector) {
  const el = await page.$(selector);
  if (!el) return null;
  const box = await el.boundingBox();
  if (!box) return null;
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

// Click an edge to SELECT it, away from its midpoint — Change 7 reveals a
// delete × exactly at the midpoint on hover (which a click also triggers),
// so a plain center-click (an SVG path's bounding-box center, or a fixed
// pixel offset guessed from a corner) can land on the × instead of the wire
// itself, and a curved path's bbox corner often isn't even ON the visible
// stroke. This reads the path's own cubic control points and tries several
// on-curve points, picking the first one `elementFromPoint` confirms is
// actually on the path itself — a short edge between two adjacent cards can
// put t=0.3 (or any single fixed fraction) inside the SOURCE card's own
// `.wfb-port-row` instead of on the open stroke, since the curve leaves the
// port at a shallow angle; trying a spread avoids depending on curve length/
// shape. Coordinates are converted from the SVG's viewport-relative space
// (_wfRedrawEdges measures everything relative to #wfb-canvas-viewport's own
// rect) to a real page coordinate.
async function clickEdgeOffCenter(page, selector) {
  // The `d` attribute is in the SVG's own (world/pan/zoom) coordinate space,
  // not screen pixels -- a prior version of this helper assumed the canvas
  // viewport's boundingBox origin could just be added to raw `d` numbers,
  // which only happens to hold when pan=(0,0) and scale=1. Any non-default
  // pan (several tests explicitly set one) makes that silently wrong. Let
  // the browser's own getScreenCTM() do the SVG->screen transform instead --
  // it's correct for any pan/zoom by construction.
  const candidates = await page.$eval(selector, (el) => {
    const len = el.getTotalLength();
    const at = (t) => {
      const p = el.getPointAtLength(t * len);
      const screen = p.matrixTransform(el.getScreenCTM());
      return { x: screen.x, y: screen.y };
    };
    // Spread away from both t=0/1 (source/target ports+cards) and t=0.5
    // (the delete × on hover). A tight card layout can put a wide/overshooting
    // Bezier (a short edge between adjacent cards can bulge well past either
    // endpoint's x) underneath a node at several of these t's at once -- the
    // wider spread here (closer to each end, and near-but-not-on center) gives
    // more chances to land on open stroke instead of a card that happens to
    // sit on the curve's path at this particular layout.
    return [0.35, 0.65, 0.25, 0.75, 0.2, 0.8, 0.1, 0.9, 0.15, 0.85, 0.45, 0.55].map(at);
  });
  for (const pt of candidates) {
    const onTarget = await page.evaluate(({ px, py, selector }) => {
      const el = document.elementFromPoint(px, py);
      return !!(el && el.closest(selector));
    }, { px: pt.x, py: pt.y, selector });
    if (onTarget) { await page.mouse.click(pt.x, pt.y); return; }
  }
  throw new Error(`clickEdgeOffCenter: no candidate point along the curve resolved to ${selector} via elementFromPoint`);
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

// MC-871 inline rehost: there is no floating builder modal any more. The
// canvas mounts into a project's OWN Workflows tab (`#wfb-inline-host-<pid>`,
// built by agent-console.js's loadWorkflows -> window._wfSyncTabsForProject),
// so every case below opens the project modal and switches to that tab
// first, exactly the real click path (openProjectModal -> "Workflows" tab).
async function openWorkflowsTab(page, pid) {
  await page.evaluate((pid) => { openProjectModal(pid); }, pid);
  await page.waitForTimeout(150);
  // The project modal defaults to 700px (`.modal-content` in app.css) -- far
  // narrower than the free canvas needs. A real user drags the existing
  // resize handles wider once and it's remembered (mc_modal_prefs); this
  // mirrors that so drop coordinates below land on the visible canvas rather
  // than past its right edge.
  // MC-871 follow-up: re-center after the resize, not just widen. `centerModalElement`
  // was already called once by `openProjectModal` above, but AT THE OLD 700px width --
  // `win.style.left` is a fixed `(innerWidth - 700) / 2`, so widening `.modal-content` in
  // place without recomputing `left` leaves the window's right edge exactly
  // (1180 - 700) / 2 = 240px further right than the ORIGINAL centering intended, which
  // this 1280px-wide test viewport has no headroom for. A wider toolbar (MC-871's
  // Action/Approval gate/Wait buttons, now to the left of Create/Run now) made this
  // start clipping Create/Run now past the browser's own viewport edge — invisible to
  // every assertion that reads coordinates via JS, but fatal to `page.click`, which
  // refuses to click something it computes as outside the viewport.
  await page.evaluate((pid) => {
    const win = document.querySelector(`.modal-window[data-modal-id="${pid}"]`);
    const content = win && win.querySelector('.modal-content');
    if (content) content.style.width = '1180px';
    if (win && typeof window.centerModalElement === 'function') window.centerModalElement(win);
  }, pid);
  await page.evaluate((pid) => { switchModalTab(pid, 'workflows'); }, pid);
  await page.waitForSelector(`#wfb-clayrune-section-${pid}`, { timeout: 5000 });
  await page.waitForTimeout(150); // let the tabs-row fetch (/api/workflows) land
}

// "+ New Workflow" (the tabs-row action window._wfNewWorkflowClick backs) —
// the inline equivalent of the old bare window.openWorkflowBuilder() call.
async function newWorkflow(page, pid) {
  await openWorkflowsTab(page, pid);
  await page.evaluate((pid) => { window._wfNewWorkflowClick(pid); }, pid);
  await page.waitForSelector('#wfb-canvas-viewport', { timeout: 5000 });
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

  const workflowPosts = [];
  // MC-871 follow-up (Item B smoke coverage): the live provider-auth probe
  // GET /api/agent/provider/<name>/auth (agent_routes.py:1339). Keyed by
  // provider name so different fixture providers can carry different
  // statuses in the same page -- tests mutate this map directly, no restart
  // needed since _wfEnsureProviderAuthFresh caches per-provider and this
  // route is read fresh on every request.
  const providerAuthResponses = {};
  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/local-auth/status') return route.fulfill({ status: 200, contentType: 'application/json', body: '{"configured":true}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/floor') return route.fulfill({ status: 200, contentType: 'application/json', body: FLOOR_JSON });
    if (path.startsWith('/api/avatars/')) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1PX });
    const authMatch = path.match(/^\/api\/agent\/provider\/([^/]+)\/auth$/);
    if (authMatch) {
      const name = authMatch[1];
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        name, installed: true, version: null, binary_path: null,
        auth_status: providerAuthResponses[name] || 'unknown', auth_method: null,
        auth_error_text: null, install_hint: '',
      }) });
    }
    // GET /api/workflows feeds the Workflows tab's tab row (MC-871 inline
    // rehost, window._wfSyncTabsForProject) -- empty here since this context
    // only ever authors a brand-new workflow.
    if (path === '/api/workflows' && req.method() === 'GET') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/workflows`) return route.fulfill({ status: 200, contentType: 'application/json', body: '{"workflows":[]}' });
    // Unmocked, these abort forever and agent-log.js's rail poller retries
    // in an unbounded tight loop -- enough iterations across a section's
    // many DOM waits crashed the renderer outright (page5, deterministic).
    if (path === `/api/project/${PID}/agent/log`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/conversations`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
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

  await newWorkflow(page, PID);
  ok('opened the Workflows tab and "+ New Workflow" mounted the canvas inline — canvas viewport present');

  // ── The palette IS the Bench (UI brief §2) ────────────────────────────────
  await page.waitForSelector('.wfb-palette-person', { timeout: 5000 });
  const paletteNames = await page.$$eval('.wfb-palette-person-name', els => els.map(e => e.textContent));
  (paletteNames.length === 5 && paletteNames[0] === 'Tobin')
    ? ok(`the palette lists the bench roster as people: ${JSON.stringify(paletteNames)}`)
    : fail(`expected the 5 bench people in the palette, got ${JSON.stringify(paletteNames)}`);
  const hasGenericAgentTile = await page.$$eval('.wfb-palette-person, .wfb-palette-more, .wfb-palette-hint',
    els => els.some(b => /agent step/i.test(b.textContent)));
  hasGenericAgentTile ? fail('a generic "Agent step" tile is still in the palette — drag PEOPLE, not primitives')
                      : ok('no generic "Agent step" tile — the only way to add an agent step is to drag a person');
  // MC-871 follow-up (Ron, verbatim): "these should go to the top of the
  // canvas screen same row as the create and run now buttons only to the
  // left of them" — the palette is people ONLY now; no tool tiles at all.
  const paletteHasTools = await page.$('.wfb-palette-block, .wfb-palette-tools-title');
  paletteHasTools ? fail('the palette still lists tool tiles — they must move to the toolbar')
                  : ok('the palette lists people only — Action/Approval gate/Wait are gone from it');
  // Action, Approval gate, Wait -- in that order (mirrors the reviewed
  // block-vocabulary proposal's Agent/Action/Human/Wait sequence, Decision
  // and Parallel deliberately absent as blocks --
  // docs/WORKFLOW_BLOCK_VOCABULARY_REVIEW.md), now living in the toolbar
  // immediately left of Create/Run now.
  const toolTiles = await page.$$eval('.wfb-toolbar-tool', els => els.map(b => b.textContent.replace(/\s+/g, ' ').trim()));
  (toolTiles.length === 3 && /Action/.test(toolTiles[0]) && /Approval gate/.test(toolTiles[1]) && /Wait/.test(toolTiles[2]))
    ? ok(`exactly three tools in the toolbar, in order: ${JSON.stringify(toolTiles)}`)
    : fail(`expected exactly Action, Approval gate, Wait (in order) in the toolbar, got ${JSON.stringify(toolTiles)}`);
  const toolbarToolsOrder = await page.$$eval('.wfb-toolbar > *', els => els.map(e => e.className || e.tagName));
  const toolsIdx = toolbarToolsOrder.findIndex(c => String(c).includes('wfb-toolbar-tools'));
  const createIdx = toolbarToolsOrder.findIndex(c => String(c).includes('btn-sched-save'));
  const runNowIdx = toolbarToolsOrder.findIndex(c => String(c).includes('btn-sched-cancel'));
  (toolsIdx > -1 && toolsIdx < createIdx && createIdx < runNowIdx)
    ? ok('the tool group sits in the toolbar row, to the left of Create and Run now')
    : fail(`expected the tool group before Create/Run now in the toolbar, order: ${JSON.stringify(toolbarToolsOrder)}`);
  const conceptHint = await page.$$eval('.wfb-palette-hint', els => els.map(e => e.textContent).join(' '));
  (/Decision or Parallel/.test(conceptHint) && /outcomes/.test(conceptHint) && /branch points/.test(conceptHint))
    ? ok('the palette teaches that outcomes/options ARE the branch points, in place of a fake Decision block')
    : fail(`expected the palette to explain Decision/Parallel discoverability, got: ${conceptHint}`);
  (/toolbar above/.test(conceptHint) && /Action/.test(conceptHint))
    ? ok('the palette hint now points Action/Approval gate/Wait at the toolbar instead of listing them itself')
    : fail(`expected the palette hint to redirect to the toolbar for Action/Approval gate/Wait, got: ${conceptHint}`);

  // Faces resolve the Floor's way: a real figure image, a real emoji, and the
  // initial mark ONLY where a character genuinely has no usable avatar.
  const faces = await page.$$eval('.wfb-palette-person', rows => rows.map(r => {
    const img = r.querySelector('img.av-fig');
    return {
      name: (r.querySelector('.wfb-palette-person-name') || {}).textContent,
      fig: !!img,
      figSrc: img ? img.getAttribute('src') : '',
      emoji: !!r.querySelector('.av-emoji'),
      initial: (r.querySelector('.wfb-face-initial') || {}).textContent || '',
      text: r.textContent,
    };
  }));
  const fTobin = faces.find(f => f.name === 'Tobin') || {};
  (fTobin.fig && /\/api\/avatars\/smith$/.test(fTobin.figSrc || ''))
    ? ok(`a fig: persona draws its REAL avatar image (${fTobin.figSrc}), not a letter bubble`)
    : fail(`expected Tobin's row to render the fig:smith image, got ${JSON.stringify(fTobin)}`);
  const fFenn = faces.find(f => f.name === 'Fenn') || {};
  (fFenn.emoji && !fFenn.initial)
    ? ok('an emoji persona draws the emoji face, not a letter bubble')
    : fail(`expected Fenn's row to render its emoji avatar, got ${JSON.stringify(fFenn)}`);
  const fNone = faces.find(f => f.name === 'Nomask') || {};
  (!fNone.fig && !fNone.emoji && fNone.initial === 'N')
    ? ok('a genuinely faceless persona falls back to its initial — the ONLY case that does')
    : fail(`expected the faceless persona to fall back to initial "N", got ${JSON.stringify(fNone)}`);
  // The Windows-console case: `??` is not a face, and must never reach the DOM
  // as text (mc/characters.clean_avatar's own reason for existing).
  const fBad = faces.find(f => f.name === 'Qmark') || {};
  (fBad.initial === 'Q' && !(fBad.text || '').includes('??'))
    ? ok('an unrenderable avatar ("??") falls THROUGH to the initial and is never echoed as text')
    : fail(`an unusable avatar leaked into the DOM or skipped the fallback: ${JSON.stringify(fBad)}`);

  await setValue(page, '.wfb-palette-search', 'fen');
  await page.waitForTimeout(80);
  const filtered = await page.$$eval('.wfb-palette-person-name', els => els.map(e => e.textContent));
  (filtered.length === 1 && filtered[0] === 'Fenn')
    ? ok('the bench search filters the palette to matching people')
    : fail(`expected the search to narrow the palette to Fenn, got ${JSON.stringify(filtered)}`);
  await setValue(page, '.wfb-palette-search', '');
  await page.waitForTimeout(80);

  // ── Drag a palette block onto the canvas ─────────────────────────────────
  // MC-962: the describe box is now a normal-flow sibling ABOVE the
  // viewport (no longer an overlay ON it), so on a brand-new empty canvas
  // the viewport itself is shorter than it used to be -- a fixed
  // vpBox.y+240 offset can now land past its bottom edge, in
  // `.wfb-modal-body` beyond it. `firstDropPoint` picks a point that leaves
  // room for the whole card (port included) inside the shrunk viewport.
  await dragPalettePersonTo(page, 'Tobin', () => firstDropPoint(page));
  let nodeCount = await page.$$eval('.wfb-node', els => els.length);
  nodeCount === 1 ? ok('dragging a PERSON from the palette placed one agent step on the canvas')
                  : fail(`expected 1 node after the palette drag, got ${nodeCount}`);

  // MC-871 agent-card mobile pass, defect 1 (Ron: "I still see the prompt as
  // gray text" -- the Prompt field's hint, not the body a prior pass already
  // collapsed). The raw-syntax hint duplicated what the Insert control below
  // it already says in plain English, and at a card's fixed 260px width it
  // wrapped to enough lines to read as a wall of disabled-looking content --
  // deleted outright rather than just shrunk. This guards the regression:
  // the bare "Prompt" label survives, with no `.memory-hint` sibling, and the
  // Insert control (which made the hint redundant) is still there doing the
  // explaining.
  // MC-963 box view: this editor only exists in the inspector now -- the box
  // itself never carries it (ground rule: "No inline textareas or dropdowns
  // on the canvas"). Open the freshly-placed node's inspector to reach it.
  const autoName1 = await page.$eval('.wfb-node', el => el.dataset.name);
  await openInspector(page, autoName1);
  const promptLabelInfo = await page.evaluate(() => {
    const label = Array.from(document.querySelectorAll('#wfb-inspector label'))
      .find(l => l.textContent.trim().startsWith('Prompt'));
    return {
      text: label ? label.textContent.trim() : null,
      hasHint: !!(label && label.querySelector('.memory-hint')),
      hasInsertSelect: !!document.querySelector('#wfb-inspector .wfb-insert-btn'),
    };
  });
  (promptLabelInfo.text === 'Prompt' && !promptLabelInfo.hasHint && promptLabelInfo.hasInsertSelect)
    ? ok('the Prompt field has no raw-syntax hint wall any more (bare "Prompt" label), and the Insert control that made it redundant is still present')
    : fail(`expected a bare "Prompt" label with no .memory-hint and the Insert control present, got ${JSON.stringify(promptLabelInfo)}`);

  // Field edits sync into the model (and `data-name` with them) only at the
  // moment of the NEXT structural action, not on every keystroke (file
  // header: "typing in one node's prompt is never clobbered by placing a new
  // block or dragging an edge elsewhere") — so the rename below won't be
  // reflected in `data-name` until the connect drag triggers a sync+render.
  // The inspector is still open on this node from the check above.
  await setValue(page, '#wfb-inspector .wfb-name', 'triage');
  await setValue(page, '#wfb-inspector .wfb-prompt', 'Decide whether this is worth drafting.');
  // MC-963: close before the next palette drag -- the inspector narrows the
  // canvas viewport (flex sibling), and the drop-target coordinates below are
  // computed off the ORIGINAL (pre-inspector) `vpBox`.
  await closeInspector(page);

  // Anchor the second drop off the FIRST node's own rendered box, not a
  // vpBox-relative constant -- a fixed `vpBox.x + 460` only happened to land
  // far enough right of wherever `firstDropPoint` put node 1 by coincidence
  // of both formulas' magic numbers. A purely horizontal clearance off node 1
  // (tried first) instead ran the OTHER way off a narrow viewport: two 260px
  // cards side by side plus any real gap don't fit in an ~888px-wide canvas
  // when node 1 already sits center-ish, so node 2's far port rendered past
  // the viewport's right edge -- clipped by its `overflow:hidden`, so
  // elementFromPoint there hits the page behind the canvas, not the port, and
  // the connect drag never starts (no pointerdown ever reaches it). Placing
  // node 2 mostly BELOW node 1 instead (vertical clearance past node 1's own
  // bottom, only a small rightward nudge) avoids overlap without needing
  // horizontal room this viewport doesn't have, and is clamped to the live
  // viewport rect so it also can't run off any other edge.
  const node1Box = await (await page.$('.wfb-node')).boundingBox();
  const vpForDrop = await (await page.$('#wfb-canvas-viewport')).boundingBox();
  const dropMargin = 20, halfW = 130, halfH = 45;
  const dropX = Math.min(
    Math.max(node1Box.x + node1Box.width / 2 + 40, vpForDrop.x + dropMargin + halfW),
    vpForDrop.x + vpForDrop.width - dropMargin - halfW);
  const dropY = Math.min(
    Math.max(node1Box.y + node1Box.height + dropMargin + halfH, vpForDrop.y + dropMargin + halfH),
    vpForDrop.y + vpForDrop.height - dropMargin - halfH);
  await dragPalettePersonTo(page, 'Fenn', dropX, dropY);
  nodeCount = await page.$$eval('.wfb-node', els => els.length);
  nodeCount === 2 ? ok('a second palette drag placed a second, independent node')
                  : fail(`expected 2 nodes, got ${nodeCount}`);
  // The whole point of dragging a person: the persona is already chosen, and
  // the card leads with their face rather than a generic type label.
  const preset = await page.evaluate(() => {
    const n = window._wfEntry()._wf.def.nodes[0];
    return { character: n.character, project_id: n.project_id };
  });
  preset.character === 'global:builder'
    ? ok(`the dropped person arrived with its persona already set (character=${preset.character}) — no "pick a persona" step`)
    : fail(`expected character "global:builder" on the dropped node, got ${JSON.stringify(preset)}`);
  const headFace = await page.$eval('.wfb-node .wfb-node-head', el => ({
    fig: !!el.querySelector('img.av-fig'),
    persona: (el.querySelector('.wfb-node-persona') || {}).textContent || '',
  }));
  (headFace.fig && headFace.persona === 'Tobin')
    ? ok('the agent card header shows the real face and name of the persona dragged in')
    : fail(`expected the card header to lead with Tobin's avatar, got ${JSON.stringify(headFace)}`);
  // Placing the second block synced the model (per `_wfPlaceNodeAt`), so the
  // first node's typed rename has already landed and `data-name` reflects it.
  const renamedOk = await page.$eval(`.wfb-node[data-name="triage"]`, () => true).catch(() => false);
  renamedOk ? ok('a typed rename in one node survives placing a second, independent block (no clobber)')
            : fail('placing a second block clobbered an unsynced rename in the first node');
  const autoName2 = await page.$eval('.wfb-node:not([data-name="triage"])', el => el.dataset.name);
  await openInspector(page, autoName2);
  await setValue(page, '#wfb-inspector .wfb-name', 'draft');
  await setValue(page, '#wfb-inspector .wfb-prompt', 'Draft a post from {{steps.triage.output}}.');
  // MC-963: editing only ever happens in the inspector now, and its close is
  // what syncs the DOM into the model (_wfSyncDomToModel inside
  // _wfCloseInspector) -- that's the "structural action" the old comment here
  // pinned to the next canvas drag. Close before the connect drag below, both
  // to commit the rename and so the inspector's 320px panel isn't narrowing
  // the canvas viewport under the drag's port-position math.
  await closeInspector(page);

  // ── Connect: drag triage's (single, unconditional) output port to draft's
  // input port. Both renames are already committed by the inspector closes
  // above, so both sides address their real, post-rename names directly. ──
  await dragPortTo(page,
    '.wfb-node[data-name="triage"] .wfb-port-out',
    '.wfb-node[data-name="draft"] .wfb-port-in');
  let edgeCount = await page.$$eval('.wfb-edge-path', els => els.length);
  edgeCount === 1 ? ok('drag from an output port to an input port drew one edge, addressing both nodes by their inspector-committed names')
                  : fail(`expected 1 edge after connecting, got ${edgeCount}`);

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
  // Click near a CORNER of the path's bounding box, not dead center: Change 7
  // reveals a delete × exactly at the edge's midpoint on hover, and a plain
  // center-click (Playwright's default) would now land on that × instead of
  // selecting the path underneath it — clicking off-center exercises the
  // ordinary "select the wire" gesture the way a user's cursor usually does.
  await clickEdgeOffCenter(page, '.wfb-edge-path'); // select the (only) real edge
  await page.keyboard.press('Delete');
  await page.waitForTimeout(120);
  edgeCount = await page.$$eval('.wfb-edge-path', els => els.length);
  edgeCount === 1 ? ok('the slot-break guard REFUSED deleting the edge {{steps.triage.output}} depends on')
                  : fail(`expected the guard to keep the edge (count 1), got ${edgeCount}`);
  const breakToasts = await page.evaluate(() => window.__toasts);
  breakToasts.some(t => /steps\.triage/.test(t)) ? ok(`a toast named the broken reference: "${breakToasts.find(t => /steps\.triage/.test(t))}"`)
                                                  : fail(`expected a toast naming {{steps.triage...}}, got ${JSON.stringify(breakToasts)}`);


  // ── The `+` on a port: "After <label>, run…" auto-places AND wires, so a
  // pipeline can be built without drawing a single arrow (UI brief §4). ─────
  const nodesBeforePlus = await page.$$eval('.wfb-node', els => els.length);
  const edgesBeforePlus = await page.$$eval('.wfb-edge-path', els => els.length);
  await page.click('.wfb-node[data-name="draft"] .wfb-port-plus');
  await page.waitForSelector('#wfb-port-popover', { timeout: 3000 });
  ok('clicking a port + opens the "After …, run…" popover');
  const popoverOffers = await page.$$eval('#wfb-port-popover .wfb-popover-row',
    els => els.map(e => e.textContent.replace(/\s+/g, ' ').trim()));
  const popoverPeople = await page.$$eval('#wfb-port-popover .wfb-popover-person-name', els => els.map(e => e.textContent));
  (popoverOffers.length === 3 && /Action/.test(popoverOffers[0]) && /Approval gate/.test(popoverOffers[1])
      && /Wait/.test(popoverOffers[2]) && popoverPeople.length === 5)
    ? ok(`the popover offers Action, Approval gate, Wait and ${popoverPeople.length} people`)
    : fail(`popover contents wrong: rows=${JSON.stringify(popoverOffers)} people=${JSON.stringify(popoverPeople)}`);
  const popoverFaces = await page.$$eval('#wfb-port-popover .wfb-popover-person',
    rows => ({ figs: rows.filter(r => r.querySelector('img.av-fig')).length,
               initials: rows.filter(r => r.querySelector('.wfb-face-initial')).length }));
  (popoverFaces.figs === 1 && popoverFaces.initials === 3)
    ? ok('popover people carry the same resolved faces as the palette (1 figure image, 3 initial fallbacks)')
    : fail(`popover faces did not resolve like the palette: ${JSON.stringify(popoverFaces)}`);

  const popoverRows = await page.$$('#wfb-port-popover .wfb-popover-row');
  await popoverRows[1].click(); // [0] Action, [1] Approval gate
  await page.waitForTimeout(150);
  const nodesAfterPlus = await page.$$eval('.wfb-node', els => els.length);
  const edgesAfterPlus = await page.$$eval('.wfb-edge-path', els => els.length);
  (nodesAfterPlus === nodesBeforePlus + 1 && edgesAfterPlus === edgesBeforePlus + 1)
    ? ok('picking from the + popover placed the new card AND wired the edge — no arrow drawn by hand')
    : fail(`expected +1 node and +1 edge from the popover pick, got nodes ${nodesBeforePlus}->${nodesAfterPlus}, edges ${edgesBeforePlus}->${edgesAfterPlus}`);
  const wiredFromDraft = await page.evaluate(() => {
    const def = window._wfEntry()._wf.def;
    const e = def.edges[def.edges.length - 1];
    return { from: e.from, toType: (def.nodes.find(n => n.name === e.to) || {}).type };
  });
  (wiredFromDraft.from === 'draft' && wiredFromDraft.toType === 'approval')
    ? ok('the new edge runs from the port that was clicked to the approval gate that was picked')
    : fail(`expected an edge draft -> approval, got ${JSON.stringify(wiredFromDraft)}`);
  await page.evaluate(() => { document.getElementById('wfb-port-popover')?.remove(); });

  // ── MC-871 Change 12b — declared-vocabulary ports (an approval gate's
  // options, an agent's outcomes) render as pills INSIDE the card body, but
  // the PORT DOT itself must sit on the card's right BORDER, not inside it
  // (Ron's screenshot: every dot sat inside the tile, indistinguishable from
  // decoration). Uses the approval gate node the popover pick just made,
  // which ships with two default options ("approve"/"reject") out of the box. ─
  const approvalNodeName = wiredFromDraft && (await page.evaluate((toType) => {
    const def = window._wfEntry()._wf.def;
    return (def.nodes.find(n => n.type === toType) || {}).name;
  }, 'approval'));
  const c12bCardBox = await (await page.$(`.wfb-node[data-name="${approvalNodeName}"]`)).boundingBox();
  const c12bPortBox = await (await page.$(`.wfb-node[data-name="${approvalNodeName}"] .wfb-vocab-row .wfb-port`)).boundingBox();
  const c12bPortCenterX = c12bPortBox.x + c12bPortBox.width / 2;
  // "On the edge" = the dot's center sits close to the card's own right
  // border, not buried well inside its content width.
  const c12bDistFromEdge = Math.abs(c12bPortCenterX - (c12bCardBox.x + c12bCardBox.width));
  c12bDistFromEdge < 12
    ? ok(`an outcome/option port dot sits on the card's right edge (${c12bDistFromEdge.toFixed(1)}px from it), not inside the tile`)
    : fail(`expected the port dot within ~12px of the card's right edge, got ${c12bDistFromEdge.toFixed(1)}px away (card right=${c12bCardBox.x + c12bCardBox.width}, dot center=${c12bPortCenterX})`);
  // Edges still land on the dot after the move, INCLUDING off default zoom —
  // _wfRedrawEdges measures real getBoundingClientRect()s at draw time, so a
  // real zoom gesture (mouse wheel -> _wfCanvasWheel) should carry the curve
  // with no JS change needed for this fix.
  const c12bEdgesBeforeZoom = await page.$$eval('.wfb-edge-path', els => els.length);
  const c12bVpBox = await (await page.$('#wfb-canvas-viewport')).boundingBox();
  await page.mouse.move(c12bVpBox.x + c12bVpBox.width / 2, c12bVpBox.y + c12bVpBox.height / 2);
  await page.mouse.wheel(0, -400); // zoom in (negative deltaY per _wfCanvasWheel's exp(-deltaY*k))
  await page.waitForTimeout(120);
  const c12bScaleAfter = await page.evaluate(() => window._wfEntry()._wf.viewport.scale);
  const c12bPortBoxZoomed = await (await page.$(`.wfb-node[data-name="${approvalNodeName}"] .wfb-vocab-row .wfb-port`)).boundingBox();
  const c12bEdgeAtZoom = await page.$$eval('g.wfb-edge-group path.wfb-edge-path', (els, target) => {
    return els.some((el) => {
      const d = el.getAttribute('d') || '';
      const m = /M\s*([\d.-]+),([\d.-]+)/.exec(d);
      if (!m) return false;
      // The edge starts (M) at its own from-port; just confirm SOME edge
      // still resolves to a real path string near the viewport (non-empty,
      // finite) post-zoom -- a stale/mis-measured port would produce NaN.
      return Number.isFinite(parseFloat(m[1])) && Number.isFinite(parseFloat(m[2]));
    });
  }, null);
  (c12bScaleAfter !== 1 && c12bPortBoxZoomed && c12bEdgeAtZoom && await page.$$eval('.wfb-edge-path', els => els.length) === c12bEdgesBeforeZoom)
    ? ok(`zoom changed to scale ${c12bScaleAfter.toFixed(2)} and the edge layer still redraws with valid, finite coordinates (edge count unchanged: ${c12bEdgesBeforeZoom})`)
    : fail(`edge layer broke under zoom: scale=${c12bScaleAfter}, portBox=${JSON.stringify(c12bPortBoxZoomed)}, edgeValid=${c12bEdgeAtZoom}`);

  // MC-871 agent-card mobile pass, defect 2 (Ron: "when zooming in it goes
  // out of boundaries"). Zoom further still (past the code's own 2.5x cap,
  // to prove the cap plus the clip both hold) and confirm no canvas content
  // (a card, a port, an edge) is ever hit-testable OUTSIDE #wfb-canvas-
  // viewport's own box -- elementFromPoint respects real overflow clipping,
  // so this is a direct proof the transform on .wfb-canvas-world never
  // escapes its ancestor's overflow:hidden, at a zoom level well past what a
  // default-zoom-only test would ever exercise.
  // Zoom is exponential (factor = exp(-deltaY*k)), so an equal count of
  // opposite-signed wheel events does NOT round-trip once either clamp is
  // hit -- save the exact pre-check viewport here and restore it directly
  // afterward, rather than trying to "undo" with more wheel events.
  const c2ViewportBefore = await page.evaluate(() => ({ ...window._wfEntry()._wf.viewport }));
  for (let i = 0; i < 6; i++) await page.mouse.wheel(0, -400); // drive well past the 2.5x clamp
  await page.waitForTimeout(150);
  const c2ScaleClamped = await page.evaluate(() => window._wfEntry()._wf.viewport.scale);
  c2ScaleClamped <= 2.5
    ? ok(`repeated zoom-in stays clamped at the code's own ceiling (scale=${c2ScaleClamped.toFixed(2)}, cap 2.5)`)
    : fail(`zoom exceeded its documented 2.5x cap: scale=${c2ScaleClamped.toFixed(2)}`);
  const c2VpBox = await (await page.$('#wfb-canvas-viewport')).boundingBox();
  const c2Escape = await page.evaluate((vp) => {
    const probe = (x, y) => {
      const el = document.elementFromPoint(x, y);
      return !!(el && el.closest && el.closest('.wfb-canvas-world'));
    };
    const midX = vp.x + vp.width / 2, midY = vp.y + vp.height / 2;
    return {
      // 4px outside each edge, at the midpoint of that edge -- inside the
      // canvas world's painted area if (and only if) clipping has failed.
      left: probe(vp.x - 4, midY), right: probe(vp.x + vp.width + 4, midY),
      top: probe(midX, vp.y - 4), bottom: probe(midX, vp.y + vp.height + 4),
    };
  }, c2VpBox);
  (!c2Escape.left && !c2Escape.right && !c2Escape.top && !c2Escape.bottom)
    ? ok(`at ${c2ScaleClamped.toFixed(2)}x zoom, nothing from the canvas world paints outside #wfb-canvas-viewport's own box on any of the 4 edges`)
    : fail(`canvas content escaped its viewport's clip at ${c2ScaleClamped.toFixed(2)}x zoom: ${JSON.stringify(c2Escape)}`);
  // Restore the exact pre-check viewport directly (see comment above on why
  // not more wheel events), so the existing single wheel(0,400) reset right
  // below still lands back at ~1.0 exactly as it did before this block.
  await page.evaluate((vp) => {
    const st = window._wfEntry()._wf;
    st.viewport.x = vp.x; st.viewport.y = vp.y; st.viewport.scale = vp.scale;
    const world = document.getElementById('wfb-world');
    if (world) world.style.transform = `translate(${vp.x}px, ${vp.y}px) scale(${vp.scale})`;
  }, c2ViewportBefore);
  await page.waitForTimeout(80);
  // Reset the zoom this check just changed -- every drop/drag test AFTER
  // this point computes its target coordinates assuming scale 1, same as
  // when they were written; leaving the canvas zoomed in ~1.8x would silently
  // break their geometry (cards ~1.8x bigger on screen, drop points landing
  // somewhere else entirely).
  await page.mouse.wheel(0, 400);
  await page.waitForTimeout(80);
  const c12bScaleReset = await page.evaluate(() => window._wfEntry()._wf.viewport.scale);
  Math.abs(c12bScaleReset - 1) < 0.05
    ? ok(`zoom reset back to ~1.0 (${c12bScaleReset.toFixed(2)}) before the remaining drop/drag geometry tests`)
    : fail(`zoom did not reset to 1.0, got ${c12bScaleReset.toFixed(2)} — later coordinate-based tests will be unreliable`);

  // ── Dropping a person ONTO a card = the same thing as that card's + ──────
  const nodesBeforeDrop = await page.$$eval('.wfb-node', els => els.length);
  const edgesBeforeDrop = await page.$$eval('.wfb-edge-path', els => els.length);
  const triageBox = await (await page.$('.wfb-node[data-name="triage"] .wfb-node-head')).boundingBox();
  await dragPalettePersonTo(page, 'Homer', triageBox.x + triageBox.width / 2, triageBox.y + triageBox.height / 2);
  const nodesAfterDrop = await page.$$eval('.wfb-node', els => els.length);
  const edgesAfterDrop = await page.$$eval('.wfb-edge-path', els => els.length);
  (nodesAfterDrop === nodesBeforeDrop + 1 && edgesAfterDrop === edgesBeforeDrop + 1)
    ? ok('dropping a person onto an existing card placed it after that card and wired the edge')
    : fail(`expected +1 node and +1 edge from drop-onto-card, got nodes ${nodesBeforeDrop}->${nodesAfterDrop}, edges ${edgesBeforeDrop}->${edgesAfterDrop}`);
  // Brief §8c rule 1: a dropped person defaults its project to that persona's
  // HOME ROOM, not to whatever project happens to be first in the list.
  const homed = await page.evaluate(() => {
    const def = window._wfEntry()._wf.def;
    return def.nodes.find(n => n.character === 'project:homed') || null;
  });
  (homed && homed.project_id === 'smoke_wf')
    ? ok(`a project-scoped persona defaulted to its home room (project_id=${homed.project_id}), not the first project in the list`)
    : fail(`expected the dropped persona to default to its home room smoke_wf, got ${JSON.stringify(homed)}`);

  // ── A Clayrune action block, dragged in separately (not wired to
  // anything) — exercises the third palette block + the unconnected-port
  // "stop stub" render path (R2-D6). ───────────────────────────────────────
  const nodesBeforeAction = await page.$$eval('.wfb-node', els => els.length);
  await dragPaletteToolTo(page, 'Action', () => emptyCanvasPoint(page));
  nodeCount = await page.$$eval('.wfb-node', els => els.length);
  nodeCount === nodesBeforeAction + 1
    ? ok('the Action tool tile placed an action node from the palette')
    : fail(`expected one more node after placing the action block, got ${nodesBeforeAction} -> ${nodeCount}`);
  const stubCount = await page.$$eval('.wfb-port-row.wfb-port-unconnected', els => els.length);
  stubCount > 0 ? ok(`${stubCount} unconnected port(s) render a stop stub`)
                : fail('expected at least one unconnected port to render a stop stub');

  // ── MC-871 Change 8: panning/dragging must never select card text ───────
  const selectionBefore = await page.evaluate(() => (window.getSelection() || {}).toString());
  const vpForPan = await (await page.$('#wfb-canvas-viewport')).boundingBox();
  await page.mouse.move(vpForPan.x + 30, vpForPan.y + 30);
  await page.mouse.down();
  await page.mouse.move(vpForPan.x + 200, vpForPan.y + 150, { steps: 10 }); // sweeps across card text
  await page.mouse.up();
  await page.waitForTimeout(80);
  const selectionAfterPan = await page.evaluate(() => (window.getSelection() || {}).toString());
  (!selectionAfterPan || selectionAfterPan === selectionBefore)
    ? ok('panning the canvas across card text selects nothing')
    : fail(`panning selected text: "${selectionAfterPan}"`);
  const canvasUserSelect = await page.evaluate(() => getComputedStyle(document.getElementById('wfb-canvas-viewport')).userSelect);
  canvasUserSelect === 'none' ? ok('the canvas viewport is user-select:none at rest')
                              : fail(`expected the canvas viewport to be user-select:none, got "${canvasUserSelect}"`);
  // MC-963: the prompt textarea lives only in the inspector now -- open
  // "triage"'s to reach it.
  await openInspector(page, 'triage');
  const promptUserSelect = await page.evaluate(() => getComputedStyle(document.querySelector('#wfb-inspector .wfb-prompt')).userSelect);
  (promptUserSelect === 'text' || promptUserSelect === 'auto')
    ? ok(`a card's own prompt textarea stays selectable/editable (user-select: ${promptUserSelect})`)
    : fail(`expected the prompt textarea to allow selection, got "${promptUserSelect}"`);
  // Selecting actual text INSIDE a field must still work (Change 8's other
  // explicit requirement — don't blanket-kill selection in form fields).
  await page.click('#wfb-inspector .wfb-prompt');
  await page.keyboard.press(process.platform === 'darwin' ? 'Meta+A' : 'Control+A');
  const fieldSelectionLen = await page.evaluate(() => {
    const el = document.activeElement;
    return el && typeof el.selectionStart === 'number' ? (el.selectionEnd - el.selectionStart) : -1;
  });
  fieldSelectionLen > 0 ? ok('selecting text WITHIN a prompt field still works (Ctrl/Cmd+A selected it)')
                        : fail(`expected a non-empty in-field selection, got length ${fieldSelectionLen}`);
  await closeInspector(page);

  // ── MC-871 Change 5 — "forgiving drop": a connect-drag completes on a drop
  // anywhere on the target CARD, not only its 40px in-port hit box. ────────
  // Places the pair directly in the model rather than via emptyCanvasPoint's
  // bottom-right scan: the canvas already carries 5 cards at 260x512 each by
  // this point, and a scan that only checks the single DROP PIXEL is free
  // (not the whole eventual card footprint) can still land two new 512px-tall
  // cards overlapping each other or their neighbours -- exactly the kind of
  // ambiguous geometry this test needs to NOT have, since it's testing the
  // CONNECT gesture, not placement (already covered above). A real
  // _wfMarkDirty()+render() follows so the drag below exercises real DOM/
  // pointer handling, not a synthetic state.
  const preC5Names = await page.evaluate(() => window._wfEntry()._wf.def.nodes.map(n => n.name));
  const c5Pair = await page.evaluate(() => {
    const st = window._wfEntry()._wf;
    const a = { type: 'agent', name: 'c5-a', x: -900, y: -900, project_id: '', character: '', prompt: '', outcomes: [] };
    const b = { type: 'agent', name: 'c5-b', x: -560, y: -900, project_id: '', character: '', prompt: '', outcomes: [] };
    st.def.nodes = (st.def.nodes || []).concat([a, b]);
    // Pan the new pair into view, placed far off in world space (-900) so
    // they can't possibly overlap the crowded cluster of existing cards.
    // Cards render ~512px tall (agent card, Insert dropdown + slot chips +
    // outcomes) -- TALLER than the canvas viewport itself at this window
    // size (~440-460px) -- so centering on the card's MIDPOINT puts its own
    // top (and the head this test drags to) in the clipped region above the
    // viewport (overflow:hidden), landing on .wfb-toolbar instead of the
    // card. Center on the HEAD near the card's top instead (a small offset,
    // not half the card height), which is the only part this test needs
    // on-screen.
    const vp = document.getElementById('wfb-canvas-viewport');
    const rect = vp.getBoundingClientRect();
    st.viewport.x = rect.width / 2 - (a.x + 260) * st.viewport.scale;
    st.viewport.y = rect.height / 2 - (a.y + 60) * st.viewport.scale;
    window._wfMarkDirty();
    // No direct "just render" export exists; _wfSetTriggerType is a real,
    // idempotent (same value in/out) exported mutator that ends in the one
    // _wfRender() every structural change here goes through, including
    // applying the viewport.x/y set just above -- reused rather than adding
    // a render-only export for test convenience alone.
    window._wfSetTriggerType(st.def.trigger.type || 'manual');
    return ['c5-a', 'c5-b'];
  });
  await page.waitForTimeout(100);
  c5Pair.length === 2 ? ok(`placed a fresh, well-separated unconnected pair for the forgiving-drop test: ${JSON.stringify(c5Pair)}`)
                       : fail(`expected 2 fresh nodes, got ${JSON.stringify(c5Pair)}`);
  const [c5From, c5To] = c5Pair;
  const c5EdgesBefore = await page.evaluate(() => window._wfEntry()._wf.def.edges.length);
  // Drop on the target's HEAD (its name/face), never its in-port — the exact
  // gap Ron hit ("no way to connect a tile to another unless triggered by
  // the small plus icon").
  const c5FromPort = await portCenter(page, `.wfb-node[data-name="${c5From}"] .wfb-port-out`);
  const c5ToHead = await (await page.$(`.wfb-node[data-name="${c5To}"] .wfb-node-head`)).boundingBox();
  await page.mouse.move(c5FromPort.x, c5FromPort.y);
  await page.mouse.down();
  await page.mouse.move((c5FromPort.x + c5ToHead.x) / 2, (c5FromPort.y + c5ToHead.y) / 2, { steps: 6 });
  await page.mouse.move(c5ToHead.x + c5ToHead.width / 2, c5ToHead.y + c5ToHead.height / 2, { steps: 6 });
  const c5Highlighted = await page.evaluate((to) => document.querySelector(`.wfb-node[data-name="${to}"]`).classList.contains('wfb-connect-target'), c5To);
  c5Highlighted ? ok('the whole target card highlights while a connect-drag hovers it, not just the in-port')
                : fail('the target card did not highlight as a drop target mid-drag');
  await page.mouse.up();
  await page.waitForTimeout(120);
  const c5EdgesAfter = await page.evaluate(() => window._wfEntry()._wf.def.edges);
  const c5NewEdge = c5EdgesAfter.find(e => e.from === c5From && e.to === c5To);
  (c5EdgesAfter.length === c5EdgesBefore + 1 && c5NewEdge)
    ? ok(`dropping on the card BODY (not the in-port) connected ${c5From} -> ${c5To} — edge landed in def.edges`)
    : fail(`forgiving drop did not add the edge: before=${c5EdgesBefore} after=${c5EdgesAfter.length} found=${JSON.stringify(c5NewEdge)}`);

  // ── MC-871 (Ron's screenshot: the output dot sat INSIDE the card, over the
  // prompt field, with the "+" and the stub pushed past the edge to its
  // right) — confirms the PLAIN single-port case (c5-a, an agent step with
  // no outcomes) now anchors the same way c12b already proved for the
  // vocabulary-pill case above, at the tight ~2px tolerance Ron's "centre
  // sits on the edge line" mockup actually asks for (c12b's own check above
  // uses a looser 12px band, which a container-based fix could pass while
  // still being visibly off). ────────────────────────────────────────────
  const mc871PlainCardBox = await (await page.$(`.wfb-node[data-name="${c5From}"]`)).boundingBox();
  const mc871PlainPortBox = await (await page.$(`.wfb-node[data-name="${c5From}"] .wfb-ports-out .wfb-port`)).boundingBox();
  const mc871PlainDist = Math.abs((mc871PlainPortBox.x + mc871PlainPortBox.width / 2) - (mc871PlainCardBox.x + mc871PlainCardBox.width));
  mc871PlainDist < 2
    ? ok(`a single (plain) output port dot sits within 2px of the card's right edge (${mc871PlainDist.toFixed(2)}px)`)
    : fail(`expected the plain port dot within 2px of the card's right edge, got ${mc871PlainDist.toFixed(2)}px away`);
  const mc871VocabCardBox = await (await page.$(`.wfb-node[data-name="${approvalNodeName}"]`)).boundingBox();
  const mc871VocabPortBox = await (await page.$(`.wfb-node[data-name="${approvalNodeName}"] .wfb-vocab-row .wfb-port`)).boundingBox();
  const mc871VocabDist = Math.abs((mc871VocabPortBox.x + mc871VocabPortBox.width / 2) - (mc871VocabCardBox.x + mc871VocabCardBox.width));
  mc871VocabDist < 2
    ? ok(`a multi-outcome port dot sits within 2px of the card's right edge (${mc871VocabDist.toFixed(2)}px)`)
    : fail(`expected the outcome port dot within 2px of the card's right edge, got ${mc871VocabDist.toFixed(2)}px away`);
  // Now pan AND zoom, then confirm the c5-a -> c5-b edge's SVG path still
  // starts exactly at the live plain port's real getBoundingClientRect()
  // center — an edge that starts even a few px off its dot is the obvious
  // regression moving the dot's anchoring could introduce. _wfRedrawEdges
  // measures the real DOM rect at draw time, so this exercises the actual
  // fix, not a snapshot of intent.
  const mc871ViewportBefore = await page.evaluate(() => ({ ...window._wfEntry()._wf.viewport }));
  await page.mouse.move(mc871PlainCardBox.x + 40, mc871PlainCardBox.y + 40);
  await page.mouse.wheel(0, -200); // zoom in a bit
  await page.waitForTimeout(100);
  await page.evaluate(() => {
    const st = window._wfEntry()._wf;
    st.viewport.x += 37; st.viewport.y -= 23; // pan
    window._wfMarkDirty();
    window._wfSetTriggerType(st.def.trigger.type || 'manual'); // idempotent, forces the one render path
  });
  await page.waitForTimeout(100);
  const mc871PanZoomCheck = await page.evaluate((from) => {
    const vp = document.getElementById('wfb-canvas-viewport');
    const rect = vp.getBoundingClientRect();
    const portEl = document.querySelector(`.wfb-port-out[data-node="${from}"][data-when=""]`);
    const pr = portEl.getBoundingClientRect();
    const wantX = pr.left + pr.width / 2 - rect.left, wantY = pr.top + pr.height / 2 - rect.top;
    for (const p of document.querySelectorAll('g.wfb-edge-group path.wfb-edge-path')) {
      const m = /^M\s*([\d.-]+),([\d.-]+)/.exec(p.getAttribute('d') || '');
      if (!m) continue;
      const dx = Math.abs(parseFloat(m[1]) - wantX), dy = Math.abs(parseFloat(m[2]) - wantY);
      if (dx < 2 && dy < 2) return { found: true, dx, dy };
    }
    return { found: false };
  }, c5From);
  mc871PanZoomCheck.found
    ? ok(`after a pan and a zoom, an edge's start point still coincides with its port dot's real center (dx=${mc871PanZoomCheck.dx.toFixed(2)}, dy=${mc871PanZoomCheck.dy.toFixed(2)})`)
    : fail(`edge start drifted from its port dot's center after pan+zoom: ${JSON.stringify(mc871PanZoomCheck)}`);
  // Restore the exact pre-check viewport. Unlike the c2 zoom-escape block
  // above (which never touches an edge again before the NEXT edge gets
  // drawn fresh by a real drag), the very next check here (Change 7) reads
  // an existing edge <path>'s live position — a direct style.transform poke
  // snaps the CARDS back instantly but leaves the SVG path strings stale at
  // the zoomed-in coordinates they were last drawn at, so go through the
  // same real render path (_wfMarkDirty + the idempotent _wfSetTriggerType
  // trick used above) to force _wfRedrawEdges to recompute them too.
  await page.evaluate((vp) => {
    const st = window._wfEntry()._wf;
    st.viewport.x = vp.x; st.viewport.y = vp.y; st.viewport.scale = vp.scale;
    window._wfMarkDirty();
    window._wfSetTriggerType(st.def.trigger.type || 'manual');
  }, mc871ViewportBefore);
  await page.waitForTimeout(80);

  // ── MC-871 Change 7 — the delete × at an edge's midpoint (reusing the
  // c5From -> c5To edge the forgiving-drop test just made). ────────────────
  const c7GroupSel = `g.wfb-edge-group:has(path[onpointerdown*="_wfEdgeClick(event,'${c5From}','${c5To}'"])`;
  // page.hover() fails its own actionability check here: the moment the
  // mouse arrives, the × (correctly) becomes the topmost element AT that
  // exact point, and Playwright treats its own hover target being covered as
  // "intercepted" and keeps retrying forever — a real user's hover just
  // reveals the ×, nothing is actually blocked. page.mouse.move() to the
  // path's own midpoint sidesteps Playwright's locator-action interception
  // check while still exercising the same real :hover CSS state.
  const c7PathBox = await (await page.$(`${c7GroupSel} path.wfb-edge-path`)).boundingBox();
  await page.mouse.move(c7PathBox.x + c7PathBox.width / 2, c7PathBox.y + c7PathBox.height / 2);
  // :hover applies on the next frame after the move and the × then fades in over
  // 0.1s (app.css .wfb-edge-del), so a fixed 80ms read raced on a loaded machine
  // (opacity "0" with the pointer already on the edge). Poll for the reveal.
  await page.waitForFunction((sel) => parseFloat(getComputedStyle(document.querySelector(sel)).opacity) > 0,
    `${c7GroupSel} .wfb-edge-del`, { timeout: 3000 }).catch(() => {});
  const c7XOpacity = await page.$eval(`${c7GroupSel} .wfb-edge-del`, el => getComputedStyle(el).opacity);
  parseFloat(c7XOpacity) > 0 ? ok('hovering an edge reveals its delete ×')
                             : fail(`expected the × to be visible on hover, opacity was "${c7XOpacity}"`);
  const c7EdgesBefore = await page.$$eval('.wfb-edge-path', els => els.length);
  // Click the BG circle, not the (larger, invisible) hit circle underneath
  // it: both sit inside the same <g class="wfb-edge-del"> whose onpointerdown
  // handles either via bubbling, but .wfb-edge-del-bg paints on top at this
  // exact point and Playwright's own actionability check insists on hitting
  // whichever element is actually topmost there.
  await page.click(`${c7GroupSel} .wfb-edge-del-bg`);
  await page.waitForTimeout(100);
  const c7EdgesAfter = await page.$$eval('.wfb-edge-path', els => els.length);
  c7EdgesAfter === c7EdgesBefore - 1 ? ok('clicking the delete × removed that one edge')
                                     : fail(`expected edge count to drop by 1, got ${c7EdgesBefore} -> ${c7EdgesAfter}`);
  // Change 5's forgiving-drop test panned the viewport off to world (-900,
  // -900) to reach the c5-a/c5-b pair it created there, and nothing since
  // has panned back — the ORIGINAL cluster (triage/draft/the approval gate)
  // is still off-screen (clipped by .wfb-canvas-viewport's overflow:hidden),
  // so a click computed against its real on-screen coordinates would land
  // outside the visible canvas entirely. Reset the pan before the
  // keyboard-delete test below, which operates back on that original cluster.
  // Center on the "draft" node's own world position rather than a hardcoded
  // (60,40) constant -- that constant assumed a specific initial-placement
  // outcome, but "draft" and the auto-placed approval node it's connected to
  // both trace back to the drag-drop math earlier in this test, which is
  // itself relative to the canvas viewport's rect AT DROP TIME (still
  // affected by the describe panel's height on an empty canvas, MC-962/
  // follow-up) -- so any change to that panel's height can quietly move
  // where this cluster ends up, and a fixed pan can lose it off-canvas.
  await page.evaluate(() => {
    const st = window._wfEntry()._wf;
    const draft = st.def.nodes.find(n => n.name === 'draft');
    const vp = document.getElementById('wfb-canvas-viewport');
    const rect = vp.getBoundingClientRect();
    st.viewport.scale = 1;
    st.viewport.x = rect.width / 2 - (draft.x + 130) * st.viewport.scale;
    st.viewport.y = rect.height / 2 - (draft.y + 60) * st.viewport.scale;
    window._wfSetTriggerType(st.def.trigger.type || 'manual');
  });
  await page.waitForTimeout(80);
  // The keyboard path (select, then Delete/Backspace) must still work too —
  // the × is a SECOND way to reach _wfDeleteEdge, not a replacement.
  const c7KeyboardEdge = await page.evaluate(() => {
    const def = window._wfEntry()._wf.def;
    const e = def.edges.find(e => e.from === 'draft' && (def.nodes.find(n => n.name === e.to) || {}).type === 'approval');
    return e ? { from: e.from, to: e.to } : null;
  });
  if (c7KeyboardEdge) {
    // Change 6's Duplicate test (above) auto-focuses the new copy's prompt
    // textarea (_wfFocusPrompt) and never blurs it — the SAME guard that
    // makes Delete/Backspace not hijack text editing in a field would then
    // swallow this Delete keypress too, since it checks
    // document.activeElement's tagName. Explicitly blur first, same as a
    // user clicking away from the field would.
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    const c7KbSel = `g.wfb-edge-group path[onpointerdown*="_wfEdgeClick(event,'${c7KeyboardEdge.from}','${c7KeyboardEdge.to}'"]`;
    const c7KbCountBefore = await page.$$eval('.wfb-edge-path', els => els.length);
    await clickEdgeOffCenter(page, c7KbSel); // off-center — see the note above on the delete × sitting at the midpoint
    await page.keyboard.press('Delete');
    await page.waitForTimeout(100);
    const c7KbCountAfter = await page.$$eval('.wfb-edge-path', els => els.length);
    c7KbCountAfter === c7KbCountBefore - 1 ? ok('the keyboard path (select edge, press Delete) still removes an edge')
                                            : fail(`keyboard edge delete regressed: ${c7KbCountBefore} -> ${c7KbCountAfter}`);
  } else {
    fail('could not find the draft->approval edge to exercise the keyboard delete path');
  }

  // ── MC-871 Change 6 — the "..." menu must open (it silently didn't:
  // pointer capture on .wfb-node-head retargeted its click away — see
  // _wfNodeDragDown's guard) and offer Duplicate / Disconnect / Delete step. ─
  const preMenuNames = await page.evaluate(() => window._wfEntry()._wf.def.nodes.map(n => n.name));
  await dragPalettePersonTo(page, 'Nomask', () => emptyCanvasPoint(page));
  await page.waitForTimeout(100);
  const menuNodeName = await page.evaluate((before) => window._wfEntry()._wf.def.nodes.map(n => n.name).find(n => !before.includes(n)), preMenuNames);
  await page.click(`.wfb-node[data-name="${menuNodeName}"] .wfb-node-menu-btn`);
  await page.waitForSelector('#wfb-node-menu', { timeout: 3000 })
    .then(() => ok('clicking a card\'s "..." button opens its menu'))
    .catch(() => fail('the node menu did NOT open on click — the dead-button regression is back'));
  const menuItemLabels = await page.$$eval('#wfb-node-menu > div', els => els.map(e => e.textContent.trim()));
  (menuItemLabels.includes('Duplicate') && menuItemLabels.includes('Disconnect') && menuItemLabels.includes('Delete step'))
    ? ok(`the menu offers Duplicate / Disconnect / Delete step: ${JSON.stringify(menuItemLabels)}`)
    : fail(`expected Duplicate/Disconnect/Delete step, got ${JSON.stringify(menuItemLabels)}`);

  // Duplicate: a distinctly-named node, zero copied edges.
  const preDupNames = await page.evaluate(() => window._wfEntry()._wf.def.nodes.map(n => n.name));
  await clickMenuItem(page, 'Duplicate');
  await page.waitForTimeout(100);
  const dupResult = await page.evaluate((before) => {
    const def = window._wfEntry()._wf.def;
    const newName = def.nodes.map(n => n.name).find(n => !before.includes(n));
    return { newName, edgesTouching: newName ? def.edges.filter(e => e.from === newName || e.to === newName).length : -1 };
  }, preDupNames);
  (dupResult.newName && dupResult.newName !== menuNodeName && dupResult.edgesTouching === 0)
    ? ok(`Duplicate added "${dupResult.newName}" — a distinct name, 0 copied edges`)
    : fail(`Duplicate did not behave as expected: ${JSON.stringify(dupResult)}`);

  // Disconnect: drop every edge into/out of a node that already has one
  // (the drop-onto-card auto-wire from earlier), leaving the card in place.
  const homerName = await page.evaluate(() => (window._wfEntry()._wf.def.nodes.find(n => n.character === 'project:homed') || {}).name);
  const homerEdgesBefore = await page.evaluate((n) => window._wfEntry()._wf.def.edges.filter(e => e.from === n || e.to === n).length, homerName);
  homerEdgesBefore > 0 ? ok(`"${homerName}" already carries ${homerEdgesBefore} edge(s) — a real case for Disconnect`)
                        : fail('expected the drop-onto-card node to already carry an edge before testing Disconnect');
  await page.click(`.wfb-node[data-name="${homerName}"] .wfb-node-menu-btn`);
  await page.waitForSelector('#wfb-node-menu', { timeout: 3000 });
  await clickMenuItem(page, 'Disconnect');
  await page.waitForTimeout(100);
  const afterDisconnect = await page.evaluate((n) => {
    const def = window._wfEntry()._wf.def;
    return { stillThere: def.nodes.some(x => x.name === n), edges: def.edges.filter(e => e.from === n || e.to === n).length };
  }, homerName);
  (afterDisconnect.stillThere && afterDisconnect.edges === 0)
    ? ok(`Disconnect dropped all of "${homerName}"'s edges and left the card in place`)
    : fail(`Disconnect did not behave as expected: ${JSON.stringify(afterDisconnect)}`);

  // Delete step: removes the node (via the menu, not just the old dead
  // button) — use the duplicate from above so nothing else depends on it.
  const preDelCount = await page.evaluate(() => window._wfEntry()._wf.def.nodes.length);
  await page.click(`.wfb-node[data-name="${dupResult.newName}"] .wfb-node-menu-btn`);
  await page.waitForSelector('#wfb-node-menu', { timeout: 3000 });
  await page.click('#wfb-node-menu .wfb-node-menu-delete');
  await page.waitForTimeout(100);
  const afterMenuDelete = await page.evaluate((n) => {
    const def = window._wfEntry()._wf.def;
    return { gone: !def.nodes.some(x => x.name === n), count: def.nodes.length };
  }, dupResult.newName);
  (afterMenuDelete.gone && afterMenuDelete.count === preDelCount - 1)
    ? ok('Delete step (via the menu) removed the node')
    : fail(`Delete step via the menu did not remove the node: ${JSON.stringify(afterMenuDelete)}`);

  // ── Touch-context: the drag handles must not carry touch-action:none
  // PERMANENTLY (the mobile scroll-lock trap) — only while a drag is
  // actually active, via a dynamically-applied class. ─────────────────────
  const touchActionAtRest = await page.evaluate(() => {
    const head = document.querySelector('.wfb-node-head');
    const person = document.querySelector('.wfb-palette-person');
    return {
      head: getComputedStyle(head).touchAction,
      person: getComputedStyle(person).touchAction,
    };
  });
  (touchActionAtRest.head !== 'none' && touchActionAtRest.person !== 'none')
    ? ok(`at rest, drag handles stay scrollable (node-head: ${touchActionAtRest.head}, palette-person: ${touchActionAtRest.person}) — no permanent scroll lock`)
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
  // The toolbar tools are dedicated single controls (not rows in a
  // scrollable list) — same precedent as the port above, touch-action:none
  // UNCONDITIONALLY rather than the palette's dynamic-class trick. This is
  // what actually prevents defect 13's trap in the opposite direction (a
  // toolbar-to-canvas drag goes DOWN, the same axis `.wfb-modal-body`'s
  // vertical scroll would otherwise claim on mobile).
  const toolbarToolTouchAction = await page.evaluate(() => getComputedStyle(document.querySelector('.wfb-toolbar-tool')).touchAction);
  toolbarToolTouchAction === 'none' ? ok('a toolbar tool (Action/Approval gate/Wait) is touch-action:none unconditionally, like a port')
                                    : fail(`expected a toolbar tool to be touch-action:none always, got "${toolbarToolTouchAction}"`);

  // ── MC-871 Change 9 — "+ N more" reveals hidden bench people (previously
  // a single button whose text prefix lied: clicking it ALWAYS opened the
  // Claydo hire dialog, with no way to see the capped-off people at all).
  // Injects a 15-person bench directly (cap is now 12) rather than opening a
  // whole new browser context — _wfPaletteSearch('') already re-renders ONLY
  // the palette box from st.bench, which is exactly the code path
  // _wfPaletteToggleExpand also uses. This runs LAST in this context: it
  // replaces the bench wholesale, so nothing after it may assume the
  // original 5-person roster. ────────────────────────────────────────────
  await page.evaluate(() => {
    const st = window._wfEntry()._wf;
    st.bench = Array.from({ length: 15 }, (_, i) => ({
      name: 'filler' + i, scope: 'global', display: 'Filler ' + i, avatar: '',
      description: '', skills: [], provider: 'claude', model: '', effort: '',
      project_id: '', project_name: '', rooms: [],
    }));
    st.paletteExpanded = false;
    window._wfPaletteSearch('');
  });
  await page.waitForTimeout(80);
  const c9Shown = await page.$$eval('.wfb-palette-person-name', els => els.length);
  c9Shown === 12 ? ok(`palette caps at 12 people by default (raised from 8 — Change 9, more room now the canvas fills the tab)`)
                 : fail(`expected 12 people shown before expanding, got ${c9Shown}`);
  const c9MoreBtn = await page.$eval('.wfb-palette-more', el => el.textContent.trim()).catch(() => null);
  c9MoreBtn === '+ 3 more' ? ok(`"+ 3 more" is its OWN button, separate from Hire someone new`)
                           : fail(`expected a "+ 3 more" button, got ${JSON.stringify(c9MoreBtn)}`);
  await page.click('.wfb-palette-more');
  await page.waitForTimeout(80);
  const c9ShownAfter = await page.$$eval('.wfb-palette-person-name', els => els.length);
  const c9HireOpened = await page.evaluate(() => !!window.__hireDialogOpened); // sanity: nothing wired this, see below
  c9ShownAfter === 15 ? ok(`clicking "+ N more" revealed all 15 people (was clicking it opening the hire dialog instead — the whole bench was unreachable)`)
                       : fail(`expected all 15 people after expanding, got ${c9ShownAfter}`);
  // "+ N more" must not be the hire flow: guard floorHire itself, since
  // nothing else in this harness defines it.
  const c9HireGuard = await page.evaluate(() => {
    let called = false;
    window.floorHire = () => { called = true; };
    document.querySelector('.wfb-palette-more').click(); // now reads "Show fewer" post-expand
    return called;
  });
  c9HireGuard ? fail('"+ N more"/"Show fewer" incorrectly triggered the hire flow')
              : ok('"+ N more"/"Show fewer" never calls the hire flow');
  const c9HireBtn = await page.$$eval('button.wfb-palette-more', els => els.find(b => /Hire someone new/.test(b.textContent)));
  c9HireBtn ? ok('"Hire someone new" remains its own separate button') : fail('"Hire someone new" button is missing');
  const c9HireCalled = await page.evaluate(() => {
    let called = false;
    window.floorHire = () => { called = true; };
    [...document.querySelectorAll('button.wfb-palette-more')].find(b => /Hire someone new/.test(b.textContent)).click();
    return called;
  });
  c9HireCalled ? ok('"Hire someone new" still calls the hire flow') : fail('"Hire someone new" no longer calls the hire flow');
  await setValue(page, '.wfb-palette-search', 'filler1');
  await page.waitForTimeout(80);
  const c9SearchCount = await page.$$eval('.wfb-palette-person-name', els => els.length);
  // "filler1","filler10".."filler14" all match the substring "filler1"
  c9SearchCount === 6 ? ok(`a search still shows every match regardless of expanded state (${c9SearchCount} matches for "filler1")`)
                       : fail(`expected 6 matches for "filler1", got ${c9SearchCount}`);

  // ── C10: the engine hover tooltip (Ron, 2026-09-11) — pinned vs inherited ──
  // Two personas added straight to st.bench (same idiom as the c9 replace
  // above; nothing after this assumes the prior bench shape either): one
  // pins its own engine, one pins nothing and must fall through to the
  // PROJECT's agent_model/agent_effort (fixtureProject above) — the
  // inherited path the brief calls out as the one that silently rots.
  const c10Names = await page.evaluate(({ pid }) => {
    const st = window._wfEntry()._wf;
    st.bench = st.bench.concat([
      { name: 'pinned-agent', scope: 'global', display: 'Pinny', avatar: '',
        description: '', skills: [], provider: 'claude', model: 'claude-opus-5', effort: 'high',
        project_id: '', project_name: '', rooms: [] },
      { name: 'plain-agent', scope: 'global', display: 'Plain', avatar: '',
        description: '', skills: [], provider: 'claude', model: '', effort: '',
        project_id: '', project_name: '', rooms: [] },
    ]);
    const pinned = { type: 'agent', name: 'c10-pinned', x: -1300, y: -900,
      project_id: pid, character: 'global:pinned-agent', prompt: '', outcomes: [] };
    const inherited = { type: 'agent', name: 'c10-inherited', x: -960, y: -900,
      project_id: pid, character: 'global:plain-agent', prompt: '', outcomes: [] };
    st.def.nodes = (st.def.nodes || []).concat([pinned, inherited]);
    const vp = document.getElementById('wfb-canvas-viewport');
    const rect = vp.getBoundingClientRect();
    st.viewport.x = rect.width / 2 - (pinned.x + 260) * st.viewport.scale;
    st.viewport.y = rect.height / 2 - (pinned.y + 60) * st.viewport.scale;
    window._wfMarkDirty();
    window._wfSetTriggerType(st.def.trigger.type || 'manual');
    return ['c10-pinned', 'c10-inherited'];
  }, { pid: PID });
  await page.waitForTimeout(100);
  c10Names.length === 2 ? ok('placed a pinned-engine node and an unpinned (inherited) one')
                        : fail(`expected 2 fresh nodes, got ${JSON.stringify(c10Names)}`);
  const c10PinnedTitle = await page.$eval('.wfb-node[data-name="c10-pinned"] .wfb-node-avatar', el => el.title);
  (/Opus 5/.test(c10PinnedTitle) && /effort high/.test(c10PinnedTitle) && /pinned on Pinny/.test(c10PinnedTitle))
    ? ok(`a persona's own engine pin shows verbatim: "${c10PinnedTitle}"`)
    : fail(`expected the pinned engine (Opus 5 / effort high / pinned on Pinny), got "${c10PinnedTitle}"`);
  const c10InheritedTitle = await page.$eval('.wfb-node[data-name="c10-inherited"] .wfb-node-avatar', el => el.title);
  (/Sonnet 5/.test(c10InheritedTitle) && /effort medium/.test(c10InheritedTitle) && /inherited, project default/.test(c10InheritedTitle))
    ? ok(`an unpinned persona's tooltip is honest that it's INHERITED from the project default, not its own: "${c10InheritedTitle}"`)
    : fail(`expected an inherited-from-project-default tooltip (Sonnet 5 / effort medium), got "${c10InheritedTitle}"`);

  // ── C11: the PALETTE popup — model before dragging + bounded width (Ron,
  // 2026-09-11 follow-up). This is the OTHER hover: the Bench popup shown
  // BEFORE a person is dropped, not the on-canvas node tooltip C10 covers.
  // Reuses the exact bench rows C10 added (Pinny pinned, Plain inherits the
  // project default) so a real hover exercises the shared resolver, plus a
  // fresh long-description row for the width bound and a temporarily
  // project-less hover for the "can't know yet" case.
  await page.evaluate(() => {
    const st = window._wfEntry()._wf;
    st.bench = st.bench.concat([{
      name: 'wide-desc-agent', scope: 'global', display: 'Wordy', avatar: '',
      description: 'This description is deliberately long enough that, rendered at full width with no bound at all, it would run edge-to-edge across a 1280px-wide desktop window — exactly the "extends all across the screen" defect Ron reported, so a fixed max-width has to force it to wrap onto several lines instead.',
      skills: [], provider: 'claude', model: '', effort: '',
      project_id: '', project_name: '', rooms: [],
    }]);
    // C9 (above) left the bench at 15 filler rows plus a "filler1" search;
    // Pinny/Plain/Wordy sort after all of those alphabetically and the
    // palette caps at 12, so without expanding, none of the three rows this
    // test hovers would even be in the DOM.
    st.paletteExpanded = true;
    window._wfPaletteSearch(''); // re-render the palette so the new rows mount
  });
  await page.waitForTimeout(50);

  const pinnedRow = await page.evaluateHandle(() => [...document.querySelectorAll('.wfb-palette-person')]
    .find(r => (r.querySelector('.wfb-palette-person-name') || {}).textContent === 'Pinny'));
  await pinnedRow.asElement().hover();
  await page.waitForTimeout(50);
  const c11Pinned = await page.evaluate(() => {
    const pop = document.getElementById('wfb-palette-popover');
    return { hidden: pop.classList.contains('hidden'), text: pop.textContent };
  });
  (!c11Pinned.hidden && /Model: Opus 5/.test(c11Pinned.text) && /pinned on Pinny/.test(c11Pinned.text))
    ? ok(`palette popup shows the pinned engine before drag: "${c11Pinned.text.trim()}"`)
    : fail(`expected the palette popup to show Pinny's pinned engine, got ${JSON.stringify(c11Pinned)}`);

  // Regression for the reported bug: the popover landed in the middle of the
  // canvas instead of next to the hovered row. Root cause was the popover's
  // markup living INSIDE the builder's `.modal-window` host, which carries a
  // permanent `filter: drop-shadow(...)` — a `filter` on an ancestor becomes
  // the containing block for a `position:fixed` descendant, so viewport-space
  // left/top land relative to the modal's own box instead of the viewport.
  // This runs in the NORMAL (non-maximized) mount, where that filter is
  // present — a maximized-only check would pass against today's bug, since
  // `.modal-window.is-maximized { filter: none; }` removes the very ancestor
  // that causes the drift. Confirms both effects: the node is a direct child
  // of <body> (escapes the modal subtree entirely), and its rendered position
  // actually lands next to the hovered row rather than merely having the
  // right left/top VALUES computed against the wrong containing block.
  const c11Pos = await page.evaluate(() => {
    const modalMaximized = !!document.querySelector('.modal-window.is-maximized');
    const row = [...document.querySelectorAll('.wfb-palette-person')]
      .find(r => (r.querySelector('.wfb-palette-person-name') || {}).textContent === 'Pinny');
    const pop = document.getElementById('wfb-palette-popover');
    const rowRect = row.getBoundingClientRect();
    const popRect = pop.getBoundingClientRect();
    return {
      modalMaximized, mountedOnBody: pop.parentElement === document.body,
      rowRight: rowRect.right, rowTop: rowRect.top, popLeft: popRect.left, popTop: popRect.top,
    };
  });
  (!c11Pos.modalMaximized && c11Pos.mountedOnBody)
    ? ok('the palette popover node is a direct child of <body> (portaled out of the filtered .modal-window), checked in the NORMAL (non-maximized) mount')
    : fail(`expected the popover on <body> in the non-maximized mount, got ${JSON.stringify(c11Pos)}`);
  const c11DeltaX = Math.abs(c11Pos.popLeft - (c11Pos.rowRight + 8));
  const c11DeltaY = Math.abs(c11Pos.popTop - c11Pos.rowTop);
  (c11DeltaX < 20 && c11DeltaY < 40)
    ? ok(`the popover renders adjacent to the hovered row (dx=${c11DeltaX.toFixed(1)}px, dy=${c11DeltaY.toFixed(1)}px from the row's real position), not offset by an ancestor's filter`)
    : fail(`popover drifted from the hovered row: row right/top=(${c11Pos.rowRight.toFixed(1)},${c11Pos.rowTop.toFixed(1)}) popover left/top=(${c11Pos.popLeft.toFixed(1)},${c11Pos.popTop.toFixed(1)}) dx=${c11DeltaX.toFixed(1)} dy=${c11DeltaY.toFixed(1)}`);

  const plainRow = await page.evaluateHandle(() => [...document.querySelectorAll('.wfb-palette-person')]
    .find(r => (r.querySelector('.wfb-palette-person-name') || {}).textContent === 'Plain'));
  await plainRow.asElement().hover();
  await page.waitForTimeout(50);
  const c11Plain = await page.evaluate(() => document.getElementById('wfb-palette-popover').textContent);
  /Model: Sonnet 5/.test(c11Plain) && /inherited, project default/.test(c11Plain)
    ? ok(`palette popup shows the inherited project-default engine for an unpinned persona: "${c11Plain.trim()}"`)
    : fail(`expected an inherited-project-default engine line, got "${c11Plain}"`);

  const wordyRow = await page.evaluateHandle(() => [...document.querySelectorAll('.wfb-palette-person')]
    .find(r => (r.querySelector('.wfb-palette-person-name') || {}).textContent === 'Wordy'));
  await wordyRow.asElement().hover();
  await page.waitForTimeout(50);
  const c11Wide = await page.evaluate(() => {
    const pop = document.getElementById('wfb-palette-popover');
    const rect = pop.getBoundingClientRect();
    return { width: rect.width, lineHeight: parseFloat(getComputedStyle(pop).lineHeight) || 0,
             descHeight: pop.querySelector('.wfb-palette-popover-desc').getBoundingClientRect().height };
  });
  (c11Wide.width > 0 && c11Wide.width <= 361)
    ? ok(`long-description palette popup is bounded to ${c11Wide.width}px (<= 360px), not full window width`)
    : fail(`expected the popup width to be bounded to <= 360px, got ${c11Wide.width}px`);
  (c11Wide.descHeight > c11Wide.lineHeight * 1.5)
    ? ok(`the long description wraps onto multiple lines (desc height ${c11Wide.descHeight}px vs one line-height ${c11Wide.lineHeight}px)`)
    : fail(`expected the long description to wrap onto multiple lines, got desc height ${c11Wide.descHeight}px vs line-height ${c11Wide.lineHeight}px`);

  // The one honest gap the brief calls out: a person with no home project,
  // hovered before the builder has any project context at all — must say it
  // doesn't know, never guess a project or global default that may not be
  // the one actually used once dropped.
  const c11NoProject = await page.evaluate(() => {
    const st = window._wfEntry()._wf;
    const savedHint = st.hintProjectId;
    st.hintProjectId = '';
    const row = [...document.querySelectorAll('.wfb-palette-person')]
      .find(r => (r.querySelector('.wfb-palette-person-name') || {}).textContent === 'Plain');
    window._wfPalettePersonHover({ currentTarget: row }, 'global', 'plain-agent');
    const text = document.getElementById('wfb-palette-popover').textContent;
    st.hintProjectId = savedHint;
    return text;
  });
  (/no project yet/.test(c11NoProject) && !/inherited/.test(c11NoProject))
    ? ok(`with no project context at all, the palette popup admits it doesn't know rather than guessing: "${c11NoProject.trim()}"`)
    : fail(`expected an honest "no project yet" line with no guessed default, got "${c11NoProject}"`);

  // ── C13: tell the user when the ENGINE won't work (Ron, MC-871 follow-up)
  // — all three engine problems are non-blocking WARNINGS on the card, never
  // a Save-blocking error: an empty model chain runs on the CLI default
  // (agent_runtime.py only appends --model `if model:`), and a catalog miss
  // is unproven (model_supported: "the user may legitimately type a model id
  // newer than our catalog"). `unknown` auth must never warn at all, and a
  // retired-but-still-valid legacy id must never be called dead. ──────────
  await page.evaluate(({ pid }) => {
    // Case 1: a project with no agent_model/agent_effort of its own --
    // "smoke_other" is otherwise untouched by every prior section, so
    // mutating it here can't disturb an already-asserted case.
    const proj = allProjects.find((p) => p.id === 'smoke_other');
    proj.agent_model = ''; proj.agent_effort = '';
    const st = window._wfEntry()._wf;
    // Case 2: a persona pinning a model id its provider catalog no longer
    // lists, and (case 3) one pinning a RETIRED-but-valid legacy id that
    // must stay silent. Seed a fake catalog directly -- /api/agent/providers
    // is aborted in this harness, so _agentProviders is otherwise null and
    // the "unknown, can't judge" branch would (correctly) stay silent.
    _agentProviders = [{ name: 'claude', display_name: 'Claude', installed: true,
      models: [{ id: 'claude-sonnet-5', label: 'Sonnet 5' }] }];
    st.bench = st.bench.concat([{
      name: 'retired-model-agent', scope: 'global', display: 'Retiro', avatar: '',
      description: '', skills: [], provider: 'claude', model: 'claude-retired-1', effort: '',
      project_id: '', project_name: '', rooms: [],
    }, {
      name: 'legacy-model-agent', scope: 'global', display: 'Legacio', avatar: '',
      description: '', skills: [], provider: 'claude', model: 'claude-opus-4-8', effort: '',
      project_id: '', project_name: '', rooms: [],
    }]);
    const noEngine = { type: 'agent', name: 'c13-noengine', x: -1300, y: -700,
      project_id: 'smoke_other', character: 'global:plain-agent', prompt: 'test', outcomes: [] };
    const deadModel = { type: 'agent', name: 'c13-deadmodel', x: -960, y: -700,
      project_id: pid, character: 'global:retired-model-agent', prompt: 'test', outcomes: [] };
    const legacyModel = { type: 'agent', name: 'c13-legacymodel', x: -620, y: -700,
      project_id: pid, character: 'global:legacy-model-agent', prompt: 'test', outcomes: [] };
    st.def.nodes = (st.def.nodes || []).concat([noEngine, deadModel, legacyModel]);
  }, { pid: PID });
  // _wfSave() calls _wfSyncDomToModel() FIRST, which reads def.name back
  // from the real #wfb-name input -- setting st.def.name directly on the
  // model would just get overwritten by that sync (silently: the save then
  // exits at its own "Name the workflow" gate, never reaching the validator
  // this section means to exercise). Type it into the DOM instead.
  await setValue(page, '#wfb-name', 'Smoke C13');
  const c13Save = await page.evaluate(() => {
    const st = window._wfEntry()._wf;
    window._wfSave();
    return { runErrors: Object.assign({}, st.runErrors) };
  });
  await page.waitForTimeout(120);
  // The load-bearing assertion of this whole section: neither engine case is
  // allowed to block Save, because neither proves the step cannot run.
  (!c13Save.runErrors['c13-noengine'] && !c13Save.runErrors['c13-deadmodel'])
    ? ok('an empty model chain and an off-catalog pin both SAVE — neither is refused as a hard error')
    : fail(`engine problems must not block Save, got ${JSON.stringify(c13Save.runErrors)}`);
  // MC-963: `.wfb-node-warning`/`.wfb-node-error` stay on the canvas box
  // (nodeStateCls), but the full inline warning TEXT moved into the
  // inspector body -- open each node in turn to read it.
  async function readEngineWarning(name) {
    const info = await page.evaluate((n) => {
      const el = document.querySelector(`.wfb-node[data-name="${n}"]`);
      if (!el) return null;
      return { warn: el.classList.contains('wfb-node-warning'), err: el.classList.contains('wfb-node-error') };
    }, name);
    if (!info) return null;
    await openInspector(page, name);
    const text = await page.evaluate(() => {
      const w = document.querySelector('#wfb-inspector .wfb-node-inline-warning');
      return w ? w.textContent : '';
    });
    await closeInspector(page);
    return { ...info, text };
  }
  const c13Warnings = {
    noengine: await readEngineWarning('c13-noengine'),
    dead: await readEngineWarning('c13-deadmodel'),
    legacy: await readEngineWarning('c13-legacymodel'),
  };
  (c13Warnings.noengine && c13Warnings.noengine.warn && !c13Warnings.noengine.err
    && /pins no model.*"Some Other Project" sets no default/.test(c13Warnings.noengine.text))
    ? ok(`empty chain warns honestly about CLI drift, in amber: "${c13Warnings.noengine.text}"`)
    : fail(`expected an amber CLI-default warning, got ${JSON.stringify(c13Warnings.noengine)}`);
  (c13Warnings.dead && c13Warnings.dead.warn && !c13Warnings.dead.err
    && /"claude-retired-1" is pinned on Retiro but claude no longer lists it/.test(c13Warnings.dead.text))
    ? ok(`off-catalog pin warns without asserting it is dead: "${c13Warnings.dead.text}"`)
    : fail(`expected an amber off-catalog warning, got ${JSON.stringify(c13Warnings.dead)}`);
  (c13Warnings.legacy && !c13Warnings.legacy.warn && !c13Warnings.legacy.text)
    ? ok('a retired-but-valid legacy id (claude-opus-4-8, MC_LEGACY_MODEL_LABELS) is never called dead')
    : fail(`legacy pinned id must not warn, got ${JSON.stringify(c13Warnings.legacy)}`);

  // Provider auth: a definite-negative status warns (non-blocking); `unknown`
  // never does. Two fake providers so each starts with a clean cache entry.
  // The response map MUST be set before the first render that touches these
  // providers -- _wfEnsureProviderAuthFresh caches per-provider for 60s, so
  // a render that fires while the map still reads "unknown" (the fallback)
  // would cache that miss and never look again in time for this test.
  providerAuthResponses.authbad = 'not_logged_in';
  providerAuthResponses.authunknown = 'unknown';
  const c13AuthNames = await page.evaluate(({ pid }) => {
    const st = window._wfEntry()._wf;
    st.bench = st.bench.concat([
      { name: 'authbad-agent', scope: 'global', display: 'Badauth', avatar: '',
        description: '', skills: [], provider: 'authbad', model: 'authbad-1', effort: '',
        project_id: '', project_name: '', rooms: [] },
      { name: 'authunknown-agent', scope: 'global', display: 'Unkauth', avatar: '',
        description: '', skills: [], provider: 'authunknown', model: 'authunknown-1', effort: '',
        project_id: '', project_name: '', rooms: [] },
    ]);
    const bad = { type: 'agent', name: 'c13-authbad', x: -1300, y: -500,
      project_id: pid, character: 'global:authbad-agent', prompt: 'test', outcomes: [] };
    const unk = { type: 'agent', name: 'c13-authunknown', x: -960, y: -500,
      project_id: pid, character: 'global:authunknown-agent', prompt: 'test', outcomes: [] };
    st.def.nodes = (st.def.nodes || []).concat([bad, unk]);
    window._wfMarkDirty();
    window._wfSetTriggerType(st.def.trigger.type || 'manual');
    return ['c13-authbad', 'c13-authunknown'];
  }, { pid: PID });
  c13AuthNames.length === 2 ? ok('placed a not-logged-in-provider node and an unknown-status one')
                            : fail(`expected 2 fresh nodes, got ${JSON.stringify(c13AuthNames)}`);
  await page.waitForSelector('.wfb-node[data-name="c13-authbad"].wfb-node-warning', { timeout: 3000 })
    .then(() => ok('the not-logged-in provider gets the live warning outline (wfb-node-warning) — not the hard-error red'),
          () => fail('expected c13-authbad to pick up wfb-node-warning after the auth probe resolved'));
  await openInspector(page, 'c13-authbad');
  const c13AuthBadText = await page.$eval('#wfb-inspector .wfb-node-inline-warning', (el) => el.textContent).catch(() => null);
  await closeInspector(page);
  (c13AuthBadText && /will fail when the workflow runs/.test(c13AuthBadText) && /authbad/.test(c13AuthBadText))
    ? ok(`warning names the consequence and the provider, not the mechanism: "${c13AuthBadText}"`)
    : fail(`expected a consequence-framed warning naming "authbad", got ${JSON.stringify(c13AuthBadText)}`);
  await page.waitForTimeout(400); // let authunknown's probe settle too -- it must NOT warn
  await openInspector(page, 'c13-authunknown');
  const c13UnknownState = await page.evaluate(() => {
    const el = document.querySelector('.wfb-node[data-name="c13-authunknown"]');
    return { warningClass: el.classList.contains('wfb-node-warning'), inlineWarning: !!document.querySelector('#wfb-inspector .wfb-node-inline-warning') };
  });
  await closeInspector(page);
  (!c13UnknownState.warningClass && !c13UnknownState.inlineWarning)
    ? ok('an `unknown` auth status never trips the warning — "unknown is not unauthenticated"')
    : fail(`expected no warning for an unknown auth status, got ${JSON.stringify(c13UnknownState)}`);

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
    if (path === '/api/local-auth/status') return route.fulfill({ status: 200, contentType: 'application/json', body: '{"configured":true}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/floor') return route.fulfill({ status: 200, contentType: 'application/json', body: FLOOR_JSON });
    if (path.startsWith('/api/avatars/')) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1PX });
    if (path === '/api/workflows' && req.method() === 'GET') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/workflows`) return route.fulfill({ status: 200, contentType: 'application/json', body: '{"workflows":[]}' });
    // Unmocked, these abort forever and agent-log.js's rail poller retries
    // in an unbounded tight loop -- enough iterations across a section's
    // many DOM waits crashed the renderer outright (page5, deterministic).
    if (path === `/api/project/${PID}/agent/log`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/conversations`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    return route.abort();
  });
  await mpage.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await mpage.waitForSelector('#projects-col .card, .mc-chat-row', { timeout: 15000 });
  await newWorkflow(mpage, PID);
  const paletteFlow = await mpage.evaluate(() => {
    const builder = document.querySelector('.wfb-builder');
    const palette = document.querySelector('.wfb-palette');
    return { builderDir: getComputedStyle(builder).flexDirection, paletteDir: getComputedStyle(palette).flexDirection };
  });
  (paletteFlow.builderDir === 'column-reverse' && paletteFlow.paletteDir === 'row')
    ? ok(`at a phone width, the palette lays out as a bottom sheet (builder: ${paletteFlow.builderDir}, palette row: ${paletteFlow.paletteDir})`)
    : fail(`expected the palette to become a horizontal bottom sheet at 390px, got ${JSON.stringify(paletteFlow)}`);

  // MC-962 regression, mobile width: same overlap check as the desktop case
  // (ctx10 below) but at 390px, where the empty-canvas describe box and the
  // default-positioned trigger card are even more likely to collide since
  // both hug the same top-left corner. Must run before the touch-drag below
  // places a node -- that removes the describe box (nodes.length > 0).
  const mOverlap = await mpage.evaluate(() => {
    const rectOf = (sel) => { const el = document.querySelector(sel); return el ? el.getBoundingClientRect() : null; };
    const d = rectOf('.wfb-describe-box'), t = rectOf('.wfb-trigger-box');
    if (!d || !t) return { d, t, intersects: null };
    const intersects = d.left < t.right && d.right > t.left && d.top < t.bottom && d.bottom > t.top;
    return { d: { left: d.left, top: d.top, right: d.right, bottom: d.bottom },
             t: { left: t.left, top: t.top, right: t.right, bottom: t.bottom }, intersects };
  });
  mOverlap.intersects === false
    ? ok(`the describe box does not overlap the trigger card at 390px (describe=${JSON.stringify(mOverlap.d)}, trigger=${JSON.stringify(mOverlap.t)})`)
    : fail(`the describe box overlaps the trigger card at 390px: ${JSON.stringify(mOverlap)}`);
  const mDescribeBtnClass = await mpage.$eval('.wfb-describe-row button', (el) => el.className);
  /\bbtn-add\b/.test(mDescribeBtnClass)
    ? ok(`the "Describe it" button carries the app's real button class at 390px too (${mDescribeBtnClass})`)
    : fail(`expected the "Describe it" button to carry btn-add at 390px, got class="${mDescribeBtnClass}"`);

  // MC-962 follow-up, mobile width: same one-row + height-ceiling checks as
  // the desktop case above -- the input shrinks to fit but the button must
  // stay on the same row and visible (not wrapped below or clipped).
  const mRow = await mpage.evaluate(() => {
    const input = document.getElementById('wfb-describe-input');
    const btn = document.querySelector('.wfb-describe-row .btn-add');
    if (!input || !btn) return null;
    const ir = input.getBoundingClientRect(), br = btn.getBoundingClientRect();
    return { inputTop: ir.top, btnTop: br.top, btnVisible: br.width > 0 && br.height > 0 };
  });
  (mRow && Math.abs(mRow.inputTop - mRow.btnTop) <= 4 && mRow.btnVisible)
    ? ok(`the describe input and "Describe it" button share one row and the button stays visible at 390px (${JSON.stringify(mRow)})`)
    : fail(`expected the describe input and visible button on the same row at 390px, got ${JSON.stringify(mRow)}`);
  const mPanelHeight = (await mpage.$eval('.wfb-canvas-empty', (el) => el.getBoundingClientRect().height));
  mPanelHeight < 100
    ? ok(`the describe panel (box + hint) fits under the one-row ceiling at 390px (${mPanelHeight}px)`)
    : fail(`expected the describe panel under 100px at 390px, got ${mPanelHeight}px`);

  // Defect 13 (Ron, phone: "unable to drag agent onto the canvas") -- a REAL
  // touch gesture via CDP Input.dispatchTouchEvent, not a mouse-emulated
  // drag: touchstart, hold past the 400ms long-press with no movement, THEN
  // move up toward the canvas (the direction a bottom-sheet drag-out always
  // is) and release. This is deliberately NOT the same check as "touch-
  // action stays scrollable at rest" above -- that only reads a computed CSS
  // property, and the mobile-layout check above only reads flex-direction;
  // neither ever performed a gesture, which is exactly why both passed while
  // the real drag was broken. touch-action is fixed for a touch's whole
  // gesture at first contact, so switching to touch-action:none 400ms into
  // an ALREADY-STARTED touch (via .wfb-palette-dragging) cannot retroactively
  // stop the browser from having already claimed a vertical move as a native
  // pan under the desktop-inherited `pan-y` -- the fix is a mobile-only
  // `touch-action: pan-x` matching this breakpoint's OWN scroll axis
  // (`.wfb-palette`'s overflow-x), freeing the vertical axis an up-drag
  // needs. This asserts the actual placement, not just the gesture's shape.
  const mCdp = await mctx.newCDPSession(mpage);
  const mPersonBox = await (await mpage.$('.wfb-palette-person')).boundingBox();
  const mCanvasBox = await (await mpage.$('#wfb-canvas-viewport')).boundingBox();
  const mStartX = mPersonBox.x + mPersonBox.width / 2, mStartY = mPersonBox.y + mPersonBox.height / 2;
  const mDropX = mCanvasBox.x + mCanvasBox.width / 2, mDropY = mCanvasBox.y + mCanvasBox.height / 2;
  const mNodesBefore = await mpage.evaluate(() => (window._wfEntry()._wf.def.nodes || []).length);
  await mCdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: mStartX, y: mStartY, id: 1 }] });
  await mpage.waitForTimeout(450); // past WFB_LONG_PRESS_MS with the finger held still
  for (const pt of [{ x: mStartX + 5, y: mStartY - 40 }, { x: (mStartX + mDropX) / 2, y: (mStartY + mDropY) / 2 }, { x: mDropX, y: mDropY }]) {
    await mCdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: pt.x, y: pt.y, id: 1 }] });
    await mpage.waitForTimeout(50);
  }
  await mCdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await mpage.waitForTimeout(200);
  const mNodesAfter = await mpage.evaluate(() => (window._wfEntry()._wf.def.nodes || []).length);
  (mNodesAfter === mNodesBefore + 1)
    ? ok('a real touch long-press-drag from the mobile palette placed a node on the canvas')
    : fail(`a real touch drag did not place a node on mobile -- nodes stayed at ${mNodesAfter} (expected ${mNodesBefore + 1}); the browser likely swallowed the up-drag as a native scroll`);

  // MC-871 follow-up (Ron, phone): Action/Approval gate/Wait moved out of the
  // palette into the toolbar, which sits ABOVE the canvas at every width
  // including mobile ("same row as create and run now") -- so a
  // toolbar-to-canvas drag goes DOWN, the same axis conflict as defect 13
  // above, just the opposite direction (that drag went UP from a bottom
  // sheet). `.wfb-toolbar-tool` is touch-action:none UNCONDITIONALLY (this
  // file's own CSS, matching `.wfb-port`'s existing precedent for a
  // dedicated single control, not a dynamic class the way the palette does
  // it) specifically so the browser never gets the chance to claim the move
  // as `.wfb-modal-body`'s native vertical scroll -- proved here with a real
  // touch gesture, not a mouse-emulated one.
  // MC-963: dropping a person auto-opens its inspector (_wfFocusPrompt, the
  // freshly-dropped-node caret-focus carried forward from the old inline
  // card -- "the prompt is the one place the author actually types") -- on
  // this mobile width that's a bottom sheet covering the canvas center
  // (`.wfb-inspector-open` at <=960px, app.css), which would otherwise eat
  // the drop coordinates every gesture below computes off `mCanvasBox2`.
  // A real user closes it before placing the next block; do the same.
  await closeInspector(mpage);
  const mToolBox = await toolbarToolBox(mpage, 'Action');
  const mCanvasBox2 = await (await mpage.$('#wfb-canvas-viewport')).boundingBox();
  const mToolStartX = mToolBox.x + mToolBox.width / 2, mToolStartY = mToolBox.y + mToolBox.height / 2;
  const mToolDropX = mCanvasBox2.x + mCanvasBox2.width / 2, mToolDropY = mCanvasBox2.y + mCanvasBox2.height / 2;
  const mToolNodesBefore = await mpage.evaluate(() => (window._wfEntry()._wf.def.nodes || []).length);
  await mCdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: mToolStartX, y: mToolStartY, id: 1 }] });
  await mpage.waitForTimeout(450); // past WFB_LONG_PRESS_MS with the finger held still
  for (const pt of [{ x: mToolStartX, y: mToolStartY + 40 }, { x: (mToolStartX + mToolDropX) / 2, y: (mToolStartY + mToolDropY) / 2 }, { x: mToolDropX, y: mToolDropY }]) {
    await mCdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: pt.x, y: pt.y, id: 1 }] });
    await mpage.waitForTimeout(50);
  }
  await mCdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await mpage.waitForTimeout(200);
  const mToolNodesAfter = await mpage.evaluate(() => (window._wfEntry()._wf.def.nodes || []).length);
  (mToolNodesAfter === mToolNodesBefore + 1)
    ? ok('a real touch long-press-drag from a toolbar tool (downward, toolbar-above-canvas axis) placed a node on the mobile canvas')
    : fail(`a real touch drag from the toolbar did not place a node on mobile -- nodes stayed at ${mToolNodesAfter} (expected ${mToolNodesBefore + 1}); the browser likely swallowed the down-drag as a native scroll`);

  // A plain tap (touchstart+touchend, NO movement at all) must also add the
  // block -- real touch, not page.click, since this exercises the same
  // pointer-event state machine the drag above does, not a synthetic one.
  const mTapBox = await toolbarToolBox(mpage, 'Approval gate');
  const mTapX = mTapBox.x + mTapBox.width / 2, mTapY = mTapBox.y + mTapBox.height / 2;
  const mTapNodesBefore = mToolNodesAfter;
  await mCdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: mTapX, y: mTapY, id: 1 }] });
  await mCdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await mpage.waitForTimeout(150);
  const mTapNodesAfter = await mpage.evaluate(() => (window._wfEntry()._wf.def.nodes || []).length);
  const mTapOverlap = await mpage.evaluate(() => {
    const world = document.getElementById('wfb-world');
    const rects = [...world.querySelectorAll('.wfb-node, .wfb-trigger-box')].map(el => ({
      x: el.offsetLeft, y: el.offsetTop, w: el.offsetWidth, h: el.offsetHeight,
    }));
    for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) {
      const a = rects[i], b = rects[j];
      if (a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y) return true;
    }
    return false;
  });
  (mTapNodesAfter === mTapNodesBefore + 1 && !mTapOverlap)
    ? ok('a plain tap on a toolbar tool (no drag at all) added a block on mobile, placed clear of every existing card')
    : fail(`tap-to-add on mobile: nodes ${mTapNodesBefore} -> ${mTapNodesAfter} (expected +1), overlap=${mTapOverlap}`);

  // MC-871 agent-card mobile pass, defect 2, AT A MOBILE WIDTH specifically
  // (Ron's own report was from a phone) -- the same clip-integrity check as
  // the desktop case above, so a regression that only shows up under the
  // mobile breakpoint's layout (`.wfb-canvas-viewport { flex:none; height:
  // 58vh }`, app.css ~9142) doesn't slip through a desktop-only assertion.
  const mViewportBefore = await mpage.evaluate(() => ({ ...window._wfEntry()._wf.viewport }));
  await mpage.mouse.move(mCanvasBox.x + mCanvasBox.width / 2, mCanvasBox.y + mCanvasBox.height / 2);
  for (let i = 0; i < 8; i++) await mpage.mouse.wheel(0, -400); // drive well past the 2.5x clamp
  await mpage.waitForTimeout(150);
  const mScaleClamped = await mpage.evaluate(() => window._wfEntry()._wf.viewport.scale);
  mScaleClamped <= 2.5
    ? ok(`mobile: repeated zoom-in stays clamped at the code's own ceiling (scale=${mScaleClamped.toFixed(2)}, cap 2.5)`)
    : fail(`mobile: zoom exceeded its documented 2.5x cap: scale=${mScaleClamped.toFixed(2)}`);
  const mVpBoxZoomed = await (await mpage.$('#wfb-canvas-viewport')).boundingBox();
  const mEscape = await mpage.evaluate((vp) => {
    const probe = (x, y) => {
      const el = document.elementFromPoint(x, y);
      return !!(el && el.closest && el.closest('.wfb-canvas-world'));
    };
    const midX = vp.x + vp.width / 2, midY = vp.y + vp.height / 2;
    return {
      left: probe(vp.x - 4, midY), right: probe(vp.x + vp.width + 4, midY),
      top: probe(midX, vp.y - 4), bottom: probe(midX, vp.y + vp.height + 4),
    };
  }, mVpBoxZoomed);
  (!mEscape.left && !mEscape.right && !mEscape.top && !mEscape.bottom)
    ? ok(`mobile width: at ${mScaleClamped.toFixed(2)}x zoom, nothing from the canvas world paints outside #wfb-canvas-viewport's own box on any of the 4 edges`)
    : fail(`mobile width: canvas content escaped its viewport's clip at ${mScaleClamped.toFixed(2)}x zoom: ${JSON.stringify(mEscape)}`);
  // Direct restore of the exact pre-check viewport (see the desktop case's
  // comment on why not more wheel events) -- Defect 14's drag right below
  // assumes scale 1 geometry.
  await mpage.evaluate((vp) => {
    const st = window._wfEntry()._wf;
    st.viewport.x = vp.x; st.viewport.y = vp.y; st.viewport.scale = vp.scale;
    const world = document.getElementById('wfb-world');
    if (world) world.style.transform = `translate(${vp.x}px, ${vp.y}px) scale(${vp.scale})`;
  }, mViewportBefore);
  await mpage.waitForTimeout(80);

  // Defect 14 -- the same treatment for the node/trigger drag path
  // (_wfNodeDragDown, which the trigger tile's own drag reuses verbatim).
  // The reasoning behind defect 13's fix does NOT automatically transfer: a
  // node drag starts ON the canvas, whose viewport carries a STATIC
  // `touch-action: none` (app.css .wfb-canvas-viewport) -- unlike the
  // palette, which lives outside the canvas in the bottom sheet with no such
  // restriction. Verified empirically with the same large mostly-vertical
  // touch drag that broke defect 13: dragging the node just placed, further
  // up the canvas, works cleanly (pointerdown/move/move/up, no
  // pointercancel) with NO code change needed. Confirmed non-bug -- this
  // assertion exists so a future regression (e.g. someone reusing this
  // exact code for a drag target OUTSIDE a touch-action:none ancestor) gets
  // caught, not to guard a fix.
  const mNodeHeadBox = await (await mpage.$('.wfb-node-head')).boundingBox();
  const mNodeStartX = mNodeHeadBox.x + mNodeHeadBox.width / 2, mNodeStartY = mNodeHeadBox.y + mNodeHeadBox.height / 2;
  const mPosBefore = await mpage.evaluate(() => { const n = window._wfEntry()._wf.def.nodes[0]; return { x: n.x, y: n.y }; });
  await mCdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: mNodeStartX, y: mNodeStartY, id: 1 }] });
  await mpage.waitForTimeout(450);
  for (const pt of [{ x: mNodeStartX, y: mNodeStartY - 150 }, { x: mNodeStartX + 20, y: mNodeStartY - 220 }]) {
    await mCdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: pt.x, y: pt.y, id: 1 }] });
    await mpage.waitForTimeout(50);
  }
  await mCdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await mpage.waitForTimeout(200);
  const mPosAfter = await mpage.evaluate(() => { const n = window._wfEntry()._wf.def.nodes[0]; return { x: n.x, y: n.y }; });
  (mPosAfter.y < mPosBefore.y - 100)
    ? ok(`a real touch long-press-drag moved an EXISTING node on the mobile canvas (dy=${(mPosAfter.y - mPosBefore.y).toFixed(0)}) -- the canvas's own touch-action:none already protects this path`)
    : fail(`a real touch drag did not move an existing node on mobile -- ${JSON.stringify(mPosBefore)} -> ${JSON.stringify(mPosAfter)}`);

  // Same noise filter every other context in this suite (and boot-smoke.mjs)
  // applies: opening a real project modal lazy-loads mermaid.js, whose CDN
  // import this hermetic run always aborts (no network) -- expected, not a
  // regression this file introduced.
  const mUncaught = mPageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (mUncaught.length) mUncaught.forEach((e) => fail('uncaught page error on mobile boot: ' + e));
  else ok('mobile viewport booted the builder clean, no uncaught exceptions');
  await mctx.close();

  // Reopen a desktop context for the save round-trip (mobile context above
  // was closed to keep viewport switching hermetic).
  const ctx2 = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page2 = await ctx2.newPage();
  const page2Errors = [];
  page2.on('pageerror', (e) => page2Errors.push(e.message || String(e)));
  const workflowPosts2 = [];
  const savedWorkflows2 = [];
  await page2.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/local-auth/status') return route.fulfill({ status: 200, contentType: 'application/json', body: '{"configured":true}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/floor') return route.fulfill({ status: 200, contentType: 'application/json', body: FLOOR_JSON });
    if (path.startsWith('/api/avatars/')) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1PX });
    // GET reflects whatever's been saved so far -- lets the tabs-row
    // assertion below confirm a save makes the workflow's tab appear.
    if (path === '/api/workflows' && req.method() === 'GET') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(savedWorkflows2) });
    if (path === `/api/project/${PID}/workflows`) return route.fulfill({ status: 200, contentType: 'application/json', body: '{"workflows":[]}' });
    // Unmocked, these abort forever and agent-log.js's rail poller retries
    // in an unbounded tight loop -- enough iterations across a section's
    // many DOM waits crashed the renderer outright (page5, deterministic).
    if (path === `/api/project/${PID}/agent/log`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/conversations`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/workflows' && req.method() === 'POST') {
      const body = JSON.parse(req.postData() || '{}');
      workflowPosts2.push(body);
      const workflow = { ...body, id: 'wf-smoke2', format: 2, created: '2026-09-11T00:00:00Z', updated: '2026-09-11T00:00:00Z' };
      savedWorkflows2.push(workflow);
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, workflow }) });
    }
    return route.abort();
  });
  await page2.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page2.waitForSelector('#projects-col .card', { timeout: 15000 });
  await newWorkflow(page2, PID);
  const vpBox2 = await (await page2.$('#wfb-canvas-viewport')).boundingBox();
  // MC-962: see the same fix + comment at the first drag above -- the
  // describe box now shortens the empty-canvas viewport.
  await dragPalettePersonTo(page2, 'Tobin', () => firstDropPoint(page2));
  // MC-963: the drop auto-opens the inspector (_wfFocusPrompt) -- it's
  // already open on this node, so address its fields there directly.
  await setValue(page2, '#wfb-inspector .wfb-name', 'harvest-triage');
  await setValue(page2, '#wfb-inspector .wfb-prompt', 'Score the signals.');
  await closeInspector(page2);
  await setValue(page2, '#wfb-name', 'Smoke test workflow');
  // #wfb-desc only exists once the description disclosure is open (Change 1
  // — collapsed by default unless the loaded def already has one).
  await page2.click('.wfb-toolbar-desc-toggle');
  await page2.waitForSelector('#wfb-desc', { timeout: 3000 });
  await setValue(page2, '#wfb-desc', 'Exercises the canvas end to end.');

  // ── MC-871 Change 2: the Trigger box lives on the canvas, first in the
  // flow, and is draggable via the SAME node-drag pointer path. ────────────
  const triggerVisible = await page2.$eval('.wfb-trigger-box', () => true).catch(() => false);
  triggerVisible ? ok('the Trigger box renders on the canvas alongside the nodes')
                 : fail('no .wfb-trigger-box found on the canvas');
  // Regression guard for the MC-871 canvas-iteration bugs Ron hit after this
  // suite last ran clean (Ron's defects 4/6/9): an untouched trigger USED TO
  // have no persisted x/y at all, recomputing a fresh default position from
  // "whichever node is currently the graph's root" on every single
  // _wfRender() — so dropping a new node, or connecting one, silently
  // re-homed the trigger tile even though nobody dragged it. _wfRenderTriggerBox
  // now pins the computed default into the model the first time it's needed,
  // so it must have real x/y immediately, and that value must NOT change
  // just because the graph changes underneath it.
  const triggerBeforeDrag = await page2.evaluate(() => window._wfEntry()._wf.def.trigger);
  (triggerBeforeDrag && typeof triggerBeforeDrag.x === 'number' && typeof triggerBeforeDrag.y === 'number')
    ? ok(`an untouched trigger already has a pinned x/y (${triggerBeforeDrag.x},${triggerBeforeDrag.y}) — no drag needed for it to be stable`)
    : fail(`expected the trigger's computed default position to be pinned into the model immediately, got ${JSON.stringify(triggerBeforeDrag)}`);
  // Force a re-render via an UNRELATED structural action (toggling Enabled)
  // and confirm the trigger did not silently re-home itself -- this is the
  // exact shape of defect 4 ("dropping a block beside Start snapped it").
  await page2.evaluate(() => { window._wfToggleEnabled(); window._wfToggleEnabled(); });
  const triggerAfterUnrelatedRerender = await page2.evaluate(() => window._wfEntry()._wf.def.trigger);
  (triggerAfterUnrelatedRerender.x === triggerBeforeDrag.x && triggerAfterUnrelatedRerender.y === triggerBeforeDrag.y)
    ? ok('the trigger stayed exactly where it was across an unrelated re-render')
    : fail(`the trigger moved from ${JSON.stringify(triggerBeforeDrag)} to ${JSON.stringify(triggerAfterUnrelatedRerender)} on a re-render nobody asked it to move for`);
  const triggerHeadBox = await (await page2.$('.wfb-trigger-box-head')).boundingBox();
  await page2.mouse.move(triggerHeadBox.x + triggerHeadBox.width / 2, triggerHeadBox.y + triggerHeadBox.height / 2);
  await page2.mouse.down();
  await page2.mouse.move(triggerHeadBox.x + 60, triggerHeadBox.y + 40, { steps: 6 });
  await page2.mouse.up();
  await page2.waitForTimeout(120);
  const triggerAfterDrag = await page2.evaluate(() => window._wfEntry()._wf.def.trigger);
  (triggerAfterDrag && typeof triggerAfterDrag.x === 'number' && typeof triggerAfterDrag.y === 'number')
    ? ok(`dragging the Trigger box's head wrote trigger.x/y into the model (${triggerAfterDrag.x},${triggerAfterDrag.y})`)
    : fail(`dragging the Trigger box did not persist a position: ${JSON.stringify(triggerAfterDrag)}`);
  // A real drag must NOT also open the config popover (Change 2's click-vs-
  // drag distinguishing test).
  const popoverAfterDrag = await page2.$('#wfb-trigger-popover');
  popoverAfterDrag ? fail('dragging the Trigger tile incorrectly opened its popover too')
                    : ok('dragging the Trigger tile does not also open its popover');

  // ── MC-871 Change 4a — REVERSED from the original spec text this smoke
  // used to assert ("the Trigger box has no ports, left unconnected, as
  // specified"): Ron asked for a real connection point, so the trigger now
  // carries exactly one output port. ────────────────────────────────────────
  const triggerPortCount = await page2.$$eval('.wfb-trigger-box .wfb-port', els => els.length);
  triggerPortCount === 1 ? ok('the Trigger box now renders exactly one output port (Change 4a reversal)')
                         : fail(`expected exactly 1 port on the Trigger box, got ${triggerPortCount}`);

  // A plain CLICK (no drag) on the trigger head opens the popover.
  await page2.click('.wfb-trigger-box-head');
  await page2.waitForSelector('#wfb-trigger-popover', { timeout: 3000 })
    .then(() => ok('a plain click on the Trigger tile opens its config popover'))
    .catch(() => fail('clicking the Trigger tile did not open a popover'));
  await page2.click('.wfb-trigger-box-head'); // a second click closes it (real toggle path, not a raw DOM removal)
  await page2.waitForTimeout(80);

  // ── MC-871 Change 12a — a fresh drop must NOT auto-wire to the trigger.
  // The first cut of 4a drew a trigger line to every ROOT node, so any
  // standalone drop (which starts with no incoming edges) instantly looked
  // wired with zero action from Ron. Confirms the correction: an ordinary
  // drop leaves `def.trigger.entry` untouched and draws no implied line. ───
  const namesBeforeC12a = await page2.evaluate(() => window._wfEntry()._wf.def.nodes.map(n => n.name));
  await dragPalettePersonTo(page2, 'Tobin', () => emptyCanvasPoint(page2));
  await page2.waitForTimeout(100);
  const c12aNewName = await page2.evaluate((before) => window._wfEntry()._wf.def.nodes.map(n => n.name).find(n => !before.includes(n)), namesBeforeC12a);
  const c12aEntryAfterPlainDrop = await page2.evaluate(() => { const t = window._wfEntry()._wf.def.trigger; return (t && t.entry) || []; });
  !c12aEntryAfterPlainDrop.includes(c12aNewName)
    ? ok(`a plain drop on empty canvas ("${c12aNewName}") is a root but is NOT auto-added to trigger.entry`)
    : fail(`a plain drop was incorrectly auto-wired to the trigger: entry=${JSON.stringify(c12aEntryAfterPlainDrop)}`);
  const c12aImpliedCount = await page2.$$eval('.wfb-edge-implied', els => els.length);
  const c12aUnwiredBadge = await page2.$(`.wfb-node[data-name="${c12aNewName}"] .wfb-node-unwired-badge`);
  c12aUnwiredBadge ? ok(`the un-wired root "${c12aNewName}" carries the honesty badge (it will still run at start)`)
                   : fail('expected the unwired-root badge on a fresh standalone root');
  // MC-963: this drop auto-opened the inspector (_wfFocusPrompt) -- close it
  // before the port-drag below, both to match a real user's next action and
  // because the open inspector narrows the canvas viewport, which shifts
  // this card's on-screen position (it was placed by `emptyCanvasPoint`
  // BEFORE the inspector claimed that screen space) enough that the target
  // head's boundingBox lands under the inspector panel instead of the card.
  await closeInspector(page2);

  // Dragging FROM the trigger's port ONTO an existing card (the honest
  // inverse) DOES explicitly wire it — this is the one gesture that's
  // supposed to add to trigger.entry.
  const triggerPortPt = await portCenter(page2, '.wfb-trigger-box .wfb-port-out');
  const c12aTargetHead = await (await page2.$(`.wfb-node[data-name="${c12aNewName}"] .wfb-node-head`)).boundingBox();
  await page2.mouse.move(triggerPortPt.x, triggerPortPt.y);
  await page2.mouse.down();
  await page2.mouse.move((triggerPortPt.x + c12aTargetHead.x) / 2, (triggerPortPt.y + c12aTargetHead.y) / 2, { steps: 6 });
  await page2.mouse.move(c12aTargetHead.x + c12aTargetHead.width / 2, c12aTargetHead.y + c12aTargetHead.height / 2, { steps: 6 });
  await page2.mouse.up();
  await page2.waitForTimeout(120);
  const c12aEntryAfterDrag = await page2.evaluate(() => (window._wfEntry()._wf.def.trigger.entry || []));
  c12aEntryAfterDrag.includes(c12aNewName)
    ? ok(`dragging FROM the trigger port ONTO the card explicitly wired "${c12aNewName}" (trigger.entry)`)
    : fail(`expected "${c12aNewName}" in trigger.entry after the explicit drag, got ${JSON.stringify(c12aEntryAfterDrag)}`);
  const c12aBadgeGone = await page2.$(`.wfb-node[data-name="${c12aNewName}"] .wfb-node-unwired-badge`);
  c12aBadgeGone ? fail('the unwired-root badge should have cleared after explicit trigger-wiring')
                : ok('the unwired-root badge clears once the node is explicitly wired');

  // A path with fill other than none is invisible-bug class (Ron's screenshot:
  // huge black wedges from a missing `fill:none` on a new SVG <path>) —
  // guard every path in the edge layer, not just the ones this suite already
  // knew to check.
  const badFills = await page2.$$eval('#wfb-canvas-svg path', els => els
    .map(el => ({ cls: el.getAttribute('class'), fill: getComputedStyle(el).fill }))
    .filter(x => x.fill !== 'none'));
  badFills.length === 0 ? ok('every SVG edge <path> computes fill:none (no black-wedge regression)')
                        : fail(`found path(s) without fill:none: ${JSON.stringify(badFills)}`);

  // The 12a/4a test node above (c12aNewName, dropped bare with no prompt or
  // project set — it only needed to exist to prove trigger-wiring behaviour)
  // would otherwise fail _wfValidateGraph's "needs a project"/"needs a
  // prompt" checks and silently block the Save this section tests next.
  // Remove it now that its own assertions are done.
  await page2.evaluate((n) => window._wfDeleteNode(n), c12aNewName);
  await page2.waitForTimeout(80);

  await page2.click('.wfb-toolbar .btn-sched-save');
  await answerHumanProofModalIfShown(page2);
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
  (posted.trigger && typeof posted.trigger.x === 'number' && typeof posted.trigger.y === 'number')
    ? ok(`the dragged trigger.x/y round-tripped into the SAVED body (${posted.trigger.x},${posted.trigger.y})`)
    : fail(`expected trigger.x/y in the posted body, got ${JSON.stringify(posted.trigger)}`);
  await page2.waitForFunction(() => {
    const btn = document.querySelector('.wfb-toolbar .btn-sched-save');
    return btn && btn.textContent.trim() === 'Update';
  }, { timeout: 3000 }).then(() => ok('after a successful save, the button relabels to "Update" (workflowId adopted)'),
                            () => fail('save button never relabeled to "Update" after a successful save'));

  // ── MC-871 Change 1: the tabs row picks up the newly-saved workflow
  // (_wfSave's refreshWorkflowsList -> loadWorkflows -> window._wfSyncTabsForProject)
  // WITHOUT losing the canvas that's still mounted showing it. ────────────
  await page2.waitForFunction(() => document.querySelectorAll('.wfb-tab:not(.wfb-tab-new)').length === 1,
    { timeout: 3000 }).then(() => ok('a tab for the newly-saved workflow appeared in the tabs row'),
                            () => fail('no workflow tab appeared after saving'));
  const tabLabel = await page2.$eval('.wfb-tab.active:not(.wfb-tab-new)', el => el.textContent.trim()).catch(() => null);
  tabLabel === 'Smoke test workflow' ? ok(`the new tab is marked active and named "${tabLabel}"`)
                                     : fail(`expected the active tab to read "Smoke test workflow", got ${JSON.stringify(tabLabel)}`);
  const canvasSurvivedTabRebuild = await page2.$eval('.wfb-node[data-name="harvest-triage"]', () => true).catch(() => false);
  canvasSurvivedTabRebuild ? ok('the tabs-row rebuild did not clobber the still-mounted canvas (same node still there)')
                           : fail('the tabs-row rebuild after save lost the mounted canvas');

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
    if (path === '/api/local-auth/status') return route.fulfill({ status: 200, contentType: 'application/json', body: '{"configured":true}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/floor') return route.fulfill({ status: 200, contentType: 'application/json', body: FLOOR_JSON });
    if (path.startsWith('/api/avatars/')) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1PX });
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
    if (path === `/api/project/${PID}/workflows`) return route.fulfill({ status: 200, contentType: 'application/json', body: '{"workflows":[]}' });
    // Unmocked, these abort forever and agent-log.js's rail poller retries
    // in an unbounded tight loop -- enough iterations across a section's
    // many DOM waits crashed the renderer outright (page5, deterministic).
    if (path === `/api/project/${PID}/agent/log`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/conversations`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    return route.abort();
  });
  await page3.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page3.waitForSelector('#projects-col .card', { timeout: 15000 });
  await page3.evaluate(() => {
    window.__toasts = [];
    const orig = window.showToast;
    window.showToast = (msg, ms) => { window.__toasts.push(msg); if (orig) orig(msg, ms); };
  });
  await newWorkflow(page3, PID);

  await setValue(page3, '#wfb-name', 'Cadence smoke workflow');
  const vpBox3 = await (await page3.$('#wfb-canvas-viewport')).boundingBox();
  // MC-962: see the same fix + comment at the first drag above -- the
  // describe box now shortens the empty-canvas viewport.
  await dragPalettePersonTo(page3, 'Tobin', () => firstDropPoint(page3));
  // MC-963: the drop auto-opens the inspector (_wfFocusPrompt) -- it's
  // already open here, but address fields under it explicitly rather than
  // rely on that side effect.
  await page3.waitForSelector('#wfb-inspector.wfb-inspector-open', { timeout: 5000 });
  await setValue(page3, '#wfb-inspector .wfb-name', 'step-one');
  await setValue(page3, '#wfb-inspector .wfb-prompt', 'Do the thing.');
  await closeInspector(page3);

  // MC-871 Change 2: the TRIGGER radios/cadence form no longer sit in a
  // permanent card above the canvas — they're reused verbatim inside a
  // popover opened by clicking the Trigger tile. Open it once; every mutator
  // below (_wfSetSchedType etc.) already ends in the shared _wfRender(),
  // which keeps #wfb-trigger-popover's content in sync while it's open, so
  // the rest of this sequence is unchanged from before the rehost.
  await page3.click('.wfb-trigger-box-head');
  await page3.waitForSelector('#wfb-trigger-popover', { timeout: 3000 })
    .then(() => ok('clicking the Trigger tile opens its config popover'))
    .catch(() => fail('the Trigger tile did not open a popover on click'));
  // Defect 11 (Ron: "if we have a Run now button, why do we also need that
  // option on the start tile?") replaced the Manual/Schedule radio pair with
  // a single unchecked-by-default "Run on a schedule" checkbox -- unchecked
  // IS manual, so checking it is the only gesture that turns scheduling on.
  await page3.click('#wfb-trigger-popover input[type="checkbox"]');
  await page3.waitForSelector('#wfb-trigger-popover .wfb-sched-cadence', { timeout: 3000 });
  ok('checking "Run on a schedule" (inside the popover) renders the cadence sub-form');

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
  {
    // Same CDN-import noise as the mobile check above (mermaid.js, aborted --
    // no network in this hermetic run).
    const cadenceUncaught = page3Errors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    if (cadenceUncaught.length) cadenceUncaught.forEach((e) => fail('uncaught page error while switching cadence type: ' + e));
    page3Errors.length = 0;
  }

  // Weekly with no day picked must refuse to save — before this fix the day
  // row was unreachable at all, so "saved empty" wasn't even the failure
  // mode; now that it's reachable, confirm the empty set is still refused.
  await page3.click('.sched-type-btn:text-is("Weekly")');
  await page3.waitForTimeout(80);
  const toastsBeforeEmpty = (await page3.evaluate(() => (window.__toasts || []).length));
  await page3.click('.wfb-toolbar .btn-sched-save');
  await page3.waitForTimeout(150);
  const newToasts = (await page3.evaluate(() => window.__toasts || [])).slice(toastsBeforeEmpty);
  newToasts.some((t) => /day/i.test(t)) ? ok(`saving Weekly with no days picked was refused: "${newToasts.find((t) => /day/i.test(t))}"`)
                                        : fail(`expected a toast refusing an empty weekly day set, got ${JSON.stringify(newToasts)}`);
  savedWorkflow3 === null ? ok('the empty-days guard fired before the workflow POST — nothing saved')
                          : fail('a workflow was posted despite an empty weekly day set');

  // Pick Wednesday and save for real. Clicking Save is itself a click
  // OUTSIDE the popover, which closes it via the same outside-click handler
  // the port "+" popover already uses — correct, expected behaviour, not a
  // bug (see _wfTriggerPopoverOutsideDown) — so it needs reopening here to
  // keep editing the cadence.
  await page3.click('.wfb-trigger-box-head');
  await page3.waitForSelector('.sched-day-btn[data-day="3"]', { timeout: 3000 });
  await page3.click('.sched-day-btn[data-day="3"]');
  await page3.click('.wfb-toolbar .btn-sched-save');
  await answerHumanProofModalIfShown(page3);
  await page3.waitForFunction(() => {
    const btn = document.querySelector('.wfb-toolbar .btn-sched-save');
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

  // Reopen the SAME workflow FROM THE SERVER (no floating modal to close any
  // more -- openWorkflowBuilder re-fetches and remounts, which is what
  // actually exercises the round trip; the save just above left nothing
  // dirty, so this doesn't hit the discard-changes confirm).
  await page3.evaluate(({ id, pid }) => { window.openWorkflowBuilder(id, pid); }, { id: savedWorkflow3.id, pid: PID });
  await page3.waitForSelector('#wfb-canvas-viewport', { timeout: 5000 });
  // A reload closes any previously-open trigger popover (openWorkflowBuilder
  // now does this explicitly — its old content would otherwise point at a
  // stale load). Reopen it to inspect the reloaded cadence.
  await page3.click('.wfb-trigger-box-head');
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

  // ── MC-871 agent-card pass: the prompt collapse/expand (Ron: "why show the
  // prompt in the first place?" -- an always-visible dimmed textarea per card
  // is a wall of grey text on a multi-step canvas). Covers: collapses/reopens
  // on the user's own toggle, the expand state survives a re-render in BOTH
  // directions, an empty prompt is never hidden behind the collapse, and a
  // reference that becomes illegal (a rename) forces the panel back open with
  // no way to hide it. ─────────────────────────────────────────────────────
  const ctx4 = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page4 = await ctx4.newPage();
  const page4Errors = [];
  page4.on('pageerror', (e) => page4Errors.push(e.message || String(e)));
  await page4.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/local-auth/status') return route.fulfill({ status: 200, contentType: 'application/json', body: '{"configured":true}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/floor') return route.fulfill({ status: 200, contentType: 'application/json', body: FLOOR_JSON });
    if (path.startsWith('/api/avatars/')) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1PX });
    if (path === '/api/workflows' && req.method() === 'GET') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/workflows`) return route.fulfill({ status: 200, contentType: 'application/json', body: '{"workflows":[]}' });
    // Unmocked, these abort forever and agent-log.js's rail poller retries
    // in an unbounded tight loop -- enough iterations across a section's
    // many DOM waits crashed the renderer outright (page5, deterministic).
    if (path === `/api/project/${PID}/agent/log`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/conversations`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    return route.abort();
  });
  await page4.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page4.waitForSelector('#projects-col .card', { timeout: 15000 });
  await newWorkflow(page4, PID);

  // MC-963: the prompt panel (with its own collapse/expand) lives only
  // inside #wfb-inspector now, one node at a time -- so every DOM check
  // below addresses `#wfb-inspector .foo` rather than a per-card selector,
  // and cross-node persistence is checked against the model's own
  // st.promptOpen map (which survives regardless of which inspector is
  // currently mounted), not the DOM.
  const vpBox4 = await (await page4.$('#wfb-canvas-viewport')).boundingBox();
  await dragPalettePersonTo(page4, 'Tobin', vpBox4.x + 140, vpBox4.y + 200);
  const c4Name1 = await page4.$eval('.wfb-node', el => el.dataset.name);
  await page4.waitForSelector('#wfb-inspector.wfb-inspector-open', { timeout: 5000 });
  (await page4.$('#wfb-inspector .wfb-prompt'))
    ? ok('a freshly-dropped, empty-prompt step opens straight to the editable textarea — never a collapsed summary')
    : fail('a fresh empty-prompt step rendered collapsed instead of forcing its panel open');
  (await page4.$('#wfb-inspector .wfb-prompt-toggle'))
    ? fail('an empty prompt still offered a collapse control — an unfixed error must not be hideable')
    : ok('an empty prompt offers no "Hide prompt" control -- the error cannot be collapsed away');
  await setValue(page4, '#wfb-inspector .wfb-name', 'triage');
  await setValue(page4, '#wfb-inspector .wfb-prompt', 'Decide whether this is worth drafting.');

  // Force a sync+render via an action unrelated to the prompt panel itself
  // (same technique the Trigger-position regression guard above uses) --
  // now that the prompt is non-empty, this is the first render where the
  // collapse control is actually reachable.
  await page4.evaluate(() => { window._wfToggleEnabled(); window._wfToggleEnabled(); });
  const c4HideBtn = await page4.$('#wfb-inspector .wfb-prompt-toggle');
  c4HideBtn ? ok('once filled in, the prompt panel offers a "Hide prompt" control')
            : fail('expected a collapse control on a filled-in, valid prompt panel');
  await c4HideBtn.click();
  await page4.waitForTimeout(80);
  const c4CollapsedSummary = await page4.$eval('#wfb-inspector .wfb-prompt-summary', el => el.textContent).catch(() => null);
  (await page4.$('#wfb-inspector .wfb-prompt')) === null && c4CollapsedSummary === 'Decide whether this is worth drafting.'
    ? ok(`clicking "Hide prompt" collapsed the panel to a one-line summary: "${c4CollapsedSummary}"`)
    : fail(`collapse did not behave as expected (summary=${JSON.stringify(c4CollapsedSummary)})`);
  await closeInspector(page4);

  // Expand state must survive a re-render (file header's own recurring
  // failure class: "state written but lost on rebuild" -- costs a whole
  // round here every time it regresses). Dropping Fenn auto-opens ITS
  // inspector (_wfFocusPrompt always switches), so triage's own panel is
  // off-DOM entirely at this point -- check the model instead.
  await dragPalettePersonTo(page4, 'Fenn', vpBox4.x + 460, vpBox4.y + 120);
  await closeInspector(page4);
  const stillCollapsed = await page4.evaluate(() => window._wfEntry()._wf.promptOpen['triage'] === false);
  stillCollapsed ? ok('the collapsed state survived an unrelated structural re-render (placing a second block)')
                 : fail('placing a second block re-expanded a panel the user had explicitly collapsed');

  const c4Name2 = await page4.$eval('.wfb-node:not([data-name="triage"])', el => el.dataset.name);
  await openInspector(page4, c4Name2);
  (await page4.$('#wfb-inspector .wfb-prompt'))
    ? ok("the second, freshly-dropped node's own empty prompt still opens straight to the textarea")
    : fail("the second node's empty prompt did not force its panel open");

  // Reopen "triage" and confirm the typed text round-tripped through the
  // collapse -- collapsing must never lose what was typed.
  await openInspector(page4, 'triage');
  await page4.click('#wfb-inspector .wfb-prompt-toggle');
  await page4.waitForTimeout(80);
  const c4Reopened = await page4.$eval('#wfb-inspector .wfb-prompt', el => el.value).catch(() => null);
  c4Reopened === 'Decide whether this is worth drafting.'
    ? ok('reopening the panel restores the exact text that was there before it was collapsed')
    : fail(`reopening lost or altered the prompt text: ${JSON.stringify(c4Reopened)}`);

  // ── A reference that BECOMES illegal (brief: "the step was renamed") must
  // force the panel open and stay legible -- the collapse must never hide it.
  await closeInspector(page4);
  await dragPortTo(page4,
    `.wfb-node[data-name="triage"] .wfb-port-out`,
    `.wfb-node[data-name="${c4Name2}"] .wfb-port-in`);
  await openInspector(page4, c4Name2);
  await setValue(page4, '#wfb-inspector .wfb-name', 'draft');
  await setValue(page4, '#wfb-inspector .wfb-prompt', 'Draft from {{steps.triage.output}}.');
  await page4.evaluate(() => { window._wfToggleEnabled(); window._wfToggleEnabled(); });
  const c4ChipBeforeRename = await page4.$eval('#wfb-inspector .wfb-slot-chip', el => el.className).catch(() => null);
  (c4ChipBeforeRename && !c4ChipBeforeRename.includes('wfb-slot-chip-broken'))
    ? ok('the reference to "triage" reads as valid before the rename')
    : fail(`expected a valid (non-broken) chip before the rename, got ${JSON.stringify(c4ChipBeforeRename)}`);
  await page4.click('#wfb-inspector .wfb-prompt-toggle'); // collapse it -- a valid prompt, nothing forcing it open
  await page4.waitForTimeout(80);
  (await page4.$('#wfb-inspector .wfb-prompt')) === null
    ? ok('the "draft" panel collapses normally while its reference is still valid')
    : fail('the "draft" panel did not collapse despite having a valid, filled-in prompt');
  await closeInspector(page4);

  // Rename "triage" -- its edges get repointed (existing behaviour), but the
  // literal `{{steps.triage.output}}` text inside "draft"'s prompt does not
  // get rewritten (file header: renames aren't guarded against breaking a
  // TEXT slot reference elsewhere), so it is now a dangling reference to a
  // name that no longer exists.
  await openInspector(page4, 'triage');
  await setValue(page4, '#wfb-inspector .wfb-name', 'triage2');
  await page4.evaluate(() => { window._wfToggleEnabled(); window._wfToggleEnabled(); });
  await closeInspector(page4);
  await openInspector(page4, 'draft');
  const c4DraftPromptAfterRename = await page4.$('#wfb-inspector .wfb-prompt');
  c4DraftPromptAfterRename
    ? ok('renaming an upstream step re-forces the dependent panel open instead of leaving it collapsed')
    : fail('a reference broken by an upstream rename stayed hidden behind the collapse');
  const c4BrokenChip = await page4.$eval('#wfb-inspector .wfb-slot-chip', el => el.className).catch(() => null);
  (c4BrokenChip && c4BrokenChip.includes('wfb-slot-chip-broken'))
    ? ok('the now-illegal {{steps.triage.output}} reference is flagged broken, visibly, without being collapsed away')
    : fail(`expected the chip to read broken after the rename, got ${JSON.stringify(c4BrokenChip)}`);
  (await page4.$('#wfb-inspector .wfb-prompt-toggle'))
    ? fail('a panel forced open by a live broken reference still offered a way to hide it')
    : ok('no collapse control is offered while the broken reference is live -- it cannot be hidden away');

  const uncaught4 = page4Errors.filter(e => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught4.length) uncaught4.forEach((e) => fail('uncaught exception in the prompt collapse/expand flow: ' + e));
  await ctx4.close();

  // ── MC-871 palette-composition pass: the Wait node end-to-end, and the
  // Action/Approval cards' narrowing pattern (thing -> verb -> settings; pick
  // the kind of wait, then only that kind's field). Each "not relevant yet"
  // assertion below checks a control's ABSENCE, not just its presence — the
  // rule this pass exists to enforce is "never show a control before it
  // applies", which a presence-only check can't catch a regression of. ──────
  let workflowPosts5 = [];
  const ctx5 = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page5 = await ctx5.newPage();
  const page5Errors = [];
  page5.on('pageerror', (e) => page5Errors.push(e.message || String(e)));
  const page5Dialogs = [];
  page5.on('dialog', async (d) => { page5Dialogs.push(d.message()); await d.accept(); });
  await page5.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/local-auth/status') return route.fulfill({ status: 200, contentType: 'application/json', body: '{"configured":true}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/floor') return route.fulfill({ status: 200, contentType: 'application/json', body: FLOOR_JSON });
    if (path.startsWith('/api/avatars/')) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1PX });
    if (path === '/api/workflows' && req.method() === 'GET') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/workflows`) return route.fulfill({ status: 200, contentType: 'application/json', body: '{"workflows":[]}' });
    // Unmocked, these abort forever and agent-log.js's rail poller retries
    // in an unbounded tight loop -- enough iterations across a section's
    // many DOM waits crashed the renderer outright (page5, deterministic).
    if (path === `/api/project/${PID}/agent/log`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/conversations`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/workflows' && req.method() === 'POST') {
      const body = JSON.parse(req.postData() || '{}');
      workflowPosts5.push(body);
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        ok: true, workflow: { ...body, id: 'wf-smoke5', format: 2, created: '2026-09-11T00:00:00Z', updated: '2026-09-11T00:00:00Z' },
      }) });
    }
    // A second `_wfSave()` after the first (the rename+insert-persistence
    // case below) goes out as a PUT once st.workflowId is set — unmocked,
    // this silently route.abort()s and the save never happens at all.
    if (path === '/api/workflows/wf-smoke5' && req.method() === 'PUT') {
      const body = JSON.parse(req.postData() || '{}');
      workflowPosts5.push(body);
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        ok: true, workflow: { ...body, id: 'wf-smoke5', format: 2, created: '2026-09-11T00:00:00Z', updated: '2026-09-11T00:10:00Z' },
      }) });
    }
    return route.abort();
  });
  await page5.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page5.waitForSelector('#projects-col .card', { timeout: 15000 });
  await newWorkflow(page5, PID);

  // ── Wait: drag it on, confirm the default is "For a delay" with a minutes
  // field and NO datetime field, then switch modes and confirm the reverse.
  // MC-963: this config lives only in #wfb-inspector now -- Wait/Action/
  // Approval drops don't auto-open it (_wfFocusPrompt only fires for
  // type:'agent'), so each one is opened explicitly below. ─────────────────
  const vpBox5 = await (await page5.$('#wfb-canvas-viewport')).boundingBox();
  await dragPaletteToolTo(page5, 'Wait', vpBox5.x + 140, vpBox5.y + 160);
  const waitName = await page5.$eval('.wfb-node', el => el.dataset.name);
  await openInspector(page5, waitName);
  (await page5.$eval('#wfb-inspector .wfb-wait-mode-select', el => el.value)) === 'delay'
    ? ok('a freshly-dropped Wait defaults to "For a delay"')
    : fail('a freshly-dropped Wait did not default to delay mode');
  (await page5.$('#wfb-inspector [data-cfg-key="minutes"]')) && !(await page5.$('#wfb-inspector [data-cfg-key="at"]'))
    ? ok('delay mode shows the minutes field and NOT the date/time field — no not-yet-relevant control')
    : fail('delay mode rendered the wrong field(s)');
  await setValue(page5, '#wfb-inspector [data-cfg-key="minutes"]', '45');
  await page5.selectOption('#wfb-inspector .wfb-wait-mode-select', 'until');
  await page5.waitForTimeout(80);
  (await page5.$('#wfb-inspector [data-cfg-key="at"]')) && !(await page5.$('#wfb-inspector [data-cfg-key="minutes"]'))
    ? ok('switching to "Until a date and time" shows the date/time field and hides minutes — never both at once')
    : fail('switching wait modes left the wrong field(s) visible');
  await setValue(page5, '#wfb-inspector [data-cfg-key="at"]', '2027-01-01T09:30');
  await closeInspector(page5);

  await setValue(page5, '#wfb-name', 'Wait smoke workflow');
  // _wfSave() now awaits humanProofFetch() internally, which does not
  // resolve until the modal below is answered -- `await page.evaluate(() =>
  // window._wfSave())` would return that unresolved promise to Playwright
  // and deadlock forever (the very next line, which answers the modal, would
  // never get to run). Discard the inner promise so evaluate() returns
  // immediately; the modal exists in the page regardless of what page.evaluate
  // awaits.
  await page5.evaluate(() => { window._wfSave(); });
  await answerHumanProofModalIfShown(page5);
  await page5.waitForTimeout(150);
  const savedWaitNode = workflowPosts5.length
    ? (workflowPosts5[workflowPosts5.length - 1].nodes || []).find(n => n.type === 'wait') : null;
  (savedWaitNode && savedWaitNode.config && savedWaitNode.config.mode === 'until' && /^2027-01-01T/.test(savedWaitNode.config.at || ''))
    ? ok(`the saved Wait node round-tripped mode "until" with the picked date: ${JSON.stringify(savedWaitNode.config)}`)
    : fail(`expected a saved wait node with mode "until", got ${JSON.stringify(savedWaitNode)}`);

  // ── Action: the two-level What/Do narrowing. Backlog has 2 verbs -> a real
  // second dropdown; Notify has 1 -> static text, no dropdown to open.
  // `emptyCanvasPoint` (not a fixed offset) — the wait card from above is
  // still on the canvas, and cards are 260px wide, so a fixed delta risks
  // dropping ON it (drop-onto-card wires an edge instead of a fresh place). ──
  const actPt = await emptyCanvasPoint(page5);
  await dragPaletteToolTo(page5, 'Action', actPt.x, actPt.y);
  const allNodeNames5 = await page5.$$eval('.wfb-node', els => els.map(e => e.dataset.name));
  const actName = allNodeNames5.find(n => n !== waitName);
  await openInspector(page5, actName);
  (await page5.$eval('#wfb-inspector .wfb-action-group-select', el => el.value)) === 'Backlog'
    ? ok('a freshly-dropped Action defaults to the Backlog group')
    : fail('a freshly-dropped Action did not default to Backlog');
  (await page5.$('#wfb-inspector .wfb-action-verb-select')) && !(await page5.$('#wfb-inspector .wfb-action-verb-single'))
    ? ok('Backlog (2 verbs) renders a real second dropdown, not static text')
    : fail('Backlog should render a verb dropdown, not static text');
  await page5.fill('#wfb-inspector [data-cfg-key="text"]', 'Something typed that would be lost');
  await page5.selectOption('#wfb-inspector .wfb-action-group-select', 'Notify');
  await page5.waitForTimeout(80);
  page5Dialogs.some(m => /clears the settings/i.test(m))
    ? ok(`switching groups with typed content prompted before discarding it: "${page5Dialogs.find(m => /clears the settings/i.test(m))}"`)
    : fail(`expected a confirm() before discarding typed config, got dialogs: ${JSON.stringify(page5Dialogs)}`);
  (await page5.$('#wfb-inspector .wfb-action-verb-single')) && !(await page5.$('#wfb-inspector .wfb-action-verb-select'))
    ? ok('Notify (1 verb) renders static text, not a single-option dropdown — no not-yet-relevant control')
    : fail('Notify should render static text, not a dropdown, for its one verb');
  (await page5.$eval('#wfb-inspector .wfb-action-id', el => el.textContent.trim())) === 'notify_operator'
    ? ok('the raw identifier stayed visible and correct after the group switch (notify_operator)')
    : fail('the raw action identifier did not update to notify_operator after switching groups');

  // ── Ron report: rename an action node, then use the Insert control on its
  // message field — the slot never actually landed in the saved definition.
  // Root cause (confirmed, not guessed): the old <select>'s onchange baked a
  // raw CSS selector containing a literal `"` into a double-quoted HTML
  // attribute, corrupting the parse; the handler never ran. Reproduces with
  // a rename still in-flight (typed, not yet synced) — the field-sync must
  // resolve the CURRENT node via the card's own DOM element, not a name
  // string baked at the last render. ──────────────────────────────────────
  await setValue(page5, '#wfb-inspector .wfb-node-own .wfb-name', 'Send email');
  await page5.click('#wfb-inspector .wfb-insert-btn');
  await page5.waitForTimeout(80);
  const menuAfterOpen = await page5.$$eval('.wfb-insert-menu-item .wfb-insert-menu-item-primary', els => els.map(e => e.textContent));
  menuAfterOpen.includes("This run's ID")
    ? ok(`the Insert menu opened with plain-English options: ${JSON.stringify(menuAfterOpen)}`)
    : fail(`expected "This run's ID" in the Insert menu, got ${JSON.stringify(menuAfterOpen)}`);
  const runIdItem = await page5.evaluateHandle(() => [...document.querySelectorAll('.wfb-insert-menu-item')]
    .find((el) => (el.querySelector('.wfb-insert-menu-item-primary') || {}).textContent === "This run's ID"));
  await runIdItem.asElement().click();
  await page5.waitForTimeout(80);
  (await page5.$('.wfb-insert-menu')) === null
    ? ok('the Insert menu closed itself on pick — no lingering open menu')
    : fail('the Insert menu stayed open after picking an item');
  const modelAfterInsert = await page5.evaluate((oldName) => {
    const def = window._wfEntry()._wf.def;
    const node = def.nodes.find(n => n.name === 'Send email') || def.nodes.find(n => n.name === oldName);
    return node ? { name: node.name, config: node.config } : null;
  }, actName);
  (modelAfterInsert && modelAfterInsert.name === 'Send email' && /\{\{run\.id\}\}/.test((modelAfterInsert.config || {}).message || ''))
    ? ok(`inserting a result into a just-renamed action node's message field persisted in-memory: ${JSON.stringify(modelAfterInsert)}`)
    : fail(`inserting a result into a just-renamed action node's message field did NOT persist: ${JSON.stringify(modelAfterInsert)}`);
  // MC-963: the message field lives in #wfb-inspector, not on the canvas
  // card -- the inspector's own re-render after the insert is what carries
  // the flash class now.
  const flashedAfterInsert = await page5.$eval('#wfb-inspector [data-cfg-key="message"]', el => el.classList.contains('clayrune-highlight'));
  flashedAfterInsert
    ? ok('the message field flashed (.clayrune-highlight) right after the insert landed — visible confirmation it worked')
    : fail('expected the message field to carry the highlight-flash class immediately after inserting');

  await setValue(page5, '#wfb-name', 'Renamed action smoke workflow');
  await page5.evaluate(() => { window._wfSave(); });
  await answerHumanProofModalIfShown(page5);
  await page5.waitForTimeout(150);
  const savedActionNode = workflowPosts5.length
    ? (workflowPosts5[workflowPosts5.length - 1].nodes || []).find(n => n.name === 'Send email') : null;
  (savedActionNode && /\{\{run\.id\}\}/.test((savedActionNode.config || {}).message || ''))
    ? ok(`the saved definition kept {{run.id}} in the renamed action node's message after Save: ${JSON.stringify(savedActionNode && savedActionNode.config)}`)
    : fail(`the saved action node lost the inserted slot after rename+Save: ${JSON.stringify(savedActionNode)}`);

  // ── Plain-English ancestor labels: an agent ancestor's option must read
  // as WHO ("Tobin's result"), with the internal step name demoted to a
  // secondary line -- never the raw step identifier as the primary label.
  // Wired directly into the model (not a live drag) -- the canvas is
  // already crowded from the cases above, and this assertion is about the
  // LABEL the menu renders for an existing legal ancestor, not about
  // drag-to-wire mechanics (covered elsewhere in this suite). ────────────────
  // Legal-slot computation (`_wfInsertOptions`) reads `entry._wf.def`
  // directly, not the DOM -- no re-render needed for the menu to see this.
  await page5.evaluate(({ actNodeName, pid }) => {
    const def = window._wfEntry()._wf.def;
    def.nodes.push({ character: 'global:builder', name: 'triage-agent', outcomes: [],
      project_id: pid, prompt: 'triage', type: 'agent', x: 40, y: 900 });
    def.edges = def.edges || [];
    def.edges.push({ from: 'triage-agent', to: actNodeName });
  }, { actNodeName: 'Send email', pid: PID });
  await page5.click('#wfb-inspector .wfb-insert-btn'); // "Send email"'s inspector is still open from above
  await page5.waitForTimeout(80);
  const ancestorItem = await page5.evaluate((stepName) => {
    const items = [...document.querySelectorAll('.wfb-insert-menu-item')];
    const hit = items.find((el) => (el.querySelector('.wfb-insert-menu-item-secondary') || {}).textContent === stepName);
    return hit ? {
      primary: (hit.querySelector('.wfb-insert-menu-item-primary') || {}).textContent,
      secondary: (hit.querySelector('.wfb-insert-menu-item-secondary') || {}).textContent,
    } : null;
  }, 'triage-agent');
  (ancestorItem && ancestorItem.primary === "Tobin's result" && ancestorItem.secondary === 'triage-agent')
    ? ok(`the agent ancestor's option leads with the display name ("Tobin's result") and demotes the step name ("triage-agent") to a secondary line`)
    : fail(`expected {primary:"Tobin's result",secondary:"triage-agent"}, got ${JSON.stringify(ancestorItem)}`);
  // Deliberately NOT Escape here: index.html's global handler closes the
  // focused modal on Escape with no guard for this menu (same unguarded
  // conflict `_wfNodeMenuOpenFor`/`_wfPortPopover`/`_wfTriggerPopoverOpen`
  // already carry -- fixing that global handler is out of scope for the
  // Insert control). An outside click is the documented close path.
  await page5.mouse.click(vpBox5.x + 10, vpBox5.y + 10);
  await page5.waitForTimeout(80);
  (await page5.$('.wfb-insert-menu')) === null
    ? ok('the Insert menu closes on an outside click')
    : fail('the Insert menu stayed open after an outside click');

  // ── Approval: the consequence-framing description line (Action-standard). ─
  const apprPt = await emptyCanvasPoint(page5);
  await dragPaletteToolTo(page5, 'Approval gate', apprPt.x, apprPt.y);
  const allNodeNames5b = await page5.$$eval('.wfb-node', els => els.map(e => e.dataset.name));
  const apprName = allNodeNames5b.find(n => n !== waitName && n !== 'Send email' && n !== 'triage-agent');
  await openInspector(page5, apprName);
  const apprDesc = await page5.$eval('#wfb-inspector .wfb-action-desc', el => el.textContent).catch(() => '');
  /Parks the run and waits for a human/.test(apprDesc)
    ? ok(`the Approval card states what it does, Action-style: "${apprDesc}"`)
    : fail(`expected the Approval card to describe its consequence, got: "${apprDesc}"`);
  await closeInspector(page5);

  // ── Click/tap-to-add (Ron's own trap warning: "a toolbar item that only
  // responds to dragging reads as a broken button"). A plain click, no
  // pointer movement at all, on the Wait tool must still add a block, placed
  // clear of every existing card (three are already on the canvas from the
  // drags above). ──────────────────────────────────────────────────────────
  const c5PreNodes = await page5.evaluate(() => (window._wfEntry()._wf.def.nodes || []).map(n => ({ x: n.x, y: n.y })));
  await clickToolbarTool(page5, 'Wait');
  await page5.waitForTimeout(120);
  const c5PostNodes = await page5.evaluate(() => (window._wfEntry()._wf.def.nodes || []).map(n => ({ x: n.x, y: n.y })));
  c5PostNodes.length === c5PreNodes.length + 1
    ? ok(`a plain click on a toolbar tool (no drag) added a block — ${c5PreNodes.length} -> ${c5PostNodes.length} nodes`)
    : fail(`clicking a toolbar tool with no drag did not add a block: ${c5PreNodes.length} -> ${c5PostNodes.length} nodes`);
  // Checks the NEW card specifically against every OTHER card -- not every
  // pair on the canvas, which would also flag pre-existing cards placed by
  // the earlier drag-drop tests above landing close together (a real
  // possibility with `emptyCanvasPoint`'s screen-space scan, unrelated to
  // what THIS assertion is testing: does click-to-add avoid the cards that
  // were already there).
  const c5NewName = await page5.evaluate(() => (window._wfEntry()._wf.def.nodes || []).slice(-1)[0].name);
  const c5Overlap = await page5.evaluate((newName) => {
    const world = document.getElementById('wfb-world');
    const mine = world.querySelector(`.wfb-node[data-name="${newName}"]`);
    if (!mine) return { error: 'new node not found in DOM' };
    const a = { x: mine.offsetLeft, y: mine.offsetTop, w: mine.offsetWidth, h: mine.offsetHeight };
    const hit = [...world.querySelectorAll('.wfb-node, .wfb-trigger-box')]
      .filter((el) => el !== mine)
      .map((el) => ({ name: el.dataset.name || '__trigger__', x: el.offsetLeft, y: el.offsetTop, w: el.offsetWidth, h: el.offsetHeight }))
      .find((b) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y);
    return { mine: a, hit: hit || null };
  }, c5NewName);
  (c5NewName && !c5Overlap.hit && !c5Overlap.error)
    ? ok(`the click-placed block ("${c5NewName}", ${JSON.stringify(c5Overlap.mine)}) does not overlap any existing card`)
    : fail(`the click-placed block overlaps an existing card: ${JSON.stringify(c5Overlap)}`);

  const uncaught5 = page5Errors.filter(e => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught5.length) uncaught5.forEach((e) => fail('uncaught exception in the Wait/Action-narrowing flow: ' + e));
  await ctx5.close();

  // ── MC-871 stale-roster fix (Ron: renamed an agent while the workflow was
  // open, saw the old name after leaving and returning; asked for a refresh
  // button too). `floorBody6` is mutable so the SAME route can serve a
  // changed roster mid-test, standing in for claydo.js's real write — this
  // exercises the listener contract (`clayrune:characters-changed` ->
  // refetch -> patch) without needing the persona editor's own DOM. ─────────
  let floorReqCount6 = 0;
  let floorBody6 = FLOOR_JSON;
  const ctx6 = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page6 = await ctx6.newPage();
  const page6Errors = [];
  page6.on('pageerror', (e) => page6Errors.push(e.message || String(e)));
  await page6.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/local-auth/status') return route.fulfill({ status: 200, contentType: 'application/json', body: '{"configured":true}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/floor') { floorReqCount6++; return route.fulfill({ status: 200, contentType: 'application/json', body: floorBody6 }); }
    if (path.startsWith('/api/avatars/')) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1PX });
    if (path === '/api/workflows' && req.method() === 'GET') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/workflows`) return route.fulfill({ status: 200, contentType: 'application/json', body: '{"workflows":[]}' });
    // Unmocked, these abort forever and agent-log.js's rail poller retries
    // in an unbounded tight loop -- enough iterations across a section's
    // many DOM waits crashed the renderer outright (page5, deterministic).
    if (path === `/api/project/${PID}/agent/log`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/conversations`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    return route.abort();
  });
  await page6.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page6.waitForSelector('#projects-col .card', { timeout: 15000 });
  await newWorkflow(page6, PID);

  const vpBox6 = await (await page6.$('#wfb-canvas-viewport')).boundingBox();
  await dragPalettePersonTo(page6, 'Tobin', vpBox6.x + 150, vpBox6.y + 150);
  const renamedNodeName = await page6.$eval('.wfb-node', el => el.dataset.name);
  const nodeSel6 = `.wfb-node[data-name="${renamedNodeName}"]`;

  // Dirty the canvas and tag the live DOM element with a JS-only property (not
  // an attribute) — a full `_wfRender()` teardown-and-rebuild would replace
  // this exact node with a fresh one that never had it set, so the tag
  // surviving is proof the refresh patched in place instead of re-rendering.
  // MC-963: the drop auto-opened this node's inspector (_wfFocusPrompt) --
  // the prompt textarea lives there now, not on the canvas card.
  await page6.waitForSelector('#wfb-inspector.wfb-inspector-open', { timeout: 5000 });
  await page6.fill('#wfb-inspector .wfb-prompt', 'do not lose this');
  await closeInspector(page6);
  const before6 = await page6.evaluate((name) => {
    const el = document.querySelector(`.wfb-node[data-name="${CSS.escape(name)}"]`);
    el._smokeCanvasMarker = true;
    return {
      undoLen: (window._wfEntry()._wf._undo || []).length,
      dirty: (document.getElementById('wfb-save-stamp') || {}).textContent,
    };
  }, renamedNodeName);

  floorBody6 = JSON.stringify({ rooms: [], quiet: [],
    bench: BENCH.map(b => b.name === 'builder' ? { ...b, display: 'Tobin Renamed' } : b), counts: {} });
  const floorReqBeforeEvent = floorReqCount6;
  await page6.evaluate(() => window.dispatchEvent(new CustomEvent('clayrune:characters-changed',
    { detail: { scope: 'global', name: 'builder', action: 'rename' } })));
  await page6.waitForTimeout(150);

  (floorReqCount6 > floorReqBeforeEvent)
    ? ok('a rename broadcast (clayrune:characters-changed) made the builder refetch /api/floor')
    : fail('the characters-changed event did not trigger a bench refetch');

  const paletteNameAfter = await page6.$eval('.wfb-palette-person-name', el => el.textContent);
  paletteNameAfter === 'Tobin Renamed'
    ? ok('the palette shows the renamed display name with no reload')
    : fail(`expected the palette to show "Tobin Renamed", got "${paletteNameAfter}"`);

  const cardNameAfter = await page6.$eval(`${nodeSel6} .wfb-node-persona`, el => el.textContent);
  cardNameAfter === 'Tobin Renamed'
    ? ok('a card already on the canvas re-resolves the new display name via _wfBenchLookup')
    : fail(`expected the placed card to show "Tobin Renamed", got "${cardNameAfter}"`);

  const after6 = await page6.evaluate((name) => {
    const el = document.querySelector(`.wfb-node[data-name="${CSS.escape(name)}"]`);
    return {
      markerSurvived: !!(el && el._smokeCanvasMarker),
      undoLen: (window._wfEntry()._wf._undo || []).length,
      dirty: (document.getElementById('wfb-save-stamp') || {}).textContent,
    };
  }, renamedNodeName);
  after6.markerSurvived
    ? ok('the bench refresh patched the existing card in place — it did not tear down/rebuild the canvas')
    : fail('the placed node card was replaced by the bench refresh (canvas was reset)');
  after6.undoLen === before6.undoLen
    ? ok('the bench refresh pushed no undo step')
    : fail(`the bench refresh changed the undo stack length (${before6.undoLen} -> ${after6.undoLen})`);
  after6.dirty === before6.dirty
    ? ok('dirty/unsaved-changes state survived the bench refresh')
    : fail(`dirty state changed across the bench refresh: "${before6.dirty}" -> "${after6.dirty}"`);

  // ── The manual refresh button (Ron: "Is there a way to maybe add refresh
  // button?"). Calls the exported click handler twice back-to-back in one
  // browser-side tick (a real double-click risks Playwright's actionability
  // wait swallowing the second click once the first disables the button,
  // which would prove nothing) — the guard must still cap it at one fetch. ──
  floorBody6 = JSON.stringify({ rooms: [], quiet: [],
    bench: BENCH.map(b => b.name === 'builder' ? { ...b, display: 'Tobin Thrice' } : b), counts: {} });
  const floorReqBeforeClick = floorReqCount6;
  await page6.evaluate(() => { window._wfPaletteRefreshClick(); window._wfPaletteRefreshClick(); });
  await page6.waitForTimeout(150);
  (floorReqCount6 === floorReqBeforeClick + 1)
    ? ok(`the refresh button refetched /api/floor exactly once despite two back-to-back calls (${floorReqCount6 - floorReqBeforeClick})`)
    : fail(`expected exactly 1 refetch from the refresh button, got ${floorReqCount6 - floorReqBeforeClick}`);
  const paletteNameAfterClick = await page6.$eval('.wfb-palette-person-name', el => el.textContent);
  paletteNameAfterClick === 'Tobin Thrice'
    ? ok('the refresh button picked up the new roster data')
    : fail(`expected "Tobin Thrice" after clicking refresh, got "${paletteNameAfterClick}"`);

  const uncaught6 = page6Errors.filter(e => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught6.length) uncaught6.forEach((e) => fail('uncaught exception in the stale-roster refresh flow: ' + e));
  await ctx6.close();

  // ── Live run strip + Cancel (incident run-42a3f2aa, 2026-09-12): a run whose
  // completion never arrived sat `running` and blocked every future run, with
  // no route or control to end it. Opening the saved workflow must show the
  // live run, and Cancel must POST the human-only cancel route (a real browser
  // click carries Origin) and clear the strip, naming the agent left running.
  const WF7 = { id: 'wf-smoke-live', format: 2, name: 'Check US stocks', enabled: true,
    trigger: { type: 'manual' }, edges: [],
    nodes: [{ type: 'agent', name: 'us-stock-investor', project_id: PID, character: '', prompt: 'scan', x: 40, y: 40 }] };
  const RUN7 = { id: 'run-42a3f2aa', workflow_id: WF7.id, status: 'running',
    trigger: { type: 'manual', fired_at: '2026-09-12T21:42:58Z' }, created: '2026-09-12T21:42:58Z',
    steps: { 'us-stock-investor': { status: 'running', project_id: PID, session_id: '4e31ad33938b' } } };
  let runs7 = [RUN7];
  const cancelPosts7 = [];
  const ctx7 = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page7 = await ctx7.newPage();
  const page7Errors = [];
  page7.on('pageerror', (e) => page7Errors.push(e.message || String(e)));
  page7.on('dialog', (d) => d.accept());
  await page7.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/local-auth/status') return route.fulfill({ status: 200, contentType: 'application/json', body: '{"configured":true}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/floor') return route.fulfill({ status: 200, contentType: 'application/json', body: FLOOR_JSON });
    if (path === '/api/schedules') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path.startsWith('/api/avatars/')) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1PX });
    if (path === '/api/workflows' && req.method() === 'GET') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([WF7]) });
    if (path === `/api/workflows/${WF7.id}/runs`) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(runs7) });
    if (path === `/api/workflow-runs/${RUN7.id}/cancel` && req.method() === 'POST') {
      cancelPosts7.push(path);
      const cancelled = { ...RUN7, status: 'cancelled',
        left_running: [{ step: 'us-stock-investor', project_id: PID, session_id: '4e31ad33938b' }] };
      runs7 = [cancelled];
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, run: cancelled }) });
    }
    if (path === `/api/project/${PID}/workflows`) return route.fulfill({ status: 200, contentType: 'application/json', body: '{"workflows":[]}' });
    // Unmocked, these abort forever and agent-log.js's rail poller retries
    // in an unbounded tight loop -- enough iterations across a section's
    // many DOM waits crashed the renderer outright (page5, deterministic).
    if (path === `/api/project/${PID}/agent/log`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/conversations`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    return route.abort();
  });
  await page7.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page7.waitForSelector('#projects-col .card', { timeout: 15000 });
  await page7.evaluate(() => {
    window.__toasts = [];
    const orig = window.showToast;
    window.showToast = (msg, ms) => { window.__toasts.push(msg); if (orig) orig(msg, ms); };
  });
  await openWorkflowsTab(page7, PID);
  const strip7 = await page7.waitForSelector('.wfb-live-run', { timeout: 5000 }).catch(() => null);
  const stripText7 = strip7 ? await strip7.textContent() : '';
  (strip7 && /run-42a3f2aa/.test(stripText7) && /us-stock-investor/.test(stripText7))
    ? ok('opening a workflow with a live run shows the run strip naming the run and its in-flight step')
    : fail(`expected a live-run strip naming run-42a3f2aa / us-stock-investor, got: ${JSON.stringify(stripText7)}`);

  // MC-946: the running step's CANVAS NODE must carry the live-running class
  // + badge, not just the text strip above it.
  const nodeLiveState7 = await page7.evaluate(() => {
    const el = document.querySelector('.wfb-node[data-name="us-stock-investor"]');
    return el ? { hasRunningClass: el.classList.contains('wfb-node-live-running'),
      hasBadge: !!el.querySelector('.wfb-node-live-badge-running') } : null;
  });
  (nodeLiveState7 && nodeLiveState7.hasRunningClass && nodeLiveState7.hasBadge)
    ? ok('the running step\'s canvas node carries wfb-node-live-running + its badge')
    : fail(`expected the us-stock-investor node to carry wfb-node-live-running + its badge, got: ${JSON.stringify(nodeLiveState7)}`);

  // MC-946: the step name in the strip is a click target that pans the
  // canvas to the running node (_wfFocusLiveNode).
  const viewportBefore7 = await page7.evaluate(() => ({ ...window._wfEntry()._wf.viewport }));
  const stepLink7 = await page7.$('.wfb-live-run-step-link');
  stepLink7
    ? ok('the strip renders the in-flight step name as a clickable link')
    : fail('expected a .wfb-live-run-step-link in the live-run strip');
  if (stepLink7) {
    await stepLink7.click();
    await page7.waitForTimeout(50);
    const viewportAfter7 = await page7.evaluate(() => ({ ...window._wfEntry()._wf.viewport }));
    (viewportAfter7.x !== viewportBefore7.x || viewportAfter7.y !== viewportBefore7.y)
      ? ok('clicking the step name panned the canvas viewport to the running node')
      : fail(`expected the viewport to move after clicking the step link, stayed at ${JSON.stringify(viewportAfter7)}`);
  }

  if (strip7) {
    await page7.click('.wfb-live-run-cancel');
    // MC-995: cancel POSTs /api/workflow-runs/<id>/cancel, gated too -- answer
    // the dashboard-passcode modal (after the native confirm dialog) before
    // the request actually fires.
    await answerHumanProofModalIfShown(page7);
    await page7.waitForTimeout(250);
    cancelPosts7.length === 1
      ? ok('Cancel run POSTed /api/workflow-runs/<id>/cancel exactly once (after the confirm)')
      : fail(`expected exactly 1 cancel POST, got ${cancelPosts7.length}`);
    (await page7.$('.wfb-live-run'))
      ? fail('the live-run strip is still showing after a successful cancel')
      : ok('the live-run strip clears after a successful cancel');
    const toasts7 = await page7.evaluate(() => window.__toasts || []);
    toasts7.some(t => /cancelled/i.test(t) && /4e31ad33938b/.test(t))
      ? ok('the cancel toast names the agent session that is still running')
      : fail(`expected a toast naming session 4e31ad33938b, got ${JSON.stringify(toasts7)}`);
    const nodeAfterCancel7 = await page7.evaluate(() => {
      const el = document.querySelector('.wfb-node[data-name="us-stock-investor"]');
      return el ? el.className : null;
    });
    (nodeAfterCancel7 && !/wfb-node-live-/.test(nodeAfterCancel7))
      ? ok('the canvas node drops its live-run styling once the run is cancelled')
      : fail(`expected no live-run class on the node after cancel, got className="${nodeAfterCancel7}"`);
  }
  const uncaught7 = page7Errors.filter(e => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught7.length) uncaught7.forEach((e) => fail('uncaught exception in the live-run cancel flow: ' + e));
  await ctx7.close();

  // ── Cancel against a server WITHOUT the cancel route (incident run-5de9dfa5,
  // 2026-09-14): the live server predated the route, so POST .../cancel got
  // Flask's HTML 404. The UI read any 404 as "already ended", cleared the
  // strip and said so, while the run stayed live and kept refusing new runs.
  // A 404 with no JSON body is not proof of anything: the strip must stay and
  // the toast must say the cancel FAILED.
  const RUN8 = { ...RUN7, id: 'run-5de9dfa5',
    steps: { 'us-stock-investor': { status: 'running', project_id: PID, session_id: '3ee1e9fa0865' } } };
  const ctx8 = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page8 = await ctx8.newPage();
  const page8Errors = [];
  page8.on('pageerror', (e) => page8Errors.push(e.message || String(e)));
  page8.on('dialog', (d) => d.accept());
  await page8.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/local-auth/status') return route.fulfill({ status: 200, contentType: 'application/json', body: '{"configured":true}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/floor') return route.fulfill({ status: 200, contentType: 'application/json', body: FLOOR_JSON });
    if (path === '/api/schedules') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path.startsWith('/api/avatars/')) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1PX });
    if (path === '/api/workflows' && req.method() === 'GET') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([WF7]) });
    if (path === `/api/workflows/${WF7.id}/runs`) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([RUN8]) });
    if (path === `/api/workflow-runs/${RUN8.id}/cancel` && req.method() === 'POST') {
      return route.fulfill({ status: 404, contentType: 'text/html; charset=utf-8', body: '<!doctype html><title>404 Not Found</title><h1>Not Found</h1>' });
    }
    if (path === `/api/project/${PID}/workflows`) return route.fulfill({ status: 200, contentType: 'application/json', body: '{"workflows":[]}' });
    // Unmocked, these abort forever and agent-log.js's rail poller retries
    // in an unbounded tight loop -- enough iterations across a section's
    // many DOM waits crashed the renderer outright (page5, deterministic).
    if (path === `/api/project/${PID}/agent/log`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/conversations`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    return route.abort();
  });
  await page8.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page8.waitForSelector('#projects-col .card', { timeout: 15000 });
  await page8.evaluate(() => {
    window.__toasts = [];
    const orig = window.showToast;
    window.showToast = (msg, ms) => { window.__toasts.push(msg); if (orig) orig(msg, ms); };
  });
  await openWorkflowsTab(page8, PID);
  const strip8 = await page8.waitForSelector('.wfb-live-run', { timeout: 5000 }).catch(() => null);
  if (!strip8) {
    fail('expected the live-run strip for run-5de9dfa5 before cancelling');
  } else {
    await page8.click('.wfb-live-run-cancel');
    await answerHumanProofModalIfShown(page8);
    await page8.waitForTimeout(250);
    (await page8.$('.wfb-live-run'))
      ? ok('a cancel that hit a missing route (HTML 404) leaves the live-run strip up')
      : fail('the live-run strip cleared although the cancel route returned an HTML 404 -- the run is still live');
    const toasts8 = await page8.evaluate(() => window.__toasts || []);
    (toasts8.some(t => /cancel failed/i.test(t)) && !toasts8.some(t => /already ended/i.test(t)))
      ? ok('the toast says the cancel failed, not that the run had already ended')
      : fail(`expected a "Cancel failed" toast and no "already ended", got ${JSON.stringify(toasts8)}`);
  }
  const uncaught8 = page8Errors.filter(e => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught8.length) uncaught8.forEach((e) => fail('uncaught exception in the missing-cancel-route flow: ' + e));
  await ctx8.close();

  // ── MC-963 explicit coverage (Dave's review of a34cae6): the earlier
  // cases above exercise the box view and inspector implicitly (every case
  // that used to type into a card now goes through openInspector/
  // closeInspector) but never ASSERT the box-view/inspector-split behaviour
  // itself. Five explicit checks: (1) the box carries no editable field,
  // (2) both the head dblclick and the "..." menu's Edit open the SAME
  // node's inspector without disturbing the canvas, (3) an inspector edit
  // survives a real save + reopen round trip read back from the DOM, (4)
  // the mobile inspector is a full-width bottom sheet that opens/closes on
  // touch, (5) a validation error shows as a badge on the offending box. ──
  const ctx9 = await browser.newContext({ viewport: { width: 1280, height: 900 }, hasTouch: true });
  const page9 = await ctx9.newPage();
  const page9Errors = [];
  page9.on('pageerror', (e) => page9Errors.push(e.message || String(e)));
  page9.on('dialog', (d) => d.accept());
  let savedWorkflows9 = [];
  await page9.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/local-auth/status') return route.fulfill({ status: 200, contentType: 'application/json', body: '{"configured":true}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/floor') return route.fulfill({ status: 200, contentType: 'application/json', body: FLOOR_JSON });
    if (path.startsWith('/api/avatars/')) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1PX });
    if (path === '/api/workflows' && req.method() === 'GET') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(savedWorkflows9) });
    if (path === `/api/project/${PID}/workflows`) return route.fulfill({ status: 200, contentType: 'application/json', body: '{"workflows":[]}' });
    // Unmocked, these abort forever and agent-log.js's rail poller retries
    // in an unbounded tight loop -- enough iterations across a section's
    // many DOM waits crashed the renderer outright (page5, deterministic).
    if (path === `/api/project/${PID}/agent/log`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/conversations`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/workflows' && req.method() === 'POST') {
      const body = JSON.parse(req.postData() || '{}');
      const saved = { ...body, id: 'wf-smoke9', format: 2, created: '2026-09-27T00:00:00Z', updated: '2026-09-27T00:00:00Z' };
      savedWorkflows9 = [saved];
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, workflow: saved }) });
    }
    if (path === '/api/workflows/wf-smoke9' && req.method() === 'PUT') {
      const body = JSON.parse(req.postData() || '{}');
      const saved = { ...body, id: 'wf-smoke9', format: 2 };
      savedWorkflows9 = [saved];
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, workflow: saved }) });
    }
    if (path === '/api/workflows/wf-smoke9/runs') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/workflows/wf-smoke9/run' && req.method() === 'POST') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: false, error: 'blocked' }) });
    if (path.startsWith('/api/schedules')) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    return route.abort();
  });
  await page9.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page9.waitForSelector('#projects-col .card', { timeout: 15000 });
  await newWorkflow(page9, PID);

  const vpBox9 = await (await page9.$('#wfb-canvas-viewport')).boundingBox();
  await dragPalettePersonTo(page9, 'Tobin', vpBox9.x + 150, vpBox9.y + 150);
  const nodeName9 = await page9.$eval('.wfb-node', el => el.dataset.name);
  // The drop auto-opens the inspector (_wfFocusPrompt) -- close it so check 1
  // inspects the box in its normal, closed resting state.
  await page9.waitForSelector('#wfb-inspector.wfb-inspector-open', { timeout: 5000 });
  await closeInspector(page9);

  // 1) Compact box: no textarea/select/input anywhere on the canvas node,
  // and it shows a non-empty title plus a non-empty one-line summary.
  const box9 = await page9.$eval(`.wfb-node[data-name="${nodeName9}"]`, el => ({
    fieldCount: el.querySelectorAll('textarea, select, input').length,
    title: (el.querySelector('.wfb-node-title') || {}).textContent || '',
    summary: (el.querySelector('.wfb-node-summary') || {}).textContent || '',
  }));
  (box9.fieldCount === 0 && box9.title.trim().length > 0 && box9.summary.trim().length > 0)
    ? ok(`the canvas box carries no editable field (0 textarea/select/input) and shows a title ("${box9.title.trim()}") + summary ("${box9.summary.trim()}")`)
    : fail(`expected 0 fields plus non-empty title/summary on the box, got ${JSON.stringify(box9)}`);

  // 2) Inspector: both the head dblclick AND the "..." menu's Edit open the
  // SAME node's inspector, and closing it leaves the canvas unchanged.
  const nodeCountBefore9 = await page9.$$eval('.wfb-node', els => els.length);
  await page9.dblclick(`.wfb-node[data-name="${nodeName9}"] .wfb-node-head`);
  await page9.waitForSelector('#wfb-inspector.wfb-inspector-open', { timeout: 5000 });
  const inspectorNameDbl9 = await page9.$eval('#wfb-inspector .wfb-node-own', el => el.dataset.name);
  await closeInspector(page9);
  const nodeCountAfterDbl9 = await page9.$$eval('.wfb-node', els => els.length);
  (inspectorNameDbl9 === nodeName9 && nodeCountAfterDbl9 === nodeCountBefore9)
    ? ok(`double-clicking the box head opened the inspector for "${nodeName9}" and closing it left the canvas at ${nodeCountAfterDbl9} node(s), unchanged`)
    : fail(`expected the dblclick to open ${nodeName9}'s inspector and leave ${nodeCountBefore9} node(s), got inspector-for="${inspectorNameDbl9}" count=${nodeCountAfterDbl9}`);

  await page9.click(`.wfb-node[data-name="${nodeName9}"] .wfb-node-menu-btn`);
  await page9.waitForSelector('#wfb-node-menu', { timeout: 3000 });
  await clickMenuItem(page9, 'Edit');
  await page9.waitForSelector('#wfb-inspector.wfb-inspector-open', { timeout: 5000 });
  const inspectorNameMenu9 = await page9.$eval('#wfb-inspector .wfb-node-own', el => el.dataset.name);
  await closeInspector(page9);
  const nodeCountAfterMenu9 = await page9.$$eval('.wfb-node', els => els.length);
  (inspectorNameMenu9 === nodeName9 && nodeCountAfterMenu9 === nodeCountBefore9)
    ? ok(`the "..." menu's Edit opened the inspector for "${nodeName9}" too, and closing it left the canvas unchanged (${nodeCountAfterMenu9} node(s))`)
    : fail(`expected the menu's Edit to open ${nodeName9}'s inspector and leave ${nodeCountBefore9} node(s), got inspector-for="${inspectorNameMenu9}" count=${nodeCountAfterMenu9}`);

  // 3) Persist: a prompt edit made in the inspector survives a real save +
  // reopen round trip -- read back from the reopened inspector's DOM, not
  // the in-memory model (a stale render could show the right JS state on
  // top of the wrong DOM).
  await openInspector(page9, nodeName9);
  await setValue(page9, '#wfb-inspector .wfb-prompt', 'Persisted prompt text, MC-963 check 3.');
  await closeInspector(page9);
  await setValue(page9, '#wfb-name', 'MC-963 check 3');
  await page9.evaluate(() => { window._wfSave(); });
  await answerHumanProofModalIfShown(page9);
  await page9.waitForTimeout(200);
  (savedWorkflows9.length === 1)
    ? ok('the workflow POSTed to the server on Save (check 3 setup)')
    : fail(`expected exactly 1 saved workflow after Save, got ${savedWorkflows9.length}`);
  await page9.evaluate(({ id, pid }) => { window.openWorkflowBuilder(id, pid); }, { id: 'wf-smoke9', pid: PID });
  await page9.waitForSelector('#wfb-canvas-viewport', { timeout: 5000 });
  const reopenedNodeName9 = await page9.$eval('.wfb-node', el => el.dataset.name);
  await openInspector(page9, reopenedNodeName9);
  // A non-empty, valid prompt reopens COLLAPSED by default (st.promptOpen is
  // per-session, fresh on this reload) -- open it if needed before reading.
  if (!(await page9.$('#wfb-inspector .wfb-prompt'))) {
    await page9.click('#wfb-inspector .wfb-prompt-toggle');
    await page9.waitForSelector('#wfb-inspector .wfb-prompt', { timeout: 3000 });
  }
  const reopenedPrompt9 = await page9.$eval('#wfb-inspector .wfb-prompt', el => el.value);
  reopenedPrompt9 === 'Persisted prompt text, MC-963 check 3.'
    ? ok('the prompt edit survived a save + reopen round trip, read back from the reopened inspector\'s DOM')
    : fail(`expected the reopened inspector's prompt to read "Persisted prompt text, MC-963 check 3.", got "${reopenedPrompt9}"`);
  await closeInspector(page9);

  // 4) Validation badge: a node with a validation error shows
  // .wfb-node-validation-badge on its box (Run-now populates st.runErrors
  // via the same client-side validator Save's whole-form guard reuses).
  await openInspector(page9, reopenedNodeName9);
  await setValue(page9, '#wfb-inspector .wfb-prompt', '');
  await closeInspector(page9);
  await page9.evaluate(() => window._wfRunNow());
  await page9.waitForTimeout(150);
  const badge9 = await page9.$eval(`.wfb-node[data-name="${reopenedNodeName9}"]`, el => ({
    hasBadge: !!el.querySelector('.wfb-node-validation-badge'),
    title: (el.querySelector('.wfb-node-validation-badge') || {}).title || null,
  })).catch(() => ({ hasBadge: false, title: null }));
  (badge9.hasBadge && /prompt/i.test(badge9.title || ''))
    ? ok(`an empty prompt (a validation error) shows .wfb-node-validation-badge on the box, title="${badge9.title}"`)
    : fail(`expected .wfb-node-validation-badge naming the missing prompt on the box, got ${JSON.stringify(badge9)}`);

  const uncaught9 = page9Errors.filter(e => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught9.length) uncaught9.forEach((e) => fail('uncaught exception in the MC-963 explicit-coverage flow: ' + e));
  await ctx9.close();

  // 5) Mobile: at <=960px the inspector renders as a full-width bottom
  // sheet, and it can be opened and closed by touch. A FRESH mobile context
  // (same pattern as `mctx` above), not a mid-session viewport resize of the
  // desktop context above -- the workflow builder lives inside a
  // `.modal-window` positioned by `centerModalElement` with inline
  // left/top computed against the viewport size AT OPEN TIME, so resizing
  // an already-open desktop modal leaves it stranded at its old desktop
  // position/size instead of exercising the real mobile-open layout.
  const ctx9m = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page9m = await ctx9m.newPage();
  const page9mErrors = [];
  page9m.on('pageerror', (e) => page9mErrors.push(e.message || String(e)));
  await page9m.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/local-auth/status') return route.fulfill({ status: 200, contentType: 'application/json', body: '{"configured":true}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/floor') return route.fulfill({ status: 200, contentType: 'application/json', body: FLOOR_JSON });
    if (path.startsWith('/api/avatars/')) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1PX });
    if (path === '/api/workflows' && req.method() === 'GET') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(savedWorkflows9) });
    if (path === `/api/project/${PID}/workflows`) return route.fulfill({ status: 200, contentType: 'application/json', body: '{"workflows":[]}' });
    // Unmocked, these abort forever and agent-log.js's rail poller retries
    // in an unbounded tight loop -- enough iterations across a section's
    // many DOM waits crashed the renderer outright (page5, deterministic).
    if (path === `/api/project/${PID}/agent/log`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/conversations`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/workflows/wf-smoke9/runs') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path.startsWith('/api/schedules')) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    return route.abort();
  });
  await page9m.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page9m.waitForSelector('#projects-col .card, .mc-chat-row', { timeout: 15000 });
  await openWorkflowsTab(page9m, PID);
  await page9m.evaluate(({ id, pid }) => { window.openWorkflowBuilder(id, pid); }, { id: 'wf-smoke9', pid: PID });
  await page9m.waitForSelector('#wfb-canvas-viewport', { timeout: 5000 });
  const mobileNodeName9 = await page9m.$eval('.wfb-node', el => el.dataset.name);

  await page9m.tap(`.wfb-node[data-name="${mobileNodeName9}"] .wfb-node-body`);
  await page9m.waitForSelector('#wfb-inspector.wfb-inspector-open', { timeout: 5000 });
  const sheetBox9 = await page9m.$eval('#wfb-inspector', el => el.getBoundingClientRect().toJSON());
  const viewport9m = page9m.viewportSize();
  const looksLikeSheet9 = Math.abs(sheetBox9.right - viewport9m.width) < 2 && Math.abs(sheetBox9.left) < 2
    && Math.abs(sheetBox9.bottom - viewport9m.height) < 2 && sheetBox9.width >= viewport9m.width - 2;
  looksLikeSheet9
    ? ok(`at 390px the inspector renders as a full-width, bottom-anchored sheet (rect ${JSON.stringify(sheetBox9)}, viewport ${viewport9m.width}x${viewport9m.height})`)
    : fail(`expected a full-width bottom-anchored inspector at 390px, got rect ${JSON.stringify(sheetBox9)} viewport ${JSON.stringify(viewport9m)}`);
  await page9m.tap('.wfb-inspector-close');
  await page9m.waitForTimeout(120);
  const inspectorOpenAfterTap9 = await page9m.$('#wfb-inspector.wfb-inspector-open');
  inspectorOpenAfterTap9 === null
    ? ok('tapping the inspector\'s close control closes the mobile bottom sheet')
    : fail('the inspector bottom sheet is still open after tapping its close control');

  const uncaught9m = page9mErrors.filter(e => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught9m.length) uncaught9m.forEach((e) => fail('uncaught exception in the MC-963 mobile-inspector flow: ' + e));
  await ctx9m.close();

  // ── MC-962 (backlog 78d23814): agent-authored DRAFT (POST /api/workflows/
  // draft) landing on the canvas UNSAVED + disabled, and read-only CHECK
  // WORKFLOW (POST /api/workflows/review) findings pinning to their node as
  // a badge distinct from the validation badge, with a dismissible
  // unassigned list and stale-on-edit clearing. A real model call is not
  // acceptable in a smoke (file header) -- both routes are stubbed. ────────
  const ctx10 = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page10 = await ctx10.newPage();
  const page10Errors = [];
  page10.on('pageerror', (e) => page10Errors.push(e.message || String(e)));
  const workflowPosts10 = [];
  let draftResult10 = null;  // set per-case before clicking "Describe it"
  let draftDelayMs10 = 0;    // >0 to catch the mid-flight "Drafting…" state
  let reviewResult10 = null; // set per-case before clicking "Check workflow"
  await page10.route('**/*', async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/local-auth/status') return route.fulfill({ status: 200, contentType: 'application/json', body: '{"configured":true}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/floor') return route.fulfill({ status: 200, contentType: 'application/json', body: FLOOR_JSON });
    if (path.startsWith('/api/avatars/')) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1PX });
    if (path === '/api/workflows' && req.method() === 'GET') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/workflows`) return route.fulfill({ status: 200, contentType: 'application/json', body: '{"workflows":[]}' });
    // Unmocked, these abort forever and agent-log.js's rail poller retries
    // in an unbounded tight loop -- enough iterations across a section's
    // many DOM waits crashed the renderer outright (page5, deterministic).
    if (path === `/api/project/${PID}/agent/log`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/conversations`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/workflows' && req.method() === 'POST') {
      const body = JSON.parse(req.postData() || '{}');
      workflowPosts10.push(body);
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        ok: true, workflow: { ...body, id: 'wf-smoke10', format: 2, created: '2026-09-27T00:00:00Z', updated: '2026-09-27T00:00:00Z' },
      }) });
    }
    if (path === '/api/workflows/draft' && req.method() === 'POST') {
      if (draftDelayMs10) await new Promise((r) => setTimeout(r, draftDelayMs10));
      const result = draftResult10 || { ok: false, error: 'draft_call_failed' };
      return route.fulfill({ status: result.ok ? 200 : 502, contentType: 'application/json', body: JSON.stringify(result) });
    }
    if (path === '/api/workflows/review' && req.method() === 'POST') {
      const result = reviewResult10 || { ok: true, findings: [] };
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(result) });
    }
    return route.abort();
  });
  await page10.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page10.waitForSelector('#projects-col .card', { timeout: 15000 });
  await page10.evaluate(() => {
    window.__toasts = [];
    const orig = window.showToast;
    window.showToast = (msg, ms) => { window.__toasts.push(msg); if (orig) orig(msg, ms); };
  });
  await newWorkflow(page10, PID);

  const describeBoxPresent10 = !!(await page10.$('.wfb-describe-box'));
  describeBoxPresent10
    ? ok('a brand-new workflow shows the "Describe what you need" box on the empty canvas')
    : fail('expected .wfb-describe-box on a brand-new, empty-canvas workflow');

  // MC-962 regression: the describe box used to be absolutely positioned
  // INSIDE the canvas at the exact spot a brand-new trigger card spawns
  // (40,40), so it rendered on top of the trigger — Ron's screenshot showed
  // the trigger's port dot poking out from underneath it. Bounding-box
  // intersection is the direct check for that, not just presence of both.
  const overlap10 = await page10.evaluate(() => {
    const rectOf = (sel) => { const el = document.querySelector(sel); return el ? el.getBoundingClientRect() : null; };
    const d = rectOf('.wfb-describe-box'), t = rectOf('.wfb-trigger-box');
    if (!d || !t) return { d, t, intersects: null };
    const intersects = d.left < t.right && d.right > t.left && d.top < t.bottom && d.bottom > t.top;
    return { d: { left: d.left, top: d.top, right: d.right, bottom: d.bottom },
             t: { left: t.left, top: t.top, right: t.right, bottom: t.bottom }, intersects };
  });
  overlap10.intersects === false
    ? ok(`the describe box does not overlap the trigger card at desktop width (describe=${JSON.stringify(overlap10.d)}, trigger=${JSON.stringify(overlap10.t)})`)
    : fail(`the describe box overlaps the trigger card at desktop width: ${JSON.stringify(overlap10)}`);

  // MC-962 regression: "Describe it" rendered as an unstyled browser-default
  // button because it carried `btn-sched-save`, a class only ever styled
  // scoped under `.schedule-form` (app.css) -- this widget has no such
  // ancestor. `btn-add` is the app's own unscoped "primary action" button
  // class, already used elsewhere at this same altitude (e.g. the backlog
  // add button).
  const describeBtnClass10 = await page10.$eval('.wfb-describe-row button', (el) => el.className);
  /\bbtn-add\b/.test(describeBtnClass10)
    ? ok(`the "Describe it" button carries the app's real button class (${describeBtnClass10})`)
    : fail(`expected the "Describe it" button to carry btn-add, got class="${describeBtnClass10}"`);

  // MC-962 follow-up: the label/textarea/button/hint used to stack across four
  // rows (~184px tall at this width). Now the input and "Describe it" button
  // must share ONE row (tops within a few px of each other, allowing for
  // border/line-height rounding) and the whole panel (box + hint) must stay
  // under a fixed ceiling well below the old stacked height.
  const row10 = await page10.evaluate(() => {
    const input = document.getElementById('wfb-describe-input');
    const btn = document.querySelector('.wfb-describe-row .btn-add');
    if (!input || !btn) return null;
    const ir = input.getBoundingClientRect(), br = btn.getBoundingClientRect();
    return { inputTop: ir.top, btnTop: br.top };
  });
  (row10 && Math.abs(row10.inputTop - row10.btnTop) <= 4)
    ? ok(`the describe input and "Describe it" button share one row at desktop width (${JSON.stringify(row10)})`)
    : fail(`expected the describe input and button on the same row at desktop width, got ${JSON.stringify(row10)}`);
  const panelHeight10 = (await page10.$eval('.wfb-canvas-empty', (el) => el.getBoundingClientRect().height));
  panelHeight10 < 100
    ? ok(`the describe panel (box + hint) fits under the one-row ceiling at desktop width (${panelHeight10}px)`)
    : fail(`expected the describe panel under 100px at desktop width, got ${panelHeight10}px`);

  // Empty description: refused client-side, no request fired.
  await page10.click('.wfb-describe-row .btn-add');
  await page10.waitForTimeout(80);
  const toastsEmpty10 = await page10.evaluate(() => window.__toasts || []);
  (workflowPosts10.length === 0 && toastsEmpty10.some(t => /describe what you need/i.test(t)))
    ? ok('clicking "Describe it" with no text is refused client-side with a visible toast, no request fired')
    : fail(`expected a client-side refusal toast and no request, got toasts=${JSON.stringify(toastsEmpty10)}`);

  // ── Case 1: the draft call fails (engine unavailable) -- must surface
  // visibly, never silently, and show a loading state while in flight. ─────
  draftResult10 = { ok: false, error: 'draft_call_failed' };
  draftDelayMs10 = 250;
  await setValue(page10, '#wfb-describe-input', 'Every morning, triage new backlog items.');
  await page10.click('.wfb-describe-row .btn-add');
  // MC-995: drafting is gated too (humanProofFetch shows the modal and holds
  // the promise BEFORE the real request fires) -- the simulated server delay
  // below doesn't start counting until the modal is answered.
  await answerHumanProofModalIfShown(page10);
  await page10.waitForTimeout(80); // mid-flight: the 250ms server delay hasn't resolved yet
  const midFlight10 = await page10.evaluate(() => ({
    textareaDisabled: (document.getElementById('wfb-describe-input') || {}).disabled,
    buttonText: (document.querySelector('.wfb-describe-row .btn-add') || {}).textContent,
  }));
  (midFlight10.textareaDisabled === true && /Drafting/.test(midFlight10.buttonText || ''))
    ? ok(`a loading state shows while the draft request is in flight (button reads "${(midFlight10.buttonText || '').trim()}", textarea disabled)`)
    : fail(`expected a "Drafting…" loading state mid-flight, got ${JSON.stringify(midFlight10)}`);
  await page10.waitForTimeout(300); // let the delayed response land
  const afterFailedDraft10 = await page10.evaluate(() => ({
    nodeCount: document.querySelectorAll('.wfb-node').length,
    errorText: (document.querySelector('.wfb-describe-error') || {}).textContent || null,
    drafting: window._wfEntry()._wf.drafting,
  }));
  (afterFailedDraft10.nodeCount === 0 && afterFailedDraft10.drafting === false && /engine is unavailable/i.test(afterFailedDraft10.errorText || ''))
    ? ok(`a failed draft call surfaces a real, visible error ("${afterFailedDraft10.errorText}") and never fails silently -- canvas still empty, loading state cleared`)
    : fail(`expected a visible engine-unavailable error, drafting=false, and 0 nodes, got ${JSON.stringify(afterFailedDraft10)}`);
  draftDelayMs10 = 0;

  // ── Case 2: the draft succeeds -- lands on the canvas UNSAVED and
  // DISABLED (dirty state; nothing enables/schedules/runs it -- the standing
  // position that only a human activates a workflow). The stubbed
  // definition sets enabled:true on purpose, to prove the client re-asserts
  // false rather than trusting the server response verbatim. ──────────────
  draftResult10 = {
    ok: true, valid: true, errors: [],
    definition: {
      name: 'Morning triage', description: 'Triage new backlog items every morning.',
      trigger: { type: 'manual' }, enabled: true,
      nodes: [{ name: 'triage', type: 'agent', character: 'global:builder', project_id: PID,
                prompt: 'Triage anything new in the backlog and summarize it.' }],
      edges: [],
    },
  };
  await page10.click('.wfb-describe-row .btn-add');
  await answerHumanProofModalIfShown(page10);
  await page10.waitForTimeout(200);
  const afterDraft10 = await page10.evaluate(() => {
    const st = window._wfEntry()._wf;
    return { nodeCount: document.querySelectorAll('.wfb-node').length, dirty: st.dirty, drafting: st.drafting,
             enabled: st.def.enabled, workflowId: st.workflowId,
             describeBoxGone: !document.querySelector('.wfb-describe-box') };
  });
  (afterDraft10.nodeCount === 1 && afterDraft10.dirty === true && afterDraft10.drafting === false
      && afterDraft10.enabled === false && !afterDraft10.workflowId && afterDraft10.describeBoxGone)
    ? ok('a successful draft loads onto the canvas as an unsaved (dirty), disabled workflow with no workflowId (client re-asserts enabled=false even though the stub sent true) -- describe box gone now that it has nodes')
    : fail(`expected 1 node, dirty=true, drafting=false, enabled=false, no workflowId, describe box gone; got ${JSON.stringify(afterDraft10)}`);

  // ── Save persists the draft -- still gated the ordinary way; Save is the
  // human action the standing position requires. ──────────────────────────
  await page10.click('.btn-sched-save');
  await answerHumanProofModalIfShown(page10);
  await page10.waitForTimeout(200);
  const savedBody10 = workflowPosts10[workflowPosts10.length - 1] || {};
  const afterSave10 = await page10.evaluate(() => {
    const st = window._wfEntry()._wf;
    return { dirty: st.dirty, workflowId: st.workflowId };
  });
  (workflowPosts10.length === 1 && savedBody10.enabled === false && afterSave10.workflowId === 'wf-smoke10' && afterSave10.dirty === false)
    ? ok('Save POSTed the drafted workflow exactly once, still enabled=false, and the canvas now tracks the persisted workflowId')
    : fail(`expected exactly 1 POST with enabled=false and workflowId wf-smoke10 after Save, got posts=${workflowPosts10.length} body.enabled=${savedBody10.enabled} after=${JSON.stringify(afterSave10)}`);

  // ── Check workflow: findings pin to their node as a badge distinct from
  // the validation badge; whole-workflow findings land in a dismissible
  // list; full text shows in the inspector. ───────────────────────────────
  reviewResult10 = {
    ok: true, valid: false,
    findings: [
      { node: 'triage', severity: 'warning', message: 'The prompt is vague about what "triage" means here.', suggestion: 'Name the specific fields to check.' },
      { node: null, severity: 'error', message: 'This workflow has no failure handling for the agent step.', suggestion: null },
    ],
  };
  await clickToolbarBtn(page10, 'Check workflow');
  await page10.waitForTimeout(200);
  const badge10 = await page10.$eval('.wfb-node[data-name="triage"]', el => ({
    hasReviewBadge: !!el.querySelector('.wfb-node-review-badge'),
    hasValidationBadge: !!el.querySelector('.wfb-node-validation-badge'),
    title: (el.querySelector('.wfb-node-review-badge') || {}).title || null,
  })).catch(() => ({}));
  (badge10.hasReviewBadge && !badge10.hasValidationBadge && /vague/i.test(badge10.title || ''))
    ? ok(`the pinned finding shows as a distinct .wfb-node-review-badge on "triage" (title="${badge10.title}"), not the validation-error badge`)
    : fail(`expected a distinct review badge on triage naming the vague-prompt finding, got ${JSON.stringify(badge10)}`);

  await openInspector(page10, 'triage');
  const inspectorFinding10 = await page10.evaluate(() => (document.querySelector('#wfb-inspector .wfb-node-review-finding') || {}).textContent || '');
  (/vague/i.test(inspectorFinding10) && /Name the specific fields/i.test(inspectorFinding10))
    ? ok('the full finding text (message + suggestion) shows in that node\'s inspector')
    : fail(`expected the full finding text + suggestion in the inspector, got "${inspectorFinding10}"`);

  const unassigned10 = await page10.$$eval('#wfb-review-unassigned .wfb-review-unassigned-item', els => els.map(e => e.textContent));
  (unassigned10.length === 1 && /no failure handling/i.test(unassigned10[0]))
    ? ok(`the whole-workflow finding (no matching node) landed in the dismissible unassigned list: "${unassigned10[0].trim()}"`)
    : fail(`expected 1 unassigned finding naming the missing failure handling, got ${JSON.stringify(unassigned10)}`);

  await page10.click('.wfb-review-unassigned-dismiss');
  await page10.waitForTimeout(80);
  const unassignedAfterDismiss10 = await page10.$$eval('#wfb-review-unassigned .wfb-review-unassigned-item', els => els.length).catch(() => 0);
  unassignedAfterDismiss10 === 0
    ? ok('dismissing the unassigned finding removes it from the list')
    : fail(`expected the unassigned list empty after dismiss, got ${unassignedAfterDismiss10} item(s) left`);

  // ── A later edit clears stale findings -- both the pinned badge AND any
  // remaining unassigned findings go together (`_wfClearStaleReviewFindings`
  // keys off one snapshot of the whole def, not per-finding). ─────────────
  // A non-empty, valid prompt reopens COLLAPSED by default -- expand it first
  // (same case as MC-963 check 3's reopen, above).
  if (!(await page10.$('#wfb-inspector .wfb-prompt'))) {
    await page10.click('#wfb-inspector .wfb-prompt-toggle');
    await page10.waitForSelector('#wfb-inspector .wfb-prompt', { timeout: 3000 });
  }
  await setValue(page10, '#wfb-inspector .wfb-prompt', 'Triage anything new: severity, owner, and a one-line summary.');
  await closeInspector(page10); // _wfCloseInspector -> _wfSyncDomToModel -> _wfRender -> clears stale findings
  const badgeAfterEdit10 = await page10.$eval('.wfb-node[data-name="triage"]', el => !!el.querySelector('.wfb-node-review-badge'));
  badgeAfterEdit10 === false
    ? ok('editing the workflow after a Check clears the now-stale review badge')
    : fail('the review badge is still pinned to "triage" after an edit made the check stale');

  // ── Check workflow itself failing (HTTP error) also surfaces visibly. ────
  await page10.route('**/*', (route) => {
    const req = route.request();
    if (new URL(req.url()).pathname === '/api/workflows/review') return route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"boom"}' });
    return route.fallback();
  });
  const toastsBeforeReviewFail10 = (await page10.evaluate(() => window.__toasts || [])).length;
  await clickToolbarBtn(page10, 'Check workflow');
  await page10.waitForTimeout(200);
  const toastsAfterReviewFail10 = (await page10.evaluate(() => window.__toasts || [])).slice(toastsBeforeReviewFail10);
  toastsAfterReviewFail10.some(t => /check failed/i.test(t))
    ? ok(`a failed Check workflow request surfaces a visible "Check failed" toast: "${toastsAfterReviewFail10.find(t => /check failed/i.test(t))}"`)
    : fail(`expected a visible "Check failed" toast, got ${JSON.stringify(toastsAfterReviewFail10)}`);

  // ── Dave's scope addition (same MC-962 item, Ron's follow-up screenshot):
  // a multi-step draft with a branch used to land every node at the SAME
  // default spot (stacked, one hiding another's ports) and leave the
  // trigger unwired to the first step (no edge drawn). Fresh workflow so
  // this isn't polluted by the single-node "triage" canvas above. ────────
  //
  // This route is a network mock -- it stands in for the SERVER, so the
  // real layout math (`_draft_auto_layout` in mc/workflows.py, which now
  // assigns every drafted node's x/y and fills `trigger.entry` with the
  // roots) never runs here; the client just trusts whatever x/y and
  // trigger.entry the response carries (`_wfDraftFromDescription`: `st.def
  // = def`, no client-side layout). That fix's OWN regression coverage is
  // Python-side: test_draft_workflow_branch_lays_out_without_overlap_and_
  // wires_trigger in tests/test_workflows.py, which calls draft_workflow()
  // for real and fails on unfixed mc/workflows.py. What THIS mock's x/y
  // and trigger.entry below check is the other half: that the CLIENT
  // faithfully renders whatever non-overlapping, wired layout the server
  // sends -- no independent re-stacking, no dropped wire -- using the same
  // depth-layered coordinates _draft_auto_layout would compute for this
  // exact graph (triage depth 0; items_found/no_new_items depth 1, rows
  // 0/1) so a real server response would look just like this.
  //
  // The canvas above is dirty (edited after Save, for the stale-badge
  // check) -- openWorkflowBuilder's own discard-confirm would otherwise get
  // Playwright's default "dismiss" for an unhandled dialog and silently
  // no-op, leaving the OLD "triage" canvas mounted with no describe box.
  page10.once('dialog', (d) => d.accept());
  await newWorkflow(page10, PID);
  draftResult10 = {
    ok: true, valid: true, errors: [],
    definition: {
      name: 'Backlog triage with branch', description: 'Triage then branch on findings.',
      trigger: { type: 'manual', entry: ['triage'] }, enabled: true,
      nodes: [
        { name: 'triage', type: 'agent', character: 'global:builder', project_id: PID,
          prompt: 'Look for new backlog items.', x: 60, y: 80 },
        { name: 'items_found', type: 'action', action: 'backlog_add', config: {}, x: 352, y: 80 },
        { name: 'no_new_items', type: 'wait', config: {}, x: 352, y: 252 },
      ],
      edges: [
        { from: 'triage', to: 'items_found', when: 'items_found' },
        { from: 'triage', to: 'no_new_items', when: 'otherwise' },
      ],
    },
  };
  await setValue(page10, '#wfb-describe-input', 'Every morning, triage new backlog items and branch on whether any were found.');
  await page10.click('.wfb-describe-row .btn-add');
  await answerHumanProofModalIfShown(page10);
  await page10.waitForTimeout(200);
  const branchLayout10 = await page10.evaluate(() => {
    const boxes = [...document.querySelectorAll('.wfb-node')].map(el => {
      const r = el.getBoundingClientRect();
      return { name: el.dataset.name, left: r.left, top: r.top, right: r.right, bottom: r.bottom };
    });
    let anyOverlap = null;
    for (let i = 0; i < boxes.length && !anyOverlap; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i], b = boxes[j];
        if (a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top) { anyOverlap = [a.name, b.name]; break; }
      }
    }
    return { count: boxes.length, boxes, anyOverlap };
  });
  (branchLayout10.count === 3 && !branchLayout10.anyOverlap)
    ? ok(`a drafted branch (3 nodes) lays out with no two node boxes overlapping (${JSON.stringify(branchLayout10.boxes.map(b => `${b.name}@${Math.round(b.left)},${Math.round(b.top)}`))})`)
    : fail(`expected 3 non-overlapping drafted nodes, got ${JSON.stringify(branchLayout10)}`);
  const triggerWire10 = await page10.evaluate(() => {
    const def = window._wfEntry()._wf.def;
    const entry = (def.trigger && Array.isArray(def.trigger.entry)) ? def.trigger.entry : [];
    // The implied trigger->root line is `.wfb-edge-implied` (_wfRedrawEdges,
    // workflow-builder.js) -- drawn only for names in `trigger.entry` that
    // still have a live port, no per-edge data-attrs to pick out "which"
    // root it targets, so presence + a non-empty entry naming the drafted
    // first step together stand in for "the edge reaches that node".
    const drawnToEntry = entry.length > 0 && !!document.querySelector('#wfb-canvas-svg .wfb-edge-implied');
    return { entry, drawnToEntry };
  });
  (triggerWire10.entry.includes('triage') && triggerWire10.drawnToEntry)
    ? ok(`the trigger is wired to the drafted entry step "triage" (trigger.entry=${JSON.stringify(triggerWire10.entry)}, edge drawn on canvas)`)
    : fail(`expected the trigger wired to "triage" with a drawn edge, got ${JSON.stringify(triggerWire10)}`);

  const uncaught10 = page10Errors.filter(e => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught10.length) uncaught10.forEach((e) => fail('uncaught exception in the MC-962 draft/review flow: ' + e));
  await ctx10.close();

  // ── Dave's THIRD scope addition (same MC-962 item, Ron's third screenshot,
  // project 'drop_shipping_company'): new workflow, don't save, "← Back to
  // conversation", reopen Workflows -- the tab got stuck on render-core.js's
  // literal "Loading..." placeholder forever. Root cause: `refreshModalById`
  // (index.html) only preserved the inline canvas host across an innerHTML
  // rebuild once `#wfb-canvas-viewport` existed inside it. Reopening the
  // Workflows tab sets `curTab` synchronously (switchModalTab) and only THEN
  // awaits `/api/workflows` inside `loadWorkflows` -- during that gap the host
  // has `#wfb-clayrune-section-<pid>` (loadWorkflows' own skeleton) but no
  // canvas yet. An SSE turn event landing in that gap (exactly what a live
  // Vector schedule session in the same project produces) called
  // `refreshModalById`, found no `#wfb-canvas-viewport`, skipped the preserve,
  // and wiped the host back to a brand-new "Loading..." node. `loadWorkflows`'s
  // own in-flight fetch then resolved and called `_wfSyncTabsForProject`,
  // which looks up `#wfb-clayrune-section-<pid>` fresh by id -- found nothing
  // (that id died with the orphaned old host) -- and returned early. Nothing
  // else ever re-calls `loadWorkflows` (the 3s poll only runs while a CC
  // fan-out exists), so the placeholder was permanent. The in-memory
  // `_wfState` survived untouched the whole time -- only the DOM mount was
  // lost -- which is why the fix widens the SAME preserve check to also cover
  // "loadWorkflows already built its skeleton here", not a client-side
  // workaround elsewhere. This reproduces that exact race by calling
  // `window.refreshModalById` mid-flight -- the identical function every SSE
  // turn-event handler calls (conversation.js), not a stand-in for it. ─────
  const ctx11 = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page11 = await ctx11.newPage();
  const page11Errors = [];
  page11.on('pageerror', (e) => page11Errors.push(e.message || String(e)));
  let delayNextWorkflowsGet11 = false;
  await page11.route('**/*', async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/local-auth/status') return route.fulfill({ status: 200, contentType: 'application/json', body: '{"configured":true}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/floor') return route.fulfill({ status: 200, contentType: 'application/json', body: FLOOR_JSON });
    if (path.startsWith('/api/avatars/')) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1PX });
    if (path === '/api/workflows' && req.method() === 'GET') {
      if (delayNextWorkflowsGet11) { delayNextWorkflowsGet11 = false; await new Promise((r) => setTimeout(r, 350)); }
      return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    }
    if (path === `/api/project/${PID}/workflows`) return route.fulfill({ status: 200, contentType: 'application/json', body: '{"workflows":[]}' });
    // Unmocked, these abort forever and agent-log.js's rail poller retries
    // in an unbounded tight loop -- enough iterations across a section's
    // many DOM waits crashed the renderer outright (page5, deterministic).
    if (path === `/api/project/${PID}/agent/log`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/conversations`) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    return route.abort();
  });
  await page11.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page11.waitForSelector('#projects-col .card', { timeout: 15000 });
  await newWorkflow(page11, PID);

  // Mark the draft so "still there" after the race is a real check, not just
  // "a canvas of SOME kind rendered". Typed into the DOM only -- the FIELD
  // SYNC discipline (workflow-builder.js) flushes it into `_wfState.def` when
  // leaving the tab below, exactly like a real user typing then navigating.
  await setValue(page11, '#wfb-name', 'Ron unsaved draft smoke');

  await page11.click('.modal-back-to-chat');
  await page11.waitForTimeout(80);

  // Desktop hides the literal tab strip (`.modal-tab-bar { display:none }`,
  // app.css @media min-width:961px) -- tabs move into the three-dot menu's
  // `_mcMenuSwitchTab`, which is a thin wrapper that closes the menu then
  // calls the SAME `switchModalTab` this calls directly. Real click path,
  // same function reached, no menu-open choreography needed to exercise it.
  delayNextWorkflowsGet11 = true;
  await page11.evaluate((pid) => { switchModalTab(pid, 'workflows'); }, PID);
  // loadWorkflows() builds its skeleton synchronously before the delayed
  // /api/workflows GET -- 60ms is well inside that gap and well before the
  // 350ms delayed response, so this lands exactly mid-flight.
  await page11.waitForTimeout(60);
  await page11.evaluate((pid) => { window.refreshModalById(pid); }, PID);
  // Past the 350ms delayed response + settle time.
  await page11.waitForTimeout(600);

  const raceResult11 = await page11.evaluate((pid) => {
    const body = document.getElementById('workflows-body-' + pid);
    const stillLoading = !!body && /Loading\.\.\./.test(body.textContent || '') && !body.querySelector('#wfb-canvas-viewport');
    const nameEl = document.querySelector('#wfb-name');
    const st = (typeof window._wfEntry === 'function' && window._wfEntry()) ? window._wfEntry()._wf : null;
    return {
      canvasPresent: !!document.getElementById('wfb-canvas-viewport'),
      stillLoading,
      nameValue: nameEl ? nameEl.value : null,
      workflowId: st ? st.workflowId : undefined,
    };
  }, PID);
  (raceResult11.canvasPresent && !raceResult11.stillLoading)
    ? ok('reopening Workflows after an SSE-style rebuild mid-flight mounts the canvas, not a stuck "Loading..." placeholder')
    : fail(`expected the canvas mounted with no stuck placeholder, got ${JSON.stringify(raceResult11)}`);
  (raceResult11.nameValue === 'Ron unsaved draft smoke' && raceResult11.workflowId === null)
    ? ok('the unsaved draft (name typed before "Back to conversation", never saved) is still there after the race')
    : fail(`expected the unsaved draft's name + null workflowId to survive the race, got ${JSON.stringify(raceResult11)}`);

  const uncaught11 = page11Errors.filter(e => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught11.length) uncaught11.forEach((e) => fail('uncaught exception in the reopen-Workflows-mid-SSE-rebuild race: ' + e));
  await ctx11.close();

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0
    ? '\n✅ PASS — the palette IS the Bench (real avatars, initial only where a face is genuinely absent, unrenderable values never echoed), drag-a-person-to-place with its persona preset, the port + popover and drop-onto-card auto-place-and-wire, port-to-port connect, a refused cycle, a refused slot break, an unconnected-port stop stub, mobile bottom-sheet layout, touch-action scroll-lock guard, save (format 2), the schedule-trigger cadence form, MC-962 describe/draft (loading state, visible error, unsaved+disabled landing, Save persists) plus Check workflow (pinned badge, dismissible unassigned list, stale-on-edit clearing), and reopening Workflows after an SSE-style rebuild mid-flight (no stuck "Loading...", unsaved draft survives) all behave correctly.'
    : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
