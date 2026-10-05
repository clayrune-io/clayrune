#!/usr/bin/env node
/**
 * Desk Studio (Ron 2026-10-03): the STORY field, "Make storyboard from story", the
 * real "Ask your agent" box, and the per-scene instruction editor. A fake server
 * stands in for /api/desk/* and the model call (mc/desk_story.py is pinned by
 * tests/test_desk_story.py); what is under test is what the browser sends and what
 * it paints from the answer.
 *
 *   1. Story    -> a pasted story grows the box, is saved with the board (PUT carries
 *                  `story`), and a reload brings it back.
 *   2. Make     -> empty board: no prompt, POST generate {mode:'board', story}; the scenes
 *                  appear, are saved, and ONE Undo takes them away again.
 *   3. Choice   -> a board with scenes asks replace / append INLINE (no window.confirm);
 *                  Append keeps the scenes and their pictures; Cancel sends nothing.
 *   4. Failure  -> a refused generation changes nothing and says so.
 *   5. Ask      -> with a scene selected, Enter POSTs {mode:'scene', scene, instruction} and
 *                  only that scene changes (undoable); with none, the whole board is sent.
 *                  The old "Sent to X" toast never appears.
 *   6. Scene    -> the selected scene's instructions are a multi-line textarea (1800 chars
 *                  fit and save; 2100 is refused in the editor, text kept).
 *   7. Shots    -> screens at 1440 and 390 into docs/desk_v1/screens/.
 *
 * RUN   cd tools/smoke && node desk-v1-story.mjs
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
  const srv = { log: [], boards: {}, fx, gen: null };
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

const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });
const puts = (srv, re) => srv.log.filter((r) => r.method === 'PUT' && re.test(r.path));
const gens = (srv) => srv.log.filter((r) => r.path === '/api/desk/storyboard/generate');
const realErrors = (pageErrors) => pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
const labels = (page) => page.$$eval('[data-storyboard] .desk-v1-sb-scene', (els) => els.map((e) => e.dataset.sceneLabel));
const toasts = (page) => page.$$eval('.toast', (els) => els.map((e) => e.textContent)).catch(() => []);

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

  // 1. The story field.
  console.log('The story field');
  (await page.$('[data-sb-story-panel] label[for="sb-story"]')) ? ok('the board has a labelled story box') : fail('no story box');
  const h0 = await page.$eval('[data-sb-story]', (t) => t.offsetHeight);
  await page.fill('[data-sb-story]', STORY);
  const h1 = await page.$eval('[data-sb-story]', (t) => t.offsetHeight);
  h1 > h0 ? ok(`the box grows with the text (${h0}px -> ${h1}px)`) : fail(`the box did not grow: ${h0} -> ${h1}`);
  const cnt = await page.textContent('[data-story-count]');
  cnt.startsWith(STORY.length.toLocaleString('en-US')) ? ok(`the count reads ${cnt}`) : fail('count: ' + cnt);
  await page.waitForTimeout(1000);
  (puts(srv, /storyboard$/).length >= 1 && srv.boards[key()] && srv.boards[key()].story === STORY)
    ? ok('the story is saved with the board (a PUT carrying `story`), with no scene yet') : fail('story not stored: ' + JSON.stringify(Object.values(srv.boards).map((b) => (b.story || '').length)));
  (await page.textContent('[data-story-save]')) === 'Saved' ? ok('the panel says Saved') : fail('save line: ' + (await page.textContent('[data-story-save]')));

  // 2. Make a storyboard: empty board, no prompt.
  console.log('Make storyboard from story');
  const cost = await page.textContent('[data-story-cost]');
  /2¢/.test(cost) ? ok(`the cost note is shown before it runs: "${cost}"`) : fail('no cost note: ' + cost);
  srv.gen = (body, J) => J({ agent: null, provider: 'claude', model: 'sonnet', mode: 'board', scenes: MADE.map((s) => ({ ...s, id: 'g' + (++GID), picture: null, edited: false })) });
  srv.log.length = 0;
  await page.click('[data-story-make]');
  await settle(page, () => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === 3);
  await page.waitForTimeout(400);
  const g = gens(srv);
  (g.length === 1 && g[0].body.mode === 'board' && g[0].body.story === STORY && !g[0].body.scenes && !g[0].body.instruction)
    ? ok('one POST generate {mode:"board", story}') : fail('generate body: ' + JSON.stringify(g.map((r) => Object.keys(r.body || {}))));
  JSON.stringify(await labels(page)) === JSON.stringify(MADE.map((s) => s.label)) ? ok('the three scenes are painted in order') : fail('rows: ' + JSON.stringify(await labels(page)));
  dialogs.length === 0 ? ok('no window.confirm / alert was raised') : fail('dialogs: ' + dialogs.join('|'));
  const stored = srv.boards[key()].scenes;
  (stored.length === 3 && stored[0].line === MADE[0].line && stored[0].duration_sec === 4)
    ? ok('the scenes are saved with their full instructions and durations') : fail('stored: ' + JSON.stringify(stored.map((s) => s.label)));
  /wrote 3 scenes/.test(await page.textContent('[data-story-status]')) ? ok('the status line is written from the answer: ' + JSON.stringify(await page.textContent('[data-story-status]'))) : fail('status: ' + (await page.textContent('[data-story-status]')));
  const undoTitle = (await page.getAttribute('[data-sb-undo]', 'title')) || '';
  /Made a storyboard from the story/.test(undoTitle) ? ok(`Undo names it: "${undoTitle}"`) : fail('undo title: ' + undoTitle);
  await page.click('[data-sb-undo]');
  await settle(page, () => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === 0);
  await page.waitForTimeout(400);
  (srv.boards[key()].scenes.length === 0 && srv.boards[key()].story === STORY)
    ? ok('ONE Undo removes all three scenes (and the story stays)') : fail('after undo: ' + srv.boards[key()].scenes.length + ' scenes');

  // 3. Replace vs append, inline.
  console.log('Replace or append');
  await page.click('[data-story-make]');
  await settle(page, () => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === 3);
  await page.waitForTimeout(300);
  const chooser = page.waitForEvent('filechooser');
  await page.click('.desk-v1-sb-scene[data-scene-label="The click"] [data-scene-picture]');
  await (await chooser).setFiles(PNG);
  await settle(page, () => !!document.querySelector('.desk-v1-sb-scene[data-scene-label="The click"] .desk-v1-sb-thumb img'));
  srv.log.length = 0;
  await page.click('[data-story-make]');
  await page.waitForSelector('[data-story-choice]:not([hidden])', { timeout: 4000 });
  const choiceText = await page.textContent('[data-story-choice]');
  (/already has 3 scenes/.test(choiceText) && (await page.$('[data-story-replace]')) && (await page.$('[data-story-append]')))
    ? ok('a board with scenes asks inline: ' + JSON.stringify(choiceText.replace(/\s+/g, ' ').trim())) : fail('choice: ' + choiceText);
  gens(srv).length === 0 ? ok('nothing is sent before the choice') : fail('generate fired before the choice');
  await page.click('[data-story-cancel]');
  (await page.$('[data-story-choice]:not([hidden])')) === null && gens(srv).length === 0 ? ok('Cancel sends nothing and closes the choice') : fail('cancel did not close');
  await page.click('[data-story-make]');
  await page.click('[data-story-append]');
  await settle(page, () => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === 6);
  await page.waitForTimeout(400);
  const kept = srv.boards[key()].scenes;
  (kept.length === 6 && kept[1].picture && kept[1].picture.path.endsWith('hero.png') && !kept[4].picture)
    ? ok('Append keeps the existing scenes and their picture, and adds three after them') : fail('append result: ' + JSON.stringify(kept.map((s) => [s.label, !!s.picture])));
  await page.click('[data-sb-undo]');
  await settle(page, () => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === 3);
  ok('Undo of the append brings back exactly the earlier three');
  await page.click('[data-story-make]');
  await page.click('[data-story-replace]');
  await settle(page, () => !document.querySelector('.desk-v1-sb-scene[data-scene-label="The click"] .desk-v1-sb-thumb img'));
  await page.waitForTimeout(300);
  (srv.boards[key()].scenes.length === 3 && srv.boards[key()].scenes.every((s) => !s.picture))
    ? ok('Replace swaps the scenes for the new three (the old picture is not carried over)') : fail('replace result');

  // 4. A refused generation changes nothing.
  console.log('A refused generation');
  srv.gen = (body, J) => J({ error: 'the model call failed, so nothing was changed: timed out' }, 502);
  const before = JSON.stringify(await labels(page));
  srv.log.length = 0;
  await page.click('[data-story-make]');
  await page.click('[data-story-replace]');
  await settle(page, () => /Nothing was changed/.test((document.querySelector('[data-story-status]') || {}).textContent || ''));
  (JSON.stringify(await labels(page)) === before && puts(srv, /storyboard$/).length === 0)
    ? ok('the scenes and the saved board are untouched: ' + JSON.stringify(await page.textContent('[data-story-status]'))) : fail('a failed call changed the board');
  (await page.inputValue('[data-sb-story]')) === STORY ? ok('the typed story is still in the box') : fail('story lost');

  // 5. The ask box.
  console.log('The ask box');
  const ask = page.locator('[data-sb-ask]');
  (await ask.evaluate((el) => el.tagName)) === 'TEXTAREA' ? ok('the ask box is a multi-line textarea') : fail('ask is not a textarea');
  (await page.textContent('[data-sb-ask-status]')).includes('whole storyboard') ? ok('with no scene selected it says it changes the whole storyboard') : fail('hint: ' + (await page.textContent('[data-sb-ask-status]')));
  await page.click('.desk-v1-sb-scene[data-scene-label="The click"] [data-scene-title]');
  await settle(page, () => /Changes scene 2/.test((document.querySelector('[data-sb-ask-status]') || {}).textContent || ''));
  ok('selecting scene 2 changes the hint to "Changes scene 2"');
  srv.gen = (body, J) => J({ agent: { ref: 'global:dave', name: 'Dave' }, provider: 'claude', model: 'sonnet', mode: 'scene', scene: { label: 'The click, slower', line: 'Slow push-in on the button for two seconds, then the click.', duration_sec: 6 } });
  srv.log.length = 0;
  await ask.fill('make this slower and more dramatic');
  await ask.press('Enter');
  await settle(page, () => !!document.querySelector('.desk-v1-sb-scene[data-scene-label="The click, slower"]'));
  await page.waitForTimeout(400);
  const sg = gens(srv);
  (sg.length === 1 && sg[0].body.mode === 'scene' && sg[0].body.instruction === 'make this slower and more dramatic' && sg[0].body.scene.label === 'The click' && sg[0].body.story === STORY)
    ? ok('Enter POSTs {mode:"scene", story, scene, instruction}') : fail('scene body: ' + JSON.stringify(sg.map((r) => r.body)));
  JSON.stringify(await labels(page)) === JSON.stringify(['Cold open', 'The click, slower', 'Done']) ? ok('only scene 2 changed') : fail('rows: ' + JSON.stringify(await labels(page)));
  srv.boards[key()].scenes[1].duration_sec === 6 ? ok('the change is saved (PUT) with the new duration') : fail('not saved');
  (await ask.inputValue()) === '' ? ok('the box is cleared only after it worked') : fail('ask not cleared');
  /Dave changed scene 2/.test(await page.textContent('[data-sb-ask-status]')) ? ok('the status names who answered: ' + JSON.stringify(await page.textContent('[data-sb-ask-status]'))) : fail('ask status: ' + (await page.textContent('[data-sb-ask-status]')));
  const all = (await toasts(page)).join(' | ');
  !/Sent to/.test(all) ? ok('no "Sent to…" toast') : fail('the fake toast is back: ' + all);
  await page.click('[data-sb-undo]');
  await settle(page, () => !!document.querySelector('.desk-v1-sb-scene[data-scene-label="The click"]'));
  await page.waitForTimeout(300);
  srv.boards[key()].scenes[1].label === 'The click' ? ok('Undo puts scene 2 back, and saves it') : fail('undo did not restore');
  // A refused ask keeps the typed request.
  srv.gen = (body, J) => J({ error: 'the model call failed, so nothing was changed: boom' }, 502);
  await ask.fill('another change');
  await ask.press('Enter');
  await settle(page, () => /Nothing was changed/.test((document.querySelector('[data-sb-ask-status]') || {}).textContent || ''));
  (await ask.inputValue()) === 'another change' ? ok('a failed ask keeps the request in the box and says nothing was changed') : fail('request lost');
  // Whole board: deselect (a second click on the row), then ask.
  await page.click('.desk-v1-sb-scene[data-scene-label="The click"] [data-scene-title]');
  await settle(page, () => /whole storyboard/.test((document.querySelector('[data-sb-ask-status]') || {}).textContent || ''));
  srv.gen = (body, J) => J({ agent: null, provider: 'claude', model: 'sonnet', mode: 'board', scenes: [
    { label: 'Cold open', line: 'New cold open.', duration_sec: 2, from: 1 }, { label: 'Extra', line: 'A new scene.', duration_sec: 2, from: null }, { label: 'Done', line: 'Same end.', duration_sec: 5, from: 3 }] });
  srv.log.length = 0;
  await ask.fill('tighten the whole thing');
  await ask.press('Enter');
  await settle(page, () => !!document.querySelector('.desk-v1-sb-scene[data-scene-label="Extra"]'));
  await page.waitForTimeout(400);
  const bg = gens(srv)[0].body;
  (bg.mode === 'board' && bg.instruction === 'tighten the whole thing' && bg.scenes.length === 3 && bg.scenes[1].label === 'The click')
    ? ok('with none selected the whole board goes with the instruction') : fail('board ask body: ' + JSON.stringify(bg));
  JSON.stringify(await labels(page)) === JSON.stringify(['Cold open', 'Extra', 'Done']) ? ok('the whole board is replaced by the revision') : fail('rows: ' + JSON.stringify(await labels(page)));
  await page.click('[data-sb-undo]');
  await settle(page, () => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === 3 && !!document.querySelector('.desk-v1-sb-scene[data-scene-label="The click"]'));
  ok('Undo of the whole-board change restores the three scenes');

  // 6. A scene's instructions are a textarea.
  console.log('Per-scene instructions');
  await page.click('.desk-v1-sb-scene[data-scene-label="Cold open"] [data-scene-edit]');
  const lineBox = page.locator('.desk-v1-sb-scene:has([data-scene-edit-line]) textarea[data-scene-edit-line]');
  (await lineBox.count()) === 1 ? ok('editing a scene shows its instructions in a textarea') : fail('no instruction textarea');
  const long = ('Wide shot of the desk, slow push in. ').repeat(60).slice(0, 1799) + '.';
  const lh0 = await lineBox.evaluate((t) => t.offsetHeight);
  await lineBox.fill(long);
  const lh1 = await lineBox.evaluate((t) => t.offsetHeight);
  lh1 > lh0 ? ok(`it grows with the text (${lh0}px -> ${lh1}px)`) : fail(`no growth: ${lh0} -> ${lh1}`);
  (await page.textContent('[data-line-count]')).startsWith('1,800 / 2,000') ? ok('the count reads 1,800 / 2,000') : fail('line count: ' + (await page.textContent('[data-line-count]')));
  srv.log.length = 0;
  await page.click('.desk-v1-sb-scene:has([data-scene-edit-line]) [data-scene-edit]');
  await settle(page, () => !document.querySelector('[data-scene-edit-line]'));
  await page.waitForTimeout(400);
  srv.boards[key()].scenes[0].line === long ? ok('the 1,800-character direction is saved whole') : fail('stored line length ' + srv.boards[key()].scenes[0].line.length);
  await page.click('.desk-v1-sb-scene[data-scene-label="Cold open"] [data-scene-edit]');
  await lineBox.fill('x'.repeat(2100));
  srv.log.length = 0;
  await page.click('.desk-v1-sb-scene:has([data-scene-edit-line]) [data-scene-edit]');
  const err = (await page.textContent('[data-scene-edit-error]')) || '';
  (/100 characters over the 2,000 limit/.test(err) && (await lineBox.count()) === 1 && puts(srv, /storyboard$/).length === 0 && (await lineBox.inputValue()).length === 2100)
    ? ok('2,100 characters is refused in the editor, with the text kept: ' + JSON.stringify(err)) : fail('over-limit handling: ' + err);
  await lineBox.fill('short again');
  await page.click('.desk-v1-sb-scene:has([data-scene-edit-line]) [data-scene-edit]');
  await settle(page, () => !document.querySelector('[data-scene-edit-line]'));

  // Reload: a fresh page opens the draft from Recent with the story and scenes back.
  await page.waitForTimeout(300);
  const itemId = key().replace(/^studio:/, '');
  const fresh = await newPage(browser, srv, { width: 1440, height: 950 });
  await fresh.page.evaluate(() => window.deskV1Nav('studio', {}));
  await fresh.page.waitForSelector(`[data-studio-recent-row="${itemId}"]`, { timeout: 8000 });
  await fresh.page.click(`[data-studio-recent-row="${itemId}"]`);
  await fresh.page.waitForSelector('[data-sb-story]:not([disabled])', { timeout: 8000 });
  await fresh.page.waitForFunction(() => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === 3, null, { timeout: 8000 });
  (await fresh.page.inputValue('[data-sb-story]')) === STORY ? ok('after a reload the story is back in the box') : fail('reloaded story differs');
  JSON.stringify(await labels(fresh.page)) === JSON.stringify(['Cold open', 'The click', 'Done']) ? ok('and the scenes are back in order') : fail('reloaded rows: ' + JSON.stringify(await labels(fresh.page)));
  realErrors(fresh.pageErrors).forEach((e) => fail('page error (reload): ' + e));
  await fresh.ctx.close();

  // 7. Nothing typed in an open scene is lost: every way out of the editor saves it first.
  console.log('Typing is saved before leaving the editor');
  const oneEditor = (label) => page.evaluate((l) => {
    const open = [...document.querySelectorAll('.desk-v1-sb-scene')].filter((li) => li.querySelector('[data-scene-edit-line]'));
    return open.length === 1 && open[0].dataset.sceneLabel === l;
  }, label);
  await page.click('.desk-v1-sb-scene[data-scene-label="The click"] [data-scene-edit]');
  await page.fill('.desk-v1-sb-scene[data-scene-label="The click"] [data-scene-edit-line]', 'Scene two: typed, and Done was never clicked.');
  srv.log.length = 0;
  await page.click('.desk-v1-sb-scene[data-scene-label="Done"] [data-scene-edit]');
  await settle(page, () => !!document.querySelector('.desk-v1-sb-scene[data-scene-label="Done"] [data-scene-edit-line]'));
  await page.waitForTimeout(400);
  (puts(srv, /storyboard$/).length >= 1 && srv.boards[key()].scenes[1].line === 'Scene two: typed, and Done was never clicked.')
    ? ok(`Edit on scene 3 without Done: scene 2's line was saved (${puts(srv, /storyboard$/).length} PUT) and is on the server`) : fail('scene 2 line: ' + srv.boards[key()].scenes[1].line);
  (await oneEditor('Done')) ? ok('scene 3 is open, and it is the only editor open') : fail('editor state after switching scenes');

  // Over the limit: the text stays, the editor stays, no PUT; the click that was refused does nothing.
  await page.fill('.desk-v1-sb-scene[data-scene-label="Done"] [data-scene-edit-line]', 'y'.repeat(2100));
  srv.log.length = 0;
  await page.evaluate(() => document.querySelector('.desk-v1-sb-scene[data-scene-label="Cold open"] [data-scene-edit]').click());
  const err2 = (await page.textContent('.desk-v1-sb-scene[data-scene-label="Done"] [data-scene-edit-error]')) || '';
  (puts(srv, /storyboard$/).length === 0 && (await oneEditor('Done')) && /100 characters over/.test(err2) &&
    (await page.inputValue('.desk-v1-sb-scene[data-scene-label="Done"] [data-scene-edit-line]')).length === 2100)
    ? ok('over the limit: Edit on another scene is refused, the editor stays open with all 2,100 characters') : fail('over-limit switch: ' + err2);
  await page.evaluate(() => document.querySelector('[data-scene-add]').click());
  (puts(srv, /storyboard$/).length === 0 && (await oneEditor('Done')) && (await labels(page)).length === 3)
    ? ok('over the limit: Add scene is refused too, nothing was added or saved') : fail('Add scene went through over the limit');

  // Under the limit: Add scene (a click that does not blur the box) saves it, then opens the new one.
  await page.fill('.desk-v1-sb-scene[data-scene-label="Done"] [data-scene-edit-line]', 'Scene three: typed before Add scene.');
  srv.log.length = 0;
  await page.evaluate(() => document.querySelector('[data-scene-add]').click());
  await settle(page, () => !!document.querySelector('.desk-v1-sb-scene[data-scene-label="New scene"] [data-scene-edit-line]'));
  await page.waitForTimeout(400);
  (srv.boards[key()].scenes[2].line === 'Scene three: typed before Add scene.' && srv.boards[key()].scenes.length === 4 && (await oneEditor('New scene')))
    ? ok('Add scene saved scene 3 first, then opened the new scene (4 scenes stored)') : fail('Add scene: ' + JSON.stringify(srv.boards[key()].scenes.map((s) => [s.label, s.line.slice(0, 20)])));

  // Deleting a different scene while one is open saves the open one.
  await page.fill('.desk-v1-sb-scene[data-scene-label="New scene"] [data-scene-edit-line]', 'Scene four: typed before a delete.');
  await page.evaluate(() => document.querySelector('.desk-v1-sb-scene[data-scene-label="Cold open"] [data-scene-delete]').click());
  await settle(page, () => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === 3);
  await page.waitForTimeout(400);
  const afterDel = srv.boards[key()].scenes;
  (afterDel.length === 3 && afterDel[2].line === 'Scene four: typed before a delete.')
    ? ok('deleting another scene saved the open one first') : fail('delete: ' + JSON.stringify(afterDel.map((s) => [s.label, s.line.slice(0, 20)])));
  await page.click('.desk-v1-sb-scene:has([data-scene-edit-line]) [data-scene-edit]');
  await settle(page, () => !document.querySelector('[data-scene-edit-line]'));

  // Leaving the box (blur) saves without waiting for a click on anything.
  await page.click('.desk-v1-sb-scene[data-scene-label="The click"] [data-scene-edit]');
  await page.fill('.desk-v1-sb-scene[data-scene-label="The click"] [data-scene-edit-line]', 'Typed, then focus moved away.');
  srv.log.length = 0;
  await page.click('[data-sb-story]');
  await page.waitForTimeout(500);
  (srv.boards[key()].scenes[0].line === 'Typed, then focus moved away.' && puts(srv, /storyboard$/).length >= 1 && (await oneEditor('The click')))
    ? ok('blur saved the instructions (PUT) and left the editor open') : fail('blur save: ' + srv.boards[key()].scenes[0].line);
  await page.click('.desk-v1-sb-scene:has([data-scene-edit-line]) [data-scene-edit]');
  await settle(page, () => !document.querySelector('[data-scene-edit-line]'));

  // 8. The real story (docs/LEARN_STORYBOARD_GEMINI.md) fits, and long answers keep every line whole.
  console.log('The real story');
  const REAL = readFileSync(resolve(REPO_ROOT, 'docs', 'LEARN_STORYBOARD_GEMINI.md'), 'utf8').replace(/\r\n/g, '\n');
  await page.fill('[data-sb-story]', REAL);
  await page.waitForTimeout(1000);
  (REAL.length < 20000 && srv.boards[key()].story === REAL)
    ? ok(`the whole file (${REAL.length.toLocaleString('en-US')} characters) is saved under the 20,000 limit, byte for byte`) : fail(`story saved ${(srv.boards[key()].story || '').length} of ${REAL.length}`);
  const realCount = await page.textContent('[data-story-count]');
  realCount.startsWith(REAL.length.toLocaleString('en-US')) ? ok(`the count reads ${realCount}`) : fail('real story count: ' + realCount);
  const LONG = [0, 1, 2, 3].map((i) => (REAL.slice(i * 1300, i * 1300 + 1800).trim() + ` «END ${i + 1}»`));
  srv.gen = (body, J) => J({ agent: null, provider: 'claude', model: 'sonnet', mode: 'board', scenes: LONG.map((line, i) => ({ label: `Long ${i + 1}`, line, duration_sec: 4, id: 'g' + (++GID), picture: null, edited: false })) });
  srv.log.length = 0;
  await page.click('[data-story-make]');
  await page.click('[data-story-replace]');
  await settle(page, () => !!document.querySelector('.desk-v1-sb-scene[data-scene-label="Long 4"]'));
  await page.waitForTimeout(500);
  const g2 = gens(srv);
  (g2.length === 1 && g2[0].body.story === REAL) ? ok('the model call carried the whole story') : fail('generate story length ' + (g2[0] && g2[0].body.story.length));
  const storedLong = srv.boards[key()].scenes.map((s) => s.line);
  (JSON.stringify(storedLong) === JSON.stringify(LONG))
    ? ok(`all 4 long lines are saved whole (${LONG.map((l) => l.length).join(', ')} characters)`) : fail('stored lines differ: ' + storedLong.map((l) => l.length).join(','));
  const shown = await page.$$eval('[data-storyboard] .desk-v1-sb-line', (els) => els.map((e) => e.textContent));
  (JSON.stringify(shown) === JSON.stringify(LONG)) ? ok('each scene paints its whole line, to the «END n» marker') : fail('painted lines differ: ' + shown.map((l) => l.length).join(','));
  await page.click('.desk-v1-sb-scene[data-scene-label="Long 2"] [data-scene-edit]');
  (await page.inputValue('.desk-v1-sb-scene[data-scene-label="Long 2"] [data-scene-edit-line]')) === LONG[1]
    ? ok('the scene editor opens on the whole line') : fail('editor value differs from the line');
  srv.log.length = 0;
  await page.click('.desk-v1-sb-scene[data-scene-label="Long 2"] [data-scene-edit]');
  await settle(page, () => !document.querySelector('[data-scene-edit-line]'));
  await page.waitForTimeout(300);
  (JSON.stringify(srv.boards[key()].scenes.map((s) => s.line)) === JSON.stringify(LONG))
    ? ok('opening and closing the editor on a long line changes nothing') : fail('long line altered by the editor');

  realErrors(pageErrors).forEach((e) => fail('page error: ' + e));
  await ctx.close();
  return srv;
}

// A library read that lands after the user already opened New video used to repaint the
// Studio home over the create page (both paint into the one route container), so the
// storyboard vanished a moment after it appeared. Gate the read, open New video, release it.
async function lateLibraryRead(browser) {
  console.log('A late library read');
  const srv = makeServer();
  let release;
  srv.materialsGate = new Promise((r) => { release = r; });
  const ctx0 = await newPage(browser, srv, { width: 1440, height: 950 });
  const { ctx, page } = ctx0;
  await page.evaluate(() => window.deskV1Nav('studio', {}));
  await page.waitForSelector('[data-studio-new="video"]', { timeout: 8000 });
  await page.evaluate(() => window.deskV1Nav('studio-create', { kind: 'video' }));
  await page.waitForSelector('[data-studio-create][data-kind="video"] [data-storyboard]', { timeout: 8000 });
  release();
  await page.waitForTimeout(600);
  const state = await page.evaluate(() => ({
    create: !!document.querySelector('[data-studio-create][data-kind="video"] [data-storyboard]'),
    home: !!document.querySelector('[data-studio] [data-studio-new]'),
  }));
  (state.create && !state.home)
    ? ok('New video stays on screen after the Studio library read lands') : fail(`the late read repainted Studio home over New video (storyboard=${state.create}, home=${state.home})`);
  realErrors(ctx0.pageErrors).forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

async function shots(browser) {
  mkdirSync(SHOTS, { recursive: true });
  for (const [w, vp] of [['1440', { width: 1440, height: 1100 }], ['390', { width: 390, height: 900 }]]) {
    const srv = makeServer();
    const { ctx, page } = await newPage(browser, srv, vp);
    await studioNewVideo(page);
    srv.gen = (body, J) => J({ agent: null, provider: 'claude', model: 'sonnet', mode: 'board', scenes: MADE.map((s) => ({ ...s, id: 'g' + (++GID), picture: null, edited: false })) });
    await page.fill('[data-sb-story]', STORY.split('\n').slice(0, 8).join('\n'));
    await page.click('[data-story-make]');
    await settle(page, () => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === 3);
    await page.click('.desk-v1-sb-scene[data-scene-label="Cold open"] [data-scene-edit]');
    await page.waitForTimeout(300);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    overflow <= 1 ? ok(`${w}: no horizontal overflow`) : fail(`${w}: ${overflow}px horizontal overflow`);
    await page.evaluate(() => { const b = document.querySelector('[data-storyboard]'); for (let n = b; n; n = n.parentElement) n.scrollTop = 0; });
    await page.screenshot({ path: resolve(SHOTS, `story_storyboard_${w}.png`) });
    await page.locator('.desk-v1-sb-scene:has([data-scene-edit-line])').scrollIntoViewIfNeeded();
    await page.screenshot({ path: resolve(SHOTS, `story_scene_editor_${w}.png`) });
    ok(`screenshots saved: story_storyboard_${w}.png, story_scene_editor_${w}.png`);
    await ctx.close();
  }
}

const browser = await chromium.launch();
try {
  await main(browser);
  await lateLibraryRead(browser);
  console.log('Screenshots');
  await shots(browser);
} catch (e) {
  fail('smoke crashed: ' + (e && e.stack ? e.stack : e));
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) FAILED` : '\nAll desk-v1-story checks passed');
process.exit(bad ? 1 : 0);
