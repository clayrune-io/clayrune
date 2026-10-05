#!/usr/bin/env node
/**
 * Desk v1 — defects found by the 2026-10-04 live QA walk (docs/desk_v1/QA_LIVE_2026-10-04.md).
 *
 * Each case pins one defect that was visible on the real app at 1440x900 or 390x844 and that the
 * route-mocked smokes did not catch. They are CSS/layout cases: the markup is the shape the
 * renderer emits (same class names), dropped into the real shell, and the assertion is on the
 * computed geometry, so the real stylesheets are what is under test.
 *
 * RUN   cd tools/smoke && node desk-v1-qa-live.mjs
 * Exit 0 = every case holds; 1 = a case regressed / harness error.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { seedDeskV1Fixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const STATIC = loadStaticJsCss(REPO_ROOT);

const PROJECTS = [{
  id: 'smoke_deskv1', name: 'Desk v1 smoke', status: 'active', domain: 'general', emoji: '🧪',
  description: '', summary: '', current_task: 'Idle', next_action: '', blocked: false, blocked_reason: null,
  activity_log: [], backlog: [], project_path: '/smoke/smoke_deskv1', last_updated: '2026-09-09T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null, display_order: 0, provider: 'claude',
  use_streaming_agent: true, distiller_mode: 'proposed', distiller_min_recurrence: 3,
  distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
  distiller_max_explorations_per_session: 3, distiller_min_turns: 5, distiller_skip_errors: true, roster: [],
}];

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));

async function fulfillOrAbort(route) {
  const path = new URL(route.request().url()).pathname;
  const J = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  const hit = STATIC[path];
  if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
  if (path === '/api/projects') return J(PROJECTS);
  if (path === '/api/config') return J({ desk_v1: true, user_timezone: '' });
  if (path === '/api/characters') return J([]);
  return route.abort();
}

// Boot the real app, open the Desk, return the page with a scratch host inside the Desk shell.
async function boot(browser, vp) {
  const ctx = await browser.newContext(vp);
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', (e) => errs.push(e.message || String(e)));
  await page.route('**/*', fulfillOrAbort);
  await seedDeskV1Fixtures(page);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openDesk === 'function', null, { timeout: 15000 });
  await page.waitForTimeout(500);
  await page.evaluate(() => window.openDesk());
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  if (errs.length) fail(`uncaught page error while booting: ${errs[0]}`);
  return { ctx, page };
}

// Drop renderer-shaped markup into the Desk shell; the real stylesheets lay it out.
const mount = (page, html) => page.evaluate((h) => {
  document.querySelectorAll('[data-qa-scratch]').forEach((n) => n.remove());
  const host = document.createElement('div');
  host.setAttribute('data-qa-scratch', '1');
  host.innerHTML = h;
  document.querySelector('.desk-v1-shell').appendChild(host);
}, html);

const DESKTOP = { viewport: { width: 1440, height: 900 } };
const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };

const CASES = [];

// ── QA-1 ─────────────────────────────────────────────────────────────────────
// `.desk-v1-conn-add-field` (the label+input pair of every Add service form) had no rule at all, so
// the label and its input sat side by side as a short inline box: "Service name or web address" with
// a 190px input, the search box clipping its own placeholder. It must stack: label over a
// full-width input.
CASES.push({
  id: 'QA-1', name: 'Add service fields stack label over a full-width input', run: async (browser) => {
    for (const [tag, vp] of [['1440', DESKTOP], ['390', PHONE]]) {
      const { ctx, page } = await boot(browser, vp);
      await mount(page, `<form class="desk-v1-conn-add"><div class="desk-v1-cf-form">
        <label class="desk-v1-conn-add-field">Service name or web address <input type="text" class="desk-v1-rules-textinput" data-q="a"></label>
        <label class="desk-v1-conn-add-field">Find a service <input type="text" class="desk-v1-rules-textinput" data-q="b" placeholder="Search, or pick Something else"></label>
      </div></form>`);
      const g = await page.evaluate(() => {
        const r = (e) => e.getBoundingClientRect();
        const lab = document.querySelector('[data-q="a"]').parentElement, inp = document.querySelector('[data-q="a"]');
        const form = document.querySelector('[data-qa-scratch] .desk-v1-cf-form');
        return { labTop: r(lab).top, inpTop: r(inp).top, inpW: r(inp).width, formW: r(form).width, scrollX: document.documentElement.scrollWidth - innerWidth };
      });
      check(g.inpTop - g.labTop >= 12 && g.inpW >= g.formW * 0.9,
        `[${tag}] input fills the row (${Math.round(g.inpW)} of ${Math.round(g.formW)}px)`,
        `[${tag}] input is ${Math.round(g.inpW)}px in a ${Math.round(g.formW)}px row: label and input sit inline`);
      check(g.scrollX <= 0, `[${tag}] no horizontal scroll`, `[${tag}] horizontal overflow ${g.scrollX}px`);
      await ctx.close();
    }
  },
});

// ── QA-2 ─────────────────────────────────────────────────────────────────────
// Engagement's "Check now" was an unstyled <button>: the browser's grey default, 19px high, beside
// Desk buttons that are 28-44px with the Desk surface and border. It must read as a Desk button
// (own background and radius) and be a phone-sized target at 390.
CASES.push({
  id: 'QA-2', name: 'Engagement Check now is a styled Desk button', run: async (browser) => {
    for (const [tag, vp, minH] of [['1440', DESKTOP, 26], ['390', PHONE, 26]]) {
      const { ctx, page } = await boot(browser, vp);
      await mount(page, `<div class="desk-v1-eng-check"><button type="button" data-eng-check>Check now</button><span class="desk-v1-eng-check-line" data-eng-check-line></span></div>`);
      const g = await page.evaluate(() => {
        const b = document.querySelector('[data-qa-scratch] [data-eng-check]'); const cs = getComputedStyle(b);
        return { h: b.getBoundingClientRect().height, radius: parseFloat(cs.borderTopLeftRadius), bw: cs.borderTopStyle, font: cs.fontFamily };
      });
      check(g.radius >= 6 && g.h >= minH, `[${tag}] Check now is ${Math.round(g.h)}px high with a ${g.radius}px radius`, `[${tag}] Check now is the browser default: ${Math.round(g.h)}px high, radius ${g.radius}`);
      await ctx.close();
    }
  },
});

// ── QA-3 ─────────────────────────────────────────────────────────────────────
// A Studio Recent row whose title is one long unbroken name (an uploaded "Gemini_Generated_Image_..."
// file) ran past the row at 390: the name was cut off at the right edge and slid under the trash
// button. The text must wrap inside its column.
CASES.push({
  id: 'QA-3', name: 'Studio Recent row wraps a long unbroken file name', run: async (browser) => {
    for (const [tag, vp] of [['1440', DESKTOP], ['390', PHONE]]) {
      const { ctx, page } = await boot(browser, vp);
      const name = 'Gemini_Generated_Image_kcgfiwkcgfiwkcgf-4f2725c8.jpeg';
      await mount(page, `<div class="desk-v1-studio-recent"><div class="desk-v1-studio-recent-item">
        <button type="button" class="desk-v1-studio-recent-row"><span class="desk-v1-studio-recent-icon">🖼</span>
          <span class="desk-v1-studio-recent-main"><span class="desk-v1-studio-recent-title">${name} · image</span><span class="desk-v1-studio-recent-meta">Saved to the Material library · not attached</span></span></button>
        <button type="button" class="desk-v1-studio-recent-del" aria-label="Delete">🗑</button></div></div>`);
      const g = await page.evaluate(() => {
        const t = document.querySelector('[data-qa-scratch] .desk-v1-studio-recent-title'), del = document.querySelector('[data-qa-scratch] .desk-v1-studio-recent-del');
        const tr = t.getBoundingClientRect(), dr = del.getBoundingClientRect(), main = t.parentElement;
        return { titleRight: tr.right, delLeft: dr.left, overflow: main.scrollWidth - main.clientWidth };
      });
      check(g.overflow <= 1 && g.titleRight <= g.delLeft + 1, `[${tag}] the long name stays in its column (ends ${Math.round(g.titleRight)}, trash starts ${Math.round(g.delLeft)})`,
        `[${tag}] the long name runs ${Math.round(g.overflow)}px past its column and ends at ${Math.round(g.titleRight)}, under the trash button at ${Math.round(g.delLeft)}`);
      await ctx.close();
    }
  },
});

// ── runner ───────────────────────────────────────────────────────────────────
const only = process.argv[2];
const browser = await chromium.launch();
try {
  for (const c of CASES) {
    if (only && c.id !== only) continue;
    console.log(`${c.id}: ${c.name}`);
    try { await c.run(browser); } catch (e) { fail(`${c.id} harness error: ${e && e.message ? e.message : e}`); }
  }
} finally { await browser.close(); }
console.log(bad ? `\n${bad} check(s) failed` : '\nAll desk-v1-qa-live checks passed');
process.exit(bad ? 1 : 0);
