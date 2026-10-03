#!/usr/bin/env node
/**
 * Desk v1 — Studio Recent: delete a row, with Undo (Ron 2026-10-03: "on the studio
 * video view, there is no way to delete unneeded videos").
 * static/js/desk-v1-studio-delete.js + desk-v1-studio.js, mc/desk_studio_items.py.
 *
 * DEMO (fixtures only, hermetic):
 *   - every Recent row has a bin that is a SIBLING of the row's open button (no
 *     button inside a button), named `Delete <title> · <kind>`
 *   - the rendering row's bin is aria-disabled, tooltip `Rendering, wait for it to
 *     finish`; the row attached to a campaign says `Attached to <campaign>, detach
 *     it there first`; clicking either removes nothing
 *   - a free row: bin click removes it at once (no dialog), the header Undo names
 *     it and brings it back, Ctrl+Z does the same; the keyboard path works
 *   - demo mode never calls the server (no DELETE / POST to /api/desk)
 *   - phone (390): the bin is always visible, >= 44px, no horizontal overflow
 * LIVE (fake server):
 *   - a draft deletes through DELETE /api/desk/studio/<id> and Undo POSTs the token
 *   - a library file deletes through DELETE /api/desk/studio/files?path=
 *   - rows the usage read marks rendering / attached / used by a draft are disabled
 *
 * RUN   cd tools/smoke && node desk-v1-studio-delete.mjs
 */
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures, installDemoFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const SHOT_DIR = resolve(REPO_ROOT, 'docs', 'desk_v1', 'screens');
mkdirSync(SHOT_DIR, { recursive: true });
const ORIGIN = 'http://mc.smoke.test';
const STATIC = loadStaticJsCss(REPO_ROOT);

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg));

const PROJECT = (p) => ({
  id: p.id, name: p.name, status: 'active', domain: 'general', emoji: '🧪', description: '', summary: '', current_task: 'Idle',
  next_action: '', blocked: false, blocked_reason: null, activity_log: [], backlog: [], project_path: '/smoke/' + p.id,
  last_updated: '2026-09-09T00:00:00Z', last_updated_relative: 'today', last_completed: null, live_agent: null, display_order: 0,
  provider: 'claude', use_streaming_agent: true, distiller_mode: 'proposed', distiller_min_recurrence: 3,
  distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3, distiller_max_explorations_per_session: 3,
  distiller_min_turns: 5, distiller_skip_errors: true, roster: [],
});

// ── one page, demo or live ─────────────────────────────────────────────────
async function newPage(browser, { live, srv, viewport }) {
  const fx = loadFixtures();
  const ctx = await browser.newContext({ viewport: viewport || { width: 1440, height: 950 } });
  const page = await ctx.newPage();
  if (!live) await installDemoFixtures(page);
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  const calls = [];
  await page.route('**/*', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();
    const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return J(fx.projects.map(PROJECT));
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: !!live, user_timezone: '' });
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (!path.startsWith('/api/desk/')) return route.abort();
    calls.push({ method, path, query: url.search });
    if (!live || !srv) return route.abort();
    return srv.handle({ method, path, query: url.searchParams, J });
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors, calls, fx };
}

const realErrors = (e) => e.filter((m) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(m));
const rowIds = (page) => page.$$eval('[data-studio-recent-row]', (els) => els.map((e) => e.dataset.studioRecentRow));
const bin = (page, id) => page.$(`[data-studio-recent-item="${id}"] [data-studio-recent-del]`);
const toastCount = (page) => page.$$eval('.toast', (t) => t.length);
async function openStudio(page) {
  await page.evaluate(() => window.deskV1Nav('studio', {}));
  await page.waitForSelector('[data-studio]', { timeout: 6000 });
  await page.waitForSelector('[data-studio-recent-row]', { timeout: 6000 });
}
const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });

// ── DEMO ───────────────────────────────────────────────────────────────────
async function demo(browser) {
  console.log('demo mode');
  const b = await newPage(browser, { live: false });
  const { page, calls } = b;
  await openStudio(page);

  // Structure: the bin is a sibling of the open button, never inside it.
  const nested = await page.$$eval('[data-studio-recent-row] button, [data-studio-recent-row] [data-studio-recent-del]', (e) => e.length);
  const items = await page.$$eval('[data-studio-recent-item]', (els) => els.map((e) => ({
    id: e.dataset.studioRecentItem,
    kids: [...e.children].map((c) => c.tagName + (c.dataset.studioRecentRow !== undefined ? ':open' : '') + (c.dataset.studioRecentDel !== undefined ? ':del' : '')),
  })));
  check(nested === 0, 'no button is nested inside a row\'s open button', `${nested} button(s) nested in an open button`);
  check(items.length >= 3 && items.every((i) => i.kids.join() === 'BUTTON:open,BUTTON:del'),
    `all ${items.length} Recent rows are [open][delete] siblings`, 'row structure wrong: ' + JSON.stringify(items));
  const labels = await page.$$eval('[data-studio-recent-del]', (els) => els.map((e) => e.getAttribute('aria-label')));
  check(labels.every((l) => /^Delete .+ · (video|image|article)$/.test(l)), `every bin is named "Delete <title> · <kind>" (${labels[0]})`, 'bad labels ' + JSON.stringify(labels));

  // Rendering: disabled, tooltip, click removes nothing.
  const rendering = await page.$eval('[data-studio-recent-row][data-state="rendering"]', (e) => e.dataset.studioRecentRow);
  const rb = await bin(page, rendering);
  check((await rb.getAttribute('aria-disabled')) === 'true' && (await rb.getAttribute('title')) === 'Rendering, wait for it to finish',
    'the rendering row\'s bin is disabled: "Rendering, wait for it to finish"', `rendering bin wrong: ${await rb.getAttribute('title')}`);
  const before = await rowIds(page);
  await rb.click({ force: true });
  check((await rowIds(page)).join() === before.join(), 'clicking the rendering row\'s bin removes nothing', 'rendering row was removed');

  // Attached to a campaign: disabled, names the campaign.
  const attachedId = await page.$eval('[data-studio-recent-row][data-campaign-id]:not([data-state="rendering"])', (e) => e.dataset.studioRecentRow);
  const ab = await bin(page, attachedId);
  const atitle = await ab.getAttribute('title');
  check((await ab.getAttribute('aria-disabled')) === 'true' && /^Attached to .+, detach it there first$/.test(atitle),
    `an attached row's bin is disabled: "${atitle}"`, `attached bin wrong: ${atitle}`);
  await ab.click({ force: true });
  check((await rowIds(page)).join() === before.join(), 'clicking the attached row\'s bin removes nothing', 'attached row was removed');

  // A free row: hover shows the bin, click deletes at once, Undo brings it back.
  const free = 'rec-dashboard-hero';
  await page.hover(`[data-studio-recent-item="${free}"]`);
  await page.waitForTimeout(300);
  const op = await page.$eval(`[data-studio-recent-item="${free}"] [data-studio-recent-del]`, (e) => getComputedStyle(e).opacity);
  check(op === '1', 'the bin shows on hover', 'bin not visible on hover, opacity=' + op);
  await page.screenshot({ path: resolve(SHOT_DIR, 'studio_delete_1440.png') });
  const dialogs = [];
  page.on('dialog', (d) => { dialogs.push(d.message()); d.dismiss(); });
  await (await bin(page, free)).click();
  check(!(await rowIds(page)).includes(free) && (await rowIds(page)).length === before.length - 1, 'bin click removes the row at once', 'row still there');
  check(dialogs.length === 0 && (await page.$$('[role="dialog"], .modal-window[data-modal-id="__confirm"]')).length === 0, 'no confirm dialog', 'a confirm appeared');
  const utitle = await page.getAttribute('#desk-v1-undo', 'title');
  check(/Deleted .*Dashboard hero/.test(utitle), `the header Undo names the delete (${utitle})`, 'Undo title ' + utitle);
  check((await toastCount(page)) === 1, 'one Undo toast', `${await toastCount(page)} toast(s)`);
  await page.click('#desk-v1-undo');
  await settle(page, (id) => !!document.querySelector(`[data-studio-recent-row="${id}"]`), free);
  check((await rowIds(page)).join() === before.join(), 'Undo brings the row back, in its old place', 'Undo order wrong: ' + (await rowIds(page)).join());

  // Ctrl+Z does the same; keyboard: Tab from the open button lands on the bin, Enter deletes.
  await page.focus(`[data-studio-recent-item="${free}"] [data-studio-recent-row]`);
  await page.keyboard.press('Tab');
  const focusedDel = await page.evaluate(() => document.activeElement && document.activeElement.dataset.studioRecentDel);
  check(focusedDel === free, 'Tab from the open button lands on its bin', 'focus after Tab: ' + focusedDel);
  await page.keyboard.press('Enter');
  check(!(await rowIds(page)).includes(free), 'Enter on the bin deletes the row', 'Enter did not delete');
  const focusInList = await page.evaluate(() => !!(document.activeElement && document.activeElement.closest('[data-studio]')));
  check(focusInList, 'focus stays inside Studio after the delete', 'focus was lost');
  await page.evaluate(() => document.activeElement && document.activeElement.blur());
  await page.keyboard.press('Control+z');
  await settle(page, (id) => !!document.querySelector(`[data-studio-recent-row="${id}"]`), free);
  check(true, 'Ctrl+Z brings it back', '');

  // A thing made this session deletes and comes back too.
  await page.click('[data-studio-new="image"]');
  await page.waitForSelector('[data-studio-create][data-kind="image"] [data-sc-source]', { timeout: 6000 });
  const prod = (await page.$$eval('[data-sc-product] option', (els) => els.map((e) => e.value))).find((v) => v);
  if (prod) await page.selectOption('[data-sc-product]', prod);
  await page.click('[data-sc-source="capture"]');
  await page.waitForSelector('[data-cap]', { timeout: 4000 });
  await page.click('[data-cap-take]');
  await page.waitForSelector('[data-sc-item]', { timeout: 4000 });
  await openStudio(page);
  const withNew = await rowIds(page);
  const mine = withNew.find((id) => !before.includes(id));
  check(!!mine, 'a capture made in Studio is listed in Recent', 'no new row: ' + withNew.join());
  await (await bin(page, mine)).click();
  check(!(await rowIds(page)).includes(mine), 'a session-made item deletes', 'session item stayed');
  await page.click('#desk-v1-undo');
  await settle(page, (id) => !!document.querySelector(`[data-studio-recent-row="${id}"]`), mine);
  check((await rowIds(page)).join() === withNew.join(), 'and Undo restores it in place', 'order after undo: ' + (await rowIds(page)).join());

  const writes = calls.filter((c) => c.method !== 'GET');
  check(writes.length === 0, 'demo mode made no write to the server', 'demo wrote: ' + JSON.stringify(writes));
  check(realErrors(b.pageErrors).length === 0, 'no uncaught page errors', 'page errors: ' + b.pageErrors.join(' | '));
  await b.ctx.close();

  // Phone.
  const m = await newPage(browser, { live: false, viewport: { width: 390, height: 844 } });
  await openStudio(m.page);
  const dims = await m.page.$$eval('[data-studio-recent-del]', (els) => els.map((e) => { const r = e.getBoundingClientRect(); return { w: r.width, h: r.height, o: getComputedStyle(e).opacity }; }));
  check(dims.length >= 3 && dims.every((d) => d.o !== '0' && d.w >= 44 && d.h >= 44), `phone: every bin is visible and >= 44px (${dims.length} rows)`, 'phone bins: ' + JSON.stringify(dims));
  const over = await m.page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(over <= 0, 'phone: no horizontal overflow', 'overflow ' + over + 'px');
  await m.page.screenshot({ path: resolve(SHOT_DIR, 'studio_delete_390.png') });
  await (await bin(m.page, 'rec-dashboard-hero')).tap().catch(() => bin(m.page, 'rec-dashboard-hero').then((e) => e.click()));
  check(!(await rowIds(m.page)).includes('rec-dashboard-hero'), 'phone: the bin deletes the row', 'phone delete failed');
  await m.page.screenshot({ path: resolve(SHOT_DIR, 'studio_delete_undo_390.png') });
  await m.ctx.close();
}

// ── LIVE ───────────────────────────────────────────────────────────────────
function makeServer() {
  const srv = {
    log: [],
    boards: [{ id: 'draft-a', title: 'Launch teaser', scenes: 3, updated_at: '2026-10-01T10:00:00Z' },
             { id: 'draft-b', title: 'Rendering one', scenes: 2, updated_at: '2026-10-01T10:00:00Z' }],
    files: [{ id: 'desk/library/image/shot-free.png', kind: 'image', title: 'shot-free.png' },
            { id: 'desk/library/image/shot-attached.png', kind: 'image', title: 'shot-attached.png' },
            { id: 'desk/library/image/Storyboards/in-draft.png', kind: 'image', title: 'in-draft.png' }],
    trash: {},
  };
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  srv.handle = ({ method, path, query, J }) => {
    srv.log.push({ method, path, query: query.toString() });
    if (path === '/api/desk/workspace') return J({ ...workspaceFromFixtures(fx), projects, pieces: fx.families.map((f) => JSON.parse(JSON.stringify(f))) });
    if (path === '/api/desk/materials') return J({ library: { video: [], image: [] }, articles: [], online: { video: [], image: [] }, recent: srv.files });
    if (path === '/api/desk/studio/storyboards') return J({ storyboards: srv.boards });
    if (path === '/api/desk/studio/usage') return J({
      rendering: ['draft-b'],
      files: {
        'desk/library/image/shot-attached.png': { attached: { piece_id: 'p1', campaign_id: 'camp-1', campaign_title: 'Beta launch' }, draft: null, piece_board: false },
        'desk/library/image/Storyboards/in-draft.png': { attached: null, draft: { id: 'draft-a', title: 'Launch teaser' }, piece_board: false },
      },
    });
    let m = path.match(/^\/api\/desk\/studio\/([^/]+)$/);
    if (m && method === 'DELETE' && m[1] !== 'files') {
      const i = srv.boards.findIndex((b) => b.id === m[1]);
      if (i < 0) return J({ error: 'no such draft' }, 404);
      const token = 't-' + m[1];
      srv.trash[token] = { kind: 'draft', at: i, entry: srv.boards.splice(i, 1)[0] };
      return J({ ok: true, token });
    }
    if (path === '/api/desk/studio/files' && method === 'DELETE') {
      const p = query.get('path');
      const i = srv.files.findIndex((f) => f.id === p);
      if (i < 0) return J({ error: 'not in the library' }, 404);
      const token = 't-file-' + i;
      srv.trash[token] = { kind: 'file', at: i, entry: srv.files.splice(i, 1)[0] };
      return J({ ok: true, token });
    }
    m = path.match(/^\/api\/desk\/studio\/trash\/([^/]+)\/restore$/);
    if (m && method === 'POST') {
      const t = srv.trash[m[1]];
      if (!t) return J({ error: 'nothing to restore' }, 404);
      (t.kind === 'draft' ? srv.boards : srv.files).splice(t.at, 0, t.entry);
      delete srv.trash[m[1]];
      return J({ ok: true });
    }
    return J({ error: 'unhandled ' + method + ' ' + path }, 404);
  };
  return srv;
}

async function live(browser) {
  console.log('live mode (fake server)');
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openStudio(page);
  await settle(page, () => document.querySelectorAll('[data-studio-recent-del]').length >= 5);
  await page.waitForTimeout(150);

  const ids = await rowIds(page);
  const state = async (id) => { const b = await bin(page, id); return b ? { dis: await b.getAttribute('aria-disabled'), title: await b.getAttribute('title') } : null; };
  const rend = await state('draft-b');
  check(rend && rend.dis === 'true' && rend.title === 'Rendering, wait for it to finish', 'a draft the server says is rendering is disabled', 'rendering draft: ' + JSON.stringify(rend));
  const att = await state('desk/library/image/shot-attached.png');
  check(att && att.dis === 'true' && att.title === 'Attached to Beta launch, detach it there first', 'a file attached to a campaign is disabled and names it', 'attached file: ' + JSON.stringify(att));
  const used = await state('desk/library/image/Storyboards/in-draft.png');
  check(used && used.dis === 'true' && /Launch teaser/.test(used.title), 'a picture a draft still uses is disabled and names the draft', 'in-draft file: ' + JSON.stringify(used));
  const free = await state('draft-a');
  check(free && free.dis === null, 'a free draft is deletable', 'free draft: ' + JSON.stringify(free));

  // Draft: DELETE, row gone, Undo POSTs the token, row back.
  await (await bin(page, 'draft-a')).click();
  await settle(page, () => !document.querySelector('[data-studio-recent-row="draft-a"]'));
  check(srv.log.some((r) => r.method === 'DELETE' && r.path === '/api/desk/studio/draft-a'), 'draft delete: DELETE /api/desk/studio/draft-a', 'no DELETE for the draft: ' + JSON.stringify(srv.log.map((r) => r.method + r.path)));
  await page.click('#desk-v1-undo');
  await settle(page, () => !!document.querySelector('[data-studio-recent-row="draft-a"]'));
  check(srv.log.some((r) => r.method === 'POST' && r.path === '/api/desk/studio/trash/t-draft-a/restore'), 'Undo POSTs the restore token', 'no restore POST');
  check((await rowIds(page)).join() === ids.join(), 'and the draft is back in its place', 'order: ' + (await rowIds(page)).join());

  // File: DELETE with ?path=, Undo restores.
  const fp = 'desk/library/image/shot-free.png';
  await (await bin(page, fp)).click();
  await settle(page, (p) => !document.querySelector(`[data-studio-recent-row="${p}"]`), fp);
  check(srv.log.some((r) => r.method === 'DELETE' && r.path === '/api/desk/studio/files' && r.query === 'path=' + encodeURIComponent(fp)),
    'file delete: DELETE /api/desk/studio/files?path=…', 'no file DELETE: ' + JSON.stringify(srv.log.filter((r) => r.method === 'DELETE')));
  await page.click('#desk-v1-undo');
  await settle(page, (p) => !!document.querySelector(`[data-studio-recent-row="${p}"]`), fp);
  check(srv.log.some((r) => r.method === 'POST' && /\/restore$/.test(r.path) && /t-file-/.test(r.path)), 'Undo restores the file', 'no file restore POST');

  // A blocked bin never reaches the server.
  const dels = srv.log.filter((r) => r.method === 'DELETE').length;
  await (await bin(page, 'draft-b')).click({ force: true });
  await (await bin(page, 'desk/library/image/shot-attached.png')).click({ force: true });
  check(srv.log.filter((r) => r.method === 'DELETE').length === dels, 'blocked bins send no DELETE', 'a blocked row hit the server');
  check(realErrors(pageErrors).length === 0, 'no uncaught page errors', 'page errors: ' + pageErrors.join(' | '));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  await demo(browser);
  await live(browser);
} catch (e) {
  fail('harness error: ' + (e && e.stack ? e.stack : e));
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) FAILED` : '\nAll desk-v1-studio-delete checks passed');
process.exit(bad ? 1 : 0);
