// Studio page order, explorer drag-to-copy and the host path line. Real Studio and
// explorer modules, fake API, both widths. Drops are synthetic DragEvents.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';

const repo = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const shots = resolve(repo, '_scratch/studio_dnd');
mkdirSync(shots, { recursive: true });
const index = readFileSync(resolve(repo, 'static/index.html'), 'utf8');
const files = loadStaticJsCss(repo);
const gif = Buffer.from('R0lGODlhAQABAAAAACwAAAAAAQABAAA=', 'base64');
const BASE = 'C:\\Users\\you\\Documents\\Launch campaign\\Footage\\Raw material\\Autumn 2026 shoot';
const browser = await chromium.launch({ headless: true });
try {
  for (const width of [1440, 390]) {
    const ctx = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 } });
    const page = await ctx.newPage();
    const errors = [], copies = [];
    page.on('pageerror', (e) => errors.push(e.message));
    const fx = loadFixtures();
    let folder = { id: 'Folder-owned', title: 'Launch photos', files: 0, items: [] }, failClip = false, slow = false;
    const shortcuts = ['Home', 'Pictures'].map((title) => ({ title, root: 'home', path: title === 'Home' ? '' : title }));
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
      if (path === '/api/desk/workspace') return J(workspaceFromFixtures(fx));
      if (path === '/api/desk/materials') return J({ library: { image: [], video: [] }, recent: [], articles: [], projects: [] });
      if (path === '/api/desk/studio/storyboards') return J({ storyboards: [] });
      if (path === '/api/desk/studio/articles') return J({ articles: [] });
      if (path === '/api/desk/studio/usage') return J({ files: {}, rendering: [] });
      if (path === '/api/desk/library/folders') return J({ folders: [folder] });
      if (path === '/api/desk/library/folders/copy') {
        copies.push(body.files.map((f) => f.path.split('/').pop()));
        if (slow) await new Promise((r) => setTimeout(r, 500));
        const ok = body.files.filter((f) => !(failClip && f.path.endsWith('.mp4')));
        const items = ok.map((it) => {
          const title = it.path.split('/').pop(), kind = title.endsWith('.mp4') ? 'video' : 'image', p = `desk/library/${kind}/Folder-owned/${title}`;
          return { id: p, path: p, kind, title, src: kind === 'image' ? '/api/serve-image?path=copy' : null, play: null };
        });
        folder.items.push(...items); folder.files = folder.items.length;
        const errors = body.files.length > ok.length ? [{ title: 'clip.mp4', root: 'home', path: 'Pictures/clip.mp4', error: 'That file changed while copying.' }] : [];
        return J({ items, errors, folder: folder.title });
      }
      if (path === '/api/desk/explorer') {
        const here = u.searchParams.get('path') || '', q = u.searchParams.get('q') || '';
        let items = here ? [
          { title: 'hero.png', path: here + '/hero.png', kind: 'image', selectable: true, src: '/api/desk/explorer/preview?image' },
          { title: 'clip.mp4', path: here + '/clip.mp4', kind: 'video', selectable: true },
          { title: 'readme.txt', path: here + '/readme.txt', selectable: false },
        ] : [{ title: 'Pictures', path: 'Pictures', directory: true }];
        if (q) items = items.filter((it) => it.title.includes(q));
        return J({ items, shortcuts, allow_all_files: true, display_path: here ? `${BASE}\\${here}` : BASE,
          breadcrumbs: [{ title: 'Home', path: '' }, ...(here ? [{ title: here, path: here }] : [])] });
      }
      return J({});
    });
    await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(() => window.sidebarNav('social'));
    await page.waitForSelector('.desk-v1-shell');
    await page.evaluate(() => window.deskV1Nav('studio', {}));
    await page.waitForSelector('[data-lf-open]');
    await page.waitForTimeout(300); // Studio repaints once the folder list lands

    // 1. Order: tiles, Material library, Recent.
    const order = await page.evaluate(() => [...document.querySelector('[data-studio]').children].map((n) =>
      n.matches('.desk-v1-studio-tiles') ? 'tiles' : n.matches('[data-studio-lib]') ? 'lib' : n.matches('[data-studio-recent]') ? 'recent' : n.tagName === 'H3' ? 'h:' + n.textContent.trim() : n.tagName));
    assert.deepEqual(order, ['H2', 'tiles', 'h:Material library', 'lib', 'h:Recent', 'recent'], JSON.stringify(order));
    assert(await page.locator('[data-lf-open]').isVisible() && await page.locator('[data-studio-recent]').isVisible());
    await page.screenshot({ path: resolve(shots, `studio-${width}.png`) });

    // 2. Open the explorer on the (empty, so collapsed) folder, go into a folder.
    await page.evaluate((f) => window.DeskV1FileExplorer.open(f), folder);
    await page.waitForSelector('[data-fe-dir]');
    assert.equal(await page.locator('[data-fe-saved-box]').evaluate((d) => d.open), false);
    await page.click('[data-fe-dir]');
    await page.waitForSelector('[data-fe-pick]');
    assert.equal(await page.locator('[data-fe-dir]').count(), 0);

    // 3. Path line: full host path, copyable, left-ellipsized on narrow screens.
    const pathLine = page.locator('[data-fe-pathline]');
    assert(await pathLine.isVisible());
    assert.equal(await page.locator('[data-fe-path]').textContent(), `${BASE}\\Pictures`);
    assert.equal(await page.locator('[data-fe-path]').evaluate((n) => getComputedStyle(n).userSelect), 'text');
    const sel = await page.evaluate(() => { const n = document.querySelector('[data-fe-path]'); const r = document.createRange(); r.selectNodeContents(n); const s = getSelection(); s.removeAllRanges(); s.addRange(r); return s.toString(); });
    assert.equal(sel, `${BASE}\\Pictures`);
    const geo = await page.evaluate(() => {
      const box = document.querySelector('.desk-fe-path-text'), t = document.querySelector('[data-fe-path]');
      const b = box.getBoundingClientRect(), r = t.getBoundingClientRect(), fe = document.querySelector('.desk-fe');
      return { textOverflow: getComputedStyle(box).textOverflow, clipped: box.scrollWidth > box.clientWidth, left: r.left - b.left, right: r.right - b.right, boxRight: b.right, feOverflow: fe.scrollWidth > fe.clientWidth, vw: innerWidth };
    });
    assert.equal(geo.textOverflow, 'ellipsis');
    assert(!geo.feOverflow && geo.boxRight <= geo.vw, 'path line does not overflow the dialog');
    if (width === 390) {
      // Overflowing: the head (drive) is cut, the deepest folder end stays in view.
      assert(geo.clipped && geo.left < 0 && Math.abs(geo.right) <= 1, JSON.stringify(geo));
    } else assert(!geo.clipped, JSON.stringify(geo));
    await page.fill('[data-fe-search]', 'hero');
    await page.waitForFunction(() => document.querySelectorAll('[data-fe-pick]').length === 1);
    assert.equal(await page.locator('[data-fe-path-label]').textContent(), 'Searching in');
    assert.equal(await page.locator('[data-fe-path]').textContent(), `${BASE}\\Pictures`);
    await page.fill('[data-fe-search]', '');
    await page.waitForFunction(() => document.querySelectorAll('[data-fe-pick]').length === 2 + 0 || document.querySelectorAll('[data-fe-pick]').length === 3);
    assert.equal(await page.locator('[data-fe-path-label]').textContent(), '');
    assert(await page.locator('[data-fe-path-label]').isHidden());

    // 4. Drag and drop. Drag helper = synthetic DragEvents sharing one DataTransfer.
    const drag = (sel, targetSel, { drop = true, leave = false } = {}) => page.evaluate(([s, t, doDrop, doLeave]) => {
      const dt = new DataTransfer(), src = document.querySelector(s), dst = document.querySelector(t);
      const ev = (type, el) => { const e = new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt }); el.dispatchEvent(e); return e; };
      ev('dragstart', src);
      const out = { enter: ev('dragenter', dst).defaultPrevented, over: ev('dragover', dst).defaultPrevented, lit: dst.classList.contains('desk-fe-drop-on'), open: dst.open, hint: getComputedStyle(dst, '::after').content };
      if (doLeave) { ev('dragleave', dst); out.litAfterLeave = dst.classList.contains('desk-fe-drop-on'); }
      if (doDrop) { out.drop = ev('drop', dst).defaultPrevented; }
      ev('dragend', src);
      out.litAfterEnd = dst.classList.contains('desk-fe-drop-on');
      return out;
    }, [sel, targetSel, drop, leave]);
    assert.equal(await page.locator('[data-fe-index="2"]').getAttribute('draggable'), null); // unsupported file
    assert.equal(await page.locator('[data-fe-pick]').first().getAttribute('draggable'), 'true');
    const idx = (n) => `[data-fe-pick][data-fe-index="${n}"]`;

    // 4a. Hover only: highlight, collapsed block opens, leaving clears it, nothing copied.
    const hover = await drag(idx(0), '[data-fe-saved-box]', { drop: false, leave: true });
    // index.html swallows dragover/drop defaults document-wide, so only dragenter's defaultPrevented is ours.
    assert(hover.enter && hover.lit && hover.open, JSON.stringify(hover));
    assert.match(hover.hint, /Drop to copy/);
    assert.equal(hover.litAfterLeave, false);
    assert.equal(copies.length, 0);
    await page.evaluate(() => { document.querySelector('[data-fe-saved-box]').open = false; });

    // 4b. Drop an unselected file on the empty, collapsed block -> only that file.
    const mid = await page.evaluate(() => { const dt = new DataTransfer(), src = document.querySelector('[data-fe-index="0"]'), dst = document.querySelector('[data-fe-saved-box]'); src.dispatchEvent(new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer: dt })); dst.dispatchEvent(new DragEvent('dragenter', { bubbles: true, cancelable: true, dataTransfer: dt })); return { hl: dst.classList.contains('desk-fe-drop-on'), open: dst.open }; });
    assert(mid.hl && mid.open);
    await page.screenshot({ path: resolve(shots, `explorer-dragging-${width}.png`) });
    await page.evaluate(() => { const dt = new DataTransfer(), dst = document.querySelector('[data-fe-saved-box]'); dst.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt })); document.querySelector('[data-fe-index="0"]').dispatchEvent(new DragEvent('dragend', { bubbles: true })); });
    await page.waitForFunction(() => document.querySelectorAll('[data-studio-file]').length === 1);
    assert.deepEqual(copies, [['hero.png']]);
    assert.equal(await page.locator('[data-fe-count]').textContent(), '0 selected');
    assert.match(await page.locator('[data-fe-results] [role=status]').textContent(), /1 file copied/);

    // 4c. Drag a file that IS in the selection -> the whole selection is copied.
    await page.click(idx(0)); await page.click(idx(1));
    assert.equal(await page.locator('[data-fe-count]').textContent(), '2 selected');
    const selDrop = await drag(idx(1), '[data-fe-saved-box]');
    assert(selDrop.litAfterEnd === false);
    await page.waitForFunction(() => document.querySelectorAll('[data-studio-file]').length === 3);
    assert.deepEqual(copies[1], ['hero.png', 'clip.mp4']);
    assert.equal(await page.locator('[data-fe-count]').textContent(), '0 selected');

    // 4d. Drag a file that is NOT in the selection -> only that file; selection untouched.
    await page.click(idx(0));
    await drag(idx(1), '[data-fe-saved-box]');
    await page.waitForFunction(() => document.querySelectorAll('[data-studio-file]').length === 4);
    assert.deepEqual(copies[2], ['clip.mp4']);
    assert.equal(await page.locator('[data-fe-count]').textContent(), '1 selected');
    await page.click('[data-fe-clear]');

    // 4e. Partial failure through the drop path is reported like the button path.
    failClip = true;
    await drag(idx(1), '[data-fe-saved-box]');
    await page.waitForFunction(() => /changed while copying/.test(document.querySelector('[data-fe-results]').textContent));
    failClip = false;

    // 4f. While a copy is running a second drop is ignored (busy), and the highlight is not offered.
    slow = true;
    const n = copies.length;
    await page.click(idx(0));
    const first = page.click('[data-fe-copy]');
    await page.waitForFunction(() => /Copying/.test(document.querySelector('[data-fe-copy]').textContent));
    const busy = await drag(idx(1), '[data-fe-saved-box]');
    assert(!busy.enter && !busy.lit, JSON.stringify(busy));
    await first;
    await page.waitForFunction(() => !/Copying/.test(document.querySelector('[data-fe-copy]').textContent));
    slow = false;
    assert.equal(copies.length, n + 1);

    // 4g. Foreign drags (no dragstart from a result tile, e.g. an OS file) are not accepted.
    const foreign = await page.evaluate(() => { const dt = new DataTransfer(), dst = document.querySelector('[data-fe-saved-box]'); const e = new DragEvent('dragenter', { bubbles: true, cancelable: true, dataTransfer: dt }); dst.dispatchEvent(e); const d = new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt }); dst.dispatchEvent(d); return [e.defaultPrevented, d.defaultPrevented]; });
    assert.equal(foreign[0], false);
    assert.equal(copies.length, n + 1);

    // 4h. The Copy button is still the touch path.
    await page.click(idx(1));
    await page.click('[data-fe-copy]');
    await page.waitForFunction(() => document.querySelector('[data-fe-count]').textContent === '0 selected');
    assert.deepEqual(copies.at(-1), ['clip.mp4']);

    await page.evaluate(() => { document.querySelector('[data-fe-saved-box]').open = true; });
    await page.screenshot({ path: resolve(shots, `explorer-${width}.png`) });
    assert.deepEqual(errors, []);
    console.log(`PASS studio-dnd ${width}: order, path line (+search label, ${width === 390 ? 'left ellipsis' : 'no clip'}), drop single/selection/unselected, partial failure, busy, foreign drag, button path`);
    await ctx.close();
  }
} finally { await browser.close(); }
