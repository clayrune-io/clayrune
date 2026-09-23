#!/usr/bin/env node
/**
 * New-conversation empty state on a short pane (Ron, 2026-09-22 screenshot).
 *
 * WHY THIS EXISTS
 * ----------------
 * On a short viewport the persona-card grid + heading + suggestion rows are
 * taller than `.agent-main`'s pane. `.agent-3pane .agent-main` is
 * `overflow: hidden` with no internal scroll wrapper — the composer's
 * autofocus (newAgentTab/dispatchAgent's `.focus()`, desktop-only) scrolled
 * that hidden-overflow ancestor to reveal itself, landing the top persona
 * rows above scrollTop 0 with no scrollbar to get back. Scrolling down
 * worked; scrolling back up past the clip did not — confirmed against the
 * unmodified app (verify-BEFORE-desktop-short.png, scrollTop landed at 787,
 * first card top === -564px).
 *
 * Fix: the empty-state content (thread header / resume banner / empty state)
 * now lives in its own `.compose-scroll > .compose-scroll-inner` region
 * (`static/js/conversation.js` dispatchRow, desktop branch), with the
 * composer/AGENT-MODEL-PERSONA bar pinned outside it in `.compose-bottom`.
 * `.agent-3pane .compose-scroll-inner { margin: auto 0 }` (static/css/app.css)
 * is the flexbox auto-margin centering trick: it centers the content when it
 * fits, and the auto margin collapses to 0 (top-anchored, scrollable) the
 * instant it overflows — never a negative, unscrollable overflow at the top.
 *
 * This proves, against the real static/js + static/css served verbatim:
 *   1. Desktop, 1280x700: with enough personas to overflow the pane, after
 *      focusing the composer (the actual autofocus trigger) the scroll
 *      region's scrollTop reaches 0 and the first persona card's top sits
 *      at/after the pane's own top — reachable, not clipped.
 *   2. Same viewport: scrolling down still reaches the composer, and the
 *      composer + AGENT/MODEL/PERSONA bar stay visible at the bottom the
 *      whole time (pinned, not pushed off-screen).
 *   3. Phone viewport (390x700): the equivalent mobile compose view still
 *      scrolls from the top with the composer pinned at the bottom — the fix
 *      must not have regressed the existing mobile compose-scroll behaviour.
 *
 * Hermetic: real index.html + real static/js/*.js + static/css/*.css served
 * verbatim (same shape as composer-persona-cards.mjs). No server, no network.
 *
 * RUN   node tools/smoke/new-conversation-short-viewport-scroll.mjs
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
const PID = 'smoke_short_viewport';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

function fixtureProject(id, name) {
  return {
    id, name, status: 'active', domain: 'general', emoji: '\u{1F9EA}',
    description: '', summary: '', current_task: 'Idle', next_action: '',
    blocked: false, blocked_reason: null, activity_log: [], backlog: [],
    project_path: '/smoke/' + id, last_updated: '2026-09-14T00:00:00Z',
    last_updated_relative: 'today', last_completed: null, live_agent: null,
    display_order: 0, provider: 'claude', use_streaming_agent: true,
    distiller_mode: 'proposed', distiller_min_recurrence: 3,
    distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
    distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
    distiller_skip_errors: true,
  };
}
const PROJECTS_JSON = JSON.stringify([fixtureProject(PID, 'Short Viewport Smoke')]);
// A full roster (18 personas) — enough to force the persona grid taller than
// a 700px-tall pane on desktop, which is what actually triggers the bug.
const NAMES = ['quill', 'halloway', 'kestrel', 'marlow', 'merrin', 'piper', 'posy',
  'rusk', 'tilda', 'tobin', 'vance', 'wren', 'bram', 'claydo', 'cutler', 'dave', 'fenn', 'vector'];
const CHARACTERS_JSON = JSON.stringify(NAMES.map((n) => ({
  name: n, scope: 'global', agent_name: n[0].toUpperCase() + n.slice(1),
  display_name: n + '-role', description: 'x', file: n + '.md', size: 10,
  avatar: '\u{1F9D9}', engine: { provider: 'claude' },
})));
const CONVERSATIONS_JSON = '[]';

async function installRoutes(page) {
  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: CHARACTERS_JSON });
    if (/\/conversations$/.test(path)) return route.fulfill({ status: 200, contentType: 'application/json', body: CONVERSATIONS_JSON });
    if (/\/agent-log$/.test(path)) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (/\/agent\/dispatch$/.test(path)) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, session_id: 'sid-1' }) });
    return route.abort();
  });
}

async function openFreshComposer(page) {
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(({ pid }) => {
    window.agentConvNew = window.agentConvNew || {};
    openProjectModal(pid);
    agentConvNew[pid] = true;
    if (typeof refreshModal === 'function') refreshModal();
  }, { pid: PID });
  await page.waitForSelector('.ces-people, .composer-empty-state', { timeout: 5000 });
}

// The real bug trigger: newAgentTab/dispatchAgent .focus() the task
// textarea on desktop, which is what scrolled the old overflow:hidden
// .agent-main out from under the top rows.
async function focusComposer(page) {
  await page.evaluate(({ pid }) => {
    const ta = document.getElementById('agent-task-' + pid);
    if (ta) ta.focus();
  }, { pid: PID });
  await page.waitForTimeout(200);
}

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

let browser, exitCode = 1;
try {
  browser = await chromium.launch();

  // ── 1. Desktop, short pane (1280x700): first card reachable at scrollTop 0 ──
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 700 } });
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
    await installRoutes(page);
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await openFreshComposer(page);
    await focusComposer(page);

    const before = await page.evaluate(() => {
      const scrollEl = document.querySelector('.compose-scroll');
      return { scrollTop: scrollEl ? scrollEl.scrollTop : null, scrollHeight: scrollEl ? scrollEl.scrollHeight : null, clientHeight: scrollEl ? scrollEl.clientHeight : null };
    });
    (before.scrollHeight > before.clientHeight)
      ? ok(`desktop 1280x700: fixture overflows the pane (scrollHeight=${before.scrollHeight} > clientHeight=${before.clientHeight}) — the bug's precondition`)
      : fail(`desktop 1280x700: fixture does NOT overflow (scrollHeight=${before.scrollHeight}, clientHeight=${before.clientHeight}) — test is not exercising the bug`);

    const reach = await page.evaluate(() => {
      const scrollEl = document.querySelector('.compose-scroll');
      const pane = document.querySelector('.agent-3pane .agent-main');
      const firstCard = document.querySelector('.ces-person-card');
      if (scrollEl) scrollEl.scrollTop = 0;
      return {
        scrollTopAfterZero: scrollEl ? scrollEl.scrollTop : null,
        cardBoxY: firstCard ? firstCard.getBoundingClientRect().top : null,
        paneTop: pane ? pane.getBoundingClientRect().top : null,
      };
    });
    (reach.scrollTopAfterZero === 0)
      ? ok('desktop 1280x700: scroll region accepts scrollTop=0 (no negative/stuck overflow)')
      : fail(`desktop 1280x700: scrollTop after setting 0 is ${reach.scrollTopAfterZero}, want 0`);
    (reach.cardBoxY !== null && reach.paneTop !== null && reach.cardBoxY >= reach.paneTop)
      ? ok(`desktop 1280x700: first persona card reachable — box.y=${reach.cardBoxY.toFixed(1)} >= pane top=${reach.paneTop.toFixed(1)}`)
      : fail(`desktop 1280x700: first persona card box.y=${reach.cardBoxY} is ABOVE the pane top=${reach.paneTop} — clipped and unreachable`);

    // Composer + AGENT/MODEL/PERSONA bar stay pinned at the bottom, in view.
    const pinned = await page.evaluate(() => {
      const composer = document.querySelector('.compose-bottom');
      const vh = window.innerHeight;
      if (!composer) return { present: false };
      const r = composer.getBoundingClientRect();
      return { present: true, top: r.top, bottom: r.bottom, vh };
    });
    (pinned.present && pinned.bottom <= pinned.vh + 1 && pinned.top >= 0)
      ? ok(`desktop 1280x700: composer/model/persona bar stays pinned inside the viewport (top=${pinned.top.toFixed(1)}, bottom=${pinned.bottom.toFixed(1)}, vh=${pinned.vh})`)
      : fail(`desktop 1280x700: composer bar not pinned in-view (${JSON.stringify(pinned)})`);

    // Scrolling down still works and reaches the composer's own scroll region end.
    const scrolledDown = await page.evaluate(() => {
      const scrollEl = document.querySelector('.compose-scroll');
      if (!scrollEl) return null;
      scrollEl.scrollTop = scrollEl.scrollHeight;
      return scrollEl.scrollTop;
    });
    (scrolledDown !== null && scrolledDown > 0)
      ? ok(`desktop 1280x700: scrolling down still works (scrollTop=${scrolledDown})`)
      : fail(`desktop 1280x700: scrolling down did not move scrollTop (got ${scrolledDown})`);

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    if (uncaught.length) uncaught.forEach((e) => fail('desktop 1280x700: uncaught exception — ' + e));

    await page.screenshot({ path: resolve(SHOT_DIR, 'new-conversation-short-viewport-desktop.png') });
    ok('desktop 1280x700 screenshot saved');
    await ctx.close();
  }

  // ── 2. Phone viewport (390x700): mobile compose-scroll still top-anchored, composer pinned ──
  {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 700 }, hasTouch: true });
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
    await installRoutes(page);
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await openFreshComposer(page);
    await focusComposer(page);

    const reach = await page.evaluate(() => {
      const scrollEl = document.querySelector('.mobile-compose-view .compose-scroll');
      const firstCard = document.querySelector('.ces-person-card');
      const pane = document.querySelector('.mobile-compose-view');
      if (scrollEl) scrollEl.scrollTop = 0;
      return {
        found: !!scrollEl,
        scrollTopAfterZero: scrollEl ? scrollEl.scrollTop : null,
        cardBoxY: firstCard ? firstCard.getBoundingClientRect().top : null,
        paneTop: pane ? pane.getBoundingClientRect().top : null,
      };
    });
    (reach.found) ? ok('phone 390x700: mobile compose-scroll region present')
      : fail('phone 390x700: .mobile-compose-view .compose-scroll not found');
    (reach.scrollTopAfterZero === 0)
      ? ok('phone 390x700: scroll region accepts scrollTop=0')
      : fail(`phone 390x700: scrollTop after setting 0 is ${reach.scrollTopAfterZero}, want 0`);
    (reach.cardBoxY !== null && reach.paneTop !== null && reach.cardBoxY >= reach.paneTop)
      ? ok(`phone 390x700: first persona card reachable — box.y=${reach.cardBoxY.toFixed(1)} >= pane top=${reach.paneTop.toFixed(1)}`)
      : fail(`phone 390x700: first persona card box.y=${reach.cardBoxY} above pane top=${reach.paneTop} — clipped`);

    const pinned = await page.evaluate(() => {
      const composer = document.querySelector('.mobile-compose-view .compose-bottom');
      const vh = window.innerHeight;
      if (!composer) return { present: false };
      const r = composer.getBoundingClientRect();
      return { present: true, top: r.top, bottom: r.bottom, vh };
    });
    (pinned.present && pinned.bottom <= pinned.vh + 1 && pinned.top >= 0)
      ? ok(`phone 390x700: composer stays pinned inside the viewport (bottom=${pinned.bottom.toFixed(1)}, vh=${pinned.vh})`)
      : fail(`phone 390x700: composer bar not pinned in-view (${JSON.stringify(pinned)})`);

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    if (uncaught.length) uncaught.forEach((e) => fail('phone 390x700: uncaught exception — ' + e));

    await page.screenshot({ path: resolve(SHOT_DIR, 'new-conversation-short-viewport-phone.png') });
    ok('phone 390x700 screenshot saved');
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
  ? '✅ PASS — new-conversation empty state scrolls from the top on a short pane (desktop + phone), composer stays pinned at the bottom.'
  : `❌ FAIL — ${bad} check(s) failed.`);
process.exit(exitCode);
