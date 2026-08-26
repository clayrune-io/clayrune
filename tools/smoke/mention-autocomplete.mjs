#!/usr/bin/env node
/**
 * @mention autocomplete — headless behaviour test.
 *
 * WHY THIS EXISTS
 * ---------------
 * The picker exists so the roster is DISCOVERABLE and a name is never mistyped.
 * Both of those fail silently:
 *
 *   * A mention is plain text by design — "ask @Fenn to review this" routes
 *     because every agent's prompt carries the roster. So a picker that stops
 *     opening breaks nothing visible; the text still sends, and you simply
 *     stop being reminded that anyone but your VP exists.
 *   * `@Fnen` also sends fine. It degrades into prose the model reads past.
 *     Offering the real names IS the feature, so "the list is right" is the
 *     thing worth asserting.
 *   * Enter must be intercepted BEFORE the textarea's inline
 *     onkeydown="handleInputEnter(...)". If the capture-phase listener ever
 *     regresses to bubble, Enter sends the half-typed mention as a message.
 *
 * Mirrors slash-autocomplete.mjs, including its disk-backed static router —
 * a new module can never be silently missing from this harness.
 *
 * RUN:  cd tools/smoke && node mention-autocomplete.mjs
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

const CHARACTERS = [
  { name: 'code-reviewer', display_name: 'code-reviewer', scope: 'global',
    agent_name: 'Fenn', avatar: 'fig:scholar',
    description: 'Review a diff before it lands.' },
  { name: 'prd-writer', display_name: 'prd-writer', scope: 'global',
    agent_name: 'Marlow', avatar: 'fig:wizard', description: 'Writes PRDs.' },
  { name: 'ui-fixer', display_name: 'ui-fixer', scope: 'global',
    agent_name: 'Tilda', avatar: 'fig:dancer', description: 'UI defects.' },
  // Never named itself — must NOT be offered. The roster the agent reads calls
  // it by its type, so `@drifter` would teach a handle nobody uses.
  { name: 'drifter', display_name: 'drifter', scope: 'global',
    description: 'Unnamed type.' },
];

function router(route) {
  const path = new URL(route.request().url()).pathname;
  const json = (body) => route.fulfill({ status: 200, contentType: 'application/json', body });
  if (path === '/' || path === '/index.html')
    return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  if (path.startsWith('/static/js/') || path.startsWith('/static/css/')) {
    const dir = path.startsWith('/static/js/') ? 'js' : 'css';
    const type = dir === 'js' ? 'text/javascript; charset=utf-8' : 'text/css; charset=utf-8';
    const name = path.split('/').pop();
    try {
      return route.fulfill({ status: 200, contentType: type, body: read('static', dir, name) });
    } catch { return route.abort(); }
  }
  if (path === '/api/characters') return json(JSON.stringify(CHARACTERS));
  if (path === '/api/slash-commands') return json(JSON.stringify({ commands: [] }));
  if (path === '/api/config') return json('{}');
  if (path.startsWith('/api/avatars')) {
    // 1x1 transparent gif — the row only needs an <img> that resolves.
    return route.fulfill({ status: 200, contentType: 'image/gif',
      body: Buffer.from('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7', 'base64') });
  }
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
// page breaking — anything else is a real uncaught error.
page.on('pageerror', (e) => {
  const m = e.message || String(e);
  if (/dynamically imported module/.test(m) && /cdn\./.test(m)) return;
  pageErrors.push(m);
});
await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#projects-col .card', { timeout: 15000 });

await page.evaluate(async (pid) => {
  openProjectModal(pid);
  window.agentConvNew = window.agentConvNew || {};
  agentConvNew[pid] = true;
  if (typeof refreshModal === 'function') refreshModal();
  await new Promise(r => setTimeout(r, 600));
}, PID);

const ta = `#agent-task-${PID}`;
await page.waitForSelector(ta, { timeout: 10000 });

const popup = '.mention-ac';
const rows = '.mention-ac .slash-ac-row';
const names = '.mention-ac .slash-ac-name';

// 1. "@" opens with the whole roster — minus the type that never named itself.
await page.click(ta);
await page.type(ta, '@');
await page.waitForSelector(popup, { timeout: 5000 }).catch(() => {});
check('opens on "@"', await page.locator(popup).count() === 1);
check('offers only agents that named themselves',
  await page.locator(rows).count() === 3, `saw ${await page.locator(rows).count()}`);
check('shows a face beside each name',
  await page.locator('.mention-ac .mention-ac-face').count() === 3);

// 2. Typing filters on the chosen name.
await page.type(ta, 'fe');
await page.waitForTimeout(180);
check('filters as you type', (await page.locator(rows).count()) === 1
  && (await page.locator(names).first().innerText()).trim() === '@Fenn',
  await page.locator(names).first().innerText().catch(() => ''));

// 3. Enter completes instead of sending.
await page.keyboard.press('Enter');
await page.waitForTimeout(220);
check('Enter inserts the mention', (await page.inputValue(ta)) === '@Fenn ',
  JSON.stringify(await page.inputValue(ta)));
check('Enter did NOT dispatch the message', dispatched === false);
check('popup closes after accepting', (await page.locator(popup).count()) === 0);

// 4. It must work MID-SENTENCE — unlike the slash picker. A mention is a
//    reference inside a sentence you are already writing, which is the whole
//    point: "have @Fenn look at this".
await page.fill(ta, '');
await page.type(ta, 'please have @mar');
await page.waitForTimeout(220);
check('opens mid-sentence', (await page.locator(popup).count()) === 1);
await page.keyboard.press('Enter');
await page.waitForTimeout(200);
check('replaces ONLY the mention, keeping what came before',
  (await page.inputValue(ta)) === 'please have @Marlow ',
  JSON.stringify(await page.inputValue(ta)));

// 5. An e-mail address must not trigger it.
await page.fill(ta, '');
await page.type(ta, 'mail ron@clay');
await page.waitForTimeout(220);
check('does NOT open inside an e-mail address', (await page.locator(popup).count()) === 0);

// 6. Searching by ROLE finds the agent whose name says nothing about the job.
await page.fill(ta, '');
await page.type(ta, '@review');
await page.waitForTimeout(220);
check('finds an agent by its role, not just its name',
  (await page.locator(rows).count()) === 1
  && (await page.locator(names).first().innerText()).trim() === '@Fenn');

// 7. Escape dismisses without altering the text.
await page.fill(ta, '');
await page.type(ta, '@ti');
await page.waitForTimeout(200);
const hadPopup = (await page.locator(popup).count()) === 1;
await page.keyboard.press('Escape');
await page.waitForTimeout(150);
check('Escape dismisses', hadPopup && (await page.locator(popup).count()) === 0
  && (await page.inputValue(ta)) === '@ti');

// 8. Arrows move the highlight.
await page.fill(ta, '');
await page.type(ta, '@');
await page.waitForTimeout(220);
await page.keyboard.press('ArrowDown');
await page.waitForTimeout(140);
const active = await page.locator('.mention-ac .slash-ac-row.active .slash-ac-name')
  .innerText().catch(() => '');
check('ArrowDown moves the highlight', active.trim() === '@Marlow', active);

check('no uncaught exceptions', pageErrors.length === 0, pageErrors.join(' | '));

await browser.close();
if (failures.length) {
  console.error(`\n❌ FAIL — ${failures.length} behaviour(s) broken: ${failures.join(', ')}`);
  process.exit(1);
}
console.log('\n✅ PASS — @mention autocomplete behaves correctly.');
