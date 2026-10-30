// Real SPA, hermetic APIs: name persistence/Undo/refusal and browser-owned M21
// upload from desktop and phone. Route/filesystem validation: test_desk_pieces.py.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';
import { seedDeskV1Fixtures } from './desk-v1-fixture-api.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const index = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const files = loadStaticJsCss(root);
for (const name of ['campaign', 'calendar']) files[`/assets/desk-thumb-${name}.png`] = ['image/png', readFileSync(resolve(root, `assets/desk-thumb-${name}.png`))];
const shots = resolve(root, '_scratch/piece-name-upload');
mkdirSync(shots, { recursive: true });
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8q8AAAAASUVORK5CYII=', 'base64');
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    const ctx = await browser.newContext({ viewport: { width, height: 950 }, isMobile: width === 390, hasTouch: width === 390 });
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.route('**/*', (route) => {
      const path = new URL(route.request().url()).pathname;
      const hit = files[path];
      const json = (body) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: index });
      if (hit) return route.fulfill({ contentType: hit[0], body: hit[1] });
      if (path === '/api/config') return json({ desk_v1: true });
      if (path === '/api/projects') return json([{ id: 'clayrune', name: 'Clayrune', status: 'active', domain: 'general', display_order: 0, roster: [] }]);
      if (path === '/api/characters') return json([]);
      return route.abort();
    });
    await seedDeskV1Fixtures(page);
    await page.goto('http://mc.smoke.test/');
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(() => window.sidebarNav('social'));
    await page.waitForSelector('.desk-v1-shell');
    await page.evaluate(() => {
      const f = window.DeskV1Store.state().families.find((x) => x.id === 'fam-30-testers');
      f.title = 'New post';
      window.deskV1Nav('piece', { campaignId: 'camp-1', familyId: f.id });
    });
    const input = page.locator('[data-piece-name-input]');
    // The title is the control: no Edit name / Post name / Save name chrome.
    assert.equal(await page.locator('[data-piece-name-edit], [data-piece-name-save], [data-piece-name-form]').count(), 0);
    await page.click('[data-piece-title-edit]');
    assert.equal(await input.inputValue(), '');
    await input.fill('Autumn launch update');
    await page.screenshot({ path: resolve(shots, `title-edit-${width}.png`), fullPage: true });
    await input.press('Enter');
    await page.waitForFunction(() => document.querySelector('.desk-v1-piece-title').textContent === 'Autumn launch update');
    assert.equal(await page.textContent('.desk-v1-crumb-title'), 'Autumn launch update');
    await page.click('#desk-v1-undo');
    await page.waitForFunction(() => document.querySelector('.desk-v1-piece-title').textContent === 'New post');
    await page.click('[data-piece-media] [data-add-media]');
    const demoChooser = page.waitForEvent('filechooser');
    await page.getByRole('menuitem', { name: 'From this computer…', exact: true }).click();
    await (await demoChooser).setFiles({ name: 'demo.png', mimeType: 'image/png', buffer: png });
    await page.waitForFunction(() => window.DeskV1Store.state().families.find((f) => f.id === 'fam-30-testers').assets.at(-1).title === 'demo.png');
    assert.match(await page.evaluate(() => window.DeskV1Store.state().families.find((f) => f.id === 'fam-30-testers').assets.at(-1).src), /^blob:/);
    await page.click('#desk-v1-undo');
    await page.waitForFunction(() => !window.DeskV1Store.state().families.find((f) => f.id === 'fam-30-testers').assets.some((a) => a.title === 'demo.png'));

    // Exercise the real command store's live run/rollback/undo path with a
    // stateful server double; no production workspace is mutated.
    await page.evaluate(() => {
      const store = window.DeskV1Store;
      store.live = () => true;
      store.write = store.run;
      window.__writes = []; window.__savedTitle = 'New post';
      store.api = async (method, url, body) => {
        window.__writes.push({ method, url, multipart: body instanceof FormData });
        if (url.startsWith('/api/desk/materials')) {
          if (window.__libraryRefuse) throw new Error('library unavailable');
          return { library: { image: [], video: [] } };
        }
        if (window.__refuse) throw new Error('test refusal');
        if (method === 'PATCH') { window.__savedTitle = body.title; return { title: body.title }; }
        if (method === 'POST' && body instanceof FormData) {
          const f = store.state().families.find((x) => x.id === 'fam-30-testers');
          window.__uploadedName = body.get('file').name;
          const a = f.assets.find((x) => x.id === body.get('id'));
          return { assets: [{ ...a, path: 'desk/library/image/Uploads/local.png', src: '/uploaded.png' }] };
        }
        return {};
      };
    });
    await page.click('[data-piece-title-edit]');
    await input.fill('Autumn launch update');
    await input.press('Enter');
    await page.waitForFunction(() => window.__savedTitle === 'Autumn launch update');

    // Empty and oversized selections never reach M21; a video piece asks for
    // videos rather than images, and cancelled selection adds nothing.
    await page.evaluate(() => {
      window.__localValidation = 0;
      const fam = { id: 'validation', kind: 'video' };
      for (const file of [new File([], 'empty.mp4'), new File(['x'], 'big.mp4')]) {
        if (file.name === 'big.mp4') Object.defineProperty(file, 'size', { value: 501 * 1024 * 1024 });
        window.DeskV1LocalUpload.open(fam, () => { window.__localValidation++; });
        const el = document.querySelector('[data-piece-local-upload="validation"]');
        if (!el.accept.includes('.mp4') || !el.accept.includes('video/mp4')) throw new Error('video accept mismatch');
        Object.defineProperty(el, 'files', { value: [file] });
        el.dispatchEvent(new Event('change'));
      }
      window.DeskV1LocalUpload.open(fam, () => { window.__localValidation++; });
      document.querySelector('[data-piece-local-upload="validation"]').dispatchEvent(new Event('cancel'));
    });
    assert.equal(await page.evaluate(() => window.__localValidation), 0);
    await page.evaluate(() => {
      const formats = window.DeskV1MediaFormats;
      for (const ext of formats.videoExtensions) {
        if (formats.error(new File(['clip'], 'video.' + ext, { type: '' }), 'video')) throw new Error('Rejected video extension ' + ext);
        if (!formats.videoAccept.includes('.' + ext)) throw new Error('Missing chooser extension ' + ext);
      }
      if (formats.error(new File(['clip'], 'unknown.format', { type: 'video/x-host-player' }), 'video')) throw new Error('Rejected video MIME');
      if (!formats.error(new File(['bad'], 'program.exe'), 'video').includes('500 MB')) throw new Error('Missing size in rejection');
    });
    await page.click('#desk-v1-undo');
    await page.waitForFunction(() => window.__savedTitle === 'New post');
    await page.evaluate(() => { window.__refuse = true; });
    await page.click('[data-piece-title-edit]');
    await input.fill('Keep my failed draft');
    await input.press('Enter');
    await page.waitForFunction(() => document.querySelector('[data-piece-name-status]').textContent.includes('test refusal'));
    assert.equal(await input.inputValue(), 'Keep my failed draft');
    assert.equal(await page.evaluate(() => window.DeskV1Store.state().families.find((f) => f.id === 'fam-30-testers').title), 'New post');
    await page.evaluate(() => { window.__refuse = false; });
    await input.fill('Autumn launch update');
    await input.press('Enter');
    await page.waitForFunction(() => window.__savedTitle === 'Autumn launch update');

    const assetCount = await page.evaluate(() => window.DeskV1Store.state().families.find((f) => f.id === 'fam-30-testers').assets.length);
    async function choose(file, accept) {
      await page.click('[data-piece-media] [data-add-media]');
      await page.waitForSelector('.desk-v1-addto-menu');
      if (!file) {
        await page.evaluate(() => { document.getElementById('toast-container').innerHTML = ''; });
        await page.screenshot({ path: resolve(shots, `add-media-${width}.png`), fullPage: true }); return;
      }
      const chooser = page.waitForEvent('filechooser');
      await page.getByRole('menuitem', { name: 'From this computer…', exact: true }).click();
      const fc = await chooser;
      assert.equal(await fc.element().getAttribute('accept'), accept);
      await fc.setFiles(file);
    }
    await choose(null);
    assert.equal(await page.getByRole('menuitem', { name: 'From your projects…', exact: true }).count(), 1);
    await choose({ name: 'local.png', mimeType: 'image/png', buffer: png }, 'image/*');
    await page.waitForFunction(() => window.__uploadedName === 'local.png');
    assert.equal(await page.evaluate(() => window.DeskV1Store.state().families.find((f) => f.id === 'fam-30-testers').assets.at(-1).src), '/uploaded.png');
    await page.click('#desk-v1-undo');
    await page.waitForFunction((n) => window.DeskV1Store.state().families.find((f) => f.id === 'fam-30-testers').assets.length === n, assetCount);
    assert(await page.evaluate(() => window.__writes.some((w) => w.method === 'DELETE' && w.url.includes('/assets/'))));
    const before = await page.evaluate(() => window.__writes.length);
    await choose({ name: 'bad.exe', mimeType: 'application/octet-stream', buffer: png }, 'image/*');
    assert.equal(await page.evaluate(() => window.__writes.filter((w) => w.method === 'POST').length), 1);
    assert((await page.evaluate(() => window.__writes.length)) >= before);
    await page.evaluate(() => { window.__refuse = true; window.__libraryRefuse = true; window.deskV1WhatInvalidateMaterials(); });
    await choose({ name: 'refused.png', mimeType: 'image/png', buffer: png }, 'image/*');
    await page.waitForFunction((n) => window.DeskV1Store.state().families.find((f) => f.id === 'fam-30-testers').assets.length === n, assetCount);
    await page.evaluate(() => { window.__refuse = false; });

    for (const panel of ['what', 'where']) {
      await page.evaluate((panel) => window.deskV1GotoCampaignPanel(panel, { campaignId: 'camp-1' }), panel);
      await page.waitForFunction(() => document.querySelector('.desk-v1-campaign')?.textContent.includes('Autumn launch update'));
    }
    await page.evaluate(() => {
      const camp = window.DeskV1Store.state().campaigns.find((c) => c.id === 'camp-1');
      camp.when = camp.when || {};
      camp.when.slots = [{ id: 'rename-slot', at: new Date().toISOString(), origin: 'user', filled: { title: 'Old copied title', versionId: 'v-testers-li', channelId: 'ch-li-page' } }];
      window.deskV1Nav('calendar', { campaignId: 'camp-1' });
    });
    await page.waitForSelector('[data-cal-view]');
    assert(await page.evaluate(() => document.querySelector('.desk-v1-shell').textContent.includes('Autumn launch update')));
    assert.equal(await page.locator('[data-slot-id="rename-slot"] .desk-v1-cal-slotchip-title').textContent(), 'Autumn launch update ⚠ held');
    assert.deepEqual(errors.filter((e) => !/aborted|Failed to fetch|EventSource/i.test(e)), []);
    console.log(`PASS ${width}: prompt, rename, live Undo/refusal, local upload/Undo/refusal, What/Where/calendar`);
    await ctx.close();
  }
} finally { await browser.close(); }
