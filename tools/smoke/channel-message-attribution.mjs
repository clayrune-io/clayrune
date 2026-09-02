#!/usr/bin/env node
/**
 * MC-937 Phase 2a regression: speaker attribution on messages in the chat pane
 * (docs/CHANNEL_MODEL_SPEC.md §4, the message-level slice — NOT the unified
 * multi-speaker stream, which is the rest of Phase 2).
 *
 * WHY THIS EXISTS
 * ----------------
 * A session's `character` renders once, as a header (fig avatar + name +
 * role), above the FIRST narration bubble of a run — then stays suppressed
 * through the rest of that run so an ordinary single-agent chat shows the
 * face once, not on every bubble. A user message re-arms it. This state
 * (`_msgAttrPending` in conversation.js) is shared between TWO independent
 * render paths that both had to agree on it:
 *   - the COLD full string-render (agentPanelHTML's outputLines builder —
 *     runs on tab switch / modal (re)open)
 *   - the WARM DOM path (appendAgentLine — runs per SSE-streamed line, and
 *     via _repaintAgentOutput's replay in resume-preview.js)
 * A regression here is silent: no server round-trip breaks, the header just
 * quietly repeats on every bubble (wall-of-faces) or never shows up, or a
 * no-character session picks up a header it never had before.
 *
 * This is a real headless boot (real index.html + real static/js/*.js served
 * verbatim, no server, no network), same hermetic shape as
 * channel-mode-roster.mjs. Both the cold render (open the project modal) and
 * the warm append path (call appendAgentLine directly, the same function the
 * live SSE handler calls) are exercised against the SAME session, so a fix
 * that only patches one path shows up as a failure here.
 *
 * RUN
 *   cd tools/smoke && node channel-message-attribution.mjs
 * Exit 0 = all cases behave; 1 = a case regressed / harness error.
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
const PID_CHAR = 'smoke_attr_char';     // session WITH a character — the header cases
const PID_PLAIN = 'smoke_attr_plain';   // session with NO character — must stay faceless

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
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID_CHAR, 'Attribution Smoke'), fixtureProject(PID_PLAIN, 'Plain Smoke')]);

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

  const FENN = { name: 'code-reviewer', scope: 'project', display_name: 'code-reviewer', agent_name: 'Fenn', avatar: 'fig:scholar' };

  // ── Seed a session with a character: 2 user turns, each followed by a
  // narration run (the 2nd run's first buffer entry contains an internal
  // newline, so it renders as TWO agent-line bubbles from ONE turn — proving
  // the collapse works within a turn, not just across turns). ──────────────
  const seededChar = await page.evaluate(({ pid, fenn }) => {
    const sid = 'sess-fenn';
    agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Attribution Smoke', task: 'review the PR', status: 'completed', startedAt: new Date().toISOString() });
    agentStatusCache[sid] = { status: 'completed', task: 'review the PR', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: 'csid-fenn', character: fenn };
    agentOutputBuffers[sid] = [
      '> Can you review the export panel fix?',
      'Reviewed the auth PR — looks fine.\nKicking off a quick check now.',
      '> Thanks, ship it when ready.',
      'Shipped.',
    ];
    openProjectModal(pid);
    return sid;
  }, { pid: PID_CHAR, fenn: FENN });

  await page.waitForSelector(`.modal-window[data-modal-id="${PID_CHAR}"] #agent-output-${seededChar}`, { timeout: 5000 });
  const scopeChar = `.modal-window[data-modal-id="${PID_CHAR}"] `;

  // ── 1. Cold render: header appears with avatar + name + role ─────────────
  const headers1 = await page.$$eval(`${scopeChar}#agent-output-${seededChar} .msg-attr`, (els) =>
    els.map((el) => ({
      name: el.querySelector('.msg-attr-name')?.textContent.trim(),
      role: el.querySelector('.msg-attr-role')?.textContent.trim(),
      hasFace: !!el.querySelector('img.av-fig, .av-emoji, .av-none'),
    })));
  headers1.length === 2
    ? ok(`cold render: 2 attribution headers (one per narration run), got ${headers1.length}`)
    : fail(`cold render: expected 2 headers (one per narration run), got ${headers1.length}: ${JSON.stringify(headers1)}`);
  (headers1[0] && headers1[0].name === 'Fenn' && headers1[0].role === 'code-reviewer' && headers1[0].hasFace)
    ? ok('header carries name "Fenn", role "code-reviewer", and a face')
    : fail(`header content wrong: ${JSON.stringify(headers1[0])}`);

  // ── 2. Consecutive narration bubbles within one run collapse to ONE header
  const bubbleOrder = await page.$$eval(`${scopeChar}#agent-output-${seededChar} > *`, (els) =>
    els.map((el) => el.className));
  // Expect: prompt, [msg-attr, narration, narration], prompt, [msg-attr, narration]
  const attrBeforeNarrationCount = bubbleOrder.filter((c, i) =>
    c === 'msg-attr' && bubbleOrder[i + 1] === 'agent-line').length;
  attrBeforeNarrationCount === 2
    ? ok('each header sits directly before the FIRST narration bubble of its run, not repeated on the 2nd')
    : fail(`expected 2 header→first-bubble pairs, layout was: ${JSON.stringify(bubbleOrder)}`);
  const narrationRunLengths = (() => {
    // Count how many bare "agent-line" bubbles follow the 2nd .msg-attr with no
    // header in between — should be 1 ("Shipped."); the FIRST run should be 2
    // ("Reviewed the auth PR..." + "Kicking off a quick check now.").
    const idx2nd = bubbleOrder.lastIndexOf('msg-attr');
    let n = 0;
    for (let i = idx2nd + 1; i < bubbleOrder.length && bubbleOrder[i] === 'agent-line'; i++) n++;
    return n;
  })();
  narrationRunLengths === 1
    ? ok('run boundary is correct: the 2nd run ("Shipped.") has exactly 1 bubble under its 1 header')
    : fail(`expected exactly 1 bubble in the 2nd run, counted ${narrationRunLengths}`);

  // ── 3. User bubbles are untouched (still agent-line-prompt, no header ever
  // attaches to them) ───────────────────────────────────────────────────────
  const promptCount = bubbleOrder.filter((c) => c.includes('agent-line-prompt')).length;
  promptCount === 2 ? ok('both user prompts still render as plain agent-line-prompt bubbles')
                     : fail(`expected 2 user-prompt bubbles, counted ${promptCount}`);
  const headerBeforePrompt = bubbleOrder.some((c, i) => c === 'msg-attr' && (bubbleOrder[i + 1] || '').includes('agent-line-prompt'));
  headerBeforePrompt ? fail('a header attached to a USER bubble — user bubbles must never carry attribution')
                      : ok('no header ever attaches to a user bubble');

  // ── 4. Warm path (live SSE-equivalent): appendAgentLine continues the SAME
  // run — no new header for a line right after "Shipped." ──────────────────
  await page.evaluate(({ sid }) => { appendAgentLine(sid, 'One more thing.'); }, { sid: seededChar });
  const headersAfterAppend = await page.$$eval(`${scopeChar}#agent-output-${seededChar} .msg-attr`, (els) => els.length);
  headersAfterAppend === 2
    ? ok('warm path: a line appended mid-run does NOT add a 3rd header')
    : fail(`warm path: expected still 2 headers after an in-run append, got ${headersAfterAppend}`);

  // ── 5. Warm path: a message AFTER the user speaks DOES re-show the header
  await page.evaluate(({ sid }) => {
    appendAgentLine(sid, '> One more question.');
    appendAgentLine(sid, 'Sure, here you go.');
  }, { sid: seededChar });
  const headersAfterReply = await page.$$eval(`${scopeChar}#agent-output-${seededChar} .msg-attr`, (els) => els.length);
  headersAfterReply === 3
    ? ok('warm path: a user message re-arms the header — 3rd header shown for the next agent reply')
    : fail(`warm path: expected 3 headers after a user interjection + reply, got ${headersAfterReply}`);

  // ── 6. A session with NO character renders exactly as before: zero headers
  const seededPlain = await page.evaluate(({ pid }) => {
    const sid = 'sess-plain';
    agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Plain Smoke', task: 'fix the bug', status: 'completed', startedAt: new Date().toISOString() });
    agentStatusCache[sid] = { status: 'completed', task: 'fix the bug', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: 'csid-plain', character: null };
    agentOutputBuffers[sid] = [
      '> Can you fix the bug?',
      'Fixed it.\nPushed the change.',
      '> Great, thanks.',
      'Anytime.',
    ];
    openProjectModal(pid);
    return sid;
  }, { pid: PID_PLAIN });
  await page.waitForSelector(`.modal-window[data-modal-id="${PID_PLAIN}"] #agent-output-${seededPlain}`, { timeout: 5000 });
  const scopePlain = `.modal-window[data-modal-id="${PID_PLAIN}"] `;
  const plainHeaders = await page.$$eval(`${scopePlain}#agent-output-${seededPlain} .msg-attr`, (els) => els.length);
  const plainBubbles = await page.$$eval(`${scopePlain}#agent-output-${seededPlain} > *`, (els) => els.map((el) => el.className));
  plainHeaders === 0
    ? ok('no-character session: zero attribution headers')
    : fail(`no-character session should render 0 headers, got ${plainHeaders}`);
  (plainBubbles.filter((c) => c === 'agent-line').length === 3 && plainBubbles.filter((c) => c.includes('agent-line-prompt')).length === 2)
    ? ok('no-character session: bubble structure unchanged (3 narration + 2 prompt, no extra nodes)')
    : fail(`no-character session bubble structure changed: ${JSON.stringify(plainBubbles)}`);
  // Warm path for the no-character session too — appendAgentLine must not
  // start inventing a header where there was never a character to begin with.
  await page.evaluate(({ sid }) => { appendAgentLine(sid, '> one more.'); appendAgentLine(sid, 'ok.'); }, { sid: seededPlain });
  const plainHeadersAfter = await page.$$eval(`${scopePlain}#agent-output-${seededPlain} .msg-attr`, (els) => els.length);
  plainHeadersAfter === 0
    ? ok('no-character session: warm path also stays faceless')
    : fail(`no-character session picked up a header via the warm path: ${plainHeadersAfter}`);

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) {
    uncaught.forEach((e) => fail('uncaught exception during interaction: ' + e));
  }

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0
    ? '\n✅ PASS — speaker attribution collapses per run, re-arms after the user speaks, and no-character sessions stay faceless (cold + warm render paths).'
    : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
