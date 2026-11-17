#!/usr/bin/env node
// Studio image panel + a browser-pane engine (Gemini on your plan). Ron 2026-11-15: the panel
// kept saying "No browser pane is open on the 'main' profile" after he opened and signed in to
// the pane, because the engine list was read once when the panel opened.
// Checks: the panel turns ready on its own once the server says so (no reopen), stops asking
// after that, never asks the model catalogue for gemini_web, and leaves a typed prompt alone.
// Mocked server; never submits a job (any POST to /jobs fails the run).
// RUN  cd tools/smoke && node desk-v1-engine-pane-watch.mjs
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss, deskLoaded } from './_static.mjs';

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const assets = loadStaticJsCss(root);
const html = readFileSync(resolve(root, 'static/index.html'), 'utf8');
let failed = 0;
const check = (yes, label) => { console[yes ? 'log' : 'error'](`${yes ? 'PASS' : 'FAIL'} ${label}`); if (!yes) failed++; };

const CLOSED = { ready: false, exists: false, state: 'pane_closed', profile: 'main', vault_entry: '',
  reason: "No browser pane is open on the 'main' profile. Open Gemini in the pane and sign in." };
const READY = { ready: true, exists: true, profile: 'main', vault_entry: '', reason: null };
const engine = (connected) => ({
  id: 'gemini_web', label: 'Gemini (your plan, in the browser pane)', auth: { kind: 'browser_pane', vault_entry: '' },
  currency: 'usd', estimate: 'plan', job_model: 'sync', model_discovery: false, connected,
  models: [{ model_id: 'hydrogen', kind: 'image', label: 'Hydrogen (your Gemini plan)', aspect_ratios: ['1:1', '16:9'],
    inputs: { text: true, reference_images_max: 4 }, price: { unit: 'image', usd: {} } }],
});

const browser = await chromium.launch();
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 950 } });
  const page = await context.newPage();
  const errors = [], paid = [], catalogue = [];
  let open = false, listGets = 0;
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/') return route.fulfill({ contentType: 'text/html', body: html });
    if (assets[path]) return route.fulfill({ contentType: assets[path][0], body: assets[path][1] });
    if (path === '/api/desk/engines') { listGets++; return json({ engines: [engine(open ? READY : CLOSED)] }); }
    if (path.endsWith('/models')) { catalogue.push(path); return json({ code: 'unknown_engine', error: 'Unknown generation engine' }, 400); }
    if (path === '/api/desk/engines/estimate') return json({ estimate: { usd: 0, basis: 'plan', note: 'Runs on your Gemini plan.' }, refusal: null });
    if (path === '/api/desk/engines/jobs') { paid.push(path); return route.abort(); }
    if (path === '/api/projects') return json([]);
    if (path === '/api/local-auth/status') return json({ configured: true });
    return json({});
  });
  await page.goto('http://mc.smoke.test');
  await deskLoaded(page);
  await page.waitForFunction(() => window.DeskV1Engines);
  await page.evaluate(() => {
    for (const child of document.body.children) child.style.display = 'none';
    document.body.insertAdjacentHTML('beforeend', '<div id="image-panel"></div>');
    window.DeskV1Engines.mountImageGenerate(document.getElementById('image-panel'), { projectId: 'p1', key: 'pane-watch' });
  });
  await page.waitForSelector('[data-eng-prompt]');
  const label = () => page.locator('[data-eng-engine] option:checked').textContent();
  check((await label()).includes('(not connected)'), 'pane closed: the engine is marked (not connected)');
  check((await page.locator('[data-eng-not-connected]').textContent()).includes("No browser pane is open"), 'pane closed: the panel says the pane is not open');

  await page.fill('[data-eng-prompt]', 'a red apple on a desk');
  open = true;   // the person opens the pane and signs in; the panel is NOT reopened
  const t0 = Date.now();
  await page.waitForFunction(() => !document.querySelector('[data-eng-not-connected]'), null, { timeout: 9000 }).catch(() => {});
  const took = Date.now() - t0;
  check(!(await page.locator('[data-eng-not-connected]').count()), `the panel turns ready on its own (${took} ms)`);
  check(!(await label()).includes('(not connected)'), 'the engine is no longer marked (not connected)');
  check((await page.inputValue('[data-eng-prompt]')) === 'a red apple on a desk', 'the typed prompt survives the repaint');
  await page.locator('[data-eng-prompt]').dispatchEvent('change');
  await page.waitForFunction(() => document.querySelector('[data-eng-estimate]')?.dataset.state === 'ok', null, { timeout: 5000 }).catch(() => {});
  check(await page.locator('[data-eng-render-btn]').isEnabled(), 'Generate is on once ready, with a prompt and a $0 plan estimate');

  const after = listGets;
  await page.waitForTimeout(9000);
  check(listGets === after, `it stops asking once ready (${listGets - after} extra list reads in 9 s)`);
  check(catalogue.length === 0, `the model catalogue is never asked for gemini_web (${catalogue.join(', ')})`);
  check(paid.length === 0, 'nothing was submitted');
  check(errors.length === 0, `zero page errors (${errors.join('; ')})`);
  await context.close();
} finally {
  await browser.close();
}
process.exitCode = failed ? 1 : 0;
