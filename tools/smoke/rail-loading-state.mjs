#!/usr/bin/env node
/**
 * Ron, 2026-09-28: after a server restart or page refresh, the conversation
 * rail said "No conversations with Dave yet." (Channel mode) — or "No
 * conversations yet." in Chats mode — while the FIRST /agent/log and
 * /conversations fetches were still in flight. A real empty result and "we
 * haven't heard back yet" rendered identically.
 *
 * ROOT CAUSE: the rail's empty-state branches (_railChannelHTML's per-person
 * text, agentPanelHTML's Chats-mode branch, both static/js/conversation.js)
 * rendered off `agentLogCache[pid]` / `conversationsCache[pid]` being falsy,
 * with no signal distinguishing "still loading" from "loaded and genuinely
 * empty" from "failed to load". Worse, upsertConversationCache creates
 * conversationsCache[pid] BEFORE any server response lands (optimistic local
 * rows), so "the cache key exists" was never a safe loaded signal either.
 * Dave (a hired roster member) can appear on the Channel roster the instant
 * /api/projects lands — independent of both these fetches — so clicking him
 * right after a refresh hit exactly this window.
 *
 * FIX (static/js/agent-log.js): _railAgentLogStatus / _railConversationsStatus
 * track per-project completion of each fetch, written ONLY when a response
 * actually lands (success or failure), never optimistically. railEmptyStateHTML()
 * — exposed on window per discovery_es_module_cross_boundary_globals, since
 * conversation.js is a different ES module — renders "Loading conversations…"
 * before either has completed, "Couldn't load conversations." if neither
 * cache was EVER populated and one failed, else the caller's real empty text.
 * conversation.js's three empty-state render sites (Chats-mode rail,
 * per-person Channel text, thread-shell heading) now call it.
 *
 * Hermetic boot (real index.html + real static/js/*.js served verbatim, no
 * real backend) — same shape as channel-mode-roster.mjs. Each scenario opens
 * a FRESH browser context so agentLogCache/conversationsCache/the new status
 * maps all start undefined, exactly like a real page load, and intercepts
 * /api/project/<pid>/agent/log + .../conversations to control precisely when
 * and how those two responses land.
 *
 * RUN: cd tools/smoke && node rail-loading-state.mjs
 * MUST FAIL ON CURRENT MASTER — master has no loading/failure text at all;
 * the empty string is static regardless of fetch state, so scenario 1's
 * "during the fetch" assertion (and scenario 3's failure-text assertion)
 * cannot pass there.
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
const PID = 'smoke_rail_loading';

// Serve every real static/js/*.js and static/css/*.css verbatim — a new
// module can never silently fall through to route.abort() the way a
// hand-maintained map would (same reasoning as channel-mode-roster.mjs).
const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

const DAVE = { name: 'dave', scope: 'global', display_name: 'dave', agent_name: 'Dave', avatar: 'fig:guard' };
const CHARACTERS_JSON = JSON.stringify([DAVE]);

function fixtureProject() {
  return {
    id: PID, name: 'Rail Loading Smoke', status: 'active', domain: 'general', emoji: '\u{1F9EA}',
    description: '', summary: '', current_task: 'Idle', next_action: '',
    blocked: false, blocked_reason: null, activity_log: [], backlog: [],
    project_path: '/smoke/' + PID, last_updated: '2026-09-28T00:00:00Z',
    last_updated_relative: 'today', last_completed: null, live_agent: null,
    display_order: 0, provider: 'claude', use_streaming_agent: true,
    distiller_mode: 'proposed', distiller_min_recurrence: 3,
    distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
    distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
    distiller_skip_errors: true,
    // Dave hired onto the roster — this is what puts him on the Channel rail
    // independent of conversationsCache/agentLogCache (see _channelRoster).
    roster: [{ character: 'global:dave', hired_at: '2026-09-20T00:00:00Z', hired_by: 'test', removed_at: null }],
  };
}
const PROJECTS_JSON = JSON.stringify([fixtureProject()]);

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

// One fresh browser context per scenario: module-level JS state
// (agentLogCache, conversationsCache, and the new _railAgentLogStatus /
// _railConversationsStatus maps) must start undefined every time, exactly
// like a real page refresh — that IS the bug this fix targets.
async function freshPage(browser, { logMode, convMode }) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

  const respond = async (route, mode) => {
    if (mode.delayMs) await new Promise((r) => setTimeout(r, mode.delayMs));
    if (mode.status && mode.status >= 400) {
      return route.fulfill({ status: mode.status, contentType: 'application/json', body: '{"error":"smoke-injected failure"}' });
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mode.body ?? []) });
  };

  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (path === `/api/project/${PID}/agent/log`) return respond(route, logMode);
    if (path === `/api/project/${PID}/conversations`) return respond(route, convMode);
    return route.abort();
  });

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });
  return { ctx, page, pageErrors };
}

const railEmptyText = (page, scope) => page.$eval(`${scope}.agent-rail-list .agent-rail-empty`, (el) => el.textContent.trim()).catch(() => null);
const channelEmptyText = (page, scope) => page.$eval(`${scope}.channel-expanded .agent-rail-empty`, (el) => el.textContent.trim()).catch(() => null);

let browser, exitCode = 1;
try {
  browser = await chromium.launch();

  // ── 1. LOADING → real empty, Channel mode. Dave is on the roster the
  // instant /api/projects lands, independent of the two fetches under test —
  // clicking him while they're still in flight is EXACTLY Ron's report. ────
  {
    const { ctx, page } = await freshPage(browser, {
      logMode: { delayMs: 2000, body: [] }, convMode: { delayMs: 2000, body: [] },
    });
    const scope = `.modal-window[data-modal-id="${PID}"] `;
    await page.evaluate((pid) => openProjectModal(pid), PID);
    await page.waitForSelector(`${scope}.agent-rail`, { timeout: 5000 });
    await page.click(`${scope}.rail-mode-btn >> text=Channel`);
    await page.waitForSelector(`${scope}.channel-row:has-text("Dave")`, { timeout: 5000 });
    await page.click(`${scope}.channel-row`);
    await page.waitForSelector(`${scope}.channel-expanded`, { timeout: 3000 });

    const during = await channelEmptyText(page, scope);
    during === 'Loading conversations…'
      ? ok(`Channel mode shows "Loading conversations…" while the fetch is in flight (was: "${during}")`)
      : fail(`Channel mode should show the loading text while in flight, got: "${during}"`);

    await page.waitForFunction((sel) => {
      const el = document.querySelector(sel);
      return el && el.textContent.trim() !== 'Loading conversations…';
    }, `${scope}.channel-expanded .agent-rail-empty`, { timeout: 6000 }).catch(() => {});
    const after = await channelEmptyText(page, scope);
    after === 'No conversations with Dave yet.'
      ? ok('once both fetches land empty, Channel mode flips to the real empty text')
      : fail(`after the fetch lands empty, expected "No conversations with Dave yet.", got: "${after}"`);

    await ctx.close();
  }

  // ── 2. EMPTY (fast fixture), Chats mode: both fetches resolve immediately
  // with []. No stuck loading text, no false failure text. ─────────────────
  {
    const { ctx, page } = await freshPage(browser, { logMode: { body: [] }, convMode: { body: [] } });
    const scope = `.modal-window[data-modal-id="${PID}"] `;
    await page.evaluate((pid) => openProjectModal(pid), PID);
    await page.waitForSelector(`${scope}.agent-rail`, { timeout: 5000 });
    await page.waitForTimeout(400);
    const text = await railEmptyText(page, scope);
    text === 'No conversations yet.'
      ? ok('Chats mode: an immediate empty fixture renders the real empty text, not the loading text')
      : fail(`expected "No conversations yet.", got: "${text}"`);
    await ctx.close();
  }

  // ── 3. FAILURE, Chats mode: both fetches 500, nothing ever cached — must
  // say the load failed, not claim the project has no conversations. ───────
  {
    const { ctx, page } = await freshPage(browser, { logMode: { status: 500 }, convMode: { status: 500 } });
    const scope = `.modal-window[data-modal-id="${PID}"] `;
    await page.evaluate((pid) => openProjectModal(pid), PID);
    await page.waitForSelector(`${scope}.agent-rail`, { timeout: 5000 });
    await page.waitForTimeout(400);
    const text = await railEmptyText(page, scope);
    text === "Couldn't load conversations."
      ? ok('Chats mode: two failed fetches with nothing cached render the failure text')
      : fail(`expected "Couldn't load conversations.", got: "${text}"`);
    await ctx.close();
  }

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0
    ? '\n✅ PASS — rail shows "Loading…" while in flight, the real empty text once landed empty, and a failure text when nothing ever loaded.'
    : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
