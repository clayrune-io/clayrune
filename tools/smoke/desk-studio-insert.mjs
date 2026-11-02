// Worktree UI in real Chromium, offline Desk API. No live server or paid calls.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';
import { seedDeskV1Fixtures } from './desk-v1-fixture-api.mjs';
const root = fileURLToPath(new URL('../..', import.meta.url));
const files = loadStaticJsCss(root);
const clip = Buffer.from(readFileSync(resolve(root, 'tools/smoke/fixtures/uploaded-clip.base64'), 'utf8').trim(), 'base64');
const videoSrc = 'http://mc.smoke.test/clip.mp4';
const browser = await chromium.launch();
const output = resolve(root, '_scratch/desk-studio-insert');
mkdirSync(output, { recursive: true });
try {
  for (const width of [1440, 390]) {
    const page = await browser.newPage({ viewport: { width, height: 1000 } });
    page.setDefaultTimeout(10000);
    const errors = [], writes = [];
    page.on('pageerror', e => errors.push(e.stack));
    await page.route('**/*', route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      const json = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: readFileSync(resolve(root, 'static/index.html'), 'utf8') });
      if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
      if (path === '/clip.mp4') return route.fulfill({ contentType: 'video/mp4', body: clip });
      if (path === '/api/config') return json({ desk_v1: true });
      if (path === '/api/projects') return json([{ id: 'clayrune', name: 'Clayrune', status: 'active', domain: 'general', display_order: 0, roster: [] }]);
      if (path === '/api/characters') return json([]);
      if (path.startsWith('/api/desk/') && req.method() === 'PUT') {
        const body = req.postDataJSON(); writes.push(body); return json({ ...body, rev: writes.length });
      }
      if (path.endsWith('/pictures')) return json({ path: 'desk/library/image/Storyboards/drop.png', title: 'drop.png', kind: 'image', src: '' });
      return route.abort();
    });
    await seedDeskV1Fixtures(page);
    await page.goto('http://mc.smoke.test/');
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(() => window.sidebarNav('social'));
    await page.waitForSelector('.desk-v1-shell');
    await page.evaluate(src => {
      window.DeskV1Store.state().materialLibrary = {
        image: [{ title: 'Insert tests', items: [{ id: 'insert-image', kind: 'image', title: 'Inserted picture', path: 'desk/library/image/Insert/picture.png' }] }],
        video: [{ title: 'Insert tests', items: [{ id: 'insert-clip', kind: 'video', title: 'Inserted clip', path: 'desk/library/video/Insert/clip.mp4', play: src }] }],
      };
    }, videoSrc);
    for (const standalone of [false, true]) {
      await page.evaluate(standalone => window.deskV1Nav(standalone ? 'studio-create' : 'storyboard', standalone ? { kind: 'video' } : { campaignId: 'camp-1', familyId: 'fam-install-video' }), standalone);
      await page.waitForSelector('[data-insert-before]');
      const tiles = page.locator('[data-sb-timeline] .desk-v1-video-scene');
      const gaps = page.locator('[data-insert-before]');
      const order = () => tiles.evaluateAll(els => els.map(el => el.dataset.sceneId));
      if (standalone) {
        // Standalone demo starts with one real scene and three examples.
        // Add a second real scene using the UI so there is a middle to test.
        assert.equal((await order()).length, 1);
        await gaps.last().click();
        await page.locator('[data-lp-pick]').first().click();
        await page.waitForFunction(() => document.querySelectorAll('[data-sb-timeline] .desk-v1-video-scene').length === 2);
      }
      const original = await order();
      assert(original.length > 1);
      assert.equal(await gaps.count(), original.length + 1);
      assert.match(await gaps.first().getAttribute('aria-label'), /before scene 1/);
      assert.match(await gaps.last().getAttribute('aria-label'), /after the last/);
      const undo = async () => { await page.click('#desk-v1-undo'); assert.deepEqual(await order(), original); };
      // Tab reaches the actual boundary buttons; Enter and Space open the
      // existing picker. All three positions persist and Undo restores order.
      for (const index of [0, 1, original.length]) {
        await tiles.last().locator('[data-trim-edge="r"]').focus();
        for (let n = 0; n <= index; n++) await page.keyboard.press('Tab');
        assert.equal(await page.evaluate(() => Number(document.activeElement.dataset.insertIndex)), index);
        await page.keyboard.press(index === 1 ? 'Space' : 'Enter');
        await page.locator('[data-lp-pick]').first().waitFor();
        await page.locator('[data-lp-pick]').first().focus();
        await page.keyboard.press('Enter');
        await page.waitForFunction(n => document.querySelectorAll('[data-sb-timeline] .desk-v1-video-scene').length === n, original.length + 1);
        const inserted = await order();
        assert.deepEqual(inserted.filter(id => original.includes(id)), original);
        assert(!original.includes(inserted[index]), `picker inserts at gap ${index}`);
        assert.equal(await page.evaluate(() => document.activeElement.hasAttribute('data-insert-before')), true, 'focus restored after repaint');
        await undo();
      }
      // A clip picked in the middle uses its decoded duration and leaves the
      // Preview hook working after timeline re-render.
      await gaps.nth(1).click();
      await page.getByRole('button', { name: 'Use Inserted clip from Insert tests' }).click();
      await page.waitForFunction(n => document.querySelectorAll('[data-sb-timeline] .desk-v1-video-scene').length === n, original.length + 1);
      assert.match(await tiles.nth(1).innerText(), /Inserted clip/);
      assert.match(await tiles.nth(1).innerText(), /0:02\.4/);
      await page.locator('[data-preview-seek]').fill('0');
      assert.equal(await page.locator('[data-sb-timeline] [aria-current="true"]').count(), 1);
      await undo();
      assert.equal(await page.locator('[data-sb-timeline] [aria-current="true"]').count(), 1, 'Preview refresh re-highlights replaced tiles');

      // A real Chromium library drag hides the chooser, shows the gap line,
      // and commits at that same boundary (synthetic events below cover OS).
      await tiles.nth(1).scrollIntoViewIfNeeded();
      const target = await tiles.nth(1).boundingBox();
      await page.click('[data-timeline-material]');
      const pick = page.getByRole('button', { name: 'Use Inserted picture from Insert tests' });
      await pick.waitFor();
      const source = await pick.boundingBox();
      await page.mouse.move(source.x + 15, source.y + 15); await page.mouse.down();
      await page.mouse.move(source.x + 35, source.y + 35, { steps: 5 });
      await page.waitForTimeout(50);
      await page.mouse.move(target.x + 2, target.y + 20, { steps: 15 });
      assert.equal(await page.locator('[data-insert-marker]').isVisible(), true);
      assert.equal(await page.locator('[data-insert-marker]').getAttribute('data-index'), '1');
      await page.mouse.up();
      await page.waitForFunction(() => !document.querySelector('[data-lp]'));
      assert.equal((await order()).length, original.length + 1);
      assert(!original.includes((await order())[1]));
      await undo();

      const data = await page.evaluateHandle(() => {
        const dt = new DataTransfer(); dt.setData('application/x-clayrune-material', JSON.stringify({ kind: 'image', title: 'Drag picture', path: 'desk/library/image/Insert/drag.png' })); return dt;
      });
      // Drag across tile halves, not just across buttons. The line and the
      // committed insertion must agree at start, middle and end.
      for (const index of [0, 1, original.length]) {
        const tile = tiles.nth(index === original.length ? index - 1 : index);
        await tile.scrollIntoViewIfNeeded();
        const box = await tile.boundingBox();
        const x = index === original.length ? box.x + box.width - 2 : box.x + 2;
        const y = box.y + 20;
        await tile.dispatchEvent('dragover', { dataTransfer: data, clientX: x, clientY: y });
        const marker = page.locator('[data-insert-marker]');
        assert.equal(await marker.isVisible(), true);
        assert.equal(await marker.getAttribute('data-index'), String(index));
        const lineX = await marker.evaluate(el => parseFloat(el.style.left));
        const expected = await tile.evaluate((el, end) => {
          const rect = el.getBoundingClientRect();
          return (end ? rect.right : rect.left) - el.closest('[data-sb-timeline]').getBoundingClientRect().left;
        }, index === original.length);
        assert(Math.abs(lineX - expected) < 0.1, 'marker exactly on the tile boundary');
        if (index === 1) await page.screenshot({ path: resolve(output, `marker-${standalone ? 'studio' : 'campaign'}-${width}.png`) });
        await tile.dispatchEvent('drop', { dataTransfer: data, clientX: x, clientY: y });
        await page.waitForFunction(n => document.querySelectorAll('[data-sb-timeline] .desk-v1-video-scene').length === n, original.length + 1);
        assert(!original.includes((await order())[index]));
        assert.equal(await page.locator('[data-insert-marker]').isVisible(), false);
        await undo();
      }
      await gaps.nth(1).dispatchEvent('dragover', { dataTransfer: data });
      await gaps.nth(1).dispatchEvent('dragleave', { relatedTarget: null });
      assert.equal(await page.locator('[data-insert-marker]').isVisible(), false);
      await gaps.nth(1).dispatchEvent('dragover', { dataTransfer: data });
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('[data-insert-marker]').isVisible(), false);
      await gaps.nth(1).dispatchEvent('dragover', { dataTransfer: data });
      await page.evaluate(() => document.dispatchEvent(new DragEvent('dragend')));
      assert.equal(await page.locator('[data-insert-marker]').isVisible(), false);
      assert.deepEqual(await order(), original);
      const unrelated = await page.evaluateHandle(() => { const dt = new DataTransfer(); dt.setData('text/plain', 'unrelated'); return dt; });
      await gaps.first().dispatchEvent('dragover', { dataTransfer: unrelated });
      assert.equal(await page.locator('[data-insert-marker]').isVisible(), false);

      // OS files: both ends plus a batch in the middle. Exercise actual
      // multipart upload and saved PUT through the existing storyboard bridge.
      await page.evaluate(() => { window.DeskV1Store.live = () => true; });
      for (const index of [0, 1, original.length]) {
        const count = index === 1 ? 2 : 1;
        const os = await page.evaluateHandle(count => {
          const dt = new DataTransfer();
          for (let i = 0; i < count; i++) dt.items.add(new File(['image'], `drop-${i}.png`, { type: 'image/png' }));
          return dt;
        }, count);
        await gaps.nth(index).dispatchEvent('dragover', { dataTransfer: os });
        assert.equal(await page.locator('[data-insert-marker]').getAttribute('data-index'), String(index));
        await gaps.nth(index).dispatchEvent('drop', { dataTransfer: os });
        await page.waitForFunction(n => document.querySelectorAll('[data-sb-timeline] .desk-v1-video-scene').length === n, original.length + count);
        assert.equal(writes.at(-1).scenes.length, original.length + count);
        for (let i = index; i < index + count; i++) assert.equal(writes.at(-1).scenes[i].picture.path, 'desk/library/image/Storyboards/drop.png');
        const savedUndo = page.waitForRequest(req => req.method() === 'PUT' && req.url().includes('/storyboard'));
        await undo();
        await savedUndo;
        assert.equal(writes.at(-1).scenes.length, original.length);
      }
      await page.evaluate(() => { window.DeskV1Store.live = () => false; });
      await page.click('[data-timebar-zoom="fit"]');
      await page.locator('[data-timebar-scroll]').scrollIntoViewIfNeeded();
      await page.screenshot({ path: resolve(output, `gaps-${standalone ? 'studio' : 'campaign'}-${width}.png`) });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      // Zoom changes control positions with the clock. Crowded short scenes
      // keep non-overlapping 44px targets without widening the timebar tiles.
      for (let n = 0; n < 7; n++) await page.click('[data-timebar-zoom="out"]');
      await page.waitForTimeout(80);
      const boxes = await gaps.evaluateAll(els => els.map(el => { const b = el.getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height }; }));
      for (let i = 0; i < boxes.length; i++) {
        assert.equal(boxes[i].w, 44); assert.equal(boxes[i].h, 44);
        for (let j = i + 1; j < boxes.length; j++) {
          const a = boxes[i], b = boxes[j];
          assert(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y, 'crowded gap controls never overlap');
        }
      }
      console.log(`PASS ${width} ${standalone ? 'standalone' : 'campaign'}: keyboard picker start/middle/end, clip + Preview refresh, drag markers/drop positions, cancel, OS batch upload/PUT/Undo, 44px targets`);
    }
    // Empty real timeline: one first-scene control, and Undo returns to empty.
    await page.evaluate(() => {
      window.DeskV1Store.state().videoDetail['fam-install-video'].scenes = [];
      window.DeskV1Store.state().studio.storyboard = [];
      window.deskV1Nav('storyboard', { campaignId: 'camp-1', familyId: 'fam-install-video' });
    });
    await page.waitForSelector('[data-insert-before]');
    assert.equal(await page.locator('[data-insert-before]').count(), 1);
    await page.getByRole('button', { name: 'Insert the first scene', exact: true }).click();
    await page.locator('[data-lp-pick]').first().click();
    await page.waitForSelector('[data-sb-timeline] .desk-v1-video-scene');
    assert.equal(await page.locator('[data-insert-before]').count(), 2);
    await page.click('#desk-v1-undo');
    assert.equal(await page.locator('[data-insert-before]').count(), 1);
    assert.deepEqual(errors, []);
    await page.close();
  }
} finally { await browser.close(); }
