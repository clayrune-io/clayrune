// Real SPA + real MP4 bytes. Hermetic APIs: no operator workspace mutation.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';
import { seedDeskV1Fixtures } from './desk-v1-fixture-api.mjs';
const root = fileURLToPath(new URL('../..', import.meta.url));
const files = loadStaticJsCss(root);
const mp4 = readFileSync(resolve(root, 'tools/smoke/fixtures/local-video.mp4'));
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', route => {
    const path = new URL(route.request().url()).pathname;
    const json = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/') return route.fulfill({ contentType: 'text/html', body: readFileSync(resolve(root, 'static/index.html'), 'utf8') });
    if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
    if (path === '/api/config') return json({ desk_v1: true });
    if (path === '/api/projects') return json([{ id: 'clayrune', name: 'Clayrune', status: 'active', domain: 'general', display_order: 0, roster: [] }]);
    if (path === '/api/characters') return json([]);
    if (path === '/small.mp4') return route.fulfill({ contentType: 'video/mp4', body: mp4 });
    return route.abort();
  });
  await seedDeskV1Fixtures(page);
  await page.goto('http://mc.smoke.test/');
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.desk-v1-shell');
  await page.evaluate(() => {
    const store = window.DeskV1Store;
    store.live = () => true; store.write = store.run;
    window.__uploads = [];
    store.api = async (method, url, body) => {
      if (url.startsWith('/api/desk/materials')) return { library: { image: [], video: [{ title: 'Studio', items: [{ kind: 'video', title: 'Rendered video', path: 'desk/library/video/Studio/render.mp4' }] }] } };
      if (body instanceof FormData) {
        window.__uploads.push(body.get('file').name);
        if (window.__refuse) throw new Error('upload refused by server');
        if (window.__empty) return { assets: [] };
        return { assets: [{ id: body.get('id'), kind: 'video', title: body.get('file').name, path: 'desk/library/video/Uploads/small.mp4', play: '/small.mp4' }] };
      }
      return {};
    };
    const f = store.state().families.find(f => f.id === 'fam-30-testers');
    f.kind = 'post'; f.assets = [];
    window.deskV1Nav('piece', { campaignId: f.campaignId, familyId: f.id });
  });
  async function choose() {
    await page.click('[data-piece-media] [data-add-media]');
    const chooser = page.waitForEvent('filechooser');
    await page.getByRole('menuitem', { name: 'From this computer…', exact: true }).click();
    const fc = await chooser;
    console.log('chooser accept:', await fc.element().getAttribute('accept'));
    await fc.setFiles({ name: 'small.mp4', mimeType: 'video/mp4', buffer: mp4 });
    await page.waitForTimeout(100);
  }
  await choose();
  const result = await page.evaluate(() => ({ uploads: window.__uploads, assets: window.DeskV1Store.state().families.find(f => f.id === 'fam-30-testers').assets, toast: document.querySelector('#toast-container')?.textContent }));
  console.log(JSON.stringify(result));
  // Current post-media policy is the #2 decision for Dave. Do not silently
  // widen the piece model: pin this evidence until he selects an option.
  assert.deepEqual(result.uploads, []);
  assert.equal(result.assets.length, 0);
  assert.match(result.toast, /PNG, JPG, GIF or WebP/);
  await page.click('[data-piece-media] [data-add-media]');
  assert.equal(await page.getByRole('menuitem', { name: 'Studio / Rendered video', exact: true }).count(), 0);
  console.log('CONFIRMED GAP: New post Add media hides library videos and rejects MP4 before M21');
  {
    await page.evaluate(() => {
      const f = window.DeskV1Store.state().families.find(f => f.id === 'fam-30-testers');
      f.kind = 'video';
      window.deskV1Nav('piece', { campaignId: f.campaignId, familyId: f.id });
    });
    await choose();
    console.log('VIDEO PIECE', await page.evaluate(() => ({ uploads: window.__uploads, assets: window.DeskV1Store.state().families.find(f => f.id === 'fam-30-testers').assets })));
  }
  assert.deepEqual(await page.evaluate(() => window.__uploads), ['small.mp4']);
  assert.equal(await page.evaluate(() => window.DeskV1Store.state().families.find(f => f.id === 'fam-30-testers').assets.at(-1).kind), 'video');
  await page.waitForFunction(() => document.querySelector('[data-piece-media] video')?.readyState >= 1);
  assert(await page.locator('[data-piece-media] video').evaluate(el => el.duration > 0));
  const count = await page.locator('[data-pmed-open]').count();
  for (const [flag, error] of [['__refuse', 'upload refused by server'], ['__empty', 'did not return the attached media file']]) {
    await page.evaluate(flag => { window[flag] = true; document.querySelector('#toast-container').innerHTML = ''; }, flag);
    await choose();
    assert.match(await page.locator('#toast-container').innerText(), new RegExp(error));
    assert.equal(await page.locator('[data-pmed-open]').count(), count, 'failed upload rolls back');
    await page.evaluate(flag => { window[flag] = false; }, flag);
  }
  await page.evaluate(() => {
    window.__createURL = URL.createObjectURL;
    URL.createObjectURL = () => { throw new Error('browser preview failed'); };
    document.querySelector('#toast-container').innerHTML = '';
  });
  await choose();
  assert.match(await page.locator('#toast-container').innerText(), /Could not add media: browser preview failed/);
  await page.evaluate(() => { URL.createObjectURL = window.__createURL; });
  assert.equal(await page.locator('[data-piece-local-upload]').count(), 0);
  console.log('PASS real MP4 paints/playback metadata; server refusal, malformed response and preview failure toast and roll back');
  assert.deepEqual(errors, []);
} finally { await browser.close(); }
