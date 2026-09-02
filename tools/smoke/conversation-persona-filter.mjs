#!/usr/bin/env node
/**
 * MC-938 Phase 0 regression: persona-dispatched sessions must appear in the
 * Chats rail.
 *
 * WHY THIS EXISTS
 * ----------------
 * `_userInitiatedConvos` (static/js/conversation.js) is the single supplier
 * for both list surfaces (desktop rail + mobile Layer-2) and the row renderer
 * that draws the character face. A persona chat dispatched without a browser
 * Origin header gets auto-tagged `source: agent` purely so it routes to the
 * side flow (mc/blueprints/agent_routes.py:5567-5574) — but the filter used to
 * treat that tag as proof of a machine-initiated thread and drop the row
 * unconditionally, silently hiding chats a human deliberately started with a
 * named persona. Fixed by letting a `character` on the row override the
 * programmatic-source drop, while still dropping rows from a genuinely
 * automated TRIGGER (scheduler, hivemind worker, ...) even if one somehow
 * carried a character.
 *
 * This is a real headless boot (real index.html + real static/js/*.js served
 * verbatim, no server, no network) rather than a unit test against an
 * extracted copy of the function, so a future edit to conversation.js is
 * exercised as shipped. `window._userInitiatedConvos` is bridged in
 * conversation.js for exactly this reason (see the comment beside it).
 *
 * RUN
 *   cd tools/smoke && node conversation-persona-filter.mjs
 * Exit 0 = all cases behave; 1 = a case regressed / harness error.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, extname } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const PROJECTS_JSON = readFileSync(resolve(__dirname, 'fixtures', 'projects.json'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_alpha';   // fixtures/projects.json

// Serve every real static/js/*.js and static/css/*.css verbatim — hermetic,
// but not hand-maintained per-file like boot-smoke.mjs's STATIC_MAP, so a new
// module never silently falls through to `route.abort()` here.
const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

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
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });

  if (pageErrors.length) {
    pageErrors.forEach((e) => fail('uncaught page error during boot: ' + e));
  } else {
    ok('app booted clean, no uncaught exceptions');
  }

  const bridged = await page.evaluate(() => typeof window._userInitiatedConvos === 'function');
  if (!bridged) {
    fail('window._userInitiatedConvos is not a function — bridge missing from conversation.js');
    throw new Error('cannot proceed without the bridge');
  }
  ok('window._userInitiatedConvos is bridged');

  // Synthetic rows covering both suppliers merged by _userInitiatedConvos:
  // conversationsCache (the normal /conversations rows) and agentLogCache
  // (rows merged in for chats that aged out of conversationsCache, MC-938's
  // second fix — the merge branch must carry `character` through too).
  const CHAR_FENN = { name: 'code-reviewer', scope: 'project', display_name: 'Fenn', agent_name: 'Fenn', avatar: 'fenn.png' };
  const result = await page.evaluate(({ pid, char }) => {
    conversationsCache[pid] = [
      // 1. Persona chat dispatched without an Origin header — the exact defect:
      //    auto-tagged source:'agent' but a human picked a character. Must be KEPT.
      { claude_session_id: 'persona-agent-source', source: 'agent', trigger_type: '', character: char, label: 'plan the release', turns: 3 },
      // 2. Genuine programmatic dispatch, no character — must stay DROPPED.
      { claude_session_id: 'programmatic-no-char', source: 'agent', trigger_type: '', character: null, label: 'nightly sweep', turns: 1 },
      // 3. Genuine cron thread, no character — must stay DROPPED.
      { claude_session_id: 'cron-thread', source: 'cron', trigger_type: '', character: null, label: 'scheduled housekeeping', turns: 2 },
      // 4. A steward conversation — the pre-existing automated-trigger exception,
      //    must remain KEPT regardless of this change.
      { claude_session_id: 'steward-convo', source: 'agent', trigger_type: '', steward: true, character: null, label: '[Steward cycle] weekly review', turns: 5 },
      // 5. A character riding along on a genuinely programmatic TRIGGER (e.g. a
      //    hivemind worker) — the trigger check must still win; a character must
      //    not launder a machine thread into the chat list.
      { claude_session_id: 'hivemind-with-char', source: 'agent', trigger_type: 'hivemind_worker', character: char, label: 'worker turn', turns: 1 },
      // 6. An ordinary human chat (no source tag at all) — must be unaffected.
      { claude_session_id: 'plain-user-chat', source: '', trigger_type: '', character: null, label: 'help me debug this', turns: 4 },
    ];
    // agentLogCache-only rows — simulate chats that aged out of /conversations
    // and are only reachable via the merge branch (conversation.js ~1630-1648).
    agentLogCache[pid] = [
      // 7. Aged-out persona chat, source:agent, character present — must be KEPT
      //    (proves `character` survives the merge branch's synthetic row).
      { claude_session_id: 'aged-persona', session_id: 'mc-aged-persona', task: 'aged persona chat', status: 'completed', num_turns: 2, source: 'agent', trigger_type: '', character: char },
      // 8. Aged-out genuinely programmatic row, no character — must stay DROPPED.
      { claude_session_id: 'aged-programmatic', session_id: 'mc-aged-programmatic', task: 'aged cron sweep', status: 'completed', num_turns: 1, source: 'agent', trigger_type: '' },
    ];
    const rows = window._userInitiatedConvos(pid);
    return rows.map(r => ({ id: r.claude_session_id, hasChar: !!(r.character && r.character.agent_name) }));
  }, { pid: PID, char: CHAR_FENN });

  const ids = result.map(r => r.id);
  const has = (id) => ids.includes(id);
  const charOf = (id) => (result.find(r => r.id === id) || {}).hasChar;

  has('persona-agent-source') ? ok("KEPT: persona chat auto-tagged source:'agent' (the defect case)") : fail("DROPPED (should be KEPT): persona chat auto-tagged source:'agent'");
  charOf('persona-agent-source') ? ok('  ...and its character (Fenn) rode along for the renderer') : fail('  ...but its character was lost — row would render faceless');
  !has('programmatic-no-char') ? ok("DROPPED: genuine programmatic row, source:'agent', no character") : fail("KEPT (should be DROPPED): programmatic row with no character");
  !has('cron-thread') ? ok("DROPPED: genuine cron thread") : fail("KEPT (should be DROPPED): cron thread");
  has('steward-convo') ? ok('KEPT: steward conversation (pre-existing exception, unaffected)') : fail('DROPPED (should be KEPT): steward conversation');
  !has('hivemind-with-char') ? ok('DROPPED: character riding a genuinely programmatic TRIGGER (hivemind_worker)') : fail('KEPT (should be DROPPED): character did not launder a programmatic-trigger row');
  has('plain-user-chat') ? ok('KEPT: ordinary human chat, no source tag') : fail('DROPPED (should be KEPT): ordinary human chat');
  has('aged-persona') ? ok('KEPT: aged-out persona chat merged in from agentLogCache') : fail('DROPPED (should be KEPT): aged-out persona chat from the agent-log merge branch');
  charOf('aged-persona') ? ok('  ...and its character rode through the merge branch too') : fail('  ...but its character was dropped by the merge branch');
  !has('aged-programmatic') ? ok('DROPPED: aged-out genuinely programmatic row from agentLogCache') : fail('KEPT (should be DROPPED): aged-out programmatic row');

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0 ? '\n✅ PASS — persona sessions unhidden, programmatic threads still filtered.' : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
