#!/usr/bin/env node
// Real Studio engine panel with read-only catalogue fixtures; no vendor or server.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const assets = loadStaticJsCss(root);
const html = readFileSync(resolve(root, 'static/index.html'), 'utf8');
let failed = 0;
const check = (yes, label) => { console[yes ? 'log' : 'error'](`${yes ? 'PASS' : 'FAIL'} ${label}`); if (!yes) failed++; };
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 950 } });
    const page = await context.newPage();
    const errors = [], requests = [];
    let catalogueFailed = false;
    page.on('pageerror', e => errors.push(e.message));
    const engines = [
      { id: 'higgsfield_mcp', label: 'Higgsfield', currency: 'credits', connected: { ready: true }, model_discovery: true,
        models: [{ model_id: 'curated', label: 'Curated fallback', kind: 'video', inputs: {} }] },
      { id: 'google', label: 'Google', connected: { ready: true }, model_discovery: true,
        models: [{ model_id: 'veo-curated', label: 'Curated Veo', kind: 'video', inputs: {} }] },
    ];
    await page.route('**/*', async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (assets[path]) return route.fulfill({ contentType: assets[path][0], body: assets[path][1] });
      if (path === '/api/desk/engines') return json({ engines });
      if (path === '/api/desk/engines/renders') return json({ render: null });
      if (path.endsWith('/models/quote')) {
        requests.push({ path, method: req.method(), body: req.postDataJSON() });
        return json({ price: { state: 'quoted', credits: 42 }, note: 'Text-only quote; pictures omitted.' });
      }
      if (path.endsWith('/models')) {
        requests.push({ path, method: req.method() });
        if (catalogueFailed) return json({ error: 'Catalogue denied' }, 502);
        const kind = new URL(req.url()).searchParams.get('kind');
        const hf = path.includes('higgsfield_mcp');
        return json({ models: [
          { model_id: hf ? 'brand-new' : 'veo-discovered', label: hf ? '<New model>' : 'Discovered Veo', kind,
            price: { state: 'unknown' }, quote_supported: hf, render_supported: false, inputs: {} },
        ], cached: true });
      }
      if (/\/api\/desk\/engines\/(estimate|render\/estimate|jobs|renders)/.test(path) && req.method() === 'POST') {
        requests.push({ path, method: req.method(), body: req.postDataJSON() });
        return json({ estimate: { credits: 5, usd: 0 } });
      }
      if (path === '/api/desk/studio/smoke/storyboard') return json({ scenes: [{ id: 's1', label: 'One', line: 'Scene', duration_sec: 5 }] });
      if (path === '/api/projects') return json([]);
      if (path === '/api/local-auth/status') return json({ configured: true });
      return json({});
    });
    await page.goto('http://mc.smoke.test');
    await page.waitForFunction(() => window.DeskV1Engines);
    await page.evaluate(() => {
      document.body.innerHTML = '<div id="scene-root"><div class="desk-v1-sb-scene" data-scene-id="s1">Scene one</div></div><div id="panel"></div>';
      window.DeskV1Engines.mountVideoRender(document.getElementById('panel'), {
        owner: { kind: 'studio', id: 'smoke' }, projectId: 'p1', getStoryboard: () => ({ scenes: [{ id: 's1' }] }),
        sceneRoot: document.getElementById('scene-root'),
      });
    });
    await page.waitForSelector('[data-eng-reason]');
    check(await page.locator('[data-eng-model] option').allTextContents().then(x => x.join() === '<New model>'), `${width}: discovered model replaces curated list and label is escaped`);
    check(await page.locator('[data-eng-usd]').textContent().then(x => x.includes('42')), `${width}: unregistered model displays a free quote`);
    check(await page.locator('[data-eng-render-btn]').isDisabled(), `${width}: read-only discovery does not enable unsupported generation`);
    check(await page.locator('[data-scene-render-one]').isDisabled(), `${width}: discovery-only model cannot submit a scene subset`);
    check(requests.filter(r => r.path.endsWith('/models/quote')).every(r => r.body.owner.id === 'smoke' && r.body.model_id === 'brand-new'), `${width}: quote uses saved owner and selected model`);
    await page.selectOption('[data-eng-engine]', 'google');
    await page.waitForSelector('[data-eng-estimate][data-state="unknown"]');
    check(await page.locator('[data-eng-estimate]').textContent().then(x => x.includes('Price unknown')), `${width}: no service quote means price unknown`);
    check(await page.locator('[data-eng-render-btn]').isDisabled(), `${width}: no fabricated price enables Render`);
    check(await page.locator('[data-scene-render-one]').isDisabled(), `${width}: unknown-price service cannot use a scene subset to render`);
    // Reloading after an unlock reruns account discovery; failure is an explicit
    // curated fallback, never an empty success or a cached full catalogue.
    catalogueFailed = true;
    await page.evaluate(() => window.dispatchEvent(new Event('vault-unlocked')));
    await page.waitForFunction(() => document.querySelector('[data-eng-discovery]')?.textContent.includes('Curated fallback'));
    check(await page.locator('[data-eng-model] option').allTextContents().then(x => x.join() === 'Curated Veo'), `${width}: failed discovery labels the curated fallback`);
    check(requests.every(r => r.path.endsWith('/models') || r.path.endsWith('/models/quote')), `${width}: browsing and quote never submit or call table estimates`);
    catalogueFailed = false;
    await page.evaluate(() => {
      document.body.innerHTML = '<div id="image-panel"></div>';
      window.DeskV1Engines.mountImageGenerate(document.getElementById('image-panel'), { projectId: 'p1' });
    });
    await page.waitForSelector('[data-eng-prompt]');
    await page.fill('[data-eng-prompt]', 'An abstract landscape');
    await page.locator('[data-eng-prompt]').dispatchEvent('change');
    await page.waitForSelector('[data-eng-usd]');
    check(requests.some(r => r.body?.kind === 'image' && r.body.prompt === 'An abstract landscape'), `${width}: image catalogue quote carries only text`);
    check(await page.locator('[data-eng-render-btn]').isDisabled(), `${width}: discovery-only image cannot generate`);
    check(requests.every(r => r.path.endsWith('/models') || r.path.endsWith('/models/quote')), `${width}: image flow also submits no paid request`);
    check(errors.length === 0, `${width}: zero page errors (${errors.join('; ')})`);
    await context.close();
  }
} finally {
  await browser.close();
}
process.exitCode = failed ? 1 : 0;
