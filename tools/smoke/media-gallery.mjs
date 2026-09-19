#!/usr/bin/env node
/**
 * MC-956 regression: the Media view (left sidebar → Media, static/js/media.js)
 *
 *  (a) could not scroll AT ALL — #media-body sat inside .modal-content, which
 *      is `overflow:hidden` by design (every surface owns its own scroll
 *      area, see app.css's "THE BOARD SCROLLS" comment on .fl-body), and
 *      #media-body carried none of the scroll-owning CSS. On a project with
 *      156 media items that clipped everything past the first couple of
 *      rows, with no way to reach the rest — reported as "can't scroll up
 *      to reach older files". Fixed by giving #media-body the existing
 *      `.modal-scroll-body` class, the same reusable scroll-owner every
 *      other modal body already uses (see modal-manager.js's
 *      savedScrollTop handling) — at both desktop and 390px mobile width.
 *
 *  (b) ~41% of image tiles on the real mission_control index point at files
 *      that were deleted after being indexed (scratch dirs / torn-down agent
 *      worktrees) — genuinely gone, not a detection bug. mc/media.py's
 *      list_media() now flags each image row `missing: true/false` up front
 *      (server-side; no more waiting on a page full of <img onerror> round
 *      trips before it's known which tiles are dead), and the gallery hides
 *      missing tiles by default with a "Show missing (N)" toggle to reveal
 *      them. Nothing is ever removed from the underlying index.
 *
 * HOW THIS RUNS — hermetic, not against the live :5199 server
 * -------------------------------------------------------------------------
 * This session's worktree and the running localhost:5199 process are two
 * separate checkouts (a worktree edits its own copy of static/js/media.js;
 * the live server keeps serving whatever it booted with until it restarts).
 * Driving :5199 here would test a stale file and silently prove nothing.
 * Instead this loads THIS worktree's real static/index.html + static/**
 * straight off disk via Playwright request interception (same technique as
 * boot-smoke.mjs) and mocks only the API layer — so it exercises the exact
 * code in this branch, real DOM/CSS included.
 *
 * RUN: node media-gallery.mjs
 */
import { readFileSync, existsSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, extname } from 'node:path';
import { chromium } from 'playwright';
import { mkdtempSync } from 'node:fs'; import { tmpdir } from 'node:os'; import { join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const ORIGIN = 'http://mc.smoke.test';

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.json': 'application/json; charset=utf-8',
};

// Real PROJECT fixture — one project, id "mc_smoke", so the Media surface has
// something to pick.
const PROJECTS = [{
  id: 'mc_smoke', name: 'Mission Control (smoke)', status: 'active', domain: 'general',
  emoji: '\ud83e\uddea', description: '', summary: '', current_task: 'Idle', next_action: '',
  blocked: false, blocked_reason: null, activity_log: [], backlog: [], project_path: '',
  last_updated: '2026-09-18T00:00:00Z', last_updated_relative: 'today', last_completed: null,
  live_agent: null, display_order: 0, provider: 'claude', use_streaming_agent: true,
}];

// Real reproduction data for the SCROLL check: 60 image entries — plenty to
// overflow a 700x80vh modal at both desktop and 390px widths, mirroring the
// real mission_control index (156 items) that actually clips.
function scrollFixtureItems() {
  const items = [];
  for (let i = 0; i < 60; i++) {
    items.push({ kind: 'image', path: `C:/fake/shot-${i}.png`, ts: 2000 - i, task: `shot ${i}`, missing: false });
  }
  return items;
}

// Real reproduction data for the MISSING-toggle check: 2 present + 3 missing,
// same field mc/media.py's list_media() now emits.
const MISSING_FIXTURE_ITEMS = [
  { kind: 'image', path: 'C:/fake/present1.png', ts: 1000, task: 'a', missing: false },
  { kind: 'image', path: 'C:/fake/present2.png', ts: 999, task: 'b', missing: false },
  { kind: 'image', path: 'C:/fake/gone1.png', ts: 998, task: 'c', missing: true },
  { kind: 'image', path: 'C:/fake/gone2.png', ts: 997, task: 'd', missing: true },
  { kind: 'image', path: 'C:/fake/gone3.png', ts: 996, task: 'e', missing: true },
];

let mediaItems = scrollFixtureItems();

function router(route) {
  const req = route.request();
  const path = new URL(req.url()).pathname;
  const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json; charset=utf-8', body: JSON.stringify(body) });

  if (path === '/api/projects') return json(PROJECTS);
  if (path === '/api/config') return json({});
  if (path === '/api/project/mc_smoke/media') return json({ items: mediaItems, count: mediaItems.length, forward_only: true });
  if (path.startsWith('/api/serve-image')) {
    // Mirror the real endpoint: a live file serves, a torn-down/deleted one
    // 404s. Otherwise every tile here would 404 (none of these paths are
    // real files) and the client-side <img onerror> fallback would mask
    // whether the server-computed `missing` flag is what actually drove the
    // hide/reveal — this fixture needs BOTH signals to disagree correctly.
    const qp = new URL(req.url()).searchParams.get('path') || '';
    const item = mediaItems.find(m => m.kind === 'image' && m.path === qp);
    if (item && !item.missing) {
      const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAYAAACp8Z5+AAAAEklEQVR4nGP8z8Dwn4EIwDiqEAAA//8DABjcA0/9b3pPAAAAAElFTkSuQmCC', 'base64');
      return route.fulfill({ status: 200, contentType: 'image/png', body: PNG });
    }
    return route.fulfill({ status: 404, body: 'not found' });
  }

  if (path === '/' || path === '/index.html') {
    return route.fulfill({ status: 200, contentType: MIME['.html'], body: readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8') });
  }
  if (path.startsWith('/static/')) {
    const full = resolve(REPO_ROOT, path.replace(/^\//, ''));
    if (full.startsWith(REPO_ROOT) && existsSync(full) && statSync(full).isFile()) {
      return route.fulfill({ status: 200, contentType: MIME[extname(full)] || 'application/octet-stream', body: readFileSync(full) });
    }
    return route.abort();
  }
  return route.abort();   // every other API/CDN call: SPA's own fallback handles it
}

const ok = (m) => console.log('  \u2713 ' + m);
let bad = 0;
const fail = (m) => { console.error('  \u2717 ' + m); bad++; };

async function openMedia(page) {
  await page.route('**/*', router);
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 20000 });
  await page.evaluate(() => window.sidebarNav('media'));
  await page.waitForSelector('.media-grid .media-tile', { timeout: 20000 });
  return pageErrors;
}

const ctx = await chromium.launchPersistentContext(mkdtempSync(join(tmpdir(), 'mc956-')), {
  args: ['--remote-debugging-port=9791'], viewport: { width: 1280, height: 900 },
});
try {
  const page = ctx.pages()[0] || await ctx.newPage();

  // ── (a) scroll: desktop ────────────────────────────────────────────────
  const pageErrors = await openMedia(page);
  ok('Media opened for the fixture project, tiles rendered');

  const scrollHost = await page.evaluate(() => {
    const el = document.getElementById('media-body');
    return el ? { hasClass: el.classList.contains('modal-scroll-body'),
      scrollHeight: el.scrollHeight, clientHeight: el.clientHeight } : null;
  });
  scrollHost && scrollHost.hasClass ? ok('#media-body carries .modal-scroll-body')
    : fail('#media-body is missing the scroll-owning class');
  scrollHost && scrollHost.scrollHeight > scrollHost.clientHeight
    ? ok(`content overflows the body (scrollHeight ${scrollHost.scrollHeight} > clientHeight ${scrollHost.clientHeight})`)
    : fail(`not enough content to prove scrolling (scrollHeight=${scrollHost && scrollHost.scrollHeight}, clientHeight=${scrollHost && scrollHost.clientHeight})`);

  // Reproduce the reported symptom directly: scroll down to reveal
  // further-down (older) tiles, then scroll back UP and confirm it moves.
  await page.evaluate(() => { document.getElementById('media-body').scrollTop = 100000; });
  const afterDown = await page.evaluate(() => document.getElementById('media-body').scrollTop);
  afterDown > 0 ? ok(`scrolled down to ${afterDown}px`) : fail('scrollTop did not move on scroll-down');
  await page.evaluate(() => { document.getElementById('media-body').scrollTop = 0; });
  const afterUp = await page.evaluate(() => document.getElementById('media-body').scrollTop);
  afterUp === 0 ? ok('scrolled back UP to the top (0px) \u2014 the reported symptom')
    : fail(`scroll-up did not reach the top (scrollTop=${afterUp})`);

  // ── (a) scroll: 390px mobile width ────────────────────────────────────
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  const mobileScroll = await page.evaluate(() => {
    const el = document.getElementById('media-body');
    return el ? { scrollHeight: el.scrollHeight, clientHeight: el.clientHeight } : null;
  });
  mobileScroll && mobileScroll.scrollHeight > mobileScroll.clientHeight
    ? ok(`390px: content still overflows (scrollHeight ${mobileScroll.scrollHeight} > clientHeight ${mobileScroll.clientHeight})`)
    : fail(`390px: no overflow to scroll (scrollHeight=${mobileScroll && mobileScroll.scrollHeight}, clientHeight=${mobileScroll && mobileScroll.clientHeight})`);
  await page.evaluate(() => { document.getElementById('media-body').scrollTop = 100000; });
  const mobileDown = await page.evaluate(() => document.getElementById('media-body').scrollTop);
  await page.evaluate(() => { document.getElementById('media-body').scrollTop = 0; });
  const mobileUp = await page.evaluate(() => document.getElementById('media-body').scrollTop);
  (mobileDown > 0 && mobileUp === 0) ? ok('390px: scroll down then back up both work')
    : fail(`390px: scroll broken (down=${mobileDown}, up-after=${mobileUp})`);
  await page.setViewportSize({ width: 1280, height: 900 });

  // ── (b) "file missing" toggle ──────────────────────────────────────────
  mediaItems = MISSING_FIXTURE_ITEMS;
  await page.evaluate(() => window.loadMedia());
  await page.waitForTimeout(400);

  const hiddenState = await page.evaluate(() => ({
    tiles: document.querySelectorAll('.media-grid .media-tile').length,
    toggleVisible: document.getElementById('media-missing-toggle').style.display !== 'none',
    label: document.getElementById('media-missing-label').textContent,
    checked: document.querySelector('#media-missing-toggle input').checked,
  }));
  hiddenState.tiles === 2 ? ok('default view hides missing tiles (2 shown of 5)')
    : fail(`expected 2 tiles hidden-by-default, saw ${hiddenState.tiles}`);
  hiddenState.toggleVisible ? ok('missing-toggle is visible when there is something missing')
    : fail('missing-toggle did not appear');
  /Show missing \(3\)/.test(hiddenState.label) ? ok(`toggle label reads "${hiddenState.label}"`)
    : fail(`toggle label wrong: "${hiddenState.label}"`);
  hiddenState.checked === false ? ok('toggle defaults to unchecked (hide missing)')
    : fail('toggle defaulted to checked');

  await page.click('#media-missing-toggle input');
  await page.waitForTimeout(200);
  const revealed = await page.evaluate(() => ({
    tiles: document.querySelectorAll('.media-grid .media-tile').length,
    dead: document.querySelectorAll('.media-grid .media-tile.media-dead').length,
  }));
  revealed.tiles === 5 ? ok('checking the toggle reveals all 5 tiles')
    : fail(`expected 5 tiles after revealing, saw ${revealed.tiles}`);
  revealed.dead === 3 ? ok('the 3 revealed missing tiles carry .media-dead ("file missing")')
    : fail(`expected 3 .media-dead tiles, saw ${revealed.dead}`);

  const realErrors = pageErrors.filter(e => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  realErrors.length === 0 ? ok('no uncaught JS error throughout')
    : realErrors.forEach((e) => fail('uncaught: ' + e));
} finally {
  await ctx.close();
}
console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
