#!/usr/bin/env node
/**
 * Mission Control dashboard — headless boot smoke test.
 *
 * WHY THIS EXISTS
 * ---------------
 * `node --check` proves the SPA's inline <script> blocks *parse*, but it cannot
 * catch a RUNTIME throw during boot. On 2026-06-08 a temporal-dead-zone bug
 * (a `let` referenced by a function called before its declaration) threw a
 * ReferenceError at the top level of the boot script, aborting it before
 * fetchProjects() ran. The dashboard hung on its "Loading..." placeholder with
 * an empty project grid — and it shipped, because the author's open tab kept
 * the old JS (server restart != tab reload), so nobody hit a fresh load.
 *
 * This test loads the REAL static/index.html in headless Chromium and asserts
 * the project grid actually populates. A boot-aborting throw leaves the grid
 * empty, which fails the test loudly.
 *
 * It is hermetic: it fulfills the page + a canned /api/projects via Playwright
 * request interception and ABORTS every other request (CDNs + non-essential
 * API). The SPA is written to degrade on fetch failure (e.g. loadDomains()
 * falls back to a default list), so aborting exercises those real fallbacks
 * rather than guessing response shapes. No running MC server, no data, no net.
 *
 * RUN
 *   cd tools/smoke
 *   npm install
 *   npx playwright install chromium      # one-time, downloads the browser
 *   npm test
 *
 * Exit code 0 = grid rendered; 1 = boot failed / grid empty / harness error.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
// The SPA's styles were extracted from the inline <style> into this file
// (modernization Phase 3 module 1) — serve the real one or the shell under
// test renders unstyled (and any future CSS-dependent assertion lies).
const APP_CSS = readFileSync(resolve(REPO_ROOT, 'static', 'css', 'app.css'), 'utf8');
// Beacon stylesheet (Phase 2 view) — same serve-or-the-shell-renders-unstyled rule.
const BEACON_CSS = readFileSync(resolve(REPO_ROOT, 'static', 'css', 'beacon.css'), 'utf8');
// Ask Claydo ES module, extracted from the inline <script> (Phase 3 module 2).
// Every extracted /static/js/*.js must be fulfilled here or the hermetic
// harness aborts its request and the SPA boots without that feature.
const CLAYDO_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'claydo.js'), 'utf8');
// Mobile pairing ES module (Phase 3 module 3) — same rule as claydo.js above.
const MOBILE_PAIRING_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'mobile-pairing.js'), 'utf8');
// Walkthrough / tour ES module (Phase 3 module 4) — same rule as claydo.js above.
const WALKTHROUGH_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'walkthrough.js'), 'utf8');
// Skills panel ES module (Phase 3 module 5) — same rule as claydo.js above.
const SKILLS_PANEL_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'skills-panel.js'), 'utf8');
// Media gallery ES module — same rule as claydo.js above (an unlisted module is
// ABORTED by the hermetic harness, so the SPA boots without it and any
// assertion that depends on it lies).
const MEDIA_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'media.js'), 'utf8');
// Settings drill-down ES module (Phase 3 module 6) — same rule as claydo.js above.
const SETTINGS_DRILL_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'settings-drill.js'), 'utf8');
// Settings sections ES module (Phase 3 module 7) — same rule as claydo.js above.
const SETTINGS_SECTIONS_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'settings-sections.js'), 'utf8');
// Terminal pop-out ES module (Phase 3 module 8) — same rule as claydo.js above.
const TERMINAL_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'terminal.js'), 'utf8');
// Mermaid render pipeline ES module (Phase 3 module 9) — same rule as claydo.js above.
const MERMAID_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'mermaid.js'), 'utf8');
// Search-past-chats ES module (Phase 3 module 10) — same rule as claydo.js above.
const SEARCH_CHATS_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'search-chats.js'), 'utf8');
// Backlog actions ES module (Phase 3 module 11) — same rule as claydo.js above.
const BACKLOG_ACTIONS_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'backlog-actions.js'), 'utf8');
// Cross-project backlog ES module (Phase 3 module 12) — same rule as claydo.js above.
const CROSS_BACKLOG_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'cross-backlog.js'), 'utf8');
// Scheduler ES module (Phase 3 module 13) — same rule as claydo.js above.
const SCHEDULER_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'scheduler.js'), 'utf8');
// Scheduled-runs calendar ES module — same serve-or-it-boots-without-it rule.
const SCHEDULE_CALENDAR_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'schedule-calendar.js'), 'utf8');
// MCP servers ES module (Phase 3 module 14) — same rule as claydo.js above.
const MCP_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'mcp.js'), 'utf8');
// Secrets vault ES module — same rule as claydo.js above.
const SECRETS_PANEL_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'secrets-panel.js'), 'utf8');
// System status ES module (Phase 3 module 15) — same rule as claydo.js above.
const SYSTEM_STATUS_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'system-status.js'), 'utf8');
// Update/Power/restart ES module (Phase 3 module 16) — same rule as claydo.js above.
const UPDATE_POWER_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'update-power.js'), 'utf8');
// Provider-auth ES module (Phase 3 module 17) — same rule as claydo.js above.
const PROVIDER_AUTH_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'provider-auth.js'), 'utf8');
// Schedule-banner ES module (Phase 3 module 18) — same rule as claydo.js above.
const SCHEDULE_BANNER_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'schedule-banner.js'), 'utf8');
// Provider-settings ES module (Phase 3 module 19) — same rule as claydo.js above.
const PROVIDER_SETTINGS_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'provider-settings.js'), 'utf8');
// Process-manager ES module (Phase 3 module 20) — same rule as claydo.js above.
const PROCESS_MANAGER_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'process-manager.js'), 'utf8');
// Cross-project Hivemind ES module (Phase 3 module 21) — same rule as claydo.js above.
const CROSS_HIVEMIND_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'cross-hivemind.js'), 'utf8');
const FEED_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'feed.js'), 'utf8');
// Beacon ES module (Phase 2 view) — same rule as claydo.js above.
const BEACON_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'beacon.js'), 'utf8');
const MOBILE_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'mobile.js'), 'utf8');
const PROJECT_ACTIONS_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'project-actions.js'), 'utf8');
const COMPOSER_EXTRAS_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'composer-extras.js'), 'utf8');
const SLASH_AC_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'slash-autocomplete.js'), 'utf8');
const APPEARANCE_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'appearance.js'), 'utf8');
const PROJECT_FORMS_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'project-forms.js'), 'utf8');
const INTERACTIONS_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'interactions.js'), 'utf8');
const RENDER_CORE_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'render-core.js'), 'utf8');
const MODAL_MANAGER_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'modal-manager.js'), 'utf8');
const AGENT_CONSOLE_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'agent-console.js'), 'utf8');
const HIVEMIND_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'hivemind.js'), 'utf8');
const AGENT_LOG_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'agent-log.js'), 'utf8');
const RESUME_PREVIEW_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'resume-preview.js'), 'utf8');
const CONVERSATION_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'conversation.js'), 'utf8');
const RICH_TEXT_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'rich-text.js'), 'utf8');
const PROJECTS_JSON = readFileSync(resolve(__dirname, 'fixtures', 'projects.json'), 'utf8');

const ORIGIN = 'http://mc.smoke.test';   // arbitrary; every request is intercepted
const BOOT_TIMEOUT_MS = 15000;

// 4x4 PNG — a stand-in custom background image.
const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAYAAACp8Z5+AAAAEklEQVR4nGP8z8Dwn4EIwDiqEAAA//8DABjcA0/9b3pPAAAAAElFTkSuQmCC';

// Boot scenarios. Each is a fresh page with a different localStorage appearance
// state (set BEFORE first paint via addInitScript). Every one must boot and
// render the grid. These cover the appearance code paths that run during boot —
// the area that has now produced TWO boot-aborting TDZ bugs (bgMode, then
// _bgDimsLoading). The "image, NO dims" case is the one that hung the whole UI
// on 2026-06-08: a legacy image with no stored dims makes applyDashboardBackground
// call _bgLoadImageDims at boot. Without it here the test was falsely green.
const SCENARIOS = [
  { name: 'default theme (no bg)', ls: {} },
  { name: 'image bg, NO stored dims (legacy)', ls: { mc_bg_mode: 'image', mc_bg_image: PNG } },
  { name: 'image bg, with dims + framing', ls: { mc_bg_mode: 'image', mc_bg_image: PNG, mc_bg_imgw: '4', mc_bg_imgh: '4', mc_bg_zoom: '140', mc_bg_posx: '30', mc_bg_posy: '70' } },
  { name: 'solid color bg', ls: { mc_bg_mode: 'color', mc_bg_color: '#123456' } },
  { name: 'warm tone', ls: { mc_tone: 'warm' } },
];

// Static asset map (path → [contentType, body]) shared by the boot scenarios
// and the dispatch guard. Adding a new extracted /static/js/*.js module means
// adding ONE entry here — both code paths pick it up, and the boot scenarios
// self-validate the map (a missing/typo'd entry empties the grid and fails).
const STATIC_MAP = {
  '/static/css/app.css': ['text/css; charset=utf-8', APP_CSS],
  '/static/css/beacon.css': ['text/css; charset=utf-8', BEACON_CSS],
  '/static/js/claydo.js': ['text/javascript; charset=utf-8', CLAYDO_JS],
  '/static/js/mobile-pairing.js': ['text/javascript; charset=utf-8', MOBILE_PAIRING_JS],
  '/static/js/walkthrough.js': ['text/javascript; charset=utf-8', WALKTHROUGH_JS],
  '/static/js/skills-panel.js': ['text/javascript; charset=utf-8', SKILLS_PANEL_JS],
  '/static/js/media.js': ['text/javascript; charset=utf-8', MEDIA_JS],
  '/static/js/settings-drill.js': ['text/javascript; charset=utf-8', SETTINGS_DRILL_JS],
  '/static/js/settings-sections.js': ['text/javascript; charset=utf-8', SETTINGS_SECTIONS_JS],
  '/static/js/terminal.js': ['text/javascript; charset=utf-8', TERMINAL_JS],
  '/static/js/mermaid.js': ['text/javascript; charset=utf-8', MERMAID_JS],
  '/static/js/search-chats.js': ['text/javascript; charset=utf-8', SEARCH_CHATS_JS],
  '/static/js/backlog-actions.js': ['text/javascript; charset=utf-8', BACKLOG_ACTIONS_JS],
  '/static/js/cross-backlog.js': ['text/javascript; charset=utf-8', CROSS_BACKLOG_JS],
  '/static/js/scheduler.js': ['text/javascript; charset=utf-8', SCHEDULER_JS],
  '/static/js/schedule-calendar.js': ['text/javascript; charset=utf-8', SCHEDULE_CALENDAR_JS],
  '/static/js/mcp.js': ['text/javascript; charset=utf-8', MCP_JS],
  '/static/js/secrets-panel.js': ['text/javascript; charset=utf-8', SECRETS_PANEL_JS],
  '/static/js/system-status.js': ['text/javascript; charset=utf-8', SYSTEM_STATUS_JS],
  '/static/js/update-power.js': ['text/javascript; charset=utf-8', UPDATE_POWER_JS],
  '/static/js/provider-auth.js': ['text/javascript; charset=utf-8', PROVIDER_AUTH_JS],
  '/static/js/schedule-banner.js': ['text/javascript; charset=utf-8', SCHEDULE_BANNER_JS],
  '/static/js/provider-settings.js': ['text/javascript; charset=utf-8', PROVIDER_SETTINGS_JS],
  '/static/js/process-manager.js': ['text/javascript; charset=utf-8', PROCESS_MANAGER_JS],
  '/static/js/cross-hivemind.js': ['text/javascript; charset=utf-8', CROSS_HIVEMIND_JS],
  '/static/js/feed.js': ['text/javascript; charset=utf-8', FEED_JS],
  '/static/js/beacon.js': ['text/javascript; charset=utf-8', BEACON_JS],
  '/static/js/mobile.js': ['text/javascript; charset=utf-8', MOBILE_JS],
  '/static/js/project-actions.js': ['text/javascript; charset=utf-8', PROJECT_ACTIONS_JS],
  '/static/js/composer-extras.js': ['text/javascript; charset=utf-8', COMPOSER_EXTRAS_JS],
  '/static/js/slash-autocomplete.js': ['text/javascript; charset=utf-8', SLASH_AC_JS],
  '/static/js/appearance.js': ['text/javascript; charset=utf-8', APPEARANCE_JS],
  '/static/js/project-forms.js': ['text/javascript; charset=utf-8', PROJECT_FORMS_JS],
  '/static/js/interactions.js': ['text/javascript; charset=utf-8', INTERACTIONS_JS],
  '/static/js/render-core.js': ['text/javascript; charset=utf-8', RENDER_CORE_JS],
  '/static/js/modal-manager.js': ['text/javascript; charset=utf-8', MODAL_MANAGER_JS],
  '/static/js/agent-console.js': ['text/javascript; charset=utf-8', AGENT_CONSOLE_JS],
  '/static/js/hivemind.js': ['text/javascript; charset=utf-8', HIVEMIND_JS],
  '/static/js/agent-log.js': ['text/javascript; charset=utf-8', AGENT_LOG_JS],
  '/static/js/resume-preview.js': ['text/javascript; charset=utf-8', RESUME_PREVIEW_JS],
  '/static/js/conversation.js': ['text/javascript; charset=utf-8', CONVERSATION_JS],
  '/static/js/rich-text.js': ['text/javascript; charset=utf-8', RICH_TEXT_JS],
};

// Hermetic router: serve the page + every extracted module + canned
// /api/projects & /api/config; abort everything else so the SPA exercises its
// real fetch-failure fallbacks. Shared by all boot scenarios.
function fulfillStaticOrAbort(route) {
  const path = new URL(route.request().url()).pathname;
  if (path === '/' || path === '/index.html')
    return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  const hit = STATIC_MAP[path];
  if (hit)
    return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
  if (path === '/api/projects')
    return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
  if (path === '/api/config')
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  return route.abort();  // CDNs + non-essential API → SPA fallbacks handle it
}

async function runScenario(browser, sc) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  await page.addInitScript((ls) => {
    try { for (const k of Object.keys(ls)) localStorage.setItem(k, ls[k]); } catch (e) {}
  }, sc.ls);
  await page.route('**/*', fulfillStaticOrAbort);
  const pageErrors = [];
  page.on('pageerror', (err) => pageErrors.push(err.message || String(err)));
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });

  let cardCount = 0, timedOut = false;
  try {
    await page.waitForSelector('#projects-col .card', { timeout: BOOT_TIMEOUT_MS });
    cardCount = await page.locator('#projects-col .card').count();
  } catch { timedOut = true; cardCount = await page.locator('#projects-col .card').count().catch(() => 0); }

  const ok = cardCount > 0;
  if (ok) {
    console.log(`✅ ${sc.name}: booted, grid rendered ${cardCount} tile(s).`);
  } else {
    console.error(`❌ ${sc.name}: project grid never rendered (boot aborted).`);
    const colText = await page.locator('#projects-col').innerText().catch(() => '(unreadable)');
    console.error(`     #projects-col text: ${JSON.stringify(colText.slice(0, 120))}`);
    if (timedOut) console.error(`     (waited ${BOOT_TIMEOUT_MS}ms for "#projects-col .card")`);
    if (pageErrors.length) {
      console.error('     Uncaught exception(s) during boot — likely the cause:');
      pageErrors.forEach((e) => console.error(`       • ${e}`));
    } else {
      console.error('     No uncaught exception captured — check the /api/projects fetch path.');
    }
  }
  await ctx.close();
  return ok;
}

// Dispatch guard — boots the page, opens the +New composer, picks a persona, and
// drives dispatchAgent(). This is the cross-module path that broke on 2026-06-12:
// resume-preview.js (the dispatch code) referenced `pendingDispatchCharacter`, a
// module-scoped `let` in conversation.js — a ReferenceError across the ES-module
// boundary that aborted EVERY dispatch (new + resumed chats flipped to STOPPED
// with no agent spawned). The boot test never caught it because it never opened
// the composer. A clean dispatch must (a) raise no uncaught exception and (b)
// promote the optimistic tab to the server session id — proving the whole
// read + clear cross-module persona path executed.
async function runDispatchGuard(browser) {
  const PID = 'smoke_alpha';
  const SESS = 'smoke_sess_1';
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  // The POST firing is the key signal: the regression threw inside dispatchAgent
  // (clearPendingCharacter, line ~294) BEFORE the fetch, so the dispatch endpoint
  // was never hit. If this goes true, the whole cross-module persona path ran.
  let dispatchHit = false;
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    // Canned persona so the picker + cross-module character resolution run.
    if (path === '/api/characters')
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify([{ name: 'analyst', scope: 'global', display_name: 'Analyst', description: 'x', file: 'analyst.md', size: 10 }]) });
    // Canned dispatch success so the post-POST promotion path (incl.
    // clearPendingCharacter) executes end-to-end.
    if (/\/agent\/dispatch$/.test(path)) {
      dispatchHit = true;
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ ok: true, session_id: SESS }) });
    }
    // The shared fixture has project_path="" (agentPanelHTML then shows the
    // "set project_path" notice, not the composer). Patch a non-empty path for
    // the guard only — the boot scenarios keep the byte-identical fixture.
    if (path === '/api/projects') {
      const patched = JSON.parse(PROJECTS_JSON);
      if (patched[0]) patched[0].project_path = '/smoke/alpha';
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(patched) });
    }
    return fulfillStaticOrAbort(route);
  });
  const pageErrors = [];
  page.on('pageerror', (err) => pageErrors.push(err.message || String(err)));
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  try {
    await page.waitForSelector('#projects-col .card', { timeout: BOOT_TIMEOUT_MS });
  } catch {
    console.error('❌ dispatch guard: grid never rendered (boot failed before the guard could run).');
    await ctx.close();
    return false;
  }

  const result = await page.evaluate(async ({ pid }) => {
    const out = { err: null, finalTab: null };
    try {
      if (typeof openProjectModal !== 'function') throw new Error('openProjectModal not defined');
      if (typeof dispatchAgent !== 'function') throw new Error('dispatchAgent not defined');
      openProjectModal(pid);
      window.agentConvNew = window.agentConvNew || {};
      agentConvNew[pid] = true;                       // force the +New composer
      if (typeof refreshModal === 'function') refreshModal();
      await new Promise((r) => setTimeout(r, 600));   // let _ensureCharacters resolve
      // Pick the persona — exercises setComposerCharacter plus the cross-module
      // getPendingCharacter/resolveCharacterMeta reads inside dispatchAgent.
      if (typeof setComposerCharacter === 'function') setComposerCharacter(pid, 'global:analyst');
      let ta = document.getElementById('agent-task-' + pid);
      if (!ta && typeof newAgentTab === 'function') {
        newAgentTab(pid);                              // the real "+ New" action
        await new Promise((r) => setTimeout(r, 400));
        ta = document.getElementById('agent-task-' + pid);
      }
      if (!ta) {
        out.diag = {
          modalWindows: document.querySelectorAll('.modal-window').length,
          textareas: Array.from(document.querySelectorAll('textarea')).map((e) => e.id || e.className),
        };
        throw new Error('composer textarea (#agent-task-' + pid + ') not rendered');
      }
      ta.value = 'smoke dispatch ping';
      await dispatchAgent(pid);
      await new Promise((r) => setTimeout(r, 300));
      out.finalTab = (window.activeAgentTab || {})[pid] || null;
    } catch (e) {
      out.err = e.message + ' | ' + ((e.stack || '').split('\n')[1] || '').trim();
    }
    return out;
  }, { pid: PID });

  await ctx.close();

  // EventSource/aborted-fetch noise (SSE + non-essential API are aborted) is
  // expected and not a real failure; only genuine uncaught exceptions count.
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  let ok = true;
  if (result.err) { ok = false; console.error(`❌ dispatch guard: dispatchAgent threw — ${result.err}`); if (result.diag) console.error('     diag: ' + JSON.stringify(result.diag)); }
  if (uncaught.length) {
    ok = false;
    console.error('❌ dispatch guard: uncaught exception(s) during dispatch:');
    uncaught.forEach((e) => console.error(`       • ${e}`));
  }
  if (ok && !dispatchHit) {
    ok = false;
    console.error('❌ dispatch guard: dispatchAgent never reached the POST — it aborted mid-path ' +
      '(the cross-module persona code throws before the fetch in the regression this guards).');
  }
  if (ok) console.log('✅ dispatch guard: +New composer dispatched cleanly to the server (persona path, no cross-module throw).');
  return ok;
}

// ── Per-provider Model picker guard ──────────────────────────────────────────
// The composer's Model picker is PER PROVIDER: model ids don't cross runtimes
// (`codex -m claude-opus-5` is a hard CLI error), so switching the Agent select
// must rebuild the options AND must not leave the previous provider's model
// armed. Both halves are invisible in the DOM diff of a normal boot, and the
// pending-model map is module-scoped to conversation.js — exactly the shape of
// bug the dispatch guard above exists for.
async function runModelPickerGuard(browser) {
  const PID = 'smoke_alpha';
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  let dispatchBody = null;
  // Two installed providers so the Agent picker renders at all (it hides when
  // only claude is available), each with the catalog the real endpoint serves.
  const PROVIDERS = { providers: [
    { name: 'claude', display_name: 'Claude Code', installed: true, capabilities: {},
      models: [{ id: 'claude-sonnet-5', label: 'Sonnet 5' }, { id: 'claude-opus-5', label: 'Opus 5' }] },
    { name: 'codex', display_name: 'Codex CLI', installed: true, capabilities: {},
      models: [{ id: 'gpt-5-codex', label: 'GPT-5 Codex' }, { id: 'gpt-5', label: 'GPT-5' }] },
  ], default: 'claude' };
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/agent/providers')
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(PROVIDERS) });
    if (path === '/api/characters')
      return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (/\/agent\/dispatch$/.test(path)) {
      try { dispatchBody = JSON.parse(route.request().postData() || '{}'); } catch { dispatchBody = {}; }
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ ok: true, session_id: 'smoke_sess_model' }) });
    }
    if (path === '/api/projects') {
      const patched = JSON.parse(PROJECTS_JSON);
      if (patched[0]) patched[0].project_path = '/smoke/alpha';
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(patched) });
    }
    return fulfillStaticOrAbort(route);
  });
  const pageErrors = [];
  page.on('pageerror', (err) => pageErrors.push(err.message || String(err)));
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  try {
    await page.waitForSelector('#projects-col .card', { timeout: BOOT_TIMEOUT_MS });
  } catch {
    console.error('❌ model picker: grid never rendered (boot failed before the guard could run).');
    await ctx.close();
    return false;
  }

  const out = await page.evaluate(async ({ pid }) => {
    const r = { err: null };
    const modelOpts = () => {
      const row = document.querySelector('#agent-panel-' + pid + ' .composer-model-row')
        || document.querySelector('.composer-model-row');
      if (!row) return null;
      return Array.from(row.querySelectorAll('option')).map((o) => o.value);
    };
    const settle = () => new Promise((res) => setTimeout(res, 350));
    try {
      openProjectModal(pid);
      window.agentConvNew = window.agentConvNew || {};
      agentConvNew[pid] = true;
      if (typeof refreshModal === 'function') refreshModal();
      await settle();
      r.claudeOpts = modelOpts();

      setComposerProvider(pid, 'codex');
      await settle();
      r.codexOpts = modelOpts();

      setComposerModel(pid, 'gpt-5-codex');
      await settle();
      r.armedOnCodex = window.getPendingDispatchModel(pid);

      // The leak check: flip back to claude — codex's pick must NOT follow.
      setComposerProvider(pid, 'claude');
      await settle();
      r.armedOnClaude = window.getPendingDispatchModel(pid);

      // ...and flipping back to codex remembers it (sticky per provider).
      setComposerProvider(pid, 'codex');
      await settle();
      r.rearmedOnCodex = window.getPendingDispatchModel(pid);

      const ta = document.getElementById('agent-task-' + pid);
      if (!ta) throw new Error('composer textarea not rendered');
      ta.value = 'smoke model ping';
      await dispatchAgent(pid);
      await new Promise((res) => setTimeout(res, 300));
    } catch (e) {
      r.err = e.message + ' | ' + ((e.stack || '').split('\n')[1] || '').trim();
    }
    return r;
  }, { pid: PID });

  await ctx.close();

  const fails = [];
  if (out.err) fails.push(`threw — ${out.err}`);
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fails.push(`uncaught: ${e}`));
  if (!out.claudeOpts) fails.push('no Model row rendered for claude');
  else if (!out.claudeOpts.includes('claude-opus-5'))
    fails.push(`claude options missing its own models: ${JSON.stringify(out.claudeOpts)}`);
  if (!out.codexOpts) fails.push('no Model row rendered for codex (the picker Ron saw missing)');
  else {
    if (!out.codexOpts.includes('gpt-5-codex'))
      fails.push(`codex options missing gpt-5-codex: ${JSON.stringify(out.codexOpts)}`);
    if (out.codexOpts.some((v) => v.startsWith('claude-')))
      fails.push(`claude model ids leaked into the codex picker: ${JSON.stringify(out.codexOpts)}`);
  }
  if (out.armedOnCodex !== 'gpt-5-codex') fails.push(`pick not armed for codex (got ${JSON.stringify(out.armedOnCodex)})`);
  if (out.armedOnClaude) fails.push(`codex's model leaked onto claude: ${JSON.stringify(out.armedOnClaude)}`);
  if (out.rearmedOnCodex !== 'gpt-5-codex') fails.push(`per-provider pick not sticky (got ${JSON.stringify(out.rearmedOnCodex)})`);
  if (!dispatchBody) fails.push('dispatch never reached the server');
  else {
    if (dispatchBody.model !== 'gpt-5-codex') fails.push(`dispatch sent model=${JSON.stringify(dispatchBody.model)}`);
    if (dispatchBody.provider !== 'codex') fails.push(`dispatch sent provider=${JSON.stringify(dispatchBody.provider)}`);
  }

  if (fails.length) {
    console.error('❌ model picker guard:');
    fails.forEach((f) => console.error(`       • ${f}`));
    return false;
  }
  console.log('✅ model picker: per-provider options render, no cross-provider leak, chosen model reaches dispatch.');
  return true;
}


// ── Backlog live-refresh + tab-reset guard ───────────────────────────────────
// Two defects that shared one symptom ("I have to close and reopen the project"):
//
//  1. /api/projects stopped shipping the `backlog` array (counts only), so
//     _preserveOpenBacklogs() copies the PREVIOUS array back over the fresh
//     record for any open modal. Every mutation then ended in refreshSilent(),
//     which restored the pre-change list — adds, status toggles and deletes were
//     all invisible until a reopen dropped _backlogFull and forced a refetch.
//  2. closeModalById() never cleared modalActiveTab, so a project closed on the
//     Backlog tab reopened straight back onto Backlog.
//
// Both are pure client state, so only a real browser catches them.
async function runBacklogRefreshGuard(browser) {
  const PID = 'smoke_alpha';
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  // Server-side truth for this project's backlog. Mutations edit THIS, and the
  // modal is only correct if it re-reads it — mirroring the real endpoint split
  // (list endpoint = counts, /backlog = the items).
  let items = [{ id: 'itm_one', text: 'existing item', status: 'open', priority: 'normal',
                 created_at: '2026-08-13T10:00:00Z', attachments: [], notes: [] }];
  await page.route('**/*', (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const json = (body) => route.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify(body) });
    if (/\/backlog$/.test(path) && req.method() === 'GET') return json(items);
    if (/\/backlog$/.test(path) && req.method() === 'POST') {
      const body = JSON.parse(req.postData() || '{}');
      const item = { id: 'itm_new', text: body.text, status: 'open',
                     priority: body.priority || 'normal',
                     created_at: '2026-08-13T11:00:00Z', attachments: [], notes: [] };
      items = [item, ...items];
      return json({ ok: true, item });
    }
    if (/\/backlog\/[^/]+$/.test(path) && req.method() === 'DELETE') {
      const id = path.split('/').pop();
      items = items.filter((i) => i.id !== id);
      return json({ ok: true });
    }
    if (path === '/api/projects') {
      // Counts only — exactly like the real endpoint. If the modal renders items
      // from this, the guard is testing the wrong thing.
      const patched = JSON.parse(PROJECTS_JSON);
      if (patched[0]) {
        patched[0].project_path = '/smoke/alpha';
        delete patched[0].backlog;
        patched[0].backlog_open_count = items.filter((i) => i.status === 'open').length;
        patched[0].backlog_total_count = items.length;
      }
      return json(patched);
    }
    return fulfillStaticOrAbort(route);
  });
  const pageErrors = [];
  page.on('pageerror', (err) => pageErrors.push(err.message || String(err)));
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  try {
    await page.waitForSelector('#projects-col .card', { timeout: BOOT_TIMEOUT_MS });
  } catch {
    console.error('❌ backlog guard: grid never rendered (boot failed before the guard could run).');
    await ctx.close();
    return false;
  }

  const out = await page.evaluate(async ({ pid }) => {
    const r = { err: null };
    const settle = (ms) => new Promise((res) => setTimeout(res, ms || 450));
    const rowTexts = () => Array.from(document.querySelectorAll(
      '.backlog-list .backlog-item'))
      .map((e) => (e.innerText || '').trim());
    try {
      openProjectModal(pid);
      await settle();
      switchModalTab(pid, 'backlog');
      await settle();
      r.before = rowTexts();

      const ta = document.getElementById('backlog-input-' + pid);
      if (!ta) throw new Error('backlog composer not rendered');
      // Compose must be ABOVE the list — otherwise adding means scrolling past
      // every item, which is what buried the "Back to conversation" bar.
      const listEl = document.querySelector('.backlog-list');
      const addEl = document.querySelector('.backlog-add');
      r.composeAboveList = !!(listEl && addEl) &&
        !!(addEl.compareDocumentPosition(listEl) & Node.DOCUMENT_POSITION_FOLLOWING);

      ta.value = 'freshly added item';
      await addBacklogItem(pid);
      await settle(700);
      r.afterAdd = rowTexts();

      // Tab must NOT survive a close/reopen cycle.
      // `let modalActiveTab` (index.html top level) is a global lexical binding —
      // reachable as a bare identifier, NOT a property of window.
      const activeTab = () => (typeof modalActiveTab !== 'undefined' ? modalActiveTab : {})[pid];
      r.tabBeforeClose = activeTab();
      closeModalById(pid);   // openModals is keyed by the bare project id
      await settle(200);
      openProjectModal(pid);
      await settle();
      r.tabAfterReopen = activeTab() || 'agent';
    } catch (e) {
      r.err = e.message + ' | ' + ((e.stack || '').split('\n')[1] || '').trim();
    }
    return r;
  }, { pid: PID });

  await ctx.close();

  const fails = [];
  if (out.err) fails.push(`threw — ${out.err}`);
  pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e))
    .forEach((e) => fails.push(`uncaught: ${e}`));
  if (!Array.isArray(out.before) || !out.before.length)
    fails.push('backlog list never rendered its seeded item');
  const added = (out.afterAdd || []).some((t) => t.includes('freshly added item'));
  if (!added)
    fails.push('added item did NOT appear without a reopen — the stale-cache bug is back ' +
      `(rows: ${JSON.stringify(out.afterAdd)})`);
  if (!out.composeAboveList) fails.push('the add-item composer is not above the list');
  if (out.tabBeforeClose !== 'backlog') fails.push(`switchModalTab didn't stick (${out.tabBeforeClose})`);
  if (out.tabAfterReopen !== 'agent')
    fails.push(`reopening restored the '${out.tabAfterReopen}' tab instead of defaulting to agent`);

  if (fails.length) {
    console.error('❌ backlog guard:');
    fails.forEach((f) => console.error(`       • ${f}`));
    return false;
  }
  console.log('✅ backlog: new item renders without a reopen, compose sits above the list, tab resets on close.');
  return true;
}


// ── Scheduled-runs calendar guard ────────────────────────────────────────────
// Two things here can be confidently wrong rather than obviously broken, which
// is why they get asserted rather than eyeballed:
//
//   * recurrence expansion — a daily/cron schedule placed on the wrong weekday
//     looks completely plausible. `days` is 1=Mon..7=Sun here while JS getDay()
//     is 0=Sun..6=Sat, and cron with BOTH dom and dow restricted is a UNION,
//     not an intersection.
//   * the paused/disabled treatment — the whole point is that the grid must
//     never imply a run that cannot happen.
async function runScheduleCalendarGuard(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  const SCHEDULES = [
    { id: 'sch_daily', project_id: 'alpha', project_name: 'Alpha', enabled: true,
      schedule_type: 'daily', time: '09:30', days: [1, 3, 5], task: 'Weekday standup' },
    { id: 'sch_everyday', project_id: 'beta', project_name: 'Beta', enabled: true,
      schedule_type: 'daily', time: '07:00', days: [], description: 'Every single day',
      task: 'LONG PROMPT BODY: ' + 'x'.repeat(400) },
    { id: 'sch_off', project_id: 'beta', project_name: 'Beta', enabled: false,
      schedule_type: 'daily', time: '21:00', days: [], task: 'Disabled nightly' },
    { id: 'sch_interval', project_id: 'gamma', project_name: 'Gamma', enabled: true,
      schedule_type: 'interval', interval_minutes: 5, task: 'Reaction poller' },
    { id: 'sch_cron', project_id: 'delta', project_name: 'Delta', enabled: true,
      schedule_type: 'cron', cron_expr: '0 6 * * 1', task: 'Monday cron' },
    { id: 'sch_weird', project_id: 'delta', project_name: 'Delta', enabled: true,
      schedule_type: 'cron', cron_expr: '@yearly', task: 'Unparseable cron',
      next_run: '2027-01-01T00:00:00Z' },
  ];
  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const json = (b) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(b) });
    if (path === '/api/schedules' && req.method() === 'GET') return json(SCHEDULES);
    if (/^\/api\/schedules\/[^/]+$/.test(path) && req.method() === 'PUT') {
      const id = path.split('/').pop();
      const body = JSON.parse(req.postData() || '{}');
      const rec = SCHEDULES.find((x) => x.id === id);
      if (rec) rec.enabled = body.enabled;
      return json({ ok: true });
    }
    if (path === '/api/config') return json({ scheduler_paused: false });
    return fulfillStaticOrAbort(route);
  });
  const pageErrors = [];
  page.on('pageerror', (err) => pageErrors.push(err.message || String(err)));
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  try {
    await page.waitForSelector('#projects-col .card', { timeout: BOOT_TIMEOUT_MS });
  } catch {
    console.error('CALFAIL calendar guard: grid never rendered.');
    await ctx.close();
    return false;
  }

  const out = await page.evaluate(async () => {
    const r = { err: null };
    const settle = (ms) => new Promise((res) => setTimeout(res, ms || 400));
    try {
      const scheds = await (await fetch('/api/schedules')).json();
      // A Sunday, so the week runs Sun 2026-08-16 .. Sat 2026-08-22.
      const weekStart = new Date(2026, 7, 16, 0, 0, 0, 0);
      const wk = scalBuildWeek(scheds, weekStart);
      const dayOf = (i) => wk.days[i].items.map((it) => it.s.id);
      r.sun = dayOf(0); r.mon = dayOf(1); r.tue = dayOf(2);
      r.alwaysIds = wk.always.map((s) => s.id);
      r.unparsedIds = wk.unparsed.map((s) => s.id);
      const monTimes = wk.days[1].items.map((it) =>
        String(it.when.getHours()).padStart(2, '0') + ':' + String(it.when.getMinutes()).padStart(2, '0'));
      r.monTimes = monTimes;
      r.monSorted = monTimes.slice().sort().join() === monTimes.join();

      const c = _scalParseCron('0 6 1 * 5');
      r.cronUnion = !!c && c.domRestricted && c.dowRestricted;
      r.cronBad = _scalParseCron('@yearly');

      // Calendar is its own modal reached from the nav — NOT a mode of the
      // Scheduled Tasks modal, where it sat under that modal's own pause bar
      // (two identical pause messages) and the whole Stewards section.
      await openSchedulerCalendar();
      await settle(900);
      r.navEntries = Array.from(document.querySelectorAll('[data-nav="calendar"]')).length;
      r.ownModal = !!document.querySelector('[data-modal-id="__calendar"]');
      r.hasTimeGrid = !!document.querySelector('#schedule-calendar .scal-time-cols');
      r.hourLabels = Array.from(document.querySelectorAll('#schedule-calendar .scal-hour span'))
        .map((e) => e.textContent.trim());
      r.blockCount = document.querySelectorAll('#schedule-calendar .scal-block').length;
      r.deadCount = document.querySelectorAll('#schedule-calendar .scal-block.dead').length;
      // Always-running schedules belong in an all-day band INSIDE the frame
      // (Outlook/Google style), one cell per day column — not a separate strip
      // above the calendar framing them as an exception to the day.
      r.hasAlwaysStrip = !!document.querySelector('#schedule-calendar .scal-strip.always');
      r.allDayBand = !!document.querySelector('#schedule-calendar .scal-allday');
      r.allDayCells = document.querySelectorAll('#schedule-calendar .scal-allday-cell').length;
      r.allDayChips = document.querySelectorAll('#schedule-calendar .scal-allday-chip').length;
      // It must sit above the hour body, not float somewhere else.
      const band = document.querySelector('#schedule-calendar .scal-allday');
      const hourBody = document.querySelector('#schedule-calendar .scal-time-body');
      r.allDayAboveHours = !!(band && hourBody &&
        band.getBoundingClientRect().bottom <= hourBody.getBoundingClientRect().top + 2);
      r.hasUnparsedStrip = !!document.querySelector('#schedule-calendar .scal-strip.unparsed');
      // Exactly one pause notice on this surface, and no second banner.
      r.pausePills = document.querySelectorAll('#schedule-calendar .scal-paused-pill').length;

      // Blocks must be positioned by TIME, not stacked in a day bucket. The
      // 07:00 run has to sit above the 21:00 one in the same column.
      const posOf = (title) => {
        const el = Array.from(document.querySelectorAll('#schedule-calendar .scal-block'))
          .find((b) => (b.textContent || '').includes(title));
        return el ? Math.round(parseFloat(el.style.top)) : null;
      };
      r.topEarly = posOf('Every single day');
      r.topLate = posOf('Disabled nightly');

      // The block label is a TITLE. The full agent prompt must NOT be on the grid.
      const firstBlock = document.querySelector('#schedule-calendar .scal-block');
      r.blockText = firstBlock ? firstBlock.textContent.trim() : '';
      r.gridLeaksPrompt = /Generate 500 scenarios|LONG PROMPT BODY/.test(
        document.getElementById('schedule-calendar').textContent);

      // …and opening one reveals it.
      r.detailBeforeOpen = document.querySelectorAll('.scal-detail').length;
      scalOpenDetail('sch_everyday');
      await settle(400);
      const sheet = document.querySelector('.scal-detail');
      r.detailOpened = !!sheet;
      r.detailShowsPrompt = !!(sheet && sheet.textContent.includes('LONG PROMPT BODY'));
      scalCloseDetail();
      await settle(200);
      r.detailClosed = document.querySelectorAll('.scal-detail').length === 0;

      // Four ranges, like every phone calendar. Each must actually change the
      // number of day columns / cells — a switcher that renders the same grid
      // four times looks like it works.
      r.views = {};
      for (const v of ['day', '3day', 'week']) {
        scalSetView(v);
        await settle(300);
        r.views[v] = document.querySelectorAll('#schedule-calendar .scal-col').length;
      }
      scalSetView('month');
      await settle(400);
      r.monthCells = document.querySelectorAll('#schedule-calendar .scal-mcell').length;
      r.monthHasTimeGrid = !!document.querySelector('#schedule-calendar .scal-time-cols');
      // A daily job must appear ONCE per month cell, not once per occurrence.
      const firstCell = document.querySelector('#schedule-calendar .scal-mcell');
      r.monthChipsInCell = firstCell ? firstCell.querySelectorAll('.scal-mchip').length : -1;
      r.monthDupInCell = firstCell
        ? (() => {
            const t = Array.from(firstCell.querySelectorAll('.scal-mchip')).map(e => e.textContent);
            return t.length !== new Set(t).size;
          })() : false;
      // Drilling into a day from the month grid.
      document.querySelector('#schedule-calendar .scal-mcell').click();
      await settle(400);
      r.afterCellClick = document.querySelectorAll('#schedule-calendar .scal-col').length;

      scalSetView('week');
      await settle(400);
      // The hour band must SCROLL, and must not spill past the modal. The render
      // target sits between the flex container and the grid, so a missing flex
      // rule on it silently turns the scroller into a full-height block: the day
      // then just stops at whatever fit on screen with no way to reach the rest.
      const body = document.querySelector('#schedule-calendar .scal-time-body');
      const timeEl = document.querySelector('#schedule-calendar .scal-time');
      const surface = document.querySelector('.scal-surface');
      r.bodyScrolls = !!(body && body.scrollHeight > body.clientHeight);
      r.gridWithinSurface = !!(timeEl && surface &&
        Math.round(timeEl.getBoundingClientRect().bottom)
          <= Math.round(surface.getBoundingClientRect().bottom) + 2);
      if (body) { body.scrollTop = 99999; r.scrolledTo = body.scrollTop; }
      // The grid must cover the WHOLE day. Clamping to the used band meant a
      // week of morning jobs rendered a calendar with no evening at all, and no
      // amount of scrolling could reveal one.
      r.hours = Array.from(document.querySelectorAll('#schedule-calendar .scal-hour span'))
        .map((e) => e.textContent.trim());
      // …and it should still OPEN on the first run rather than at midnight.
      // Navigation must actually move, and must SAY so — a permanently-captioned
      // "Today" button read as a label for the range, so every day looked like
      // today. It now appears only when today is off-screen.
      scalSetView('day');
      // Earlier steps drilled into a month cell, which moves the anchor — reset
      // to today first or "on today" assertions are testing some other day.
      scalShift(0);
      await settle(350);
      const label = () => document.querySelector('#schedule-calendar .scal-range').textContent.trim();
      const todayBtn = () => document.querySelectorAll('#schedule-calendar .scal-today').length;
      r.labelOnToday = label();
      r.todayBtnOnToday = todayBtn();
      scalShift(1); await settle(350);
      r.labelNext = label();
      r.todayBtnAway = todayBtn();
      r.todayHighlightAway = document.querySelectorAll('#schedule-calendar .scal-head.today').length;

      // Swipe left = forward, exactly like the > button.
      const swipeSurface = document.querySelector('.scal-surface');
      const touchAt = (x, y) => new Touch({ identifier: 1, target: swipeSurface, clientX: x, clientY: y });
      const fire = (type, x, y) => swipeSurface.dispatchEvent(new TouchEvent(type, {
        bubbles: true, cancelable: true,
        touches: type === 'touchend' ? [] : [touchAt(x, y)],
        changedTouches: [touchAt(x, y)],
      }));
      const swipe = (dx, dy) => { fire('touchstart', 200, 300); fire('touchend', 200 + dx, 300 + (dy || 0)); };

      swipe(-150); await settle(350);
      r.labelAfterSwipeFwd = label();
      swipe(150); await settle(350);
      r.labelAfterSwipeBack = label();

      // A mostly-vertical drag is a scroll, not a page turn.
      r.labelBeforeVertical = label();
      swipe(20, 300); await settle(300);
      r.labelAfterVertical = label();

      scalShift(0); await settle(300);
      r.labelAfterJumpHome = label();

      scalSetView('week');
      await settle(500);
      const b2 = document.querySelector('#schedule-calendar .scal-time-body');
      r.openScrollTop = b2 ? Math.round(b2.scrollTop) : -1;

      r.liveBefore = document.querySelectorAll('#schedule-calendar .scal-block:not(.dead)').length;
      await scalToggle('sch_everyday', false);
      await settle(600);
      r.liveAfter = document.querySelectorAll('#schedule-calendar .scal-block:not(.dead)').length;
    } catch (e) {
      r.err = e.message;
    }
    return r;
  });

  await ctx.close();

  const fails = [];
  const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  if (out.err) fails.push('threw - ' + out.err);
  pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e))
    .forEach((e) => fails.push('uncaught: ' + e));
  // Sunday: only the two every-day rows. days:[1,3,5] must NOT land here — that
  // is the 1=Mon vs 0=Sun conversion.
  if (!eq(out.sun, ['sch_everyday', 'sch_off']))
    fails.push('Sunday wrong: ' + JSON.stringify(out.sun));
  if (!eq(out.mon, ['sch_cron', 'sch_everyday', 'sch_daily', 'sch_off']))
    fails.push('Monday wrong: ' + JSON.stringify(out.mon));
  if (out.tue.includes('sch_daily'))
    fails.push('Mon/Wed/Fri schedule leaked onto Tuesday');
  if (!out.monSorted) fails.push('day not time-ordered: ' + JSON.stringify(out.monTimes));
  if (!eq(out.alwaysIds, ['sch_interval'])) fails.push('always-strip wrong: ' + JSON.stringify(out.alwaysIds));
  if (!eq(out.unparsedIds, ['sch_weird'])) fails.push('unparsed-strip wrong: ' + JSON.stringify(out.unparsedIds));
  if (!out.cronUnion) fails.push('cron dom+dow union flags not set');
  if (out.cronBad !== null) fails.push('unparseable cron was not rejected - it would be guessed at');
  if (!out.blockCount) fails.push('calendar rendered no blocks');
  if (!out.deadCount) fails.push('the disabled schedule did not render as dead');
  if (out.hasAlwaysStrip)
    fails.push('always-running still rendered as a separate strip above the grid');
  if (!out.allDayBand) fails.push('no all-day band — always-running schedules vanished');
  if (out.allDayCells !== 7)
    fails.push('all-day band has ' + out.allDayCells + ' cells, expected one per day column');
  if (out.allDayChips !== 7)
    fails.push('the always-running schedule is not on every day (' + out.allDayChips + ' chips)');
  if (!out.allDayAboveHours) fails.push('the all-day band is not above the hour body');
  if (!out.hasUnparsedStrip) fails.push('unparsed strip missing (silently dropping schedules)');
  if (out.navEntries < 2) fails.push('Calendar missing from sidebar and/or mobile drawer (found ' + out.navEntries + ')');
  if (!out.ownModal) fails.push('Calendar did not open as its own modal');
  if (!out.hasTimeGrid) fails.push('no time grid rendered - it is a day-bucket list again');
  if (!(out.hourLabels || []).length) fails.push('no hour gutter labels');
  else if (!/^\d\d:00$/.test(out.hourLabels[0])) fails.push('hour labels malformed: ' + out.hourLabels[0]);
  if ((out.hours || []).length !== 24)
    fails.push('grid does not cover the full day (' + (out.hours || []).length + ' hour rows)');
  else if (out.hours[0] !== '00:00' || out.hours[23] !== '23:00')
    fails.push('day does not run 00:00-23:00 (' + out.hours[0] + ' .. ' + out.hours[23] + ')');
  if (!(out.openScrollTop > 0))
    fails.push('calendar opened at midnight instead of scrolling to the first run');
  if (out.labelOnToday === out.labelNext)
    fails.push('navigating a day did not change the range label (' + out.labelOnToday + ')');
  if (out.todayBtnOnToday !== 0)
    fails.push('the Today button is shown while already on today - it reads as a label');
  if (out.todayBtnAway !== 1)
    fails.push('no way back to today once navigated away');
  if (out.todayHighlightAway !== 0)
    fails.push('a non-today day is highlighted as today');
  if (out.labelAfterSwipeFwd === out.labelNext)
    fails.push('swiping left did not advance the calendar');
  if (out.labelAfterSwipeBack !== out.labelNext)
    fails.push('swiping right did not go back (' + out.labelAfterSwipeBack + ')');
  if (out.labelAfterVertical !== out.labelBeforeVertical)
    fails.push('a vertical drag paged the calendar - it should scroll instead');
  if (out.labelAfterJumpHome !== out.labelOnToday)
    fails.push('Today did not return to today');
  // Scheduler is live in this fixture, so there must be NO pause pill at all —
  // and never more than one when paused (the duplicate-banner regression).
  if (out.pausePills !== 0) fails.push('pause notice shown while the scheduler is live (' + out.pausePills + ')');
  if (out.topEarly === null || out.topLate === null)
    fails.push('could not locate the timed blocks to compare');
  else if (!(out.topEarly < out.topLate))
    fails.push('blocks are not positioned by time (07:00 at ' + out.topEarly + 'px, 21:00 at ' + out.topLate + 'px)');
  if (out.gridLeaksPrompt) fails.push('the full agent prompt is rendered on the grid - labels must be titles');
  if (out.detailBeforeOpen !== 0) fails.push('detail sheet was open before it was asked for');
  if (!out.detailOpened) fails.push('clicking a block did not open the detail sheet');
  if (!out.detailShowsPrompt) fails.push('the detail sheet does not show the full prompt');
  if (!out.detailClosed) fails.push('the detail sheet did not close');
  if (out.views.day !== 1) fails.push('Day view rendered ' + out.views.day + ' columns');
  if (out.views['3day'] !== 3) fails.push('3-Day view rendered ' + out.views['3day'] + ' columns');
  if (out.views.week !== 7) fails.push('Week view rendered ' + out.views.week + ' columns');
  if (out.monthHasTimeGrid) fails.push('Month view drew the time grid instead of day cells');
  // Whole weeks: a month always spans a multiple of 7 cells, 28..42.
  if (!(out.monthCells >= 28 && out.monthCells <= 42 && out.monthCells % 7 === 0))
    fails.push('Month grid is not whole weeks (' + out.monthCells + ' cells)');
  if (out.monthDupInCell) fails.push('a schedule appears more than once in one month cell');
  if (out.afterCellClick !== 1) fails.push('clicking a month cell did not drill into that day');
  if (!out.bodyScrolls) fails.push('the hour band does not scroll - later hours are unreachable');
  if (!out.scrolledTo) fails.push('scrolling the hour band had no effect');
  if (!out.gridWithinSurface) fails.push('the grid overflows its surface (bottom is clipped by the modal)');
  if (!(out.liveAfter < out.liveBefore))
    fails.push('toggling off from the grid did not strike its chips (' + out.liveBefore + ' -> ' + out.liveAfter + ')');

  if (fails.length) {
    console.error('CALFAIL calendar guard:');
    fails.forEach((f) => console.error('       - ' + f));
    return false;
  }
  console.log('OKAY calendar: time grid places runs by hour, titles on blocks + prompt in the sheet, one pause notice, toggle works.');
  return true;
}


let browser, allOk = false;
try {
  browser = await chromium.launch();
  const results = [];
  for (const sc of SCENARIOS) results.push(await runScenario(browser, sc));
  // Cross-module dispatch guard — runs after the boot scenarios so a boot
  // regression is reported on its own first.
  results.push(await runDispatchGuard(browser));
  results.push(await runModelPickerGuard(browser));
  results.push(await runBacklogRefreshGuard(browser));
  results.push(await runScheduleCalendarGuard(browser));
  allOk = results.every(Boolean);
  console.log(allOk
    ? `\n✅ PASS — ${SCENARIOS.length} boot scenarios + dispatch, model-picker, backlog & calendar guards all green.`
    : `\n❌ FAIL — ${results.filter((r) => !r).length}/${results.length} check(s) failed.`);
} catch (err) {
  console.error('❌ FAIL — smoke harness error:', err && err.stack ? err.stack : err);
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(allOk ? 0 : 1);
}
