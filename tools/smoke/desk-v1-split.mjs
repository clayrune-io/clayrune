#!/usr/bin/env node
/**
 * Desk split (Ron 2026-10-15): the vertical edge between a screen's form and its
 * agent chat column is draggable (static/js/desk-v1-split.js). A fake server stands
 * in for /api/desk/*; what is under test is the browser's layout and storage.
 *
 *   1. Mount     -> campaign + story each get a focusable role=separator with aria values, sitting
 *                   mid-gap, and neither layout moves (chat stays at its stylesheet default).
 *   2. Drag      -> mouse drag left widens the chat by the distance, the form shrinks by it; the
 *                   share is saved per screen type (campaign and story are separate keys).
 *   3. Persist   -> a reload brings the same width back.
 *   4. Clamp     -> dragged far either way, the chat stops at 260px and the form at 380px; the
 *                   composer and Send stay inside the chat column.
 *   5. Keyboard  -> Left/Right step 24px (Shift 96px), Home/End go to the limits, aria-valuenow follows.
 *   6. Reset     -> double click removes the inline width and the saved value.
 *   7. Touch/pen -> synthetic touch and pen pointer drags move it too.
 *   8. Re-clamp  -> a saved width wider than a narrower window allows is pulled back in; widening
 *                   the window does not strand it.
 *   9. Phone     -> at 390 the separator is hidden, no inline width is left, columns stay stacked,
 *                   no sideways scroll; returning to desktop brings the saved width back.
 *  10. Hidden    -> a stop with no chat column (Where) hides the separator.
 *  11. Pop-out   -> the chat's pop-out still opens and closes with Esc.
 *  12. Shots     -> 1440 wide: default, chat widened, campaign widened, into _scratch/desk_split/.
 *
 * RUN   cd tools/smoke && node desk-v1-split.mjs
 */
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const SHOTS = resolve(REPO_ROOT, '_scratch', 'desk_split');
const ORIGIN = 'http://mc.smoke.test';
const STATIC = loadStaticJsCss(REPO_ROOT);

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const near = (a, b, tol = 2) => Math.abs(a - b) <= tol;
const check = (cond, pass, failMsg) => (cond ? ok(pass) : fail(failMsg));

function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: p.id === 'clayrune' ? 'global:claydo' : null, state: 'active' } }));
  const campaigns = fx.campaigns.map((c) => JSON.parse(JSON.stringify(c)));
  const srv = { fx, projects, campaigns };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects, campaigns: campaigns.map((c) => JSON.parse(JSON.stringify(c))) });
  return srv;
}

async function boot(page) {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.waitForFunction(() => window.DeskV1Store.state().campaigns.length > 0, null, { timeout: 8000 });
}

async function newPage(browser, srv, viewport) {
  const ctx = await browser.newContext({ viewport, hasTouch: true });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return J(srv.fx.projects.map((p) => ({
      id: p.id, name: p.name, status: 'active', domain: 'general', emoji: '🧪', description: '', summary: '', current_task: 'Idle',
      next_action: '', blocked: false, blocked_reason: null, activity_log: [], backlog: [], project_path: '/smoke/' + p.id,
      last_updated: '2026-09-09T00:00:00Z', last_updated_relative: 'today', last_completed: null, live_agent: null, display_order: 0,
      provider: 'claude', use_streaming_agent: true, distiller_mode: 'proposed', distiller_min_recurrence: 3,
      distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3, distiller_max_experiments_per_session: 3,
      distiller_min_turns: 5, distiller_skip_errors: true, roster: [],
    })));
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true, user_timezone: '' });
    if (path === '/api/characters') return J([{ name: 'claydo', scope: 'global', agent_name: 'Claydo', avatar: '' }]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (!path.startsWith('/api/desk/')) return route.abort();
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path.endsWith('/chat')) return J({ thread: [] });
    if (path === '/api/desk/materials') return J({ library: { video: [], image: [] }, articles: [], online: { video: [], image: [] }, recent: [] });
    if (path === '/api/desk/studio/storyboards') return J({ storyboards: [] });
    return J({});
  });
  await boot(page);
  return { ctx, page, pageErrors };
}

const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });
const realErrors = (errs) => errs.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));

const openCampaign = async (page, id, panel = 'how') => {
  await page.evaluate((cid) => window.deskV1Nav('campaign', { campaignId: cid }), id);
  await settle(page, () => !!document.querySelector('.desk-v1-campaign'));
  await page.evaluate(([cid, p]) => window.deskV1GotoCampaignPanel(p, { campaignId: cid }), [id, panel]);
  await settle(page, () => !!document.querySelector('[data-camp-chat]'));
  await settle(page, () => !!document.querySelector('.desk-v1-camp-main [data-split-sep]'));
};
const openStory = async (page) => {
  await page.evaluate(() => window.deskV1Nav('studio', {}));
  await page.waitForSelector('[data-studio-new="video"]', { timeout: 8000 });
  await page.evaluate(() => window.deskV1Nav('studio-create', { kind: 'video' }));
  await page.waitForSelector('[data-studio-create][data-kind="video"] [data-storyboard]', { timeout: 8000 });
  await settle(page, () => !!document.querySelector('.desk-v1-sb-layout [data-split-sep]'));
};

const pl = (S) => ({ ...S, open: undefined });   // page.evaluate cannot carry the function
// A screen under test: where its columns and separator are.
const CAMP = { name: 'campaign', key: 'campaign', sep: '.desk-v1-camp-main [data-split-sep]', side: '#desk-v1-camp-rightcol', main: '#desk-v1-camp-tabbody', host: '.desk-v1-camp-main', def: 320, minMain: 380, open: openCampaign };
const STORY = { name: 'story', key: 'story', sep: '.desk-v1-sb-layout [data-split-sep]', side: '.desk-v1-sb-agent', main: '.desk-v1-sb-main', host: '.desk-v1-sb-layout', def: 320, minMain: 340, open: openStory };

const geo = (page, S) => page.evaluate((s) => {
  const r = (q) => { const e = document.querySelector(q); return e ? e.getBoundingClientRect() : null; };
  const sep = document.querySelector(s.sep);
  const sr = sep.getBoundingClientRect();
  const side = r(s.side), main = r(s.main), host = r(s.host);
  const sideEl = document.querySelector(s.side);
  const hostEl = document.querySelector(s.host);
  return {
    side: side.width, main: main.width, host: host.width, sepX: sr.left + sr.width / 2, sepW: sr.width, sepHidden: getComputedStyle(sep).display === 'none',
    gapMid: (main.right + side.left) / 2, sideLeft: side.left, mainTop: main.top, sideTop: side.top,
    inline: s.name === 'story' ? hostEl.style.gridTemplateColumns : sideEl.style.flex,
    now: +sep.getAttribute('aria-valuenow'), min: +sep.getAttribute('aria-valuemin'), max: +sep.getAttribute('aria-valuemax'),
    stored: localStorage.getItem('clayrune.desk.split.' + s.key),
    hOver: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  };
}, pl(S));

const dragBy = async (page, S, dx, { steps = 8 } = {}) => {
  const g = await geo(page, S);
  const y = await page.$eval(S.sep, (e) => { const b = e.getBoundingClientRect(); return b.top + Math.min(b.height / 2, 200); });
  await page.mouse.move(g.sepX, y);
  await page.mouse.down();
  await page.mouse.move(g.sepX + dx, y, { steps });
  await page.mouse.up();
};

async function reload(page) {
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.waitForFunction(() => window.DeskV1Store.state().campaigns.length > 0, null, { timeout: 8000 });
}

async function exercise(browser, srv, S) {
  console.log(`\n[${S.name}]`);
  const { ctx, page, pageErrors } = await newPage(browser, srv, { width: 1440, height: 950 });
  await page.evaluate(() => localStorage.clear());
  await S.open(page, 'camp-1');

  // 1. Mount
  const attrs = await page.$eval(S.sep, (e) => ({ role: e.getAttribute('role'), tab: e.tabIndex, ori: e.getAttribute('aria-orientation'), label: e.getAttribute('aria-label'), cursor: getComputedStyle(e).cursor, ta: getComputedStyle(e).touchAction }));
  check(attrs.role === 'separator' && attrs.tab === 0 && attrs.ori === 'vertical' && attrs.label, 'a focusable vertical role=separator with a label', 'separator attrs: ' + JSON.stringify(attrs));
  check(attrs.cursor === 'col-resize' && attrs.ta === 'none', 'col-resize cursor and touch-action none', 'cursor/touch: ' + JSON.stringify(attrs));
  let g = await geo(page, S);
  check(near(g.side, S.def), `the chat column is at its stylesheet default (${g.side}px)`, `default width ${g.side}`);
  check(near(g.sepX, g.gapMid), `the separator sits mid-gap (${g.sepX.toFixed(1)} vs ${g.gapMid.toFixed(1)})`, `sep ${g.sepX} gapMid ${g.gapMid}`);
  check(g.stored === null && g.inline === '', 'nothing saved and no inline width until the user drags', `stored ${g.stored} inline ${g.inline}`);
  check(g.now >= g.min && g.now <= g.max, `aria-valuenow ${g.now} is inside ${g.min}..${g.max}`, 'aria range ' + JSON.stringify(g));

  // hover grip is visible
  await page.hover(S.sep);
  const gripOp = await page.$eval(S.sep + ' .desk-v1-split-grip', (e) => getComputedStyle(e).opacity);
  await page.waitForTimeout(250);
  const gripOp2 = await page.$eval(S.sep + ' .desk-v1-split-grip', (e) => getComputedStyle(e).opacity);
  check(+gripOp2 === 1, `the grip shows on hover (opacity ${gripOp} -> ${gripOp2})`, 'grip opacity on hover ' + gripOp2);

  // 2. Drag left 150px: chat +150, form -150
  const base = g;
  await dragBy(page, S, -150);
  g = await geo(page, S);
  check(near(g.side, base.side + 150, 3) && near(g.main, base.main - 150, 3), `drag left 150: chat ${base.side} -> ${g.side}, form ${base.main} -> ${g.main}`, `after drag chat ${g.side} form ${g.main}`);
  check(g.stored && near(+g.stored, g.side / g.host, 0.01), `the share is saved (${g.stored})`, 'stored ' + g.stored);
  check(near(g.sepX, g.gapMid), 'the separator follows the gap', `sep ${g.sepX} gapMid ${g.gapMid}`);
  const savedSide = g.side;

  // 3. Persist across a reload
  await reload(page);
  await S.open(page, 'camp-1');
  g = await geo(page, S);
  check(near(g.side, savedSide, 2), `reload: the chat is ${g.side}px again (saved ${savedSide})`, `after reload ${g.side}, expected ${savedSide}`);

  // 4. Clamp both ways
  await dragBy(page, S, -3000);
  g = await geo(page, S);
  check(g.main >= S.minMain - 1, `dragged far left, the form stops at ${g.main}px (floor ${S.minMain})`, `form ${g.main} below ${S.minMain}`);
  const ui = await page.evaluate((s) => {
    const side = document.querySelector(s.side).getBoundingClientRect();
    const send = document.querySelector(s.side + ' [data-posy-send], ' + s.side + ' .btn-dispatch');
    const ask = document.querySelector(s.side + ' [data-sb-ask]');
    const inside = (e) => { if (!e) return null; const b = e.getBoundingClientRect(); return b.width > 0 && b.left >= side.left - 1 && b.right <= side.right + 1; };
    return { send: inside(send), ask: inside(ask) };
  }, pl(S));
  check(ui.ask === true && (ui.send === true || ui.send === null), 'at the widest chat the composer and Send are inside the column', 'composer: ' + JSON.stringify(ui));
  await dragBy(page, S, 3000);
  g = await geo(page, S);
  check(near(g.side, 260, 1), `dragged far right, the chat stops at ${g.side}px (floor 260)`, `chat ${g.side}`);
  const ui2 = await page.evaluate((s) => {
    const side = document.querySelector(s.side).getBoundingClientRect();
    const send = document.querySelector(s.side + ' [data-posy-send], ' + s.side + ' .btn-dispatch');
    const ask = document.querySelector(s.side + ' [data-sb-ask]');
    const inside = (e) => { if (!e) return null; const b = e.getBoundingClientRect(); return b.width > 0 && b.left >= side.left - 1 && b.right <= side.right + 1; };
    return { send: inside(send), ask: inside(ask), askW: ask.getBoundingClientRect().width };
  }, pl(S));
  check(ui2.ask === true && (ui2.send === true || ui2.send === null) && ui2.askW > 100, 'at the narrowest chat the composer and Send are still inside the column', 'composer: ' + JSON.stringify(ui2));

  // 5. Keyboard
  await page.focus(S.sep);
  let before = (await geo(page, S));
  await page.keyboard.press('ArrowLeft');
  g = await geo(page, S);
  check(near(g.side, before.side + 24, 1) && g.now < before.now, `ArrowLeft widens the chat by 24 (${before.side} -> ${g.side}), aria-valuenow ${before.now} -> ${g.now}`, `ArrowLeft: ${before.side} -> ${g.side}, now ${before.now} -> ${g.now}`);
  await page.keyboard.press('ArrowRight');
  g = await geo(page, S);
  check(near(g.side, before.side, 1), 'ArrowRight steps back', `ArrowRight: ${g.side}`);
  await page.keyboard.press('Shift+ArrowLeft');
  g = await geo(page, S);
  check(near(g.side, before.side + 96, 1), 'Shift+Arrow steps 96', `Shift: ${g.side}`);
  await page.keyboard.press('Home');
  g = await geo(page, S);
  check(g.main >= S.minMain - 1 && near(g.main, S.minMain, 4) || g.main >= S.minMain - 1, `Home: the form at its smallest (${g.main}px), valuenow ${g.now} = valuemin ${g.min}`, `Home: form ${g.main}`);
  check(g.now === g.min, 'Home sets aria-valuenow to aria-valuemin', `now ${g.now} min ${g.min}`);
  await page.keyboard.press('End');
  g = await geo(page, S);
  check(near(g.side, 260, 1) && g.now === g.max, `End: the chat at its smallest (${g.side}px), valuenow = valuemax ${g.max}`, `End: side ${g.side} now ${g.now} max ${g.max}`);
  const focusKept = await page.evaluate((s) => document.activeElement === document.querySelector(s.sep), pl(S));
  check(focusKept, 'the separator keeps focus while it is moved', 'focus lost');

  // 6. Reset
  await dragBy(page, S, -120);
  g = await geo(page, S);
  check(!!g.stored && g.inline !== '', 'a drag sets the inline width and saves it', `stored ${g.stored} inline ${g.inline}`);
  await page.dblclick(S.sep);
  g = await geo(page, S);
  check(near(g.side, S.def) && g.stored === null && g.inline === '', `double click: back to ${g.side}px, inline width and saved value removed`, `after dblclick: ${JSON.stringify(g)}`);

  // 7. Touch and pen (synthetic pointer events on the separator)
  for (const pointerType of ['touch', 'pen']) {
    const b0 = await geo(page, S);
    await page.$eval(S.sep, (e, [type, x0, dx]) => {
      const y = e.getBoundingClientRect().top + 60;
      const fire = (name, x) => e.dispatchEvent(new PointerEvent(name, { pointerId: 77, pointerType: type, isPrimary: true, bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, buttons: name === 'pointerup' ? 0 : 1 }));
      fire('pointerdown', x0); fire('pointermove', x0 + dx / 2); fire('pointermove', x0 + dx); fire('pointerup', x0 + dx);
    }, [pointerType, b0.sepX, -100]);
    const b1 = await geo(page, S);
    check(near(b1.side, b0.side + 100, 3), `${pointerType} drag left 100: chat ${b0.side} -> ${b1.side}`, `${pointerType} drag: ${b0.side} -> ${b1.side}`);
    await page.dblclick(S.sep);
  }

  // 8. Re-clamp when the window changes. Save a wide chat, then shrink the window.
  await dragBy(page, S, -300);
  g = await geo(page, S);
  const wide = g.side;
  await page.setViewportSize({ width: 1100, height: 950 });
  await settle(page, () => true);
  await page.waitForTimeout(250);
  g = await geo(page, S);
  check(g.main >= S.minMain - 1 && g.side >= 260 - 1, `1100 wide: form ${g.main}px (>= ${S.minMain}), chat ${g.side}px (>= 260)`, `at 1100: form ${g.main} chat ${g.side}`);
  await page.setViewportSize({ width: 1440, height: 950 });
  await page.waitForTimeout(250);
  g = await geo(page, S);
  check(near(g.side, wide, 3), `back at 1440 the saved width returns (${g.side}px, saved ${wide})`, `back at 1440: ${g.side}, expected ${wide}`);

  // 9. Phone
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  const ph = await geo(page, S).catch(() => null);
  check(ph && ph.sepHidden, 'phone width: the separator is not shown', 'phone: separator visible ' + JSON.stringify(ph));
  check(ph && ph.inline === '', 'phone width: no inline width is left on the columns', `phone inline: ${ph && ph.inline}`);
  check(ph && ph.sideTop > ph.mainTop && near(ph.sideLeft, await page.$eval(S.main, (e) => e.getBoundingClientRect().left), 2), 'phone width: the columns still stack', 'phone: columns not stacked ' + JSON.stringify(ph));
  check(ph && ph.hOver <= 0, 'phone width: no sideways scroll', 'phone hOver ' + (ph && ph.hOver));
  await page.setViewportSize({ width: 1440, height: 950 });
  await page.waitForTimeout(300);
  g = await geo(page, S);
  check(near(g.side, wide, 3) && !g.sepHidden, `back to desktop: the saved width is back (${g.side}px)`, `back from phone: ${g.side}, expected ${wide}`);

  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function hiddenAndPopout(browser, srv) {
  console.log('\n[hidden + pop-out + shots]');
  const { ctx, page, pageErrors } = await newPage(browser, srv, { width: 1440, height: 950 });
  await page.evaluate(() => localStorage.clear());
  await openCampaign(page, 'camp-1', 'how');

  // 10. Where hides the chat column, so the separator goes with it
  await page.evaluate(() => window.deskV1GotoCampaignPanel('where', { campaignId: 'camp-1' }));
  await settle(page, () => document.querySelector('#desk-v1-camp-tabbody').dataset.panel === 'where');
  await page.waitForTimeout(250);
  const where = await page.evaluate(() => ({ side: getComputedStyle(document.querySelector('#desk-v1-camp-rightcol')).display, sep: getComputedStyle(document.querySelector('.desk-v1-camp-main [data-split-sep]')).display }));
  check(where.side === 'none' && where.sep === 'none', 'Where: no chat column, no separator', 'where: ' + JSON.stringify(where));
  await page.evaluate(() => window.deskV1GotoCampaignPanel('how', { campaignId: 'camp-1' }));
  await settle(page, () => document.querySelector('#desk-v1-camp-tabbody').dataset.panel === 'how');
  await page.waitForTimeout(250);
  const back = await page.evaluate(() => getComputedStyle(document.querySelector('.desk-v1-camp-main [data-split-sep]')).display);
  check(back !== 'none', 'back on Brief the separator is shown again', 'separator stayed hidden');

  // 11. Pop-out untouched by a saved width
  await dragBy(page, CAMP, -120);
  await page.click('[data-camp-chat] [data-chat-pop]');
  await settle(page, () => document.querySelector('[data-camp-chat]').classList.contains('is-popped'));
  const popped = await page.evaluate(() => { const r = document.querySelector('[data-camp-chat]').getBoundingClientRect(); return { w: r.width, vw: innerWidth }; });
  check(popped.w > 500, `pop-out still opens large (${Math.round(popped.w)}px wide)`, 'pop-out width ' + popped.w);
  await page.keyboard.press('Escape');
  await settle(page, () => !document.querySelector('[data-camp-chat]').classList.contains('is-popped'));
  ok('Esc closes the pop-out');
  await page.dblclick(CAMP.sep);

  // 12. Shots at 1440
  mkdirSync(SHOTS, { recursive: true });
  const shot = async (name) => {
    await page.waitForTimeout(250);
    await page.screenshot({ path: resolve(SHOTS, name) });
    console.log('  shot ' + resolve(SHOTS, name));
  };
  await shot('campaign_1440_default.png');
  await dragBy(page, CAMP, -260);
  await shot('campaign_1440_chat_widened.png');
  await dragBy(page, CAMP, 480);
  await shot('campaign_1440_campaign_widened.png');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function separateKeys(browser, srv) {
  console.log('\n[one saved value per screen type]');
  const { ctx, page } = await newPage(browser, srv, { width: 1440, height: 950 });
  await page.evaluate(() => localStorage.clear());
  await openCampaign(page, 'camp-1', 'how');
  await dragBy(page, CAMP, -100);
  await openStory(page);
  const g = await geo(page, STORY);
  check(near(g.side, STORY.def), `the story screen is not moved by the campaign's width (${g.side}px)`, `story side ${g.side}`);
  await dragBy(page, STORY, -60);
  const keys = await page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith('clayrune.desk.split.')).sort());
  check(keys.join() === 'clayrune.desk.split.campaign,clayrune.desk.split.story', 'two keys: ' + keys.join(', '), 'keys: ' + keys.join(', '));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  const srv = makeServer();
  await exercise(browser, srv, CAMP);
  await exercise(browser, srv, STORY);
  await hiddenAndPopout(browser, srv);
  await separateKeys(browser, srv);
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) FAILED` : '\nall checks passed');
process.exit(bad ? 1 : 0);
