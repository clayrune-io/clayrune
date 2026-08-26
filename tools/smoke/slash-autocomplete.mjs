#!/usr/bin/env node
/**
 * Slash-command autocomplete — headless behaviour test.
 *
 * WHY THIS EXISTS
 * ---------------
 * The picker's value depends on rules that are easy to get subtly wrong and
 * invisible when they break:
 *
 *   * It must open ONLY when the slash is the first bytes of the turn. The CLI
 *     won't parse a command anywhere else (MC encodes this server-side as
 *     _SLASH_COMMAND_RE), so a popup after "hello /" would offer a command that
 *     silently degrades to plain text — the exact failure this feature exists
 *     to prevent.
 *   * Enter must be intercepted BEFORE the textarea's inline
 *     onkeydown="handleInputEnter(...)". If the capture-phase listener ever
 *     regresses to bubble, Enter dispatches the half-typed command as a chat
 *     message instead of completing it.
 *
 * Hermetic: reuses boot-smoke's static map by importing nothing from the
 * server — the page, modules, and a canned /api/slash-commands are all
 * fulfilled via request interception.
 *
 * RUN:  cd tools/smoke && node slash-autocomplete.mjs
 * Exit 0 = all behaviours hold.
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

// Unlike boot-smoke's explicit STATIC_MAP, the router below reads
// /static/js/* and /static/css/* straight off disk by request path — so a new
// module can never be silently missing from this harness.
const COMMANDS = {
  commands: [
    { name: 'compact', description: 'Free up context by summarizing', argumentHint: '<instructions>' },
    { name: 'context', description: 'Show current context usage' },
    { name: 'goal', description: 'Set a goal Claude checks before stopping', argumentHint: '[<condition> | clear]' },
    { name: 'clear', description: 'Start a new session with empty context' },
  ],
  source: 'test',
};

function router(route) {
  const path = new URL(route.request().url()).pathname;
  const json = (body) => route.fulfill({ status: 200, contentType: 'application/json', body });
  if (path === '/' || path === '/index.html')
    return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  if (path.startsWith('/static/js/')) {
    const name = path.split('/').pop();
    try {
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8',
        body: read('static', 'js', name) });
    } catch { return route.abort(); }
  }
  if (path.startsWith('/static/css/')) {
    const name = path.split('/').pop();
    try {
      return route.fulfill({ status: 200, contentType: 'text/css; charset=utf-8',
        body: read('static', 'css', name) });
    } catch { return route.abort(); }
  }
  if (path === '/api/slash-commands') return json(JSON.stringify(COMMANDS));
  if (path === '/api/config') return json('{}');
  if (path === '/api/characters') return json('[]');
  if (path === '/api/projects') {
    const patched = JSON.parse(PROJECTS_JSON);
    if (patched[0]) patched[0].project_path = '/smoke/alpha';
    return json(JSON.stringify(patched));
  }
  if (/\/agent\/dispatch$/.test(path)) return json(JSON.stringify({ ok: true, session_id: 's1' }));
  return route.abort();
}

const failures = [];
function check(name, cond, detail) {
  if (cond) console.log(`✅ ${name}`);
  else { console.error(`❌ ${name}${detail ? ` — ${detail}` : ''}`); failures.push(name); }
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
const page = await ctx.newPage();
let dispatched = false;
page.on('request', r => { if (/\/agent\/dispatch$/.test(r.url())) dispatched = true; });
await page.route('**/*', router);
const pageErrors = [];
// The router ABORTS every external request by design, so the SPA's lazy
// mermaid import from the CDN rejects. That is the harness working, not the
// page breaking — counting it failed this suite on a clean tree.
page.on('pageerror', (e) => {
  const m = e.message || String(e);
  if (/dynamically imported module/.test(m) && /cdn\./.test(m)) return;
  pageErrors.push(m);
});
await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#projects-col .card', { timeout: 15000 });

// Open the +New composer.
await page.evaluate(async (pid) => {
  openProjectModal(pid);
  window.agentConvNew = window.agentConvNew || {};
  agentConvNew[pid] = true;
  if (typeof refreshModal === 'function') refreshModal();
  await new Promise(r => setTimeout(r, 600));
}, PID);

const ta = `#agent-task-${PID}`;
await page.waitForSelector(ta, { timeout: 10000 });

const popup = '.slash-ac';
const rows = '.slash-ac-row';

// 1. "/" at position 0 opens the picker with the full list.
await page.click(ta);
await page.type(ta, '/');
await page.waitForSelector(popup, { timeout: 5000 }).catch(() => {});
check('opens on a leading "/"', await page.locator(popup).count() === 1);
check('lists all commands', await page.locator(rows).count() === COMMANDS.commands.length,
  `saw ${await page.locator(rows).count()}`);

// 2. Typing filters.
await page.type(ta, 'go');
await page.waitForTimeout(150);
check('filters as you type', (await page.locator(rows).count()) === 1
  && (await page.locator('.slash-ac-name').first().innerText()).trim() === '/goal');

// 3. Enter completes instead of sending the message.
await page.keyboard.press('Enter');
await page.waitForTimeout(200);
const val = await page.inputValue(ta);
check('Enter inserts the command', val === '/goal ', JSON.stringify(val));
check('Enter did NOT dispatch the message', dispatched === false);
check('popup closes after accepting', (await page.locator(popup).count()) === 0);

// 4. A slash that is NOT the first bytes must not trigger — the CLI wouldn't
//    parse it there, so offering a command would be a lie.
await page.fill(ta, '');
await page.type(ta, 'hello /go');
await page.waitForTimeout(200);
check('does NOT open mid-sentence', (await page.locator(popup).count()) === 0);

// 5. A unix path must not trigger either.
await page.fill(ta, '');
await page.type(ta, '/usr/bin');
await page.waitForTimeout(200);
check('does NOT open on a unix path', (await page.locator(popup).count()) === 0);

// 6. Escape dismisses without altering the text.
await page.fill(ta, '');
await page.type(ta, '/co');
await page.waitForTimeout(200);
const hadPopup = (await page.locator(popup).count()) === 1;
await page.keyboard.press('Escape');
await page.waitForTimeout(150);
check('Escape dismisses', hadPopup && (await page.locator(popup).count()) === 0
  && (await page.inputValue(ta)) === '/co');

// 7. Arrow keys move the selection.
await page.fill(ta, '');
await page.type(ta, '/c');            // compact, context, clear
await page.waitForTimeout(200);
await page.keyboard.press('ArrowDown');
await page.waitForTimeout(120);
const activeName = await page.locator('.slash-ac-row.active .slash-ac-name').innerText().catch(() => '');
check('ArrowDown moves the highlight', activeName.trim() === '/context', activeName);

check('no uncaught exceptions', pageErrors.length === 0, pageErrors.join(' | '));

await browser.close();
if (failures.length) {
  console.error(`\n❌ FAIL — ${failures.length} behaviour(s) broken: ${failures.join(', ')}`);
  process.exit(1);
}
console.log('\n✅ PASS — slash autocomplete behaves correctly.');
