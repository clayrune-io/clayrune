// Worktree UI, offline export API: no paid engine, server restart or install.
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
const root = fileURLToPath(new URL('../..', import.meta.url));
const files = loadStaticJsCss(root), fx = loadFixtures();
const output = resolve(root, '_scratch/desk-studio-export');
mkdirSync(output, { recursive: true });
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    const page = await browser.newPage({ viewport: { width, height: 1050 } });
    const errors = [], requests = [], paid = [];
    page.on('pageerror', e => errors.push(e.message));
    let mode = 'held', polls = 0, starts = 0, job = null;
    const scenes = [
      { id: 'text', label: 'Text scene', line: 'Plain black on export', duration_sec: 1.25, picture: null },
      { id: 'clip', label: 'Trimmed clip', line: '', duration_sec: .5, picture: { kind: 'video', path: 'desk/library/video/Uploads/clip.mp4', src: '/clip.mp4' } },
    ];
    const clip = Buffer.from(readFileSync(resolve(root, 'tools/smoke/fixtures/uploaded-clip.base64'), 'utf8').trim(), 'base64');
    await page.route('**/*', async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      const json = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: readFileSync(resolve(root, 'static/index.html'), 'utf8') });
      if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
      if (path === '/clip.mp4') return route.fulfill({ contentType: 'video/mp4', body: clip });
      if (path === '/api/config') return json({ desk_v1: true, desk_v1_live: true });
      if (path === '/api/projects') return json([{ id: 'clayrune', name: 'Clayrune', status: 'active', roster: [] }]);
      if (path === '/api/characters') return json([]);
      if (path === '/api/desk/workspace') return json(workspaceFromFixtures(fx));
      if (path === '/api/desk/engines') return json({ engines: [] });
      if (path === '/api/desk/storyboard/chat') return json({ thread: [] });
      if (path === '/api/desk/materials') return json({ library: { image: [], video: [] }, recent: [], articles: [] });
      if (path === '/api/desk/studio/storyboards') return json({ storyboards: [] });
      if (path.endsWith('/storyboard')) return json({ rev: 0, scenes, pending_edits: [] });
      if (path === '/api/desk/exports') {
        if (req.method() === 'GET') return json({ job });
        starts++; requests.push(req.postDataJSON());
        if (mode === 'held') return json({ status: 'held', message: 'Approve ffmpeg in Settings > Add-ons, then click Export again.' }, 202);
        if (mode === 'error') return json({ error: 'Source clip no longer exists.' }, 400);
        job = { id: 'job', status: 'queued', progress: 0, message: 'Queued local export' };
        return json(job, 202);
      }
      if (path === '/api/desk/exports/job') {
        polls++;
        if (mode === 'lost') return json({ error: 'Export job not found. The server may have restarted; retry Export.' }, 404);
        if (mode === 'failed') return json({ id: 'job', status: 'failed', message: 'ffmpeg encoder failed' });
        job = polls % 2 ? { id: 'job', status: 'running', progress: 45, message: 'Prepared scene 1 of 2' }
          : { id: 'job', status: 'done', progress: 100, message: 'MP4 ready. Text-only scenes use plain black frames; titles are not burned in.', download: '/api/desk/exports/job/file' };
        return json(job);
      }
      if (path === '/api/desk/exports/job/file') return route.fulfill({ contentType: 'video/mp4', body: clip });
      if (/render|estimate|passcode|generate/.test(path)) paid.push(path);
      return route.abort();
    });
    await page.goto('http://mc.smoke.test/');
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(() => window.sidebarNav('social'));
    await page.waitForSelector('.desk-v1-shell');
    for (const standalone of [false, true]) {
      job = null; polls = 0; mode = 'held';
      await page.evaluate(standalone => {
        if (standalone) window.DeskV1Store.state().studio.items = [{ id: 'draft', kind: 'video', title: 'Export draft', status: 'draft' }];
        window.deskV1Nav(standalone ? 'studio-create' : 'storyboard', standalone ? { kind: 'video', itemId: 'draft' } : { campaignId: 'camp-1', familyId: 'fam-install-video' });
      }, standalone);
      await page.waitForSelector('[data-export-start]:not([disabled])');
      await page.waitForTimeout(700); // existing Render panel debounces its mount estimate
      const paidBefore = paid.length;
      await page.click('[data-export-start]');
      await page.waitForFunction(() => document.querySelector('[data-export-status]').textContent.includes('Approve ffmpeg'));
      assert.equal(await page.locator('[data-export-start]').isDisabled(), false);
      mode = 'done';
      await page.click('[data-export-start]');
      await page.waitForFunction(() => !document.querySelector('[data-export-progress]').hidden);
      assert.equal(await page.locator('[data-export-start]').isDisabled(), true);
      assert.deepEqual(requests.at(-1).scenes.map(s => [s.id, s.duration_sec]), [['text', 1.25], ['clip', .5]]);
      assert.equal(requests.at(-1).owner.kind, standalone ? 'studio' : 'piece');
      await page.waitForSelector('[data-export-download]:visible');
      assert.match(await page.locator('[data-export-status]').innerText(), /plain black/);
      assert.match(await page.locator('[data-export-open]').getAttribute('href'), /open=1/);
      assert.equal(paid.length, paidBefore, 'no paid calls or passcode');
      await page.locator('[data-sb-preview]').scrollIntoViewIfNeeded();
      await page.screenshot({ path: resolve(output, `export-done-${standalone ? 'studio' : 'campaign'}-${width}.png`) });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      // Return to the same editor: restore the completed link without a POST.
      const before = starts;
      await page.evaluate(() => window.deskV1Nav('studio'));
      await page.evaluate(standalone => window.deskV1Nav(standalone ? 'studio-create' : 'storyboard', standalone ? { kind: 'video', itemId: 'draft' } : { campaignId: 'camp-1', familyId: 'fam-install-video' }), standalone);
      await page.waitForSelector('[data-export-download]:visible');
      assert.equal(starts, before);
      mode = 'failed';
      await page.click('[data-export-start]');
      await page.waitForFunction(() => document.querySelector('[data-export-status]').textContent.includes('encoder failed'));
      assert.equal(await page.locator('[data-export-download]').isVisible(), false);
      mode = 'error';
      await page.click('[data-export-start]');
      await page.waitForFunction(() => document.querySelector('[data-export-status]').textContent.includes('Source clip no longer exists'));
      mode = 'lost';
      await page.click('[data-export-start]');
      await page.waitForFunction(() => document.querySelector('[data-export-status]').textContent.includes('server may have restarted'));
      assert.equal(await page.locator('[data-export-start]').isDisabled(), false);
      // Detaching a running UI stops polling; the server owns the ongoing job.
      mode = 'done'; await page.click('[data-export-start]');
      await page.waitForFunction(() => !document.querySelector('[data-export-progress]').hidden);
      await page.evaluate(() => window.deskV1Nav('studio'));
      const pollBefore = polls;
      await page.waitForTimeout(1100);
      assert.equal(polls, pollBefore);
      console.log(`PASS ${width} ${standalone ? 'standalone' : 'campaign'}: snapshot/trim, approval/retry, progress, download/reopen, failure/restart, navigation cleanup`);
    }
    assert.deepEqual(errors, []);
    await page.close();
  }
} finally { await browser.close(); }
