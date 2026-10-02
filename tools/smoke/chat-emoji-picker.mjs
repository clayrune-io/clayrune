#!/usr/bin/env node
/**
 * Chat emoji picker — headless behaviour test.
 *
 * The picker (composer-extras.js) is a popover beside the agent-chat input:
 *   * a cell must insert AT THE CARET (not append), replacing any selection,
 *     and leave the textarea focused — a click that steals focus loses the caret
 *     and the second pick lands at the end;
 *   * it stays open for several picks, closes on an outside click, on Esc, and
 *     on a second press of its own button;
 *   * at <=960px the button is hidden (a phone's keyboard already has emojis).
 *
 * Hermetic, same harness shape as slash-autocomplete.mjs.
 * RUN:  cd tools/smoke && node chat-emoji-picker.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_alpha';

const read = (...p) => readFileSync(resolve(REPO_ROOT, ...p), 'utf8');
const INDEX_HTML = read('static', 'index.html');
const PROJECTS_JSON = read('tools', 'smoke', 'fixtures', 'projects.json');

function router(route) {
  const path = new URL(route.request().url()).pathname;
  const json = (body) => route.fulfill({ status: 200, contentType: 'application/json', body });
  if (path === '/' || path === '/index.html')
    return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  if (path.startsWith('/static/js/')) {
    try {
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8',
        body: read('static', 'js', path.split('/').pop()) });
    } catch { return route.abort(); }
  }
  if (path.startsWith('/static/css/')) {
    try {
      return route.fulfill({ status: 200, contentType: 'text/css; charset=utf-8',
        body: read('static', 'css', path.split('/').pop()) });
    } catch { return route.abort(); }
  }
  if (path === '/api/config') return json('{}');
  if (path === '/api/characters') return json('[]');
  if (path === '/api/projects') {
    const patched = JSON.parse(PROJECTS_JSON);
    if (patched[0]) patched[0].project_path = '/smoke/alpha';
    return json(JSON.stringify(patched));
  }
  return route.abort();
}

const failures = [];
function check(name, cond, detail) {
  if (cond) console.log(`✅ ${name}`);
  else { console.error(`❌ ${name}${detail ? ` — ${detail}` : ''}`); failures.push(name); }
}

const browser = await chromium.launch();
const pageErrors = [];

async function openComposer(viewport) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  await page.route('**/*', router);
  page.on('pageerror', (e) => {
    const m = e.message || String(e);
    if (/dynamically imported module/.test(m) && /cdn\./.test(m)) return;
    pageErrors.push(m);
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(async (pid) => {
    openProjectModal(pid);
    window.agentConvNew = window.agentConvNew || {};
    agentConvNew[pid] = true;
    if (typeof refreshModal === 'function') refreshModal();
    await new Promise(r => setTimeout(r, 600));
  }, PID);
  await page.waitForSelector(`#agent-task-${PID}`, { timeout: 10000 });
  return page;
}

// ── Desktop ────────────────────────────────────────────────────────────────
{
  const page = await openComposer({ width: 1280, height: 800 });
  const ta = `#agent-task-${PID}`;
  const btn = `#btn-emoji-agent-task-${PID}`;
  const pop = '.chat-emoji-pop';
  const val = () => page.inputValue(ta);
  const focused = () => page.evaluate((s) => document.activeElement === document.querySelector(s), ta);

  check('desktop: emoji button is visible', await page.locator(btn).isVisible());
  check('tooltip names the OS picker', /Win\s*\+\s*\./.test(await page.getAttribute(btn, 'title') || ''));

  await page.click(ta);
  await page.type(ta, 'ab');
  await page.evaluate((s) => document.querySelector(s).setSelectionRange(1, 1), ta);
  await page.click(btn);
  check('click opens the picker', await page.locator(pop).count() === 1);
  const n = await page.locator('.cep-cell').count();
  check('has a modest curated set (60-100)', n >= 60 && n <= 100, `saw ${n}`);

  const first = await page.locator('.cep-cell').first().getAttribute('data-emoji');
  await page.locator('.cep-cell').first().click();
  check('inserts at the caret, not at the end', (await val()) === `a${first}b`, JSON.stringify(await val()));
  check('textarea keeps focus after a pick', await focused());
  check('picker stays open for another pick', await page.locator(pop).count() === 1);

  const second = await page.locator('.cep-cell').nth(1).getAttribute('data-emoji');
  await page.locator('.cep-cell').nth(1).click();
  check('second pick lands after the first (caret advanced)',
    (await val()) === `a${first}${second}b`, JSON.stringify(await val()));

  // A selection is replaced.
  await page.evaluate((s) => document.querySelector(s).setSelectionRange(0, 1), ta);
  await page.locator('.cep-cell').nth(2).click();
  const third = await page.locator('.cep-cell').nth(2).getAttribute('data-emoji');
  check('a selection is replaced by the pick', (await val()).startsWith(third), JSON.stringify(await val()));

  await page.mouse.click(5, 400);
  await page.waitForTimeout(100);
  check('outside click closes', await page.locator(pop).count() === 0);

  await page.click(ta);
  await page.click(btn);
  await page.keyboard.press('Escape');
  await page.waitForTimeout(100);
  check('Esc closes', await page.locator(pop).count() === 0);
  check('Esc returns focus to the textarea', await focused());

  await page.click(btn);
  await page.click(btn);
  check('second press of the button closes', await page.locator(pop).count() === 0);

  check('no uncaught exceptions (desktop)', pageErrors.length === 0, pageErrors.join(' | '));
  await page.context().close();
}

// ── Mobile (<=960px): the button is hidden ─────────────────────────────────
{
  const page = await openComposer({ width: 600, height: 900 });
  const btn = `#btn-emoji-agent-task-${PID}`;
  // Rendered (so this is not vacuous — the composer is up) but display:none.
  const rendered = await page.locator(btn).count();
  const display = rendered ? await page.locator(btn).evaluate(e => getComputedStyle(e).display) : '';
  check('mobile: emoji button is in the DOM but display:none at <=960px',
    rendered === 1 && display === 'none', `rendered=${rendered} display=${display}`);
  await page.context().close();
}

await browser.close();
if (failures.length) {
  console.error(`\n❌ FAIL — ${failures.length} behaviour(s) broken: ${failures.join(', ')}`);
  process.exit(1);
}
console.log('\n✅ PASS — chat emoji picker behaves correctly.');
