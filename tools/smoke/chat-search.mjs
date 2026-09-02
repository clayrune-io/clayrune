#!/usr/bin/env node
/**
 * In-chat search: find something said earlier in the CURRENTLY OPEN
 * conversation (chat-search.js, conversation.js's Find button + Ctrl/Cmd+F).
 *
 * WHY THIS EXISTS
 * ----------------
 * The chat pane never contains the whole conversation. conversation.js caps
 * the cold render at MAX_RENDER_LINES=500 (an "earlier lines" affordance
 * loads the rest via expandedOutputSessions), and the client's own in-memory
 * buffer — agentOutputBuffers[sid], what that render draws from — is trimmed
 * to the last 1500 lines once a live session passes 2000 (resume-preview.js).
 * A find-in-DOM would silently report "no matches" for text that is
 * demonstrably in the conversation, for two independent reasons stacked on
 * top of each other. The fix fetches the complete, uncapped transcript from
 * the server on search-open (GET .../transcript/<csid>/full-buffer),
 * replaces the client buffer with it when it's more complete, forces the
 * "load all" render path, and only THEN searches — so by construction, every
 * match the count claims is already on screen to scroll to.
 *
 * This is a hermetic boot (real index.html + real static/js/*.js served
 * verbatim, no real backend — same shape as channel-message-attribution.mjs),
 * chosen specifically because the live dev server on :5199 has NOT been
 * restarted to pick up the new /full-buffer route (a Python route needs a
 * process restart; this session was told not to restart the shared server),
 * so the real server can't yet demonstrate the completes-from-server path —
 * this harness mocks that one endpoint instead, deterministically, while
 * every line of client JS involved (chat-search.js, conversation.js's render
 * loop, resume-preview.js's repaint hook) runs for real in a real browser.
 *
 * Three fixture sessions, one project each:
 *   sess-reach   — proves the CORE claim: a match that exists ONLY above the
 *                  500-line render cut AND only in the server's complete
 *                  transcript (not yet in the capped client buffer, modeling
 *                  the resume-preview 1500-line trim) is found and reachable.
 *   sess-partial — no claude_session_id (non-Claude provider keeps no
 *                  transcript). Search must degrade HONESTLY: search what's
 *                  buffered and say it's partial, not report a clean zero.
 *   sess-struct  — count/nav/wrap/Escape/no-match, plus non-corruption of a
 *                  markdown table, a mermaid block, and a plan card after a
 *                  highlight pass runs over them.
 *
 * RUN: node chat-search.mjs
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

const PID_REACH = 'smoke_cs_reach';
const PID_PARTIAL = 'smoke_cs_partial';
const PID_STRUCT = 'smoke_cs_struct';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

function fixtureProject(id, name) {
  return {
    id, name, status: 'active', domain: 'general', emoji: '\u{1f9ea}',
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
const PROJECTS_JSON = JSON.stringify([
  fixtureProject(PID_REACH, 'Search Reach Smoke'),
  fixtureProject(PID_PARTIAL, 'Search Partial Smoke'),
  fixtureProject(PID_STRUCT, 'Search Structure Smoke'),
]);

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

// The complete, on-disk transcript for sess-reach: 900 narration lines. The
// needle sits at index 5 — inside the 500-line render cap's blind spot AND
// absent from the (deliberately short, pre-trim-simulating) client buffer.
const NEEDLE = 'ZEBRA_QUOKKA_NEEDLE_4471';
const FULL_LINES_REACH = [];
for (let i = 0; i < 900; i++) {
  FULL_LINES_REACH.push(i === 5 ? `Early on we settled the ${NEEDLE} question.` : `Filler narration line number ${i}.`);
}
// What the client already has in memory BEFORE search opens: the tail only,
// same as a session that already hit resume-preview's 1500-line trim — the
// needle (index 5 of the full 900) is not in here at all.
const CLIENT_BUFFER_REACH = FULL_LINES_REACH.slice(-40);

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    // The one server route this feature adds. csid-reach is the only session
    // with a resolvable transcript; everything else 404s (no transcript).
    if (path === `/api/project/${PID_REACH}/transcript/csid-reach/full-buffer`) {
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ claude_session_id: 'csid-reach', log_lines: FULL_LINES_REACH }) });
    }
    if (path.endsWith('/full-buffer')) {
      return route.fulfill({ status: 404, contentType: 'application/json', body: '{"error":"transcript not found or empty"}' });
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });

  if (pageErrors.length) pageErrors.forEach((e) => fail('uncaught page error during boot: ' + e));
  else ok('app booted clean, no uncaught exceptions');

  const bridged = await page.evaluate(() => typeof window.openChatSearch === 'function' && typeof window.chatSearchBarHTML === 'function');
  bridged ? ok('chat-search.js is bridged onto window') : fail('openChatSearch/chatSearchBarHTML missing — is the page serving stale JS?');
  if (!bridged) throw new Error('no bridge');

  // ═══════════════════════════════════════════════════════════════════════
  // 1. REACH: a match above the 500-line render cut AND missing from the
  //    (trimmed) client buffer must be found via the server fetch, then
  //    actually be reachable on screen.
  // ═══════════════════════════════════════════════════════════════════════
  const reach = await page.evaluate(({ pid, buf }) => {
    const sid = 'sess-reach';
    agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Search Reach Smoke', task: 'long thread', status: 'completed', startedAt: new Date().toISOString() });
    agentStatusCache[sid] = { status: 'completed', task: 'long thread', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: 'csid-reach' };
    agentOutputBuffers[sid] = buf.slice();
    openProjectModal(pid);
    return sid;
  }, { pid: PID_REACH, buf: CLIENT_BUFFER_REACH });
  await page.waitForSelector(`.modal-window[data-modal-id="${PID_REACH}"] #agent-output-${reach}`, { timeout: 5000 });
  const scopeReach = `.modal-window[data-modal-id="${PID_REACH}"] `;

  const before = await page.evaluate((sel) => document.querySelector(sel)?.innerText || '', `${scopeReach}#agent-output-${reach}`);
  !before.includes(NEEDLE) ? ok('pre-search: needle is not on screen (buffer trimmed + render-capped, as designed)')
    : fail('pre-search: needle already visible — the fixture does not exercise the cap at all');

  await page.click(`${scopeReach} button[onclick*="openChatSearch"]`);
  await page.waitForSelector(`${scopeReach}#chat-search-bar-${reach}`, { timeout: 5000 });
  ok('Find button opened the search bar');

  await page.fill(`${scopeReach}#chat-search-input-${reach}`, NEEDLE);
  await page.waitForFunction(
    (sel) => (document.querySelector(sel)?.textContent || '').trim().length > 0,
    `${scopeReach}#chat-search-count-${reach}`, { timeout: 5000 });

  const afterCount = await page.textContent(`${scopeReach}#chat-search-count-${reach}`);
  afterCount.trim() === '1 of 1' ? ok(`match found and counted correctly ("${afterCount.trim()}")`)
    : fail(`expected "1 of 1", got "${afterCount.trim()}"`);

  // scrollIntoView is deferred a frame (see _csSetCurrent) — give it a beat.
  await page.waitForTimeout(200);
  const markInfo = await page.evaluate((sel) => {
    const out = document.querySelector(sel);
    const mark = out?.querySelector('mark.mc-chat-search-hit.mc-chat-search-current');
    if (!mark) return null;
    const r = mark.getBoundingClientRect();
    const outR = out.getBoundingClientRect();
    return { text: mark.textContent, inView: r.top >= outR.top - 2 && r.bottom <= outR.bottom + 2 };
  }, `${scopeReach}#agent-output-${reach}`);
  markInfo ? ok(`current match is highlighted ("${markInfo.text}")`) : fail('no current-match <mark> found in the DOM');
  markInfo && markInfo.inView ? ok('the match is scrolled into view within the (now fully-expanded) output pane')
    : fail(`match highlighted but not scrolled into view: ${JSON.stringify(markInfo)}`);

  const stillTruncated = await page.evaluate((sel) => !!document.querySelector(sel), `${scopeReach}[onclick*="expandAgentOutput"]`);
  !stillTruncated ? ok('the "N earlier lines" affordance is gone — the whole buffer rendered, not just the tail')
    : fail('render is still truncated; the match landed outside what a user could reach');

  const partialShownReach = await page.evaluate((sel) => !!document.querySelector(sel), `${scopeReach}.mc-chat-search-partial`);
  !partialShownReach ? ok('no partial-results notice — this session DOES have a full transcript')
    : fail('partial notice shown even though the server transcript was fetched successfully');

  // ═══════════════════════════════════════════════════════════════════════
  // 2. PARTIAL: no claude_session_id at all (non-Claude provider keeps no
  //    transcript) — search must still run, honestly labeled, no clean zero.
  // ═══════════════════════════════════════════════════════════════════════
  const partial = await page.evaluate(({ pid }) => {
    const sid = 'sess-partial';
    agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Search Partial Smoke', task: 'gemini chat', status: 'completed', startedAt: new Date().toISOString() });
    agentStatusCache[sid] = { status: 'completed', task: 'gemini chat', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: '' };
    agentOutputBuffers[sid] = ['> Ron: what does the config flag do?', 'It toggles the beta renderer.'];
    openProjectModal(pid);
    return sid;
  }, { pid: PID_PARTIAL });
  await page.waitForSelector(`.modal-window[data-modal-id="${PID_PARTIAL}"] #agent-output-${partial}`, { timeout: 5000 });
  const scopePartial = `.modal-window[data-modal-id="${PID_PARTIAL}"] `;

  await page.click(`${scopePartial} button[onclick*="openChatSearch"]`);
  await page.waitForSelector(`${scopePartial}#chat-search-bar-${partial}`, { timeout: 5000 });
  await page.fill(`${scopePartial}#chat-search-input-${partial}`, 'beta renderer');
  await page.waitForFunction(
    (sel) => (document.querySelector(sel)?.textContent || '').trim().length > 0,
    `${scopePartial}#chat-search-count-${partial}`, { timeout: 5000 });
  const partialCount = (await page.textContent(`${scopePartial}#chat-search-count-${partial}`)).trim();
  partialCount === '1 of 1' ? ok(`partial session: search still WORKS on the buffered content ("${partialCount}")`)
    : fail(`partial session: expected "1 of 1" on what IS buffered, got "${partialCount}"`);
  const partialNoticeShown = await page.evaluate((sel) => !!document.querySelector(sel), `${scopePartial}.mc-chat-search-partial`);
  partialNoticeShown ? ok('partial session: honestly labeled as partial (no full transcript on disk)')
    : fail('partial session: no partial notice shown — a real gap would silently read as a clean, complete search');

  // ═══════════════════════════════════════════════════════════════════════
  // 3. STRUCTURE: count/nav/wrap/Escape/no-match + a highlight pass must not
  //    corrupt a table, a mermaid block, or a collapsed plan block.
  // ═══════════════════════════════════════════════════════════════════════
  const struct = await page.evaluate(({ pid }) => {
    const sid = 'sess-struct';
    const buf = [
      '> Ron: compare the two runs and show the plan',
      'banana appears once here.',
      '| fruit | count |',
      '| --- | --- |',
      '| banana | 3 |',
      '| apple | 1 |',
      'banana shows up again after the table.',
      '```mermaid',
      'graph TD; A-->B;',
      '```',
      'banana once more, near the mermaid block.',
      // A tool line flushes the render loop's plan-block accumulator (see
      // conversation.js's ExitPlanMode handling) — without this boundary, the
      // table + mermaid + narration lines above ALSO get swept into the
      // "Show Plan" collapse below (they're all still un-flushed content
      // since the last prompt/tool line), which is not what this fixture is
      // testing (that path is covered by the plan-block assertions on its own).
      '[tool: Read]',
      'Plan line one about the banana rollout.',
      'Plan line two, ready to ship.',
      '[tool: ExitPlanMode]',
      'banana, said one final time after the plan.',
    ];
    agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Search Structure Smoke', task: 'structured content', status: 'completed', startedAt: new Date().toISOString() });
    agentStatusCache[sid] = { status: 'completed', task: 'structured content', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: '' };
    agentOutputBuffers[sid] = buf;
    openProjectModal(pid);
    return sid;
  }, { pid: PID_STRUCT });
  await page.waitForSelector(`.modal-window[data-modal-id="${PID_STRUCT}"] #agent-output-${struct}`, { timeout: 5000 });
  const scopeStruct = `.modal-window[data-modal-id="${PID_STRUCT}"] `;

  const structuresBefore = await page.evaluate((sel) => {
    const out = document.querySelector(sel);
    return {
      tableRows: out.querySelectorAll('.hl-table table tr').length,
      tableCells: out.querySelectorAll('.hl-table table td, .hl-table table th').length,
      mermaidBlocks: out.querySelectorAll('.mermaid-block').length,
      mermaidSource: out.querySelector('.mermaid-block')?.getAttribute('data-source') || '',
      planShowBtn: out.querySelectorAll('.plan-show-btn').length,
      planHiddenBlock: out.querySelectorAll('.plan-hidden-block').length,
    };
  }, `${scopeStruct}#agent-output-${struct}`);
  (structuresBefore.tableRows === 3 && structuresBefore.mermaidBlocks === 1 && structuresBefore.planShowBtn === 1 && structuresBefore.planHiddenBlock === 1)
    ? ok('fixture rendered a table, a mermaid block, and a collapsed plan block, as expected (cold render)')
    : fail(`fixture render is not what the test expects: ${JSON.stringify(structuresBefore)}`);

  await page.click(`${scopeStruct} button[onclick*="openChatSearch"]`);
  await page.waitForSelector(`${scopeStruct}#chat-search-bar-${struct}`, { timeout: 5000 });
  // Opening search repaints #agent-output-<sid> from the buffer via the SAME
  // _repaintAgentOutput() every ordinary tab switch already uses (see the note
  // on openChatSearch). For an ExitPlanMode line, that warm/incremental path
  // behaves differently than the cold render above: it unconditionally flips
  // waitingForPlanApproval and appends an ACTIONABLE `.plan-card` (approve/
  // review buttons + the plan text repeated verbatim in `.plan-card-raw`) —
  // pre-existing app behavior (switchAgentTab already triggers it on every
  // tab switch), not something this feature introduces. That both replaces
  // the collapsed "Show Plan" block with plain visible lines AND duplicates
  // the plan text once (in `.plan-card-raw`), which is why the match count
  // below is 7, not 6 — one real occurrence, counted where it now visibly
  // appears twice.
  await page.fill(`${scopeStruct}#chat-search-input-${struct}`, 'banana');
  await page.waitForFunction(
    (sel) => (document.querySelector(sel)?.textContent || '').trim().length > 0,
    `${scopeStruct}#chat-search-count-${struct}`, { timeout: 5000 });

  // "banana" appears: 1 (narration) + 1 (table cell) + 1 (narration) + 1 (narration)
  // + 1 (plan line one) + 1 (plan line one, again, inside .plan-card-raw) +
  // 1 (narration after the plan) = 7.
  const countText = (await page.textContent(`${scopeStruct}#chat-search-count-${struct}`)).trim();
  countText === '1 of 7' ? ok(`count is correct across plain text, a table cell, and a plan card ("${countText}")`)
    : fail(`expected "1 of 7", got "${countText}"`);

  const structuresAfter = await page.evaluate((sel) => {
    const out = document.querySelector(sel);
    return {
      tableRows: out.querySelectorAll('.hl-table table tr').length,
      tableCells: out.querySelectorAll('.hl-table table td, .hl-table table th').length,
      tableCellText: [...out.querySelectorAll('.hl-table table td, .hl-table table th')].map(td => td.textContent).join('|'),
      mermaidBlocks: out.querySelectorAll('.mermaid-block').length,
      mermaidSource: out.querySelector('.mermaid-block')?.getAttribute('data-source') || '',
      planCards: out.querySelectorAll('.plan-card').length,
      planApprove: out.querySelectorAll('.plan-card .btn-plan-approve').length,
      planReview: out.querySelectorAll('.plan-card .btn-plan-review').length,
      marksInTable: out.querySelectorAll('.hl-table mark.mc-chat-search-hit').length,
      marksInPlanCard: out.querySelectorAll('.plan-card mark.mc-chat-search-hit').length,
    };
  }, `${scopeStruct}#agent-output-${struct}`);
  (structuresAfter.tableRows === structuresBefore.tableRows && structuresAfter.tableCells === structuresBefore.tableCells)
    ? ok('table structure (rows/cells) UNCHANGED after the highlight pass')
    : fail(`table structure changed: ${JSON.stringify(structuresAfter)} vs before ${JSON.stringify(structuresBefore)}`);
  structuresAfter.tableCellText.includes('fruit') && structuresAfter.tableCellText.includes('banana') && structuresAfter.tableCellText.includes('apple')
    ? ok('table cell text intact (header + both data rows still readable)')
    : fail(`table cell text corrupted: "${structuresAfter.tableCellText}"`);
  structuresAfter.marksInTable >= 1 ? ok('the table-cell occurrence of the query IS highlighted (still findable inside a table)')
    : fail('no highlight found inside the table cell');
  (structuresAfter.mermaidBlocks === structuresBefore.mermaidBlocks && structuresAfter.mermaidSource === structuresBefore.mermaidSource)
    ? ok('mermaid block (count + source attribute) UNCHANGED after the highlight pass')
    : fail(`mermaid block changed: ${JSON.stringify(structuresAfter)} vs before ${JSON.stringify(structuresBefore)}`);
  (structuresAfter.planCards === 1 && structuresAfter.planApprove === 1 && structuresAfter.planReview === 1)
    ? ok('plan card (Approve + Review buttons) renders intact after the highlight pass')
    : fail(`plan card structure broken: ${JSON.stringify(structuresAfter)}`);
  structuresAfter.marksInPlanCard >= 1 ? ok('the occurrence inside the plan card IS highlighted (not skipped, not corrupted)')
    : fail('no highlight found inside the plan card');

  // Next / previous, with wraparound.
  await page.click(`${scopeStruct}#chat-search-next-${struct}`);
  let idx = (await page.textContent(`${scopeStruct}#chat-search-count-${struct}`)).trim();
  idx === '2 of 7' ? ok('Next advances to match 2') : fail(`Next: expected "2 of 7", got "${idx}"`);

  for (let i = 0; i < 6; i++) await page.click(`${scopeStruct}#chat-search-next-${struct}`);
  idx = (await page.textContent(`${scopeStruct}#chat-search-count-${struct}`)).trim();
  idx === '1 of 7' ? ok('Next wraps from the last match back to the first')
    : fail(`Next-wrap: expected "1 of 7", got "${idx}"`);

  await page.click(`${scopeStruct}#chat-search-prev-${struct}`);
  idx = (await page.textContent(`${scopeStruct}#chat-search-count-${struct}`)).trim();
  idx === '7 of 7' ? ok('Previous wraps from the first match back to the last')
    : fail(`Previous-wrap: expected "7 of 7", got "${idx}"`);

  // Enter / Shift+Enter drive the same nav.
  await page.focus(`${scopeStruct}#chat-search-input-${struct}`);
  await page.keyboard.press('Enter');
  idx = (await page.textContent(`${scopeStruct}#chat-search-count-${struct}`)).trim();
  idx === '1 of 7' ? ok('Enter in the input navigates to the next match (wraps 7→1)')
    : fail(`Enter: expected "1 of 7", got "${idx}"`);
  await page.keyboard.press('Shift+Enter');
  idx = (await page.textContent(`${scopeStruct}#chat-search-count-${struct}`)).trim();
  idx === '7 of 7' ? ok('Shift+Enter navigates to the previous match')
    : fail(`Shift+Enter: expected "7 of 7", got "${idx}"`);

  // No-match state.
  await page.fill(`${scopeStruct}#chat-search-input-${struct}`, 'no such fruit anywhere');
  await page.waitForFunction(
    (sel) => (document.querySelector(sel)?.textContent || '').includes('No matches'),
    `${scopeStruct}#chat-search-count-${struct}`, { timeout: 5000 });
  ok('no-match query reports "No matches", not a stale count');
  const navDisabled = await page.evaluate((sel) => document.querySelector(sel)?.disabled, `${scopeStruct}#chat-search-next-${struct}`);
  navDisabled ? ok('nav buttons disable on zero matches') : fail('next button still enabled with zero matches');

  // Escape closes and removes the highlight marks.
  await page.fill(`${scopeStruct}#chat-search-input-${struct}`, 'banana');
  await page.waitForFunction(
    (sel) => (document.querySelector(sel)?.textContent || '').trim() === '1 of 7',
    `${scopeStruct}#chat-search-count-${struct}`, { timeout: 5000 });
  await page.keyboard.press('Escape');
  const closedInfo = await page.evaluate(({ barSel, outSel }) => ({
    barGone: !document.querySelector(barSel),
    marksGone: document.querySelectorAll(outSel + ' mark.mc-chat-search-hit').length === 0,
  }), { barSel: `${scopeStruct}#chat-search-bar-${struct}`, outSel: `${scopeStruct}#agent-output-${struct}` });
  closedInfo.barGone ? ok('Escape closes the search bar') : fail('search bar still present after Escape');
  closedInfo.marksGone ? ok('Escape removes all highlight marks (text restored, not left half-wrapped)')
    : fail('highlight marks remain in the DOM after closing search');

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail('uncaught exception during interaction: ' + e));

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0
    ? '\n✅ PASS — in-chat search reaches matches above the render cap and missing from the client buffer, degrades honestly with no transcript, and a highlight pass leaves tables/mermaid/plan blocks intact.'
    : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
