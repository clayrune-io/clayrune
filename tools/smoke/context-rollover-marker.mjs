#!/usr/bin/env node
/**
 * Context-rollover / starting-fresh notices render as a small inline marker
 * (static/js/context-rollover-marker.js, static/css/context-rollover-marker.css).
 *
 * Ron, 2026-10-15 (MC-1101): "[Context rolled over mid-task — continuing in a
 * fresh session]" and "[Session context is Nk tokens — starting fresh]" each
 * took a full chat row and broke reading flow. Each is now a hairline divider
 * with a tiny pill; the full sentence is the tooltip and unfolds on click.
 *
 * Checks, 1440px AND 390px:
 *   1. cold render (history path): every server-written variant is a
 *      .agent-line-rollover with a pill, detail hidden, strip <= 14px tall and
 *      markedly shorter than an ordinary status row in the same chat;
 *   2. lookalikes are NOT markers: another bracketed status line, an agent
 *      reply that merely quotes the phrase, a typed message that quotes it;
 *   3. the pill's tooltip is the full original sentence;
 *   4. click, Enter and Space expand/collapse the full sentence;
 *   5. refreshModal rebuild keeps the reader's open marker open;
 *   6. live path (appendAgentLine) produces the same marker;
 *   7. no horizontal overflow; screenshots land in _scratch/rollover_marker/.
 *
 * RUN: node tools/smoke/context-rollover-marker.mjs
 */
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_rollover';
const STATIC = loadStaticJsCss(REPO_ROOT);
const SHOTS = resolve(REPO_ROOT, '_scratch', 'rollover_marker');
mkdirSync(SHOTS, { recursive: true });

const project = {
  id: PID, name: 'Rollover Smoke', status: 'active', domain: 'general', emoji: '\u{1f504}',
  description: '', summary: '', current_task: 'Idle', next_action: '',
  blocked: false, blocked_reason: null, activity_log: [], backlog: [],
  project_path: '/smoke/' + PID, last_updated: '2026-10-15T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null,
  display_order: 0, provider: 'claude', use_streaming_agent: true,
  distiller_mode: 'proposed', distiller_min_recurrence: 3,
  distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
  distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
  distiller_skip_errors: true,
};
const PROJECTS_JSON = JSON.stringify([project]);

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, pass, failMsg) => (cond ? ok(pass) : fail(failMsg));

// The exact sentences mc/blueprints/agent_routes.py writes.
const ROLLOVER = '[Context rolled over mid-task — continuing in a fresh session]';
const TOKENS = '[Session context is 106k tokens — starting fresh]';
const SIZE = '[Session transcript too large (52 MB) — starting fresh]';
const RESUME_FAILED = '[Resume of session 0123456789ab failed — restarting fresh]';
const NO_OUTPUT = '[Resume produced no output before exiting — restarting fresh]';
const RECOVER = '[Previous rollover was interrupted before it produced a transcript — recovering the real handoff]';
const MARKERS = [
  [ROLLOVER, 'rollover', 'Rolled over'],
  [TOKENS, 'fresh', 'Fresh start · 106k'],
  [SIZE, 'fresh', 'Fresh start · 52 MB'],
  [RESUME_FAILED, 'restart', 'Restarted fresh'],
  [NO_OUTPUT, 'restart', 'Restarted fresh'],
  [RECOVER, 'recover', 'Recovering handoff'],
];
const OTHER_STATUS = '[Got your message]';
const TYPED_QUOTE = '> Ron: why does it print [Context rolled over mid-task — continuing in a fresh session] so often?';
const REPLY_QUOTE = 'The notice [Session context is 106k tokens — starting fresh] appears when the cap is hit.';
const BUFFER = [
  '> Ron: please keep going', 'Working on it now.', '[tool: Bash] git status', ROLLOVER, 'Picking the task back up after the rollover.',
  TOKENS, 'A reply between the two notices.', SIZE, RESUME_FAILED, NO_OUTPUT, RECOVER,
  OTHER_STATUS, REPLY_QUOTE, TYPED_QUOTE, 'Final agent line.',
];
const TS = BUFFER.map((_, i) => new Date(2026, 9, 15, 10, 10 + i).toISOString());

async function newPage(browser, viewport, extraRoute) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message || String(e)));
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    const extra = extraRoute && extraRoute(path, route);
    if (extra) return extra;
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  return { page, errors };
}

// Real-world noise, not a regression: the hermetic harness aborts the mermaid CDN.
const realErrors = (errs) => errs.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));

async function run(browser, label, viewport, sid) {
  console.log(`\n── ${label} (${viewport.width}px) ──`);
  const mobile = viewport.width <= 960;
  const extraRoute = mobile ? (path, route) => {
    if (path === `/api/project/${PID}/conversations`) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{
        mc_session_id: sid, claude_session_id: 'csid-' + sid, provider: 'claude', live: false, status: 'completed',
        label: 'rollover smoke thread', first_user: 'rollover smoke thread', last_user: 'rollover smoke thread',
        ts: TS[TS.length - 1], ts_relative: '0m ago', turns: 1, waiting_for_question: false,
        waiting_for_plan_approval: false, resumable: true }]) });
    }
    if (path === `/api/project/${PID}/session/${sid}/reconstruct`) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        task: 'rollover smoke thread', started_at: TS[0], claude_session_id: 'csid-' + sid,
        log_lines: BUFFER, log_line_ts: TS }) });
    }
    return null;
  } : null;
  const { page, errors } = await newPage(browser, viewport, extraRoute);
  const outSel = `.modal-window[data-modal-id="${PID}"] #agent-output-${sid}`;
  if (mobile) {
    await page.evaluate((pid) => openProjectModal(pid), PID);
    const rowSel = `.modal-window[data-modal-id="${PID}"] .conv-row[data-mcsid="${sid}"]`;
    await page.waitForSelector(rowSel, { timeout: 5000 });
    await page.click(rowSel);
    await page.waitForSelector(outSel, { timeout: 5000 });
  } else {
    await page.evaluate(({ pid, sid, buf, ts }) => {
      agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Rollover Smoke', task: 'a thread', status: 'completed', startedAt: ts[0] });
      agentStatusCache[sid] = { status: 'completed', task: 'a thread', projectId: pid, startedAt: ts[0], claudeSessionId: 'csid-' + sid };
      agentOutputBuffers[sid] = buf;
      agentOutputTimestamps[sid] = ts;
      openProjectModal(pid);
    }, { pid: PID, sid, buf: BUFFER, ts: TS });
    await page.waitForSelector(outSel, { timeout: 5000 });
  }
  realErrors(errors).length ? realErrors(errors).forEach((e) => fail('page error during boot/open: ' + e)) : ok('app booted and chat opened clean');

  // 1. every variant is a collapsed marker, one slim strip
  const rows = await page.evaluate((sel) => [...document.querySelectorAll(sel + ' .agent-line-rollover')].map((r) => {
    const pill = r.querySelector('.ro-pill');
    return {
      kind: r.querySelector('.ro-row') && r.querySelector('.ro-row').dataset.roKind,
      label: pill && pill.textContent.replace(/\s+/g, ' ').trim(),
      expanded: pill && pill.getAttribute('aria-expanded'),
      detailDisplay: getComputedStyle(r.querySelector('.ro-detail')).display,
      h: Math.round(r.getBoundingClientRect().height),
      overflowX: r.scrollWidth > r.clientWidth + 1,
      pillW: pill && Math.round(pill.getBoundingClientRect().width),
    };
  }), outSel);
  check(rows.length === MARKERS.length, `${rows.length} markers rendered`, `expected ${MARKERS.length} markers, got ${rows.length}: ${JSON.stringify(rows)}`);
  MARKERS.forEach(([, kind, text], i) => check(rows[i] && rows[i].kind === kind && rows[i].label.endsWith(text),
    `marker ${i + 1}: ${kind} — "${rows[i] && rows[i].label}"`, `marker ${i + 1}: wanted ${kind} "${text}", got ${JSON.stringify(rows[i])}`));
  check(rows.every((r) => r.expanded === 'false' && r.detailDisplay === 'none'), 'all markers collapsed, detail display:none', `a marker is open: ${JSON.stringify(rows)}`);
  check(rows.every((r) => r.h <= 14), `each marker strip is ${rows.map((r) => r.h).join('/')}px tall`, `a marker is taller than 14px: ${JSON.stringify(rows.map((r) => r.h))}`);
  check(rows.every((r) => !r.overflowX), 'no marker overflows horizontally', 'a marker overflows horizontally');
  // Back-to-back markers (rows 4-6 in the buffer) must not draw on top of each other.
  const rects = await page.evaluate((sel) => [...document.querySelectorAll(sel + ' .ro-pill')].map((p) => { const r = p.getBoundingClientRect(); return [r.top, r.bottom]; }), outSel);
  check(rects.every((r, i) => i === 0 || r[0] >= rects[i - 1][1] - 0.5), 'no two pills overlap vertically', `pills overlap: ${JSON.stringify(rects)}`);

  // 2. lookalikes keep their old rendering; the ordinary status row is the height baseline
  const other = await page.evaluate((sel) => {
    const st = [...document.querySelectorAll(sel + ' .agent-line-status')].filter((d) => !d.classList.contains('agent-line-rollover'));
    return st.map((d) => ({ text: d.textContent.trim(), h: Math.round(d.getBoundingClientRect().height), mt: parseFloat(getComputedStyle(d).marginTop), hasPill: !!d.querySelector('.ro-pill') }));
  }, outSel);
  check(other.length === 1 && other[0].text === OTHER_STATUS && !other[0].hasPill, `"${OTHER_STATUS}" stays an ordinary status row`, `other status wrong: ${JSON.stringify(other)}`);
  // Net vertical space a row adds = (neighbour-to-neighbour gap with it) - (same gap with it removed), in BOTH chat styles.
  const footprints = async (style) => page.evaluate(({ sel, style }) => {
    setChatStyle(style);
    const out = document.querySelector(sel);
    const cost = (el) => {
      const a = el.previousElementSibling, b = el.nextElementSibling;
      const gap = () => b.getBoundingClientRect().top - a.getBoundingClientRect().bottom;
      const withIt = gap(); el.style.display = 'none'; const without = gap(); el.style.display = '';
      return Math.round(withIt - without);
    };
    const marker = out.querySelector('.agent-line-rollover');
    const plain = [...out.querySelectorAll('.agent-line-status')].find((d) => !d.classList.contains('agent-line-rollover'));
    return { marker: cost(marker), plain: cost(plain) };
  }, { sel: outSel, style });
  for (const style of ['flow', 'bubbles']) {
    const f = await footprints(style);
    check(f.marker >= 0 && f.marker <= 6 && f.marker * 4 <= f.plain, `${style} chat style: marker adds ${f.marker}px to the thread, an ordinary status row adds ${f.plain}px`, `${style}: marker adds ${f.marker}px vs status row ${f.plain}px`);
  }
  await page.evaluate(() => setChatStyle('flow'));
  const quoted = await page.evaluate((sel) => [...document.querySelectorAll(sel + ' .agent-line, ' + sel + ' .agent-line-prompt')]
    .filter((d) => /so often|when the cap is hit/.test(d.textContent)).map((d) => ({ marker: d.classList.contains('agent-line-rollover'), pill: !!d.querySelector('.ro-pill') })), outSel);
  check(quoted.length === 2 && quoted.every((q) => !q.marker && !q.pill), 'a reply and a typed message that quote the phrase are NOT turned into markers', `quoted lines wrong: ${JSON.stringify(quoted)}`);

  // 3. tooltip = full original sentence
  const tips = await page.evaluate((sel) => [...document.querySelectorAll(sel + ' .ro-pill')].map((p) => [p.title, p.getAttribute('aria-label')]), outSel);
  check(MARKERS.every(([line], i) => tips[i] && tips[i][0] === line.slice(1, -1) && tips[i][1] === line.slice(1, -1)),
    'each pill title / aria-label is the full original sentence', `tooltips wrong: ${JSON.stringify(tips)}`);

  await page.screenshot({ path: resolve(SHOTS, `${label}-collapsed.png`) });

  // 4. click / Enter / Space
  const pill0 = `${outSel} .ro-pill >> nth=0`;
  await page.click(pill0);
  let st = await page.evaluate((sel) => {
    const r = document.querySelector(sel + ' .agent-line-rollover'); const d = r.querySelector('.ro-detail');
    return { exp: r.querySelector('.ro-pill').getAttribute('aria-expanded'), disp: getComputedStyle(d).display, text: d.textContent, h: Math.round(r.getBoundingClientRect().height) };
  }, outSel);
  check(st.exp === 'true' && st.disp !== 'none' && st.text === ROLLOVER.slice(1, -1), `click unfolds the full sentence (marker now ${st.h}px)`, `click did not expand: ${JSON.stringify(st)}`);
  await page.screenshot({ path: resolve(SHOTS, `${label}-expanded.png`) });
  await page.click(pill0);
  st = await page.evaluate((sel) => document.querySelector(sel + ' .ro-pill').getAttribute('aria-expanded'), outSel);
  check(st === 'false', 'second click folds it again', `still expanded: ${st}`);
  await page.focus(pill0);
  await page.keyboard.press('Enter');
  st = await page.evaluate((sel) => document.querySelector(sel + ' .ro-pill').getAttribute('aria-expanded'), outSel);
  check(st === 'true', 'Enter opens the focused pill', `Enter: aria-expanded=${st}`);
  await page.keyboard.press('Space');
  st = await page.evaluate((sel) => document.querySelector(sel + ' .ro-pill').getAttribute('aria-expanded'), outSel);
  check(st === 'false', 'Space closes it', `Space: aria-expanded=${st}`);
  await page.keyboard.press('Enter');  // leave marker 0 open for the rebuild check

  // 5. rebuild keeps the reader's open marker
  await page.evaluate((pid) => refreshModalById(pid), PID);
  await page.waitForSelector(outSel, { timeout: 5000 });
  const after = await page.evaluate((sel) => [...document.querySelectorAll(sel + ' .ro-pill')].map((p) => p.getAttribute('aria-expanded')), outSel);
  check(after.length === MARKERS.length && after[0] === 'true' && after.slice(1).every((x) => x === 'false'),
    'after refreshModal: marker 1 still open, the rest folded', `after rebuild: ${JSON.stringify(after)}`);

  // 6. live path
  await page.evaluate(({ sid, line }) => { appendAgentLine(sid, line); }, { sid, line: '[Session context is 117k tokens — starting fresh]' });
  const live = await page.evaluate((sel) => {
    const kids = [...document.querySelectorAll(sel + ' > .agent-line-rollover')]; const k = kids[kids.length - 1];
    return { n: kids.length, label: k && k.querySelector('.ro-pill') && k.querySelector('.ro-pill').textContent.trim(),
      open: k && k.querySelector('.ro-pill').getAttribute('aria-expanded'), h: k && Math.round(k.getBoundingClientRect().height) };
  }, outSel);
  check(live.n === MARKERS.length + 1 && live.label && live.label.endsWith('Fresh start · 117k') && live.open === 'false' && live.h <= 14,
    `live line arrives as a folded marker (${live.label}, ${live.h}px)`, `live marker wrong: ${JSON.stringify(live)}`);

  // 7. page-level overflow
  const pageOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  check(!pageOverflow, 'no page-level horizontal overflow', 'the page overflows horizontally');
  await page.evaluate((sel) => { const o = document.querySelector(sel); o.scrollTop = 0; }, outSel);
  await page.screenshot({ path: resolve(SHOTS, `${label}-top.png`) });

  realErrors(errors).length === 0 ? ok('no uncaught page errors') : realErrors(errors).forEach((e) => fail('page error: ' + e));
  await page.context().close();
}

let browser;
try {
  browser = await chromium.launch();
  await run(browser, 'desktop-1440', { width: 1440, height: 900 }, 'sess-ro-desktop');
  await run(browser, 'mobile-390', { width: 390, height: 844 }, 'sess-ro-mobile');
} finally {
  if (browser) await browser.close();
}
if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('\nall context-rollover-marker checks passed');
