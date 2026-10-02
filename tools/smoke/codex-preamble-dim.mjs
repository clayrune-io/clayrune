#!/usr/bin/env node
/**
 * Codex preamble dimming (backlog 30e6a946, part 2).
 *
 * `codex exec --json` agent messages carry no commentary/final_answer phase
 * (only the rollout file does), so in a Codex chat the narration sent before
 * tool calls and the final answer rendered as two equal replies and read as
 * the same answer twice. Rule (Dave's call, option B): in a CODEX session a
 * narration block that is directly followed by a tool line is interim and is
 * rendered subordinate (dimmed + smaller, still fully visible). The final
 * answer (narration not followed by a tool line before the next prompt / end)
 * renders normally. Claude and every other engine are unchanged.
 *
 * Both render paths are exercised and must agree:
 *   - cold render (agentPanelHTML: planBlock wrapped in .agent-interim-wrap)
 *   - live stream (appendAgentLine: .agent-interim added to the narration
 *     once the tool line arrives, rich-text.js markInterimNarration)
 *
 * Offline: serves static/ from THIS checkout, every API call but the three
 * stubs is aborted, nothing touches real sessions.
 *
 * RUN: node codex-preamble-dim.mjs
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
const SHOT_DIR = resolve(REPO_ROOT, '_scratch', 'codex-preamble-dim');
mkdirSync(SHOT_DIR, { recursive: true });
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_preamble_dim';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

const project = {
  id: PID, name: 'Preamble Dim Smoke', status: 'active', domain: 'general', emoji: '\u{1f4ac}',
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
const PROJECTS_JSON = JSON.stringify([project]);

// Shaped like apex_trader b03b8d64dd31's tail: preamble, tools, preamble,
// tools, FINAL, a second prompt, then a reply with no tool at all.
const PROMPT = '> Ron: do these tests';
const TURN = [
  PROMPT,
  'PRE-ONE: I will test setup type, same-time-of-day volume, and previous-day context separately.',
  '[tool: shell] powershell -Command "Get-Content one"',
  '[tool: shell] powershell -Command "Get-Content two"',
  'PRE-TWO: the split check is resolved; I will use only events after the listing date.',
  '[tool: file_change]',
  'FINAL: possibly, but 56.1% was the selected early-2026 result.',
  '\n> Ron: thanks\n',
  'PLAIN: no tools ran for this one, so it is an answer and stays normal.',
];
const INTERIM = ['PRE-ONE', 'PRE-TWO'];
const NORMAL = ['FINAL', 'PLAIN'];

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, m) => (cond ? ok(m) : fail(m));

// Computed style of every bubble that carries one of the marker words.
const measure = (page, sel) => page.evaluate(({ sel, words }) => {
  const out = {};
  const root = document.querySelector(sel);
  for (const w of words) {
    const el = [...root.querySelectorAll('.agent-line')].find((e) => e.textContent.includes(w));
    if (!el) { out[w] = null; continue; }
    const cs = getComputedStyle(el);
    out[w] = { opacity: parseFloat(cs.opacity), font: parseFloat(cs.fontSize), text: el.textContent.slice(0, 40) };
  }
  const tool = [...root.querySelectorAll('.agent-line-tool')].map((e) => parseFloat(getComputedStyle(e).opacity));
  const prompt = [...root.querySelectorAll('.agent-line-prompt')].map((e) => parseFloat(getComputedStyle(e).opacity));
  return { out, tool, prompt, wrap: root.querySelectorAll('.agent-interim-wrap').length, marked: root.querySelectorAll('.agent-interim').length };
}, { sel, words: [...INTERIM, ...NORMAL] });

function verify(label, provider, m) {
  if (provider === 'codex') {
    for (const w of INTERIM) {
      const x = m.out[w];
      check(x && x.opacity < 1 && x.font < m.out.FINAL.font, `${label}: ${w} is dimmed + smaller (opacity ${x && x.opacity}, ${x && x.font}px vs final ${m.out.FINAL.font}px)`);
    }
    for (const w of NORMAL) {
      const x = m.out[w];
      check(x && x.opacity === 1, `${label}: ${w} renders normally (opacity ${x && x.opacity})`);
    }
    check(m.tool.every((o) => o === 1) && m.prompt.every((o) => o === 1), `${label}: tool lines and prompts untouched`);
  } else {
    check(INTERIM.concat(NORMAL).every((w) => m.out[w] && m.out[w].opacity === 1), `${label}: every narration block renders normally (${provider} unchanged)`);
    check(m.wrap === 0 && m.marked === 0, `${label}: no interim markup at all (wrap=${m.wrap}, marked=${m.marked})`);
  }
}

const browser = await chromium.launch();
let exitCode = 1;
try {
  for (const vp of [{ name: 'desktop', width: 1400, height: 900 }, { name: 'mobile', width: 390, height: 844 }]) {
    const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height } });
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
    await page.waitForSelector('#projects-col .card, .project-card, #projects-col', { timeout: 15000 });
    await page.waitForFunction(() => typeof window.appendAgentLine === 'function' && typeof window.openProjectModal === 'function', null, { timeout: 15000 });
    console.log(`\n== ${vp.name} (${vp.width}x${vp.height}) ==`);

    let n = 0;
    for (const provider of ['codex', 'claude']) {
      for (const mode of ['cold', 'live']) {
        const sid = `s-${vp.name}-${provider}-${mode}-${n++}`;
        const label = `${provider}/${mode}`;
        await page.evaluate(({ pid, sid, provider, mode, turn, vp }) => {
          const startedAt = new Date().toISOString();
          const rec = { projectId: pid, sessionId: sid, projectName: 'Preamble Dim Smoke', task: 'q', status: 'completed', startedAt, provider, claudeSessionId: 'csid-' + sid };
          agentHistory.unshift(rec);
          agentStatusCache[sid] = { status: 'completed', task: 'q', projectId: pid, startedAt, provider, claudeSessionId: 'csid-' + sid };
          agentOutputBuffers[sid] = mode === 'cold' ? turn.slice() : [];
          agentOutputTimestamps[sid] = [];
          if (vp.name === 'mobile') { activeAgentTab[pid] = sid; }
          if (typeof closeAllModals === 'function') closeAllModals();
          openProjectModal(pid);
          activeAgentTab[pid] = sid;
          refreshModal();
        }, { pid: PID, sid, provider, mode, turn: TURN, vp });
        const sel = `.modal-window[data-modal-id="${PID}"] #agent-output-${sid}`;
        await page.waitForSelector(sel, { state: 'attached', timeout: 5000 });
        if (mode === 'live') {
          // Streamed one line at a time, checking the in-flight decision too:
          // narration is NOT dimmed until a tool line follows it.
          await page.evaluate(({ sid, l }) => appendAgentLine(sid, l), { sid, l: TURN[0] });
          await page.evaluate(({ sid, l }) => appendAgentLine(sid, l), { sid, l: TURN[1] });
          const before = await measure(page, sel);
          check(before.out['PRE-ONE'] && before.out['PRE-ONE'].opacity === 1, `${label}: narration is normal while it is still the latest block`);
          for (const l of TURN.slice(2)) await page.evaluate(({ sid, l }) => appendAgentLine(sid, l), { sid, l });
        }
        const m = await measure(page, sel);
        verify(label, provider, m);
        if (mode === 'live' && provider === 'codex') {
          // Live must also have dimmed exactly the blocks the cold path wraps.
          check(m.marked === 2, `${label}: exactly the 2 interim blocks carry .agent-interim (got ${m.marked})`);
        }
        if (mode === 'cold' && provider === 'codex') {
          check(m.wrap === 2, `${label}: each interim run is wrapped once (wrap=${m.wrap})`);
        }
        await page.evaluate((s) => { const e = document.querySelector(s); if (e) e.scrollTop = 0; }, sel);
        await page.screenshot({ path: resolve(SHOT_DIR, `${vp.name}-${provider}-${mode}.png`) });
      }
    }
    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    uncaught.forEach((e) => fail('uncaught exception: ' + e));
    await ctx.close();
  }
  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0 ? '\n✅ PASS — codex preamble dims on cold render and live, claude unchanged, desktop + 390px.' : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
} finally {
  await browser.close().catch(() => {});
  process.exit(exitCode);
}
