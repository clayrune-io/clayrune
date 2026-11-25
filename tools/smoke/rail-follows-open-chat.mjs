#!/usr/bin/env node
/**
 * The left rail follows the open chat (Ron, 2026-11-25).
 *
 * Ron clicked Tilda on the Floor with Dave expanded in the Channel rail. The
 * pane opened Tilda's dispatched chat and the rail still showed Dave: "while I
 * thought I was looking at Dave, it was actually Tilda session".
 *
 * Pins, at 1440 and 390px:
 *   1. Floor door (floorOpenFigure), modal already open and freshly opened:
 *      the chat's person is the only expanded row, its own chat row is active
 *      and (desktop) in view. A dispatched child lights the child, not the
 *      spawner.
 *   2. The user's collapse survives repaints and polls.
 *   3. Opening another chat expands again: tab switch, the "Open its chat"
 *      banner link, a deep link, and re-opening the very chat that was open.
 *   4. A chat with no roster person lights nobody.
 *   5. A worker nested under a spawner of the same person is still marked.
 *   6. Chats and Topics modes: the active row follows the open chat and is in
 *      view, and the rail keeps its scroll position across repaints.
 *
 * Hermetic: page + static assets from this checkout, /api/* canned.
 * RUN: node tools/smoke/rail-follows-open-chat.mjs
 * Screenshots: MC_SMOKE_SHOT_DIR=<dir>
 * Against another checkout (before/after): MC_SMOKE_ROOT=<repo root>
 */
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = process.env.MC_SMOKE_ROOT || resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const STATIC = loadStaticJsCss(REPO_ROOT);
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_rail';
const SHOT_DIR = process.env.MC_SMOKE_SHOT_DIR || '';
if (SHOT_DIR) mkdirSync(SHOT_DIR, { recursive: true });

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));

const person = (name, agent) => ({ name, scope: 'global', display_name: name, agent_name: agent, avatar: 'fig:smith' });
const DAVE = person('dave', 'Dave');
const TILDA = person('ui-fixer', 'Tilda');
const DAVE_KEY = 'global:dave', TILDA_KEY = 'global:ui-fixer';

const base = JSON.parse(readFileSync(join(__dirname, 'fixtures', 'projects.json'), 'utf8'))[0];
const PROJECT = { ...base, id: PID, name: 'Rail Smoke', project_path: '/smoke/' + PID, backlog: [] };

const NOW = Math.floor(Date.now() / 1000);
const row = (id, character, age, label, over = {}) => ({
  claude_session_id: 'csid-' + id, mc_session_id: 'mc-' + id, character, mtime: NOW - age,
  ts_relative: `${Math.ceil(age / 60)}m ago`, status: 'completed', turns: 3, label,
  first_user: label, last_user: label, provider: 'claude', live: false, ...over });
const ROWS = [
  ...Array.from({ length: 14 }, (_, i) => row('dave' + i, DAVE, 60 + i * 60, 'Dave chat ' + i)),
  // Tilda's dispatched chat is deliberately old, so it sits low in every list.
  row('tilda-child', TILDA, 4000, 'Tilda dispatched fix', { spawned_by_session_id: 'mc-dave0' }),
  row('tilda-sub', TILDA, 4100, 'Tilda sub task', { spawned_by_session_id: 'mc-tilda-child' }),
  row('tilda-new', TILDA, 3000, 'Tilda newest chat'),
  ...Array.from({ length: 12 }, (_, i) => row('plain' + i, null, 200 + i * 60, 'Plain chat ' + i)),
  row('last', DAVE, 9000, 'Oldest chat in the list'),
];
const session = (r) => ({
  session_id: r.mc_session_id, claude_session_id: r.claude_session_id, status: 'idle', task: r.label,
  log_lines: ['> Lead: ' + r.label, 'Transcript of ' + r.label], started_at: new Date(r.mtime * 1000).toISOString(),
  process_alive: true, mode: 'B', character: r.character, spawned_by_session_id: r.spawned_by_session_id || '',
  live_copies: [], cwd_moved_from: '', reports_waiting: [], children_running: [], active_subagents: [] });
const SESSIONS = ['dave0', 'tilda-child', 'tilda-sub', 'plain0']
  .map((id) => session(ROWS.find((r) => r.mc_session_id === 'mc-' + id)));
const TOPICS = { stale: false, generated_at: new Date().toISOString(), topics: Array.from({ length: 16 }, (_, i) => ({
  id: 't' + i, title: 'Topic ' + i, gist: 'Gist for topic ' + i, status: 'open', chat_count: 1,
  chats: [{ csid: i === 15 ? 'csid-tilda-child' : i === 0 ? 'csid-dave0' : 'csid-plain' + (i - 1), label: 'c', ts: '' }] })) };
// The dispatched chat's topic must sort last (oldest mtime), so it starts out of view.
TOPICS.topics.slice(1, 15).forEach((t, i) => { t.chats[0].csid = 'csid-dave' + i; });
TOPICS.topics[0].chats[0].csid = 'csid-plain0';

const browser = await chromium.launch();

async function open(width) {
  const ctx = await browser.newContext({ viewport: { width, height: width > 500 ? 900 : 844 } });
  const page = await ctx.newPage();
  await page.addInitScript(() => { localStorage.setItem('walkthrough_done', '1'); localStorage.setItem('mc_walkthrough_done', '1'); });
  const errors = [];
  page.on('pageerror', (e) => { if (!/cdn\.jsdelivr\.net|aborted|net::ERR|Failed to fetch|EventSource/i.test(e.message || '')) errors.push(e.message || String(e)); });
  await page.route('**/*', (route) => {
    const req = route.request(); const url = new URL(req.url()); const path = url.pathname;
    const json = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.origin !== ORIGIN) return route.abort();
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path.startsWith('/api/avatars/'))
      return route.fulfill({ status: 200, contentType: 'image/webp', body: readFileSync(resolve(__dirname, '..', '..', 'assets', 'avatars', 'guard.webp')) });
    if (req.method() !== 'GET') return route.abort();
    if (path === '/api/projects') return json([PROJECT]);
    if (path === '/api/local-auth/status') return json({ configured: true });
    if (path === '/api/config') return json({});
    if (path === '/api/characters') return json([]);
    if (path === `/api/project/${PID}/conversations`) return json(ROWS);
    if (path === `/api/project/${PID}/agent/status`) return json({ sessions: SESSIONS });
    if (path === `/api/project/${PID}/agent/log`) return json([]);
    if (path === `/api/project/${PID}/topics`) return json(TOPICS);
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => ['floorOpenFigure', 'openProjectModal', 'setRailMode', 'toggleChannelPerson',
    'switchAgentTab', 'openConversation', 'openProjectAtSession', 'backToConvList']
    .every((n) => typeof window[n] === 'function')
    && window.eval('typeof allProjects !== "undefined" && allProjects.length > 0'), null, { timeout: 20000 });
  await page.evaluate(() => localStorage.setItem('mc_rail_mode', 'chats'));
  return { page, ctx, errors };
}

const WIN = `.modal-window[data-modal-id="${PID}"]`;
const settle = (page) => page.waitForTimeout(700);
const shot = async (page, name) => { if (SHOT_DIR) await page.screenshot({ path: join(SHOT_DIR, name) }); };

// What the rail says right now.
const rail = (page) => page.evaluate((sel) => {
  const list = document.querySelector(`${sel} .agent-rail-list, ${sel} .conv-list-scroll`);
  if (!list) return { missing: true };
  const box = list.getBoundingClientRect();
  const inView = (el) => { const r = el.getBoundingClientRect(); return box.height > 0 && r.top >= box.top - 1 && r.bottom <= box.bottom + 1; };
  const active = Array.from(list.querySelectorAll('.conv-row.active:not(.channel-row)'));
  const topic = list.querySelector('.rail-topic.active');
  return {
    expanded: Array.from(list.querySelectorAll('.channel-row.expanded')).map((el) => el.dataset.charKey),
    personInView: !!list.querySelector('.channel-row.expanded') && inView(list.querySelector('.channel-row.expanded')),
    listed: Array.from(list.querySelectorAll('.channel-expanded .conv-row')).map((el) => el.dataset.mcsid),
    active: active.map((el) => el.dataset.mcsid),
    activeInView: active.length === 1 && inView(active[0]),
    activeIsChild: active.length === 1 && active[0].classList.contains('conv-row-child'),
    topic: topic ? topic.querySelector('.rail-topic-title').textContent : '',
    topicInView: !!topic && inView(topic),
    shown: box.height > 0, scrollTop: Math.round(list.scrollTop),
    openSid: window.eval('typeof activeAgentTab === "undefined" ? "" : (activeAgentTab["' + sel.split('"')[1] + '"] || "")'),
  };
}, WIN);

const only = (r, key, sid) => r.expanded.length === 1 && r.expanded[0] === key && r.active.length === 1 && r.active[0] === sid;

try {
  for (const width of [1440, 390]) {
    const mobile = width <= 960;
    console.log(`── ${width}px`);
    const { page, ctx, errors } = await open(width);
    // On a phone the rail is its own screen: step back from the chat to read it.
    const toList = async () => { if (mobile) { await page.evaluate((p) => window.backToConvList(p), PID); await settle(page); } };
    const personRow = (key) => page.locator(`${WIN} .channel-row[data-char-key="${key}"]`).first();
    const tapPerson = async (key) => { await personRow(key).evaluate((el) => el.click()); await settle(page); };
    const floor = async (id) => {
      await page.evaluate(([p, s]) => window.floorOpenFigure(p, 'csid-' + s, 'mc-' + s), [PID, id]);
      await page.waitForTimeout(1500);
    };
    const switchTo = async (id) => { await page.evaluate(([p, s]) => window.switchAgentTab(p, 'mc-' + s), [PID, id]); await settle(page); };
    // Desktop: the rail sits beside the chat, so the chat's own row is marked.
    // Phone: the list is only on screen with no chat open (no row is ever
    // marked there), so the person must be expanded, in view, and list the chat.
    const expect = async (what, key, sid) => {
      await toList();
      const r = await rail(page);
      if (mobile) {
        check(r.expanded.join() === key && r.listed.includes(sid), `${what}: ${key} alone is expanded and lists ${sid}`, `${what}: ${JSON.stringify(r)}`);
        check(r.personInView, `${what}: that person is in view`, `${what}: person out of view ${JSON.stringify(r)}`);
      } else {
        check(only(r, key, sid), `${what}: ${key} alone is expanded and ${sid} is its marked chat`, `${what}: ${JSON.stringify(r)}`);
        check(r.activeInView, `${what}: the marked chat is in view`, `${what}: marked chat out of view ${JSON.stringify(r)}`);
      }
      return r;
    };

    await page.evaluate((p) => window.openProjectModal(p), PID);
    await page.waitForSelector(`${WIN} .rail-mode-btn`, { state: 'attached' });
    await settle(page);
    await page.evaluate((p) => window.setRailMode(p, 'channel'), PID);
    await settle(page);
    // Desktop auto-selects Dave's newest chat, so the rail already follows it.
    // The phone lands on the list with nothing open, so nothing is expanded.
    let r = await rail(page);
    check(r.expanded.join() === (mobile ? '' : DAVE_KEY),
      mobile ? 'no chat open: nobody is expanded' : 'switching to Channel expands the person of the open chat', JSON.stringify(r));
    if (!(r.expanded || []).includes(DAVE_KEY)) await tapPerson(DAVE_KEY);
    await toList();
    r = await rail(page);
    check(r.expanded.join() === DAVE_KEY, 'precondition: Dave is expanded', 'precondition: ' + JSON.stringify(r));
    await shot(page, `rail-1-dave-expanded-${width}.png`);

    // 1. Ron's path: Floor figure click while Dave is expanded.
    await floor('tilda-child');
    await shot(page, `rail-2-floor-opened-tilda-${width}.png`);
    await expect('Floor click, modal already open', TILDA_KEY, 'mc-tilda-child');
    await shot(page, `rail-3-floor-opened-tilda-list-${width}.png`);

    // 2. The user's collapse sticks.
    await tapPerson(TILDA_KEY);
    r = await rail(page);
    check(r.expanded.length === 0, 'clicking the highlighted person collapses it', JSON.stringify(r));
    await page.evaluate(async (p) => {
      for (let i = 0; i < 3; i++) { window.refreshModalById(p); await window.fetchAgentStatus(p); await window.loadConversations(p); }
    }, PID);
    await settle(page);
    r = await rail(page);
    check(r.expanded.length === 0, 'it stays collapsed across repaints and polls', JSON.stringify(r));

    // 3. Another chat expands again, through each door.
    await switchTo('dave0');
    await expect('tab switch', DAVE_KEY, 'mc-dave0');
    await switchTo('tilda-child');
    const banner = page.locator(`${WIN} .dispatched-by-link`).first();
    if (await banner.count()) {
      await banner.evaluate((el) => el.click());
      await settle(page);
      await expect('"Open its chat" banner link', DAVE_KEY, 'mc-dave0');
    } else fail('the dispatched-by banner link did not render in the child chat');
    await page.evaluate((p) => window.openProjectAtSession(p, 'mc-tilda-child'), PID);
    await settle(page);
    await expect('deep link', TILDA_KEY, 'mc-tilda-child');
    await switchTo('tilda-child');
    await toList();
    await tapPerson(TILDA_KEY);
    await floor('tilda-child');
    await expect('re-opening the chat after collapsing its person', TILDA_KEY, 'mc-tilda-child');

    // 1b. Fresh open from the Floor (openProjectModal resets, then the chat opens).
    await page.evaluate((p) => window.closeModalById(p), PID);
    await settle(page);
    await floor('tilda-child');
    await page.waitForSelector(WIN, { state: 'attached' });
    await expect('Floor click, modal freshly opened', TILDA_KEY, 'mc-tilda-child');

    // 5. Same-person spawner: the worker is nested and still marked.
    await switchTo('tilda-sub');
    r = await expect('nested worker', TILDA_KEY, 'mc-tilda-sub');
    if (!mobile) check(r.activeIsChild, 'the marked row is the nested child row', JSON.stringify(r));

    // 4. No roster person: nobody is lit.
    await switchTo('plain0');
    await toList();
    r = await rail(page);
    check(r.expanded.length === 0, 'a chat with no persona lights nobody', JSON.stringify(r));
    await shot(page, `rail-4-characterless-${width}.png`);

    // 6. Chats mode.
    await page.evaluate((p) => window.setRailMode(p, 'chats'), PID);
    await settle(page);
    await switchTo('plain0');
    await switchTo('tilda-sub');
    if (!mobile) {
      r = await rail(page);
      check(r.active.join() === 'mc-tilda-sub', 'Chats: the open chat is the marked row', JSON.stringify(r));
      check(r.activeInView, 'Chats: the marked row is scrolled into view', JSON.stringify(r));
      await page.evaluate((s) => { document.querySelector(`${s} .agent-rail-list`).scrollTop = 40; }, WIN);
      await page.evaluate((p) => window.refreshModalById(p), PID);
      await settle(page);
      r = await rail(page);
      check(r.scrollTop === 40, 'Chats: a repaint keeps where the user scrolled to', JSON.stringify(r));
    }
    await shot(page, `rail-5-chats-mode-${width}.png`);

    // 6b. Topics mode.
    await page.evaluate((p) => window.setRailMode(p, 'topics'), PID);
    await settle(page);
    await switchTo('plain0');
    await switchTo('tilda-child');
    if (!mobile) {
      r = await rail(page);
      check(r.topic === 'Topic 15', 'Topics: the topic holding the open chat is marked', JSON.stringify(r));
      check(r.topicInView, 'Topics: the marked topic is scrolled into view', JSON.stringify(r));
    }
    await toList();
    await shot(page, `rail-6-topics-mode-${width}.png`);

    errors.forEach((e) => fail('uncaught exception: ' + e));
    await ctx.close();
  }
} catch (err) {
  fail('harness error: ' + (err && err.stack ? err.stack : err));
} finally {
  await browser.close().catch(() => {});
}
console.log(bad === 0 ? '\n✅ PASS: the rail follows the open chat.' : `\n❌ FAIL: ${bad} check(s) failed.`);
process.exit(bad === 0 ? 0 : 1);
