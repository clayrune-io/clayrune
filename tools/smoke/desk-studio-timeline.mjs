// Campaign and standalone share the timeline; real pointer/keyboard gestures.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';
import { seedDeskV1Fixtures } from './desk-v1-fixture-api.mjs';
const root = fileURLToPath(new URL('../..', import.meta.url));
const files = loadStaticJsCss(root);
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    const page = await browser.newPage({ viewport: { width, height: 950 } });
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
      return route.abort();
    });
    await seedDeskV1Fixtures(page);
    await page.goto('http://mc.smoke.test/');
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(() => window.sidebarNav('social'));
    await page.waitForSelector('.desk-v1-shell');
    for (const standalone of [false, true]) {
      await page.evaluate(standalone => {
        window.deskV1Nav(standalone ? 'studio-create' : 'storyboard', standalone ? { kind: 'video' } : { campaignId: 'camp-1', familyId: 'fam-install-video' });
      }, standalone);
      await page.waitForSelector('[data-sb-timeline]');
      const tile = page.locator('[data-sb-timeline] .desk-v1-video-scene').first();
      assert.match(await tile.innerText(), /\d+s/);
      const id = await tile.getAttribute('data-scene-id');
      const order = () => page.locator('[data-sb-timeline] .desk-v1-video-scene').evaluateAll(els => els.map(e => e.dataset.sceneId));
      let before = await order();
      if (before.length > 1) {
        await tile.focus(); await page.keyboard.press('ArrowRight');
        assert.notDeepEqual(await order(), before);
        await page.click('#desk-v1-undo');
        assert.deepEqual(await order(), before);
        if (width === 1440) {
          const source = page.locator(`.desk-v1-sb-scene[data-scene-id="${before[0]}"] [data-scene-handle]`);
          await source.scrollIntoViewIfNeeded();
          const a = await source.boundingBox();
          const target = page.locator(`[data-sb-timeline] .desk-v1-video-scene[data-scene-id="${before[1]}"]`);
          const b = await target.boundingBox();
          await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2); await page.mouse.down();
          await page.mouse.move(a.x + a.width / 2 + 20, a.y + a.height / 2, { steps: 5 });
          await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 12 }); await page.mouse.up();
          assert.notDeepEqual(await order(), before, 'scene-list handle drops onto timeline');
          await page.click('#desk-v1-undo'); assert.deepEqual(await order(), before);
        }
      }
      const duration = () => page.locator(`[data-sb-timeline] [data-scene-id="${id}"] .desk-v1-video-scene-duration`).innerText();
      const old = parseInt(await duration());
      await page.locator(`[data-sb-timeline] [data-scene-id="${id}"] [data-trim-edge="r"]`).focus();
      await page.keyboard.press('ArrowRight');
      assert.equal(await duration(), `${old + 1}s`);
      await page.click('#desk-v1-undo'); assert.equal(await duration(), `${old}s`);
      if (width === 1440) {
        const edge = page.locator(`[data-sb-timeline] [data-scene-id="${id}"] [data-trim-edge="r"]`);
        await edge.scrollIntoViewIfNeeded(); const box = await edge.boundingBox();
        await page.mouse.move(box.x + 3, box.y + box.height / 2); await page.mouse.down();
        await page.mouse.move(box.x + 23, box.y + box.height / 2, { steps: 10 }); await page.mouse.up();
        assert(parseInt(await duration()) > old);
        await page.click('#desk-v1-undo'); assert.equal(await duration(), `${old}s`);
      }
      // A library picture dropped BEFORE a tile creates a scene and uses the
      // existing storyboard command/Undo (no network in demo mode).
      before = await order();
      await tile.dispatchEvent('drop', { dataTransfer: await page.evaluateHandle(() => {
        const dt = new DataTransfer();
        dt.setData('application/x-clayrune-material', JSON.stringify({ kind: 'image', title: 'Dropped picture', path: 'desk/library/image/Studio/picture.png' }));
        return dt;
      }) });
      assert.equal((await order()).length, before.length + 1);
      assert.match(await page.locator('[data-sb-timeline] .desk-v1-video-scene').first().innerText(), /Dropped picture/);
      await page.click('#desk-v1-undo'); assert.deepEqual(await order(), before);
      if (width === 1440) {
        await page.evaluate(() => {
          window.DeskV1Store.state().materialLibrary.image = [{ title: 'Studio', items: [{ id: 'drag-picture', kind: 'image', title: 'Native library drag', path: 'desk/library/image/Studio/drag.png' }] }];
        });
        await tile.scrollIntoViewIfNeeded(); const target = await tile.boundingBox();
        await page.click('[data-timeline-material]');
        const pick = page.locator('[data-lp-pick]').first();
        await pick.waitFor(); const source = await pick.boundingBox();
        await page.mouse.move(source.x + 15, source.y + 15); await page.mouse.down();
        await page.mouse.move(source.x + 35, source.y + 35, { steps: 5 });
        await page.waitForTimeout(50);
        await page.mouse.move(target.x + 30, target.y + 20, { steps: 15 }); await page.mouse.up();
        await page.waitForFunction(() => !document.querySelector('[data-lp]'));
        assert.equal((await order()).length, before.length + 1, 'native library drag inserts a scene');
        await page.click('#desk-v1-undo'); assert.deepEqual(await order(), before);
      }
      // OS picture drop uses the real multipart picture route and real saved
      // storyboard command. Inspect the PUT, then Undo sends the previous list.
      let puts = [];
      await page.route('**/api/desk/**', route => {
        if (route.request().method() === 'PUT') {
          const body = route.request().postDataJSON(); puts.push(body);
          return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ...body, rev: puts.length }) });
        }
        if (route.request().url().endsWith('/pictures')) return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ path: 'desk/library/image/Storyboards/drop.png', title: 'drop.png', src: '' }) });
        return route.fallback();
      });
      await page.evaluate(() => { window.DeskV1Store.live = () => true; });
      await tile.dispatchEvent('drop', { dataTransfer: await page.evaluateHandle(() => {
        const dt = new DataTransfer(); dt.items.add(new File(['picture bytes'], 'drop.png', { type: 'image/png' })); return dt;
      }) });
      await page.waitForFunction(() => Array.from(document.querySelectorAll('[data-sb-timeline] .desk-v1-video-scene')).some(e => e.textContent.includes('New scene')));
      await page.waitForTimeout(50);
      assert.equal(puts.at(-1).scenes.length, before.length + 1);
      assert.equal(puts.at(-1).scenes[0].picture.path, 'desk/library/image/Storyboards/drop.png');
      await page.click('#desk-v1-undo'); await page.waitForTimeout(50);
      assert.equal(puts.at(-1).scenes.length, before.length);
      assert.deepEqual(await order(), before);
      await page.evaluate(() => { window.DeskV1Store.live = () => false; });
      mkdirSync(resolve(root, '_scratch/desk-video'), { recursive: true });
      await page.locator('[data-sb-timeline]').scrollIntoViewIfNeeded();
      await page.screenshot({ path: resolve(root, `_scratch/desk-video/timeline-${standalone ? 'studio' : 'campaign'}-${width}.png`) });
      assert.equal(await page.locator('[data-storyboard]').count(), 1);
      console.log(`PASS ${width} ${standalone ? 'standalone' : 'campaign'}: duration, trim, reorder, library drag, OS picture drop/PUT, Undo`);
    }
    assert.deepEqual(errors, []);
    await page.close();
  }
} finally { await browser.close(); }
