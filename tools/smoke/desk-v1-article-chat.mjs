#!/usr/bin/env node
// Real writer/Posy UI; hermetic API and model replies. No live Desk writes.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const html = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const files = loadStaticJsCss(root);
const shots = resolve(root, '_scratch/article-chat');
mkdirSync(shots, { recursive: true });
const origin = 'http://mc.smoke.test';
const replacement = 'Local tools keep the work close.\nA proposed opening, ready for your review.';

async function run(browser, width) {
  const fx = loadFixtures();
  const workspace = workspaceFromFixtures(fx);
  workspace.projects[0].presence = { ...workspace.projects[0].presence, desk_agent: 'global:planner' };
  const articles = {};
  const chats = {};
  const posts = [];
  let refusal = false, release = null, defer = false;
  const context = await browser.newContext({ viewport: { width, height: 950 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route('**/*', async (route) => {
    const req = route.request(), path = new URL(req.url()).pathname, method = req.method();
    const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/') return route.fulfill({ contentType: 'text/html', body: html });
    if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true });
    if (path === '/api/projects') return J(workspace.projects.map((p) => ({ ...p, status: 'active', roster: [], project_path: '/smoke/' + p.id })));
    if (path === '/api/characters') return J([{ scope: 'global', name: 'planner', agent_name: 'The Planner', avatar: '🪶' }]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (path === '/api/desk/workspace') return J(workspace);
    if (path === '/api/desk/materials') return J({ library: { video: [], image: [] }, recent: [] });
    if (path === '/api/desk/studio/storyboards') return J({ storyboards: [] });
    if (path === '/api/desk/studio/articles') return J({ articles: Object.values(articles) });
    const match = path.match(/^\/api\/desk\/studio\/articles\/([^/]+)(?:\/chat(?:\/([^/]+))?)?$/);
    if (!match) return route.abort();
    const id = match[1];
    const body = req.postData() ? JSON.parse(req.postData()) : null;
    if (!path.includes('/chat')) {
      if (method === 'GET') return J(articles[id] || { error: 'not found' }, articles[id] ? 200 : 404);
      if (method === 'PUT') {
        const a = articles[id];
        if (a && a.rev !== body.rev) return J({ error: 'stale revision' }, 409);
        articles[id] = { ...body, id, rev: body.rev + 1, attached: false };
        return J(articles[id]);
      }
    }
    if (method === 'GET') return J({ thread: chats[id] || [] });
    if (match[2]) {
      const a = articles[id], turn = chats[id].find((t) => t.id === match[2]);
      const p = turn.proposal, tab = a.tabs.find((t) => t.id === p.tab_id);
      const before = body.action === 'undo' ? p.body : p.before;
      if (body.rev !== a.rev || tab.body !== before) return J({ error: 'draft changed' }, 409);
      tab.body = body.action === 'undo' ? p.before : p.body;
      p.status = body.action === 'undo' ? 'undone' : 'applied';
      a.rev++;
      return J({ article: a, turn });
    }
    posts.push({ id, body });
    if (refusal) { refusal = false; return J({ error: 'test model unavailable' }, 502); }
    if (defer) { defer = false; await new Promise((r) => { release = r; }); }
    const turns = [{ id: 'user-' + posts.length, role: 'user', text: body.message },
      { id: 'agent-' + posts.length, role: 'agent', agent: articles[id].project_id ? 'The Planner' : 'Studio agent',
        text: 'Here is a proposed opening. You can accept it or keep writing.',
        proposal: { status: 'proposed', tab_id: body.tab_id, label: 'Draft', before: body.body, body: replacement } }];
    (chats[id] ||= []).push(...turns);
    return J({ turns, agent: { name: turns[1].agent } });
  });
  const wait = (fn, arg) => page.waitForFunction(fn, arg, { timeout: 10000 });
  const chat = () => page.locator('[data-sa-agent] textarea');
  const editor = () => page.locator('[data-writer-body]');
  async function open(id) {
    await page.evaluate((itemId) => window.deskV1Nav('studio-create', { itemId }), id);
    await page.waitForSelector('[data-sa-agent] textarea:not(:disabled)');
  }
  async function create(title, project = '') {
    await page.evaluate(() => window.deskV1Nav('studio-create', { kind: 'article' }));
    await page.fill('[data-sa-topic]', title);
    if (project) await page.selectOption('[data-sa-project]', project);
    await page.click('[data-sa-start]');
    await page.waitForSelector('[data-sa-agent] textarea:not(:disabled)');
    return page.getAttribute('[data-view="writer"]', 'data-item-id');
  }
  async function send(message) {
    await chat().fill(message);
    await chat().press('Enter');
    await page.waitForSelector('[data-chat-thinking]', { state: 'detached' });
    await page.waitForSelector('[data-sa-agent] textarea:not(:disabled)');
  }
  await page.goto(origin);
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social'));
  await wait(() => window.DeskV1Store.state().projects.length > 0);
  const id = await create('Why local tools matter');
  assert.match(await page.locator('[data-sa-agent] .desk-thread-head').textContent(), /Studio agent.*Why local tools matter/s);
  await editor().fill('Original text with [[image:example.png|A real screenshot]]');
  await send('Please rewrite the opening');
  assert.equal(posts[0].body.body, 'Original text with [[image:example.png|A real screenshot]]');
  assert.equal(posts[0].body.tab_id, 'tab-draft');
  assert.match(await editor().textContent(), /^Original/);
  assert.equal(await page.locator('[data-article-accept]').count(), 1);
  await page.waitForFunction(() => document.querySelector('[data-sa-status]').textContent === 'Saved');
  await page.locator('[data-sa-agent]').scrollIntoViewIfNeeded();
  await page.screenshot({ path: resolve(shots, `default_${width}.png`) });
  await page.click('[data-article-accept]');
  await wait(() => document.querySelector('[data-writer-body]').textContent.startsWith('Local tools'));
  assert.equal(articles[id].tabs[0].body, replacement);
  await open(id); // Undo must use this new writer bridge, not the detached one.
  // Studio's library refresh can replace an off-screen article's cache object.
  // Undo from another draft must update that cache without repainting this one.
  await create('An unrelated draft');
  await page.evaluate(() => window.DeskV1StudioArticle.load());
  await page.click('#desk-v1-undo');
  await page.waitForFunction((original) => window.DeskV1StudioArticle.find(original).draft.tabs[0].body.startsWith('Original'), id);
  assert.equal(await editor().textContent(), '');
  await open(id);
  await wait(() => document.querySelector('[data-writer-body]').textContent.startsWith('Original'));
  assert.match(articles[id].tabs[0].body, /^Original/);
  // Reopen the same draft; unsent text and the thread stay scoped to this id.
  await chat().fill('An unsent follow-up');
  await open(id);
  assert.equal(await chat().inputValue(), 'An unsent follow-up');
  assert.equal(await page.locator('[data-chat-turn]').count(), 2);
  refusal = true;
  await send('A failed request');
  assert.equal(await chat().inputValue(), 'A failed request');
  assert.match(await page.locator('[data-article-chat-status]').textContent(), /test model unavailable/);
  await send('A successful retry');
  await editor().fill('Manual edits after asking');
  await page.locator('[data-article-accept]').click();
  assert.equal(await editor().textContent(), 'Manual edits after asking');
  assert.match(await page.locator('[data-article-chat-status]').textContent(), /draft changed/i);
  // A late answer cannot appear in the other draft, including same-title drafts.
  defer = true;
  await chat().fill('A delayed request');
  await chat().press('Enter');
  await wait(() => !!document.querySelector('[data-chat-thinking]'));
  const other = await create('Why local tools matter', workspace.projects[0].id);
  assert.notEqual(other, id);
  assert.match(await page.locator('[data-sa-agent] .desk-thread-head').textContent(), /The Planner/);
  release();
  await page.waitForTimeout(100);
  assert.equal(await page.locator('[data-chat-turn]').count(), 0);
  await editor().fill('Another project, another draft.');
  await send('Draft a better introduction');
  assert.equal(posts.at(-1).id, other);
  assert.equal(posts.at(-1).body.body, 'Another project, another draft.');
  assert.match(await editor().textContent(), /Another project/);
  // Shared conversation controls remain live in the large view.
  await page.click('[data-sa-agent] [data-chat-pop]');
  assert.equal(await page.locator('[data-article-chat].is-popped').count(), 1);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('[data-article-chat].is-popped').count(), 0);
  await page.waitForFunction(() => document.querySelector('[data-sa-status]').textContent === 'Saved');
  // Finish the stale-edit checks before the review captures, so their old toast
  // does not obscure the composer on the small screen.
  await page.evaluate(() => document.querySelectorAll('.toast').forEach((t) => t.remove()));
  await page.locator('[data-sa-agent]').scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: resolve(shots, `writer_${width}.png`) });
  await editor().scrollIntoViewIfNeeded();
  await page.screenshot({ path: resolve(shots, `draft_${width}.png`) });
  // Reload clears all module caches: persisted conversation still comes back.
  await page.reload();
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social'));
  await wait(() => window.DeskV1Store.state().projects.length > 0);
  await open(other);
  assert.equal(await page.locator('[data-chat-turn]').count(), 2);
  assert.equal(await page.locator('[data-article-accept]').count(), 1);
  // A refused acceptance leaves the visible draft alone and unlocks editing.
  await page.route('**/chat/agent-*', (route) => route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ error: 'test newer revision' }) }));
  await page.click('[data-article-accept]');
  await wait(() => /test newer revision/.test(document.querySelector('[data-article-chat-status]').textContent));
  assert.equal(await editor().textContent(), 'Another project, another draft.');
  assert.equal(await editor().getAttribute('contenteditable'), 'true');
  const realErrors = errors.filter((m) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(m));
  assert.deepEqual(realErrors, []);
  await context.close();
  console.log(`PASS ${width}: context, proposals, Accept/Undo, stale edits, retry, draft isolation, pop-out, reload, layout`);
}

const browser = await chromium.launch();
try { await run(browser, 1440); await run(browser, 390); }
finally { await browser.close(); }
