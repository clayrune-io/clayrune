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
  const vp = await (await page.$('#wfb-canvas-viewport')).boundingBox();
  const candidates = await page.$eval(selector, (el) => {
    const d = el.getAttribute('d') || '';
    const m = /M\s*([\d.-]+),([\d.-]+)\s*C\s*([\d.-]+),([\d.-]+)\s*([\d.-]+),([\d.-]+)\s*([\d.-]+),([\d.-]+)/.exec(d);
    if (!m) return [];
    const [, x0, y0, x1, y1, x2, y2, x3, y3] = m.map(Number);
    const at = (t) => {
      const mt = 1 - t;
      return {
        x: mt ** 3 * x0 + 3 * mt ** 2 * t * x1 + 3 * mt * t ** 2 * x2 + t ** 3 * x3,
        y: mt ** 3 * y0 + 3 * mt ** 2 * t * y1 + 3 * mt * t ** 2 * y2 + t ** 3 * y3,
      };
    };
    // Spread away from both t=0/1 (source/target ports+cards) and t=0.5
    // (the delete × on hover).
    return [0.35, 0.65, 0.25, 0.75, 0.2, 0.8].map(at);
  });
  for (const pt of candidates) {
    const px = vp.x + pt.x, py = vp.y + pt.y;
    const onTarget = await page.evaluate(({ px, py, selector }) => {
      const el = document.elementFromPoint(px, py);
      return !!(el && el.closest(selector));
    }, { px, py, selector });
    if (onTarget) { await page.mouse.click(px, py); return; }
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
  const vpBox = await (await page.$('#wfb-canvas-viewport')).boundingBox();
  await dragPalettePersonTo(page, 'Tobin', vpBox.x + 140, vpBox.y + 240);
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
  const promptLabelInfo = await page.evaluate(() => {
    const label = Array.from(document.querySelectorAll('.wfb-node label'))
      .find(l => l.textContent.trim().startsWith('Prompt'));
    return {
      text: label ? label.textContent.trim() : null,
      hasHint: !!(label && label.querySelector('.memory-hint')),
      hasInsertSelect: !!document.querySelector('.wfb-node .wfb-insert-btn'),
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
  // Capture the auto-generated name now, for that first drag's selector.
  const autoName1 = await page.$eval('.wfb-node', el => el.dataset.name);
  await setValue(page, '.wfb-node .wfb-name', 'triage');
  await setValue(page, '.wfb-node .wfb-prompt', 'Decide whether this is worth drafting.');

  await dragPalettePersonTo(page, 'Fenn', vpBox.x + 460, vpBox.y + 120);
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
  const promptUserSelect = await page.evaluate(() => getComputedStyle(document.querySelector('.wfb-node .wfb-prompt')).userSelect);
  (promptUserSelect === 'text' || promptUserSelect === 'auto')
    ? ok(`a card's own prompt textarea stays selectable/editable (user-select: ${promptUserSelect})`)
    : fail(`expected the prompt textarea to allow selection, got "${promptUserSelect}"`);
  // Selecting actual text INSIDE a field must still work (Change 8's other
  // explicit requirement — don't blanket-kill selection in form fields).
  await page.click('.wfb-node .wfb-prompt');
  await page.keyboard.press(process.platform === 'darwin' ? 'Meta+A' : 'Control+A');
  const fieldSelectionLen = await page.evaluate(() => {
    const el = document.activeElement;
    return el && typeof el.selectionStart === 'number' ? (el.selectionEnd - el.selectionStart) : -1;
  });
  fieldSelectionLen > 0 ? ok('selecting text WITHIN a prompt field still works (Ctrl/Cmd+A selected it)')
                        : fail(`expected a non-empty in-field selection, got length ${fieldSelectionLen}`);

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
  await page.waitForTimeout(80);
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
  // outside the visible canvas entirely. Reset to the default pan before the
  // keyboard-delete test below, which operates back on that original cluster.
  await page.evaluate(() => {
    const st = window._wfEntry()._wf;
    st.viewport.x = 60; st.viewport.y = 40; st.viewport.scale = 1;
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
  const menuItemEls = await page.$$('#wfb-node-menu > div');
  await menuItemEls[0].click(); // Duplicate is first
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
  const homerMenuEls = await page.$$('#wfb-node-menu > div');
  await homerMenuEls[1].click(); // Disconnect is second
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
  const c13Warnings = await page.evaluate(() => {
    const read = (n) => {
      const el = document.querySelector(`.wfb-node[data-name="${n}"]`);
      if (!el) return null;
      const w = el.querySelector('.wfb-node-inline-warning');
      return { warn: el.classList.contains('wfb-node-warning'),
               err: el.classList.contains('wfb-node-error'),
               text: w ? w.textContent : '' };
    };
    return { noengine: read('c13-noengine'), dead: read('c13-deadmodel'), legacy: read('c13-legacymodel') };
  });
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
  const c13AuthBadText = await page.$eval('.wfb-node[data-name="c13-authbad"] .wfb-node-inline-warning', (el) => el.textContent).catch(() => null);
  (c13AuthBadText && /will fail when the workflow runs/.test(c13AuthBadText) && /authbad/.test(c13AuthBadText))
    ? ok(`warning names the consequence and the provider, not the mechanism: "${c13AuthBadText}"`)
    : fail(`expected a consequence-framed warning naming "authbad", got ${JSON.stringify(c13AuthBadText)}`);
  await page.waitForTimeout(400); // let authunknown's probe settle too -- it must NOT warn
  const c13UnknownState = await page.evaluate(() => {
    const el = document.querySelector('.wfb-node[data-name="c13-authunknown"]');
    return { warningClass: el.classList.contains('wfb-node-warning'), inlineWarning: !!el.querySelector('.wfb-node-inline-warning') };
  });
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
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/floor') return route.fulfill({ status: 200, contentType: 'application/json', body: FLOOR_JSON });
    if (path.startsWith('/api/avatars/')) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1PX });
    if (path === '/api/workflows' && req.method() === 'GET') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/workflows`) return route.fulfill({ status: 200, contentType: 'application/json', body: '{"workflows":[]}' });
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
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/floor') return route.fulfill({ status: 200, contentType: 'application/json', body: FLOOR_JSON });
    if (path.startsWith('/api/avatars/')) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1PX });
    // GET reflects whatever's been saved so far -- lets the tabs-row
    // assertion below confirm a save makes the workflow's tab appear.
    if (path === '/api/workflows' && req.method() === 'GET') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(savedWorkflows2) });
    if (path === `/api/project/${PID}/workflows`) return route.fulfill({ status: 200, contentType: 'application/json', body: '{"workflows":[]}' });
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
  await dragPalettePersonTo(page2, 'Tobin', vpBox2.x + 140, vpBox2.y + 240);
  await setValue(page2, '.wfb-node .wfb-name', 'harvest-triage');
  await setValue(page2, '.wfb-node .wfb-prompt', 'Score the signals.');
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
  await dragPalettePersonTo(page3, 'Tobin', vpBox3.x + 140, vpBox3.y + 240);
  await setValue(page3, '.wfb-node .wfb-name', 'step-one');
  await setValue(page3, '.wfb-node .wfb-prompt', 'Do the thing.');

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
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/floor') return route.fulfill({ status: 200, contentType: 'application/json', body: FLOOR_JSON });
    if (path.startsWith('/api/avatars/')) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1PX });
    if (path === '/api/workflows' && req.method() === 'GET') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/workflows`) return route.fulfill({ status: 200, contentType: 'application/json', body: '{"workflows":[]}' });
    return route.abort();
  });
  await page4.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page4.waitForSelector('#projects-col .card', { timeout: 15000 });
  await newWorkflow(page4, PID);

  const vpBox4 = await (await page4.$('#wfb-canvas-viewport')).boundingBox();
  await dragPalettePersonTo(page4, 'Tobin', vpBox4.x + 140, vpBox4.y + 200);
  const c4Name1 = await page4.$eval('.wfb-node', el => el.dataset.name);
  (await page4.$(`.wfb-node[data-name="${c4Name1}"] .wfb-prompt`))
    ? ok('a freshly-dropped, empty-prompt step opens straight to the editable textarea — never a collapsed summary')
    : fail('a fresh empty-prompt step rendered collapsed instead of forcing its panel open');
  (await page4.$(`.wfb-node[data-name="${c4Name1}"] .wfb-prompt-toggle`))
    ? fail('an empty prompt still offered a collapse control — an unfixed error must not be hideable')
    : ok('an empty prompt offers no "Hide prompt" control -- the error cannot be collapsed away');
  await setValue(page4, `.wfb-node[data-name="${c4Name1}"] .wfb-name`, 'triage');
  await setValue(page4, `.wfb-node[data-name="${c4Name1}"] .wfb-prompt`, 'Decide whether this is worth drafting.');

  // Force a sync+render via an action unrelated to the prompt panel itself
  // (same technique the Trigger-position regression guard above uses) --
  // now that the prompt is non-empty, this is the first render where the
  // collapse control is actually reachable.
  await page4.evaluate(() => { window._wfToggleEnabled(); window._wfToggleEnabled(); });
  const c4HideBtn = await page4.$(`.wfb-node[data-name="triage"] .wfb-prompt-toggle`);
  c4HideBtn ? ok('once filled in, the prompt panel offers a "Hide prompt" control')
            : fail('expected a collapse control on a filled-in, valid prompt panel');
  await c4HideBtn.click();
  await page4.waitForTimeout(80);
  const c4CollapsedSummary = await page4.$eval('.wfb-node[data-name="triage"] .wfb-prompt-summary', el => el.textContent).catch(() => null);
  (await page4.$('.wfb-node[data-name="triage"] .wfb-prompt')) === null && c4CollapsedSummary === 'Decide whether this is worth drafting.'
    ? ok(`clicking "Hide prompt" collapsed the panel to a one-line summary: "${c4CollapsedSummary}"`)
    : fail(`collapse did not behave as expected (summary=${JSON.stringify(c4CollapsedSummary)})`);

  // Expand state must survive a re-render (file header's own recurring
  // failure class: "state written but lost on rebuild" -- costs a whole
  // round here every time it regresses).
  await dragPalettePersonTo(page4, 'Fenn', vpBox4.x + 460, vpBox4.y + 120);
  const stillCollapsed = (await page4.$('.wfb-node[data-name="triage"] .wfb-prompt')) === null
    && !!(await page4.$('.wfb-node[data-name="triage"] .wfb-prompt-summary'));
  stillCollapsed ? ok('the collapsed state survived an unrelated structural re-render (placing a second block)')
                 : fail('placing a second block re-expanded a panel the user had explicitly collapsed');

  const c4Name2 = await page4.$eval('.wfb-node:not([data-name="triage"])', el => el.dataset.name);
  (await page4.$(`.wfb-node[data-name="${c4Name2}"] .wfb-prompt`))
    ? ok("the second, freshly-dropped node's own empty prompt still opens straight to the textarea")
    : fail("the second node's empty prompt did not force its panel open");

  // Reopen "triage" and confirm the typed text round-tripped through the
  // collapse -- collapsing must never lose what was typed.
  await page4.click('.wfb-node[data-name="triage"] .wfb-prompt-toggle');
  await page4.waitForTimeout(80);
  const c4Reopened = await page4.$eval('.wfb-node[data-name="triage"] .wfb-prompt', el => el.value).catch(() => null);
  c4Reopened === 'Decide whether this is worth drafting.'
    ? ok('reopening the panel restores the exact text that was there before it was collapsed')
    : fail(`reopening lost or altered the prompt text: ${JSON.stringify(c4Reopened)}`);

  // ── A reference that BECOMES illegal (brief: "the step was renamed") must
  // force the panel open and stay legible -- the collapse must never hide it.
  await dragPortTo(page4,
    `.wfb-node[data-name="triage"] .wfb-port-out`,
    `.wfb-node[data-name="${c4Name2}"] .wfb-port-in`);
  await setValue(page4, `.wfb-node[data-name="${c4Name2}"] .wfb-name`, 'draft');
  await setValue(page4, `.wfb-node[data-name="${c4Name2}"] .wfb-prompt`, 'Draft from {{steps.triage.output}}.');
  await page4.evaluate(() => { window._wfToggleEnabled(); window._wfToggleEnabled(); });
  const c4ChipBeforeRename = await page4.$eval('.wfb-node[data-name="draft"] .wfb-slot-chip', el => el.className).catch(() => null);
  (c4ChipBeforeRename && !c4ChipBeforeRename.includes('wfb-slot-chip-broken'))
    ? ok('the reference to "triage" reads as valid before the rename')
    : fail(`expected a valid (non-broken) chip before the rename, got ${JSON.stringify(c4ChipBeforeRename)}`);
  await page4.click('.wfb-node[data-name="draft"] .wfb-prompt-toggle'); // collapse it -- a valid prompt, nothing forcing it open
  await page4.waitForTimeout(80);
  (await page4.$('.wfb-node[data-name="draft"] .wfb-prompt')) === null
    ? ok('the "draft" panel collapses normally while its reference is still valid')
    : fail('the "draft" panel did not collapse despite having a valid, filled-in prompt');

  // Rename "triage" -- its edges get repointed (existing behaviour), but the
  // literal `{{steps.triage.output}}` text inside "draft"'s prompt does not
  // get rewritten (file header: renames aren't guarded against breaking a
  // TEXT slot reference elsewhere), so it is now a dangling reference to a
  // name that no longer exists.
  await setValue(page4, '.wfb-node[data-name="triage"] .wfb-name', 'triage2');
  await page4.evaluate(() => { window._wfToggleEnabled(); window._wfToggleEnabled(); });
  const c4DraftPromptAfterRename = await page4.$(`.wfb-node[data-name="draft"] .wfb-prompt`);
  c4DraftPromptAfterRename
    ? ok('renaming an upstream step re-forces the dependent panel open instead of leaving it collapsed')
    : fail('a reference broken by an upstream rename stayed hidden behind the collapse');
  const c4BrokenChip = await page4.$eval('.wfb-node[data-name="draft"] .wfb-slot-chip', el => el.className).catch(() => null);
  (c4BrokenChip && c4BrokenChip.includes('wfb-slot-chip-broken'))
    ? ok('the now-illegal {{steps.triage.output}} reference is flagged broken, visibly, without being collapsed away')
    : fail(`expected the chip to read broken after the rename, got ${JSON.stringify(c4BrokenChip)}`);
  (await page4.$('.wfb-node[data-name="draft"] .wfb-prompt-toggle'))
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
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/floor') return route.fulfill({ status: 200, contentType: 'application/json', body: FLOOR_JSON });
    if (path.startsWith('/api/avatars/')) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1PX });
    if (path === '/api/workflows' && req.method() === 'GET') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/workflows`) return route.fulfill({ status: 200, contentType: 'application/json', body: '{"workflows":[]}' });
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
  // field and NO datetime field, then switch modes and confirm the reverse. ──
  const vpBox5 = await (await page5.$('#wfb-canvas-viewport')).boundingBox();
  await dragPaletteToolTo(page5, 'Wait', vpBox5.x + 140, vpBox5.y + 160);
  const waitName = await page5.$eval('.wfb-node', el => el.dataset.name);
  const waitSel = `.wfb-node[data-name="${waitName}"]`;
  (await page5.$eval(`${waitSel} .wfb-wait-mode-select`, el => el.value)) === 'delay'
    ? ok('a freshly-dropped Wait defaults to "For a delay"')
    : fail('a freshly-dropped Wait did not default to delay mode');
  (await page5.$(`${waitSel} [data-cfg-key="minutes"]`)) && !(await page5.$(`${waitSel} [data-cfg-key="at"]`))
    ? ok('delay mode shows the minutes field and NOT the date/time field — no not-yet-relevant control')
    : fail('delay mode rendered the wrong field(s)');
  await setValue(page5, `${waitSel} [data-cfg-key="minutes"]`, '45');
  await page5.selectOption(`${waitSel} .wfb-wait-mode-select`, 'until');
  await page5.waitForTimeout(80);
  (await page5.$(`${waitSel} [data-cfg-key="at"]`)) && !(await page5.$(`${waitSel} [data-cfg-key="minutes"]`))
    ? ok('switching to "Until a date and time" shows the date/time field and hides minutes — never both at once')
    : fail('switching wait modes left the wrong field(s) visible');
  await setValue(page5, `${waitSel} [data-cfg-key="at"]`, '2027-01-01T09:30');

  await setValue(page5, '#wfb-name', 'Wait smoke workflow');
  await page5.evaluate(() => window._wfSave());
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
  const actSel = `.wfb-node[data-name="${actName}"]`;
  (await page5.$eval(`${actSel} .wfb-action-group-select`, el => el.value)) === 'Backlog'
    ? ok('a freshly-dropped Action defaults to the Backlog group')
    : fail('a freshly-dropped Action did not default to Backlog');
  (await page5.$(`${actSel} .wfb-action-verb-select`)) && !(await page5.$(`${actSel} .wfb-action-verb-single`))
    ? ok('Backlog (2 verbs) renders a real second dropdown, not static text')
    : fail('Backlog should render a verb dropdown, not static text');
  await page5.fill(`${actSel} [data-cfg-key="text"]`, 'Something typed that would be lost');
  await page5.selectOption(`${actSel} .wfb-action-group-select`, 'Notify');
  await page5.waitForTimeout(80);
  page5Dialogs.some(m => /clears the settings/i.test(m))
    ? ok(`switching groups with typed content prompted before discarding it: "${page5Dialogs.find(m => /clears the settings/i.test(m))}"`)
    : fail(`expected a confirm() before discarding typed config, got dialogs: ${JSON.stringify(page5Dialogs)}`);
  (await page5.$(`${actSel} .wfb-action-verb-single`)) && !(await page5.$(`${actSel} .wfb-action-verb-select`))
    ? ok('Notify (1 verb) renders static text, not a single-option dropdown — no not-yet-relevant control')
    : fail('Notify should render static text, not a dropdown, for its one verb');
  (await page5.$eval(`${actSel} .wfb-action-id`, el => el.textContent.trim())) === 'notify_operator'
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
  await setValue(page5, `${actSel} .wfb-node-own .wfb-name`, 'Send email');
  await page5.click(`${actSel} .wfb-insert-btn`);
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
  const actSelNow = '.wfb-node[data-name="Send email"]'; // the render after insert wrote the NEW data-name
  const flashedAfterInsert = await page5.$eval(`${actSelNow} [data-cfg-key="message"]`, el => el.classList.contains('clayrune-highlight'));
  flashedAfterInsert
    ? ok('the message field flashed (.clayrune-highlight) right after the insert landed — visible confirmation it worked')
    : fail('expected the message field to carry the highlight-flash class immediately after inserting');

  await setValue(page5, '#wfb-name', 'Renamed action smoke workflow');
  await page5.evaluate(() => window._wfSave());
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
  await page5.click(`${actSelNow} .wfb-insert-btn`);
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
  const apprDesc = await page5.$eval(`.wfb-node[data-name="${apprName}"] .wfb-action-desc`, el => el.textContent).catch(() => '');
  /Parks the run and waits for a human/.test(apprDesc)
    ? ok(`the Approval card states what it does, Action-style: "${apprDesc}"`)
    : fail(`expected the Approval card to describe its consequence, got: "${apprDesc}"`);

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
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === '/api/floor') { floorReqCount6++; return route.fulfill({ status: 200, contentType: 'application/json', body: floorBody6 }); }
    if (path.startsWith('/api/avatars/')) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1PX });
    if (path === '/api/workflows' && req.method() === 'GET') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/workflows`) return route.fulfill({ status: 200, contentType: 'application/json', body: '{"workflows":[]}' });
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
  await page6.fill(`${nodeSel6} .wfb-prompt`, 'do not lose this');
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

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0
    ? '\n✅ PASS — the palette IS the Bench (real avatars, initial only where a face is genuinely absent, unrenderable values never echoed), drag-a-person-to-place with its persona preset, the port + popover and drop-onto-card auto-place-and-wire, port-to-port connect, a refused cycle, a refused slot break, an unconnected-port stop stub, mobile bottom-sheet layout, touch-action scroll-lock guard, save (format 2), and the schedule-trigger cadence form all behave correctly.'
    : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
