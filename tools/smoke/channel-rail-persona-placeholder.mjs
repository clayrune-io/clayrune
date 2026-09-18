#!/usr/bin/env node
/**
 * Channel-rail regression: a new chat's client-side placeholder row must
 * carry its persona from the moment it's created, and a later status poll
 * must repair a row that started without one.
 *
 * WHY THIS EXISTS
 * ----------------
 * `upsertConversationCache` (static/js/agent-log.js) is what seeds a chat's
 * row into `conversationsCache` the instant it's dispatched — before the
 * server's own `/conversations` list has a chance to include it (resume-
 * preview.js:464). Until this fix it never accepted `character`/`identity`
 * at all, so a placeholder row for a persona chat had NEITHER field.
 * `_convCharKey` (conversation.js) returns '' for such a row and
 * `_channelRoster` skips it (spec §10's "no retroactive attribution" guard
 * for unattributed rows caught this legitimate persona row too), so the
 * chat was invisible under Channel mode until a hard refresh replaced the
 * placeholder with the server's correctly-attributed one.
 *
 * The follow-up path (conversation.js ~4827) and the status poll
 * (~5153) hit the same upsert with only status/ids/turns — never persona —
 * so a row that started characterless stayed that way forever, even once
 * the server-side session clearly had a character (Mode B's turn_complete/
 * idle ticks never re-fetch /conversations to pick up the correction).
 *
 * This is a real headless boot (real index.html + real static/js/*.js
 * served verbatim, no server, no network) — same hermetic shape as
 * channel-mode-roster.mjs / conversation-persona-filter.mjs — driving the
 * exact call sites (`upsertConversationCache`, bridged on `window`) rather
 * than an extracted copy, so a future edit is exercised as shipped.
 *
 * RUN
 *   cd tools/smoke && node channel-rail-persona-placeholder.mjs
 * Exit 0 = both cases behave; 1 = a case regressed / harness error.
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
const PID = 'smoke_placeholder';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

function fixtureProject(id, name) {
  return {
    id, name, status: 'active', domain: 'general', emoji: '🧪',
    description: '', summary: '', current_task: 'Idle', next_action: '',
    blocked: false, blocked_reason: null, activity_log: [], backlog: [],
    project_path: '/smoke/' + id, last_updated: '2026-09-02T00:00:00Z',
    last_updated_relative: 'today', last_completed: null, live_agent: null,
    display_order: 0, provider: 'claude', use_streaming_agent: true,
    distiller_mode: 'proposed', distiller_min_recurrence: 3,
    distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
    distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
    distiller_skip_errors: true,
  };
}
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Placeholder Smoke')]);
// The catalog record the composer resolves a pick against. Case 3 goes through
// resolveCharacterMeta (the real dispatch path), so the avatar must come from
// HERE -- the hand-built FENN/MARLOW fixtures below already carry one and so
// could never catch a resolver that drops it.
const DAVE_REC = { name: 'dave', scope: 'global', display_name: 'dave', agent_name: 'Dave', avatar: 'fig:guard' };
const CHARACTERS_JSON = JSON.stringify([DAVE_REC]);

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });

  if (pageErrors.length) {
    pageErrors.forEach((e) => fail('uncaught page error during boot: ' + e));
  } else {
    ok('app booted clean, no uncaught exceptions');
  }

  const FENN = { name: 'code-reviewer', scope: 'project', display_name: 'code-reviewer', agent_name: 'Fenn', avatar: 'fig:scholar' };

  await page.evaluate((pid) => { openProjectModal(pid); }, PID);
  await page.waitForSelector(`.modal-window[data-modal-id="${PID}"] .agent-rail`, { timeout: 5000 });
  const scope = `.modal-window[data-modal-id="${PID}"] `;
  await page.click(`${scope}.rail-mode-btn >> text=Channel`);
  await page.waitForSelector(`${scope}.channel-empty, ${scope}.channel-row`, { timeout: 3000 });

  const rosterNames = async () => page.$$eval(`${scope}.channel-row .conv-name`, (els) => els.map((el) => el.textContent.trim()));

  // ── Case 1: a placeholder started WITH a persona must appear immediately,
  // the moment the dispatch-time upsert runs — no poll, no refresh. ─────────
  await page.evaluate(({ pid, fenn }) => {
    upsertConversationCache(pid, '', 'review this diff', 'running', {
      mcSessionId: 'mc-fenn-new', provider: 'claude', live: true, character: fenn,
    });
    refreshModalById(pid);
  }, { pid: PID, fenn: FENN });
  await page.waitForTimeout(150);
  (await rosterNames()).includes('Fenn')
    ? ok('a placeholder created WITH a persona appears in the Channel roster immediately')
    : fail(`Fenn should be on the roster right after the persona-carrying dispatch upsert, got: ${JSON.stringify(await rosterNames())}`);

  // ── Case 2: a placeholder started WITHOUT one (the historical bug: dispatch
  // races ahead of the first status poll) is invisible until repaired... ────
  await page.evaluate((pid) => {
    upsertConversationCache(pid, '', 'plan the release', 'running', {
      mcSessionId: 'mc-marlow-new', provider: 'claude', live: true,
    });
    refreshModalById(pid);
  }, PID);
  await page.waitForTimeout(150);
  (await rosterNames()).includes('Marlow')
    ? fail('a characterless placeholder should NOT be on the roster yet (proves the next step is a real repair, not a no-op)')
    : ok('a characterless placeholder stays off the roster until repaired (matches the pre-fix symptom)');

  // ── ...and ONE POLL (status poll shape: same mc_session_id, no text, meta
  // now carries the resolved character) must repair it in place. ───────────
  const MARLOW = { name: 'prd-writer', scope: 'global', display_name: 'prd-writer', agent_name: 'Marlow', avatar: 'fig:wizard' };
  await page.evaluate(({ pid, marlow }) => {
    upsertConversationCache(pid, '', '', 'running', {
      mcSessionId: 'mc-marlow-new', provider: 'claude', live: true, touch: false, character: marlow,
    });
    refreshModalById(pid);
  }, { pid: PID, marlow: MARLOW });
  await page.waitForTimeout(150);
  (await rosterNames()).includes('Marlow')
    ? ok('one status-poll-shaped upsert repairs a characterless row into the roster')
    : fail(`Marlow should appear after the repairing poll, got: ${JSON.stringify(await rosterNames())}`);

  // ── A later poll carrying no character must NOT blank out one already set —
  // upsertConversationCache must never overwrite a present value with empty. ─
  await page.evaluate((pid) => {
    upsertConversationCache(pid, '', '', 'idle', {
      mcSessionId: 'mc-marlow-new', provider: 'claude', live: false, touch: false,
    });
    refreshModalById(pid);
  }, PID);
  await page.waitForTimeout(150);
  (await rosterNames()).includes('Marlow')
    ? ok('a later characterless poll does not clobber an already-resolved persona')
    : fail('Marlow disappeared from the roster after a characterless follow-up poll — a present value was overwritten with empty');

  // ── Case 3: a chat dispatched from the composer builds its placeholder
  // persona with resolveCharacterMeta. That meta must carry the avatar, or
  // the newest (placeholder) row wins the roster and the row renders an empty
  // face until the next /conversations poll (seen live 2026-09-18). ─────────
  await page.evaluate(async (pid) => {
    reloadCharacters(pid);
    for (let i = 0; i < 50 && !window.characterCacheFor(pid).length; i++) await new Promise((r) => setTimeout(r, 20));
    upsertConversationCache(pid, '', 'what did we do', 'running', {
      mcSessionId: 'mc-dave-new', provider: 'claude', live: true,
      character: window.resolveCharacterMeta(pid, 'global:dave'),
    });
    refreshModalById(pid);
  }, PID);
  await page.waitForTimeout(150);
  const daveFace = await page.$$eval(`${scope}.channel-row`, (rows) => {
    const r = rows.find((el) => (el.querySelector('.conv-name') || {}).textContent?.trim() === 'Dave');
    return r ? { found: true, img: !!r.querySelector('img, .av-fig, [style*="background-image"]'), html: r.innerHTML.slice(0, 300) } : { found: false };
  });
  !daveFace.found
    ? fail(`Dave should be on the roster after a composer dispatch, got: ${JSON.stringify(await rosterNames())}`)
    : daveFace.img
      ? ok('a composer-dispatched placeholder row renders the persona avatar, not an empty face')
      : fail('Dave row rendered with no avatar -- resolveCharacterMeta dropped it: ' + daveFace.html);

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) {
    uncaught.forEach((e) => fail('uncaught exception during interaction: ' + e));
  }

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0
    ? '\n✅ PASS — persona-carrying placeholders show immediately, characterless ones are repaired by one poll, and a present persona survives a later characterless poll.'
    : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
