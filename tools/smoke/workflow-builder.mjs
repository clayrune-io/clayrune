#!/usr/bin/env node
/**
 * MC-871 Q7 — the workflow builder modal (static/js/workflow-builder.js).
 * Real headless boot (real index.html + real static/js/*.js, no network),
 * same hermetic shape as channel-mode-roster.mjs / drag-to-hire.mjs.
 *
 * Covers, per the brief: add a step, reorder by drag (both the plain case and
 * the slot-order guard refusing an unsafe move), a Paths branch, and save
 * round-tripping through the API.
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

// `page.fill()` calls Playwright's own scrollIntoViewIfNeeded, which — for a
// field inside this modal's OWN `overflow-y:auto` body — scrolled the outer
// document instead of the modal's internal scroll container, sliding the
// modal up under the app's fixed top toolbar and hiding the drag handle from
// later pointer coordinates (elementFromPoint at the handle's old position
// returned the toolbar, not the handle). A harness artifact of `.fill()`,
// not a real interaction path (nothing in real use calls scrollIntoView
// explicitly) — set the value directly and dispatch the same `input` event
// the real typing path fires, which is all `_wfSyncDomToModel` reads anyway.
async function setValue(page, selector, value) {
  await page.evaluate(({ selector, value }) => {
    const el = document.querySelector(selector);
    if (!el) throw new Error('setValue: not found ' + selector);
    el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }, { selector, value });
}

// Click a button by its exact visible text, scoped under a container selector
// — avoids CSS-quoting a `data-listpath="[]"` attribute value inside an
// onclick-attribute selector.
async function clickByText(page, containerSel, text) {
  const clicked = await page.evaluate(({ containerSel, text }) => {
    const container = document.querySelector(containerSel);
    if (!container) return false;
    const btn = [...container.querySelectorAll('button')].find(b => b.textContent.trim() === text);
    if (!btn) return false;
    btn.click();
    return true;
  }, { containerSel, text });
  if (!clicked) throw new Error(`button "${text}" not found in ${containerSel}`);
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
        ok: true, workflow: { ...body, id: 'wf-smoke1', created: '2026-09-11T00:00:00Z', updated: '2026-09-11T00:00:00Z' },
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
  await page.waitForSelector('#wfb-body .wfb-spine', { timeout: 5000 });
  ok('builder modal opened blank (new workflow)');

  // ── Add a step ───────────────────────────────────────────────────────────
  await clickByText(page, '.wfb-list[data-listpath="[]"]', '+ Agent step');
  let topNames = await page.$$eval('.wfb-list[data-listpath="[]"] > .wfb-card-wrap > .wfb-card > .wfb-card-own > .wfb-name', els => els.map(e => e.value));
  topNames.length === 1 ? ok(`"+ Agent step" appended a card (name defaulted to "${topNames[0]}")`)
                        : fail(`expected 1 top-level card after add, got ${topNames.length}`);

  // Name it, and set its prompt.
  await setValue(page, '.wfb-list[data-listpath="[]"] > .wfb-card-wrap:nth-child(1) .wfb-name', 'first');
  await setValue(page, '.wfb-list[data-listpath="[]"] > .wfb-card-wrap:nth-child(1) .wfb-prompt', 'Do the first thing.');

  // ── A second, independent step — the plain reorder case ──────────────────
  await clickByText(page, '.wfb-list[data-listpath="[]"]', '+ Agent step');
  await setValue(page, '.wfb-list[data-listpath="[]"] > .wfb-card-wrap:nth-child(2) .wfb-name', 'second');
  await setValue(page, '.wfb-list[data-listpath="[]"] > .wfb-card-wrap:nth-child(2) .wfb-prompt', 'Do the second thing, unrelated to the first.');
  topNames = await page.$$eval('.wfb-list[data-listpath="[]"] > .wfb-card-wrap > .wfb-card > .wfb-card-own > .wfb-name', els => els.map(e => e.value));
  topNames.join(',') === 'first,second' ? ok('two independent top-level steps in order: first, second')
                                        : fail(`expected [first,second], got [${topNames.join(',')}]`);

  // ── Reorder by drag: drag "second" above "first" (no cross-reference —
  // must succeed) ────────────────────────────────────────────────────────
  const handle2 = await page.$('.wfb-list[data-listpath="[]"] > .wfb-card-wrap:nth-child(2) .wfb-drag-handle');
  const box2 = await handle2.boundingBox();
  const wrap1 = await page.$('.wfb-list[data-listpath="[]"] > .wfb-card-wrap:nth-child(1)');
  const box1 = await wrap1.boundingBox();
  await page.mouse.move(box2.x + box2.width / 2, box2.y + box2.height / 2);
  await page.mouse.down();
  await page.mouse.move(box2.x + box2.width / 2, box1.y + 4, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(150);
  topNames = await page.$$eval('.wfb-list[data-listpath="[]"] > .wfb-card-wrap > .wfb-card > .wfb-card-own > .wfb-name', els => els.map(e => e.value));
  topNames.join(',') === 'second,first' ? ok('drag reordered the two independent steps to [second, first]')
                                        : fail(`expected [second,first] after the drag, got [${topNames.join(',')}]`);

  // ── The slot-order guard: give "first" (now 2nd) a prompt that reads
  // {{steps.second.output}} — "second" now runs BEFORE "first" after the
  // reorder above, so this reference is currently valid. Then try to drag
  // "second" to AFTER "first", which would break it — must be refused. ────
  await setValue(page, '.wfb-list[data-listpath="[]"] > .wfb-card-wrap:nth-child(2) .wfb-prompt', 'Reads {{steps.second.output}} from the step before it.');
  const handleFirst = await page.$('.wfb-list[data-listpath="[]"] > .wfb-card-wrap:nth-child(1) .wfb-drag-handle');
  const boxFirstBefore = await handleFirst.boundingBox();
  const wrap2 = await page.$('.wfb-list[data-listpath="[]"] > .wfb-card-wrap:nth-child(2)');
  const box2b = await wrap2.boundingBox();
  // Drag "second" (currently card 1) DOWN past "first" (card 2) — would put
  // first's dependency (second) after it.
  await page.mouse.move(boxFirstBefore.x + boxFirstBefore.width / 2, boxFirstBefore.y + boxFirstBefore.height / 2);
  await page.mouse.down();
  await page.mouse.move(boxFirstBefore.x + boxFirstBefore.width / 2, box2b.y + box2b.height - 4, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(150);
  const namesAfterGuard = await page.$$eval('.wfb-list[data-listpath="[]"] > .wfb-card-wrap > .wfb-card > .wfb-card-own > .wfb-name', els => els.map(e => e.value));
  namesAfterGuard.join(',') === 'second,first' ? ok('slot-order guard REFUSED the move that would break {{steps.second.output}} — order unchanged')
                                               : fail(`guard should have kept [second,first], got [${namesAfterGuard.join(',')}]`);
  const toasts = await page.evaluate(() => window.__toasts);
  toasts.some(t => /steps\.second/.test(t)) ? ok(`a toast named the broken reference: "${toasts.find(t => /steps\.second/.test(t))}"`)
                                            : fail(`expected a toast naming the broken {{steps.second...}} reference, got ${JSON.stringify(toasts)}`);

  // ── A Paths branch ────────────────────────────────────────────────────────
  // "first" is now card 1 (an agent step); split it into paths.
  await clickByText(page, '.wfb-list[data-listpath="[]"]', '+ Split into paths');
  await page.waitForSelector('.wfb-branch-grid', { timeout: 3000 }).then(
    () => ok('Paths node added — branch grid rendered'),
    () => fail('Paths node did not render a branch grid'));
  const colTitles = await page.$$eval('.wfb-branch-col-title', els => els.map(e => e.textContent.trim()));
  (colTitles.includes('branch-1') && colTitles.some(t => t.startsWith('otherwise')))
    ? ok(`branch columns present: ${JSON.stringify(colTitles)}`)
    : fail(`expected a "branch-1" column and an "otherwise" column, got ${JSON.stringify(colTitles)}`);

  // Add a step inside the branch-1 column specifically (nested listpath).
  const branchListPath = await page.$$eval('.wfb-branch-col', cols => {
    const col = cols.find(c => c.querySelector('.wfb-branch-col-title').textContent.trim() === 'branch-1');
    const list = col && col.querySelector('.wfb-list');
    return list ? list.getAttribute('data-listpath') : null;
  });
  branchListPath && branchListPath !== '[]' ? ok(`branch-1's own list has a nested listpath: ${branchListPath}`)
                                            : fail(`branch-1 list should have a nested (non-top-level) listpath, got ${branchListPath}`);
  await clickByText(page, `.wfb-list[data-listpath='${branchListPath}']`, '+ Agent step');
  const nestedCard = await page.$(`.wfb-list[data-listpath='${branchListPath}'] > .wfb-card-wrap > .wfb-card`);
  const nestedPath = nestedCard ? JSON.parse(await nestedCard.getAttribute('data-nodepath')) : null;
  (nestedPath && nestedPath.length === 4 && nestedPath[1] === 'branches' && nestedPath[2] === 'branch-1')
    ? ok(`step added inside the branch nests at the right path: ${JSON.stringify(nestedPath)}`)
    : fail(`expected a nodepath like [idx,"branches","branch-1",0], got ${JSON.stringify(nestedPath)}`);

  // ── Save round-trips through the API ─────────────────────────────────────
  await setValue(page, '#wfb-name', 'Smoke test workflow');
  await setValue(page, '#wfb-desc', 'Exercises the builder end to end.');
  await clickByText(page, '.wfb-actions', 'Create');
  await page.waitForTimeout(250);
  workflowPosts.length === 1 ? ok('POST /api/workflows fired exactly once on Save')
                             : fail(`expected exactly 1 POST /api/workflows, got ${workflowPosts.length}`);
  const posted = workflowPosts[0] || {};
  posted.name === 'Smoke test workflow' ? ok('posted body carries the name field')
                                        : fail(`posted name should be "Smoke test workflow", got ${JSON.stringify(posted.name)}`);
  // Top level is [second, first, paths]: the earlier drags left order
  // [second, first], and "+ Split into paths" appends after the LAST step
  // (first) — both "second" and "first" are real agent steps, so the paths
  // node is the third top-level node, not the second.
  const postedTop = (posted.steps || []).map(s => s.type);
  postedTop.join(',') === 'agent,agent,paths' ? ok(`posted steps round-trip the tree shape: [${postedTop.join(',')}]`)
                                              : fail(`expected posted top-level types [agent,agent,paths], got [${postedTop.join(',')}]`);
  const postedPaths = (posted.steps || []).find(s => s.type === 'paths');
  (postedPaths && postedPaths.branches && Array.isArray(postedPaths.branches['branch-1']) && postedPaths.branches['branch-1'].length === 1
    && Array.isArray(postedPaths.otherwise))
    ? ok('posted Paths node carries branch-1 (with the nested step) + a mandatory otherwise array')
    : fail(`posted Paths node malformed: ${JSON.stringify(postedPaths)}`);

  await page.waitForFunction(() => {
    const btn = document.querySelector('.wfb-actions .btn-sched-save');
    return btn && btn.textContent.trim() === 'Update';
  }, { timeout: 3000 }).then(() => ok('after a successful save, the button relabels to "Update" (workflowId adopted)'),
                            () => fail('save button never relabeled to "Update" after a successful save'));

  const uncaught = pageErrors.filter(e => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail('uncaught exception during interaction: ' + e));

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0
    ? '\n✅ PASS — add-step, drag-reorder (plain + slot-order-guarded), a Paths branch, and save all round-trip correctly.'
    : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
