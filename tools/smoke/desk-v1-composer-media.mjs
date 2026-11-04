// C2: real SPA + isolated Flask piece/library storage, never the operator Desk.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { installDemoFixtures } from './desk-v1-fixture-api.mjs';
const root = fileURLToPath(new URL('../..', import.meta.url));
const shots = resolve(root, '_scratch/composer_c2');
mkdirSync(shots, { recursive: true });
const image = readFileSync(resolve(root, 'assets/desk-thumb-campaign.png'));
const video = readFileSync(resolve(root, 'tools/smoke/fixtures/local-video.mp4'));
const file = (name, mimeType, buffer = image) => ({ name, mimeType, buffer });
async function register(pid, command) {
  const r = await fetch('http://localhost:5199/api/processes/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pid, name: 'Composer C2 media smoke', command, project_id: 'mission_control' }) });
  assert(r.ok, 'process registration failed');
}
await register(process.pid, 'node tools/smoke/desk-v1-composer-media.mjs');
const python = process.env.MC_PYTHON || 'python';
const fixture = spawn(python, ['tools/smoke/_desk_what_add_server.py'], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
await register(fixture.pid, `${python} tools/smoke/_desk_what_add_server.py`);
let browser, origin, stderr = '', current;
fixture.stderr.on('data', data => { stderr += data; });
const getState = async (page) => (await page.request.get(origin + '/__state')).json();
async function boot(page, demo = false) {
  if (demo) {
    await installDemoFixtures(page);
    await page.route('**/api/config', r => r.fulfill({ json: { desk_v1: true, desk_v1_live: false } }));
  }
  await page.goto(origin);
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.desk-v1-home');
  await page.evaluate((id) => window.deskV1Nav('campaign', { campaignId: id, panel: 'what' }), demo ? 'camp-1' : 'camp-test');
  await page.click('[data-what-type="post"]');
  await page.waitForSelector('[data-composer]');
}
async function settled(page) {
  await page.waitForFunction(() => !document.querySelector('[data-composer-add-media]')?.disabled);
}
async function upload(page, files) {
  await page.click('[data-composer-add-media]');
  assert.equal(await page.locator('[data-cm-tab][aria-selected="true"]').getAttribute('data-cm-tab'), 'upload');
  await page.locator('[data-cm-files]').setInputFiles(files);
  await settled(page);
}
async function transfer(page, type, files, target = '[data-composer-text]', wait = true) {
  await page.locator(target).evaluate((el, { type, files }) => {
    const dt = new DataTransfer();
    files.forEach(f => dt.items.add(new File([new Uint8Array(f.bytes)], f.name, { type: f.mimeType })));
    const event = type === 'paste' ? new ClipboardEvent(type, { clipboardData: dt, bubbles: true, cancelable: true }) : new DragEvent(type, { dataTransfer: dt, bubbles: true, cancelable: true });
    el.dispatchEvent(event);
  }, { type, files: files.map(f => ({ name: f.name, mimeType: f.mimeType, bytes: [...f.buffer] })) });
  if (wait) await settled(page);
}
const count = page => page.locator('[data-composer-media-assets] [data-pmed-open]').count();
try {
  origin = await new Promise((done, reject) => {
    let out = '';
    fixture.stdout.on('data', data => { out += data; const m = out.match(/\{"port":\s*(\d+)\}/); if (m) done('http://127.0.0.1:' + m[1]); });
    fixture.once('exit', code => reject(new Error(`fixture exited ${code}: ${stderr}`)));
    setTimeout(() => reject(new Error('fixture timeout: ' + stderr)), 15000).unref();
  });
  browser = await chromium.launch();
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 950 } });
    const page = await context.newPage(); current = page; page.setDefaultTimeout(9000);
    const errors = [], writes = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => { if (r.method() !== 'GET' && new URL(r.url()).pathname.startsWith('/api/desk/')) writes.push(r); });
    await boot(page);
    const before = Object.keys((await getState(page)).pieces).length;
    await page.click('[data-composer-add-media]');
    assert.equal(await page.locator('[data-cm-tab]').count(), 2);
    await page.focus('[data-cm-close]'); await page.keyboard.press('Shift+Tab');
    assert(await page.locator('[data-cm-files]').evaluate(e => e === document.activeElement), 'reverse Tab wraps');
    await page.keyboard.press('Tab');
    assert(await page.locator('[data-cm-close]').evaluate(e => e === document.activeElement), 'Tab wraps');
    await page.screenshot({ path: resolve(shots, `upload-${width}.png`), fullPage: true });
    await page.keyboard.press('Escape');
    assert.equal(Object.keys((await getState(page)).pieces).length, before, 'opening/closing picker creates nothing');
    assert(await page.locator('[data-composer-add-media]').evaluate(e => e === document.activeElement), 'Esc restores focus');
    // Media is valid first content: browser-native multi-file selection.
    await upload(page, [file(`First ${width}.png`, 'image/png'), file(`Clip ${width}.mp4`, 'video/mp4', video)]);
    assert.equal(await count(page), 2);
    const creates = writes.filter(r => new URL(r.url()).pathname === '/api/desk/pieces');
    assert.equal(creates.length, 1, 'multi-file first content creates one post');
    const pieceId = creates[0].postDataJSON().id;
    let piece = (await getState(page)).pieces[pieceId];
    assert.equal(piece.body, ''); assert.equal(piece.assets.length, 2);
    assert(piece.assets.every(a => /\/Uploads\//.test(a.path)), 'every upload lands in library');
    await page.waitForFunction(() => document.querySelector('[data-composer-media-assets] video')?.readyState >= 1);
    assert(await page.locator('[data-composer-media-assets] video').evaluate(v => v.duration > 0), 'real H264 metadata plays');
    // Paste over Write and drop over the top bar both retain copy and media.
    await page.fill('[data-composer-text]', 'C2 media and copy');
    await transfer(page, 'paste', [file(`Pasted ${width}.png`, 'image/png')]);
    await transfer(page, 'drop', [file(`Dropped ${width}.mov`, 'video/quicktime', video)], '.desk-v1-composer-top');
    assert.equal(await count(page), 4);
    assert.equal(await page.inputValue('[data-composer-text]'), 'C2 media and copy');
    await page.locator('[data-composer-text]').evaluate(el => { const dt = new DataTransfer(); dt.setData('text/plain', 'plain text'); const e = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }); el.dispatchEvent(e); if (e.defaultPrevented) throw new Error('text paste was intercepted'); });
    await page.locator('[data-composer-media-assets]').scrollIntoViewIfNeeded();
    await page.screenshot({ path: resolve(shots, `composer-${width}.png`), fullPage: true });
    await page.click('[data-composer-add-media]');
    await page.focus('[data-cm-tab="upload"]'); await page.keyboard.press('ArrowRight');
    await page.waitForSelector('[data-cm-pick]');
    const listed = await page.locator('[data-cm-pick]').evaluateAll(bs => bs.map(b => b.dataset.cmPick));
    const materials = await (await page.request.get(origin + '/api/desk/materials')).json();
    const recent = ['image', 'video'].flatMap(k => materials.library[k].flatMap(f => f.items)).sort((a, b) => b.mtime - a.mtime);
    assert.deepEqual(listed, recent.map(f => f.path), 'recent first across kinds and folders');
    assert.equal(await page.locator('[data-cm-pick] img, [data-cm-pick] video').count(), listed.length);
    await page.screenshot({ path: resolve(shots, `library-${width}.png`), fullPage: true });
    assert(await page.locator('dialog').evaluate(e => e.scrollWidth <= e.clientWidth), 'picker fits');
    const rect = await page.locator('[data-composer-media-picker]').boundingBox();
    assert(Math.abs(rect.x + rect.width / 2 - width / 2) < 2, 'picker centered');
    const libraryPath = listed[0];
    await page.locator('[data-cm-pick]').first().click(); await settled(page);
    assert.equal(await count(page), 5);
    assert.equal((await getState(page)).pieces[pieceId].assets.at(-1).path, libraryPath, 'library attach reuses path');
    await page.click('#desk-v1-undo');
    await page.waitForFunction(() => document.querySelectorAll('[data-composer-media-assets] [data-pmed-open]').length === 4);
    await page.waitForFunction(async (pid) => (await (await fetch('/__state')).json()).pieces[pid].assets.length === 4, pieceId);
    // Library read refusal retains Upload and gives retry, not a false empty result.
    await page.route('**/api/desk/materials', r => r.fulfill({ status: 503, json: { error: 'library read refused' } }));
    await page.click('[data-composer-add-media]'); await page.click('[data-cm-tab="library"]');
    await page.waitForFunction(() => document.querySelector('[data-cm-library]').textContent.includes('library read refused'));
    await page.unroute('**/api/desk/materials');
    await page.getByRole('button', { name: 'Retry', exact: true }).click(); await page.waitForSelector('[data-cm-pick]');
    await page.click('[data-cm-close]');
    // Refused upload rolls back, keeps text, and the same file can be retried.
    await page.route('**/api/desk/pieces/*/assets', r => r.fulfill({ status: 409, json: { error: 'upload refused' } }));
    await upload(page, file('Retry.png', 'image/png'));
    assert.equal(await count(page), 4);
    assert.match(await page.locator('[data-composer-media-status]').innerText(), /upload refused/);
    await page.unroute('**/api/desk/pieces/*/assets');
    await page.route('**/api/desk/pieces/*/assets', r => r.fulfill({ json: { assets: [] } }));
    await upload(page, file('Missing reply.png', 'image/png'));
    assert.equal(await count(page), 4);
    assert.match(await page.locator('[data-composer-media-status]').innerText(), /did not include the saved media/);
    await page.unroute('**/api/desk/pieces/*/assets');
    await upload(page, file('Retry.png', 'image/png'));
    assert.equal(await count(page), 5);
    await upload(page, file('invalid.txt', 'text/plain', Buffer.from('invalid')));
    assert.equal(await count(page), 5, 'invalid file is not attached');
    assert.match(await page.locator('[data-composer-media-status]').innerText(), /Choose a PNG/);
    // Reload through the actual workspace; no blob URLs or fixture-only media.
    await page.reload(); await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(() => window.sidebarNav('social')); await page.waitForSelector('.desk-v1-home');
    await page.evaluate(pid => window.deskV1Nav('composer', { campaignId: 'camp-test', familyId: pid }), pieceId);
    await page.waitForSelector('[data-composer-media-assets] [data-pmed-open]');
    assert.equal(await count(page), 5);
    assert.equal(await page.inputValue('[data-composer-text]'), 'C2 media and copy');
    assert(await page.locator('[data-composer]').evaluate(e => e.scrollWidth <= e.clientWidth), 'composer fits');
    await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-test', panel: 'what' }));
    assert.equal(await page.locator('[data-what-row][data-kind="post"] [data-add-media]').count(), 0, 'no Post card media entry');
    await page.evaluate(pid => window.deskV1Nav('piece', { campaignId: 'camp-test', familyId: pid }), pieceId);
    assert.equal(await page.locator('[data-piece-media] [data-add-media]').count(), 0, 'no legacy Post detail media entry');
    assert(!writes.some(r => /approve|publish/.test(r.url())), 'human gates untouched');
    assert.deepEqual(errors, []);
    console.log(`PASS C2 ${width}: native upload, media-first, MP4 metadata, paste/drop MOV, text paste, recent thumbnails, reuse/Undo, read/upload refusal/retry, invalid, reload, retired entry points, layout`);
    // The fixture's approved/scheduled post remains viewable, with no media writes.
    await page.evaluate(() => window.deskV1Nav('composer', { campaignId: 'camp-library', familyId: 'post-library' }));
    assert(await page.locator('[data-composer-add-media]').isDisabled());
    assert.equal(await page.locator('[data-composer-media-assets] [data-pmed-remove]').count(), 0);
    const nwrites = writes.length;
    await transfer(page, 'paste', [file('Locked.png', 'image/png')], '[data-composer-text]', false);
    assert.equal(writes.length, nwrites, 'locked paste makes no writes');
    assert.match(await page.locator('[data-composer-media-status]').innerText(), /Return this post to review/);
    await context.close();
  }
  // First text/create and two media gestures arrive while create is delayed.
  const race = await browser.newPage(); current = race;
  await boot(race);
  const rwrites = [];
  race.on('request', r => { if (r.method() === 'POST' && new URL(r.url()).pathname === '/api/desk/pieces') rwrites.push(r); });
  await race.route('**/api/desk/pieces', async r => { await new Promise(done => setTimeout(done, 150)); await r.continue(); });
  await race.fill('[data-composer-text]', 'Typing before media');
  const a = transfer(race, 'paste', [file('Race1.png', 'image/png')]);
  const b = transfer(race, 'drop', [file('Race2.mp4', 'video/mp4', video)], '.desk-v1-composer-top');
  await Promise.all([a, b]);
  assert.equal(rwrites.length, 1); assert.equal(await count(race), 2, 'gestures share create and attach queue');
  assert.equal((await getState(race)).pieces[rwrites[0].postDataJSON().id].assets.length, 2);
  await race.unroute('**/api/desk/pieces');
  // A rejected first-content create keeps the composer retryable.
  await race.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-test', panel: 'what' }));
  await race.click('[data-what-type="post"]');
  await race.route('**/api/desk/pieces', r => r.fulfill({ status: 409, json: { error: 'draft create refused' } }));
  await upload(race, file('Create retry.png', 'image/png'));
  assert.equal(await count(race), 0);
  assert.match(await race.locator('[data-composer-media-status]').innerText(), /draft create refused/);
  await race.unroute('**/api/desk/pieces');
  await upload(race, file('Create retry.png', 'image/png'));
  assert.equal(await count(race), 1);
  // Remount during a pending write must complete against the current surface.
  await race.route('**/api/desk/pieces/*/assets', async r => { await new Promise(done => setTimeout(done, 250)); await r.continue(); });
  const remount = transfer(race, 'drop', [file('Remount.png', 'image/png')]);
  await race.waitForRequest(r => /\/assets$/.test(r.url()) && r.method() === 'POST');
  await race.evaluate(() => { const root = document.querySelector('[data-composer]').parentElement; const fam = window.DeskV1Store.state().families.at(-1); window.deskV1RenderComposer(root, { campaignId: 'camp-test', familyId: fam.id }); });
  await remount;
  assert.equal(await count(race), 2);
  console.log('PASS C2 edge contracts: locked posts, concurrent text/paste/drop, first-create refusal/retry, remount while uploading');
  await race.close();
  const demo = await browser.newPage({ viewport: { width: 390, height: 950 } }); current = demo;
  const mutations = [];
  demo.on('request', r => { if (r.method() !== 'GET' && new URL(r.url()).pathname.startsWith('/api/desk/')) mutations.push(r.url()); });
  await boot(demo, true);
  await upload(demo, file('Demo.png', 'image/png'));
  assert.equal(await count(demo), 1); assert.deepEqual(mutations, []);
  assert(await demo.locator('[data-composer-review]').isEnabled(), 'media-only post can go to review');
  await demo.click('[data-composer-add-media]'); await demo.click('[data-cm-tab="library"]');
  await demo.waitForSelector('[data-cm-pick]');
  assert.match(await demo.locator('[data-cm-pick]').first().innerText(), /Demo.png/);
  console.log('PASS C2 demo: local media-first create and attach, no mutation requests');
} catch (e) {
  if (current && !current.isClosed()) await current.screenshot({ path: resolve(shots, 'failure.png'), fullPage: true });
  throw e;
} finally {
  if (browser) await browser.close();
  if (origin) await fetch(origin + '/__shutdown', { method: 'POST' });
  await new Promise(done => { if (fixture.exitCode !== null) done(); else fixture.once('exit', done); });
}
