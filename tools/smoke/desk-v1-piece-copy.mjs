#!/usr/bin/env node
// Hermetic piece-copy smoke: real SPA/modules, fake Desk persistence, no live writes.
// Run: node tools/smoke/desk-v1-piece-copy.mjs
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures, installDemoFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const index = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const files = loadStaticJsCss(root);
const screenshots = resolve(root, '_scratch/piece-copy');
mkdirSync(screenshots, { recursive: true });
const pieceId = 'copy-smoke';
const origin = 'http://mc.smoke.test';
const editor = '[data-piece-copy-text]';
const status = '[data-piece-copy-status]';
const check = (message) => console.log('PASS ' + message);

async function boot(browser, { live = true, width = 1440 } = {}) {
  const fx = loadFixtures();
  fx.families.push({ id: pieceId, campaignId: 'camp-1', kind: 'post', title: 'New post', body: '', assets: [], versions: [] });
  const srv = { fx, patches: [], fail: null, hold: null, refreshFail: false };
  const ctx = await browser.newContext({ viewport: { width, height: 950 } });
  const page = await ctx.newPage();
  page.setDefaultTimeout(8000);
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  if (!live) {
    await installDemoFixtures(page);
    await page.addInitScript((piece) => window.DeskV1Fixtures.families.push(piece), fx.families.at(-1));
  }
  await page.route('**/*', async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const json = (body, code = 200) => route.fulfill({ status: code, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: index });
    if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
    if (path === '/api/config') return json({ desk_v1: true, desk_v1_live: live, user_timezone: '' });
    if (path === '/api/projects') return json(fx.projects.map((p) => ({
      ...p, status: 'active', domain: 'general', activity_log: [], backlog: [], roster: [],
      project_path: '/smoke/' + p.id, last_updated: '2026-10-29T00:00:00Z',
    })));
    if (path === '/api/characters') return json([]);
    if (path === '/api/local-auth/status') return json({ configured: true });
    if (path === '/api/desk/workspace') return srv.refreshFail ? json({ error: 'workspace unavailable' }, 503) : json(workspaceFromFixtures(fx));
    if (path === `/api/desk/pieces/${pieceId}` && req.method() === 'PATCH') {
      const body = req.postDataJSON();
      srv.patches.push(body);
      if (srv.hold) await srv.hold;
      if (srv.fail) { const msg = srv.fail; srv.fail = null; return json({ error: msg }, 409); }
      assert.deepEqual(Object.keys(body), ['body'], 'only draft body is patched');
      const p = fx.families.find((f) => f.id === pieceId);
      p.body = body.body.trim();
      return json(p);
    }
    return route.abort();
  });
  async function open() {
    await page.evaluate(() => window.sidebarNav('social'));
    await page.waitForSelector('.desk-v1-shell');
    await page.waitForFunction(() => window.DeskV1Store.state().families.some((f) => f.id === 'copy-smoke'));
    await page.evaluate(() => {
      window.deskV1Nav('campaign', { campaignId: 'camp-1' });
      window.deskV1Nav('piece', { campaignId: 'camp-1', familyId: 'copy-smoke' });
    });
  }
  await page.goto(origin + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await open();
  return { ctx, page, srv, errors, open };
}

async function saved(page, text = 'Saved') {
  try {
    await page.waitForFunction(([selector, expected]) => document.querySelector(selector)?.textContent === expected, [status, text]);
  } catch (e) {
    throw new Error(`${e.message}; expected ${text}, actual ${await page.locator(status).textContent()}`);
  }
}
async function importFile(page, name, text) {
  // The visible button opens the actual local picker, rather than a URL upload.
  const chooser = page.waitForEvent('filechooser');
  await page.locator('[data-piece-copy-import]').click();
  await (await chooser).setFiles({ name, mimeType: 'text/plain', buffer: Buffer.from(text) });
}

let browser;
try {
  browser = await chromium.launch();
  const { ctx, page, srv, errors, open } = await boot(browser);
  assert.equal(await page.locator(editor).inputValue(), '');
  assert.equal(await page.locator(editor).getAttribute('placeholder'), 'Start typing your post…');
  assert.match(await page.locator('[data-piece-section="copy"]').innerText(), /Add a version from the Where board/);
  await page.screenshot({ path: resolve(screenshots, 'empty-1440.png') });
  check('new post has editor, import, Save and next-step hint');

  const text = 'First draft\n' + 'A complete editable paragraph. '.repeat(30);
  await page.locator(editor).fill(text);
  await page.locator('[data-piece-copy-save]').click();
  await saved(page);
  assert.deepEqual(srv.patches, [{ body: text }]);
  assert.equal(srv.fx.families.find((f) => f.id === pieceId).body, text.trim());
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await open();
  assert.equal(await page.locator(editor).inputValue(), text.trim());
  check('type -> explicit Save -> actual reload retains full body over 400 characters');

  await page.locator(editor).fill('Saved on blur');
  await page.locator(editor).blur();
  await saved(page);
  assert.equal(srv.patches.at(-1).body, 'Saved on blur');
  check('blur saves through the same PATCH path');

  let count = srv.patches.length;
  await importFile(page, 'draft.txt', 'Imported .txt\nSecond line');
  await saved(page, 'Imported · Not saved');
  assert.equal(await page.locator(editor).inputValue(), 'Imported .txt\nSecond line');
  await page.waitForTimeout(80);
  assert.equal(srv.patches.length, count, 'import must not save');
  await page.locator('[data-piece-copy-save]').click();
  await saved(page);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await open();
  assert.equal(await page.locator(editor).inputValue(), 'Imported .txt\nSecond line');
  check('visible file picker imports .txt without saving; Save persists it across reload');

  await importFile(page, 'draft.html', '<html><head><style>bad CSS</style></head><body><p>Hello &amp; <b>world</b></p><p>Next<br>line</p><script>window.importRan=true</script></body></html>');
  await saved(page, 'Imported · Not saved');
  assert.equal(await page.locator(editor).inputValue(), 'Hello & world\nNext\nline');
  assert.equal(await page.evaluate(() => window.importRan), undefined);
  await page.locator('[data-piece-copy-save]').click();
  await saved(page);
  check('HTML imports decoded plain text and line breaks without scripts/styles');

  srv.fail = 'this campaign is archived';
  await page.locator(editor).fill('Keep this failed draft');
  await page.locator('[data-piece-copy-save]').click();
  await saved(page, 'Not saved: this campaign is archived');
  assert.equal(await page.locator(editor).inputValue(), 'Keep this failed draft');
  await page.evaluate(() => window.deskV1PieceRepaint());
  assert.equal(await page.locator(editor).inputValue(), 'Keep this failed draft');
  await saved(page, 'Not saved: this campaign is archived');
  await page.locator('[data-piece-copy-save]').click();
  await saved(page);
  check('server refusal retains draft and error across repaint; retry succeeds');

  let release;
  srv.hold = new Promise((r) => { release = r; });
  count = srv.patches.length;
  await page.locator(editor).fill('First in flight');
  await page.locator('[data-piece-copy-save]').click();
  await page.waitForFunction(() => document.querySelector('[data-piece-copy-status]').textContent === 'Saving…');
  await page.locator(editor).fill('Newer text while saving');
  await page.locator(editor).blur();
  srv.hold = null;
  release();
  await saved(page);
  assert.equal(srv.patches.length, count + 2);
  assert.equal(srv.patches.at(-1).body, 'Newer text while saving');
  assert.equal(await page.locator(editor).inputValue(), 'Newer text while saving');
  check('edits during an in-flight save survive; a later blur queues a serialized save');

  srv.hold = new Promise((r) => { release = r; });
  count = srv.patches.length;
  await page.locator(editor).fill('Another pending save');
  await page.locator('[data-piece-copy-save]').click();
  await saved(page, 'Saving…');
  await page.locator(editor).fill('Keep working without blur');
  await page.locator(editor).evaluate((el) => el.setSelectionRange(4, 9));
  srv.hold = null;
  release();
  await saved(page, 'Unsaved changes');
  assert.equal(srv.patches.length, count + 1);
  assert.equal(await page.locator(editor).inputValue(), 'Keep working without blur');
  assert.deepEqual(await page.locator(editor).evaluate((el) => [document.activeElement === el, el.selectionStart, el.selectionEnd]), [true, 4, 9]);
  await page.locator('[data-piece-copy-save]').click();
  await saved(page);
  check('a completed save preserves focused newer text and caret without autosaving it');

  // A different repaint (e.g. media attach) must not erase an unsaved draft.
  await page.locator(editor).fill('Still typing');
  await page.evaluate(() => window.deskV1PieceRepaint());
  assert.equal(await page.locator(editor).inputValue(), 'Still typing');
  await page.locator('[data-piece-copy-save]').click();
  await saved(page);
  check('unrelated piece repaint preserves unsaved input');

  count = srv.patches.length;
  await importFile(page, 'too-long.txt', 'x'.repeat(100001));
  await saved(page, 'Import failed: The file exceeds 100,000 characters.');
  assert.equal(await page.locator(editor).inputValue(), 'Still typing');
  assert.equal(srv.patches.length, count);
  await importFile(page, 'wrong.csv', 'do not replace');
  await saved(page, 'Import failed: Choose a text, Markdown or HTML file.');
  assert.equal(await page.locator(editor).inputValue(), 'Still typing');
  await importFile(page, 'draft.markdown', '**Markdown stays as written**');
  await saved(page, 'Imported · Not saved');
  assert.equal(await page.locator(editor).inputValue(), '**Markdown stays as written**');
  await page.locator('[data-piece-copy-save]').click();
  await saved(page);
  check('oversized/unsupported imports preserve text; Markdown stays editable');

  await page.evaluate(() => {
    const area = document.querySelector('[data-piece-copy-text]');
    area.value = 'x'.repeat(100001);
    area.dispatchEvent(new Event('input'));
  });
  count = srv.patches.length;
  await page.locator('[data-piece-copy-save]').click();
  await saved(page, 'Not saved: body is too long (max 100000 characters).');
  assert.equal(srv.patches.length, count);
  await page.locator(editor).fill('x'.repeat(100000));
  await page.locator('[data-piece-copy-save]').click();
  await saved(page);
  assert.equal(srv.patches.at(-1).body.length, 100000);
  check('MAX_BODY exact boundary saves; oversized input never PATCHes');

  await page.evaluate(() => {
    const fam = window.DeskV1Store.state().families.find((f) => f.id === 'copy-smoke');
    fam.versions = [{ id: 'copy-version', channelId: 'ch-x', state: 'needs_review', claims: [] }];
    window.deskV1PieceRepaint();
  });
  assert.equal(await page.locator('[data-copy-row="copy-version"] [data-review-btn]').count(), 1);
  for (const kind of ['image', 'video']) {
    await page.evaluate((k) => {
      window.DeskV1Store.state().families.find((f) => f.id === 'copy-smoke').kind = k;
      window.deskV1PieceRepaint();
    }, kind);
    assert.equal(await page.locator(editor).count(), 1);
  }
  await page.evaluate(() => {
    window.DeskV1Store.state().families.find((f) => f.id === 'copy-smoke').kind = 'article';
    window.deskV1PieceRepaint();
  });
  assert.equal(await page.locator(editor).count(), 0);
  assert.equal(await page.locator('[data-piece-edit-article]').count(), 1);
  await page.evaluate(() => {
    const fam = window.DeskV1Store.state().families.find((f) => f.id === 'copy-smoke');
    fam.kind = 'post'; fam.archivedAt = '2026-10-29T00:00:00Z';
    window.deskV1PieceRepaint();
  });
  assert.equal(await page.locator(editor).count(), 0);
  check('version Review retained; image/video editable; article/archive exclusions hold');

  await page.evaluate(() => {
    window.DeskV1Store.state().families.find((f) => f.id === 'copy-smoke').archivedAt = null;
    window.deskV1PieceRepaint();
  });
  srv.refreshFail = true;
  await page.locator(editor).fill('Body saved despite refresh failure');
  await page.locator('[data-piece-copy-save]').click();
  await page.waitForFunction(() => document.querySelector('[data-piece-copy-status]').textContent.includes('Saved, but workspace refresh failed:'));
  assert.equal(srv.fx.families.find((f) => f.id === pieceId).body, 'Body saved despite refresh failure');
  assert.equal(await page.locator(editor).inputValue(), 'Body saved despite refresh failure');
  check('workspace refresh refusal reports saved body separately and retains visible draft');
  assert.deepEqual(errors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e)), []);
  await ctx.close();

  const mobile = await boot(browser, { width: 390 });
  await mobile.page.screenshot({ path: resolve(screenshots, 'empty-390.png') });
  const bounds = await mobile.page.locator('[data-piece-copy]').boundingBox();
  assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390, 'editor fits mobile viewport');
  assert.deepEqual(mobile.errors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e)), []);
  await mobile.ctx.close();
  const demo = await boot(browser, { live: false, width: 390 });
  await demo.page.locator(editor).fill('Demo text');
  await demo.page.locator('[data-piece-copy-save]').click();
  await saved(demo.page, 'Saved in demo only');
  assert.equal(demo.srv.patches.length, 0);
  assert.equal(await demo.page.evaluate(() => window.DeskV1Store.state().families.find((f) => f.id === 'copy-smoke').body), 'Demo text');
  assert.deepEqual(demo.errors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e)), []);
  await demo.ctx.close();
  check('390px empty state fits; fixture editing is local-only without page errors');
  console.log('PASS piece-copy smoke; screenshots: ' + screenshots);
} catch (e) {
  console.error(e.stack || e);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
}
