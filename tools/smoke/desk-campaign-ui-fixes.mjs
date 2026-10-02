#!/usr/bin/env node
/**
 * Desk campaign UI fixes (Ron, 2026-10-02, desktop + phone) — fixtures only,
 * run at 1440 and 390 wide. Five defects pinned:
 *
 *  1. The ⋯ menu on a Home draft row opened but was invisible: the row's ⋯
 *     wrapper had `transform: translateY(-50%)`, which made it the containing
 *     block of the position:fixed menu, so .desk-v1-home-block's
 *     overflow:hidden clipped it. Pin: the menu is inside the viewport AND the
 *     element at its centre / bottom edge is the menu (not the card behind it).
 *  2. ONE agent picker (Ron 2026-10-02): the Campaign card's Agent picker on
 *     Brief is the only place to choose; it shows each agent's FIGURE + name +
 *     role (never a `fig:` ref as text), keyboard + bottom sheet at 390. The
 *     right-hand box only SHOWS the agent (figure + name, not a control); with
 *     none it reads "No agent yet, pick one in Campaign" and links to the card.
 *  3. The "Tell your agent what to change" box was one line tall, so the
 *     wrapped placeholder was cut off. Pin: >= 2 lines tall and it grows.
 *  4. Only already-hired agents were offered, which read as broken. Pin:
 *     "+ Hire an agent onto <project>…" sits before "+ Create new agent", lists
 *     only agents NOT hired there, nothing is hired until the user clicks one,
 *     and the click POSTs the roster/hire route then selects it.
 *  5. Active status pill and Pause were at opposite ends of the header. Pin:
 *     one joined control (pill + action half) at the left, ⋯ at the right;
 *     Paused -> Resume; Proposed / Ended have no action half.
 *  6. A STARTED campaign with no project ('Clayrune promotion', started before
 *     a project was required) showed 'Project: none' read-only and 'Pick a
 *     project first' disabled, with no way out. Pin: How and Launch both show
 *     the project picker for it; picking one assigns it, enables the Agent
 *     select, and locks the field again; a started campaign that already has
 *     a project stays locked.
 *
 * RUN: cd tools/smoke && node desk-campaign-ui-fixes.mjs
 * Screenshots (gitignored): _scratch/desk-campaign-ui-fixes/<width>-*.png
 */
import { readFileSync, readdirSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { seedDeskV1Fixtures } from './desk-v1-fixture-api.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const ASSETS_DIR = resolve(REPO_ROOT, 'assets');
const SHOTS = resolve(REPO_ROOT, '_scratch', 'desk-campaign-ui-fixes');
mkdirSync(SHOTS, { recursive: true });
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';

const MIME = { '.webp': 'image/webp', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml' };
const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];
for (const f of readdirSync(ASSETS_DIR)) {
  const ext = f.slice(f.lastIndexOf('.'));
  if (MIME[ext]) STATIC[`/assets/${f}`] = [MIME[ext], readFileSync(resolve(ASSETS_DIR, f))];
}

const mkProject = (id, name, roster) => ({
  id, name, status: 'active', domain: 'general', emoji: '🧪',
  description: '', summary: '', current_task: 'Idle', next_action: '',
  blocked: false, blocked_reason: null, activity_log: [], backlog: [],
  project_path: '/smoke/' + id, last_updated: '2026-09-09T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null,
  display_order: 0, provider: 'claude', use_streaming_agent: true,
  distiller_mode: 'proposed', distiller_min_recurrence: 3,
  distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
  distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
  distiller_skip_errors: true, roster,
});
const PROJECTS = [mkProject('clayrune', 'Clayrune', []), mkProject('engulfing_scanner', 'Engulfing scanner', [])];
const CHARACTERS = [
  { scope: 'global', name: 'claydo', agent_name: 'Claydo', avatar: 'fig:newcomer', description: 'The mascot who builds new agents. Second sentence is cut.' },
  { scope: 'global', name: 'dave', agent_name: 'Dave', avatar: 'fig:smith', description: 'Program manager that keeps long work moving.' },
  { scope: 'global', name: 'not-hired', agent_name: 'Tilda Test', avatar: 'fig:courier', description: 'Posts and replies for the project.' },
];

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg));

async function boot(browser, width, height) {
  const ctx = await browser.newContext({ viewport: { width, height }, hasTouch: width < 600, isMobile: width < 600 });
  const page = await ctx.newPage();
  const pageErrors = [];
  const hires = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', async (route) => {
    const req = route.request();
    const u = new URL(req.url());
    const path = u.pathname;
    const J = (body, status) => route.fulfill({ status: status || 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return J(PROJECTS);
    if (path === '/api/config') return J({ desk_v1: true, user_timezone: '' });
    if (path === '/api/characters') return J(CHARACTERS);
    if (path.startsWith('/api/avatars/')) return route.fulfill({ status: 200, contentType: 'image/svg+xml', headers: { 'cache-control': 'max-age=86400' }, body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="14" fill="#c96"/></svg>' });
    if (path === '/api/floor') return J({ bench: [] });
    const m = path.match(/^\/api\/project\/([^/]+)\/roster\/hire$/);
    if (m && req.method() === 'POST') {
      const body = JSON.parse(req.postData() || '{}');
      hires.push({ project: m[1], ...body });
      // The server's roster write: the Desk's fixture project reads it back.
      await page.evaluate(([pid, ref]) => {
        const p = window.DeskV1Fixtures.projects.find((x) => x.id === pid);
        if (p && Array.isArray(p.roster) && !p.roster.includes(ref)) p.roster.push(ref);
      }, [m[1], body.character]);
      return J({ roster: [{ character: body.character, removed_at: null }], already_hired: false });
    }
    return route.abort();
  });
  await seedDeskV1Fixtures(page);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors, hires };
}

async function openCampaign(page, id, stop) {
  await page.evaluate((cid) => window.deskV1Nav('campaign', { campaignId: cid }), id);
  await page.waitForSelector('.desk-v1-campaign', { timeout: 8000 });
  if (stop) {
    await page.evaluate(([cid, s]) => window.deskV1GotoCampaignPanel(s, { campaignId: cid }), [id, stop]);
    await page.waitForSelector('.desk-v1-how', { timeout: 8000 });
  }
}

// The one Agent picker (Brief > Campaign card): open it, read its rows, pick one.
const openPicker = async (page) => {
  await page.click('[data-how-agent]');
  await page.waitForSelector('.desk-v1-agentlist [role="option"]', { timeout: 3000 });
  await listFiguresLoaded(page);
};
// A figure still in flight is an empty hole in a screenshot (and `complete` is
// false); wait for them so the shots and the figure assertions see what a user does.
const listFiguresLoaded = (page) => page.waitForFunction(() => Array.from(document.querySelectorAll('.desk-v1-agentlist img.av-fig')).every((i) => i.complete && i.naturalWidth > 0), null, { timeout: 3000 });
// Every non-action row (including the selected / current-agent one) holds a LOADED figure.
const rowsAllHaveFigure = (rows) => rows.filter((r) => !/^__/.test(r.id)).every((r) => r.figure && r.loaded);
const pickerRows = (page) => page.$$eval('.desk-v1-agentlist [role="option"]', (rs) => rs.map((r) => ({
  id: r.dataset.agentId, text: r.textContent.replace(/\s+/g, ' ').trim(), sel: r.getAttribute('aria-selected') === 'true',
  figure: !!r.querySelector('img.av-fig'), loaded: !!r.querySelector('img.av-fig') && r.querySelector('img.av-fig').complete && r.querySelector('img.av-fig').naturalWidth > 0, role: (r.querySelector('.desk-v1-agentlist-role') || {}).textContent || '',
})));
const noFigText = (page) => page.evaluate(() => !(/\bfig:[a-z]/.test(document.body.innerText) || Array.from(document.querySelectorAll('option, button, [role="option"]')).some((e) => /\bfig:[a-z]/.test(e.textContent))));

const rectOf = (page, sel) => page.$eval(sel, (el) => { const r = el.getBoundingClientRect(); return { l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height }; });

async function run(browser, width, height) {
  const tag = `[${width}]`;
  console.log(`\n── ${width}px ──`);
  const { ctx, page, pageErrors, hires } = await boot(browser, width, height);

  // ── 1. Draft row ⋯ menu is visible ──────────────────────────────────────
  await page.evaluate(() => {
    // The LAST row of its project block: the menu hangs below the block edge,
    // which is where the old overflow clip showed.
    const c = window.DeskV1Fixtures.campaigns.find((x) => x.id === 'camp-4');
    c.state = 'draft';
    window.deskV1Nav('home', {});
  });
  await page.waitForSelector('.desk-v1-home-row[data-state="draft"] [data-draft-more]', { timeout: 8000 });
  await page.click('.desk-v1-home-row[data-state="draft"] [data-draft-more]');
  await page.waitForSelector('.desk-v1-camp-cardmenu', { timeout: 3000 });
  const menu = await page.evaluate(() => {
    const m = document.querySelector('.desk-v1-camp-cardmenu');
    const r = m.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    const hit = (x, y) => { const e = document.elementFromPoint(x, y); return !!(e && (e === m || m.contains(e))); };
    return {
      inView: r.width > 0 && r.height > 0 && r.left >= 0 && r.top >= 0 && r.right <= vw && r.bottom <= vh,
      centre: hit((r.left + r.right) / 2, (r.top + r.bottom) / 2),
      bottomEdge: hit((r.left + r.right) / 2, r.bottom - 2),
      text: m.textContent.trim(),
      rect: [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)],
    };
  });
  check(menu.inView && menu.centre && menu.bottomEdge && /Delete draft/.test(menu.text),
    `${tag} 1 draft ⋯ menu is fully visible and on top (${menu.rect.join(',')})`,
    `${tag} 1 draft ⋯ menu clipped/hidden: ${JSON.stringify(menu)}`);
  await page.screenshot({ path: resolve(SHOTS, `${width}-1-draft-menu.png`) });
  await page.click('.desk-v1-home-row[data-state="draft"] [data-draft-more]'); // toggles the menu closed
  await page.evaluate(() => { window.DeskV1Fixtures.campaigns.find((x) => x.id === 'camp-4').state = 'active'; });

  // ── 3 + 2 + 4 on a campaign whose project has no agent (Engulfing) ──────
  await openCampaign(page, 'camp-3', 'how');
  const label = await page.$eval('.desk-v1-camp-posy [data-goto-campaign-agent]', (b) => b.textContent.trim());
  check(label === 'No agent yet, pick one in Campaign',
    `${tag} 2 unset agent reads "${label}" (a link, not a picker)`, `${tag} 2 label wrong: ${label}`);

  // 3: the Tell-your-agent box
  const ta0 = await page.$eval('.desk-v1-posy-input', (t) => { const cs = getComputedStyle(t); return { h: t.getBoundingClientRect().height, line: parseFloat(cs.lineHeight), padT: parseFloat(cs.paddingTop), padB: parseFloat(cs.paddingBottom), clipped: t.scrollHeight > t.clientHeight + 1, ph: t.placeholder }; });
  check(ta0.h >= ta0.line * 2 + ta0.padT + ta0.padB && !ta0.clipped,
    `${tag} 3 input is ${Math.round(ta0.h)}px tall (>= 2 lines + padding), placeholder "${ta0.ph}" not clipped`,
    `${tag} 3 input too short/clipped: ${JSON.stringify(ta0)}`);
  await page.fill('.desk-v1-posy-input', 'one\ntwo\nthree\nfour\nfive');
  await page.dispatchEvent('.desk-v1-posy-input', 'input');
  const ta1 = await page.$eval('.desk-v1-posy-input', (t) => t.getBoundingClientRect().height);
  check(ta1 > ta0.h + 30, `${tag} 3 input grows with text (${Math.round(ta0.h)}px -> ${Math.round(ta1)}px)`, `${tag} 3 input did not grow: ${ta0.h} -> ${ta1}`);
  await page.screenshot({ path: resolve(SHOTS, `${width}-3-input-grown.png`) });
  await page.fill('.desk-v1-posy-input', '');
  await page.dispatchEvent('.desk-v1-posy-input', 'input');
  const ta2 = await page.$eval('.desk-v1-posy-input', (t) => t.getBoundingClientRect().height);
  check(Math.abs(ta2 - ta0.h) < 2, `${tag} 3 input shrinks back when emptied`, `${tag} 3 input stayed tall: ${ta2} vs ${ta0.h}`);

  // 4: Agent picker rows (Engulfing has no hired agents)
  await page.waitForFunction(() => { const s = document.querySelector('[data-how-agent]'); return s && !s.disabled; }, null, { timeout: 4000 });
  check(await page.$eval('[data-how-agent]', (b) => b.tagName === 'BUTTON') && (await page.$$('select[data-how-agent]')).length === 0,
    `${tag} 4 the Agent control is a button + listbox, not a native <select>`, `${tag} 4 Agent control is still a <select>`);
  const hint = await page.$eval('[data-how-agent-hint]', (h) => h.textContent);
  check(/hired on Engulfing scanner/i.test(hint) || /No agents are hired on Engulfing scanner/i.test(hint), `${tag} 4 hint explains the list and how to add: "${hint}"`, `${tag} 4 hint wrong: ${hint}`);
  await openPicker(page);
  const rows0 = await pickerRows(page);
  const ids0 = rows0.map((r) => r.id);
  const hireIdx = ids0.indexOf('__hire__'), createIdx = ids0.indexOf('__create__');
  check(hireIdx >= 0 && createIdx === hireIdx + 1 && createIdx === ids0.length - 1,
    `${tag} 4 picker offers "+ Hire an agent onto Engulfing scanner…" right before "+ Create new agent" (last)`,
    `${tag} 4 picker rows wrong: ${JSON.stringify(ids0)}`);
  check(await noFigText(page), `${tag} 2 no raw "fig:" text on screen with the picker open`, `${tag} 2 raw fig: text is visible`);
  await page.keyboard.press('Escape');
  check((await page.$('.desk-v1-agentlist')) === null && (await page.evaluate(() => document.activeElement && document.activeElement.hasAttribute('data-how-agent'))),
    `${tag} 2 Esc closes the picker and returns focus to the Agent button`, `${tag} 2 Esc did not close/refocus`);

  // 2: the right-hand box SHOWS, never chooses. Nothing in it opens a picker.
  check(hires.length === 0, `${tag} 4 opening the picker has hired nothing`, `${tag} 4 hired before any click: ${JSON.stringify(hires)}`);
  await page.click('.desk-v1-camp-posy [data-goto-campaign-agent]');
  await page.waitForTimeout(150);
  check((await page.$('.desk-v1-agentlist')) === null && (await page.$('.desk-v1-addto-menu')) === null,
    `${tag} 2 clicking the box's "No agent yet" link opens no picker (it goes to the Campaign card)`, `${tag} 2 the box opened a picker`);
  check(await page.evaluate(() => document.activeElement && document.activeElement.hasAttribute('data-how-agent')),
    `${tag} 2 ...and puts focus on the Campaign card's Agent picker`, `${tag} 2 focus did not reach the Agent picker`);

  // Hire through the picker: the Hire row, then Dave (not hired on Engulfing).
  await openPicker(page);
  await page.click('.desk-v1-agentlist [data-agent-id="__hire__"]');
  await page.waitForFunction(() => /Dave/.test((document.querySelector('.desk-v1-agentlist') || {}).textContent || ''), null, { timeout: 4000 });
  await listFiguresLoaded(page);
  const cands = await pickerRows(page);
  check(cands.length === 3 && cands.some((c) => /Dave/.test(c.text)) && cands.some((c) => /Tilda Test/.test(c.text)) && cands.some((c) => /Claydo/.test(c.text)),
    `${tag} 4 hire list shows the installed agents not hired here: ${JSON.stringify(cands.map((c) => c.text))}`, `${tag} 4 hire list wrong: ${JSON.stringify(cands)}`);
  check(rowsAllHaveFigure(cands), `${tag} 2 every hire row renders its figure (<img>, loaded)`, `${tag} 2 a hire row has no figure: ${JSON.stringify(cands)}`);
  check(cands.every((c) => /\S/.test(c.role)) && cands.find((c) => /Claydo/.test(c.text)).role === 'The mascot who builds new agents.',
    `${tag} 2 each hire row carries a one-line role (first sentence only)`, `${tag} 2 roles wrong: ${JSON.stringify(cands.map((c) => c.role))}`);
  check(await noFigText(page), `${tag} 2 no raw "fig:" text in the Hire list`, `${tag} 2 raw fig: text in the Hire list`);
  await page.screenshot({ path: resolve(SHOTS, `${width}-2-hire-list.png`) });
  check(hires.length === 0, `${tag} 4 listing candidates still hired nothing`, `${tag} 4 hired on listing: ${JSON.stringify(hires)}`);
  await page.click('.desk-v1-agentlist [data-agent-id="global:dave"]');
  await page.waitForFunction(() => (document.querySelector('[data-how-agent]') || {}).dataset.value === 'global:dave', null, { timeout: 4000 });
  check(hires.length === 1 && hires[0].project === 'engulfing_scanner' && hires[0].character === 'global:dave' && hires[0].hired_by === 'desk',
    `${tag} 4 one click hired Dave via the roster/hire route: ${JSON.stringify(hires[0])}`, `${tag} 4 hire call wrong: ${JSON.stringify(hires)}`);
  const after = await page.evaluate(() => ({
    stored: window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-3').how.agent,
    boxName: document.querySelector('.desk-v1-camp-posy .desk-thread-name').textContent.trim(),
    boxTag: document.querySelector('.desk-v1-camp-posy .desk-thread-name').tagName,
    boxFigure: !!document.querySelector('.desk-v1-camp-posy .desk-thread-head img.av-fig'),
    boxControls: document.querySelectorAll('.desk-v1-camp-posy .desk-thread-head button:not([data-scope-trigger]), .desk-v1-camp-posy [data-pick-agent-btn], .desk-v1-camp-posy [data-goto-campaign-agent]').length,
    value: (document.querySelector('[data-how-agent]') || {}).dataset.value,
    btn: (document.querySelector('[data-how-agent]') || {}).textContent.replace(/\s+/g, ' ').trim(),
    btnFigure: !!document.querySelector('[data-how-agent] img.av-fig'),
    placeholder: document.querySelector('.desk-v1-posy-input').placeholder,
  }));
  check(after.stored === 'global:dave' && after.value === 'global:dave' && /^Dave/.test(after.btn) && after.btnFigure,
    `${tag} 3 after the hire the Agent picker shows the hired agent selected: "${after.btn}" with its figure`, `${tag} 3 picker not on the hired agent: ${JSON.stringify(after)}`);
  check(after.boxName === 'Dave' && after.boxTag === 'SPAN' && after.boxFigure && after.boxControls === 0 && /Dave/.test(after.placeholder),
    `${tag} 2 the right-hand box shows Dave's figure + name as plain text (no control)`, `${tag} 2 box state wrong: ${JSON.stringify(after)}`);
  await openPicker(page);
  const rows1 = await pickerRows(page);
  check(rows1.filter((r) => r.sel).length === 1 && rows1.find((r) => r.sel).id === 'global:dave' && rowsAllHaveFigure(rows1) && rows1.find((r) => r.sel).figure,
    `${tag} 2 reopened, exactly the current agent is marked and EVERY agent row, the selected one included, holds a loaded figure`, `${tag} 2 reopened picker wrong: ${JSON.stringify(rows1)}`);
  // A toast raised while the picker is open must not sit on top of it (at 390 the Desk
  // parks toasts at the bottom, where the sheet is: it lay across the Hire row).
  await page.evaluate(() => window.DeskV1Kit.toast('Hired Dave onto Engulfing scanner.'));
  await page.waitForSelector('#toast-container .toast', { timeout: 2000 });
  await page.waitForTimeout(450); // toastIn is 300ms
  const ov = await page.evaluate(() => {
    const r = (e) => { const b = e.getBoundingClientRect(); return { l: b.left, t: b.top, r: b.right, b: b.bottom }; };
    const sheet = r(document.querySelector('.desk-v1-agentlist'));
    const toasts = Array.from(document.querySelectorAll('#toast-container .toast')).map(r);
    const hit = (a, b) => a.l < b.r && a.r > b.l && a.t < b.b && a.b > b.t;
    return { sheet, toasts, overlap: toasts.some((t) => hit(t, sheet)), visible: toasts.length > 0 && toasts.every((t) => t.t >= 0 && t.b <= innerHeight && t.l >= 0 && t.r <= innerWidth) };
  });
  check(ov.visible && !ov.overlap, `${tag} 2 a toast raised with the picker open is fully on screen and clear of the list`, `${tag} 2 toast overlaps the picker: ${JSON.stringify(ov)}`);
  await page.screenshot({ path: resolve(SHOTS, `${width}-2-picker-open.png`) });
  // Keyboard: arrow to another agent, Enter picks it (writes how.agent), Undo is the store's.
  await page.keyboard.press('ArrowDown');
  const focused = await page.evaluate(() => document.activeElement && document.activeElement.dataset.agentId);
  check(focused && focused !== 'global:dave', `${tag} 2 ArrowDown moves the highlight (now ${focused})`, `${tag} 2 ArrowDown did nothing`);
  await page.keyboard.press('Escape');

  // 4 again on the select path (Clayrune: claydo+dave hired, Tilda Test is not)
  await openCampaign(page, 'camp-1', 'how');
  await page.waitForFunction(() => { const s = document.querySelector('[data-how-agent]'); return s && !s.disabled; }, null, { timeout: 4000 });
  await openPicker(page);
  await page.click('.desk-v1-agentlist [data-agent-id="__hire__"]');
  await page.waitForFunction(() => /Tilda Test/.test((document.querySelector('.desk-v1-agentlist') || {}).textContent || ''), null, { timeout: 3000 });
  const c2 = await pickerRows(page);
  check(c2.length === 1 && /Tilda Test/.test(c2[0].text), `${tag} 4 Clayrune: the Hire list shows only the unhired agent: ${JSON.stringify(c2.map((c) => c.text))}`, `${tag} 4 hire list wrong: ${JSON.stringify(c2)}`);
  await page.screenshot({ path: resolve(SHOTS, `${width}-4-hire-menu.png`) });
  const before = hires.length;
  await page.click('.desk-v1-agentlist [role="option"]');
  await page.waitForFunction(() => (document.querySelector('[data-how-agent]') || {}).dataset.value === 'global:not-hired', null, { timeout: 4000 });
  check(hires.length === before + 1 && hires[before].character === 'global:not-hired' && hires[before].project === 'clayrune',
    `${tag} 4 select path hired and selected the agent for the campaign`, `${tag} 4 select path wrong: ${JSON.stringify(hires)}`);

  // ── 3. Blank state: a chosen agent the roster does not list yet still shows ──
  // (the hire's roster row can land after the pick; the old select went blank).
  await page.evaluate(() => {
    const c = window.DeskV1Fixtures.campaigns.find((x) => x.id === 'camp-1');
    const p = window.DeskV1Fixtures.projects.find((x) => x.id === c.projectId);
    p.roster = p.roster.filter((r) => r !== 'global:not-hired');
    c.how.agent = 'global:not-hired';
    window.deskV1Render();
  });
  await page.waitForFunction(() => { const s = document.querySelector('[data-how-agent]'); return s && !s.disabled; }, null, { timeout: 4000 });
  const blank = await page.$eval('[data-how-agent]', (b) => ({ v: b.dataset.value, t: b.textContent.replace(/\s+/g, ' ').trim(), fig: !!b.querySelector('img.av-fig') }));
  check(blank.v === 'global:not-hired' && /^Tilda Test/.test(blank.t) && blank.fig,
    `${tag} 3 a campaign agent missing from the roster still shows on the picker ("${blank.t}"), never blank`, `${tag} 3 picker blank/wrong: ${JSON.stringify(blank)}`);
  await openPicker(page);
  const rowsB = await pickerRows(page);
  check(rowsB.some((r) => r.id === 'global:not-hired' && r.sel && r.figure && r.loaded), `${tag} 3 ...and it is listed, marked, and holds its figure in the open picker`, `${tag} 3 current agent missing from the list: ${JSON.stringify(rowsB)}`);
  await page.keyboard.press('Escape');
  await page.evaluate(() => { window.DeskV1Fixtures.campaigns.find((x) => x.id === 'camp-1').how.agent = null; });

  // ── 5. One joined status control ────────────────────────────────────────
  await openCampaign(page, 'camp-1');
  await page.waitForSelector('[data-status-control] [data-pause-btn]', { timeout: 4000 });
  const geo = async () => page.evaluate(() => {
    const g = (s) => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect(); return { l: r.left, t: r.top, r: r.right, b: r.bottom, h: r.height }; };
    const top = document.querySelector('.desk-v1-camp-summary-top').getBoundingClientRect();
    return { pill: g('[data-status-control] .desk-v1-camp-state-pill'), act: g('[data-status-control] .desk-v1-camp-status-action'), ctl: g('[data-status-control]'), more: g('.desk-v1-camp-summary-top [data-camp-more-btn]'), top: { l: top.left, r: top.right }, pauseCount: document.querySelectorAll('[data-pause-btn]').length, word: (document.querySelector('[data-status-control] .desk-v1-state-word') || {}).textContent };
  });
  let g = await geo();
  const joined = (x) => x.pill && x.act && Math.abs(x.act.l - x.pill.r) <= 1 && Math.abs(x.act.t - x.pill.t) <= 1 && Math.abs(x.act.b - x.pill.b) <= 1;
  check(joined(g) && g.ctl.l - g.top.l < 4 && g.more && g.more.l > g.ctl.r && g.top.r - g.more.r < 24 && g.pauseCount === 1,
    `${tag} 5 Active: pill + Pause joined at the left (pill ${Math.round(g.pill.l)}-${Math.round(g.pill.r)}, Pause ${Math.round(g.act.l)}-${Math.round(g.act.r)}), ⋯ stays at the right edge (${Math.round(g.more.r)} of ${Math.round(g.top.r)})`,
    `${tag} 5 Active layout wrong: ${JSON.stringify(g)}`);
  await page.screenshot({ path: resolve(SHOTS, `${width}-5-active.png`) });
  const clicks0 = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-1').state);
  await page.click('[data-pause-btn]'); // ONE click, no sheet
  await page.waitForSelector('[data-status-control] [data-resume-btn]', { timeout: 4000 });
  g = await geo();
  const sheet = await page.$('[data-sheet-confirm]');
  check(clicks0 === 'active' && !sheet && joined(g) && g.word === 'Paused' && g.pauseCount === 0,
    `${tag} 5 Pause is one click (no sheet); control is now [Paused | Resume], still joined`, `${tag} 5 Paused control wrong: ${JSON.stringify({ clicks0, sheet: !!sheet, g })}`);
  await page.screenshot({ path: resolve(SHOTS, `${width}-5-paused.png`) });
  // Resume keeps its gate: it opens the sheet, it does not resume by itself.
  await page.click('[data-resume-btn]');
  await page.waitForSelector('[data-sheet-confirm], .desk-v1-sheet, [role="dialog"]', { timeout: 4000 }).then(
    () => ok(`${tag} 5 Resume still opens its confirm sheet (gate unchanged)`),
    () => fail(`${tag} 5 Resume no longer opens its sheet`));
  for (const [id, state] of [['camp-2', 'proposed'], ['camp-archived-1', 'archived']]) {
    await page.evaluate(() => { const cl = document.querySelector('[data-sheet-cancel]'); if (cl) cl.click(); });
    await openCampaign(page, id);
    const n = await page.evaluate(() => ({ act: document.querySelectorAll('[data-status-control] .desk-v1-camp-status-action, [data-pause-btn], [data-resume-btn]').length, anyPill: !!document.querySelector('.desk-v1-state-label') }));
    check(n.act === 0 && n.anyPill, `${tag} 5 ${state}: plain status, no action half`, `${tag} 5 ${state} has an action: ${JSON.stringify(n)}`);
  }

  // ── 6. Started campaign with no project can be given one ────────────────
  // Item 5's Resume sheet is still up; close it so these shots are clean.
  await page.evaluate(() => { [...document.querySelectorAll('button')].filter((b) => b.textContent.trim() === 'Cancel').forEach((b) => b.click()); });
  const probe = () => page.evaluate(() => ({
    pick: !!document.querySelector('[data-setup-project]'),
    ro: !!document.querySelector('[data-how-project-ro], [data-launch-project-ro]'),
    roText: ((document.querySelector('[data-how-project-ro], [data-launch-project-ro]') || {}).textContent || '').trim(),
    agentDisabled: (document.querySelector('[data-how-agent]') || {}).disabled,
    hint: ((document.querySelector('[data-setup-project-hint]') || {}).textContent || '').trim(),
  }));
  for (const panel of ['how', 'launch']) {
    await page.evaluate(() => {
      const c = window.DeskV1Fixtures.campaigns.find((x) => x.id === 'camp-4');
      c.state = 'active'; c.projectId = null; if (c.how) c.how.agent = null;
    });
    await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-4' }));
    await page.waitForSelector('.desk-v1-campaign', { timeout: 8000 });
    await page.evaluate(([cid, s]) => window.deskV1GotoCampaignPanel(s, { campaignId: cid }), ['camp-4', panel]);
    await page.waitForSelector(panel === 'how' ? '.desk-v1-how' : '.desk-v1-launch', { timeout: 8000 });
    const before6 = await probe();
    check(before6.pick && !before6.ro && /started without a project/.test(before6.hint) && (panel !== 'how' || before6.agentDisabled),
      `${tag} 6 ${panel}: started campaign with no project shows the project picker (hint: "${before6.hint.slice(0, 40)}…")`,
      `${tag} 6 ${panel}: picker missing on a started, project-less campaign: ${JSON.stringify(before6)}`);
    await page.screenshot({ path: resolve(SHOTS, `${width}-6-${panel}-noproject.png`) });
    await page.selectOption('[data-setup-project]', 'clayrune');
    await page.waitForSelector('[data-how-project-ro], [data-launch-project-ro]', { timeout: 4000 });
    const after6 = await probe();
    const stored6 = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((x) => x.id === 'camp-4').projectId);
    check(stored6 === 'clayrune' && !after6.pick && /Project: Clayrune/.test(after6.roText),
      `${tag} 6 ${panel}: picking Clayrune assigned it and the field locked ("${after6.roText}")`,
      `${tag} 6 ${panel}: assign/lock wrong: ${JSON.stringify({ stored6, after6 })}`);
    if (panel === 'how') {
      await page.waitForFunction(() => { const s = document.querySelector('[data-how-agent]'); return s && !s.disabled; }, null, { timeout: 4000 });
      ok(`${tag} 6 how: the Agent picker now works off the chosen project`);
    }
  }
  // A started campaign that HAS a project stays locked (no regression).
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.waitForSelector('.desk-v1-campaign', { timeout: 8000 });
  await page.evaluate(() => window.deskV1GotoCampaignPanel('how', { campaignId: 'camp-1' }));
  await page.waitForSelector('.desk-v1-how', { timeout: 8000 });
  const locked = await probe();
  check(!locked.pick && locked.ro, `${tag} 6 a started campaign that has a project stays locked`, `${tag} 6 started+project not locked: ${JSON.stringify(locked)}`);

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail(`${tag} uncaught page error: ${e}`));
  await ctx.close();
}

(async () => {
  const browser = await chromium.launch();
  try {
    await run(browser, 1440, 950);
    await run(browser, 390, 844);
  } catch (e) {
    fail('harness error: ' + (e && e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(bad ? `\nFAILED: ${bad}` : '\nALL PASS');
  process.exit(bad ? 1 : 0);
})();
