#!/usr/bin/env node
/**
 * Day separators in agent chats (MC-954, backlog item 00a65265).
 *
 * conversation.js/resume-preview.js insert a centered "Today"/"Yesterday"/
 * "Mon, Sep 21" divider (`.chat-date-divider`, class + `data-date`) between
 * bubbles from different LOCAL calendar days, purely client-side — never
 * written into agentOutputBuffers, never sent to the agent.
 *
 * Deduped against the live DOM (querying `.chat-date-divider` children), the
 * same convention `renderAgentQuestion`'s `data-qid` dedupe uses — never a
 * JS Set that would outlive a rebuilt container (discovery_askuserquestion_
 * dom_dedup.md's bug class).
 *
 * REAL PER-LINE TIMESTAMPS (Dave's MC-954 scope correction): the server now
 * threads a per-line `log_line_ts` / SSE `ts` alongside every log_lines array
 * (agent_routes.py's `_transcript_buffer_lines_and_ts`, `/agent/status`,
 * `/session/<id>/reconstruct`, the SSE `output` event) into the client's
 * `agentOutputTimestamps[sid]` — same index as `agentOutputBuffers[sid]`.
 * `agentPanelHTML`'s cold render and `_repaintAgentOutput`'s replay both walk
 * that array and insert a divider at EVERY real day boundary they find, not
 * just one. Sections 1/4/5 below exercise the FALLBACK still used when no
 * per-line ts exists at all (pre-MC-954 data, or a path that genuinely can't
 * recover a date — Dave's "no divider rather than a wrong one" rule) — there
 * the only anchor available is the session's own `startedAt`, so a cold
 * render/repaint gets exactly one divider and a `false` dateHint on a
 * historical line renders no divider at all. Section 6 exercises the REAL
 * multi-day case now that per-line dates are wired through.
 *
 * STICKY (MC-954 follow-up, Ron's phone report 2026-09-24): Telegram/
 * WhatsApp-style behavior — the current day's label stays pinned at the top
 * of .agent-output (its own scrolling ancestor) while its messages are on
 * screen, swapping to the next/previous day's label as you scroll. Pure CSS
 * (`position: sticky; top: 0` on `.chat-date-divider`, app.css ~4235) — no
 * scroll-listener JS. Section 8 verifies it by scrolling into the middle of
 * each day's (15-line) block and confirming that day's label — not an
 * adjacent one — is the one actually pinned at the container's top.
 *
 * RUN: node chat-date-dividers.mjs
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
const PID = 'smoke_date_dividers';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

function fixtureProject(id, name) {
  return {
    id, name, status: 'active', domain: 'general', emoji: '\u{1f4c5}',
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
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Date Divider Smoke')]);

// Same key format conversation.js's _dateKeyLocal uses — computed here in
// Node against the SAME real clock the browser will use, so the test isn't
// racing a hardcoded date across a midnight boundary.
function dateKeyLocal(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
const now = new Date();
const yesterday = new Date(now);
yesterday.setDate(yesterday.getDate() - 1);
const todayKey = dateKeyLocal(now);
const yesterdayKey = dateKeyLocal(yesterday);
const startedAtISO = yesterday.toISOString();

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

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
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });

  if (pageErrors.length) pageErrors.forEach((e) => fail('uncaught page error during boot: ' + e));
  else ok('app booted clean, no uncaught exceptions');

  // ── 1. Cold render, NO per-line ts (fallback path): one anchor divider ────
  const sid = await page.evaluate(({ pid, startedAt }) => {
    const id = 'sess-dividers';
    agentHistory.unshift({ projectId: pid, sessionId: id, projectName: 'Date Divider Smoke', task: 'long thread', status: 'completed', startedAt });
    agentStatusCache[id] = { status: 'completed', task: 'long thread', projectId: pid, startedAt, claudeSessionId: 'csid-dividers' };
    agentOutputBuffers[id] = ['Narration from yesterday, line one.', 'Narration from yesterday, line two.'];
    openProjectModal(pid);
    return id;
  }, { pid: PID, startedAt: startedAtISO });
  await page.waitForSelector(`.modal-window[data-modal-id="${PID}"] #agent-output-${sid}`, { timeout: 5000 });
  const scope = `.modal-window[data-modal-id="${PID}"] `;
  const outSel = `${scope}#agent-output-${sid}`;

  const coldDividers = await page.evaluate((sel) =>
    [...document.querySelectorAll(sel + ' .chat-date-divider')].map(d => ({ date: d.dataset.date, text: d.textContent })),
    outSel);
  coldDividers.length === 1 ? ok(`cold render: exactly one divider (${JSON.stringify(coldDividers)})`)
    : fail(`cold render: expected exactly 1 divider, got ${JSON.stringify(coldDividers)}`);
  coldDividers[0]?.date === yesterdayKey ? ok(`cold divider dated correctly (${yesterdayKey})`)
    : fail(`cold divider date: expected ${yesterdayKey}, got ${coldDividers[0]?.date}`);
  coldDividers[0]?.text === 'Yesterday' ? ok('cold divider reads "Yesterday"')
    : fail(`cold divider text: expected "Yesterday", got "${coldDividers[0]?.text}"`);
  const dividerIsFirst = await page.evaluate((sel) => document.querySelector(sel).firstElementChild?.classList.contains('chat-date-divider'), outSel);
  dividerIsFirst ? ok('divider is the first child (renders above the first bubble)')
    : fail('divider is not the first child of the output container');

  // ── 2. A live line arriving "now" crosses the day boundary ────────────────
  await page.evaluate((id) => { appendAgentLine(id, 'A brand new live line just arrived.'); }, sid);
  const afterLive = await page.evaluate((sel) =>
    [...document.querySelectorAll(sel + ' .chat-date-divider')].map(d => ({ date: d.dataset.date, text: d.textContent })),
    outSel);
  afterLive.length === 2 ? ok(`live line crossing midnight adds a second divider (${JSON.stringify(afterLive)})`)
    : fail(`expected 2 dividers after a live cross-day line, got ${JSON.stringify(afterLive)}`);
  afterLive[1]?.date === todayKey && afterLive[1]?.text === 'Today' ? ok('new divider is dated today and reads "Today"')
    : fail(`second divider wrong: expected {date:"${todayKey}",text:"Today"}, got ${JSON.stringify(afterLive[1])}`);

  // ── 3. DOM-dedup: another live line the SAME day must NOT add a 3rd ───────
  await page.evaluate((id) => { appendAgentLine(id, 'Another live line, same day.'); }, sid);
  const afterSecondLive = await page.evaluate((sel) => document.querySelectorAll(sel + ' .chat-date-divider').length, outSel);
  afterSecondLive === 2 ? ok('a second same-day live line does not duplicate the divider (DOM-deduped, not a stale JS Set)')
    : fail(`expected divider count to stay at 2, got ${afterSecondLive}`);

  // ── 4. Split-pane hydration path (_repaintAgentOutput), NO per-line ts: ───
  //      falls back to the same one accurate anchor divider — proves the
  //      fallback mechanism isn't cold-render-only.
  await page.evaluate(({ pid, startedAt }) => {
    const id2 = 'sess-dividers-split';
    agentStatusCache[id2] = { status: 'completed', task: 'split pane thread', projectId: pid, startedAt, claudeSessionId: 'csid-dividers-2' };
    agentOutputBuffers[id2] = ['A split-pane narration line from yesterday.'];
    const host = document.createElement('div');
    host.id = `agent-output-${id2}`;
    host.className = 'agent-output';
    document.body.appendChild(host);
    window._repaintAgentOutput(id2);
  }, { pid: PID, startedAt: startedAtISO });
  const splitDividers = await page.evaluate((id2) =>
    [...document.querySelectorAll(`#agent-output-${id2} .chat-date-divider`)].map(d => d.dataset.date), 'sess-dividers-split');
  (splitDividers.length === 1 && splitDividers[0] === yesterdayKey)
    ? ok('split-view hydration path (_repaintAgentOutput) seeds the same anchor divider')
    : fail(`split-pane hydration divider wrong: ${JSON.stringify(splitDividers)}`);

  // ── 5. Documented limitation: a buffer-growth repaint of the PRIMARY ──────
  //      session (reconciliation path, never the hot streaming path) replays
  //      from the buffer with no per-line dates, so it can only re-seed the
  //      one known anchor — the live "Today" divider from step 2 does not
  //      survive a full repaint. Push the two live lines into the buffer
  //      (as the real SSE handler would) and repaint to confirm this is
  //      exactly what happens, not a silent duplicate/missing state.
  await page.evaluate((id) => {
    agentOutputBuffers[id].push('A brand new live line just arrived.', 'Another live line, same day.');
    window._repaintAgentOutput(id);
  }, sid);
  const afterRepaint = await page.evaluate((sel) =>
    [...document.querySelectorAll(sel + ' .chat-date-divider')].map(d => ({ date: d.dataset.date, text: d.textContent })),
    outSel);
  (afterRepaint.length === 1 && afterRepaint[0].date === yesterdayKey)
    ? ok('documented limitation confirmed: a buffer repaint re-seeds only the one known anchor divider (no per-line history dates exist to restore "Today")')
    : fail(`repaint behavior changed unexpectedly: ${JSON.stringify(afterRepaint)} — update this test AND the MC-954 report if this is now backed by real per-line timestamps`);

  // ── 6. REAL per-line timestamps (MC-954 server plumbing), 3 different ─────
  //      days in ONE buffer: proves the per-line day-boundary WALK added to
  //      _repaintAgentOutput/_maybeInsertDateDivider, not just the
  //      single-anchor fallback exercised in sections 1/4/5 above.
  const dayBefore = new Date(now);
  dayBefore.setDate(dayBefore.getDate() - 2);
  const dayBeforeKey = dateKeyLocal(dayBefore);
  await page.evaluate(({ pid, d0, d1, d2 }) => {
    const id3 = 'sess-dividers-multiday';
    agentStatusCache[id3] = { status: 'completed', task: 'multi-day thread', projectId: pid, startedAt: d0, claudeSessionId: 'csid-dividers-3' };
    agentOutputBuffers[id3] = ['Line from two days ago.', 'Line from yesterday.', 'Line from today.'];
    agentOutputTimestamps[id3] = [d0, d1, d2];
    const host = document.createElement('div');
    host.id = `agent-output-${id3}`;
    host.className = 'agent-output';
    document.body.appendChild(host);
    window._repaintAgentOutput(id3);
  }, { pid: PID, d0: dayBefore.toISOString(), d1: yesterday.toISOString(), d2: now.toISOString() });
  const multiDayDividers = await page.evaluate((id3) =>
    [...document.querySelectorAll(`#agent-output-${id3} .chat-date-divider`)].map(d => ({ date: d.dataset.date, text: d.textContent })),
    'sess-dividers-multiday');
  multiDayDividers.length === 3 ? ok(`real per-line ts: 3 dividers render for 3 real days in one buffer (${JSON.stringify(multiDayDividers)})`)
    : fail(`real per-line ts: expected 3 dividers, got ${JSON.stringify(multiDayDividers)}`);
  const expectedKeys = [dayBeforeKey, yesterdayKey, todayKey];
  const gotKeys = multiDayDividers.map((d) => d.date);
  JSON.stringify(gotKeys) === JSON.stringify(expectedKeys) ? ok(`dividers ordered/dated correctly (${gotKeys.join(', ')})`)
    : fail(`divider dates wrong: expected ${JSON.stringify(expectedKeys)}, got ${JSON.stringify(gotKeys)}`);
  (multiDayDividers[1]?.text === 'Yesterday' && multiDayDividers[2]?.text === 'Today')
    ? ok('labels read "Yesterday"/"Today" correctly for the two recent days')
    : fail(`labels wrong: ${JSON.stringify(multiDayDividers.map((d) => d.text))}`);

  // ── 7. MOBILE viewport, through the REAL click + fetch path ───────────────
  //      Ron reported no date labels on mobile (MC-954 follow-up) even after
  //      a server restart; every check above runs at a 1400px desktop
  //      viewport and seeds agentStatusCache/agentOutputTimestamps by direct
  //      object injection, so neither the mobile drill-down list (WhatsApp-
  //      Communities layout, isMobileChatList()===true) nor the real
  //      /conversations + /session/<id>/reconstruct fetch path that populates
  //      agentOutputTimestamps in production (openConversation, conversation.js
  //      ~3565-3571) was ever exercised. Reproduces the exact user gesture:
  //      open the project, land on the conversation list, tap a row.
  const mobileCtx = await browser.newContext({ viewport: { width: 412, height: 915 } });
  const mobilePage = await mobileCtx.newPage();
  const mobilePageErrors = [];
  mobilePage.on('pageerror', (e) => mobilePageErrors.push(e.message || String(e)));
  const RECON_SID = 'sess-dividers-mobile-reconstruct';
  const reconDayBefore = new Date(now);
  reconDayBefore.setDate(reconDayBefore.getDate() - 1);
  const reconLogLines = ['Line from yesterday (mobile reconstruct).', 'Line from today (mobile reconstruct).'];
  const reconLogTs = [reconDayBefore.toISOString(), now.toISOString()];
  await mobilePage.route('**/*', (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === `/api/project/${PID}/conversations`) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{
        mc_session_id: RECON_SID, claude_session_id: 'csid-mobile-reconstruct', provider: 'claude',
        live: false, status: 'completed', label: 'mobile reconstruct thread', first_user: 'mobile reconstruct thread',
        last_user: 'mobile reconstruct thread', ts: reconLogTs[1], ts_relative: '0m ago', turns: 1,
        waiting_for_question: false, waiting_for_plan_approval: false, resumable: true,
      }]) });
    }
    if (path === `/api/project/${PID}/session/${RECON_SID}/reconstruct`) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        task: 'mobile reconstruct thread', started_at: reconLogTs[0], claude_session_id: 'csid-mobile-reconstruct',
        log_lines: reconLogLines, log_line_ts: reconLogTs,
      }) });
    }
    return route.abort();
  });
  await mobilePage.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await mobilePage.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await mobilePage.evaluate((pid) => openProjectModal(pid), PID);
  const convRowSel = `.modal-window[data-modal-id="${PID}"] .conv-row[data-mcsid="${RECON_SID}"]`;
  const rowFound = await mobilePage.waitForSelector(convRowSel, { timeout: 5000 }).then(() => true).catch(() => false);
  rowFound ? ok('mobile drill-down list renders the conversation row (isMobileChatList() path)')
    : fail('mobile drill-down list never rendered a conv-row for the fixture session — cannot proceed with click-through check');
  if (rowFound) {
    await mobilePage.click(convRowSel);
    await mobilePage.waitForSelector(`.modal-window[data-modal-id="${PID}"] #agent-output-${RECON_SID}`, { timeout: 5000 }).catch(() => {});
    const mobileDividers = await mobilePage.evaluate((sid) =>
      [...document.querySelectorAll(`#agent-output-${sid} .chat-date-divider`)].map(d => ({ date: d.dataset.date, text: d.textContent, visible: d.getClientRects().length > 0 })),
      RECON_SID);
    mobileDividers.length === 2 ? ok(`mobile real-fetch reconstruct path: 2 dividers render (${JSON.stringify(mobileDividers)})`)
      : fail(`mobile real-fetch reconstruct path: expected 2 dividers, got ${JSON.stringify(mobileDividers)}`);
    mobileDividers.every(d => d.visible) ? ok('mobile dividers are actually visible in the DOM (not display:none / zero-size)')
      : fail(`mobile dividers present but not visible: ${JSON.stringify(mobileDividers)}`);
  }
  const uncaughtMobile = mobilePageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaughtMobile.length) uncaughtMobile.forEach((e) => fail('uncaught page error on mobile: ' + e));
  await mobileCtx.close();

  // ── 8. STICKY follow-up (MC-954, backlog item 00a65265): each divider is
  //      `position: sticky; top: 0` inside .agent-output — its own scrolling
  //      ancestor — so it pins to the top for as long as its day's messages
  //      are on screen (Telegram/WhatsApp behavior), no scroll-listener JS.
  //      Multiple dividers can be simultaneously "stuck" at the same pixel
  //      (they share one containing block — the whole scroll container — so
  //      CSS doesn't evict an earlier one once a later one also qualifies);
  //      what the USER sees is only the latest DOM-order one, since it
  //      paints on top of the earlier ones at the identical spot (confirmed
  //      against the live server — see docs/_journal for the investigation).
  //      15 lines/day so each day's block is taller than any viewport,
  //      giving real "scrolled into the middle of a day" positions.
  const stickyLines = [];
  const stickyTs = [];
  [dayBefore, yesterday, now].forEach((d, di) => {
    for (let i = 1; i <= 15; i++) { stickyLines.push(`Sticky-check line ${i}, day ${di}.`); stickyTs.push(d.toISOString()); }
  });
  const stickyId = 'sess-dividers-sticky';
  await page.evaluate(({ pid, id, lines, ts, startedAt }) => {
    agentStatusCache[id] = { status: 'completed', task: 'sticky thread', projectId: pid, startedAt, claudeSessionId: 'csid-dividers-sticky' };
    agentOutputBuffers[id] = lines;
    agentOutputTimestamps[id] = ts;
    const host = document.createElement('div');
    host.id = `agent-output-${id}`;
    host.className = 'agent-output';
    host.style.height = '500px';   // bounded height so overflow-y:auto actually scrolls (no flex parent here to size it)
    document.body.appendChild(host);
    window._repaintAgentOutput(id);
  }, { pid: PID, id: stickyId, lines: stickyLines, ts: stickyTs, startedAt: dayBefore.toISOString() });
  const outSel8 = `#agent-output-${stickyId}`;

  const cssOk = await page.evaluate((sel) =>
    [...document.querySelectorAll(sel + ' .chat-date-divider')].every((d) => getComputedStyle(d).position === 'sticky'),
    outSel8);
  cssOk ? ok('every divider computes position:sticky') : fail('a divider did not compute position:sticky');

  // Reset to top first: offsetTop on a stuck sticky element reflects its
  // CURRENT screen position, not its true document position, once scrolled.
  await page.evaluate((sel) => { document.querySelector(sel).scrollTop = 0; }, outSel8);
  const staticOffsets = await page.evaluate((sel) =>
    [...document.querySelectorAll(sel + ' .chat-date-divider')].map((d) => ({ text: d.textContent, offsetTop: d.offsetTop })),
    outSel8);
  const yestOff = staticOffsets.find((d) => d.text === 'Yesterday');
  const todayOff = staticOffsets.find((d) => d.text === 'Today');
  if (!yestOff || !todayOff) {
    fail(`sticky check: expected Yesterday/Today dividers, got ${JSON.stringify(staticOffsets)}`);
  } else {
    const baseline = await page.evaluate((sel) => {
      const out = document.querySelector(sel);
      const first = out.querySelector('.chat-date-divider');
      return first.getBoundingClientRect().top - out.getBoundingClientRect().top;
    }, outSel8);
    async function pinnedLabelAt(scrollTop) {
      await page.evaluate(({ sel, top }) => { document.querySelector(sel).scrollTop = top; }, { sel: outSel8, top: scrollTop });
      return page.evaluate(({ sel, baseline }) => {
        const out = document.querySelector(sel);
        const divs = [...out.querySelectorAll('.chat-date-divider')];
        const outRect = out.getBoundingClientRect();
        const matches = divs.filter((d) => Math.abs((d.getBoundingClientRect().top - outRect.top) - baseline) < 3);
        const pinned = matches[matches.length - 1];
        return pinned ? pinned.textContent : null;
      }, { sel: outSel8, baseline });
    }
    const midYesterday = await pinnedLabelAt(yestOff.offsetTop + 300);
    midYesterday === 'Yesterday' ? ok('scrolled into the middle of "Yesterday": that label stays pinned at the top')
      : fail(`scrolled mid-Yesterday: expected "Yesterday" pinned, got ${JSON.stringify(midYesterday)}`);
    const midToday = await pinnedLabelAt(todayOff.offsetTop + 300);
    midToday === 'Today' ? ok('scrolled into the middle of "Today": that label stays pinned at the top (previous day pushed out)')
      : fail(`scrolled mid-Today: expected "Today" pinned, got ${JSON.stringify(midToday)}`);
  }

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail('uncaught exception during interaction: ' + e));

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0
    ? '\n✅ PASS — date dividers render on cold load, cross the day boundary live, dedupe against the DOM, the single-anchor fallback is confirmed, real per-line server timestamps render all 3 dividers for a 3-day buffer, the mobile drill-down list + real fetch/reconstruct path also renders visible dividers, and each day\'s label stays sticky-pinned at the top while scrolling through that day.'
    : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
