#!/usr/bin/env node
/**
 * Stop-hook draft collapse (2026-09-15; reworked 2026-10-01).
 *
 * WHY THIS EXISTS
 * ----------------
 * A global Stop hook (reply-length-guard.py / permission-ask-guard.py /
 * turn-guard.py) blocks a reply and feeds its reason back so the model keeps
 * going in the SAME turn. The backend marks that boundary
 * ('[stop-hook-redo:length]' / '[stop-hook-redo:other]',
 * mc/agent_runtime.stop_hook_marker) and the chat renderer may fold the
 * earlier draft into a closed "Show earlier draft" <details> -- but ONLY when
 * the follow-up actually replaces it:
 *
 *   - 'other' (permission-ask / turn guard: "continue the work") NEVER folds;
 *   - 'length' (re-send shorter) folds only if the follow-up carries the
 *     draft's content (rich-text.js stopHookDraftReplaced: >= 20 words and
 *     >= 40% content-word overlap).
 *
 * 2026-10-01 incident: Dave's full 4-bullet answer was folded away because a
 * length hook fired and his follow-up was a one-line "nothing to resend", so
 * the visible reply said nothing. Cases (b)/(c) below pin that.
 *
 * Two render paths share the one rule and both are driven here against THIS
 * checkout's static/js (served from ROOT, not the live server's copy):
 *   LIVE    appendAgentLine() on a page shell of a running MC
 *   HISTORY the cold render in conversation.js (agentOutputBuffers -> modal)
 *           on a fully mocked app, no server needed
 *
 * RUN: node stop-hook-draft-collapse.mjs   (live half needs MC on localhost:5199, or MC_SMOKE_BASE)
 */
import { chromium } from 'playwright';
import { existsSync, readFileSync, readdirSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, m) => (cond ? ok(m) : fail(m));

// ── Fixture text ────────────────────────────────────────────────────────────
// Ron's real case: a full answer, then a one-line meta reply after the hook.
const ANSWER = [
  'Yes, through Settings. A pop-up usually appears there first.',
  '- Pop-up: within an hour of a new release, a message appears saying an update is available. Its Update button opens Settings. The other choices are Later (asks again in 24 hours) or Dismiss (skips this version).',
  '- Manually: Settings, Server, Update Clayrune.',
  '- Confirmation: they confirm before it installs, then Clayrune restarts. On the Mac that confirmation is the passcode step you will see when you update.',
  '- Mac exception: this only works from v2.4.5 on. On v2.4.4 or older, the Update button cannot install the new build, so they download v2.4.5 by hand once.',
];
const ANSWER_HEAD = ANSWER[0];
// A genuine compressed re-send of the same content (what the length hook asks for).
const RESEND = [
  'Yes, through Settings: a pop-up appears within an hour of a new release and its Update button opens Settings,',
  'or go manually via Settings, Server, Update Clayrune. They confirm before it installs, then Clayrune restarts.',
  'On the Mac the Update button only installs from v2.4.5 on.',
];
const RESEND_HEAD = RESEND[0];
// Ron's meta one-liner (17 words) and a longer one (>= 20 words) so the
// overlap rule is exercised on its own, not just the word floor.
const META_SHORT = "Nothing was left undone: the answer to your Settings question was complete, so there's nothing to resend.";
const META_LONG = 'Nothing was left undone here, because the earlier answer was complete and nothing is waiting on you or needs to be sent again, so I am simply confirming that nothing further remains outstanding at this point.';
const LENGTH_FEEDBACK = 'BREVITY RULE VIOLATED: that reply was 211 prose words against a 160-word hard ceiling.';
const PERMISSION_FEEDBACK = 'You ended your turn ASKING PERMISSION to do something reversible.';

// ── LIVE path ───────────────────────────────────────────────────────────────
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 900, height: 500 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message || String(e)));
  await page.route('**/*', (route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (req.method() !== 'GET') return route.abort();
    if (url.hostname === 'localhost' && /^\/static\/(js|css)\//.test(url.pathname)) {
      const f = join(ROOT, url.pathname);
      if (existsSync(f)) {
        return route.fulfill({ status: 200,
          contentType: url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript; charset=utf-8',
          body: readFileSync(f, 'utf8') });
      }
    }
    return route.continue();
  });
  // MC_SMOKE_BASE points this at a second instance (own port + MC_DATA_DIR)
  // instead of the operator's live server.
  await page.goto(process.env.MC_SMOKE_BASE || 'http://localhost:5199/', { waitUntil: 'domcontentloaded' });
  const bridged = await page.waitForFunction(
    () => typeof window.appendAgentLine === 'function'
      && typeof window.armDraftCollapse === 'function'
      && typeof window.stopHookDraftReplaced === 'function',
    null, { timeout: 20000 }).then(() => true).catch(() => false);
  check(bridged, 'checkout JS loaded (appendAgentLine, armDraftCollapse, stopHookDraftReplaced bridged)');
  if (!bridged) throw new Error('no bridge');

  await page.evaluate(() => {
    const wrap = document.createElement('div');
    wrap.className = 'agent-chat';
    wrap.style.cssText = 'width:800px;background:#1a1a1a;padding:12px;';
    document.body.appendChild(wrap);
  });

  // Feeds `lines` through appendAgentLine into a fresh container and reports
  // what the user would see. `lines` entries: a string, or {after: n} is not
  // needed -- the sequence order IS the stream order.
  const runLive = (sid, lines, draftHead) => page.evaluate(({ sid, lines, draftHead }) => {
    const out = document.createElement('div');
    out.className = 'agent-output';
    out.id = `agent-output-${sid}`;
    document.querySelector('.agent-chat').appendChild(out);
    for (const l of lines) appendAgentLine(sid, l);
    const details = out.querySelectorAll('details.draft-block');
    const visible = Array.from(out.children).filter((c) => c.tagName !== 'DETAILS')
      .map((c) => c.textContent || '').join('\n');
    return {
      detailCount: details.length,
      detailsOpen: details.length ? details[0].open : null,
      detailsText: details.length ? details[0].textContent || '' : '',
      visible,
      draftVisible: visible.includes(draftHead),
      draftInDetails: details.length > 0 && (details[0].textContent || '').includes(draftHead),
      markerShown: /\[stop-hook-redo/.test(out.textContent || ''),
      prompts: Array.from(out.querySelectorAll('.agent-line-prompt')).map((e) => e.textContent || ''),
    };
  }, { sid, lines, draftHead });

  // (a) length hook, follow-up RESTATES the content -> collapsed (existing behaviour kept).
  const a = await runLive('shA', ['> Ron: How does the Update pop-up work?', ...ANSWER,
    '[stop-hook-redo:length]', ...RESEND], ANSWER_HEAD);
  check(a.detailCount === 1, '(a) length + restating re-send: the draft collapsed into one <details class="draft-block">');
  check(a.detailsOpen === false, '(a) the toggle is CLOSED by default');
  check(a.draftInDetails && !a.draftVisible, '(a) draft text preserved inside the toggle, not also visible');
  check(a.visible.includes(RESEND_HEAD), '(a) the compressed re-send is the visible reply');
  check(!a.markerShown, '(a) the raw marker never reaches the screen');
  check(a.detailsText.includes('Mac exception'), '(a) the whole draft (all four bullets) is inside the toggle');

  // Streaming order: right after the marker nothing has replaced the draft yet.
  const pending = await page.evaluate(({ sid, answer, head }) => {
    const out = document.createElement('div');
    out.className = 'agent-output';
    out.id = `agent-output-${sid}`;
    document.querySelector('.agent-chat').appendChild(out);
    for (const l of answer) appendAgentLine(sid, l);
    appendAgentLine(sid, '[stop-hook-redo:length]');
    return { details: out.querySelectorAll('details.draft-block').length,
      draftVisible: (out.textContent || '').includes(head) };
  }, { sid: 'shStream', answer: ANSWER, head: ANSWER_HEAD });
  check(pending.details === 0 && pending.draftVisible,
    'live: at the marker the draft stays visible; it folds only once a replacing follow-up arrives');

  // (b) THE BUG: length hook, follow-up is a meta one-liner -> NOT collapsed.
  for (const [label, meta] of [['17-word one-liner', META_SHORT], ['>=20-word meta reply', META_LONG]]) {
    const b = await runLive('shB' + label.length, ['> Ron: How does the Update pop-up work?', ...ANSWER,
      '[stop-hook-redo:length]', meta], ANSWER_HEAD);
    check(b.detailCount === 0, `(b) length + ${label}: nothing collapsed`);
    check(b.draftVisible && b.visible.includes('Mac exception'), `(b) length + ${label}: the full answer stays visible`);
    check(b.visible.includes(meta.slice(0, 30)), `(b) length + ${label}: the follow-up appends below the answer`);
    check(b.visible.indexOf(ANSWER_HEAD) < b.visible.indexOf(meta.slice(0, 30)), `(b) length + ${label}: answer first, follow-up after`);
  }

  // (c) permission-ask / turn-guard hook -> NEVER collapsed, even when the follow-up repeats the content.
  const c = await runLive('shC', ['> Ron: Fix it.', ...ANSWER, '[stop-hook-redo:other]', ...RESEND], ANSWER_HEAD);
  check(c.detailCount === 0, '(c) other-kind hook + a follow-up that restates the draft: nothing collapsed');
  check(c.draftVisible && c.visible.includes(RESEND_HEAD), '(c) other-kind hook: draft AND follow-up both visible');
  const c2 = await runLive('shC2', ['> Ron: Fix it.', ...ANSWER, '[stop-hook-redo:other]',
    'I made the change in static/js/rich-text.js and ran the smoke; all checks passed on the first run.'], ANSWER_HEAD);
  check(c2.detailCount === 0 && c2.draftVisible, '(c) other-kind hook + continued work: draft stays visible');

  // Untagged marker from stored history: a candidate, but still must pass the overlap rule.
  const u1 = await runLive('shU1', ['> Ron: q', ...ANSWER, '[stop-hook-redo]', ...RESEND], ANSWER_HEAD);
  check(u1.detailCount === 1 && !u1.draftVisible, 'untagged marker + restating re-send: collapsed');
  const u2 = await runLive('shU2', ['> Ron: q', ...ANSWER, '[stop-hook-redo]', META_LONG], ANSWER_HEAD);
  check(u2.detailCount === 0 && u2.draftVisible, 'untagged marker + meta reply: NOT collapsed');

  // A follow-up that is a tool call is new work, not a re-send.
  const t = await runLive('shT', ['> Ron: q', ...ANSWER, '[stop-hook-redo:length]', '[tool: Bash]', ...RESEND], ANSWER_HEAD);
  check(t.detailCount === 0 && t.draftVisible, 'length hook followed by a tool call: draft stays visible');

  // Legacy buffers: the hook feedback sits in memory as a fake Ron prompt.
  const l1 = await runLive('shL1', ['\n> Ron: What is the status?\n', ...ANSWER,
    '\n> Ron: Stop hook feedback:\n' + LENGTH_FEEDBACK + '\n', ...RESEND], ANSWER_HEAD);
  check(l1.prompts.length === 1 && !l1.prompts.some((p) => /Stop hook feedback/.test(p)),
    'legacy buffer: "> Ron: Stop hook feedback" never renders as a Ron bubble');
  check(l1.detailCount === 1 && !l1.draftVisible && l1.visible.includes(RESEND_HEAD),
    'legacy length feedback + restating re-send: collapses, re-send visible');
  const l2 = await runLive('shL2', ['\n> Ron: What is the status?\n', ...ANSWER,
    '\n> Ron: Stop hook feedback:\n' + PERMISSION_FEEDBACK + '\n', ...RESEND], ANSWER_HEAD);
  check(l2.detailCount === 0 && l2.draftVisible && !/Stop hook feedback/.test(l2.visible),
    'legacy permission feedback: draft stays visible, no hook text shown');
  const l3 = await runLive('shL3', ['\n> Ron: What is the status?\n', ...ANSWER,
    '\n> Ron: Stop hook feedback:\n' + LENGTH_FEEDBACK + '\n', META_SHORT], ANSWER_HEAD);
  check(l3.detailCount === 0 && l3.draftVisible, 'legacy length feedback + meta one-liner: draft stays visible');

  // The substitution advisory stays in log_lines (agent-facing) but must not
  // be visible in the chat.
  const coverage = await page.evaluate(() => {
    const out = document.getElementById('agent-output-shA');
    appendAgentLine('shA', '[coverage] you specified /tmp/x.pdf — no tool call this turn used it');
    const el = Array.from(out.children).pop();
    const shown = el && getComputedStyle(el).display !== 'none';
    const cls = el ? el.className : '';
    el && el.remove();
    return { shown, cls };
  });
  check(/agent-line-coverage/.test(coverage.cls) && !coverage.shown,
    `[coverage] advisory is in the DOM but hidden (display:none) — class "${coverage.cls}"`);

  const shotDir = join(ROOT, 'data', 'uploads');
  mkdirSync(shotDir, { recursive: true });
  const closedShotPath = join(shotDir, 'smoke-stop-hook-draft-collapse-closed.png');
  await page.locator('#agent-output-shA').screenshot({ path: closedShotPath });
  console.log('  screenshot (case a, closed): ' + closedShotPath);
  const keptShotPath = join(shotDir, 'smoke-stop-hook-draft-collapse-kept.png');
  await page.locator('#agent-output-shB' + '17-word one-liner'.length).screenshot({ path: keptShotPath });
  console.log('  screenshot (case b, answer kept): ' + keptShotPath);

  // Open the toggle and confirm the draft becomes readable — never destroyed.
  await page.click('#agent-output-shA details.draft-block summary');
  const afterOpen = await page.evaluate(() => {
    const details = document.querySelector('#agent-output-shA details.draft-block');
    return { open: details.open, visibleText: details.textContent || '' };
  });
  check(afterOpen.open === true, 'clicking "Show earlier draft" opens the toggle');
  check(afterOpen.visibleText.includes(ANSWER_HEAD), 'opened toggle shows the full draft text');
  check(errors.length === 0, `no page errors (${errors.length}): ${errors.slice(0, 3).join(' | ')}`);

  // ── HISTORY path (conversation.js cold render) on a mocked app ────────────
  const JS_DIR = join(ROOT, 'static', 'js');
  const CSS_DIR = join(ROOT, 'static', 'css');
  const INDEX_HTML = readFileSync(join(ROOT, 'static', 'index.html'), 'utf8');
  const ORIGIN = 'http://mc.smoke.test';
  const PID = 'smoke_stop_hook';
  const STATIC = {};
  for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(join(JS_DIR, f), 'utf8')];
  for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(join(CSS_DIR, f), 'utf8')];
  const project = {
    id: PID, name: 'Stop Hook Smoke', status: 'active', domain: 'general', emoji: '\u{1f4c5}',
    description: '', summary: '', current_task: 'Idle', next_action: '',
    blocked: false, blocked_reason: null, activity_log: [], backlog: [],
    project_path: '/smoke/' + PID, last_updated: '2026-09-02T00:00:00Z',
    last_updated_relative: 'today', last_completed: null, live_agent: null,
    display_order: 0, provider: 'claude', use_streaming_agent: true,
    distiller_mode: 'proposed', distiller_min_recurrence: 3,
    distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
    distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
    distiller_skip_errors: true,
  };
  const histErrors = [];
  const newHistPage = async () => {
    // Own browser context per case: the app persists the active session per
    // project, so a reused context keeps showing the previous case's chat.
    const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
    const pg = await ctx.newPage();
    pg.on('pageerror', (e) => histErrors.push(e.message || String(e)));
    await pg.route('**/*', (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
      if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
      if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([project]) });
      if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      return route.abort();
    });
    await pg.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await pg.waitForSelector('#projects-col .card', { timeout: 15000 });
    return { ctx, pg };
  };

  // Each case renders in its own modal open; the buffer is exactly what the
  // server's _transcript_buffer_lines emits for that history.
  const runHistory = async (id, lines) => {
    const { ctx, pg: hist } = await newHistPage();
    await hist.evaluate(({ pid, id, lines }) => {
      agentHistory.unshift({ projectId: pid, sessionId: id, projectName: 'Stop Hook Smoke', task: 'hook', status: 'completed', startedAt: new Date().toISOString() });
      agentStatusCache[id] = { status: 'completed', task: 'hook', projectId: pid, startedAt: new Date().toISOString(), claudeSessionId: 'csid-' + id };
      agentOutputBuffers[id] = lines;
      openProjectModal(pid);
    }, { pid: PID, id, lines });
    const sel = `.modal-window[data-modal-id="${PID}"] #agent-output-${id}`;
    await hist.waitForSelector(sel, { timeout: 5000 });
    const res = await hist.evaluate((sel) => {
      const out = document.querySelector(sel);
      const details = out.querySelectorAll('details.draft-block');
      const visible = Array.from(out.children).filter((c) => c.tagName !== 'DETAILS')
        .map((c) => c.textContent || '').join('\n');
      return { detailCount: details.length, detailsOpen: details.length ? details[0].open : null,
        detailsText: details.length ? details[0].textContent || '' : '', visible,
        markerShown: /\[stop-hook-redo/.test(out.textContent || '') };
    }, sel);
    await ctx.close();
    return res;
  };
  const ASK = '\n> Ron: How does the Update pop-up work?\n';
  const joined = ANSWER;   // each bullet is its own buffer line
  const ha = await runHistory('hA', [ASK, ...joined, '[stop-hook-redo:length]', ...RESEND]);
  check(ha.detailCount === 1 && ha.detailsOpen === false && ha.detailsText.includes('Mac exception'),
    'history (a): length + restating re-send collapses the whole draft into a closed toggle');
  check(ha.visible.includes(RESEND_HEAD) && !ha.visible.includes(ANSWER_HEAD), 'history (a): only the re-send is visible');
  check(!ha.markerShown, 'history (a): no raw marker on screen');
  for (const [label, meta] of [['17-word one-liner', META_SHORT], ['>=20-word meta reply', META_LONG]]) {
    const hb = await runHistory('hB' + label.length, [ASK, ...joined, '[stop-hook-redo:length]', meta]);
    check(hb.detailCount === 0 && hb.visible.includes('Mac exception') && hb.visible.includes(meta.slice(0, 30)),
      `history (b): length + ${label}: full answer AND the follow-up visible, nothing collapsed`);
  }
  const hc = await runHistory('hC', [ASK, ...joined, '[stop-hook-redo:other]', ...RESEND]);
  check(hc.detailCount === 0 && hc.visible.includes(ANSWER_HEAD) && hc.visible.includes(RESEND_HEAD),
    'history (c): other-kind hook never collapses, draft and follow-up both visible');
  const hu1 = await runHistory('hU1', [ASK, ...joined, '[stop-hook-redo]', ...RESEND]);
  check(hu1.detailCount === 1 && !hu1.visible.includes(ANSWER_HEAD), 'history: untagged marker + restating re-send collapses');
  const hu2 = await runHistory('hU2', [ASK, ...joined, '[stop-hook-redo]', META_LONG]);
  check(hu2.detailCount === 0 && hu2.visible.includes(ANSWER_HEAD), 'history: untagged marker + meta reply does not collapse');
  const hd = await runHistory('hD', [ASK, ...joined, '[stop-hook-redo:length]', '[tool: Bash]', ...RESEND]);
  check(hd.detailCount === 0 && hd.visible.includes(ANSWER_HEAD), 'history: length hook then a tool call: draft stays visible');
  // Double block on one draft (real fixture shape): length, then permission.
  const he = await runHistory('hE', [ASK, ...joined, '[stop-hook-redo:length]', '[stop-hook-redo:other]', ...RESEND]);
  check(he.detailCount === 0 && he.visible.includes(ANSWER_HEAD), 'history: length then other on the same draft: nothing collapsed');
  // The mocked app aborts every non-local request, so the lazy mermaid CDN
  // import fails by design -- that is not a render error.
  const realHistErrors = histErrors.filter((e) => !/dynamically imported module/.test(e));
  check(realHistErrors.length === 0, `history: no page errors (${realHistErrors.length}): ${realHistErrors.slice(0, 3).join(' | ')}`);
} finally {
  await browser.close();
}

console.log('');
if (bad > 0) {
  console.error(`FAIL — ${bad} check(s) failed`);
  process.exit(1);
} else {
  console.log('PASS — a draft collapses only when a length-hook follow-up replaces it; every other answer stays visible.');
}
