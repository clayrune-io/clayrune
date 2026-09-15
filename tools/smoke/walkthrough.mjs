#!/usr/bin/env node
/**
 * First-run walkthrough (static/js/walkthrough.js) end-to-end, on a fresh
 * install's DOM: every step's spotlight target (real element or demo
 * fallback) must actually resolve, or the step silently points at nothing
 * (the "highlight glowing around an empty rectangle" bug class this file's
 * own comments describe fixing more than once).
 *
 * Real headless boot (real index.html + real static/js/*.js, no network),
 * same hermetic shape as drag-to-hire.mjs / channel-mode-roster.mjs. The
 * fixture mirrors a fresh install: one project, the seeded "clayrune"
 * onboarding project the sample-tile step targets.
 *
 * `WT_STEPS` is module-scoped (walkthrough.js is a `<script type="module">`)
 * and not bridged to `window`, so this drives the tour the same way a user
 * does — startWalkthrough() then the bridged wtNext()/wtSkip() — and reads
 * step identity off the rendered .wt-title text rather than the array.
 *
 * RUN
 *   cd tools/smoke && node walkthrough.mjs
 * Screenshots (one per step) go to MC_SMOKE_SHOT_DIR if set, else skipped.
 * Exit 0 = every step resolved a target; 1 = a step regressed / harness error.
 */
import { readFileSync, readdirSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const SHOT_DIR = process.env.MC_SMOKE_SHOT_DIR || '';
if (SHOT_DIR) mkdirSync(SHOT_DIR, { recursive: true });

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

// Mirrors what seed_onboarding_on_startup() actually creates (guide_routes.py)
// — the real target of the "Project Tiles" step on a fresh install.
const CLAYRUNE_PROJECT = {
  id: 'clayrune', name: 'Clayrune', status: 'active', domain: 'general',
  _is_onboarding_project: true, emoji: '', description: '', summary: 'tour',
  current_task: 'Tour Clayrune', next_action: '', blocked: false, blocked_reason: null,
  activity_log: [], backlog: [], project_path: '/smoke/clayrune',
  last_updated: '2026-09-14T00:00:00Z', last_updated_relative: 'today',
  last_completed: null, live_agent: null, display_order: 0, provider: 'claude',
  use_streaming_agent: true, roster: [],
};

// Two installed providers so the 'provider-choice' step's skip() guard
// (skip when <=1 installed) actually clears — a claude-only machine (the
// common case) would otherwise never exercise this step's own render path.
const PROVIDERS_FIXTURE = {
  providers: [
    { name: 'claude', display_name: 'Claude Code', installed: true, in_use: true, default: true },
    { name: 'codex', display_name: 'Codex', installed: true, in_use: false, default: false },
  ],
  default: 'claude',
};

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

async function shoot(page, name) {
  if (!SHOT_DIR) return null;
  const path = resolve(SHOT_DIR, `walkthrough-${name}.png`);
  await page.screenshot({ path });
  return path;
}

// Reads the currently-rendered step (title/body text, whether a highlight
// box was drawn, whether the card is centered) without touching WT_STEPS.
async function readStep(page) {
  return page.evaluate(() => {
    const overlay = document.getElementById('wt-overlay');
    if (!overlay) return null;
    const card = overlay.querySelector('.wt-card');
    return {
      title: (overlay.querySelector('.wt-title') || {}).textContent || '',
      centered: !!(card && card.classList.contains('centered')),
      hasHighlight: !!overlay.querySelector('.wt-highlight'),
      progress: (overlay.querySelector('.wt-progress') || {}).textContent || '',
    };
  });
}

async function driveTour(page, viewportLabel, shotPaths) {
  const seen = [];
  await page.evaluate(() => window.startWalkthrough());
  await page.waitForTimeout(150);

  for (let i = 0; i < 20; i++) {
    const step = await readStep(page);
    if (!step) break; // overlay gone — tour ended
    if (seen.some((s) => s.title === step.title)) {
      fail(`[${viewportLabel}] step repeated / loop detected at "${step.title}"`);
      break;
    }
    seen.push(step);

    // Every step either centers (a plain intro/outro card, no target) or
    // must have drawn a highlight box — the two valid shapes wtShow can
    // produce. Anything else means targetEl fell through to null and the
    // step silently points at nothing.
    if (!step.centered && !step.hasHighlight) {
      fail(`[${viewportLabel}] "${step.title}" resolved no target (no highlight, not centered)`);
    } else {
      ok(`[${viewportLabel}] "${step.title}" — ${step.centered ? 'centered' : 'target resolved'} (${step.progress})`);
    }

    const shotName = `${viewportLabel}-${String(i).padStart(2, '0')}-${step.title.replace(/[^a-z0-9]+/gi, '-').toLowerCase().slice(0, 40)}`;
    const p = await shoot(page, shotName);
    if (p) shotPaths.push(p);

    await page.evaluate(() => window.wtNext());
    await page.waitForTimeout(200); // onEnter/onLeave + async fetches settle
  }
  return seen;
}

let browser, exitCode = 1;
const shotPaths = [];
try {
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

  // A fresh install auto-starts the tour 600ms after boot; that timer would
  // restart it mid-drive. Mark it done up front and start it by hand instead.
  await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([CLAYRUNE_PROJECT]) });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/walkthrough/sample-project') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, id: 'clayrune', existed: true }) });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/agent/providers') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(PROVIDERS_FIXTURE) });
    return route.abort();
  });

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 }).catch(() => {});

  console.log('Desktop pass (1400x900):');
  await driveTour(page, 'desktop', shotPaths);

  // Mobile pass: a fresh page/context at a phone width so the desktop-only
  // steps (sidebar, header, floor, desk, hivemind,
  // scheduler) self-skip via their skip() guards and #bottom-tab-bar's step
  // — invisible above 960px — actually gets exercised.
  const ctxMobile = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const pageMobile = await ctxMobile.newPage();
  await pageMobile.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
  pageMobile.on('pageerror', (e) => pageErrors.push('[mobile] ' + (e.message || String(e))));
  await pageMobile.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([CLAYRUNE_PROJECT]) });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/walkthrough/sample-project') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, id: 'clayrune', existed: true }) });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/agent/providers') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(PROVIDERS_FIXTURE) });
    return route.abort();
  });
  await pageMobile.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await pageMobile.waitForTimeout(800);

  console.log('Mobile pass (390x844):');
  await driveTour(pageMobile, 'mobile', shotPaths);
  await ctxMobile.close();

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) {
    fail('uncaught exception(s) during the tour:');
    uncaught.forEach((e) => console.error('       • ' + e));
  }

  await ctx.close();
  exitCode = bad === 0 ? 0 : 1;
  console.log(exitCode === 0
    ? '\n✅ PASS — every walkthrough step resolved a real target or demo fallback.'
    : `\n❌ FAIL — ${bad} problem(s).`);
  if (SHOT_DIR) {
    console.log(`Screenshots (${shotPaths.length}):`);
    shotPaths.forEach((p) => console.log('  ' + p));
  }
} catch (err) {
  console.error('❌ FAIL — smoke harness error:', err && err.stack ? err.stack : err);
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
