// MC-1111: real Studio/folder/explorer modules, fake API, both viewport widths.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';

const repo = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const shots = resolve(repo, '_scratch/mc1111');
mkdirSync(shots, { recursive: true });
const index = readFileSync(resolve(repo, 'static/index.html'), 'utf8');
const files = loadStaticJsCss(repo);
const gif = Buffer.from('R0lGODlhAQABAAAAACwAAAAAAQABAAA=', 'base64');
const browser = await chromium.launch({ headless: true });
try {
  for (const width of [1440, 390]) {
    const ctx = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 } });
    const page = await ctx.newPage();
    const errors = [], calls = [];
    page.on('pageerror', (e) => errors.push(e.message));
    const fx = loadFixtures();
    let folder = { id: 'Folder-owned', title: 'Launch photos', files: 0, items: [] }, deleted = false, failDelete = false, failCopy = false, failBrowse = false;
    const shortcuts = ['Home', 'Pictures', 'Videos', 'Desktop', 'Downloads', 'My project'].map((title, i) => ({ title, root: i === 5 ? 'project-test' : 'home', path: i === 0 || i === 5 ? '' : title }));
    await ctx.route('**/*', async (route) => {
      const req = route.request(), u = new URL(req.url()), path = u.pathname, method = req.method();
      const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: index });
      if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
      if (path === '/api/projects') return J(fx.projects.map((p) => ({ ...p, status: 'active', domain: 'general', emoji: '🧪', backlog: [], roster: [], project_path: '/smoke/' + p.id })));
      if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true });
      if (path === '/api/characters') return J([]);
      if (path === '/api/serve-image' || path === '/api/desk/explorer/preview') return route.fulfill({ contentType: 'image/gif', body: gif });
      if (!path.startsWith('/api/desk/')) return route.abort();
      let body; try { body = req.postDataJSON(); } catch { body = null; }
      calls.push({ path, method, body });
      if (path === '/api/desk/workspace') return J(workspaceFromFixtures(fx));
      if (path === '/api/desk/materials') return J({ library: Object.fromEntries(['image', 'video'].map((kind) => [kind, folder.items.some((i) => i.kind === kind) ? [{ ...folder, items: folder.items.filter((i) => i.kind === kind) }] : []])), recent: [], articles: [], projects: [] });
      if (path === '/api/desk/studio/storyboards') return J({ storyboards: [] });
      if (path === '/api/desk/studio/articles') return J({ articles: [] });
      if (path === '/api/desk/studio/usage') return J({ files: {}, rendering: [] });
      if (path === '/api/desk/library/folders') {
        if (method === 'GET') return J({ folders: deleted ? [] : [folder] });
        if (method === 'DELETE') {
          if (failDelete) return J({ error: 'This folder contains a file used by a piece or draft.' }, 409);
          deleted = true; return J({ deleted: true });
        }
        folder = { ...folder, title: body.title }; return J(folder, method === 'POST' ? 201 : 200);
      }
      if (path === '/api/desk/library/folders/copy') {
        const items = body.files.filter((it) => !failCopy || !it.path.endsWith('.mp4')).map((it) => {
          const title = it.path.split('/').pop(), kind = title.endsWith('.mp4') ? 'video' : 'image', path = `desk/library/${kind}/Folder-owned/${title}`;
          return { id: path, path, kind, title, src: kind === 'image' ? '/api/serve-image?path=copy' : null, play: null };
        });
        folder.items.push(...items); folder.files = folder.items.length;
        return J({ items, errors: failCopy ? [{ title: 'clip.mp4', root: 'home', path: 'Pictures/clip.mp4', error: 'That file changed while copying.' }] : [], folder: folder.title });
      }
      if (path === '/api/desk/explorer') {
        if (failBrowse) return J({ error: 'Could not read that folder. Try again.' }, 503);
        const here = u.searchParams.get('path') || '', all = u.searchParams.get('filter') === 'all', q = u.searchParams.get('q') || '';
        let items = here ? [
          { title: 'hero.png', path: here + '/hero.png', kind: 'image', selectable: true, src: '/api/desk/explorer/preview?image' },
          { title: 'clip.mp4', path: here + '/clip.mp4', kind: 'video', selectable: true },
          ...(all ? [{ title: 'readme.txt', path: here + '/readme.txt', selectable: false }] : []),
        ] : [{ title: 'Pictures', path: 'Pictures', directory: true }];
        if (q) items = items.filter((it) => it.title.includes(q));
        return J({ items, shortcuts, limited: q === 'limit', breadcrumbs: [{ title: 'Home', path: '' }, ...(here ? [{ title: here, path: here }] : [])] });
      }
      return J({});
    });
    await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(() => window.sidebarNav('social'));
    await page.waitForSelector('.desk-v1-shell');
    await page.evaluate(() => window.deskV1Nav('studio', {}));
    await page.waitForSelector('[data-lf-open]');
    await page.locator('[data-lf-open]').scrollIntoViewIfNeeded();
    await page.screenshot({ path: resolve(shots, `folders-${width}.png`) });
    await page.click('[data-lf-new]');
    await page.fill('[data-lf-name]', 'Product launch');
    await page.click('[data-lf-editor] [type=submit]');
    await page.waitForSelector('[data-fe-dir]');
    assert.equal(await page.locator('[data-file-explorer] h2').textContent(), 'Product launch');
    assert.equal(await page.locator('[data-fe-location]').count(), 6);
    await page.click('[data-fe-dir]');
    await page.waitForSelector('[data-fe-pick]');
    await page.locator('[data-fe-pick]').nth(0).click();
    await page.locator('[data-fe-pick]').nth(1).click();
    assert.equal(await page.locator('[data-fe-count]').textContent(), '2 selected');
    await page.screenshot({ path: resolve(shots, `explorer-${width}.png`) });
    const dimensions = await page.locator('.desk-fe').evaluate((el) => ({ left: el.getBoundingClientRect().left, right: el.getBoundingClientRect().right, overflow: el.scrollWidth > el.clientWidth }));
    assert(dimensions.left >= 0 && dimensions.right <= width && !dimensions.overflow);
    if (width === 390) {
      assert(await page.locator('[data-fe-copy]').evaluate((el) => el.getBoundingClientRect().bottom <= innerHeight));
      assert(await page.locator('[data-fe-copy]').evaluate((el) => el.getBoundingClientRect().height >= 44));
    }
    await page.selectOption('[data-fe-filter]', 'all');
    await page.waitForSelector('[data-fe-index="2"]');
    assert(await page.locator('[data-fe-index="2"]').isDisabled());
    await page.fill('[data-fe-search]', 'hero');
    await page.waitForFunction(() => document.querySelectorAll('[data-fe-pick]').length === 1);
    assert.equal(await page.locator('[data-fe-count]').textContent(), '2 selected');
    failCopy = true;
    await page.click('[data-fe-copy]');
    await page.waitForFunction(() => document.querySelector('[data-fe-count]').textContent === '1 selected');
    assert.match(await page.locator('[data-fe-results]').textContent(), /changed while copying/);
    failCopy = false;
    await page.click('[data-fe-copy]');
    await page.waitForFunction(() => document.querySelector('[data-fe-count]').textContent === '0 selected');
    assert.equal(await page.locator('[data-studio-file]').count(), 2);
    failBrowse = true;
    await page.locator('[data-fe-crumb]').first().click();
    await page.waitForSelector('[data-fe-retry]');
    assert.match(await page.locator('[data-fe-results]').textContent(), /Could not read/);
    failBrowse = false;
    await page.click('[data-fe-retry]');
    await page.waitForSelector('[data-fe-dir]');
    await page.keyboard.press('Escape');
    await page.waitForSelector('[data-file-explorer]', { state: 'detached' });
    await page.waitForSelector('[data-lf-rename]');
    await page.click('[data-lf-rename]');
    await page.fill('[data-lf-name]', 'Ready to post');
    await page.click('[data-lf-editor] [type=submit]');
    await page.waitForFunction(() => document.querySelector('[data-lf-open]').textContent.includes('Ready to post'));
    await page.evaluate(() => { window._mc1111pick = null; window.DeskV1LibraryPicker.open({ kinds: ['image', 'video'], onPick: (it) => window._mc1111pick = it }); });
    await page.waitForSelector('[data-lp-pick]');
    assert.equal(await page.locator('[data-lp-folder]').count(), 2); // All + one mixed folder
    assert.equal(await page.locator('[data-lp-pick]').count(), 2);
    await page.locator('[data-lp-pick]').first().click();
    await page.waitForFunction(() => window._mc1111pick);
    assert.match(await page.evaluate(() => window._mc1111pick.path), /Folder-owned/);
    failDelete = true;
    await page.click('[data-lf-delete]'); await page.click('[data-lf-confirm]');
    await page.waitForFunction(() => document.querySelector('[data-lf-error]').textContent.includes('used by a piece'));
    assert.equal(await page.locator('[data-lf-open]').count(), 1);
    failDelete = false;
    await page.click('[data-lf-confirm]');
    await page.waitForFunction(() => !document.querySelector('[data-lf-open]'));
    assert.equal(calls.filter((c) => c.path.endsWith('/folders/copy')).length, 2);
    assert.deepEqual(errors, []);
    console.log(`PASS MC-1111 ${width}: create/open, browse/breadcrumb, filter/search, multiselect, partial-copy retry, rename, picker, delete guard, layout`);
    await ctx.close();
  }
} finally { await browser.close(); }
