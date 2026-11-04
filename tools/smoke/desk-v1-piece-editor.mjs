// Piece editor (Ron 2026-10-29): click-to-edit title (Enter/blur save, Esc cancel,
// keyboard), no Edit name / Post name / Save name / dashed "Add a version" box,
// focus rings that sit inside the border, and the empty VERSIONS link that opens
// Where with the piece's card marked. Desktop (1440), mid (1000) and phone (390).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';
import { seedDeskV1Fixtures } from './desk-v1-fixture-api.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const index = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const files = loadStaticJsCss(root);
for (const name of ['campaign', 'calendar']) files[`/assets/desk-thumb-${name}.png`] = ['image/png', readFileSync(resolve(root, `assets/desk-thumb-${name}.png`))];
const FAM = 'fam-30-testers';
const browser = await chromium.launch();
let n = 0;
const ok = (m) => { console.log(`  ✓ ${m}`); n++; };
try {
  for (const width of [1440, 1000, 390]) {
    console.log(`${width}px`);
    const ctx = await browser.newContext({ viewport: { width, height: 950 }, isMobile: width === 390, hasTouch: width === 390 });
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.route('**/*', (route) => {
      const path = new URL(route.request().url()).pathname;
      const hit = files[path];
      const json = (body) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: index });
      if (hit) return route.fulfill({ contentType: hit[0], body: hit[1] });
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
    await page.evaluate((id) => {
      const f = window.DeskV1Store.state().families.find((x) => x.id === id);
      f.title = 'New post'; f.versions = [];
      window.deskV1Nav('piece', { campaignId: 'camp-1', familyId: id });
    }, FAM);
    const title = () => page.evaluate((id) => window.DeskV1Store.state().families.find((x) => x.id === id).title, FAM);
    await page.waitForSelector('.desk-v1-piece-title', { state: 'attached' });

    // 1. The title is the control.
    for (const gone of ['Edit name', 'Post name', 'Save name', 'Add a version from the Where board']) {
      assert.equal(await page.getByText(gone).count(), 0, `"${gone}" is gone`);
    }
    assert.equal(await page.locator('.desk-v1-piece-title-btn').count(), 1);
    assert.equal(await page.locator('[data-piece-name-input]').count(), 0, 'not editing until clicked');
    ok('no Edit name / Post name / Save name / dashed Add-a-version box; title is a button');

    const btn = page.locator('[data-piece-title-edit]');
    await btn.click();
    const input = page.locator('[data-piece-name-input]');
    assert.equal(await input.inputValue(), '', 'a generic title opens empty');
    assert.equal(await input.evaluate((el) => el === document.activeElement), true);
    await input.fill('Autumn launch');
    await input.press('Enter');
    await page.waitForFunction(() => document.querySelector('.desk-v1-piece-title').textContent.trim() === 'Autumn launch');
    assert.equal(await title(), 'Autumn launch');
    assert.equal(await btn.evaluate((el) => el === document.activeElement), true, 'Enter returns focus to the title');
    ok('click, type, Enter saves in place and returns focus');

    await btn.click();
    assert.equal(await input.inputValue(), 'Autumn launch');
    await input.fill('Never saved');
    await input.press('Escape');
    assert.equal(await title(), 'Autumn launch');
    assert.equal(await page.locator('[data-piece-name-input]').count(), 0);
    ok('Esc cancels and leaves the name alone');

    await btn.focus();
    await page.keyboard.press('Enter');
    await input.waitFor();
    await input.fill('Blur saved');
    await page.locator('[data-piece-section="copy"] h2').click();
    await page.waitForFunction(() => document.querySelector('.desk-v1-piece-title').textContent.trim() === 'Blur saved');
    assert.equal(await title(), 'Blur saved');
    ok('keyboard opens the edit; leaving the field saves');

    await btn.click();
    await input.press('Enter');
    assert.equal(await title(), 'Blur saved');
    assert.equal(await page.locator('[data-piece-name-input]').count(), 0);
    await btn.click();
    await input.fill('   ');
    await input.press('Enter');
    assert.match(await page.locator('[data-piece-name-status]').innerText(), /1–200 characters/);
    assert.equal(await title(), 'Blur saved');
    await input.press('Escape');
    ok('unchanged Enter just closes; a blank name is refused in place');

    // 2. Focus rings sit inside the border, so the scroll parents cannot clip them.
    await btn.click();
    const ring = await input.evaluate((el) => { const c = getComputedStyle(el); return [c.outlineStyle, parseFloat(c.outlineOffset)]; });
    assert.equal(ring[0], 'solid');
    assert.ok(ring[1] < 0, `name input ring offset ${ring[1]}`);
    await input.press('Escape');
    const ta = page.locator('[data-piece-copy-text]');
    await ta.focus();
    const taRing = await ta.evaluate((el) => { const c = getComputedStyle(el); return [c.outlineStyle, parseFloat(c.outlineOffset)]; });
    assert.equal(taRing[0], 'solid');
    assert.ok(taRing[1] < 0, `textarea ring offset ${taRing[1]}`);
    const box = await page.evaluate(() => {
      const body = document.querySelector('#desk-v1-body').getBoundingClientRect();
      return ['[data-piece-copy-text]', '#desk-v1-piece-rightcol'].map((s) => { const r = document.querySelector(s).getBoundingClientRect(); return [s, r.left >= body.left && r.right <= body.right + 0.5]; });
    });
    for (const [s, inside] of box) assert.equal(inside, true, `${s} inside the Desk body at ${width}`);
    ok('focus rings are inside the border; textarea and Dave column stay inside the Desk body');

    // 3. VERSIONS empty state -> Where with the piece marked.
    const empty = page.locator('[data-piece-section="versions"] [data-piece-versions-empty]');
    assert.match(await empty.innerText(), /^No destinations yet\s+View campaign channels ›$/);
    await page.locator('[data-piece-put-on-channel]').click();
    await page.waitForSelector('[data-where]');
    const mark = page.locator(`.desk-v1-where-highlight[data-where-message][data-family-id="${FAM}"]`);
    assert.equal(await mark.count(), 1, 'the piece card is marked on Where');
    assert.equal(await mark.evaluate((el) => el === document.activeElement), true, 'and focused');
    await page.screenshot({ path: resolve(root, `docs/desk_v1/screens/piece_editor_where_highlight_${width}.png`) });
    await page.evaluate(() => window.deskV1Back());
    await page.waitForSelector('.desk-v1-piece-title', { state: 'attached' });
    assert.equal(await page.locator('[data-piece-put-on-channel]').count(), 1);
    ok('"View campaign channels" opens Where with this piece marked and focused; Back returns');

    assert.deepEqual(errors, []);
    await ctx.close();
  }
  console.log(`\n✅ PASS — ${n} checks`);
} catch (e) { console.error(e.stack || e); process.exitCode = 1; }
finally { await browser.close(); }
