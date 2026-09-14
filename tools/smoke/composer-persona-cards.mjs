#!/usr/bin/env node
/**
 * WHO-leads-WHAT persona picker (Ron, 2026-09-14, phone screenshot).
 *
 * WHY THIS EXISTS
 * ----------------
 * The only way to pick a persona on the +New screen used to be a tiny
 * "Claude Code · Incognito off · Change" line under the composer — Ron's
 * report: "This is what I wanted to move away from. The agent selection
 * needs to be front and center." The fix adds a face-card grid
 * (_composerPersonPickerHTML, static/js/conversation.js) ahead of the
 * "What should X work on?" heading, wired to the SAME setComposerCharacter()
 * the old dropdown/sheet use — never a second source of truth.
 *
 * This proves, against the real static/js + static/css served verbatim:
 *   1. On a phone viewport, the card row sits inside the initial viewport
 *      (no scroll needed to reach it) — the defect the screenshot showed.
 *   2. Tapping a card is the SAME state setComposerCharacter always set —
 *      the headline/placeholder update, and the character rides the actual
 *      POST /agent/dispatch body, intercepted and inspected.
 *   3. Selecting "Resuming: …" (a past conversation) hides the section —
 *      persona is frozen at spawn, matching _composerCharacterPicker's own
 *      resumeId gate.
 * Also captures a phone and a desktop screenshot for visual review.
 *
 * Hermetic: real index.html + real static/js/*.js + static/css/*.css served
 * verbatim (same shape as conversation-persona-filter.mjs). No server, no
 * network.
 *
 * RUN   node tools/smoke/composer-persona-cards.mjs
 * Exit  0 = all checks pass; 1 = a regression / harness error.
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
const SHOT_DIR = resolve(__dirname, '_shots');
mkdirSync(SHOT_DIR, { recursive: true });

const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_persona_cards';
const SESS = 'smoke_persona_sess_1';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

function fixtureProject(id, name) {
  return {
    id, name, status: 'active', domain: 'general', emoji: '\u{1F9EA}',
    description: '', summary: '', current_task: 'Idle', next_action: '',
    blocked: false, blocked_reason: null, activity_log: [], backlog: [],
    project_path: '/smoke/' + id, last_updated: '2026-09-14T00:00:00Z',
    last_updated_relative: 'today', last_completed: null, live_agent: null,
    display_order: 0, provider: 'claude', use_streaming_agent: true,
    distiller_mode: 'proposed', distiller_min_recurrence: 3,
    distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
    distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
    distiller_skip_errors: true,
  };
}
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Persona Cards Smoke')]);
const CHARACTERS_JSON = JSON.stringify([
  { name: 'fenn', scope: 'global', agent_name: 'Fenn', display_name: 'code-reviewer', description: 'x', file: 'fenn.md', size: 10, avatar: '\u{1F9D0}', engine: { model: '' } },
  { name: 'vector', scope: 'global', agent_name: 'Vector', display_name: 'default agent', description: 'x', file: 'vector.md', size: 10, avatar: '⚡', engine: { provider: 'claude' } },
]);
const CONVERSATIONS_JSON = JSON.stringify([
  { claude_session_id: 'csid-persona-1', mc_session_id: SESS, character: null, mtime: 1000, ts_relative: 'earlier', status: 'completed', turns: 3, label: 'a past chat', first_user: 'a past chat', last_user: 'a past chat' },
]);

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

async function installRoutes(page) {
  const dispatchBodies = [];
  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (/\/conversations$/.test(path)) return route.fulfill({ status: 200, contentType: 'application/json', body: CONVERSATIONS_JSON });
    if (/\/agent-log$/.test(path)) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (/\/agent\/dispatch$/.test(path)) {
      dispatchBodies.push(JSON.parse(req.postData() || '{}'));
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, session_id: SESS }) });
    }
    return route.abort();
  });
  return dispatchBodies;
}

async function openFreshComposer(page) {
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(({ pid }) => {
    window.agentConvNew = window.agentConvNew || {};
    openProjectModal(pid);
    agentConvNew[pid] = true;
    if (typeof refreshModal === 'function') refreshModal();
  }, { pid: PID });
  await page.waitForSelector('.ces-people', { timeout: 5000 });
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();

  // ── 1. Phone viewport: cards above the fold, tap sets the dispatched persona ──
  {
    const ctx = await browser.newContext({ viewport: { width: 412, height: 883 }, hasTouch: true });
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
    const dispatchBodies = await installRoutes(page);
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await openFreshComposer(page);

    const fold = await page.evaluate(() => {
      const row = document.querySelector('.ces-people-row');
      const cards = Array.from(document.querySelectorAll('.ces-person-card'));
      const r = row.getBoundingClientRect();
      return {
        rowTop: r.top, rowBottom: r.bottom, vh: window.innerHeight,
        cardCount: cards.length,
        firstCardH: cards[0] ? cards[0].getBoundingClientRect().height : 0,
      };
    });
    (fold.cardCount >= 3) ? ok(`phone: ${fold.cardCount} face cards rendered (2 personas + None + New)`)
      : fail(`phone: expected >=3 cards (2 personas + None/New), got ${fold.cardCount}`);
    (fold.rowTop >= 0 && fold.rowBottom <= fold.vh)
      ? ok(`phone: card row sits above the fold (top=${fold.rowTop.toFixed(0)}, bottom=${fold.rowBottom.toFixed(0)}, viewport=${fold.vh})`)
      : fail(`phone: card row NOT fully in the initial viewport (top=${fold.rowTop.toFixed(0)}, bottom=${fold.rowBottom.toFixed(0)}, viewport=${fold.vh})`);
    (fold.firstCardH >= 44)
      ? ok(`phone: card touch target is ${fold.firstCardH.toFixed(0)}px tall (>=44px)`)
      : fail(`phone: card touch target only ${fold.firstCardH.toFixed(0)}px tall (<44px)`);

    await page.screenshot({ path: resolve(SHOT_DIR, 'composer-persona-cards-phone.png') });
    ok('phone screenshot saved');

    // Tap the second persona card (Vector) — not "None", not "New persona".
    const tapped = await page.evaluate(() => {
      const cards = Array.from(document.querySelectorAll('.ces-person-card:not(.ces-person-none):not(.ces-person-add)'));
      const target = cards.find(c => c.querySelector('.ces-person-name')?.textContent === 'Vector');
      if (!target) return { err: 'Vector card not found', names: cards.map(c => c.querySelector('.ces-person-name')?.textContent) };
      target.click();
      return { err: null };
    });
    if (tapped.err) fail('phone: ' + tapped.err + ' (found: ' + JSON.stringify(tapped.names) + ')');
    else ok('phone: tapped the Vector face card');

    const afterTap = await page.evaluate(({ pid }) => ({
      selected: document.querySelector('.ces-person-card.selected .ces-person-name')?.textContent || null,
      pending: getPendingCharacter(pid),
      heading: document.querySelector('.ces-heading')?.textContent || '',
      placeholder: document.getElementById('agent-task-' + pid)?.placeholder || '',
    }), { pid: PID });
    (afterTap.selected === 'Vector') ? ok('phone: the Vector card shows .selected')
      : fail(`phone: .selected card shows "${afterTap.selected}", want "Vector"`);
    (afterTap.pending === 'global:vector') ? ok('phone: getPendingCharacter(pid) === "global:vector" (same state setComposerCharacter always set)')
      : fail(`phone: getPendingCharacter(pid) === "${afterTap.pending}", want "global:vector"`);
    (afterTap.heading === 'What should Vector work on?') ? ok(`phone: headline follows the selection ("${afterTap.heading}")`)
      : fail(`phone: headline is "${afterTap.heading}", want "What should Vector work on?"`);
    (afterTap.placeholder.includes('Vector')) ? ok(`phone: composer placeholder names Vector ("${afterTap.placeholder}")`)
      : fail(`phone: composer placeholder is "${afterTap.placeholder}", expected it to include "Vector"`);

    // Dispatch and confirm the character rides the real POST body.
    await page.evaluate(({ pid }) => {
      const ta = document.getElementById('agent-task-' + pid);
      ta.value = 'smoke: does Vector get dispatched';
    }, { pid: PID });
    await page.evaluate(({ pid }) => dispatchAgent(pid), { pid: PID });
    await page.waitForTimeout(300);
    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    if (uncaught.length) uncaught.forEach((e) => fail('phone: uncaught exception — ' + e));
    (dispatchBodies.length === 1 && dispatchBodies[0].character === 'global:vector')
      ? ok(`phone: POST /agent/dispatch body.character === "global:vector" (intercepted, not asserted)`)
      : fail(`phone: dispatch body character was ${JSON.stringify(dispatchBodies[0]?.character)} (${dispatchBodies.length} POSTs seen), want "global:vector"`);

    await ctx.close();
  }

  // ── 2. Resume hides the section (persona frozen at spawn) ──
  {
    const ctx = await browser.newContext({ viewport: { width: 412, height: 883 }, hasTouch: true });
    const page = await ctx.newPage();
    await installRoutes(page);
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
    await page.evaluate(({ pid, sid }) => {
      window.agentConvNew = window.agentConvNew || {};
      openProjectModal(pid);
      agentConvNew[pid] = true;
      pendingResumeId[pid] = sid;
      if (typeof refreshModal === 'function') refreshModal();
    }, { pid: PID, sid: 'csid-persona-1' });
    await page.waitForTimeout(300);
    const peopleVisible = await page.evaluate(() => !!document.querySelector('.ces-people'));
    (!peopleVisible) ? ok('resume: face-card section is gone once a past conversation is armed to resume')
      : fail('resume: .ces-people still rendered while resuming a past conversation — persona should be frozen at spawn');
    await ctx.close();
  }

  // ── 3. Desktop viewport: section renders, screenshot for visual review ──
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    await installRoutes(page);
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await openFreshComposer(page);
    const desktopCount = await page.evaluate(() => document.querySelectorAll('.ces-person-card').length);
    (desktopCount >= 3) ? ok(`desktop: ${desktopCount} face cards rendered in the empty main pane`)
      : fail(`desktop: expected >=3 cards, got ${desktopCount}`);
    await page.screenshot({ path: resolve(SHOT_DIR, 'composer-persona-cards-desktop.png') });
    ok('desktop screenshot saved');
    await ctx.close();
  }

  exitCode = bad === 0 ? 0 : 1;
} catch (e) {
  console.error('❌ harness error: ' + (e.stack || e));
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}

console.log('');
console.log(exitCode === 0
  ? '✅ PASS — persona face cards render above the fold, tapping one is the real setComposerCharacter() state and rides the dispatch POST, resume hides the section.'
  : `❌ FAIL — ${bad} check(s) failed.`);
process.exit(exitCode);
