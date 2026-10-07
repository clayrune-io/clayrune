#!/usr/bin/env node
/**
 * Desk Studio (Ron 2026-10-06): "Your agent" beside the storyboard is a CONVERSATION,
 * not a one-way instruction box. A fake server stands in for /api/desk/* and the
 * model call (mc/desk_story_chat.py is pinned by tests/test_desk_story_chat.py); what
 * is under test is what the browser sends and what it paints and applies.
 *
 *   6. Unsaved  -> asking before anything was saved saves the board once and asks again.
 *   1. Question -> an empty thread says what to do; a question with a scene selected POSTs
 *                  {owner, message, scene, story, scenes}, shows both turns, and changes and
 *                  saves NOTHING; Shift+Enter is a newline, Enter sends.
 *   2. Change   -> a proposed scene change is applied as ONE undoable command, saved, and the
 *                  reply carries a chip naming the scene; Undo restores it.
 *   3. Board    -> with none selected the whole board goes; a sparse board change is applied.
 *   4. Refused  -> a change the server refused shows why and changes nothing; a failed call
 *                  keeps the message in the box and adds no turn.
 *   5. Reload   -> a fresh page opens the draft and the thread is back.
 *   8. Send     -> the box empties the moment Enter sends (the pending turn shows the message)
 *                  and stays disabled until the reply is in; a failed send puts the text back.
 *   9. Text     -> A-/A+ scale the thread and the box, keyboard reachable, clamped, and the
 *                  size survives a reload (localStorage).
 *  10. Pop out  -> the SAME chat element is lifted to a large view: sending from it lands in the
 *                  one thread, Esc / the backdrop / the button close it and return focus; at 1440
 *                  and 390 with no horizontal scroll.
 *   7. Shots    -> screens at 1440 and 390 into docs/desk_v1/screens/.
 *
 * RUN   cd tools/smoke && node desk-v1-story-chat.mjs
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
const SHOTS = resolve(REPO_ROOT, 'docs', 'desk_v1', 'screens');
const ORIGIN = 'http://mc.smoke.test';
const STATIC = loadStaticJsCss(REPO_ROOT);

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

const GIF = 'R0lGODlhAQABAAAAACwAAAAAAQABAAA=';
const SRC_PIC = (name) => `data:image/gif;base64,${GIF}#${name}`;
const PNG = { name: 'hero.png', mimeType: 'image/png', buffer: Buffer.from('not-really-a-png') };

let GID = 0;
const STORY = Array.from({ length: 40 }, (_, i) => `Shot ${i + 1}: the installer window opens, the cursor moves to the button and the narrator says line ${i + 1}.`).join('\n');
const MADE = [
  { label: 'Cold open', line: 'Black screen, a single cursor blinks. Narrator: "Installing used to take an afternoon."', duration_sec: 4 },
  { label: 'The click', line: 'Close on the installer button, one click, no admin prompt.', duration_sec: 3 },
  { label: 'Done', line: 'The dashboard appears fully loaded. End card with the logo.', duration_sec: 5 },
];

function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  const pieces = fx.families.map((f) => JSON.parse(JSON.stringify(f)));
  const srv = { log: [], boards: {}, threads: {}, fx, gen: null, reply: null };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects, pieces: pieces.map((p) => JSON.parse(JSON.stringify(p))) });
  srv.out = (key) => {
    const b = srv.boards[key] || { rev: 0, scenes: [], pending_edits: [], title: '', story: '' };
    return {
      rev: b.rev, title: b.title, story: b.story || '', pending_edits: b.pending_edits,
      scenes: b.scenes.map((s) => ({ ...s, picture: s.picture ? { ...s.picture, src: SRC_PIC(s.picture.path) } : null })),
    };
  };
  return srv;
}

async function newPage(browser, srv, viewport) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  const pageErrors = [];
  const dialogs = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  page.on('dialog', (d) => { dialogs.push(d.message()); d.dismiss().catch(() => {}); });
  await page.route('**/*', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();
    const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return J(srv.fx.projects.map((p) => ({
      id: p.id, name: p.name, status: 'active', domain: 'general', emoji: '🧪', description: '', summary: '', current_task: 'Idle',
      next_action: '', blocked: false, blocked_reason: null, activity_log: [], backlog: [], project_path: '/smoke/' + p.id,
      last_updated: '2026-09-09T00:00:00Z', last_updated_relative: 'today', last_completed: null, live_agent: null, display_order: 0,
      provider: 'claude', use_streaming_agent: true, distiller_mode: 'proposed', distiller_min_recurrence: 3,
      distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3, distiller_max_explorations_per_session: 3,
      distiller_min_turns: 5, distiller_skip_errors: true, roster: [],
    })));
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true, user_timezone: '' });
    if (path === '/api/characters') return J([{ name: 'dave', scope: 'global', agent_name: 'Dave', avatar: '' }]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    const raw = req.postData() || '';
    try { body = req.postDataJSON(); } catch (_) { /* multipart or none */ }
    srv.log.push({ method, path, body, raw });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/materials') {
      if (srv.materialsGate) await srv.materialsGate;
      return J({ library: { video: [], image: [] }, articles: [], online: { video: [], image: [] }, recent: [] });
    }
    if (path === '/api/desk/studio/storyboards') {
      return J({ storyboards: Object.entries(srv.boards).filter(([k]) => k.startsWith('studio:')).map(([k, b]) => ({ id: k.slice(7), title: b.title, scenes: b.scenes.length, updated_at: '2026-10-01T10:00:00Z' })) });
    }
    if (path === '/api/desk/storyboard/chat') {
      if (method === 'POST' && srv.chatGate) await srv.chatGate;
      if (method === 'GET') return J({ thread: (srv.threads[`${url.searchParams.get('kind')}:${url.searchParams.get('id')}`] || []).map((t) => ({ ...t })) });
      const key = `${body.owner.kind}:${body.owner.id}`;
      if (!srv.boards[key]) return J({ error: 'this storyboard is not saved yet, so there is nowhere to keep the conversation' }, 404);
      if (!srv.reply) return J({ error: 'no fake answer set' }, 500);
      const r = srv.reply(body);
      if (r.status) return J({ error: r.error }, r.status);
      const mk = (role, text, extra) => ({ id: 'tn' + (++GID), role, text, at: '2026-10-06T10:00:00Z', scene: body.scene, agent: role === 'agent' ? 'Dave' : null, change: null, change_error: null, ...extra });
      const turns = [mk('user', body.message), mk('agent', r.reply, r.change ? { change: { mode: r.change.mode, summary: r.change.summary, numbers: r.change.numbers || {}, status: 'proposed' } } : { change_error: r.change_error || null })];
      (srv.threads[key] = srv.threads[key] || []).push(...turns);
      return J({ agent: { ref: 'global:dave', name: 'Dave' }, provider: 'claude', model: 'sonnet', turns, ...(r.change ? { change: r.change } : {}) });
    }
    if (path === '/api/desk/storyboard/chat/status' && method === 'POST') {
      const t = (srv.threads[`${body.owner.kind}:${body.owner.id}`] || []).find((x) => x.id === body.turn_id);
      if (!t || !t.change) return J({ error: 'that turn is not in this conversation' }, 404);
      if (t.change.status === 'proposed') { t.change.status = body.status; if (body.status === 'failed') t.change.note = body.note; }
      return J(t);
    }
    if (path === '/api/desk/storyboard/generate' && method === 'POST') {
      if (srv.gen) return srv.gen(body, J);
      return J({ error: 'no fake answer set' }, 500);
    }
    let m = path.match(/^\/api\/desk\/(pieces|studio)\/([^/]+)\/storyboard$/);
    if (m) {
      const key = (m[1] === 'pieces' ? 'piece:' : 'studio:') + m[2];
      if (method === 'GET') return J(srv.out(key));
      if (method === 'PUT') {
        const cur = srv.boards[key] || { rev: 0, scenes: [], pending_edits: [], title: '', story: '' };
        if (body.rev !== cur.rev) return J({ error: 'this storyboard changed since you loaded it', problems: [`current_rev=${cur.rev}`] }, 409);
        srv.boards[key] = {
          rev: cur.rev + 1, title: m[1] === 'studio' ? (body.title || cur.title || '') : '',
          story: typeof body.story === 'string' ? body.story : (cur.story || ''),
          scenes: body.scenes.map((s) => ({ ...s })), pending_edits: body.pending_edits || [],
        };
        return J(srv.out(key));
      }
    }
    m = path.match(/^\/api\/desk\/(pieces|studio)\/([^/]+)\/storyboard\/pictures$/);
    if (m && method === 'POST') {
      const file = (raw.match(/name="file"; filename="([^"]+)"/) || [])[1] || 'x.png';
      const rel = 'desk/library/image/Storyboards/' + file;
      return J({ path: rel, kind: 'image', title: file, src: SRC_PIC(rel) }, 201);
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.waitForFunction(() => window.DeskV1Store.state().campaigns.length > 0, null, { timeout: 8000 });
  return { ctx, page, pageErrors, dialogs };
}


async function reopen(page, itemId) {
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.waitForFunction(() => window.DeskV1Store.state().campaigns.length > 0, null, { timeout: 8000 });
  await page.evaluate(() => window.deskV1Nav('studio', {}));
  await page.waitForSelector(`[data-studio-recent-row="${itemId}"]`, { timeout: 8000 });
  await page.click(`[data-studio-recent-row="${itemId}"]`);
  await page.waitForSelector('[data-sb-story]:not([disabled])', { timeout: 8000 });
  await page.waitForSelector('[data-chat-chrome][data-wired]', { timeout: 8000 });
}
const fontPx = (page, sel) => page.$eval(sel, (e) => parseFloat(getComputedStyle(e).fontSize));
const hOverflow = (page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
const popState = (page) => page.evaluate(() => {
  const el = document.querySelector('[data-chat]'); const r = el.getBoundingClientRect();
  const ae = document.activeElement;
  return {
    same: el === window.__chat, popped: el.classList.contains('is-popped'), open: el.matches(':popover-open'),
    l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, vw: innerWidth, vh: innerHeight,
    threads: document.querySelectorAll('[data-chat-thread]').length,
    focus: ae && (ae.matches('[data-chat-pop]') ? 'pop' : ae.matches('[data-sb-ask]') ? 'ask' : ae.tagName),
    desk: !!document.querySelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell'),
  };
});

const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });
const puts = (srv, re) => srv.log.filter((r) => r.method === 'PUT' && re.test(r.path));
const chats = (srv) => srv.log.filter((r) => r.path === '/api/desk/storyboard/chat' && r.method === 'POST');
const realErrors = (pageErrors) => pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
const labels = (page) => page.$$eval('[data-storyboard] .desk-v1-sb-scene', (els) => els.map((e) => e.dataset.sceneLabel));
const toasts = (page) => page.$$eval('.toast', (els) => els.map((e) => e.textContent)).catch(() => []);
const turns = (page) => page.$$eval('[data-chat-turn]:not([data-chat-thinking])', (els) => els.map((e) => [e.dataset.chatTurn, e.querySelector('.desk-v1-chat-text').textContent]));
const statusText = (page) => page.textContent('[data-sb-ask-status]');
const lastChip = (page) => page.$$eval('[data-chat-change]', (els) => (els.length ? els[els.length - 1].textContent : ''));

async function studioNewVideo(page) {
  await page.evaluate(() => window.deskV1Nav('studio', {}));
  await page.waitForSelector('[data-studio-new="video"]', { timeout: 8000 });
  await page.evaluate(() => window.deskV1Nav('studio-create', { kind: 'video' }));
  await page.waitForSelector('[data-studio-create][data-kind="video"] [data-storyboard]', { timeout: 8000 });
  await page.waitForSelector('[data-sb-story]:not([disabled])', { timeout: 8000 });
}

async function main(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors, dialogs } = await newPage(browser, srv, { width: 1440, height: 950 });
  await studioNewVideo(page);
  const key = () => Object.keys(srv.boards)[0];
  const ask = page.locator('[data-sb-ask]');

  // 6. Nothing has been typed or saved yet: the first ask saves the board once.
  console.log('Asking before anything is saved');
  (await page.textContent('[data-chat-empty]')).includes('brainstorm') ? ok('an empty thread says what the box is for') : fail('empty thread text');
  srv.reply = () => ({ reply: 'Start with who the viewer is, then the first shot follows.' });
  srv.log.length = 0;
  await ask.fill('where should I start?');
  await ask.press('Enter');
  await settle(page, () => document.querySelectorAll('[data-chat-turn]').length >= 2 && !document.querySelector('[data-chat-thinking]'));
  (chats(srv).length === 2 && puts(srv, /storyboard$/).length === 1)
    ? ok('the first ask found no saved board (404), saved it once (PUT) and asked again') : fail('unsaved flow: ' + JSON.stringify(srv.log.map((r) => r.method + ' ' + r.path)));
  JSON.stringify(await turns(page)) === JSON.stringify([['user', 'where should I start?'], ['agent', 'Start with who the viewer is, then the first shot follows.']])
    ? ok('both turns are in the thread') : fail('turns: ' + JSON.stringify(await turns(page)));
  (await ask.inputValue()) === '' ? ok('the box is cleared once the answer is in') : fail('box not cleared');

  // Make a storyboard so there is something to talk about.
  const STORY = 'Shot 1: a cursor blinks. Shot 2: the installer button is clicked. Shot 3: the dashboard appears.';
  await page.fill('[data-sb-story]', STORY);
  await page.waitForTimeout(900);
  const MADE = [
    { label: 'Cold open', line: 'Black screen, a single cursor blinks.', duration_sec: 4 },
    { label: 'The click', line: 'Close on the installer button, one click.', duration_sec: 3 },
    { label: 'Done', line: 'The dashboard appears fully loaded.', duration_sec: 5 }];
  srv.gen = (body, J) => J({ agent: null, provider: 'claude', model: 'sonnet', mode: 'board', scenes: MADE.map((s) => ({ ...s, id: 'g' + (++GID), picture: null, edited: false })) });
  await page.click('[data-story-make]');
  await settle(page, () => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === 3);
  await page.waitForTimeout(500);

  // 1. A question changes nothing.
  console.log('A question');
  await page.click('.desk-v1-sb-scene[data-scene-label="The click"] [data-scene-title]');
  await settle(page, () => /Talking about scene 2/.test((document.querySelector('[data-sb-ask-status]') || {}).textContent || ''));
  ok('selecting scene 2 changes the hint to "Talking about scene 2"');
  srv.reply = () => ({ reply: 'It works because one click with no prompt is the whole promise of the video.' });
  srv.log.length = 0;
  await ask.fill('line one');
  await ask.press('Shift+Enter');
  (await ask.inputValue()) === 'line one\n' && chats(srv).length === 0 ? ok('Shift+Enter adds a line and sends nothing') : fail('shift+enter: ' + JSON.stringify(await ask.inputValue()));
  await ask.fill('why does scene 2 work?');
  await ask.press('Enter');
  await settle(page, () => document.querySelectorAll('[data-chat-turn]').length >= 4 && !document.querySelector('[data-chat-thinking]'));
  await page.waitForTimeout(300);
  const q = chats(srv);
  (q.length === 1 && q[0].body.message === 'why does scene 2 work?' && q[0].body.scene === 2 && q[0].body.story === STORY &&
    q[0].body.scenes.length === 3 && q[0].body.owner.kind === 'studio' && q[0].body.scenes[1].label === 'The click')
    ? ok('Enter POSTs {owner, message, scene: 2, story, scenes}') : fail('chat body: ' + JSON.stringify(q.map((r) => r.body)));
  (puts(srv, /storyboard$/).length === 0 && JSON.stringify(await labels(page)) === JSON.stringify(MADE.map((s) => s.label)))
    ? ok('a question changes and saves nothing') : fail('a question changed the board');
  (await page.$$('[data-chat-change]')).length === 0 ? ok('no change chip on an answer') : fail('chip on a plain answer');
  const meta = await page.$$eval('[data-chat-turn="user"] .desk-v1-chat-meta', (e) => e.map((x) => x.textContent));
  meta[meta.length - 1] === 'You · scene 2' ? ok('the user turn is tagged with the scene it was about') : fail('meta: ' + JSON.stringify(meta));
  !/Sent to/.test((await toasts(page)).join(' ')) ? ok('no "Sent to…" toast') : fail('the fake toast is back');

  // 2. A proposed scene change.
  console.log('A scene change');
  srv.reply = () => ({
    reply: 'Slower it is: I held the push-in so the click lands.',
    change: { mode: 'scene', scene_number: 2, scene: { label: 'The click, slower', line: 'Slow push-in on the button, then the click.', duration_sec: 6 }, summary: 'Changed scene 2.', numbers: { changed: [2], added: [], removed: [] } },
  });
  srv.log.length = 0;
  await ask.fill('make scene 2 slower');
  await ask.press('Enter');
  await settle(page, () => !!document.querySelector('.desk-v1-sb-scene[data-scene-label="The click, slower"]'));
  await settle(page, () => { const c = document.querySelectorAll('[data-chat-change]'); return c.length && /Changed: Changed scene 2\./.test(c[c.length - 1].textContent); });
  await page.waitForTimeout(400);
  JSON.stringify(await labels(page)) === JSON.stringify(['Cold open', 'The click, slower', 'Done']) ? ok('only scene 2 changed') : fail('rows: ' + JSON.stringify(await labels(page)));
  (puts(srv, /storyboard$/).length === 1 && srv.boards[key()].scenes[1].duration_sec === 6) ? ok('the change is saved (one PUT) with the new duration') : fail('not saved');
  const st = srv.log.filter((r) => r.path === '/api/desk/storyboard/chat/status');
  (st.length === 1 && st[0].body.status === 'applied' && srv.threads[key()].slice(-1)[0].change.status === 'applied')
    ? ok('the page reported the change as applied, and the saved turn says so') : fail('status reports: ' + JSON.stringify(st.map((r) => r.body)));
  /Changed scene 2\. Undo/.test(await statusText(page)) ? ok('the status line is written from the answer: ' + JSON.stringify(await statusText(page))) : fail('status: ' + (await statusText(page)));
  await page.click('[data-sb-undo]');
  await settle(page, () => !!document.querySelector('.desk-v1-sb-scene[data-scene-label="The click"]'));
  await page.waitForTimeout(300);
  srv.boards[key()].scenes[1].label === 'The click' ? ok('Undo is ONE step: scene 2 is back, and saved') : fail('undo did not restore');

  // 3. The whole board, sparse change.
  console.log('The whole board');
  await page.click('.desk-v1-sb-scene[data-scene-label="The click"] [data-scene-title]');
  await settle(page, () => /whole storyboard/.test((document.querySelector('[data-sb-ask-status]') || {}).textContent || ''));
  srv.reply = () => ({
    reply: 'I tightened the open and added a beat after the click.',
    change: { mode: 'board', summary: 'Changed scene 1. Added scene 3.', numbers: { changed: [1], added: [3], removed: [] }, scenes: [
      { label: 'Cold open', line: 'A tighter cold open.', duration_sec: 2, from: 1 },
      { label: 'The click', line: MADE[1].line, duration_sec: 3, from: 2 },
      { label: 'Beat', line: 'A one-second pause.', duration_sec: 1, from: null },
      { label: 'Done', line: MADE[2].line, duration_sec: 5, from: 3 }] },
  });
  srv.log.length = 0;
  await ask.fill('tighten the open and add a beat');
  await ask.press('Enter');
  await settle(page, () => !!document.querySelector('.desk-v1-sb-scene[data-scene-label="Beat"]'));
  await page.waitForTimeout(400);
  const bq = chats(srv)[0].body;
  (bq.scene === null && bq.scenes.length === 3) ? ok('with none selected the whole board goes (scene: null)') : fail('board body: ' + JSON.stringify(bq));
  JSON.stringify(await labels(page)) === JSON.stringify(['Cold open', 'The click', 'Beat', 'Done']) ? ok('the revision is applied (4 scenes)') : fail('rows: ' + JSON.stringify(await labels(page)));
  const chip = await lastChip(page);
  (chip === 'Changed: Changed scene 1. Added scene 3.' && /Added scene 3\. Undo/.test(await statusText(page)))
    ? ok('the chip and the status line name the scene numbers: ' + JSON.stringify(chip)) : fail('summary: ' + chip + ' / ' + (await statusText(page)));
  await page.click('[data-sb-undo]');
  await settle(page, () => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === 3);
  ok('Undo of the board change restores the three scenes');

  // 4. A refused change, and a failed call.
  console.log('Refused and failed');
  srv.reply = () => ({ reply: 'I would cut scene 9.', change_error: 'an edit named a scene that is not there' });
  srv.log.length = 0;
  await ask.fill('cut scene 9');
  await ask.press('Enter');
  await settle(page, () => { const c = document.querySelectorAll('[data-chat-change]'); return c.length && /not there/.test(c[c.length - 1].textContent); });
  (puts(srv, /storyboard$/).length === 0 && (await labels(page)).length === 3) ? ok('a refused change is shown with its reason and changes nothing') : fail('refused change touched the board');
  srv.reply = () => ({ status: 502, error: 'the model call failed, so nothing was changed: boom' });
  const before = (await turns(page)).length;
  await ask.fill('another thought');
  await ask.press('Enter');
  await settle(page, () => /Nothing was changed/.test((document.querySelector('[data-sb-ask-status]') || {}).textContent || ''));
  ((await ask.inputValue()) === 'another thought' && (await turns(page)).length === before && !(await page.$('[data-chat-pending]')))
    ? ok('a failed call keeps the message in the box, adds no turn, leaves no pending bubble') : fail('failure handling');

  // 5. Reload.
  console.log('Reload');
  await page.waitForTimeout(300);
  const itemId = key().replace(/^studio:/, '');
  const fresh = await newPage(browser, srv, { width: 1440, height: 950 });
  await fresh.page.evaluate(() => window.deskV1Nav('studio', {}));
  await fresh.page.waitForSelector(`[data-studio-recent-row="${itemId}"]`, { timeout: 8000 });
  await fresh.page.click(`[data-studio-recent-row="${itemId}"]`);
  await fresh.page.waitForSelector('[data-sb-story]:not([disabled])', { timeout: 8000 });
  await settle(fresh.page, (n) => document.querySelectorAll('[data-chat-turn]').length >= n, srv.threads[key()].length);
  const back = await turns(fresh.page);
  (back.length === srv.threads[key()].length && back[0][1] === 'where should I start?')
    ? ok(`after a reload the conversation is back (${back.length} turns)`) : fail('reloaded thread: ' + JSON.stringify(back));
  const chips = await fresh.page.$$eval('[data-chat-change]', (e) => e.map((x) => x.textContent));
  (chips.some((c) => /^Changed: Changed scene 2\./.test(c)) && chips.some((c) => /not there/.test(c)))
    ? ok('and each reply keeps its chip (applied / refused)') : fail('chips: ' + JSON.stringify(chips));
  realErrors(fresh.pageErrors).forEach((e) => fail('page error (reload): ' + e));
  await fresh.ctx.close();

  // 8. The box empties the moment Enter sends; it stays disabled until the turn is over.
  console.log('Send empties the box at once');
  let release;
  srv.chatGate = new Promise((r) => { release = r; });
  srv.reply = () => ({ reply: 'Held reply.' });
  await ask.fill('hold this reply');
  await ask.press('Enter');
  await settle(page, () => !!document.querySelector('[data-chat-pending]'));
  const mid = await page.evaluate(() => {
    const a = document.querySelector('[data-sb-ask]');
    return { v: a.value, d: a.disabled, pend: document.querySelector('[data-chat-pending] .desk-v1-chat-text').textContent, thinking: !!document.querySelector('[data-chat-thinking]') };
  });
  (mid.v === '' && mid.d && mid.pend === 'hold this reply' && mid.thinking)
    ? ok('while the reply is pending the box is empty and disabled, and the thread shows the message once (pending bubble)') : fail('pending state: ' + JSON.stringify(mid));
  release(); srv.chatGate = null;
  await settle(page, () => !document.querySelector('[data-chat-thinking]') && !document.querySelector('[data-sb-ask]').disabled);
  ((await ask.inputValue()) === '' && (await turns(page)).slice(-1)[0][1] === 'Held reply.') ? ok('the reply lands, the box is empty and usable again') : fail('after reply: ' + (await ask.inputValue()));
  srv.chatGate = new Promise((r) => { release = r; });
  srv.reply = () => ({ status: 502, error: 'the model call failed, so nothing was changed: boom' });
  await ask.fill('this one will fail');
  await ask.press('Enter');
  await settle(page, () => !!document.querySelector('[data-chat-pending]'));
  (await ask.inputValue()) === '' ? ok('a send that will fail also empties the box at once') : fail('box not empty while pending');
  release(); srv.chatGate = null;
  await settle(page, () => /Nothing was changed/.test((document.querySelector('[data-sb-ask-status]') || {}).textContent || ''));
  ((await ask.inputValue()) === 'this one will fail' && !(await ask.isDisabled()) && !(await page.$('[data-chat-pending]')) && /back in the box/.test(await statusText(page)))
    ? ok('the failure puts the typed text back, re-enables the box and says so') : fail('restore: ' + JSON.stringify(await ask.inputValue()));
  await ask.fill('');

  // 9. Text size: A-/A+, keyboard reachable, clamped, remembered across a reload.
  console.log('Text size');
  const AGENT_TXT = '[data-chat-turn="agent"] .desk-v1-chat-text';
  const t0 = await fontPx(page, AGENT_TXT);
  const a0 = await fontPx(page, '[data-sb-ask]');
  await page.click('[data-chat-text-more]');
  await page.focus('[data-chat-text-more]');
  await page.keyboard.press('Enter');
  const t1 = await fontPx(page, AGENT_TXT);
  const a1 = await fontPx(page, '[data-sb-ask]');
  (t1 > t0 * 1.25 && a1 > a0 * 1.25 && (await page.textContent('[data-chat-text-read]')) === '130%')
    ? ok(`A+ (mouse, then keyboard) scales the thread ${t0}px -> ${t1}px and the box ${a0}px -> ${a1}px (130%)`) : fail('scale up: ' + JSON.stringify({ t0, t1, a0, a1 }));
  for (let i = 0; i < 10; i++) if (!(await page.isDisabled('[data-chat-text-more]'))) await page.click('[data-chat-text-more]');
  const tMax = await fontPx(page, AGENT_TXT);
  ((await page.isDisabled('[data-chat-text-more]')) && (await page.textContent('[data-chat-text-read]')) === '175%' && tMax > t1)
    ? ok(`A+ stops at 175% (${tMax}px) and disables itself`) : fail('max: ' + tMax);
  for (let i = 0; i < 12; i++) if (!(await page.isDisabled('[data-chat-text-less]'))) await page.click('[data-chat-text-less]');
  const tMin = await fontPx(page, AGENT_TXT);
  ((await page.isDisabled('[data-chat-text-less]')) && (await page.textContent('[data-chat-text-read]')) === '85%' && tMin < t0)
    ? ok(`A- stops at 85% (${tMin}px) and disables itself`) : fail('min: ' + tMin);
  for (let i = 0; i < 3; i++) await page.click('[data-chat-text-more]');
  const want = await fontPx(page, AGENT_TXT);
  await page.waitForTimeout(300);
  await reopen(page, itemId);
  await settle(page, () => document.querySelectorAll('[data-chat-turn]').length > 0);
  const stored = await page.evaluate(() => localStorage.getItem('clayrune.desk.chatTextScale'));
  const back2 = await fontPx(page, AGENT_TXT);
  (stored === '1.3' && back2 === want && (await page.textContent('[data-chat-text-read]')) === '130%')
    ? ok(`after a reload the size is still 130% (stored ${stored}, ${back2}px)`) : fail('persist: ' + JSON.stringify({ stored, want, back2 }));

  // 10. Pop out: the same chat element, large, live; Esc / backdrop / button close it.
  console.log('Pop out');
  await page.evaluate(() => { window.__chat = document.querySelector('[data-chat]'); });
  const before0 = (await turns(page)).length;
  await page.click('[data-chat-pop]');
  let ps = await popState(page);
  (ps.same && ps.popped && ps.open && ps.w >= 800 && ps.l >= 0 && ps.r <= ps.vw && ps.b <= ps.vh && ps.threads === 1 && ps.focus === 'ask')
    ? ok(`the large view is the same element (one thread in the page), in the top layer, ${Math.round(ps.w)}px wide, focus in the ask box`) : fail('pop state: ' + JSON.stringify(ps));
  (await hOverflow(page)) <= 1 ? ok('no horizontal scroll with the large view open (1440)') : fail('overflow when popped');
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: resolve(SHOTS, 'studio-agent-chat-popout-1440.png') });
  ok('docs/desk_v1/screens/studio-agent-chat-popout-1440.png');
  srv.reply = () => ({ reply: 'Answered inside the large view.' });
  srv.log.length = 0;
  await ask.fill('sent from the large view');
  await ask.press('Enter');
  await settle(page, () => !document.querySelector('[data-chat-thinking]') && [...document.querySelectorAll('[data-chat-turn="agent"]')].some((e) => e.textContent.includes('Answered inside the large view.')));
  const sent = chats(srv);
  (sent.length === 1 && sent[0].body.message === 'sent from the large view' && (await turns(page)).length === before0 + 2 && (await ask.inputValue()) === '')
    ? ok('sending from the large view posts once and both turns appear in the thread') : fail('popped send: ' + JSON.stringify(sent.map((r) => r.body && r.body.message)));
  await page.keyboard.press('Escape');
  ps = await popState(page);
  (!ps.popped && !ps.open && ps.same && ps.focus === 'pop' && ps.desk)
    ? ok('Esc closes the large view, focus returns to the pop-out button, and the Desk stays open') : fail('after Esc: ' + JSON.stringify(ps));
  const docked = await turns(page);
  (docked.length === before0 + 2 && docked.slice(-2)[0][1] === 'sent from the large view')
    ? ok('the docked thread already has the exchange (same live thread)') : fail('docked: ' + JSON.stringify(docked.slice(-2)));
  await page.click('[data-chat-pop]');
  await page.mouse.click(4, 4);
  ps = await popState(page);
  !ps.popped ? ok('a click on the dimmed backdrop closes it') : fail('backdrop click did not close');
  await page.click('[data-chat-pop]');
  await page.click('[data-chat-pop]');
  ps = await popState(page);
  (!ps.popped && ps.focus === 'pop') ? ok('the button (now x) closes it too') : fail('button close: ' + JSON.stringify(ps));
  await page.click('[data-chat-pop]');
  const tabs = [];
  for (let i = 0; i < 8; i++) { await page.keyboard.press('Tab'); tabs.push(await page.evaluate(() => !!document.activeElement.closest('[data-chat]'))); }
  tabs.every(Boolean) ? ok('Tab stays inside the large view') : fail('focus escaped: ' + tabs.join(','));
  await page.keyboard.press('Escape');

  // 7. Screens: a thread long enough to scroll, at desktop and phone width.
  console.log('Screens');
  srv.reply = (b) => ({ reply: `About "${b.message}": the strongest scenes here each give the viewer one new fact. Scene 1 sets the problem, scene 2 shows the fix, scene 3 closes the loop. If I had to cut one it would be the pause, because the cursor already carries it.` });
  for (const m of ['is the opening too slow?', 'what would you cut first?']) {
    await ask.fill(m);
    await ask.press('Enter');
    await settle(page, (n) => !document.querySelector('[data-chat-thinking]') && [...document.querySelectorAll('[data-chat-turn="user"]')].some((e) => e.textContent.includes(n)), m);
  }
  mkdirSync(SHOTS, { recursive: true });
  const thread = await page.$eval('[data-chat-thread]', (t) => ({ sh: t.scrollHeight, ch: t.clientHeight, top: t.scrollTop }));
  (thread.sh > thread.ch && thread.top + thread.ch >= thread.sh - 2) ? ok(`a long thread scrolls inside its own region (${thread.sh}px in ${thread.ch}px) and stays at the newest turn`) : fail('thread scroll: ' + JSON.stringify(thread));
  await page.evaluate(() => document.querySelector('[data-sb-agent]').scrollIntoView({ block: 'start' }));
  await page.screenshot({ path: resolve(SHOTS, 'studio-agent-chat-1440.png') });
  ok('docs/desk_v1/screens/studio-agent-chat-1440.png');
  realErrors(pageErrors).forEach((e) => fail('page error: ' + e));
  dialogs.length === 0 ? ok('no window.confirm / alert was raised') : fail('dialogs: ' + dialogs.join('|'));
  await ctx.close();

  const m = await newPage(browser, srv, { width: 390, height: 844 });
  await m.page.evaluate(() => window.deskV1Nav('studio', {}));
  await m.page.waitForSelector(`[data-studio-recent-row="${itemId}"]`, { timeout: 8000 });
  await m.page.click(`[data-studio-recent-row="${itemId}"]`);
  await settle(m.page, (n) => document.querySelectorAll('[data-chat-turn]').length >= n, srv.threads[key()].length);
  const ov = await m.page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  const box = await m.page.$eval('[data-sb-agent]', (e) => { const r = e.getBoundingClientRect(); return { l: r.left, r: r.right, w: innerWidth }; });
  (ov <= 1 && box.l >= 0 && box.r <= box.w + 1) ? ok(`at 390px the agent panel fits the screen (page overflow ${ov}px)`) : fail('mobile overflow: ' + JSON.stringify({ ov, box }));
  await m.page.evaluate(() => document.querySelector('[data-sb-agent]').scrollIntoView({ block: 'start' }));
  await m.page.screenshot({ path: resolve(SHOTS, 'studio-agent-chat-390.png') });
  ok('docs/desk_v1/screens/studio-agent-chat-390.png');
  // the large view at phone width
  await m.page.evaluate(() => { window.__chat = document.querySelector('[data-chat]'); });
  await m.page.click('[data-chat-pop]');
  const mp = await popState(m.page);
  (mp.same && mp.popped && mp.open && mp.l >= 0 && mp.r <= mp.vw + 0.5 && mp.t >= 0 && mp.b <= mp.vh + 0.5 && mp.w >= 360 && (await hOverflow(m.page)) <= 1)
    ? ok(`at 390px the large view fills the screen (${Math.round(mp.w)}px wide) with no horizontal scroll`) : fail('mobile pop: ' + JSON.stringify(mp));
  const n390 = (await turns(m.page)).length;
  srv.reply = () => ({ reply: 'Phone reply.' });
  await m.page.fill('[data-sb-ask]', 'from my phone');
  await m.page.press('[data-sb-ask]', 'Enter');
  await settle(m.page, () => !document.querySelector('[data-chat-thinking]') && [...document.querySelectorAll('[data-chat-turn="agent"]')].some((e) => e.textContent.includes('Phone reply.')));
  (await turns(m.page)).length === n390 + 2 ? ok('sending from the large view works at 390px') : fail('mobile popped send');
  await m.page.screenshot({ path: resolve(SHOTS, 'studio-agent-chat-popout-390.png') });
  ok('docs/desk_v1/screens/studio-agent-chat-popout-390.png');
  await m.page.keyboard.press('Escape');
  const mc = await popState(m.page);
  (!mc.popped && mc.focus === 'pop' && (await hOverflow(m.page)) <= 1) ? ok('closing it at 390px returns to the docked panel, focus on the button') : fail('mobile close: ' + JSON.stringify(mc));
  const toolBox = await m.page.$eval('[data-chat-chrome]', (e) => { const r = e.getBoundingClientRect(); return { r: r.right, w: innerWidth, h: Math.min(...[...e.querySelectorAll('button')].map((b) => b.getBoundingClientRect().height)) }; });
  (toolBox.r <= toolBox.w && toolBox.h >= 32) ? ok('the A-/A+/pop-out strip fits at 390px with 32px+ targets') : fail('mobile strip: ' + JSON.stringify(toolBox));
  realErrors(m.pageErrors).forEach((e) => fail('page error (390): ' + e));
  await m.ctx.close();
}

const browser = await chromium.launch();
try { await main(browser); } catch (e) { fail('smoke crashed: ' + (e && e.stack || e)); } finally { await browser.close(); }
if (bad) { console.error(`\n${bad} check(s) FAILED`); process.exit(1); }
console.log('\nAll checks passed.');
