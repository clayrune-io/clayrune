#!/usr/bin/env node
/**
 * Desk v1 (MC-977, R0 plan T5) — video intake + director smoke (frame 12d;
 * 11d not in the repo, intake built from §5 text).
 *
 * Closes: A9 (watch-and-review reuses T3's review layout, player variant —
 * never a fork), A12 (never invent status prose / never write "free" unless
 * guaranteed — MED-04's cost card).
 *
 * Real headless boot (real index.html + real static/js|css, no network), same
 * hermetic shape as desk-v1-review.mjs / desk-v1-harness.mjs.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-video.mjs
 * Exit 0 = every case holds (render checks in all three tones, interaction
 * checks once); 1 = a case regressed / harness error.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const ASSETS_DIR = resolve(REPO_ROOT, 'assets');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';

const MIME = { '.webp': 'image/webp', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml' };

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];
for (const f of readdirSync(ASSETS_DIR)) {
  const ext = f.slice(f.lastIndexOf('.'));
  if (MIME[ext]) STATIC[`/assets/${f}`] = [MIME[ext], readFileSync(resolve(ASSETS_DIR, f))];
}

const PID = 'smoke_deskv1video';
const PROJECTS = [{
  id: PID, name: 'Desk v1 video smoke', status: 'active', domain: 'general', emoji: '🧪',
  description: '', summary: '', current_task: 'Idle', next_action: '',
  blocked: false, blocked_reason: null, activity_log: [], backlog: [],
  project_path: '/smoke/' + PID, last_updated: '2026-09-09T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null,
  display_order: 0, provider: 'claude', use_streaming_agent: true,
  distiller_mode: 'proposed', distiller_min_recurrence: 3,
  distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
  distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
  distiller_skip_errors: true, roster: [],
}];

const TONES = [
  { name: 'default/dark', ls: {} },
  { name: 'tone-warm', ls: { mc_tone: 'warm' } },
  { name: 'tone-editorial', ls: { mc_tone: 'editorial' } },
];

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

async function fulfillOrAbort(route) {
  const req = route.request();
  const path = new URL(req.url()).pathname;
  const J = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  const hit = STATIC[path];
  if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
  if (path === '/api/projects') return J(PROJECTS);
  if (path === '/api/config') return J({ desk_v1: true, user_timezone: '' });
  if (path === '/api/characters') return J([]);
  return route.abort();
}

async function newBootedPage(browser, tone, viewport) {
  const ctx = await browser.newContext({ viewport: viewport || { width: 1440, height: 950 } });
  const page = await ctx.newPage();
  await page.addInitScript((ls) => {
    try { for (const k of Object.keys(ls)) localStorage.setItem(k, ls[k]); } catch (e) {}
  }, (tone && tone.ls) || {});
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', fulfillOrAbort);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors };
}

async function navToVideo(page, familyId) {
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.evaluate((fid) => window.deskV1Nav('video', { campaignId: 'camp-1', familyId: fid }), familyId);
}

function reportUncaught(pageErrors, tag) {
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail(`${tag} uncaught page error: ${e}`));
}

// ── director render checks, one per tone: stale badge (r3's OWN duration,
// not a recompute from since-edited scenes), scene strip proportions, render
// card math (rate x formats), A12 "never write free". ──────────────────────
async function runToneRenderChecks(browser, tone) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, tone);
  await navToVideo(page, 'fam-install-video');
  await page.waitForSelector('.desk-v1-video-director', { timeout: 8000 });

  const title = (await page.textContent('.desk-v1-crumb-title').catch(() => '') || '').trim();
  if (title === 'Install in two minutes') ok(`[${tone.name}] director title: "${title}"`);
  else fail(`[${tone.name}] director title wrong: ${JSON.stringify(title)}`);

  // r3's OWN duration (39s, set at render time) — NOT a recompute from
  // today's scenes (42s, after the "Restore demo" trim to 12s).
  const badge = (await page.textContent('.desk-v1-video-stalebadge').catch(() => '') || '');
  if (/r3/.test(badge) && /0:39/.test(badge)) {
    ok(`[${tone.name}] stale badge reports r3's own 0:39, not the since-edited 0:42: "${badge.trim()}"`);
  } else {
    fail(`[${tone.name}] stale badge wrong: ${JSON.stringify(badge)}`);
  }

  const pendingTotal = (await page.textContent('.desk-v1-video-pending-total').catch(() => '') || '');
  if (/0:42/.test(pendingTotal)) ok(`[${tone.name}] pending-edits card shows the CURRENT total 0:42`);
  else fail(`[${tone.name}] pending total wrong: ${JSON.stringify(pendingTotal)}`);

  const widths = await page.$$eval('.desk-v1-video-scene', (els) => els.map((e) => e.getBoundingClientRect().width));
  if (widths.length === 5 && widths[1] > widths[0] && widths[2] > widths[3]) {
    ok(`[${tone.name}] scene strip: 5 tiles, widths proportional to duration (Installer 14s > Download 6s, Restore-demo 12s > Restore 5s)`);
  } else {
    fail(`[${tone.name}] scene strip widths not proportional: ${JSON.stringify(widths)}`);
  }
  const dot = await page.$('.desk-v1-video-scene[data-scene-id="sc-3"] .desk-v1-video-scene-dot');
  if (dot) ok(`[${tone.name}] Scene 3 ("Restore demo") shows the edited "•" marker`);
  else fail(`[${tone.name}] Scene 3 missing its edited "•" marker`);

  // Render card: 2 distinct formats (9:16 published + 16:9 needs_review; the
  // planned blog version has no format) x $0.40/format rate (RENDER_BUDGET,
  // T0a fixture) = $0.80 estimate, $1.60 maximum (covers one retry).
  const card = (await page.textContent('.desk-v1-video-rendercard').catch(() => '') || '');
  if (/\$0\.40 \/ format/.test(card) && /2 × \$0\.40 = \$0\.80/.test(card) && /\$1\.60/.test(card)) {
    ok(`[${tone.name}] render card: Rate $0.40/format, Estimate 2×$0.40=$0.80, Maximum $1.60`);
  } else {
    fail(`[${tone.name}] render card math wrong: ${JSON.stringify(card)}`);
  }
  if (/\$134\.00/.test(card) && /\$132\.40/.test(card)) {
    ok(`[${tone.name}] render card: budget left $134.00 (150-16), after-at-most $132.40 (134-1.60)`);
  } else {
    fail(`[${tone.name}] render card budget lines wrong: ${JSON.stringify(card)}`);
  }
  const renderBtn = (await page.textContent('[data-render-btn]').catch(() => '') || '').trim();
  if (renderBtn === 'Render · up to $1.60') ok(`[${tone.name}] render button: "${renderBtn}"`);
  else fail(`[${tone.name}] render button label wrong: ${JSON.stringify(renderBtn)}`);

  // A9: watch-and-review is enabled (render.revision === the needs_review
  // version's own revision, both 3) and reuses T3, not a fork.
  const watchDisabled = await page.$eval('[data-watch-review]', (b) => b.disabled).catch(() => true);
  if (!watchDisabled) ok(`[${tone.name}] A9: "Watch and review ›" is enabled (r3 rendered, v-install-li at r3)`);
  else fail(`[${tone.name}] A9: watch-and-review should be enabled here`);

  // A12: the ONE legitimate spot for invented status prose is the render-job
  // vocabulary itself — everywhere else (intake cost line, render card) must
  // never claim "free" outright.
  const intakeCheck = card + badge;
  if (!/\bfree\b/i.test(intakeCheck)) ok(`[${tone.name}] A12: render card never claims "free"`);
  else fail(`[${tone.name}] A12 violated: "free" found in render card text`);

  reportUncaught(pageErrors, `[${tone.name}]`);
  await ctx.close();
}

// ── Intake sheet: 4 tiles, brief -> storyboard creates a new family + jumps
// into its Director (client-side write through commandBus, Undo removes it).
// A12: "No video-generation charge yet." — never "free". ────────────────────
async function runIntakeCreate(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.evaluate(() => window.deskV1Nav('video', { campaignId: 'camp-1', mode: 'intake' })); // force intake even though fam-install-video already exists
  await page.waitForSelector('.desk-v1-video-intake', { timeout: 8000 });

  const tiles = await page.$$eval('.desk-v1-video-tile', (els) => els.map((e) => e.textContent.trim()));
  if (tiles.length === 4 && /Create/.test(tiles[0]) && /Upload/.test(tiles[1]) && /Connect/.test(tiles[2]) && /Record/.test(tiles[3])) {
    ok('intake: 4 tiles in order Create · Upload · Connect · Record');
  } else {
    fail(`intake: tiles wrong: ${JSON.stringify(tiles)}`);
  }

  const costline = (await page.textContent('.desk-v1-video-costline').catch(() => '') || '');
  if (/No video-generation charge yet/.test(costline) && /2¢/.test(costline) && !/\bfree\b/i.test(costline)) {
    ok(`A12: intake cost line states the AI cost, never "free": "${costline.trim()}"`);
  } else {
    fail(`A12 violated / cost line wrong: ${JSON.stringify(costline)}`);
  }

  await page.fill('#desk-v1-video-brief', 'A quick teaser for the new dashboard');
  await page.click('[data-intake-create]');
  await page.waitForSelector('.desk-v1-video-director', { timeout: 3000 });
  const newTitle = (await page.textContent('.desk-v1-crumb-title').catch(() => '') || '').trim();
  if (newTitle === 'A quick teaser for the new dashboard') {
    ok(`intake: "Make a storyboard" creates the family and jumps straight into its Director: "${newTitle}"`);
  } else {
    fail(`intake: create-and-jump failed: ${JSON.stringify(newTitle)}`);
  }

  // Undo removes the family and returns to intake.
  const toastText = (await page.textContent('.toast').catch(() => '') || '');
  if (/Started a storyboard/.test(toastText)) {
    await page.click('.toast .toast-btn.primary');
    await page.waitForSelector('.desk-v1-video-intake', { timeout: 3000 });
    ok('intake: Undo removes the new family and returns to the intake sheet');
  } else {
    fail(`intake: no Undo-able toast after create: ${JSON.stringify(toastText)}`);
  }

  reportUncaught(pageErrors, '[intake-create]');
  await ctx.close();
}

// ── Upload: reject (wrong codec/size) then accept a canned file — both
// outcomes in §5's "validate type, size and codec" reachable deterministically. ──
async function runUploadFlow(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.evaluate(() => window.deskV1Nav('video', { campaignId: 'camp-1', mode: 'intake' }));
  await page.waitForSelector('.desk-v1-video-intake', { timeout: 8000 });
  await page.click('[data-intake-tile="upload"]');

  await page.click('[data-upload-file="raw-capture.mov"]');
  await page.waitForSelector('.desk-v1-video-reject', { timeout: 2000 });
  const reject = (await page.textContent('.desk-v1-video-reject').catch(() => '') || '');
  if (/500MB/.test(reject) && /ProRes/.test(reject)) ok(`upload: oversized/unsupported-codec file rejected with a reason: "${reject.trim()}"`);
  else fail(`upload: reject reason wrong: ${JSON.stringify(reject)}`);

  await page.click('[data-upload-file="install-walkthrough.mp4"]');
  await page.waitForSelector('.desk-v1-video-notice:has-text("Processing")', { timeout: 1000 }).catch(() => {});
  await page.waitForSelector('.desk-v1-video-notice:has-text("accepted")', { timeout: 2000 });
  ok('upload: a valid file passes type/size/codec and is accepted');

  reportUncaught(pageErrors, '[upload]');
  await ctx.close();
}

// ── Connect: classification is deterministic per URL — import / preview /
// link-only. Never fabricates a preview (U11) for a link-only source. ───────
async function runConnectFlow(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.evaluate(() => window.deskV1Nav('video', { campaignId: 'camp-1', mode: 'intake' }));
  await page.waitForSelector('.desk-v1-video-intake', { timeout: 8000 });
  await page.click('[data-intake-tile="connect"]');

  const cases = [
    { url: 'https://drive.google.com/file/x', mode: 'import', text: /imported/ },
    { url: 'https://youtube.com/watch?v=x', mode: 'preview', text: /previewed here, but not imported/ },
    { url: 'https://example.com/clip.mp4', mode: 'link', text: /Link only/ },
  ];
  for (const c of cases) {
    await page.fill('#desk-v1-video-connect-url', c.url);
    await page.click('[data-connect-run]');
    await page.waitForSelector(`[data-connect-mode="${c.mode}"]`, { timeout: 2000 });
    const text = (await page.textContent(`[data-connect-mode="${c.mode}"]`).catch(() => '') || '');
    if (c.text.test(text)) ok(`connect: ${c.url} -> ${c.mode} ("${text.trim()}")`);
    else fail(`connect: ${c.url} classified wrong: ${JSON.stringify(text)}`);
  }
  // U11: link-only never offers a fabricated preview/"Make a storyboard" from a
  // source Clayrune can't actually read frames from.
  const hasCreateFollowup = await page.$('[data-connect-mode="link"] ~ [data-intake-create]');
  if (!hasCreateFollowup) ok('U11: link-only source never fabricates a preview or storyboard follow-up');
  else fail('U11 violated: link-only source offered a storyboard follow-up');

  reportUncaught(pageErrors, '[connect]');
  await ctx.close();
}

// ── Scene strip: drag-reorder + edge-trim (pointer-drag.js), both go through
// commandBus (toast + Undo), pendingEdits + total length update live. ───────
async function runSceneDragFlow(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToVideo(page, 'fam-install-video');
  await page.waitForSelector('.desk-v1-video-scenestrip', { timeout: 8000 });

  // Reorder: drag scene 1 ("Download") past scene 2 ("Installer").
  const scenes = await page.$$('.desk-v1-video-scene');
  const box1 = await scenes[0].boundingBox();
  const box2 = await scenes[1].boundingBox();
  await page.mouse.move(box1.x + box1.width / 2, box1.y + box1.height / 2);
  await page.mouse.down();
  await page.mouse.move(box2.x + box2.width / 2, box2.y + box2.height / 2, { steps: 6 });
  await page.mouse.up();
  await page.waitForSelector('.desk-v1-video-pending-row:has-text("Reordered")', { timeout: 2000 });
  const order = await page.$$eval('.desk-v1-video-scene', (els) => els.map((e) => e.dataset.sceneId));
  if (order[0] === 'sc-2' && order[1] === 'sc-1') ok(`drag-reorder: "Download" dropped after "Installer" -> order ${JSON.stringify(order)}`);
  else fail(`drag-reorder failed: ${JSON.stringify(order)}`);

  // Undo restores the original order.
  await page.click('.toast .toast-btn.primary');
  await page.waitForSelector('.desk-v1-video-scene', { timeout: 2000 });
  const restored = await page.$$eval('.desk-v1-video-scene', (els) => els.map((e) => e.dataset.sceneId));
  if (restored[0] === 'sc-1' && restored[1] === 'sc-2') ok('drag-reorder: Undo restores the original order');
  else fail(`drag-reorder Undo failed: ${JSON.stringify(restored)}`);

  // Trim: drag scene 1's right edge outward by 20px (~+3s at 1px/0.15s).
  const edge = await page.$('.desk-v1-video-scene[data-scene-id="sc-1"] [data-trim-edge="r"]');
  const eb = await edge.boundingBox();
  await page.mouse.move(eb.x + eb.width / 2, eb.y + eb.height / 2);
  await page.mouse.down();
  await page.mouse.move(eb.x + eb.width / 2 + 20, eb.y + eb.height / 2, { steps: 5 });
  await page.mouse.up();
  await page.waitForSelector('.desk-v1-video-pending-row:has-text("Trimmed")', { timeout: 2000 });
  ok('drag-trim: dragging a scene edge commits a "Trimmed ..." pending edit');

  reportUncaught(pageErrors, '[scene-drag]');
  await ctx.close();
}

// ── Insert-scene (+ between tiles) and Posy scope menu (MED-03: "Replies
// state which versions are affected"). ──────────────────────────────────────
async function runInsertAndScopeFlow(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToVideo(page, 'fam-install-video');
  await page.waitForSelector('.desk-v1-video-scenestrip', { timeout: 8000 });

  await page.evaluate(() => { window.prompt = () => 'Uninstall demo'; });
  const before = await page.$$eval('.desk-v1-video-scene', (els) => els.length);
  await page.hover('.desk-v1-video-scenestrip');
  await page.click('[data-insert-at="1"]');
  await page.waitForFunction((n) => document.querySelectorAll('.desk-v1-video-scene').length === n, before + 1, { timeout: 2000 });
  const labels = await page.$$eval('.desk-v1-video-scene-label', (els) => els.map((e) => e.textContent));
  if (labels.some((l) => /Uninstall demo/.test(l))) ok('insert-scene: "+" between tiles inserts a new scene at that index');
  else fail(`insert-scene failed: ${JSON.stringify(labels)}`);

  // Posy scope: default "Whole video" -> pick "Scene 1" via the scope menu.
  const scopeBtn = await page.$('[data-scope-trigger]');
  const initialScope = (await scopeBtn.textContent()).trim();
  if (/Whole video/.test(initialScope)) ok(`Posy scope: defaults to "Whole video"`);
  else fail(`Posy scope default wrong: ${JSON.stringify(initialScope)}`);
  await scopeBtn.click();
  await page.waitForSelector('.desk-v1-addto-menu', { timeout: 2000 });
  const menuItems = await page.$$eval('.desk-v1-addto-menu button', (els) => els.map((e) => e.textContent.trim()));
  if (!menuItems.includes('+ New campaign')) ok('Posy scope menu: noAppendNew suppresses "+ New campaign" on a plain choice menu');
  else fail(`Posy scope menu wrongly appended "+ New campaign": ${JSON.stringify(menuItems)}`);
  await page.click('.desk-v1-addto-menu button:has-text("Scene 1")');
  const scopeAfter = (await page.textContent('[data-scope-trigger]').catch(() => '') || '').trim();
  if (/Scene 1/.test(scopeAfter)) ok(`Posy scope: switches to "${scopeAfter}" after picking it from the menu`);
  else fail(`Posy scope did not update: ${JSON.stringify(scopeAfter)}`);

  reportUncaught(pageErrors, '[insert-scope]');
  await ctx.close();
}

// ── Render flow (ADS-03): atomic budget reservation at click time, job goes
// queued -> rendering -> ready, unused retry reserve refunded on completion. ─
async function runRenderFlow(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToVideo(page, 'fam-install-video');
  await page.waitForSelector('.desk-v1-video-rendercard', { timeout: 8000 });

  const spentBefore = await page.evaluate(() => window.DeskV1Fixtures.renderBudget.spent);
  await page.click('[data-render-btn]');
  const spentAfterClick = await page.evaluate(() => window.DeskV1Fixtures.renderBudget.spent);
  if (Math.abs(spentAfterClick - (spentBefore + 1.60)) < 0.001) {
    ok(`render: clicking Render reserves the full MAXIMUM ($1.60) atomically (spent ${spentBefore} -> ${spentAfterClick})`);
  } else {
    fail(`render: budget reservation wrong: ${spentBefore} -> ${spentAfterClick}`);
  }

  // The Render click leaves a "Started rendering..." Undo toast on screen
  // (§10's commandBus convention, same as every other drop) with no
  // auto-dismiss timer, same as T3/T4. We're asserting job/budget state
  // here, not the toast itself, so clear it without invoking Undo rather
  // than let it intercept the Renders-tab click underneath it.
  await page.evaluate(() => document.querySelectorAll('#toast-container .toast').forEach((t) => t.remove()));

  await page.waitForSelector('[data-video-tab="renders"]', { timeout: 2000 });
  await page.click('[data-video-tab="renders"]');
  // Fixtures already carry a Ready r3 job, so target the NEW job (render-r4)
  // by id — matching on data-job-status alone would silently pass against
  // the pre-existing r3 row and never actually observe this job's transition.
  await page.waitForSelector('.desk-v1-video-jobrow[data-job-id="render-r4"][data-job-status="ready"]', { timeout: 3000 });
  const jobRow = (await page.textContent('.desk-v1-video-jobrow[data-job-id="render-r4"]').catch(() => '') || '');
  if (/r4/.test(jobRow) && /Ready/.test(jobRow)) ok(`render: job reaches "Ready" (r4): "${jobRow.trim()}"`);
  else fail(`render: job never reached Ready: ${JSON.stringify(jobRow)}`);

  const spentAfterReady = await page.evaluate(() => window.DeskV1Fixtures.renderBudget.spent);
  if (Math.abs(spentAfterReady - (spentBefore + 0.80)) < 0.001) {
    ok(`render: unused retry reserve refunded once ready (spent settles at +$0.80, not +$1.60)`);
  } else {
    fail(`render: refund-on-completion wrong: expected +0.80 from ${spentBefore}, got ${spentAfterReady}`);
  }

  reportUncaught(pageErrors, '[render-flow]');
  await ctx.close();
}

// ── Phone (§11, ground rule 7): rail stacks below main, scene strip scrolls
// instead of shrinking tiles unreadably, hit targets >=44px. ────────────────
async function runPhoneLayout(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} }, { width: 390, height: 844 });
  await navToVideo(page, 'fam-install-video');
  await page.waitForSelector('.desk-v1-video-director', { timeout: 8000 });

  const stacked = await page.$eval('.desk-v1-video-layout', (el) => getComputedStyle(el).flexDirection === 'column');
  if (stacked) ok('§11: main + rail stack vertically on phone width');
  else fail('§11: main + rail did not stack on phone width');

  const stripOverflow = await page.$eval('.desk-v1-video-scenestrip', (el) => getComputedStyle(el).overflowX);
  if (stripOverflow === 'auto' || stripOverflow === 'scroll') ok(`§11: scene strip scrolls horizontally on phone (overflow-x: ${stripOverflow})`);
  else fail(`§11: scene strip should scroll on phone, got overflow-x: ${stripOverflow}`);

  const tileHeights = await page.$$eval('.desk-v1-video-scene', (els) => els.map((e) => e.getBoundingClientRect().height));
  if (tileHeights.every((h) => h >= 44)) ok(`§11: scene tiles hit target >= 44px (${tileHeights.map((h) => h.toFixed(0)).join(',')})`);
  else fail(`§11: scene tiles below 44px: ${JSON.stringify(tileHeights)}`);

  const widths = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth }));
  if (widths.scrollWidth <= widths.innerWidth) ok(`§11: director never exceeds the phone viewport (scrollWidth ${widths.scrollWidth} <= innerWidth ${widths.innerWidth})`);
  else fail(`§11: director overflows the phone viewport: scrollWidth ${widths.scrollWidth} > innerWidth ${widths.innerWidth}`);

  reportUncaught(pageErrors, '[phone]');
  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  for (const tone of TONES) await runToneRenderChecks(browser, tone);
  await runIntakeCreate(browser);
  await runUploadFlow(browser);
  await runConnectFlow(browser);
  await runSceneDragFlow(browser);
  await runInsertAndScopeFlow(browser);
  await runRenderFlow(browser);
  await runPhoneLayout(browser);
  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error('❌ FAIL — smoke harness error: ' + (e && e.message ? e.message : e));
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}

console.log(bad
  ? `\n❌ FAIL — ${bad} Desk v1 video check(s) regressed.`
  : '\n✅ PASS — Desk v1 T5 video: intake (create/upload/connect), director (stale badge, render card math, A9 watch-and-review, A12 never "free"), scene drag (reorder/trim/insert), Posy scope, render flow (reserve+refund), phone layout all hold.');
process.exit(exitCode);
