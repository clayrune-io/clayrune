// Real Studio edits and browser wheel gestures, using worktree assets only.
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
    const page = await browser.newPage({ viewport: { width, height: 1050 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
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
      // Seed one picture; all edits use the editor's trim/repaint/Undo paths.
      await page.evaluate(standalone => {
        const state = window.DeskV1Store.state();
        const id = standalone ? 'autofit-draft' : 'fam-install-video';
        if (standalone) state.studio.items = [{ id, kind: 'video', title: 'Autofit test', status: 'draft' }];
        state.videoDetail[id] = { scenes: [{ id: 'picture', label: 'Picture', durationSec: 3,
          picture: { kind: 'image', path: 'desk/library/image/picture.png' } }], pendingEdits: [], jobs: [] };
        window.deskV1Nav(standalone ? 'studio-create' : 'storyboard', standalone ? { kind: 'video', itemId: id } : { campaignId: 'camp-1', familyId: id });
      }, standalone);
      await page.waitForSelector('[data-sb-timeline] [data-scene-id="picture"]');
      const scroll = page.locator('[data-timebar-scroll]');
      const scale = () => scroll.evaluate(el => Number(el.dataset.pxPerSecond));
      const fitted = async () => {
        const g = await scroll.evaluate(el => ({ width: el.clientWidth,
          track: el.querySelector('[data-timebar-track]').getBoundingClientRect().width, left: el.scrollLeft }));
        assert(g.track <= g.width + 1, `whole board fits at ${width}: ${JSON.stringify(g)}`);
        assert.equal(g.left, 0, 'fit starts at zero');
      };
      const trim = async (key, count = 1) => {
        for (let i = 0; i < count; i++) {
          await page.locator('[data-sb-timeline] [data-trim-edge="r"]').focus();
          await page.keyboard.press(key);
        }
      };
      await fitted();
      const initialScale = await scale();
      await trim('ArrowRight', 33);
      assert.equal(await page.locator('[data-timebar-title]').innerText(), 'Timeline · 0:36 total');
      await fitted();
      assert(Math.abs(initialScale / await scale() - 12) < .01, '3s to 36s automatically refits');
      assert(await page.locator('[data-timebar-ruler] [data-time]').evaluateAll(els => Math.max(...els.map(el => Number(el.dataset.time)))) >= 30, 'ruler reaches at least 0:30');
      assert.equal(await page.locator('[data-timebar-zoom="out"]').getAttribute('title'), 'Zoom out');
      assert.equal(await page.locator('[data-timebar-zoom="in"]').getAttribute('title'), 'Zoom in');
      assert.equal(await page.locator('[data-timebar-zoom="fit"]').getAttribute('title'), 'Fit whole video');
      mkdirSync(resolve(root, '_scratch/timebar-autofit'), { recursive: true });
      await scroll.scrollIntoViewIfNeeded();
      await page.screenshot({ path: resolve(root, `_scratch/timebar-autofit/36s-${standalone ? 'studio' : 'campaign'}-${width}.png`) });

      await page.click('[data-timebar-zoom="in"]');
      const manualScale = await scale();
      await trim('ArrowRight');
      assert.equal(await scale(), manualScale, 'duration edit preserves manual zoom');
      await page.click('#desk-v1-undo');
      assert.equal(await scale(), manualScale, 'Undo preserves manual zoom');
      await page.click('[data-timebar-zoom="fit"]');
      await fitted();
      await trim('ArrowRight');
      await fitted();
      assert(await scale() < manualScale, 'Fit restores automatic mode through edits');
      await page.click('#desk-v1-undo');
      await fitted();

      const wheel = async (modifier, deltaY) => {
        await scroll.scrollIntoViewIfNeeded();
        const box = await scroll.boundingBox();
        const x = box.x + box.width * .4, y = box.y + 12;
        await page.mouse.move(x, y);
        const before = await scroll.evaluate((el, clientX) => ({ scale: Number(el.dataset.pxPerSecond),
          time: (el.scrollLeft + clientX - el.getBoundingClientRect().left - el.clientLeft) / Number(el.dataset.pxPerSecond) }), x);
        await page.keyboard.down(modifier);
        try { await page.mouse.wheel(0, deltaY); } finally { await page.keyboard.up(modifier); }
        await page.waitForFunction(previous => Number(document.querySelector('[data-timebar-scroll]').dataset.pxPerSecond) !== previous, before.scale);
        const after = await scroll.evaluate((el, clientX) => ({ scale: Number(el.dataset.pxPerSecond),
          time: (el.scrollLeft + clientX - el.getBoundingClientRect().left - el.clientLeft) / Number(el.dataset.pxPerSecond) }), x);
        assert(deltaY < 0 ? after.scale > before.scale : after.scale < before.scale, `${modifier} wheel changes zoom in the requested direction`);
        assert(Math.abs(after.time - before.time) * after.scale < 1.1, 'pointer time remains anchored within one pixel');
        return after.scale;
      };
      await wheel('Control', -250);
      const wheelScale = await wheel('Meta', 80);
      await trim('ArrowRight');
      assert.equal(await scale(), wheelScale, 'wheel zoom remains manual through repaint');
      // Plain and Shift wheel retain browser scrolling; neither changes zoom.
      await scroll.scrollIntoViewIfNeeded();
      const box = await scroll.boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + 12);
      await page.mouse.wheel(100, 0);
      await page.keyboard.down('Shift');
      try { await page.mouse.wheel(0, 100); } finally { await page.keyboard.up('Shift'); }
      assert.equal(await scale(), wheelScale);
      await page.click('[data-timebar-zoom="fit"]');
      await fitted();
      await page.evaluate(() => {
        const el = document.querySelector('[data-timebar-scroll]');
        window.timebarWheelDefaults = [false, true].map(shiftKey => {
          const event = new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: 50, shiftKey });
          el.dispatchEvent(event); return event.defaultPrevented;
        });
      });
      assert.deepEqual(await page.evaluate(() => window.timebarWheelDefaults), [false, false]);

      // Ruler drag: right stretches, left squeezes; the grabbed time stays under the pointer.
      const ruler = page.locator('[data-timebar-ruler]');
      await ruler.scrollIntoViewIfNeeded();
      const rb = await ruler.boundingBox();
      const dragRuler = async (fromFrac, toFrac) => {
        const y = rb.y + rb.height / 2, x0 = rb.x + rb.width * fromFrac, x1 = rb.x + rb.width * toFrac;
        const timeAt = clientX => scroll.evaluate((el, cx) => (el.scrollLeft + cx - el.getBoundingClientRect().left - el.clientLeft) / Number(el.dataset.pxPerSecond), clientX);
        const before = { scale: await scale(), time: await timeAt(x0) };
        await page.mouse.move(x0, y);
        await page.mouse.down();
        await page.mouse.move(x1, y, { steps: 6 });
        await page.mouse.up();
        const after = { scale: await scale(), time: await timeAt(x1) };
        assert(Math.abs(after.time - before.time) * after.scale < 1.5, `ruler drag keeps grabbed time under pointer: ${JSON.stringify({ before, after })}`);
        return after.scale / before.scale;
      };
      assert(await dragRuler(.25, .5) > 1.9, 'dragging the ruler right stretches the scale');
      const stretched = await scale();
      await trim('ArrowRight');
      assert.equal(await scale(), stretched, 'ruler stretch stays manual through edits');
      assert(await dragRuler(.5, .25) < .6, 'dragging the ruler left squeezes the scale');
      await page.click('#desk-v1-undo');
      await page.click('[data-timebar-zoom="fit"]');
      await fitted();
      console.log(`PASS ${width} ${standalone ? 'standalone' : 'campaign'}: 3s → 36s autofit/ruler, manual edit+Undo, Fit resets, Ctrl/Meta-wheel anchored zoom, plain/Shift scrolling retained, ruler drag stretch/squeeze`);
    }
    assert.deepEqual(errors, []);
    await page.close();
  }
} finally { await browser.close(); }
