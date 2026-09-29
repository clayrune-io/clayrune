#!/usr/bin/env node
/**
 * Desk v1 (MC-977, IA5) — piece page smoke (docs/THE_DESK_V1_IA_REVISION.md
 * §2.4, §5 row IA5).
 *
 * Closes IA5's own acceptance row: card -> piece -> switch all four facets
 * keeps the header + Posy box as the SAME DOM node (identity, not just
 * text); What -> Review -> Back reads '‹ <piece title>'; How -> video
 * director -> Back returns to How.
 *
 * Real headless boot (real index.html + real static/js|css, no network),
 * same hermetic shape as desk-v1-review.mjs / desk-v1-video.mjs.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-piece.mjs
 * Exit 0 = every case holds; 1 = a case regressed / harness error.
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

const PID = 'smoke_deskv1piece';
const PROJECTS = [{
  id: PID, name: 'Desk v1 piece smoke', status: 'active', domain: 'general', emoji: '🧪',
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

async function newBootedPage(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', fulfillOrAbort);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors };
}

function reportUncaught(pageErrors, tag) {
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail(`${tag} uncaught page error: ${e}`));
}

async function navToCampaign(page) {
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.waitForSelector('.desk-v1-campaign', { timeout: 8000 });
}

// Through the campaign card's own primary action (ticket: "the content
// card's primary action opens the piece"), not a direct deskV1Nav — this is
// the real path a user takes, matching desk-v1-review.mjs's own comment on
// why it goes through the campaign page first.
async function navToPieceViaCard(page, familyId) {
  await navToCampaign(page);
  await page.waitForSelector(`[data-family-id="${familyId}"] [data-primary-action]`, { timeout: 8000 });
  await page.click(`[data-family-id="${familyId}"] [data-primary-action]`);
  await page.waitForSelector('.desk-v1-piece', { timeout: 8000 });
}

// ── card -> piece: the primary action on a content card opens the piece
// page, header shows the family's own title. ────────────────────────────────
async function runCardOpensPiece(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);
  await navToPieceViaCard(page, 'fam-restore-points');

  const title = (await page.textContent('.desk-v1-piece-title').catch(() => '') || '').trim();
  if (title === 'Undo anything: restore points in Clayrune 2.1') {
    ok(`card -> piece: header title "${title}"`);
  } else {
    fail(`card -> piece: header title wrong: ${JSON.stringify(title)}`);
  }
  const crumbTitle = (await page.textContent('.desk-v1-crumb-title').catch(() => '') || '').trim();
  if (crumbTitle === title) ok(`card -> piece: crumb title matches ("${crumbTitle}")`);
  else fail(`card -> piece: crumb title wrong: ${JSON.stringify(crumbTitle)}`);

  reportUncaught(pageErrors, '[card-opens-piece]');
  await ctx.close();
}

// ── switch all 4 facets: header + Posy box stay the SAME DOM node (identity,
// not just text) — same marker-property technique as
// desk-v1-campaign.mjs's runPosyDraftPersistence tab-switch check. ─────────
async function runFacetSwitchIdentity(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);
  await navToPieceViaCard(page, 'fam-install-video');
  await page.waitForSelector('[data-facet="what"]', { timeout: 8000 });

  await page.evaluate(() => {
    document.getElementById('desk-v1-piece-header')._deskv1SmokeMarker = 'same-header';
    document.getElementById('desk-v1-piece-posy-input')._deskv1SmokeMarker = 'same-posy';
  });

  const order = ['how', 'when', 'where', 'what'];
  for (const facet of order) {
    await page.click(`[data-facet="${facet}"]`);
    await page.waitForSelector(`[data-facet="${facet}"][aria-selected="true"]`, { timeout: 2000 });
    const identity = await page.evaluate(() => ({
      header: document.getElementById('desk-v1-piece-header')?._deskv1SmokeMarker,
      posy: document.getElementById('desk-v1-piece-posy-input')?._deskv1SmokeMarker,
    }));
    if (identity.header === 'same-header' && identity.posy === 'same-posy') {
      ok(`facet switch -> "${facet}": header + Posy box are the same DOM node`);
    } else {
      fail(`facet switch -> "${facet}": node identity lost: ${JSON.stringify(identity)}`);
    }
  }

  // Content itself did switch (not a no-op) — How shows the video director
  // hand-off, absent from the other three facets' markup.
  const howBody = (await page.textContent('.desk-v1-piece-body').catch(() => '') || '');
  await page.click('[data-facet="how"]');
  await page.waitForSelector('[data-facet="how"][aria-selected="true"]', { timeout: 2000 });
  const howBody2 = (await page.textContent('.desk-v1-piece-body').catch(() => '') || '');
  if (/Open video director/.test(howBody2)) ok('facet switch: "How" body renders the video hand-off');
  else fail(`facet switch: "How" body missing the video hand-off: ${JSON.stringify(howBody2)}`);
  if (howBody2 !== howBody) ok('facet switch: body content actually changes between facets');
  else fail('facet switch: body content identical across facets (switch is a no-op)');

  reportUncaught(pageErrors, '[facet-identity]');
  await ctx.close();
}

// ── What -> Review -> Back reads '‹ <piece title>' (ticket: "review 12b...
// become piece children"). ───────────────────────────────────────────────
async function runWhatToReviewBack(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);
  await navToPieceViaCard(page, 'fam-restore-points');
  await page.waitForSelector('[data-facet="what"][aria-selected="true"]', { timeout: 8000 });

  await page.click('[data-review-btn]');
  await page.waitForSelector('.desk-v1-review', { timeout: 8000 });

  const back = (await page.textContent('.desk-v1-back').catch(() => '') || '').trim();
  if (back.includes('Undo anything: restore points in Clayrune 2.1')) {
    ok(`What -> Review: Back reads "${back}"`);
  } else {
    fail(`What -> Review: Back wrong: ${JSON.stringify(back)}`);
  }

  reportUncaught(pageErrors, '[what-review-back]');
  await ctx.close();
}

// ── How -> video director -> Back returns to How (piece entry's own params
// are untouched by the forward nav, so the facet survives the round trip).
async function runHowToVideoBack(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);
  await navToPieceViaCard(page, 'fam-install-video');
  await page.click('[data-facet="how"]');
  await page.waitForSelector('[data-facet="how"][aria-selected="true"]', { timeout: 8000 });

  await page.click('[data-video-btn]');
  await page.waitForSelector('.desk-v1-video-director', { timeout: 8000 });
  const videoBack = (await page.textContent('.desk-v1-back').catch(() => '') || '').trim();
  if (videoBack.includes('Install in two minutes')) {
    ok(`How -> video director: Back reads "${videoBack}"`);
  } else {
    fail(`How -> video director: Back wrong: ${JSON.stringify(videoBack)}`);
  }

  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-piece', { timeout: 8000 });
  const activeFacet = await page.$eval('.desk-v1-camp-tab[aria-selected="true"]', (b) => b.dataset.facet).catch(() => null);
  if (activeFacet === 'how') {
    ok('How -> video director -> Back: returns to How');
  } else {
    fail(`How -> video director -> Back: landed on facet ${JSON.stringify(activeFacet)}, expected "how"`);
  }

  reportUncaught(pageErrors, '[how-video-back]');
  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  await runCardOpensPiece(browser);
  await runFacetSwitchIdentity(browser);
  await runWhatToReviewBack(browser);
  await runHowToVideoBack(browser);
  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error('❌ FAIL — smoke harness error: ' + (e && e.message ? e.message : e));
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}

console.log(bad
  ? `\n❌ FAIL — ${bad} Desk v1 piece check(s) regressed.`
  : '\n✅ PASS — Desk v1 IA5 piece page: card -> piece, facet-switch DOM identity, What -> Review -> Back, How -> video -> Back all hold.');
process.exit(exitCode);
