#!/usr/bin/env node
// Real worktree SPA/Floor against fixture projections; no live config or store.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const assets = loadStaticJsCss(root);
const index = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const projects = readFileSync(resolve(root, 'tools/smoke/fixtures/projects.json'), 'utf8');
const counts = { pass: 3, fail: 1, skip: 1, error: 0, xfail: 0, xpass: 0 };
const known = {
  available: true, active_count: 1, phase: 'progress', runner: 'pytest', unit: 'test',
  selected_total: 10, completed: 5, percent: 50, capture: 'complete', counts,
  runs: [], runs_omitted: 0, total_recalculated: false,
  ownerless_count: 0, unavailable_owner_count: 0,
};
const run = (overrides = {}) => ({ run_id: 'run-one', runner: 'pytest', counts, ...overrides });
const unknown = {
  ...known, selected_total: null, completed: null, percent: null, counts: null,
  phase: 'unknown', runs: [run()],
};
let snapshot;
function board(summary, state = 'idle', roomSummary = summary) {
  const figure = { session_id: 'session-one', claude_session_id: 'conversation',
    name: 'Sol_Tobin', name_from: 'self', state, task: 'Run selected checks',
    provider: 'codex', model: '', avatar: '', age: '', reason: 'question' };
  const room = { id: 'smoke_alpha', name: 'Test project', figures: [figure] };
  if (summary !== undefined) figure.test_runs = summary;
  if (roomSummary !== undefined) room.test_runs = roomSummary;
  return { rooms: [room], quiet: [], bench: [], counts: { figures: 1, rooms: 1 }, poll_seconds: 60 };
}

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    const json = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (url.origin !== 'http://test-run-floor.smoke') return route.abort();
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: index });
    if (assets[url.pathname]) {
      const [contentType, body] = assets[url.pathname];
      return route.fulfill({ contentType, body });
    }
    if (url.pathname === '/api/projects') return route.fulfill({ contentType: 'application/json', body: projects });
    if (url.pathname === '/api/config') return json({});
    if (url.pathname === '/api/floor') return json(snapshot);
    return route.abort();
  });
  snapshot = board(undefined);
  await page.goto('http://test-run-floor.smoke/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card');
  await page.evaluate(() => window.openFloor());
  const figure = page.locator('.fl-fig');
  const testing = figure.locator('.fl-test');
  const roomLine = page.locator('.fl-test-room');
  const render = async (summary, state = 'idle', roomSummary = summary) => {
    snapshot = board(summary, state, roomSummary);
    await page.evaluate(() => window.refreshFloor());
    assert.equal(await page.evaluate(() => window._floorTimerArmed()), true);
  };
  const check = async (name, fn) => { await fn(); console.log(`PASS ${name}`); };

  await check('flag off: no test DOM, original idle wording', async () => {
    assert.equal(await page.locator('.fl-test').count(), 0);
    assert.match(await figure.locator('.fl-act').innerText(), /idle/);
  });
  await check('known total: measured bar and outcome counts on figure and room', async () => {
    await render(known);
    assert.equal(await figure.locator('.fl-act').innerText(), 'testing 50%');
    assert.match(await testing.innerText(), /5\/10 tests done/);
    for (const text of ['3 pass', '1 fail', '1 skip']) assert.ok((await testing.innerText()).includes(text));
    assert.equal(await testing.locator('progress').getAttribute('value'), '5');
    assert.match(await roomLine.innerText(), /testing 50%/);
    assert.equal(await page.locator('link[href="/static/css/test-run-floor.css"]').count(), 1);
    assert.equal(await testing.evaluate(el => getComputedStyle(el).display), 'flex');
  });
  await check('unknown total: observed per-run counts, no bar', async () => {
    await render(unknown);
    assert.equal(await figure.locator('.fl-act').innerText(), 'testing, total unknown');
    assert.match(await testing.innerText(), /pytest · observed 3 pass/);
    assert.equal(await page.locator('.fl-test progress').count(), 0);
  });
  await check('collecting stays collecting', async () => {
    await render({ ...unknown, phase: 'collecting' });
    assert.equal(await figure.locator('.fl-act').innerText(), 'testing, collecting');
  });
  await check('mixed multiple runs: independent observed outcomes, no aggregate', async () => {
    await render({ ...unknown, active_count: 2, phase: 'multiple', runs: [run(), run({ runner: 'node', run_id: 'run-two' })] });
    assert.match(await figure.locator('.fl-act').innerText(), /multiple · 2 runs/);
    assert.equal(await testing.locator('.fl-test-run').count(), 2);
    assert.equal(await testing.locator('progress').count(), 0);
  });
  await check('compatible multiple runs retain canonical weighted total', async () => {
    await render({ ...known, active_count: 2, completed: 5, selected_total: 20, percent: 25 });
    assert.equal(await figure.locator('.fl-act').innerText(), 'testing 25% · 2 runs');
    assert.match(await testing.innerText(), /5\/20 tests done/);
  });
  await check('asking remains primary and test progress secondary', async () => {
    await render(known, 'asking');
    assert.equal(await figure.locator('.fl-act').innerText(), 'waiting on your answer');
    assert.equal(await figure.locator('.fl-state').textContent(), 'needs you');
    assert.match(await figure.locator('.fl-test-secondary').innerText(), /testing 50%/);
  });
  await check('unavailable room with no figures remains explicit', async () => {
    snapshot = board(undefined, 'idle', { available: false, active_count: 0 });
    snapshot.rooms[0].figures = [];
    await page.evaluate(() => window.refreshFloor());
    assert.equal(await page.locator('.fl-fig').count(), 0);
    assert.equal(await roomLine.innerText(), 'testing unavailable');
  });
  await check('ownerless and unavailable owners appear only in room summary', async () => {
    await render(known, 'idle', { ...known, ownerless_count: 1, unavailable_owner_count: 2 });
    assert.match(await roomLine.innerText(), /1 ownerless.*2 owner unavailable/s);
    assert.doesNotMatch(await testing.innerText(), /ownerless|owner unavailable/);
  });
  await check('malicious run ID, runner and name are inert text', async () => {
    const malicious = '\"><img src=x onerror="window.__testRunInjected=1"><script>window.__testRunInjected=1</script>';
    snapshot = board({ ...unknown, runs: [run({ run_id: malicious, runner: malicious })] });
    snapshot.rooms[0].figures[0].name = malicious;
    await page.evaluate(() => window.refreshFloor());
    assert.equal(await testing.locator('img,script').count(), 0);
    assert.equal(await testing.locator('.fl-test-run').getAttribute('title'), malicious);
    assert.ok((await testing.innerText()).includes(malicious));
    assert.equal(await page.evaluate(() => window.__testRunInjected), undefined);
  });
  await check('partial capture suppresses stale percentage and denominator', async () => {
    await render({ ...known, capture: 'partial', selected_total: 10, percent: 99 });
    assert.match(await testing.innerText(), /partial capture/);
    assert.equal(await testing.locator('progress').count(), 0);
    assert.equal(await figure.locator('.fl-act').innerText(), 'testing, total unknown');
  });
  await check('finalizing is never a passed verdict', async () => {
    await render({ ...known, completed: 10, phase: 'finalizing' });
    assert.equal(await figure.locator('.fl-act').innerText(), 'testing, finalizing');
    assert.equal(await testing.locator('progress').getAttribute('value'), '10');
  });
  await check('zero total has no percentage', async () => {
    await render({ ...known, completed: 0, selected_total: 0, percent: null, phase: 'unknown' });
    assert.equal(await figure.locator('.fl-act').innerText(), 'testing 0/0');
    assert.equal(await testing.locator('progress').count(), 0);
  });
  await check('recalculated totals may move backwards', async () => {
    await render({ ...known, selected_total: 20, percent: 25, total_recalculated: true });
    assert.equal(await figure.locator('.fl-act').innerText(), 'testing 25%');
    assert.match(await testing.innerText(), /total recalculated/);
  });
  await check('indeterminate run detail is bounded with explicit omitted count', async () => {
    await render({ ...unknown, active_count: 105, runs: Array.from({ length: 100 }, (_, i) => run({ run_id: String(i) })), runs_omitted: 5 });
    assert.equal(await testing.locator('.fl-test-run').count(), 3);
    assert.match(await testing.innerText(), /\+102 more runs/);
  });
  await check('invalid counts/totals never become markup or a progress bar', async () => {
    await render({ ...known, selected_total: '<img onerror=1>', counts: { pass: '<img onerror=1>', fail: -1 }, runs: [] });
    assert.equal(await page.locator('.fl-test progress,.fl-test img').count(), 0);
    assert.equal(await figure.locator('.fl-act').innerText(), 'testing, total unknown');
  });
  await check('terminal cleanup / flag-off next poll removes all added DOM', async () => {
    await render({ ...known, active_count: 0 });
    assert.equal(await page.locator('.fl-test').count(), 0);
    await render(undefined);
    assert.equal(await page.locator('.fl-test').count(), 0);
  });
  await render(known, 'asking');
  mkdirSync(resolve(root, '_scratch/test-run-floor'), { recursive: true });
  await page.locator('[data-fl-room="smoke_alpha"]').screenshot({ path: resolve(root, '_scratch/test-run-floor/asking-desktop.png') });
  await page.evaluate(() => window.closeFloor());
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.openFloor());
  await render(known, 'asking');
  await check('narrow figure details fit their card', async () => {
    const box = await testing.evaluate(el => ({ width: el.clientWidth, scroll: el.scrollWidth }));
    assert.ok(box.scroll <= box.width + 1, JSON.stringify(box));
  });
  await page.locator('[data-fl-room="smoke_alpha"]').screenshot({ path: resolve(root, '_scratch/test-run-floor/asking-mobile.png') });
  await page.evaluate(() => window.closeFloor());
  assert.equal(await page.evaluate(() => window._floorTimerArmed()), false);
  console.log('ALL PASS (18 Floor projection cases, real worktree assets)');
} finally {
  await browser.close();
}
