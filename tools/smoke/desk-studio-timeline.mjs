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
const seconds = text => { const [m, s] = text.split(':').map(Number); return m * 60 + s; };
const format = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
const near = (a, b, label) => assert(Math.abs(a - b) < 0.1, `${label}: ${a} vs ${b}`);
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
      assert.match(await tile.innerText(), /\d+:\d{2}/);
      assert.equal(await page.locator('[data-timebar-ruler]').count(), 1);
      assert.equal(await page.locator('[data-sb-timeline] [data-trim-edge="l"]').count(), 0);
      const scale = () => page.locator('[data-timebar-scroll]').evaluate(el => Number(el.dataset.pxPerSecond));
      const geometry = () => page.locator('[data-sb-timeline] .desk-v1-video-scene').evaluateAll(els => els.map(el => ({
        id: el.dataset.sceneId, width: el.getBoundingClientRect().width, left: el.getBoundingClientRect().left - el.closest('[data-timebar-track]').getBoundingClientRect().left,
        duration: el.querySelector('.desk-v1-video-scene-duration').textContent,
      })));
      const originalScale = await scale();
      await page.click('[data-timebar-zoom="in"]');
      near(await scale(), originalScale * 1.5, 'zoom in changes px/sec');
      await page.click('[data-timebar-zoom="out"]');
      near(await scale(), originalScale, 'zoom out restores px/sec');
      await page.click('[data-timebar-zoom="fit"]');
      const fitted = await geometry();
      const totalWidth = fitted.reduce((n, t) => n + t.width, 0);
      near(totalWidth, await page.locator('[data-timebar-scroll]').evaluate(el => el.clientWidth), 'Fit spans viewport');
      for (const t of fitted) near(t.width, seconds(t.duration) * await scale(), 'tile width follows clock');
      const ticks = await page.locator('[data-timebar-ruler] [data-time]').evaluateAll(els => els.map(el => ({ time: Number(el.dataset.time), left: parseFloat(el.style.left), label: el.textContent })));
      assert(ticks.length >= 2, 'ruler has time labels');
      for (const tick of ticks) { near(tick.left, tick.time * await scale(), 'ruler tick aligned'); assert.equal(tick.label, format(tick.time)); }
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
      const old = seconds(await duration());
      await page.locator(`[data-sb-timeline] [data-scene-id="${id}"] [data-trim-edge="r"]`).focus();
      await page.keyboard.press('ArrowRight');
      assert.equal(await duration(), format(old + 1));
      near(await scale(), originalScale, 'keyboard commit holds scale');
      await page.click('#desk-v1-undo'); assert.equal(await duration(), format(old));
      if (fitted.length >= 3) {
        const edge = page.locator(`[data-sb-timeline] [data-scene-id="${fitted[1].id}"] [data-trim-edge="r"]`);
        await edge.scrollIntoViewIfNeeded();
        const baseline = await geometry(), held = await scale(), box = await edge.boundingBox();
        const x = box.x + box.width / 2, y = box.y + box.height / 2;
        await page.mouse.move(x, y); await page.mouse.down();
        await page.mouse.move(x + 12, y, { steps: 3 });
        await page.mouse.move(x + Math.max(24, held * 2), y, { steps: 10 });
        const live = await geometry();
        for (const i of [0, 2]) near(live[i].width, baseline[i].width, `live neighbour ${i + 1} width`);
        const growth = live[1].width - baseline[1].width;
        assert(growth > 0, 'right edge grows scene 2');
        near(live[2].left - baseline[2].left, growth, 'scene 3 shifts by scene 2 growth');
        assert.equal(await page.locator('[data-timebar-badge]').isVisible(), true);
        assert.equal(await page.locator('[data-timebar-badge]').innerText(), live[1].duration);
        await page.mouse.up();
        const committed = await geometry();
        for (const i of [0, 2]) near(committed[i].width, baseline[i].width, `committed neighbour ${i + 1} width`);
        near(committed[2].left - baseline[2].left, growth, 'committed scene 3 shifts');
        near(await scale(), held, 'drag commit holds scale');
        assert.equal(await page.locator('[data-timebar-badge]').isVisible(), false);
        await page.click('#desk-v1-undo');
        const undone = await geometry();
        for (let i = 0; i < baseline.length; i++) near(undone[i].width, baseline[i].width, 'Undo restores width');
        // Cancel restores the original geometry without creating a trim command.
        await edge.scrollIntoViewIfNeeded(); const cancelBox = await edge.boundingBox();
        await page.mouse.move(cancelBox.x + cancelBox.width / 2, cancelBox.y + 20); await page.mouse.down();
        await page.mouse.move(cancelBox.x + held * 3 + 24, cancelBox.y + 20, { steps: 10 });
        await page.keyboard.press('Escape'); await page.mouse.up();
        const cancelled = await geometry();
        for (let i = 0; i < baseline.length; i++) near(cancelled[i].width, baseline[i].width, 'Escape restores width');
        // Zoomed trimming uses the NEW clock conversion and keeps scroll/scale
        // through the commit. A very large drag still clamps to one hour.
        await page.click('[data-timebar-zoom="in"]');
        const zoomedScale = await scale();
        await edge.scrollIntoViewIfNeeded();
        const maxBox = await edge.boundingBox();
        await page.mouse.move(maxBox.x + maxBox.width / 2, maxBox.y + 25); await page.mouse.down();
        await page.mouse.move(maxBox.x + 30, maxBox.y + 25, { steps: 3 });
        await page.mouse.move(maxBox.x + 4000 * zoomedScale, maxBox.y + 25, { steps: 4 }); await page.mouse.up();
        assert.equal((await geometry())[1].duration, '60:00');
        near(await scale(), zoomedScale, 'max duration commit holds zoomed scale');
        await edge.focus(); await page.keyboard.press('ArrowRight');
        assert.equal((await geometry())[1].duration, '60:00', 'keyboard respects max');
        await page.click('[data-timebar-zoom="fit"]');
        const tinyScale = await scale();
        for (const t of await geometry()) near(t.width, seconds(t.duration) * tinyScale, 'long board keeps ruler honest');
        await page.click('#desk-v1-undo');
        near(await scale(), tinyScale, 'Undo never refits');
        await page.click('[data-timebar-zoom="fit"]');
      }
      // One-second scenes keep true width (full text is in title/accessible name).
      for (let i = old; i > 0; i--) {
        await page.locator(`[data-sb-timeline] [data-scene-id="${id}"] [data-trim-edge="r"]`).focus();
        await page.keyboard.press('ArrowLeft');
      }
      assert.equal(await duration(), '0:01');
      near((await geometry())[0].width, await scale(), '1s has no misleading minimum width');
      assert.match(await tile.getAttribute('title'), /0:01/);
      for (let i = old; i > 1; i--) await page.click('#desk-v1-undo');
      assert.equal(await duration(), format(old));
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
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'timebar does not widen page');
      console.log(`PASS ${width} ${standalone ? 'standalone' : 'campaign'}: ruler/zoom, live+committed trim geometry, fixed scale, 1s, cancel, reorder, library drag, OS picture drop/PUT, Undo`);
    }
    // The saved board arrives after mount. Its durations determine the initial
    // Fit, not the temporary samples shown before that asynchronous load.
    await page.evaluate(() => {
      window.DeskV1Store.live = () => true;
      window.DeskV1Store.storyboard.load = async (_owner, detail) => {
        await new Promise(resolve => setTimeout(resolve, 50));
        detail.scenes = [1, 41, 156].map((durationSec, i) => ({ id: `loaded-${i}`, label: `New scene ${i + 1}`, durationSec }));
      };
      window.deskV1Nav('storyboard', { campaignId: 'camp-1', familyId: 'fam-install-video' });
    });
    await page.waitForFunction(() => document.querySelector('[data-timebar-title]')?.textContent === 'Timeline · 3:18 total');
    const loaded = await page.locator('[data-sb-timeline] .desk-v1-video-scene').evaluateAll(els => els.reduce((n, e) => n + e.getBoundingClientRect().width, 0));
    near(loaded, await page.locator('[data-timebar-scroll]').evaluate(el => el.clientWidth), 'saved board fits after load');
    // Capture the same loaded scenes in demo mode, without unrelated live
    // agent/history requests that this offline harness deliberately aborts.
    await page.evaluate(() => {
      window.DeskV1Store.live = () => false;
      window.deskV1Nav('storyboard', { campaignId: 'camp-1', familyId: 'fam-install-video' });
    });
    await page.waitForFunction(() => document.querySelector('[data-timebar-title]')?.textContent === 'Timeline · 3:18 total');
    await page.locator('[data-timebar-scroll]').scrollIntoViewIfNeeded();
    await page.screenshot({ path: resolve(root, `_scratch/desk-video/timebar-198s-${width}.png`) });
    console.log(`PASS ${width}: asynchronous saved-board initial Fit`);
    assert.deepEqual(errors, []);
    await page.close();
  }
} finally { await browser.close(); }
