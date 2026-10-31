// Real MP4 decoding with worktree UI, a revision-guarded fake Desk API, no paid calls.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const root = fileURLToPath(new URL('../..', import.meta.url));
const staticFiles = loadStaticJsCss(root);
const clip = Buffer.from(readFileSync(resolve(root, 'tools/smoke/fixtures/uploaded-clip.base64'), 'utf8').trim(), 'base64');
const src = 'http://mc.smoke.test/smoke/clip';
const browser = await chromium.launch();
let checks = 0;
try {
  for (const width of [1440, 390]) {
    const fx = loadFixtures();
    const family = fx.families.find(f => f.kind === 'video');
    family.title = 'Campaign capture'; family.assets = []; family.versions = [];
    const boards = new Map(), writes = [], patches = [], errors = [];
    let uploads = 0, sceneUploads = 0, conflict = false;
    const page = await browser.newPage({ viewport: { width, height: width === 1440 ? 1400 : 1000 } });
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: readFileSync(resolve(root, 'static/index.html'), 'utf8') });
      if (path === '/smoke/clip') return route.fulfill({ contentType: 'video/mp4', body: clip });
      if (staticFiles[path]) return route.fulfill({ contentType: staticFiles[path][0], body: staticFiles[path][1] });
      if (path === '/api/config') return json({ desk_v1: true, desk_v1_live: true });
      if (path === '/api/projects') return json(fx.projects.map(p => ({ ...p, status: 'active', roster: [] })));
      if (path === '/api/characters') return json([]);
      if (path === '/api/desk/storyboard/chat') return json({ thread: [] });
      if (path === '/api/desk/engines') return json({ engines: [] });
      if (path === '/api/local-auth/status') return json({ configured: true });
      if (path === '/api/desk/workspace') return json(workspaceFromFixtures(fx));
      if (path === '/api/desk/materials') return json({ library: { image: [], video: [{ title: 'Uploads', items: family.assets }] }, articles: [], recent: [] });
      if (path === '/api/desk/studio/storyboards') return json({ storyboards: [] });
      if (path === `/api/desk/pieces/${family.id}/assets` && req.method() === 'POST') {
        uploads++;
        const id = /name="id"\r?\n\r?\n([^\r\n]+)/.exec(req.postData())?.[1];
        assert(id, 'What uploads an asset ID in multipart');
        family.assets.push({ id, title: 'uploaded.mp4', path: 'desk/library/video/Uploads/uploaded.mp4', kind: 'video', play: src, src: null });
        return json(family, 201);
      }
      if (path === `/api/desk/pieces/${family.id}` && req.method() === 'PATCH') {
        const body = req.postDataJSON(); patches.push(body); Object.assign(family, body); return json(family);
      }
      const match = path.match(/^\/api\/desk\/(pieces|studio)\/([^/]+)\/storyboard$/);
      if (match) {
        const key = match[1] + ':' + match[2];
        const board = boards.get(key) || { rev: 0, scenes: [], title: '', pending_edits: [] };
        if (req.method() === 'GET') return json(board);
        const body = req.postDataJSON();
        if (conflict) {
          conflict = false; boards.set(key, { ...board, rev: board.rev + 1, scenes: [{ id: 'other-tab', label: 'Other tab wins', duration_sec: 4, picture: null }] });
          return json({ error: 'changed elsewhere' }, 409);
        }
        if (body.rev !== board.rev) return json({ error: 'changed elsewhere' }, 409);
        writes.push(body);
        const saved = { ...board, ...body, rev: board.rev + 1,
          scenes: body.scenes.map(s => ({ ...s, picture: s.picture ? { ...s.picture, src: s.picture.kind === 'video' ? src : '' } : null })) };
        boards.set(key, saved); return json(saved);
      }
      if (path.endsWith('/storyboard/pictures')) {
        sceneUploads++;
        return json({ path: 'desk/library/video/Storyboards/clip.mp4', title: 'uploaded.mp4', kind: 'video', src }, 201);
      }
      return route.abort();
    });
    await page.goto('http://mc.smoke.test/');
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(() => window.sidebarNav('social'));
    await page.waitForSelector('.desk-v1-shell');
    const what = async () => {
      await page.evaluate(f => window.deskV1Nav('campaign', { campaignId: f.campaignId, panel: 'what' }), family);
      await page.waitForSelector(`[data-what-row][data-family-id="${family.id}"]`);
    };
    const card = () => page.locator(`[data-what-row][data-family-id="${family.id}"]`);
    await what();
    // Use What's actual upload menu and file chooser.
    await card().locator('[data-add-media]').first().click();
    const chooser = page.waitForEvent('filechooser');
    await page.getByRole('menuitem', { name: /From this computer/i }).click();
    await (await chooser).setFiles({ name: 'uploaded.mp4', mimeType: 'video/mp4', buffer: clip });
    await page.waitForFunction(() => window.DeskV1Store.state().families.find(f => f.title === 'Campaign capture')?.assets?.[0]?.path);
    assert.equal(uploads, 1); checks++;
    await card().locator('[data-primary-action]').click();
    await page.waitForSelector('[data-sb-title] [data-piece-title-edit]:not([disabled])');
    await page.waitForSelector('.desk-v1-sb-scene video[data-scene-clip]');
    await page.waitForFunction(() => document.querySelector('.desk-v1-sb-scene video')?.readyState >= 2);
    assert.equal(await page.locator('[data-sb-title]').innerText(), 'Campaign capture');
    const key = 'pieces:' + family.id;
    assert.equal(boards.get(key).scenes.length, 1);
    assert(Math.abs(boards.get(key).scenes[0].duration_sec - 2.4) < 0.02);
    assert.equal(boards.get(key).assets_seeded, true);
    assert.equal(await page.locator('[data-sb-timeline] video[data-scene-clip]').count(), 1); checks += 5;
    mkdirSync(resolve(root, '_scratch/clip-editor'), { recursive: true });
    await page.screenshot({ path: resolve(root, `_scratch/clip-editor/uploaded-${width}.png`), fullPage: true });
    // Rename and Undo use real title UI and the normal PATCH path.
    await page.locator('[data-piece-title-edit]').click();
    await page.locator('[data-piece-name-input]').fill('Edited capture');
    await page.locator('[data-piece-name-input]').press('Enter');
    await page.waitForFunction(() => document.querySelector('[data-piece-title-edit]')?.textContent === 'Edited capture');
    assert.equal(patches.at(-1).title, 'Edited capture');
    await page.click('#desk-v1-undo');
    await page.waitForFunction(() => document.querySelector('[data-piece-title-edit]')?.textContent === 'Campaign capture');
    assert.equal(patches.at(-1).title, 'Campaign capture'); checks += 2;
    // Trim, persist, reopen without reseeding; then delete all scenes and reopen.
    await page.locator('[data-sb-timeline] [data-trim-edge="r"]').focus();
    await page.keyboard.press('ArrowLeft');
    await page.waitForFunction(() => document.querySelector('[data-timebar-title]')?.textContent.includes('0:01.4'));
    assert(Math.abs(boards.get(key).scenes[0].duration_sec - 1.4) < 0.02);
    await what(); await card().locator('[data-primary-action]').click();
    await page.waitForSelector('[data-piece-title-edit]:not([disabled])');
    assert.equal(boards.get(key).scenes.length, 1); checks += 2;
    await page.click('[data-scene-library]');
    await page.waitForSelector('[data-lp-pick]');
    await page.click('[data-lp-pick]');
    await page.waitForFunction(() => document.querySelector('[data-timebar-title]')?.textContent.includes('0:02.4'));
    assert.equal(boards.get(key).scenes[0].picture.kind, 'video'); checks++;
    // Unsupported video must toast before any scene upload request.
    const pick = page.waitForEvent('filechooser');
    await page.click('[data-scene-picture]');
    await (await pick).setFiles({ name: 'broken.anything', mimeType: 'video/x-unknown', buffer: Buffer.from('not a clip') });
    await page.waitForFunction(() => document.querySelector('.toast')?.textContent.includes('cannot play'));
    assert.equal(sceneUploads, 0); checks++;
    // Valid per-scene upload needs no extension allowlist and takes native duration.
    const replace = page.waitForEvent('filechooser');
    const savedUpload = page.waitForResponse(r => r.request().method() === 'PUT' && new URL(r.url()).pathname.endsWith('/storyboard'));
    await page.click('[data-scene-picture]');
    await (await replace).setFiles({ name: 'capture', mimeType: 'video/mp4', buffer: clip });
    await savedUpload;
    await page.waitForFunction(() => document.querySelector('[data-timebar-title]')?.textContent.includes('0:02.4'));
    assert.equal(sceneUploads, 1); checks++;
    await page.click('[data-scene-delete]');
    await page.waitForFunction(() => !!document.querySelector('[data-sb-no-scenes]'));
    await what(); await card().locator('[data-primary-action]').click();
    await page.waitForSelector('[data-piece-title-edit]:not([disabled])');
    assert.equal(await page.locator('.desk-v1-sb-scene').count(), 0); checks++;
    // A stale seed adopts the competing tab rather than retrying or appending.
    boards.delete(key); conflict = true;
    await what(); await card().locator('[data-primary-action]').click();
    await page.waitForSelector('[data-piece-title-edit]:not([disabled])');
    assert.match(await page.locator('[data-scenes]').innerText(), /Other tab wins/); checks++;
    // Standalone titles use the same control, guarded board PUT and Undo.
    await page.evaluate(() => window.deskV1Nav('studio-create', { kind: 'video' }));
    await page.waitForSelector('[data-piece-title-edit]');
    const original = await page.locator('[data-piece-title-edit]').innerText();
    await page.locator('[data-piece-title-edit]').click();
    await page.locator('[data-piece-name-input]').fill('Standalone clip');
    await page.locator('[data-piece-name-input]').press('Enter');
    await page.waitForFunction(() => document.querySelector('[data-piece-title-edit]')?.textContent === 'Standalone clip');
    assert.equal(writes.at(-1).title, 'Standalone clip');
    await page.click('#desk-v1-undo');
    await page.waitForFunction(title => document.querySelector('[data-piece-title-edit]')?.textContent === title, original);
    assert.equal(writes.at(-1).title, original); checks += 2;
    // Seed multiple uploaded videos in asset order; ignore images and never
    // overwrite a non-empty board or repeat a completed seed.
    const order = await page.evaluate(async ({ src, width }) => {
      const store = window.DeskV1Store, owner = { kind: 'piece', id: 'seed-order-' + width };
      const detail = { scenes: [] };
      const board = await store.storyboard.load(owner, detail);
      const family = { assets: [
        { id: 'second', title: 'Second asset', path: 'desk/library/video/Uploads/second.mp4', kind: 'video', play: src },
        { id: 'image', title: 'Picture', path: 'desk/library/image/Uploads/pic.png', kind: 'image' },
        { id: 'first', title: 'First asset', path: 'desk/library/video/Uploads/first.mp4', kind: 'video', play: src },
      ] };
      await window.DeskV1StudioClips.seed(owner, family, detail, board, () => true);
      const saved = await store.storyboard.load(owner, detail);
      const labels = detail.scenes.map(s => s.label);
      const durations = detail.scenes.map(s => s.durationSec);
      detail.scenes = [];
      await window.DeskV1StudioClips.seed(owner, family, detail, saved, () => true);
      return { labels, durations, repeated: detail.scenes.length };
    }, { src, width });
    assert.deepEqual(order.labels, ['Second asset', 'First asset']);
    assert(order.durations.every(d => Math.abs(d - 2.4) < 0.02));
    assert.equal(order.repeated, 0); checks += 3;
    assert.deepEqual(errors, []); checks++;
    console.log(`PASS ${width}: What MP4 upload/open, title+Undo, native duration, thumbnails, trim/reopen, bad-codec toast, extensionless upload, delete/no reseed, 409, Studio title+Undo`);
    await page.close();
  }
  console.log(`PASS ${checks} checks`);
} finally { await browser.close(); }
