#!/usr/bin/env node
/**
 * Desk v1 (MC-1205, half 1) — the first open paints the home page at once.
 *
 * Before: opening the Desk with a slow workspace read left a header and one grey
 * line, "Loading your workspace...", until the read finished (measured 7 ms to
 * that line, 33 s to the home items). Now the home page's own frame is there in
 * the same frame as the click, with placeholder rows, and the data fills in.
 *
 *   1. SAME FRAME      sidebar click -> (same JS turn) the home frame is in the DOM:
 *                      column headings, placeholder rows, the crumb's Engagement
 *                      and Studio buttons; New campaign is held back; the words
 *                      "Loading your workspace" are still there.
 *   2. FILLS IN        the workspace answers -> the placeholders go, the project
 *                      shows, New campaign is enabled.
 *   3. ONE FAILURE     the engagement read never answers -> it does not hold the board.
 *   4. ERROR + RETRY   the workspace read fails -> the error sits INSIDE the home
 *                      frame (headings still there) with Try again; Try again
 *                      re-reads it and the home page fills in; no endless spinner.
 *
 * Fails on the code before MC-1205 at check 1 (no headings, no rows).
 *
 * RUN   cd tools/smoke && node desk-v1-first-open.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const STATIC = loadStaticJsCss(REPO_ROOT);

const PID = 'smoke_first_open';
const PROJECTS = [{
  id: PID, name: 'First open smoke', status: 'active', domain: 'general', emoji: '🧪',
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
const DRAFT = JSON.parse(JSON.stringify(loadFixtures().campaigns.find((c) => c.id === 'camp-1')));
Object.assign(DRAFT, { id: 'fo-draft', projectId: PID, state: 'draft' });
DRAFT.plan.title = 'First open draft'; delete DRAFT.map; delete DRAFT.subject;
const WORKSPACE = {
  projects: [{ id: PID, name: 'Server Project Quill', state: 'active', roster: [],
    presence: { replies: 'drafts', desk_agent: null, state: 'active' } }],
  campaigns: [DRAFT], accounts: [], pieces: [],
};

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

// `workspace` is a handler so a case can hold, fail or answer the read on demand.
async function newPage(browser, { workspace, engagementHangs = false }) {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return J(PROJECTS);
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true, user_timezone: '' });
    if (path === '/api/characters') return J([]);
    if (path === '/api/desk/workspace') return workspace(J);
    if (path === '/api/desk/engagement') { if (engagementHangs) return undefined; return J({ conversations: [] }); }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });
  return { ctx, page, pageErrors };
}

// Click the Desk and read the DOM in the SAME JS turn: no frame, no request can
// have come back in between, so whatever is there came from memory.
async function clickAndLook(page) {
  return page.evaluate(() => {
    document.querySelector('.sidebar-item[data-nav="social"]').click();
    const body = document.getElementById('desk-v1-body');
    const crumbBtns = [...document.querySelectorAll('#desk-v1-crumb-tools button')];
    const newBtn = crumbBtns.find((b) => /New campaign/.test(b.textContent));
    return {
      shell: !!document.querySelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell'),
      text: body ? body.innerText : '',
      rows: body ? body.querySelectorAll('.desk-v1-loading-row').length : 0,
      crumbBtns: crumbBtns.map((b) => b.textContent.trim()),
      newDisabled: newBtn ? newBtn.disabled : null,
    };
  });
}
const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });

const browser = await chromium.launch();
try {
  // ── 1-3: held workspace, engagement never answers ──────────────────────────
  {
    let release;
    const gate = new Promise((r) => { release = r; });
    const { ctx, page, pageErrors } = await newPage(browser, {
      engagementHangs: true,
      workspace: async (J) => { await gate; return J(WORKSPACE); },
    });
    const first = await clickAndLook(page);
    first.shell ? ok('the Desk frame is in the DOM in the same JS turn as the click') : fail('no Desk frame at the click');
    (/CAMPAIGN/.test(first.text) && /GOAL PROGRESS/.test(first.text) && /NEEDS YOU/.test(first.text))
      ? ok('home column headings are painted before any data') : fail('no home headings at the click: ' + JSON.stringify(first.text.slice(0, 120)));
    first.rows >= 3 ? ok(`${first.rows} placeholder rows are painted at the click`) : fail(`placeholder rows at the click: ${first.rows}`);
    /Loading your workspace/.test(first.text) ? ok('the loading words are still there') : fail('loading words missing');
    (first.crumbBtns.some((t) => /Engagement/.test(t)) && first.crumbBtns.some((t) => /Studio/.test(t)))
      ? ok('crumb navigation (Studio, Connections, Engagement) is painted at the click') : fail('crumb buttons at the click: ' + first.crumbBtns.join('|'));
    first.newDisabled === true ? ok('New campaign is held back while the workspace is loading') : fail('New campaign not disabled while loading: ' + first.newDisabled);

    release();
    await settle(page, () => /Server Project Quill/.test(document.body.innerText));
    ok('the workspace answers -> the project shows (the engagement read is still hung)');
    const after = await page.evaluate(() => {
      const b = [...document.querySelectorAll('#desk-v1-crumb-tools button')].find((x) => /New campaign/.test(x.textContent));
      return { loading: !!document.querySelector('.desk-v1-loading-board'), newDisabled: b ? b.disabled : null };
    });
    !after.loading ? ok('placeholders are gone once the data is in') : fail('placeholders still painted after the load');
    after.newDisabled === false ? ok('New campaign is enabled once the data is in') : fail('New campaign still disabled: ' + after.newDisabled);
    pageErrors.length ? fail('page errors: ' + pageErrors.join(' | ')) : ok('zero page errors');
    await ctx.close();
  }

  // ── 4: the workspace fails, then Try again works ───────────────────────────
  {
    let failing = true; let hits = 0;
    const { ctx, page, pageErrors } = await newPage(browser, {
      workspace: (J) => { hits++; return failing ? J({ error: 'workspace store is down' }, 500) : J(WORKSPACE); },
    });
    await clickAndLook(page);
    await settle(page, () => !!document.querySelector('[data-store-gate="error"]'));
    const err = await page.evaluate(() => {
      const b = document.getElementById('desk-v1-body');
      return { text: b.innerText, retry: !!b.querySelector('[data-store-retry]'), spinner: !!b.querySelector('.desk-v1-loading-row') };
    });
    /workspace store is down/.test(err.text) ? ok("the failure shows the server's own words") : fail('error text: ' + err.text.slice(0, 120));
    /CAMPAIGN/.test(err.text) ? ok('the error sits inside the home frame (headings still there)') : fail('error replaced the whole page');
    err.retry ? ok('Try again is offered') : fail('no Try again');
    !err.spinner ? ok('no placeholder rows left spinning on a failure') : fail('placeholder rows still painted after the failure');
    failing = false;
    await page.click('[data-store-retry]');
    await settle(page, () => /Server Project Quill/.test(document.body.innerText));
    ok(`Try again re-reads the workspace (${hits} reads) and the home page fills in`);
    pageErrors.length ? fail('page errors: ' + pageErrors.join(' | ')) : ok('zero page errors');
    await ctx.close();
  }
} finally {
  await browser.close();
}
if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('\nall checks passed');
