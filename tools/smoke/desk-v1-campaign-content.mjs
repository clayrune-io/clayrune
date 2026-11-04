// MC-1112: real isolated Flask store + account selection, reload, Undo and media.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const SHOTS = resolve(ROOT, '_scratch/mc1112');
mkdirSync(SHOTS, { recursive: true });
const fixture = spawn('python', [resolve(ROOT, 'tools/smoke/_desk_campaign_content_server.py')], { cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
let browser, origin, currentPage;
let stderr = '';
fixture.stderr.on('data', (data) => { stderr += data; });
const failures = [];
const results = [];
async function state(page) { return page.request.get(origin + '/__state').then((r) => r.json()); }
async function where(page) {
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-test', panel: 'where' }));
  await page.waitForSelector('[data-where]');
}
async function what(page) {
  await page.evaluate(() => window.deskV1GotoCampaignPanel('what', { campaignId: 'camp-test' }));
  await page.waitForSelector('[data-what]');
}
async function shot(page, name, width) {
  const path = resolve(SHOTS, `${name}-${width}.png`);
  await page.screenshot({ path }); results.push({ screenshot: path });
}

async function boot(page) {
  await page.goto(origin, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.desk-v1-shell');
  await page.waitForFunction(() => window.DeskV1Store.gate() === null);
}
try {
  const registration = await fetch('http://localhost:5199/api/processes/register', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pid: fixture.pid, name: 'MC-1112 isolated UI fixture', project_id: 'mission_control', command: 'python tools/smoke/_desk_campaign_content_server.py' }) });
  assert(registration.ok, 'fixture PID registered');
  const port = await new Promise((resolvePort, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error('Fixture startup timeout: ' + stderr)), 15000);
    fixture.stdout.on('data', (data) => {
      output += data;
      if (output.includes('\n')) { clearTimeout(timer); try { resolvePort(JSON.parse(output.split('\n')[0]).port); } catch (e) { reject(e); } }
    });
    fixture.once('exit', (code) => { clearTimeout(timer); reject(new Error('Fixture exited ' + code + ': ' + stderr)); });
  });
  origin = `http://127.0.0.1:${port}`;
  browser = await chromium.launch();
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 } });
    const page = await context.newPage(); currentPage = page;
    const errors = []; page.on('pageerror', (e) => { errors.push(e.message); console.error('PAGE ERROR: ' + e.message); });
    await boot(page); await where(page);
    assert.equal(await page.locator('[data-where-source]').count(), 2, 'all configured accounts visible including disconnected');
    assert.equal(await page.locator('[data-where-version]').count(), 0, 'suggestion channel did not prefill a tile');
    await shot(page, 'where-drop-hint', width);
    await page.locator('[data-where-source][data-channel-id="ch-blog"]').click();
    await page.waitForSelector('.desk-v1-where-col[data-channel-id="ch-blog"]');
    assert.equal(await page.locator('[data-where-version]').count(), 0, 'new tile starts empty');
    await shot(page, 'where-empty-tile', width);
    await page.locator('[data-where-source][data-channel-id="ch-off"]').click();
    await page.waitForSelector('.desk-v1-where-col[data-channel-id="ch-off"]');
    await shot(page, 'where-unconnected-dropped', width);
    await page.locator('[data-where-remove][data-channel-id="ch-off"]').click();
    assert.equal(await page.locator('.desk-v1-where-col[data-channel-id="ch-off"]').count(), 0);
    assert.equal(await page.locator('[data-where-source][data-channel-id="ch-off"]').count(), 1);
    await shot(page, 'where-x-removed', width);
    // C5: individual assignment add/remove/Undo/refusal now belongs to composer smokes.
    assert.equal(await page.locator('[data-where-remove-version]').count(), 0);
    await boot(page); await where(page);
    assert.equal(await page.locator('.desk-v1-where-col[data-channel-id="ch-blog"]').count(), 1, 'account bound survived reload');
    await page.route('**/api/desk/campaigns/*', route => route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ error: 'test refusal' }) }));
    await page.locator('[data-where-source][data-channel-id="ch-off"]').click();
    await page.locator('#toast-container').getByText('This change was not saved: test refusal', { exact: false }).last().waitFor();
    assert.equal(await page.locator('.desk-v1-where-col[data-channel-id="ch-off"]').count(), 0, 'refused account add rolls back');
    await page.unroute('**/api/desk/campaigns/*');
    await what(page);
    const row = page.locator('[data-what-row][data-family-id="article-test"]');
    await row.locator('[data-more-btn]').click(); await page.click('[data-menu-archive]');
    await page.waitForFunction(() => !document.querySelector('[data-what-row][data-family-id="article-test"]'));
    await page.waitForFunction(async () => !!(await (await fetch('/__state')).json()).pieces['article-test'].archive);
    await boot(page); await where(page); await what(page);
    await page.click('[data-what-filter="archived"]');
    await page.waitForSelector('[data-what-row][data-family-id="article-test"]');
    assert((await state(page)).pieces['article-test'].archive);
    await shot(page, 'what-archived', width);
    await page.locator('[data-what-row][data-family-id="article-test"] [data-more-btn]').click(); await page.click('[data-menu-archive]');
    await page.waitForTimeout(150); await page.click('[data-what-filter="all"]');
    await page.waitForSelector('[data-what-row][data-family-id="article-test"]');
    await shot(page, 'what-restored', width);
    await row.locator('[data-more-btn]').click(); await page.click('[data-menu-delete-piece]');
    await page.waitForFunction(() => !document.querySelector('[data-what-row][data-family-id="article-test"]'));
    await page.waitForFunction(() => (document.querySelector('#desk-v1-undo').title || '').includes('Deleted'));
    await page.click('#desk-v1-undo');
    await page.waitForSelector('[data-what-row][data-family-id="article-test"]');
    await page.waitForFunction(async () => !!(await (await fetch('/__state')).json()).pieces['article-test']);
    assert((await state(page)).pieces['article-test']);
    await row.locator('[data-primary-action]').click();
    await page.click('[data-piece-edit-article]');
    await page.waitForSelector('[data-writer-body] [data-article-path] img');
    const embedsBefore = await page.locator('[data-writer-body] [data-article-path]').count();
    assert.equal(embedsBefore, width === 1440 ? 3 : 4);
    assert.equal(await page.locator('[data-writer-body] video').count(), 1);
    await page.waitForFunction(() => document.querySelector('[data-writer-body] video').videoWidth === 320);
    assert.equal(await page.locator('[data-writer-body] a').count(), 1);
    await shot(page, 'what-article-media', width);
    await page.click('[data-article-library]');
    await page.waitForSelector('[data-lp-pick]');
    await page.locator('[data-lp-pick]').first().click();
    assert.equal(await page.locator('[data-writer-body] [data-article-path]').count(), embedsBefore + 1);
    await page.click('[data-writer-save]');
    await page.waitForSelector('[data-what-list]');
    await page.waitForTimeout(150);
    const text = (await state(page)).pieces['article-test'].body;
    assert(text.includes('Opening paragraph') && text.includes('guide.pdf') && text.includes('overview.png'));
    assert(!text.includes('Loading') && !text.includes('Remove embedded'), 'widgets serialize to media markers');
    await boot(page); await where(page); await what(page);
    await page.locator('[data-what-row][data-family-id="article-test"] [data-primary-action]').click();
    await page.click('[data-piece-edit-article]');
    await page.waitForSelector('[data-writer-body] [data-article-path] img');
    assert.equal(await page.locator('[data-writer-body] [data-article-path]').count(), embedsBefore + 1, 'body media survived a full reload');
    await page.evaluate(() => window.deskV1Nav('studio-create', { itemId: 'artd-test', kind: 'article' }));
    await page.waitForSelector('[data-sa-library]'); await page.click('[data-sa-library]');
    await page.waitForSelector('[data-lp-pick]'); await page.locator('[data-lp-pick]').first().click();
    await page.click('[data-writer-save]'); await page.waitForTimeout(200);
    assert((await state(page)).studio_articles['artd-test'].tabs[0].body.includes('desk/library/'));
    // Reset campaign placement for the second width using the UI's own X.
    await where(page);
    await page.locator('[data-where-remove][data-channel-id="ch-blog"]').click();
    assert.equal(errors.length, 0, errors.join('\n'));
    console.log(`PASS ${width}: account selection/removal, persistence, archive/restore/delete Undo, inline media`);
    results.push({ width, errors, pass: true });
    await context.close();
  }
} catch (error) {
  failures.push(error.stack || String(error)); console.error(failures.at(-1));
  if (currentPage && !currentPage.isClosed()) {
    await currentPage.screenshot({ path: resolve(SHOTS, 'failure.png') });
    writeFileSync(resolve(SHOTS, 'failure.txt'), await currentPage.locator('body').innerText());
  }
} finally {
  if (browser) await browser.close();
  if (origin) { try { await fetch(origin + '/__shutdown', { method: 'POST' }); } catch (_) {} }
  if (fixture.exitCode === null) {
    await Promise.race([new Promise((done) => fixture.once('exit', done)), new Promise((done) => setTimeout(done, 2000))]);
    if (fixture.exitCode === null) fixture.kill();
  }
  writeFileSync(resolve(SHOTS, 'ui-results.json'), JSON.stringify({ results, failures }, null, 2));
}
process.exitCode = failures.length ? 1 : 0;
