// Worktree-only UI; no live server, data writes or model calls.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';

const root = fileURLToPath(new URL('../..', import.meta.url));
const files = loadStaticJsCss(root);
const shots = resolve(root, '_scratch/studio_layout');
mkdirSync(shots, { recursive: true });
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    const fx = loadFixtures(), boards = new Map(), errors = [], generated = [];
    const page = await browser.newPage({ viewport: { width, height: 1050 } });
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      const json = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: readFileSync(resolve(root, 'static/index.html'), 'utf8') });
      if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
      if (path === '/api/config') return json({ desk_v1: true, desk_v1_live: true });
      if (path === '/api/projects') return json(fx.projects.map(p => ({ ...p, status: 'active', roster: [] })));
      if (path === '/api/characters') return json([{ name: 'dave', scope: 'global', agent_name: 'Dave', avatar: '' }]);
      if (path === '/api/desk/workspace') return json(workspaceFromFixtures(fx));
      if (path === '/api/desk/storyboard/chat') return json({ thread: [] });
      if (path === '/api/desk/engines') return json({ engines: [] });
      if (path === '/api/desk/exports') return json({ job: null });
      if (path === '/api/desk/materials') return json({ library: { image: [], video: [] }, recent: [], articles: [] });
      if (path === '/api/desk/studio/storyboards') return json({ storyboards: [] });
      if (path === '/api/desk/storyboard/generate') {
        generated.push(req.postDataJSON());
        return json({ agent: { name: 'Dave' }, scenes: [{ id: 'made', label: 'Opening', line: 'The story opens.', duration_sec: 3 }] });
      }
      const match = path.match(/^\/api\/desk\/(pieces|studio)\/([^/]+)\/storyboard$/);
      if (match) {
        const key = match[1] + ':' + match[2];
        const before = boards.get(key) || { rev: 0, story: '', scenes: [], pending_edits: [] };
        if (req.method() === 'GET') return json(before);
        const body = req.postDataJSON();
        assert.equal(body.rev, before.rev);
        const saved = { ...body, rev: before.rev + 1 };
        boards.set(key, saved); return json(saved);
      }
      return route.abort();
    });
    await page.goto('http://mc.smoke.test/');
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(() => window.sidebarNav('social'));
    await page.waitForSelector('.desk-v1-shell');
    for (const standalone of [false, true]) {
      const id = standalone ? 'layout-draft' : 'fam-install-video';
      const key = `${standalone ? 'studio' : 'pieces'}:${id}`;
      boards.set(key, { rev: 0, title: 'Studio layout', story: 'A builder opens the dashboard.', pending_edits: [], scenes: [
        { id: 'one', label: 'Open the dashboard', line: 'Show the project and its working agent.', duration_sec: 5 },
        { id: 'two', label: 'Arrange the scenes', line: 'Drag the next scene into the timeline.', duration_sec: 5 },
      ] });
      await page.evaluate(({ standalone, id }) => {
        if (standalone) window.DeskV1Store.state().studio.items = [{ id, kind: 'video', title: 'Studio layout', status: 'draft' }];
        window.deskV1Nav(standalone ? 'studio-create' : 'storyboard', standalone ? { kind: 'video', itemId: id } : { campaignId: 'camp-1', familyId: id });
      }, { standalone, id });
      await page.waitForSelector('[data-story-expand]');
      await page.waitForSelector('[data-sb-timeline] [data-scene-id="one"]');
      const geometry = () => page.evaluate(() => {
        const rect = s => { const r = document.querySelector(s).getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height, bottom: r.bottom }; };
        return { editor: rect('[data-storyboard]'), grid: rect('.desk-v1-sb-layout'), main: rect('.desk-v1-sb-main'),
          chat: rect('[data-sb-agent]'), timeline: rect('[data-sb-timeline-wrap]'), story: rect('[data-sb-story-panel]'), preview: rect('[data-sb-preview]') };
      });
      let g = await geometry();
      assert(g.timeline.width >= g.editor.width * .9, `timeline spans editor at ${width}: ${JSON.stringify(g)}`);
      assert(g.timeline.y >= g.grid.bottom - 1, 'timeline below both columns');
      if (width === 1440) {
        assert(Math.abs(g.chat.height - g.main.height) < 2, 'chat fills story + preview height');
        const before = g.chat.height;
        await page.locator('[data-chat-thread]').evaluate(el => { el.innerHTML = '<p>Conversation line</p>'.repeat(200); });
        g = await geometry();
        assert(Math.abs(g.chat.height - before) < 2, 'long conversation scrolls instead of stretching grid');
        assert(await page.locator('[data-chat-thread]').evaluate(el => el.scrollHeight > el.clientHeight));
        await page.locator('[data-chat-thread]').evaluate(el => { el.innerHTML = '<p>What should we change in this scene?</p>'; });
      } else {
        assert(g.chat.y >= g.main.bottom - 1, 'mobile chat stacks below preview');
      }
      // Blurring fresh text starts Saving before click. The compact card must
      // reserve that label's space so Make does not move out from the pointer.
      await page.locator('[data-sb-story]').fill('A fresh story.\n'.repeat(50));
      await page.click('[data-story-make]');
      assert.equal(await page.locator('[data-story-choice]').isVisible(), true, 'first click after typing reaches Make');
      await page.click('[data-story-cancel]');
      const edge = page.locator('[data-story-resize]');
      await edge.scrollIntoViewIfNeeded();
      const box = await edge.boundingBox();
      const initialHeight = (await geometry()).story.height;
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down(); await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2 + 70, { steps: 8 }); await page.mouse.up();
      g = await geometry();
      assert(g.story.height >= initialHeight + 60, 'bottom edge resizes story card');
      assert(g.preview.y >= g.story.bottom, 'preview does not overlap resized story');
      await edge.focus(); await page.keyboard.press('ArrowUp');
      assert((await geometry()).story.height < g.story.height, 'keyboard resizes story card');
      const story = page.locator('[data-sb-story]');
      const text = 'The dashboard opens.\nThe builder arranges a story.';
      await story.fill(text);
      await page.click('[data-story-expand]');
      const dialog = page.locator('[data-story-dialog]');
      assert.equal(await dialog.isVisible(), true);
      assert.equal(await story.inputValue(), text);
      assert.equal(await story.count(), 1, 'single mounted story value');
      assert((await dialog.boundingBox()).width >= width * .7, 'large overlay editor');
      await story.fill(text + '\nThe same text returns to the card.');
      const edited = await story.inputValue();
      assert.equal(await page.locator('[data-story-count]').innerText(), `${edited.length} / 20,000`);
      await page.screenshot({ path: resolve(shots, `story-expanded-${standalone ? 'studio' : 'campaign'}-${width}.png`) });
      await page.keyboard.press('Escape');
      await dialog.waitFor({ state: 'detached' });
      assert.equal(await dialog.count(), 0);
      assert.equal(await story.inputValue(), edited, 'Escape round-trips story');
      assert.equal(await page.locator('[data-story-expand]').evaluate(el => el === document.activeElement), true);
      await page.click('[data-story-make]');
      assert.equal(await page.locator('[data-story-choice]').isVisible(), true, 'card Make uses existing generation choices');
      await page.click('[data-story-cancel]');
      await page.click('[data-story-expand]');
      await page.click('[data-story-make]');
      await page.click('[data-story-append]');
      await page.waitForFunction(() => document.querySelector('[data-story-status]').dataset.kind === 'ok');
      assert.equal(generated.at(-1).story, edited, 'pop-out Make sends same story');
      assert.equal(boards.get(key).story, edited, 'same story saved with generated board');
      await page.click('[data-story-close]');
      await dialog.waitFor({ state: 'detached' });
      assert.equal(await story.inputValue(), edited, 'Close round-trips story');
      await page.waitForTimeout(750);
      await page.locator('[data-storyboard]').evaluate(el => el.closest('.desk-v1-body').scrollTop = 0);
      await page.screenshot({ path: resolve(shots, `editor-${standalone ? 'studio' : 'campaign'}-${width}.png`) });
      await page.locator('[data-sb-timeline-wrap]').scrollIntoViewIfNeeded();
      await page.screenshot({ path: resolve(shots, `timeline-${standalone ? 'studio' : 'campaign'}-${width}.png`) });
      const h = (await geometry()).story.height;
      await story.fill('long story '.repeat(2000));
      assert.equal(await page.locator('[data-story-count]').innerText(), '22,000 / 20,000');
      assert.equal((await geometry()).story.height, h, 'long text scrolls inside resized card');
      await page.click('[data-story-expand]');
      const requests = generated.length;
      await page.click('[data-story-make]');
      assert.match(await page.locator('[data-story-status]').innerText(), /over the 20,000 limit/);
      assert.equal(generated.length, requests, 'same limit enforced in pop-out');
      await story.fill(edited); await page.click('[data-story-close]');
      await dialog.waitFor({ state: 'detached' });
      await page.waitForTimeout(750);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'no horizontal document scroll');
      assert(await page.locator('[data-storyboard]').evaluate(el => el.scrollWidth <= el.clientWidth + 1), 'no horizontal editor scroll');
      assert.deepEqual(errors, []);
      console.log(`PASS layout, resize, round-trip, generation and limit ${standalone ? 'studio' : 'campaign'} ${width}`);
    }
    await page.close();
  }
} finally { await browser.close(); }
