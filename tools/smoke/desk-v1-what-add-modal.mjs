// Real SPA + temporary real Flask/filesystem store. Never touches the live Desk.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const SHOTS = resolve(ROOT, '_scratch/composer_c1');
mkdirSync(SHOTS, { recursive: true });
const before = process.argv.includes('--before');
const state = async (page) => (await page.request.get(origin + '/__state')).json();
async function boot(page) {
  await page.goto(origin);
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.desk-v1-shell');
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-test', panel: 'what' }));
  await page.waitForSelector('[data-what-type="video"]');
}
async function add(page, kind, name) {
  await page.click(`[data-what-type="${kind}"]`);
  await page.waitForSelector('[data-what-create]');
  const id = await page.locator('[data-what-create]').getAttribute('data-family-id');
  if (name) { await page.fill('[data-what-title]', name); await page.press('[data-what-title]', 'Tab'); }
  await page.waitForFunction(async (id) => !!(await (await fetch('/__state')).json()).pieces[id], id);
  return id;
}
async function saved(page, id, body) {
  await page.waitForFunction(async ({ id, body }) => (await (await fetch('/__state')).json()).pieces[id]?.body === body, { id, body });
}
const register = async (pid, name, command) => {
  const r = await fetch('http://localhost:5199/api/processes/register', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pid, name, command, project_id: 'mission_control' }) });
  assert(r.ok, 'process registration failed: ' + await r.text());
};
await register(process.pid, 'What add modal smoke', 'node tools/smoke/desk-v1-what-add-modal.mjs' + (before ? ' --before' : ''));
const fixture = spawn('python', [resolve(ROOT, 'tools/smoke/_desk_what_add_server.py')], { cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
await register(fixture.pid, 'Isolated What add fixture', 'python tools/smoke/_desk_what_add_server.py');
let browser, origin, currentPage, stderr = '';
fixture.stderr.on('data', (data) => { stderr += data; });
try {
  origin = await new Promise((done, reject) => {
    let output = '';
    fixture.stdout.on('data', (data) => { output += data; const m = output.match(/\{"port":\s*(\d+)\}/); if (m) done('http://127.0.0.1:' + m[1]); });
    fixture.once('exit', (code) => reject(new Error('fixture exited ' + code + ': ' + stderr)));
    setTimeout(() => reject(new Error('fixture timeout: ' + stderr)), 15000).unref();
  });
  browser = await chromium.launch({ headless: true });
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 950 } });
    const page = await context.newPage(); currentPage = page;
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await boot(page);
    const firstVideo = await add(page, 'video');
    await page.screenshot({ path: resolve(SHOTS, `what_add_${before ? 'before' : 'after'}_${width}.png`) });
    if (!before) {
      assert.equal(await page.locator('dialog[open][data-what-add-modal]').count(), 1);
      const dialog = page.locator('[data-what-add-modal]');
      assert.equal(await dialog.locator('h2').innerText(), 'Video');
      assert.equal(await page.locator('[data-what-list] [data-what-create]').count(), 0, 'chooser is not inline');
      assert.deepEqual(await dialog.locator('[data-what-source]').evaluateAll((els) => els.map((e) => e.dataset.whatSource)), ['record', 'upload', 'online', 'create']);
      const rect = await dialog.boundingBox();
      assert(Math.abs(rect.x + rect.width / 2 - width / 2) < 2, 'dialog horizontally centered');
      assert(rect.x >= 0 && rect.x + rect.width <= width && rect.y >= 0 && rect.y + rect.height <= 950, 'dialog fits viewport');
      assert(await dialog.evaluate((d) => d.scrollWidth <= d.clientWidth), 'no horizontal overflow');
      await page.focus('[data-what-modal-close]'); await page.keyboard.press('Shift+Tab');
      assert(await page.locator('[data-what-remove]').evaluate((e) => e === document.activeElement), 'reverse Tab wrapped');
      await page.keyboard.press('Tab');
      assert(await page.locator('[data-what-modal-close]').evaluate((e) => e === document.activeElement), 'Tab wrapped');
      await page.fill('[data-what-title]', `Kept video draft ${width}`);
      await page.keyboard.press('Escape');
      assert.equal(await dialog.count(), 0, 'Escape closes');
      await page.waitForFunction(async ({ id, title }) => (await (await fetch('/__state')).json()).pieces[id].title === title,
        { id: firstVideo, title: `Kept video draft ${width}` });
      assert(await page.locator('[data-what-type="video"]').evaluate((e) => e === document.activeElement), 'focus restored');
      // Real pointer drop: exactly one new piece, visible immediately in a modal.
      const n = Object.keys((await state(page)).pieces).length;
      const tile = await page.locator('[data-what-type="video"]').boundingBox();
      const target = await page.locator('[data-what-list]').boundingBox();
      await page.mouse.move(tile.x + tile.width / 2, tile.y + tile.height / 2); await page.mouse.down();
      await page.mouse.move(target.x + 24, target.y + 30, { steps: 12 }); await page.mouse.up();
      await page.waitForSelector('dialog[open] [data-what-source="record"]');
      await page.waitForFunction(async (n) => Object.keys((await (await fetch('/__state')).json()).pieces).length === n + 1, n);
      await page.mouse.click(2, 2);
      assert.equal(await dialog.count(), 0, 'backdrop closes');
      await page.waitForTimeout(320); // existing drag-end click suppression
      // Keyboard path and all type registries.
      await page.focus('[data-what-type="image"]'); await page.keyboard.press('Enter');
      assert.deepEqual(await page.locator('[data-what-source]').evaluateAll((els) => els.map((e) => e.dataset.whatSource)), ['upload', 'library', 'generate']);
      await page.click('[data-what-source="library"]');
      await page.waitForSelector('[data-what-libfile]');
      assert.equal(await page.locator('[data-what-dropzone]').isVisible(), false);
      await page.keyboard.press('Escape');
      // Article validation and persistence through the actual importer + routes.
      const article = await add(page, 'article', `Imported article ${width}`);
      assert.deepEqual(await page.locator('[data-what-source]').evaluateAll((els) => els.map((e) => e.dataset.whatSource)), ['browse', 'write', 'import']);
      await page.click('[data-what-source="import"]');
      await page.screenshot({ path: resolve(SHOTS, `what_add_article_${width}.png`) });
      for (const [name, buffer, message] of [
        ['bad.exe', Buffer.from('invalid'), 'Choose a text'],
        ['big.txt', Buffer.alloc(4 * 1024 * 1024 + 1), '4 MB'],
        ['long.md', Buffer.alloc(100001, 'x'), '100,000'],
      ]) {
        await page.setInputFiles('[data-what-article-file]', { name, mimeType: 'text/plain', buffer });
        await page.waitForFunction((m) => document.querySelector('[data-what-import-status]').textContent.includes(m), message);
        assert.equal((await state(page)).pieces[article].body, '', 'invalid import never persisted');
      }
      await page.route('**/api/desk/pieces/' + article, (route) => route.request().method() === 'PATCH'
        ? route.fulfill({ status: 409, contentType: 'application/json', body: '{"error":"import write refused"}' }) : route.continue());
      await page.setInputFiles('[data-what-article-file]', { name: 'refused.txt', mimeType: 'text/plain', buffer: Buffer.from('refused text') });
      await page.waitForFunction(() => document.querySelector('[data-what-import-status]').textContent.includes('import write refused'));
      assert.equal((await state(page)).pieces[article].body, '');
      await page.unroute('**/api/desk/pieces/' + article);
      const imported = 'Opening\nSecond line';
      await page.setInputFiles('[data-what-article-file]', { name: 'article.html', mimeType: 'text/html',
        buffer: Buffer.from('<script>window.importRan=true</script><style>bad</style><p>Opening<br>Second line</p>') });
      await saved(page, article, imported);
      await page.waitForSelector('[data-what-add-modal]', { state: 'detached' });
      assert.equal((await state(page)).pieces[article].word_count, 3);
      assert.equal(await page.evaluate(() => window.importRan), undefined);
      await boot(page);
      assert.equal(await page.locator(`[data-what-row][data-family-id="${article}"]`).count(), 1, 'article survives reload');
      assert.equal(await page.evaluate((id) => window.DeskV1Store.state().families.find((f) => f.id === id).body, article), imported);
      for (const name of ['plain.txt', 'draft.md']) {
        const id = await add(page, 'article', name + ' ' + width);
        await page.click('[data-what-source="import"]');
        await page.setInputFiles('[data-what-article-file]', { name, mimeType: 'text/plain', buffer: Buffer.from('Editable text') });
        await saved(page, id, 'Editable text');
        await page.waitForSelector('[data-what-add-modal]', { state: 'detached' });
        await page.click('#desk-v1-undo');
        await saved(page, id, '');
        assert.equal((await state(page)).pieces[article].body, imported, 'Undo affects only this import');
      }
      // Post no longer has a source fork: keyboard opens the composer without
      // creating a record. Persist first content through the actual piece routes.
      const count = Object.keys((await state(page)).pieces).length;
      await page.focus('[data-what-type="post"]'); await page.keyboard.press('Space');
      await page.waitForSelector('[data-composer-text]');
      assert.equal(await page.locator('[data-what-add-modal]').count(), 0);
      assert.equal(Object.keys((await state(page)).pieces).length, count);
      const body = `Real composer post ${width}`;
      await page.fill('[data-composer-text]', body);
      await page.waitForFunction(() => document.querySelector('[data-composer-status]').textContent === 'Saved');
      const post = Object.values((await state(page)).pieces).find((p) => p.body === body);
      assert(post, 'first content saved in the real store');
      assert.deepEqual(post.versions, []);
      assert(await page.locator('[data-composer-review]').isDisabled(), 'empty campaign bound cannot send to review');
      await page.fill('[data-composer-text]', body + ' updated');
      await page.click('[data-composer-save]');
      await saved(page, post.id, body + ' updated');
      await boot(page);
      await page.click(`[data-what-row][data-family-id="${post.id}"] [data-primary-action]`);
      await page.waitForSelector('[data-composer-text]');
      assert.equal(await page.inputValue('[data-composer-text]'), body + ' updated');
      await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-test', panel: 'what' }));
      await add(page, 'image');
      await page.evaluate(() => window.deskV1Nav('home', {}));
      await page.waitForSelector('[data-what-add-modal]', { state: 'detached' });
      console.log(`PASS persistence ${width}: HTML import, file validation/refusal, post composer create/save/reload`);
    }
    assert.deepEqual(errors, []);
    console.log(`PASS ${before ? 'baseline' : 'modal'} ${width}`);
    await context.close();
  }
} catch (error) {
  if (currentPage && !currentPage.isClosed()) await currentPage.screenshot({ path: resolve(ROOT, '_scratch/what-add-failure.png') });
  console.error(error); process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  if (origin) await fetch(origin + '/__shutdown', { method: 'POST' });
  if (fixture.exitCode === null) {
    await Promise.race([new Promise((done) => fixture.once('exit', done)), new Promise((done) => setTimeout(done, 2000))]);
    if (fixture.exitCode === null) fixture.kill();
  }
}
