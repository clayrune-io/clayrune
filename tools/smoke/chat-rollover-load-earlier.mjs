#!/usr/bin/env node
/**
 * "Load earlier conversation" (MC-978): a rolled-over chat only ever seeds its
 * HEAD transcript into the chat pane — everything before the last auto-fresh
 * rollover is on disk (server `rolled_from` chain) but was unreachable from the
 * UI. conversation.js now renders a control above the messages whenever the
 * open session's row (conversationsCache) carries `rolled_from` entries not
 * yet pulled in (rolloverLoadedCounts), and `loadEarlierConversationPart`
 * fetches one PRIOR LINK at a time — nearest-to-head first — via the existing
 * GET .../transcript/<csid>/full-buffer route and prepends it to the buffer.
 *
 * Hermetic boot (real index.html + real static/js/*.js, no real backend — same
 * shape as chat-search.mjs). conversationsCache/agentStatusCache/
 * agentOutputBuffers are seeded directly via page.evaluate rather than through
 * a mocked /conversations fetch: the render reads conversationsCache live, so
 * seeding it is equivalent and keeps the fixture small.
 *
 * NOT IN THE FIRST PAGE (2026-09-26): /conversations?limit=20 returned 18
 * Scribe/condense transform transcripts for drop_shipping_company, so a chat
 * older than the 20 freshest had no row, no `rolled_from`, and no button. The
 * mock serves 20 filler rows WITHOUT the head unless the request carries
 * `include=csid-head`; the open chat must fetch its own row that way.
 *
 * Fixture chain: oldest -> middle -> head (2 older links). Walks BOTH clicks:
 *   click 1 (part 2 of 3) loads 'middle' (nearest-to-head), prepended above
 *            the head's own lines already in the buffer;
 *   click 2 (part 1 of 3) loads 'oldest', and the button then disappears —
 *            nothing left to load.
 *
 * RUN: node chat-rollover-load-earlier.mjs
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
const PID = 'smoke_rollover_load_earlier';

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
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Rollover Load-Earlier Smoke')]);

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

const MIDDLE_NEEDLE = 'MIDDLE_LINK_TAMARIN_9021';
const OLDEST_NEEDLE = 'OLDEST_LINK_PANGOLIN_3384';
const MIDDLE_LINES = ['\n> Ron: add Reddit to it\n', `continuing from the channel list — ${MIDDLE_NEEDLE}`];
const OLDEST_LINES = ['\n> Ron: what is our GTM channel list?\n', `here is the list of channels — ${OLDEST_NEEDLE}`];

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  const fetched = [];
  const convRequests = [];
  const FILLER = Array.from({ length: 20 }, (_, i) => ({
    claude_session_id: `csid-filler-${i}`, mc_session_id: `sess-filler-${i}`, turns: 2,
    label: `filler chat ${i}`, rolled_from: [] }));
  await page.route('**/*', (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/transcript/csid-middle/full-buffer`) {
      fetched.push('csid-middle');
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ claude_session_id: 'csid-middle', log_lines: MIDDLE_LINES, log_line_ts: [null, null] }) });
    }
    if (path === `/api/project/${PID}/transcript/csid-oldest/full-buffer`) {
      fetched.push('csid-oldest');
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ claude_session_id: 'csid-oldest', log_lines: OLDEST_LINES, log_line_ts: [null, null] }) });
    }
    if (path === `/api/project/${PID}/conversations`) {
      const inc = (url.searchParams.get('include') || '').split(',');
      convRequests.push(url.search);
      const head = { claude_session_id: 'csid-head', mc_session_id: 'sess-head', turns: 3,
        rolled_from: ['csid-oldest', 'csid-middle'] };
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify(inc.includes('csid-head') ? [head, ...FILLER] : FILLER) });
    }
    if (path.endsWith('/full-buffer')) {
      return route.fulfill({ status: 404, contentType: 'application/json', body: '{"error":"transcript not found or empty"}' });
    }
    return route.abort();
  });
  await page.addInitScript((f) => { window.__FILLER = f; }, FILLER);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });

  if (pageErrors.length) pageErrors.forEach((e) => fail('uncaught page error during boot: ' + e));
  else ok('app booted clean, no uncaught exceptions');

  const bridged = await page.evaluate(() => typeof window.loadEarlierConversationPart === 'function');
  bridged ? ok('loadEarlierConversationPart is bridged onto window')
    : fail('loadEarlierConversationPart missing — is the page serving stale JS?');
  if (!bridged) throw new Error('no bridge');

  const sid = await page.evaluate(({ pid }) => {
    const s = 'sess-head';
    conversationsCache[pid] = [{
      claude_session_id: 'csid-head', mc_session_id: s, turns: 3,
      rolled_from: ['csid-oldest', 'csid-middle'],
    }];
    agentHistory.unshift({ projectId: pid, sessionId: s, projectName: 'Rollover Load-Earlier Smoke', task: 'rolled chat', status: 'completed', startedAt: new Date().toISOString() });
    agentStatusCache[s] = { status: 'completed', task: 'rolled chat', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: 'csid-head' };
    agentOutputBuffers[s] = ['\n> Ron: what about LinkedIn\n', 'head reply — Reddit added earlier'];
    agentOutputTimestamps[s] = [null, null];
    openProjectModal(pid);
    return s;
  }, { pid: PID });
  await page.waitForSelector(`.modal-window[data-modal-id="${PID}"] #agent-output-${sid}`, { timeout: 5000 });
  const scope = `.modal-window[data-modal-id="${PID}"] `;

  // ── Before any click: button present, correctly counted, nothing fetched ──
  const btnSel = `${scope}#rollover-load-${sid}`;
  const btnText0 = (await page.textContent(btnSel).catch(() => null)) || '';
  btnText0.includes('part 2 of 3') ? ok(`"Load earlier conversation" button shows "part 2 of 3" (${btnText0.trim()})`)
    : fail(`expected the button text to include "part 2 of 3", got "${btnText0.trim()}"`);
  fetched.length === 0 ? ok('no /full-buffer fetch has happened yet (lazy — nothing loaded until clicked)')
    : fail(`unexpected eager fetch(es): ${JSON.stringify(fetched)}`);
  const headBodyBefore = await page.textContent(`${scope}#agent-output-${sid}`);
  !headBodyBefore.includes(MIDDLE_NEEDLE) && !headBodyBefore.includes(OLDEST_NEEDLE)
    ? ok('neither older link\'s content is on screen before loading it')
    : fail('older-link content already visible before the button was clicked');

  // ── Every other repaint path keeps the control (the 2026-09-26 regression) ──
  // switchAgentTab repaints via _repaintAgentOutput right after fetchAgentStatus
  // on EVERY rail open; before the fix that wiped the button within ~300ms.
  await page.evaluate((s) => { window._repaintAgentOutput(s); window._repaintAgentOutput(s); }, sid);
  const afterRepaint = await page.evaluate((s) => ({
    n: document.querySelectorAll(`#rollover-load-${s}`).length,
    text: document.getElementById(`rollover-load-${s}`)?.textContent || '',
  }), sid);
  afterRepaint.n === 1 && afterRepaint.text.includes('part 2 of 3')
    ? ok('button survives _repaintAgentOutput (the switchAgentTab path), exactly once, still "part 2 of 3"')
    : fail(`after _repaintAgentOutput expected 1 button "part 2 of 3", got ${JSON.stringify(afterRepaint)}`);

  // NOT in the first page: the cached list is 20 fresher chats, none of them
  // the open one. The repaint finds no row, so no button yet; it must then
  // fetch its own row (`include=`) and add the button when that lands,
  // through the late-fetch path that keeps the already-mounted output node.
  const openState = await page.evaluate(({ pid, s }) => {
    conversationsCache[pid] = JSON.parse(JSON.stringify(window.__FILLER));
    window._repaintAgentOutput(s);
    return { noBtn: !document.getElementById(`rollover-load-${s}`), tab: activeAgentTab[pid] };
  }, { pid: PID, s: sid, });
  openState.noBtn ? ok('open chat absent from a 20-row first page -> no row yet, no button yet')
    : fail('button rendered with no rolled_from row to back it');
  openState.tab === sid ? ok('the chat is the project active tab (activeAgentTab)')
    : fail(`activeAgentTab is ${JSON.stringify(openState.tab)}, expected ${sid}`);
  await page.waitForFunction((s) => !!document.getElementById(`rollover-load-${s}`), sid, { timeout: 5000 })
    .then(() => ok('button appears after the chat fetches its own row, with no user action'))
    .catch(() => fail('open chat outside the first page never got its row, so never got the button'));
  convRequests.some((q) => q.includes('include=csid-head'))
    ? ok(`/conversations was asked to include the open chat's csid (${convRequests.filter((q) => q.includes('include=')).join(' ')})`)
    : fail(`no /conversations request carried include=csid-head: ${JSON.stringify(convRequests)}`);
  const inCache = await page.evaluate((pid) => (conversationsCache[pid] || []).some((c) => c.claude_session_id === 'csid-head'), PID);
  inCache ? ok('the open chat row is now in conversationsCache')
    : fail('the open chat row never reached conversationsCache');

  // ── Click 1: loads 'middle' (nearest-to-head), prepended above head lines ──
  await page.click(btnSel);
  await page.waitForFunction(({ needle, sel }) => (document.querySelector(sel)?.innerText || '').includes(needle),
    { needle: MIDDLE_NEEDLE, sel: `${scope}#agent-output-${sid}` }, { timeout: 5000 });
  fetched.length === 1 && fetched[0] === 'csid-middle'
    ? ok('click 1 fetched csid-middle (the nearest-to-head predecessor), not csid-oldest')
    : fail(`click 1: expected exactly ["csid-middle"], got ${JSON.stringify(fetched)}`);

  const order1 = await page.evaluate((sel) => document.querySelector(sel)?.innerText || '', `${scope}#agent-output-${sid}`);
  const midPos = order1.indexOf(MIDDLE_NEEDLE);
  const headPos = order1.indexOf('Reddit added earlier');
  (midPos >= 0 && headPos >= 0 && midPos < headPos)
    ? ok('the newly-loaded middle-link content renders ABOVE the head\'s own lines')
    : fail(`expected middle content before head content in the rendered order; positions mid=${midPos} head=${headPos}`);

  const btnText1 = (await page.textContent(btnSel).catch(() => null)) || '';
  btnText1.includes('part 1 of 3') ? ok(`after click 1, button now shows "part 1 of 3" (${btnText1.trim()})`)
    : fail(`expected "part 1 of 3" after one load, got "${btnText1.trim()}"`);

  const notTruncatedAfterLoad = await page.evaluate((sel) => !document.querySelector(sel), `${scope}[onclick*="expandAgentOutput"]`);
  notTruncatedAfterLoad ? ok('loading an earlier link forces the full render (no re-truncation hiding what was just fetched)')
    : fail('the "N earlier lines" truncation button reappeared, hiding the newly-loaded content');

  // ── Click 2: loads 'oldest' — button disappears, nothing left to load ──
  await page.click(btnSel);
  await page.waitForFunction(({ needle, sel }) => (document.querySelector(sel)?.innerText || '').includes(needle),
    { needle: OLDEST_NEEDLE, sel: `${scope}#agent-output-${sid}` }, { timeout: 5000 });
  fetched.length === 2 && fetched[1] === 'csid-oldest'
    ? ok('click 2 fetched csid-oldest (the chain\'s remaining, oldest link)')
    : fail(`click 2: expected fetch order ["csid-middle","csid-oldest"], got ${JSON.stringify(fetched)}`);

  const order2 = await page.evaluate((sel) => document.querySelector(sel)?.innerText || '', `${scope}#agent-output-${sid}`);
  const oldPos = order2.indexOf(OLDEST_NEEDLE);
  const midPos2 = order2.indexOf(MIDDLE_NEEDLE);
  (oldPos >= 0 && midPos2 >= 0 && oldPos < midPos2)
    ? ok('the oldest link renders above the middle link, which renders above the head — full chain in order')
    : fail(`expected oldest before middle in the rendered order; positions old=${oldPos} mid=${midPos2}`);

  const btnGone = await page.evaluate((sel) => !document.querySelector(sel), btnSel);
  btnGone ? ok('the "Load earlier conversation" button is gone — the whole chain is now loaded')
    : fail('button still present after every link in the chain was loaded');

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail('uncaught exception during interaction: ' + e));

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0
    ? '\n✅ PASS — a rolled chat\'s "Load earlier conversation" control counts down correctly, fetches each older link nearest-to-head-first, prepends it in chain order, and disappears once the whole chain is loaded.'
    : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
