#!/usr/bin/env node
// Real worktree SPA and shared reader; all APIs are hermetic fixtures.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const assets = loadStaticJsCss(root);
const index = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const projects = JSON.parse(readFileSync(resolve(root, 'tools/smoke/fixtures/projects.json'), 'utf8'));
projects.push({ ...projects[0], id: 'no_figures', name: 'No live figures', display_order: 2 });
const row = (pid, id, owner, extra = {}) => ({ project_id: pid, run_id: id, owner_session_id: owner,
  display_label: 'identical test name', source: 'wrapper', runner: 'pytest', unit: 'test',
  state: 'running', seq: 1, selected_total: 10, completed: 4, capture: 'complete',
  created_at: 100, counts: { pass: 3, fail: 1, skip: 0, error: 0, xfail: 0, xpass: 0 },
  latest_failures: [{ name: 'identical failure name', phase: 'call' }], ...extra });
const rows = {
  smoke_alpha: [row('smoke_alpha', 'same-id', 'session-a1'), row('smoke_alpha', 'run-a2', 'session-a2', { state: 'failed', exit_code: 1 })],
  smoke_beta: [row('smoke_beta', 'same-id', 'session-b1', { completed: 8, counts: { pass: 8 } }), row('smoke_beta', 'run-b2', 'session-b2')],
  no_figures: [row('no_figures', 'ownerless', null, { source: 'harness', state: 'passed', exit_code: 0,
    selected_total: 4, counts: { pass: 4, fail: 0, skip: 0, error: 0, xfail: 0, xpass: 0 }, latest_failures: [] })],
};
let enabled = false, failReads = false;
const reads = [], writes = [];
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const request = route.request(), url = new URL(request.url());
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.origin !== 'http://project-test-runs.smoke') return route.abort();
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: index });
    if (assets[url.pathname]) {
      const [contentType, body] = assets[url.pathname]; return route.fulfill({ contentType, body });
    }
    if (request.method() !== 'GET') { writes.push(url.pathname); return route.abort(); }
    if (url.pathname === '/api/projects') return json(projects);
    if (url.pathname === '/api/config') return json({});
    if (url.pathname === '/api/characters') return json([]);
    if (url.pathname === '/api/floor') return json({ rooms: [], quiet: [], bench: [], counts: { figures: 0, rooms: 0 } });
    const match = url.pathname.match(/^\/api\/project\/([^/]+)\/test-runs$/);
    if (match) {
      reads.push({ pid: match[1], session: url.searchParams.get('session') });
      if (!enabled) return route.fulfill({ status: 404, contentType: 'text/html', body: 'Not Found' });
      if (failReads) return json({ error: 'fixture unavailable' }, 503);
      return json({ runs: rows[match[1]] || [] });
    }
    return route.abort();
  });
  await page.goto('http://project-test-runs.smoke/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card');
  const modal = pid => page.locator(`.modal-window[data-modal-id="${pid}"]`);
  const panel = pid => modal(pid).locator('[data-project-test-runs]');
  const open = async pid => {
    await page.evaluate(id => window.openProjectModal(id), pid);
    await modal(pid).waitFor();
  };
  const refresh = pid => page.evaluate(id => window.TestRunData.refresh(id), pid);
  const select = async pid => {
    // The desktop uses the same menu as mobile; this exercises delegated clicks.
    await page.evaluate(id => window.focusModal(id), pid);
    await modal(pid).locator('.modal-menu-btn').click();
    await modal(pid).locator('.mc-tabs-in-menu [data-test-runs-nav]').click();
    await panel(pid).waitFor();
  };
  let checks = 0;
  const check = async (name, fn) => { await fn(); checks++; console.log(`PASS ${name}`); };
  await open('smoke_alpha');
  await refresh('smoke_alpha');
  await check('flag off: byte-identical modal HTML, no navigation/panel/style', async () => {
    assert.equal(await page.locator('[data-test-runs-nav], [data-project-test-runs], link[href$="project-test-runs.css"]').count(), 0);
    assert.equal(await page.evaluate(() => {
      const p = allProjects.find(p => p.id === 'smoke_alpha');
      const withModule = modalContentHTML(p), saved = window.ProjectTestRuns;
      delete window.ProjectTestRuns;
      const without = modalContentHTML(p);
      window.ProjectTestRuns = saved;
      return withModule === without;
    }), true);
  });
  enabled = true;
  await modal('smoke_alpha').locator('.modal-menu-btn').click();
  await refresh('smoke_alpha');
  await check('first enabled read adds navigation without dismissing an open menu', async () => {
    assert.equal(await modal('smoke_alpha').locator('.modal-menu-dropdown.open').count(), 1);
    assert.equal(await modal('smoke_alpha').locator('.mc-tabs-in-menu [data-test-runs-nav]').count(), 1);
    await page.evaluate(() => window._mcMenuClose());
  });
  await open('smoke_beta'); await refresh('smoke_beta');
  await select('smoke_alpha');
  await select('smoke_beta');
  await check('two projects/two sessions with identical test names and same run ID never bleed', async () => {
    const a = await panel('smoke_alpha').innerText(), b = await panel('smoke_beta').innerText();
    assert.match(a, /session-a1/); assert.match(a, /session-a2/); assert.match(a, /4\/10/);
    assert.doesNotMatch(a, /session-b|8\/10/);
    assert.match(b, /session-b1/); assert.match(b, /session-b2/); assert.match(b, /8\/10/);
    assert.doesNotMatch(b, /session-a/);
    assert.equal(await panel('smoke_alpha').locator('[data-run-id="same-id"]').count(), 1);
    assert.equal(await panel('smoke_beta').locator('[data-run-id="same-id"]').count(), 1);
    assert.ok(reads.every(r => r.session === null));
  });
  await check('full modal rebuild and tab reopen retain exactly one panel and each row', async () => {
    await page.evaluate(() => { for (let n = 0; n < 4; n++) refreshModalById('smoke_beta'); });
    await page.evaluate(() => switchModalTab('smoke_beta', 'agent'));
    assert.equal(await panel('smoke_beta').count(), 0);
    await select('smoke_beta');
    assert.equal(await panel('smoke_beta').count(), 1);
    assert.equal(await panel('smoke_beta').locator('.ptr-run').count(), 2);
  });
  await open('no_figures'); await refresh('no_figures'); await select('no_figures');
  await check('ownerless completed run appears with no live figures or sessions', async () => {
    assert.match(await panel('no_figures').innerText(), /Ownerless/);
    assert.match(await panel('no_figures').innerText(), /harness/);
    assert.match(await panel('no_figures').innerText(), /passed/);
    assert.equal(await page.evaluate(() => Object.keys(agentStatusCache).length), 0);
  });
  await check('malicious labels and failures stay text; unknown capture stays indeterminate', async () => {
    const evil = `'"><img src=x onerror="window.__ptrInjected=1"><script>window.__ptrInjected=1</script>`;
    rows.no_figures = [row('no_figures', 'evil', null, { seq: 2, display_label: evil, runner: evil,
      selected_total: null, capture: 'partial', latest_failures: [{ name: evil }] })];
    await refresh('no_figures');
    assert.equal(await panel('no_figures').locator('img,script,progress').count(), 0);
    assert.ok((await panel('no_figures').innerText()).includes(evil));
    assert.match(await panel('no_figures').innerText(), /total unknown \(partial capture\)/);
    assert.equal(await page.evaluate(() => window.__ptrInjected), undefined);
  });
  await check('updates use the shared reader; finalizing never claims success', async () => {
    rows.smoke_beta[0] = { ...rows.smoke_beta[0], seq: 2, completed: 10 };
    await page.evaluate(() => window.TestRunData.hint('smoke_beta', { run_id: 'same-id', seq: 2 }));
    await panel('smoke_beta').locator('[data-run-id="same-id"] .ptr-verdict').filter({ hasText: 'finalizing' }).waitFor();
    assert.doesNotMatch(await panel('smoke_beta').locator('[data-run-id="same-id"]').innerText(), /passed/);
  });
  await check('stale data remains visible and refresh recovers', async () => {
    failReads = true; await refresh('no_figures'); await refresh('no_figures');
    assert.match(await panel('no_figures').innerText(), /last received snapshots/);
    assert.equal(await panel('no_figures').locator('.ptr-run').count(), 2);
    failReads = false;
    await panel('no_figures').locator('.ptr-refresh').click();
    await page.waitForFunction(() => TestRunData.status('no_figures').state === 'ok');
    assert.doesNotMatch(await panel('no_figures').innerText(), /last received snapshots/);
  });
  await check('desktop and 390px panel fits, keyboard menu navigation, empty history', async () => {
    mkdirSync(resolve(root, '_scratch/project-test-runs'), { recursive: true });
    await page.screenshot({ path: resolve(root, '_scratch/project-test-runs/desktop.png') });
    await page.evaluate(() => { for (const id of ['smoke_alpha', 'smoke_beta', 'no_figures']) closeModalById(id); });
    await page.setViewportSize({ width: 390, height: 844 });
    await open('smoke_alpha'); await refresh('smoke_alpha');
    await modal('smoke_alpha').locator('.modal-menu-btn').click();
    const nav = modal('smoke_alpha').locator('.mc-tabs-in-menu [data-test-runs-nav]');
    await nav.focus(); await page.keyboard.press('Enter');
    await panel('smoke_alpha').waitFor();
    assert.equal(await panel('smoke_alpha').evaluate(el => el.scrollWidth <= el.clientWidth + 1), true);
    await page.screenshot({ path: resolve(root, '_scratch/project-test-runs/mobile.png') });
    await page.evaluate(() => closeModalById('smoke_alpha'));
    rows.smoke_beta = []; // new browser reader below avoids intentionally retained cache
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => allProjects.length === 3);
    await open('smoke_beta'); await refresh('smoke_beta'); await select('smoke_beta');
    assert.match(await panel('smoke_beta').innerText(), /No test runs recorded/);
  });
  await check('runtime flag-off removes panel/nav and closing releases the shared poller', async () => {
    await open('smoke_alpha'); await refresh('smoke_alpha');
    await page.evaluate(() => window.focusModal('smoke_beta'));
    await modal('smoke_beta').locator('.modal-menu-btn').click();
    enabled = false; await refresh('smoke_beta');
    assert.equal(await page.locator('[data-test-runs-nav], [data-project-test-runs], link[href$="project-test-runs.css"]').count(), 0);
    await page.evaluate(() => { closeModalById('smoke_beta'); closeModalById('smoke_alpha'); });
    assert.equal(await page.evaluate(() => TestRunData.stats().timerArmed), false);
  });
  // The harness deliberately aborts CDN imports. All other runtime errors fail.
  assert.deepEqual(errors.filter(message => message !== 'Failed to fetch dynamically imported module: https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs'), []);
  // Unrelated boot reads may POST; this feature must never POST a test-run route.
  assert.equal(writes.some(path => path.includes('/test-runs')), false);
  console.log(`ALL PASS (${checks} cases); no live server/config/store accessed`);
} finally { await browser.close(); }
