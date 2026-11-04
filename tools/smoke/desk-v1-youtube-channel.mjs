// Real composer/store/preview, hermetic workspace, no signed-in pane or upload.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';
const root = resolve(import.meta.dirname, '../..'), files = loadStaticJsCss(root);
const index = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const browser = await chromium.launch({ headless: true });
try {
  for (const width of [1440, 390]) {
    const fx = loadFixtures(), errors = [], writes = [];
    const camp = fx.campaigns.find((c) => c.id === 'camp-1');
    const yt = { id: 'ch-yt', platform: 'youtube', identity: 'my-channel', label: 'YouTube · my-channel', capability: 'assisted', connected: true, browser_profile: 'youtube-channel' };
    fx.channels.push(yt); camp.plan.accounts = [yt.id];
    const fam = { id: 'fam-yt', campaignId: camp.id, kind: 'post', title: 'Internal title', body: 'Video title\nVideo description', assets: [], versions: [{ id: 'ver-yt', channelId: yt.id, state: 'drafting' }] };
    fx.families.push(fam);
    const page = await browser.newPage({ viewport: { width, height: 950 } });
    page.setDefaultTimeout(10000);
    page.on('pageerror', (e) => errors.push(e.message));
    await page.addInitScript((fixture) => { window.DeskV1Fixtures = fixture; }, fx);
    await page.route('**/*', (route) => {
      const req = route.request(), path = new URL(req.url()).pathname;
      const json = (v) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(v) });
      if (path === '/') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: index });
      if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
      if (path === '/api/config') return json({ desk_v1: true, desk_v1_live: false });
      if (path === '/api/projects') return json(fx.projects.map((p) => ({ ...p, status: 'active', activity_log: [], backlog: [], roster: [] })));
      if (path === '/api/characters') return json([]);
      if (req.method() !== 'GET') writes.push(path);
      return route.abort();
    });
    await page.goto('http://mc.smoke.test/');
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(() => window.sidebarNav('social'));
    await page.waitForFunction(() => window.DeskV1Store.state().campaigns.length);
    await page.evaluate(() => window.deskV1Nav('composer', { campaignId: 'camp-1', familyId: 'fam-yt' }));
    await page.waitForSelector('[data-composer]');
    const fill = await page.evaluate(() => {
      const ch = window.DeskV1Store.state().channels.find((c) => c.id === 'ch-yt');
      const A = window.DeskV1AssistedPost;
      return { approved: A.fillHTML(ch, { id: 'v-yt', state: 'approved' }),
        draft: A.fillHTML(ch, { id: 'v-yt', state: 'needs_review' }),
        enable: A.canEnable({ ...ch, capability: 'none' }), proof: window.DeskV1YouTubePost.uploadProof };
    });
    assert.match(fill.approved, /Fill in on YouTube/); assert.equal(fill.draft, ''); assert(fill.enable);
    assert.match(fill.proof, /private draft.*Details.*Publish\/Save/);
    assert.match(await page.locator('[data-composer-channel="ch-yt"]').textContent(), /my-channel/);
    assert.match(await page.locator('[data-youtube-requirements]').textContent(), /exactly one video.*First line = title.*Publish\/Save/);
    assert(await page.locator('[data-composer-review]').isDisabled());
    if (width === 390) await page.click('.desk-v1-composer-preview-toggle');
    assert.equal(await page.locator('.desk-v1-pv-yt-title').textContent(), 'Video title');
    assert.equal(await page.locator('[data-composer-preview-stage] .desk-v1-pv-copy').textContent(), 'Video description');
    // The media module calls update after an attachment finishes. Re-render from store
    // here to cover the same saved-asset shape without uploading a file externally.
    async function media(assets) {
      await page.evaluate((assets) => {
        const s = window.DeskV1Store.state(); s.families.find((f) => f.id === 'fam-yt').assets = assets;
        window.deskV1Nav('composer', { campaignId: 'camp-1', familyId: 'fam-yt' });
      }, assets);
    }
    await media([{ kind: 'image', src: 'data:image/png;base64,AA==', title: 'An image' }]);
    assert(await page.locator('[data-composer-review]').isDisabled());
    assert.equal(await page.locator('[data-composer-preview-stage] .desk-v1-pv-media img').count(), 0, 'image never masquerades as YouTube video');
    await media([{ kind: 'video', play: 'http://mc.smoke.test/mock.mp4', title: 'A video' }]);
    assert.equal(await page.locator('[data-composer-review]').isDisabled(), false);
    await page.fill('[data-composer-text]', 't'.repeat(101));
    assert(await page.locator('[data-composer-review]').isDisabled());
    await page.fill('[data-composer-text]', 'Title\n' + 'd'.repeat(5001));
    assert.match(await page.locator('[data-youtube-requirements]').textContent(), /5000 characters/);
    assert(await page.locator('[data-composer-review]').isDisabled());
    await page.fill('[data-composer-text]', 'Title\nDescription');
    assert.equal(await page.locator('[data-composer-review]').isDisabled(), false);
    await page.click('[data-composer-channel="ch-yt"]');
    assert.equal(await page.locator('[data-youtube-requirements]').isVisible(), false);
    assert.deepEqual(errors, []); assert.equal(writes.filter((p) => /approve|publish|assisted-post/.test(p)).length, 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    console.log(`PASS YouTube composer ${width}: channel, title/description, video-only, limits, preview, human gate`);
    await page.close();
  }
} finally { await browser.close(); }
