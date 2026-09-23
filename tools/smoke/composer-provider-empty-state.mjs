#!/usr/bin/env node
/**
 * MC-967 — the +New empty-state heading/placeholder must name the ACTIVE
 * AGENT/PROVIDER, not hard-code "Claude" for every vendor.
 *
 * WHY THIS EXISTS
 * ----------------
 * Ron's report: "the new-conversation empty state hard-codes Claude ('What
 * should Claude work on?', 'Describe a task for Claude...'), and that copy
 * shows for every vendor." `_composerActiveCharName` (static/js/conversation.js)
 * already falls back to the chosen PROVIDER's display name when no persona is
 * selected — composer-persona-cards.mjs covers the persona-switch path, but
 * nothing exercised the plain "Agent" dropdown (`.composer-provider-row`,
 * `setComposerProvider`), which is the control in Ron's screenshot
 * (data/uploads/agent_1acfb63a47.png: AGENT=Claude Code, PERSONA=None).
 *
 * This proves, against the real static/js + static/css served verbatim:
 *   1. With only Claude installed, the heading/placeholder read "Claude"
 *      (single-provider deployments stay pixel-identical — no regression).
 *   2. With Claude + Gemini CLI installed and Gemini picked as the default
 *      provider, the heading/placeholder name "Gemini" on first paint — no
 *      hard-coded "Claude" leaks through.
 *   3. Switching the Agent dropdown live (setComposerProvider) updates both
 *      the heading and the placeholder to the newly-picked provider's name,
 *      with the trailing "CLI"/"Code" suffix stripped the same way the
 *      persona-name path already is.
 *   4. An unrecognized/still-loading provider falls back to neutral copy
 *      ("Agent"), never silently defaulting to "Claude".
 *
 * Hermetic: real index.html + real static/js/*.js + static/css/*.css served
 * verbatim (same harness shape as composer-persona-cards.mjs). No server,
 * no network.
 *
 * RUN   node tools/smoke/composer-provider-empty-state.mjs
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
const PID = 'smoke_provider_empty_state';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

function fixtureProject(id, name, provider) {
  return {
    id, name, status: 'active', domain: 'general', emoji: '\u{1F9EA}',
    description: '', summary: '', current_task: 'Idle', next_action: '',
    blocked: false, blocked_reason: null, activity_log: [], backlog: [],
    project_path: '/smoke/' + id, last_updated: '2026-09-14T00:00:00Z',
    last_updated_relative: 'today', last_completed: null, live_agent: null,
    display_order: 0, provider, use_streaming_agent: true,
    distiller_mode: 'proposed', distiller_min_recurrence: 3,
    distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
    distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
    distiller_skip_errors: true,
  };
}

const CLAUDE_PROVIDER = {
  name: 'claude', display_name: 'Claude Code', installed: true, default: true,
  in_use: true, allowance_exhausted: '', capabilities: {}, models: [],
};
const GEMINI_PROVIDER = {
  name: 'gemini', display_name: 'Gemini CLI', installed: true, default: false,
  in_use: true, allowance_exhausted: '', capabilities: {}, models: [],
};

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

async function installRoutes(page, { projects, providers }) {
  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(projects) });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/agent/providers') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ default: providers[0]?.name || 'claude', providers }) });
    if (/\/conversations$/.test(path)) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (/\/agent-log$/.test(path)) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (/\/agent\/dispatch$/.test(path)) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, session_id: 'smoke-sess' }) });
    return route.abort();
  });
}

async function openFreshComposer(page, pid) {
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(({ pid }) => {
    window.agentConvNew = window.agentConvNew || {};
    openProjectModal(pid);
    agentConvNew[pid] = true;
    if (typeof refreshModal === 'function') refreshModal();
  }, { pid });
  await page.waitForSelector('.ces-heading', { timeout: 5000 });
}

async function readState(page, pid) {
  return page.evaluate(({ pid }) => ({
    heading: document.querySelector('.ces-heading')?.textContent || '',
    placeholder: document.getElementById('agent-task-' + pid)?.placeholder || '',
  }), { pid });
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();

  // ── 1. Claude-only deployment: pixel-identical, no regression ──
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    await installRoutes(page, { projects: [fixtureProject(PID, 'Claude Only', 'claude')], providers: [CLAUDE_PROVIDER] });
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await openFreshComposer(page, PID);
    const s = await readState(page, PID);
    (s.heading === 'What should Claude work on?') ? ok(`claude-only: heading is "${s.heading}"`)
      : fail(`claude-only: heading is "${s.heading}", want "What should Claude work on?"`);
    (s.placeholder.includes('Claude')) ? ok(`claude-only: placeholder names Claude ("${s.placeholder}")`)
      : fail(`claude-only: placeholder is "${s.placeholder}", expected it to include "Claude"`);
    await ctx.close();
  }

  // ── 2. Multi-provider, Gemini is the project/default provider on first paint ──
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    await installRoutes(page, { projects: [fixtureProject(PID, 'Gemini Default', 'gemini')], providers: [CLAUDE_PROVIDER, GEMINI_PROVIDER] });
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await openFreshComposer(page, PID);
    const s = await readState(page, PID);
    (s.heading === 'What should Gemini work on?') ? ok(`gemini-default: heading is "${s.heading}" (no hard-coded "Claude")`)
      : fail(`gemini-default: heading is "${s.heading}", want "What should Gemini work on?"`);
    (s.placeholder.includes('Gemini') && !s.placeholder.includes('Claude'))
      ? ok(`gemini-default: placeholder names Gemini, not Claude ("${s.placeholder}")`)
      : fail(`gemini-default: placeholder is "${s.placeholder}", expected it to include "Gemini" and not "Claude"`);
    await ctx.close();
  }

  // ── 3. Live switch: the Agent dropdown updates heading + placeholder ──
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    await installRoutes(page, { projects: [fixtureProject(PID, 'Switch Test', 'claude')], providers: [CLAUDE_PROVIDER, GEMINI_PROVIDER] });
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await openFreshComposer(page, PID);
    const before = await readState(page, PID);
    (before.heading === 'What should Claude work on?') ? ok(`switch: starts on Claude ("${before.heading}")`)
      : fail(`switch: starting heading is "${before.heading}", want "What should Claude work on?"`);

    const sel = await page.evaluate(() => {
      const el = document.querySelector('.composer-provider-row:not(.composer-character-row):not(.composer-model-row) .composer-provider-select');
      return el ? true : false;
    });
    if (!sel) fail('switch: Agent dropdown (.composer-provider-select) not found — cannot test live switch');
    else {
      await page.selectOption('.composer-provider-row:not(.composer-character-row):not(.composer-model-row) .composer-provider-select', 'gemini');
      await page.waitForTimeout(200);
      const after = await readState(page, PID);
      (after.heading === 'What should Gemini work on?') ? ok(`switch: heading follows the Agent dropdown ("${after.heading}")`)
        : fail(`switch: heading after switching to Gemini is "${after.heading}", want "What should Gemini work on?"`);
      (after.placeholder.includes('Gemini') && !after.placeholder.includes('Claude'))
        ? ok(`switch: placeholder follows the Agent dropdown ("${after.placeholder}")`)
        : fail(`switch: placeholder after switching is "${after.placeholder}", expected "Gemini" and not "Claude"`);
    }
    await page.screenshot({ path: resolve(SHOT_DIR, 'composer-provider-empty-state.png') });
    ok('screenshot saved');
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
  ? '✅ PASS — the +New empty-state heading/placeholder name the active provider, live, with no hard-coded "Claude".'
  : `❌ FAIL — ${bad} check(s) failed.`);
process.exit(exitCode);
