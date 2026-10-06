#!/usr/bin/env node
/**
 * Project-window ⋮ menu must always fit and scroll inside the window it lives in.
 *
 * WHY THIS EXISTS
 * ----------------
 * df0ba75a capped `.modal-menu-dropdown` at `calc(100vh - 96px)` with
 * `overflow-y: auto`. That only works when the project modal is full screen.
 * A WINDOWED (restored) modal shorter than the viewport has
 * `.modal-content { overflow: hidden }`: the menu is taller than the window,
 * the window clips it, and the 100vh cap never bites — so the last rows
 * (Export, MCP, …) were cut off with nothing to scroll (Ron, 2026-10-06,
 * data/uploads/agent_5e0012a310.png).
 *
 * Fix: `_fitModalMenu` (modal-manager.js) measures the room between the menu's
 * top and its window's bottom when the menu opens, and on resize.
 *
 * For each geometry this asserts: the menu box ends inside its window, it is
 * scrollable (content taller than box), and after scrolling to the bottom the
 * LAST visible row sits fully inside the window. Also: an inline submenu
 * (Change Status) still opens inside the box, and the window being resized
 * shorter while the menu is open re-fits it.
 *
 * Hermetic: real index.html + real static/js + static/css, stub API. No server.
 *
 * RUN   node tools/smoke/modal-menu-scroll.mjs
 * Exit  0 = all checks pass; 1 = a regression.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_modal_menu_scroll';
const STATIC = loadStaticJsCss(REPO_ROOT);

const PROJECT = {
  id: PID, name: 'Menu Scroll Smoke', status: 'active', domain: 'general', emoji: '🧪',
  description: '', summary: '', current_task: 'Idle', next_action: '',
  blocked: false, blocked_reason: null, activity_log: [], backlog: [],
  project_path: '/smoke/' + PID, last_updated: '2026-10-06T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null,
  display_order: 0, provider: 'claude', use_streaming_agent: true,
  distiller_mode: 'proposed', distiller_min_recurrence: 3,
  distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
  distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
  distiller_skip_errors: true,
};
const PROJECTS_JSON = JSON.stringify([PROJECT]);
const ANDROID_UA = 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36';

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

async function route(page) {
  await page.route('**/*', (r) => {
    const path = new URL(r.request().url()).pathname;
    if (path === '/' || path === '/index.html') return r.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return r.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return r.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return r.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return r.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/floor') return r.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    return r.abort();
  });
}

// Geometry of the menu against its own window, and against the viewport.
function measure(pid) {
  const dd = document.getElementById('modal-menu-' + pid);
  const box = dd.closest('.modal-content');
  const b = box.getBoundingClientRect(), d = dd.getBoundingClientRect();
  const rows = [...dd.querySelectorAll('.modal-menu-item')].filter((e) => e.offsetParent !== null);
  const last = rows[rows.length - 1];
  const l = last.getBoundingClientRect();
  return {
    open: dd.classList.contains('open'),
    ddBottom: Math.round(d.bottom), winBottom: Math.round(b.bottom), vh: window.innerHeight,
    scrollable: dd.scrollHeight > dd.clientHeight + 1,
    scrollHeight: dd.scrollHeight, clientHeight: dd.clientHeight,
    lastText: last.textContent.trim().replace(/\s+/g, ' '),
    lastBottom: Math.round(l.bottom), lastTop: Math.round(l.top),
    overflowY: getComputedStyle(dd).overflowY,
  };
}

async function openMenu(page) {
  await page.evaluate((id) => openProjectModal(id), PID);
  await page.waitForSelector(`#modal-layer .modal-window[data-modal-id="${PID}"]`, { timeout: 5000 });
  await page.waitForTimeout(200);
}

async function check(page, label) {
  await page.click(`#modal-layer .modal-window[data-modal-id="${PID}"] .modal-menu-btn`);
  await page.waitForTimeout(150);
  const m = await page.evaluate(measure, PID);
  console.log(`  [${label}] ${JSON.stringify(m)}`);
  m.open ? ok(`${label}: menu opened`) : fail(`${label}: menu did not open`);
  m.ddBottom <= m.winBottom + 1 && m.ddBottom <= m.vh
    ? ok(`${label}: menu box ends inside its window (${m.ddBottom} <= ${m.winBottom}, vh ${m.vh})`)
    : fail(`${label}: menu box runs past its window (${m.ddBottom} vs window ${m.winBottom}, vh ${m.vh})`);
  m.scrollable
    ? ok(`${label}: menu is scrollable (${m.scrollHeight} > ${m.clientHeight})`)
    : fail(`${label}: menu is not scrollable (${m.scrollHeight} vs ${m.clientHeight}) — rows are clipped, not scrolled`);
  // Scroll the menu to the bottom and look where the last row landed.
  await page.evaluate((id) => { const dd = document.getElementById('modal-menu-' + id); dd.scrollTop = dd.scrollHeight; }, PID);
  await page.waitForTimeout(100);
  const after = await page.evaluate(measure, PID);
  after.lastBottom <= after.winBottom && after.lastBottom <= after.vh
    ? ok(`${label}: after scrolling, last row "${after.lastText}" is fully visible (bottom ${after.lastBottom} <= window ${after.winBottom})`)
    : fail(`${label}: last row "${after.lastText}" still clipped after scrolling (bottom ${after.lastBottom}, window ${after.winBottom}, vh ${after.vh})`);
  return after;
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();

  // 1. Windowed, window much shorter than the viewport (Ron's screenshot).
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    const errs = []; page.on('pageerror', (e) => errs.push(String(e)));
    await route(page);
    console.log('\n[windowed, 420px window in a 900px viewport]');
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#claydo-fab', { timeout: 15000 });
    await openMenu(page);
    await page.evaluate((id) => {
      const w = document.querySelector(`#modal-layer .modal-window[data-modal-id="${id}"]`);
      w.querySelector('.modal-content').style.height = '420px';
      w.style.top = '40px';
    }, PID);
    await page.waitForTimeout(100);
    await check(page, 'windowed');

    // Inline submenu still opens inside the same scroll box.
    const sub = await page.evaluate((id) => {
      const dd = document.getElementById('modal-menu-' + id);
      const btn = [...dd.querySelectorAll('.modal-menu-item')].find((e) => /Change Status/.test(e.textContent));
      if (!btn) return { err: 'no Change Status row' };
      btn.click();
      const s = document.getElementById('status-sub-' + id);
      const box = dd.closest('.modal-content').getBoundingClientRect();
      const r = s.getBoundingClientRect();
      return { open: s.classList.contains('open'), insideDd: dd.contains(s), subBottomVsWin: Math.round(r.bottom - box.bottom) };
    }, PID);
    sub.open && sub.insideDd ? ok(`Change Status submenu opens inline inside the menu box (${JSON.stringify(sub)})`) : fail(`submenu broke: ${JSON.stringify(sub)}`);
    const afterSub = await page.evaluate(measure, PID);
    afterSub.ddBottom <= afterSub.winBottom + 1
      ? ok('menu box still ends inside the window with the submenu open')
      : fail(`menu box grew past the window with the submenu open (${afterSub.ddBottom} vs ${afterSub.winBottom})`);

    // Shrink the window while the menu is open: menu must re-fit.
    await page.evaluate((id) => {
      document.querySelector(`#modal-layer .modal-window[data-modal-id="${id}"] .modal-content`).style.height = '300px';
    }, PID);
    await page.waitForTimeout(250);
    const shrunk = await page.evaluate(measure, PID);
    shrunk.ddBottom <= shrunk.winBottom + 1
      ? ok(`window shrunk to 300px with menu open: menu re-fit (bottom ${shrunk.ddBottom} <= ${shrunk.winBottom})`)
      : fail(`window shrunk but menu did not re-fit (bottom ${shrunk.ddBottom} vs window ${shrunk.winBottom})`);

    errs.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e)).forEach((e) => fail('uncaught page error: ' + e));
    await ctx.close();
  }

  // 2. Maximized on a short screen (the case df0ba75a covered).
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 480 } });
    const page = await ctx.newPage();
    const errs = []; page.on('pageerror', (e) => errs.push(String(e)));
    await route(page);
    console.log('\n[maximized, 480px viewport]');
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#claydo-fab', { timeout: 15000 });
    await openMenu(page);
    await page.evaluate((id) => toggleModalMaximize(id), PID);
    await page.waitForTimeout(300);
    await check(page, 'maximized');
    errs.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e)).forEach((e) => fail('uncaught page error: ' + e));
    await ctx.close();
  }

  // 3. Mobile sheet (≤960px) on a short phone.
  {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 520 }, hasTouch: true, userAgent: ANDROID_UA });
    const page = await ctx.newPage();
    const errs = []; page.on('pageerror', (e) => errs.push(String(e)));
    await route(page);
    console.log('\n[mobile, 390x520]');
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#bottom-tab-bar', { timeout: 15000 });
    await openMenu(page);
    await check(page, 'mobile');
    errs.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e)).forEach((e) => fail('uncaught page error: ' + e));
    await ctx.close();
  }

  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error(e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(exitCode);
