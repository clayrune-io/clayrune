#!/usr/bin/env node
/**
 * Desk v1 (MC-977, R0 plan T8) — R0 exit gate.
 *
 * Every ticket smoke (T0a-T7) already asserts its OWN A-check, per its own
 * surface, and (per grep of each file's header) already loops the render
 * assertions across all three tones. T8's job is the two things no single
 * ticket owns:
 *
 *   PART A — aggregate: re-run all 12 existing Desk smokes and require every
 *   one green. This is the actual regression gate a "R0 exit" needs after
 *   7 lanes' worth of sequential merges (CLAUDE.md 2026-09-10: run smokes
 *   after EACH merge, not just the last one) — T8 runs last, so it is the
 *   one point that can see all of them at once.
 *
 *   PART B — cross-cutting sweeps that need EVERY route, INCLUDING the two
 *   surfaces no ticket smoke drives end-to-end together with the rest
 *   (rules popover, Proposed campaign + Start sheet), which none of the
 *   per-ticket smokes needed to combine:
 *     - A1 (no node graph / connector paths), re-asserted on every route in
 *       this file's own list, across all 3 tones.
 *     - A12 (copy lint) run over the FULL rendered text of every route, via
 *       the shared kit's own lintCopy(), across all 3 tones.
 *     - A15 (status readable in greyscale): a structural check that every
 *       rendered status carries both glyph AND word (across all 3 tones,
 *       cheap), plus a real greyscale screenshot per route (default tone,
 *       archived for a human look — CLAUDE.md's UX-01 rule can be asserted
 *       structurally but "readable" is ultimately a human call).
 *
 * A2-A11, A13, A14 are NOT re-implemented here — they are feature-behavior
 * checks that already live, in depth, in the surface's own ticket smoke
 * (see docs/desk_v1_r0_exit.md's traceability table). Re-deriving them here
 * would be a second copy of the same assertion, drifting the first time
 * either one is touched.
 *
 * U01-U16 and the 5-user test are NOT scriptable — see docs/desk_v1_r0_exit.md.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-exit.mjs
 * Exit 0 = all 12 existing smokes green AND every PART B sweep holds;
 * 1 = something regressed. Also writes
 * docs/desk_v1/screens/exit_greyscale_<route>.png (default tone).
 */
import { readFileSync, readdirSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { spawnSync } from 'node:child_process';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const ASSETS_DIR = resolve(REPO_ROOT, 'assets');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const SHOT_DIR = resolve(REPO_ROOT, 'docs', 'desk_v1', 'screens');
mkdirSync(SHOT_DIR, { recursive: true });

const MIME = { '.webp': 'image/webp', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml' };
const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];
for (const f of readdirSync(ASSETS_DIR)) {
  const ext = f.slice(f.lastIndexOf('.'));
  if (MIME[ext]) STATIC[`/assets/${f}`] = [MIME[ext], readFileSync(resolve(ASSETS_DIR, f))];
}

// A 1x1 transparent PNG — /api/avatars/courier only needs to exist so the
// posy-avatar fetch this session's T8 fix added (desk-v1-kit.js) resolves to
// SOMETHING other than the neutral dashed circle; the exact pixels don't
// matter to any check here (unlike _scratch/capture_fix_screens.mjs's
// before/after pair, which used the real asset for a faithful screenshot).
const PIXEL_PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');

const PID = 'smoke_deskv1exit';
const PROJECTS = [{
  id: PID, name: 'Desk v1 exit smoke', status: 'active', domain: 'general', emoji: '🧪',
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
const BENCH = [{ scope: 'global', name: 'social-media-strategist', avatar: 'fig:courier', display: 'Posy' }];

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
  if (path === '/api/floor') return J({ bench: BENCH, figures: [] });
  if (path.startsWith('/api/avatars/')) return route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL_PNG });
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

// ── PART A: run every other Desk smoke as a subprocess, require green ──────
// This is the actual regression gate — it is what "R0 exit" means after 7
// independent lanes' worth of sequential merges. See the docs/CLAUDE.md
// 2026-09-10 rule this cites in the file banner.
const OTHER_SMOKES = [
  'desk-v1-harness.mjs', 'desk-v1-kit.mjs', 'desk-v1-home.mjs',
  'desk-v1-campaign.mjs', 'desk-v1-rules.mjs', 'desk-v1-review.mjs',
  'desk-v1-calendar.mjs', 'desk-v1-video.mjs', 'desk-v1-conversations.mjs',
  'desk-v1-results.mjs', 'desk.mjs', 'boot-smoke.mjs',
];

function runOtherSmokes() {
  const results = [];
  for (const f of OTHER_SMOKES) {
    const r = spawnSync(process.execPath, [resolve(__dirname, f)], { cwd: __dirname, encoding: 'utf8' });
    const outLines = (r.stdout || '').split('\n').map((l) => l.trim()).filter(Boolean);
    const lastLine = outLines.length ? outLines[outLines.length - 1] : '(no stdout)';
    const passed = r.status === 0;
    results.push({ file: f, passed, lastLine, status: r.status });
    if (passed) ok(`${f}: exit 0 — "${lastLine}"`);
    else fail(`${f}: exit ${r.status} — "${lastLine}"`);
  }
  return results;
}

// ── PART B surfaces: every route this ticket names, reached the same way a
// real caller reaches it (deskV1Nav / the exported open-functions), not by
// re-deriving click paths already covered by each surface's own smoke. ─────
const SURFACES = [
  { key: 'home', label: 'Home', wait: '.desk-v1-stub-link, .desk-v1-home',
    nav: async (page) => { await page.evaluate(() => { while (document.querySelector('.desk-v1-back')) document.querySelector('.desk-v1-back').click(); }); } },
  { key: 'campaign-active', label: 'Campaign (camp-1, Active)', wait: '.desk-v1-camp-summary',
    nav: async (page) => page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' })) },
  { key: 'rules-popover', label: 'Rules popover (camp-1)', wait: '.desk-v1-rules-pop',
    nav: async (page) => page.evaluate(() => window.deskV1Nav('rules', { campaignId: 'camp-1' })),
    teardown: async (page) => { await page.keyboard.press('Escape'); } },
  { key: 'calendar', label: 'Calendar (camp-1)', wait: '.desk-v1-calendar',
    nav: async (page) => page.evaluate(() => window.deskV1Nav('calendar', { campaignId: 'camp-1' })) },
  { key: 'review', label: 'Full-width review (camp-1)', wait: '.desk-v1-review',
    nav: async (page) => page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1' })) },
  { key: 'video-intake', label: 'Video intake (camp-1)', wait: '.desk-v1-video-intake',
    nav: async (page) => page.evaluate(() => window.deskV1Nav('video', { campaignId: 'camp-1', mode: 'intake' })) },
  { key: 'video-director', label: 'Video director (fam-install-video)', wait: '.desk-v1-video-director',
    nav: async (page) => page.evaluate(() => window.deskV1Nav('video', { campaignId: 'camp-1', familyId: 'fam-install-video' })) },
  { key: 'conversations', label: 'Conversations (camp-1)', wait: '.desk-v1-conversations',
    nav: async (page) => page.evaluate(() => window.deskV1Nav('conversations', { campaignId: 'camp-1' })) },
  { key: 'results', label: 'Results (camp-1)', wait: '.desk-v1-results',
    nav: async (page) => page.evaluate(() => window.deskV1Nav('results', { campaignId: 'camp-1' })) },
  { key: 'campaign-proposed', label: 'Campaign (camp-2, Proposed)', wait: '.desk-v1-rules-proposed',
    nav: async (page) => page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-2' })) },
  { key: 'start-sheet', label: 'Start sheet (camp-2)', wait: '.desk-v1-rules-sheet',
    nav: async (page) => page.evaluate(() => window.deskV1OpenStartSheet('camp-2')),
    teardown: async (page) => { await page.keyboard.press('Escape'); } },
];

// A1 + A12, all 3 tones (cheap: text/DOM queries only, no screenshot).
async function runA1AndA12Sweep(browser) {
  for (const tone of TONES) {
    const { ctx, page, pageErrors } = await newBootedPage(browser, tone);
    for (const s of SURFACES) {
      await s.nav(page);
      await page.waitForSelector(s.wait, { timeout: 8000 }).catch(() => {});

      const svgPaths = await page.$$eval('.modal-window[data-modal-id="__desk"] svg path', (els) => els.length);
      if (svgPaths === 0) ok(`[${tone.name}] A1: "${s.label}" has no SVG connector paths`);
      else fail(`[${tone.name}] A1 violated on "${s.label}": ${svgPaths} svg path element(s)`);

      const fullText = await page.textContent('.modal-window[data-modal-id="__desk"]').catch(() => '');
      const hits = await page.evaluate((t) => (window.DeskV1Kit ? window.DeskV1Kit.lintCopy(t) : []), fullText || '');
      if (!hits.length) ok(`[${tone.name}] A12: "${s.label}" copy lint clean`);
      else fail(`[${tone.name}] A12 violated on "${s.label}": ${JSON.stringify(hits)}`);

      if (s.teardown) await s.teardown(page);
    }
    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    if (uncaught.length) uncaught.forEach((e) => fail(`[${tone.name}] uncaught page error during A1/A12 sweep: ${e}`));
    await ctx.close();
  }
}

// A15 structural check (all 3 tones) + a real greyscale screenshot archive
// (default tone — a human still has to look; see docs/desk_v1_r0_exit.md).
async function runA15Sweep(browser) {
  for (const tone of TONES) {
    const { ctx, page, pageErrors } = await newBootedPage(browser, tone);
    for (const s of SURFACES) {
      await s.nav(page);
      await page.waitForSelector(s.wait, { timeout: 8000 }).catch(() => {});

      const labels = await page.$$eval('.modal-window[data-modal-id="__desk"] .desk-v1-state-label', (els) =>
        els.map((el) => ({
          glyph: (el.querySelector('.desk-v1-state-glyph') || {}).textContent || '',
          word: (el.querySelector('.desk-v1-state-word') || {}).textContent || '',
        })));
      const missing = labels.filter((l) => !l.glyph.trim() || !l.word.trim());
      if (missing.length === 0) ok(`[${tone.name}] A15: "${s.label}" — ${labels.length} status label(s), all carry glyph+word`);
      else fail(`[${tone.name}] A15 violated on "${s.label}": ${missing.length} status label(s) missing glyph or word: ${JSON.stringify(missing)}`);

      if (tone.name === 'default/dark') {
        await page.evaluate(() => { document.documentElement.style.filter = 'grayscale(1)'; });
        await page.waitForTimeout(30);
        await page.screenshot({ path: resolve(SHOT_DIR, `exit_greyscale_${s.key}.png`) });
        await page.evaluate(() => { document.documentElement.style.filter = ''; });
        ok(`[${tone.name}] A15: greyscale screenshot saved for "${s.label}": exit_greyscale_${s.key}.png`);
      }

      if (s.teardown) await s.teardown(page);
    }
    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    if (uncaught.length) uncaught.forEach((e) => fail(`[${tone.name}] uncaught page error during A15 sweep: ${e}`));
    await ctx.close();
  }
}

let browser, exitCode = 1;
const smokeResults = runOtherSmokes();
try {
  browser = await chromium.launch();
  await runA1AndA12Sweep(browser);
  await runA15Sweep(browser);
  exitCode = (bad === 0 && smokeResults.every((r) => r.passed)) ? 0 : 1;
} catch (e) {
  console.error('exit smoke error:', e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad === 0 ? `\nAll checks passed.` : `\n${bad} check(s) failed.`);
process.exit(exitCode);
