// Worktree UI, real media decoding and revision-guarded boards. No live services.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';
import { loadFixtures, workspaceFromFixtures, seedDeskV1Fixtures } from './desk-v1-fixture-api.mjs';

const root = fileURLToPath(new URL('../..', import.meta.url));
const files = loadStaticJsCss(root);
const clip = Buffer.from(readFileSync(resolve(root, 'tools/smoke/fixtures/uploaded-clip.base64'), 'utf8').trim(), 'base64');
const image = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64');
const origin = 'http://mc.smoke.test';
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    const fx = loadFixtures(), boards = new Map(), writes = [], errors = [], forbidden = [];
    const page = await browser.newPage({ viewport: { width, height: 1050 } });
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      const json = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: readFileSync(resolve(root, 'static/index.html'), 'utf8') });
      if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
      if (path === '/smoke/clip') {
        // Match send_file's byte ranges; a plain 200 fixture decodes but reports
        // seekable=[0,0] in Chromium, making every paused seek land back at zero.
        const range = /bytes=(\d+)-(\d*)/.exec(request.headers().range || '');
        const start = range ? Number(range[1]) : 0;
        const end = range?.[2] ? Math.min(Number(range[2]), clip.length - 1) : clip.length - 1;
        return route.fulfill({ status: range ? 206 : 200, contentType: 'video/mp4',
          headers: { 'Accept-Ranges': 'bytes', ...(range ? { 'Content-Range': `bytes ${start}-${end}/${clip.length}` } : {}) },
          body: clip.subarray(start, end + 1) });
      }
      if (path === '/smoke/picture') return route.fulfill({ contentType: 'image/png', body: image });
      if (path === '/smoke/delayed-picture') return new Promise(done => setTimeout(done, 700))
        .then(() => route.fulfill({ contentType: 'image/png', body: image }));
      if (path === '/api/config') return json({ desk_v1: true, desk_v1_live: true });
      if (path === '/api/projects') return json(fx.projects.map(project => ({ ...project, status: 'active', roster: [] })));
      if (path === '/api/characters') return json([]);
      if (path === '/api/desk/workspace') return json(workspaceFromFixtures(fx));
      if (path === '/api/desk/engines') return json({ engines: [] });
      if (path === '/api/desk/storyboard/chat') return json({ thread: [] });
      if (path === '/api/desk/materials') return json({ library: { image: [], video: [] }, recent: [], articles: [] });
      if (path === '/api/desk/studio/storyboards') return json({ storyboards: [] });
      if (path === '/api/desk/exports') return json({ job: null });
      if (path === '/api/local-auth/status') return json({ configured: true });
      const board = path.match(/^\/api\/desk\/(pieces|studio)\/([^/]+)\/storyboard$/);
      if (board) {
        const key = board[1] + ':' + board[2];
        const previous = boards.get(key) || { rev: 0, scenes: [], pending_edits: [] };
        if (request.method() === 'GET') return json(previous);
        assert.equal(request.method(), 'PUT');
        const body = request.postDataJSON();
        assert.equal(body.rev, previous.rev);
        writes.push(body);
        const saved = { ...body, rev: previous.rev + 1, scenes: body.scenes.map(scene => ({ ...scene,
          picture: scene.picture && { ...scene.picture, src: `${origin}/smoke/${scene.picture.kind === 'video' ? 'clip' : 'picture'}` } })) };
        boards.set(key, saved); return json(saved);
      }
      if (/render|estimate|passcode|generate/.test(path)) forbidden.push(path);
      return route.abort();
    });
    await page.goto(origin);
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(() => window.sidebarNav('social'));
    await page.waitForSelector('.desk-v1-shell');
    for (const standalone of [false, true]) {
      const id = standalone ? 'preview-draft' : 'fam-install-video';
      boards.set(`${standalone ? 'studio' : 'pieces'}:${id}`, { rev: 0, title: 'Preview test', pending_edits: [], scenes: [
        { id: 'clip', label: 'First: clip', line: 'Trimmed to one second.', duration_sec: 1,
          picture: { kind: 'video', path: 'desk/library/video/clip.mp4', src: `${origin}/smoke/clip` } },
        { id: 'picture', label: 'Second: picture', line: '', duration_sec: 1.2,
          picture: { kind: 'image', path: 'desk/library/image/picture.png', src: `${origin}/smoke/picture` } },
        { id: 'card', label: 'Third: card', line: 'Your scene instructions appear here.', duration_sec: 1, picture: null },
      ] });
      await page.evaluate(({ standalone, id }) => {
        if (standalone) {
          const state = window.DeskV1Store.state();
          state.studio.items = [{ id, kind: 'video', title: 'Preview test', status: 'draft' }];
        }
        window.deskV1Nav(standalone ? 'studio-create' : 'storyboard', standalone ? { kind: 'video', itemId: id } : { campaignId: 'camp-1', familyId: id });
      }, { standalone, id });
      await page.waitForSelector('[data-sb-timeline] [data-scene-id="clip"]');
      await page.waitForSelector('[data-preview-play]:not([disabled])');
      // The editor's existing paid-render panel estimates on mount. Preview
      // must add zero requests of its own, measured after that panel settles.
      // Wait for quiet (no new paid-panel request for 400ms), not a fixed
      // 150ms: on a loaded box the mount estimate landed after the window
      // and was miscounted as a preview call (2026-11-02 flake, 2 !== 1).
      for (let seen = -1, quiet = 0; quiet < 400; ) {
        await page.waitForTimeout(100);
        if (forbidden.length === seen) quiet += 100; else { seen = forbidden.length; quiet = 0; }
      }
      const baseline = writes.length;
      const paidBaseline = forbidden.length;
      assert.equal(await page.locator('[data-preview-player]').isVisible(), true);
      assert.equal(await page.locator('[data-preview-open]').count(), 0);
      assert.equal(await page.locator('[data-preview-close]').count(), 0);
      assert.equal(await page.locator('[data-preview-play]').isVisible(), true);
      assert.equal(await page.locator('[data-preview-stage]').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(0, 0, 0)');
      assert(!/free/i.test(await page.locator('[data-sb-preview]').innerText()));
      mkdirSync(resolve(root, '_scratch/desk-studio-export'), { recursive: true });
      await page.locator('[data-sb-preview]').scrollIntoViewIfNeeded();
      await page.screenshot({ path: resolve(root, `_scratch/desk-studio-export/preview-idle-${standalone ? 'studio' : 'campaign'}-${width}.png`) });
      const current = () => page.locator('[data-sb-preview]').getAttribute('data-preview-scene');
      assert.equal(await current(), 'clip');
      assert.equal(await page.locator('[data-preview-video]').isVisible(), false, 'idle stage stays black');
      await page.waitForFunction(() => document.querySelector('[data-preview-video]').readyState >= 2);
      assert(Math.abs(await page.locator('[data-preview-video]').evaluate(video => video.duration) - 2.4) < 0.02, 'real clip decoded');
      assert.equal(Number(await page.locator('[data-preview-seek]').getAttribute('max')), 3.2);
      const visits = [];
      await page.exposeFunction(`visit${standalone}`, id => visits.push({ id, time: Date.now() }));
      await page.evaluate(standalone => {
        const host = document.querySelector('[data-sb-preview]');
        const observer = new MutationObserver(() => window[`visit${standalone}`](host.dataset.previewScene));
        observer.observe(host, { attributes: true, attributeFilter: ['data-preview-scene'] });
        window.previewVisitObserver = observer;
      }, standalone);
      await page.locator('[data-preview-seek]').focus();
      await page.keyboard.press('Shift+Tab');
      assert.equal(await page.locator('[data-preview-play]').evaluate(el => el === document.activeElement), true, 'Play is in the Tab order');
      await page.keyboard.press('Space');
      await page.waitForFunction(() => !document.querySelector('[data-preview-video]').paused);
      assert.equal(await page.locator('[data-preview-pause]').evaluate(el => el === document.activeElement), true);
      await page.keyboard.press('Enter');
      assert.equal(await page.locator('[data-preview-play]').isVisible(), true, 'Space starts and Enter pauses');
      assert.equal(await page.locator('[data-preview-play]').evaluate(el => el === document.activeElement), true);
      await page.click('[data-preview-play]');
      await page.waitForFunction(() => document.querySelector('[data-preview-video]').currentTime > 0.15);
      assert.equal(await page.locator('[data-preview-video]').isVisible(), true);
      assert.equal(await page.locator('[data-preview-play]').isVisible(), false);
      // The clock keeps one shape every frame (Ron 2026-11-02: it flipped
      // between 0:03.4, 0:03.45 and 0:04 while playing).
      const clocks = await page.evaluate(() => new Promise(done => {
        const seen = [], out = document.querySelector('[data-preview-time]');
        const sample = n => { seen.push(out.textContent); n ? requestAnimationFrame(() => sample(n - 1)) : done(seen); };
        sample(30);
      }));
      const shape = /^\d+:\d\d\.\d \/ \d+:\d\d\.\d$/;
      assert(clocks.every(text => shape.test(text)), `clock keeps m:ss.t every frame: ${[...new Set(clocks)].join(' | ')}`);
      assert(new Set(clocks.map(text => text.length)).size === 1, 'clock width never changes while playing');
      await page.screenshot({ path: resolve(root, `_scratch/desk-studio-export/preview-playing-${standalone ? 'studio' : 'campaign'}-${width}.png`) });
      await page.click('[data-preview-pause]');
      const stopped = await page.locator('[data-preview-seek]').inputValue();
      await page.waitForTimeout(180);
      assert.equal(await page.locator('[data-preview-seek]').inputValue(), stopped, 'pause holds position');
      await page.click('[data-preview-play]');
      await page.waitForFunction(() => document.querySelector('[data-sb-preview]').dataset.previewScene === 'picture');
      assert(Number(await page.locator('[data-preview-seek]').inputValue()) < 1.25, 'clip uses its 1s trim, not the native 2.4s');
      assert.equal(await page.locator('[data-preview-image]').isVisible(), true);
      await page.waitForFunction(() => document.querySelector('[data-preview-image]').naturalWidth > 0);
      assert.equal(await page.locator('[data-sb-timeline] [aria-current="true"]').getAttribute('data-scene-id'), 'picture');
      await page.waitForFunction(() => document.querySelector('[data-sb-preview]').dataset.previewScene === 'card');
      assert.equal(await page.locator('[data-preview-title]').innerText(), 'Third: card');
      assert.equal(await page.locator('[data-preview-line]').innerText(), 'Your scene instructions appear here.');
      const pictureAt = visits.find(visit => visit.id === 'picture').time;
      const cardAt = visits.find(visit => visit.id === 'card').time;
      assert(Math.abs(cardAt - pictureAt - 1200) < 250, `picture duration: ${cardAt - pictureAt}ms`);
      await page.waitForFunction(() => document.querySelector('[data-preview-play]').textContent === 'Replay');
      assert.equal(Number(await page.locator('[data-preview-seek]').inputValue()), 3.2);
      assert.equal(writes.length, baseline, 'opening and playing do not save');
      assert.equal(forbidden.length, paidBaseline, 'preview adds no render, estimate, passcode or generation calls');
      const scrub = async seconds => page.locator('[data-preview-seek]').evaluate((el, value) => {
        el.value = value; el.dispatchEvent(new Event('input', { bubbles: true }));
      }, String(seconds));
      await scrub(0.6);
      await page.waitForFunction(() => Math.abs(document.querySelector('[data-preview-video]').currentTime - 0.6) < 0.05);
      assert.equal(await current(), 'clip');
      await scrub(1.3); assert.equal(await current(), 'picture');
      assert.equal(await page.locator('[data-preview-play]').innerText(), 'Play', 'scrubbing back from the end leaves Play');
      // Real edit controls persist order/length and refresh the visible stage.
      await page.locator('[data-sb-timeline] [data-scene-id="clip"]').focus();
      await page.keyboard.press('ArrowRight');
      await page.waitForFunction(() => document.querySelector('[data-sb-timeline] .desk-v1-video-scene').dataset.sceneId === 'picture');
      await page.waitForFunction(() => !document.querySelector('[data-preview-play]').disabled);
      assert.equal(await current(), 'picture');
      await page.locator('[data-sb-timeline] [data-scene-id="picture"] [data-trim-edge="r"]').focus();
      await page.keyboard.press('ArrowRight');
      await page.waitForFunction(() => Number(document.querySelector('[data-preview-seek]').max) > 4);
      assert.equal(await page.locator('[data-preview-play]').innerText(), 'Play', 'editing pauses/reset preview');
      assert.equal(Number(await page.locator('[data-preview-seek]').getAttribute('max')), 4.2);
      assert.equal(await current(), 'picture');
      await scrub(3.4);
      assert(await page.locator('[data-sb-timeline] [aria-current="true"]').evaluate(tile => {
        const a = tile.getBoundingClientRect(), b = tile.closest('[data-timebar-scroll]').getBoundingClientRect();
        return a.left >= b.left - 1 && a.right <= b.right + 1;
      }), 'the active scene follows into the horizontally scrolled timeline');
      await page.locator('[data-sb-preview]').evaluate(el => el.scrollIntoView({ block: 'start' }));
      mkdirSync(resolve(root, '_scratch/desk-studio-preview'), { recursive: true });
      await page.screenshot({ path: resolve(root, `_scratch/desk-studio-preview/${standalone ? 'studio' : 'campaign'}-${width}.png`) });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no page overflow');
      const bounds = await page.locator('[data-sb-preview]').boundingBox();
      assert(bounds.x >= 0 && bounds.x + bounds.width <= width + 1, 'player fits viewport');
      // Navigation while running releases the detached player (no ghost audio).
      await scrub(2.4); await page.click('[data-preview-play]');
      await page.waitForFunction(() => !document.querySelector('[data-preview-video]').paused);
      await page.evaluate(() => {
        window.detachedPreviewVideo = document.querySelector('[data-preview-video]');
        window.previewVisitObserver.disconnect();
        window.deskV1Nav('studio');
      });
      await page.waitForFunction(() => window.detachedPreviewVideo.paused && !window.detachedPreviewVideo.hasAttribute('src'));
      console.log(`PASS ${width} ${standalone ? 'standalone' : 'campaign'}: real clip/picture/card order, trim, pause/scrub, picture clock, highlight, reorder/retime/reopen, no paid calls, navigation cleanup`);
    }
    // A genuinely empty live board disables Preview with a visible reason.
    boards.set('pieces:fam-install-video', { rev: 0, scenes: [], pending_edits: [] });
    await page.evaluate(() => window.deskV1Nav('storyboard', { campaignId: 'camp-1', familyId: 'fam-install-video' }));
    await page.waitForSelector('[data-sb-no-scenes]');
    assert.equal(await page.locator('[data-preview-play]').isDisabled(), true);
    assert.equal(await page.locator('[data-preview-player]').isVisible(), true);
    assert.match(await page.locator('[data-preview-empty]').innerText(), /Add a scene/);
    // Existing media errors stay visible and still permit local card playback.
    boards.set('pieces:fam-install-video', { rev: 0, pending_edits: [], scenes: [{ id: 'broken', label: 'Missing picture', line: 'Keep this scene visible.', duration_sec: 1,
      picture: { kind: 'image', path: 'desk/library/image/missing.png', src: `${origin}/smoke/missing` } }] });
    await page.evaluate(() => window.deskV1Nav('storyboard', { campaignId: 'camp-1', familyId: 'fam-install-video' }));
    await page.waitForSelector('[data-sb-timeline] [data-scene-id="broken"]');
    await page.waitForFunction(() => document.querySelector('[data-preview-status]').textContent.includes('could not load'));
    await page.click('[data-preview-play]');
    assert.equal(await page.locator('[data-preview-card]').isVisible(), true);
    assert.match(await page.locator('[data-preview-status]').innerText(), /could not load/);
    await page.waitForFunction(() => document.querySelector('[data-preview-play]').textContent === 'Replay');
    assert.equal(await page.locator('[data-preview-player]').isVisible(), true);
    boards.set('pieces:fam-install-video', { rev: 0, pending_edits: [], scenes: [{ id: 'delayed', label: 'Loading picture', duration_sec: 1,
      picture: { kind: 'image', path: 'desk/library/image/delayed.png', src: `${origin}/smoke/delayed-picture` } }] });
    await page.evaluate(() => window.deskV1Nav('storyboard', { campaignId: 'camp-1', familyId: 'fam-install-video' }));
    await page.waitForSelector('[data-sb-timeline] [data-scene-id="delayed"]');
    await page.click('[data-preview-play]');
    await page.waitForTimeout(120);
    assert.equal(Number(await page.locator('[data-preview-seek]').inputValue()), 0, 'loading a picture does not consume its hold time');
    await page.waitForFunction(() => document.querySelector('[data-preview-image]').naturalWidth > 0);
    await page.waitForFunction(() => document.querySelector('[data-preview-play]').textContent === 'Replay');
    assert.deepEqual(errors, []);
    await page.close();
    // Demo examples never become preview scenes, even if their pictures exist.
    const demo = await browser.newPage({ viewport: { width, height: 950 } });
    await demo.route('**/*', route => {
      const path = new URL(route.request().url()).pathname;
      const json = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: readFileSync(resolve(root, 'static/index.html'), 'utf8') });
      if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
      if (path === '/api/config') return json({ desk_v1: true });
      if (path === '/api/projects') return json([{ id: 'clayrune', name: 'Clayrune', status: 'active', roster: [] }]);
      if (path === '/api/characters') return json([]);
      return route.abort();
    });
    await seedDeskV1Fixtures(demo);
    await demo.goto(origin);
    await demo.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await demo.evaluate(() => { window.sidebarNav('social'); window.deskV1Nav('studio-create', { kind: 'video' }); });
    await demo.waitForSelector('[data-sc-body] [data-storyboard]');
    assert.equal(Number(await demo.locator('[data-preview-seek]').getAttribute('max')), await demo.evaluate(() => {
      const id = document.querySelector('[data-storyboard]').dataset.familyId;
      return window.DeskV1Store.state().videoDetail[id].scenes.filter(s => !s.placeholder).reduce((sum, s) => sum + s.durationSec, 0);
    }));
    await demo.evaluate(() => {
      const id = document.querySelector('[data-storyboard]').dataset.familyId;
      window.DeskV1Store.state().videoDetail[id].scenes.forEach(s => { s.placeholder = true; });
    });
    await demo.evaluate(() => window.DeskV1StudioPreview.refresh(document.querySelector('[data-storyboard]').parentElement));
    assert.equal(await demo.locator('[data-preview-play]').isDisabled(), true);
    assert.equal(await demo.locator('[data-preview-player]').isVisible(), true);
    console.log(`PASS ${width}: empty black stage with reason; example scenes excluded; missing picture card playback; loading picture holds clock`);
    await demo.close();
  }
} finally { await browser.close(); }
