#!/usr/bin/env node
/**
 * ws_005 regression: the Channel rail could not represent a session with no
 * persona — which meant the operator's own DEFAULT agent (e.g. "Vector") was
 * structurally invisible on it. `docs/research/_ws005_channel_roster_audit.md`
 * traced it to `_convCharKey` (static/js/conversation.js) returning `''` for
 * any row whose `character` was falsy, and `_channelRoster` skipping rows
 * with an empty key. Ron's report: "the Channel view is not showing all
 * active agents (Vector is never shown there)."
 *
 * THE BUG THIS CATCHES THAT channel-mode-roster.mjs DOES NOT
 * ---------------------------------------------------------------------
 * `channel-mode-roster.mjs` already covers "an unattributed chat does not
 * leak onto the roster" — but its fixture rows never carry the `identity`
 * field the real server now emits (mc/identity.py), so that assertion is
 * about a DIFFERENT case: a row the server itself has no answer for. This
 * test seeds rows the way the FIXED server actually shapes them —
 * `character: null` + `identity: {key:'default:', name:'Vector', ...}` —
 * the exact live-evidence shape from the audit (14 of 15 real
 * `/conversations` rows, 7 of 8 real `/agent/status` sessions carried
 * `character: null` the day this was diagnosed). Against the PARENT commit's
 * conversation.js (no `identity`-aware `_convCharKey`, no `_convCharFor`
 * shape-normalization), Vector's row never renders — this test fails there
 * and passes here.
 *
 * Also covers the two roster-membership siblings from the audit's arm 4:
 * a delegated session with no persona (MC-925, `identity.key === 'unnamed:'`)
 * must count while genuinely working, and must NOT persist to the Bench once
 * it stops — "live only, never benched."
 *
 * Real headless boot (real index.html + real static/js/*.js served verbatim,
 * no server, no network) — same hermetic shape as channel-mode-roster.mjs,
 * which the ws_005 audit itself judged sufficiently "real": it drives actual
 * `openProjectModal → refreshModal → _railChannelHTML` and asserts on real
 * DOM, not a render function called by hand.
 *
 * RUN
 *   cd tools/smoke && node channel-default-agent-visible.mjs
 * Exit 0 = the default agent is visible with real sessions behind it; 1 = a
 * case regressed / harness error.
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
const PID = 'smoke_default_agent';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

function fixtureProject(id, name) {
  return {
    id, name, status: 'active', domain: 'general', emoji: '🧪',
    description: '', summary: '', current_task: 'Idle', next_action: '',
    blocked: false, blocked_reason: null, activity_log: [], backlog: [],
    project_path: '/smoke/' + id, last_updated: '2026-09-08T00:00:00Z',
    last_updated_relative: 'today', last_completed: null, live_agent: null,
    display_order: 0, provider: 'claude', use_streaming_agent: true,
    distiller_mode: 'proposed', distiller_min_recurrence: 3,
    distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
    distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
    distiller_skip_errors: true,
  };
}
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Default Agent Smoke')]);

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

// The real identity mc/identity.py resolves a no-persona session to (see
// tests/test_channel_roster_default_identity.py for the server-side half).
const DEFAULT_IDENTITY = { key: 'default:', name: 'Vector', avatar: 'fig:navigator', from: 'default' };
const UNNAMED_IDENTITY = { key: 'unnamed:', name: 'unnamed', avatar: '', from: 'unnamed' };

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
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });

  if (pageErrors.length) {
    pageErrors.forEach((e) => fail('uncaught page error during boot: ' + e));
  } else {
    ok('app booted clean, no uncaught exceptions');
  }

  // ── Seed real-shaped rows: a finished chat and a LIVE running session with
  // no persona (the actual "Vector mid-turn, invisible" symptom Ron reported),
  // plus a delegated no-persona (MC-925 'unnamed') session that IS working —
  // must be counted — and a second one that's merely idle/finished — must
  // NOT be benched. ────────────────────────────────────────────────────────
  const seeded = await page.evaluate(({ pid, defaultIdentity, unnamedIdentity }) => {
    conversationsCache[pid] = [
      { claude_session_id: 'default-1', mc_session_id: 'mc-default-1', character: null, identity: defaultIdentity, mtime: 1000, ts_relative: '2h ago', status: 'completed', turns: 3, label: 'fixed the export bug', first_user: 'fixed the export bug', last_user: 'fixed the export bug' },
    ];
    // The live, currently-generating no-persona session — this is the exact
    // "agent working, roster shows nobody" symptom.
    agentStatusCache['mc-default-live'] = { status: 'running', task: 'refactoring the parser', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: '', character: null, identity: defaultIdentity };
    // A delegated (source='agent') no-persona session that IS working right
    // now — arm 4 of the audit's fix: counted while live.
    agentStatusCache['mc-unnamed-working'] = { status: 'running', task: 'nested lookup', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: '', character: null, identity: unnamedIdentity };
    // A delegated no-persona session that has already FINISHED — arm 4's
    // other half: never benched, so this must not add a 2nd "unnamed" row
    // once idle.
    agentStatusCache['mc-unnamed-done'] = { status: 'idle', task: 'nested lookup (done)', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: '', character: null, identity: unnamedIdentity };
    openProjectModal(pid);
    return true;
  }, { pid: PID, defaultIdentity: DEFAULT_IDENTITY, unnamedIdentity: UNNAMED_IDENTITY });
  if (!seeded) fail('seeding evaluate() returned falsy');

  await page.waitForSelector(`.modal-window[data-modal-id="${PID}"] .agent-rail`, { timeout: 5000 });
  const scope = `.modal-window[data-modal-id="${PID}"] `;
  await page.click(`${scope}.rail-mode-btn >> text=Channel`);
  await page.waitForSelector(`${scope}.channel-row`, { timeout: 5000 });

  const rows = await page.$$eval(`${scope}.channel-row`, (els) => els.map((el) => ({
    key: el.dataset.charKey, name: el.querySelector('.conv-name')?.textContent.trim(),
  })));

  // ── 1. Vector (the default agent) has a roster row at all ────────────────
  const vectorRow = rows.find((r) => r.key === 'default:');
  vectorRow
    ? ok(`the default agent has a roster row: name="${vectorRow.name}"`)
    : fail(`no roster row keyed 'default:' — Vector is still invisible. Rows: ${JSON.stringify(rows)}`);
  (vectorRow && vectorRow.name === 'Vector')
    ? ok('the row is named from CONFIG.agent_name ("Vector"), not blank or a placeholder')
    : fail(`expected the row name "Vector", got: ${vectorRow && vectorRow.name}`);

  // ── 2. It has a REAL session behind it: the finished chat AND the live run
  // both collapse into this ONE row (not two, not zero) ────────────────────
  rows.filter((r) => r.key === 'default:').length === 1
    ? ok('the finished chat and the live run collapsed into ONE Vector row, not split across two')
    : fail(`Vector should appear exactly once, appeared ${rows.filter((r) => r.key === 'default:').length} times`);

  // ── 3. Because it's genuinely mid-turn, it sits "In the room" — not just
  // benched as history ──────────────────────────────────────────────────────
  const sectionOf = async (key) => page.evaluate(({ scopeSel, k }) => {
    const rowsEls = Array.from(document.querySelectorAll(scopeSel + '.channel-row'));
    const row = rowsEls.find((el) => el.dataset.charKey === k);
    if (!row) return null;
    let el = row.previousElementSibling;
    while (el && !el.classList.contains('channel-section-header')) el = el.previousElementSibling;
    return el ? el.textContent.replace(/\d+$/, '').trim() : null;
  }, { scopeSel: scope, k: key });
  (await sectionOf('default:')) === 'In the room'
    ? ok('Vector sits under "In the room" while the live session is actively generating')
    : fail(`Vector should be under "In the room", section was: ${await sectionOf('default:')}`);

  // ── 4. Clicking Vector expands her real conversations inline, not an empty
  // pane, and the roster stays put around the expanded row (accordion) ──────
  await page.click(`${scope}.channel-row[data-char-key="default:"]`);
  await page.waitForSelector(`${scope}.channel-row[data-char-key="default:"].expanded`, { timeout: 3000 });
  const filteredIds = await page.$$eval(`${scope}.channel-expanded .conv-row[data-csid]`, (els) => els.map((el) => el.dataset.csid).filter(Boolean));
  filteredIds.includes('default-1')
    ? ok('clicking Vector\'s row expands her real conversation(s), not an empty pane')
    : fail(`clicking Vector should show her chat(s), got: ${JSON.stringify(filteredIds)}`);

  // ── 5. The delegated no-persona ('unnamed') session counts while working,
  // and does NOT leave a permanent Bench row once idle (MC-925 + "live only,
  // never benched") ─────────────────────────────────────────────────────────
  const rows2 = await page.$$eval(`${scope}.channel-row`, (els) => els.map((el) => el.dataset.charKey));
  rows2.includes('unnamed:')
    ? ok('the delegated no-persona session (MC-925) has a roster row while it is actually working')
    : fail(`expected an 'unnamed:' row for the working delegated session, rows: ${JSON.stringify(rows2)}`);
  rows2.filter((k) => k === 'unnamed:').length === 1
    ? ok('exactly one "unnamed" row — the finished delegated session did not leave a stray Bench duplicate')
    : fail(`expected exactly 1 'unnamed:' row, got ${rows2.filter((k) => k === 'unnamed:').length}`);
  (await sectionOf('unnamed:')) === 'In the room'
    ? ok('the "unnamed" row sits "In the room" (never benched, per the audit\'s arm 4)')
    : fail(`the "unnamed" row should only ever appear "In the room", section was: ${await sectionOf('unnamed:')}`);
  rows2.includes('default:')
    ? ok('Vector and the delegated worker are DISTINCT rows — no accidental merge into one identity')
    : fail('Vector\'s row disappeared after the unnamed-row assertions');

  // ── 6. The in-place SSE patch (updateRailRowStatus, MC-940) has its OWN
  // independent character-key derivation — same sibling bug, fixed the same
  // way. Flip Vector's live session to "waiting" and drive the in-place path
  // directly (the way turn_start/turn_complete does) instead of a full
  // rebuild, and confirm her row's pill updates without one. ───────────────
  await page.evaluate(() => {
    agentStatusCache['mc-default-live'].waitingForQuestion = true;
    window.updateRailRowStatus('mc-default-live');
  });
  const vectorBadge = await page.$eval(`${scope}.channel-row[data-char-key="default:"] .conv-live-badge.waiting`, (el) => el.textContent.trim()).catch(() => null);
  vectorBadge === 'Waiting for you'
    ? ok('in-place patch (updateRailRowStatus) reaches Vector\'s row too — not just the full-rebuild path')
    : fail(`expected Vector's row to show the waiting badge via the in-place patch, got: ${vectorBadge}`);

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) {
    uncaught.forEach((e) => fail('uncaught exception during interaction: ' + e));
  }

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0
    ? '\n✅ PASS — the default agent (Vector) is visible on the Channel roster, with real sessions grouped behind it, and a delegated no-persona worker is counted live without polluting the Bench.'
    : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
