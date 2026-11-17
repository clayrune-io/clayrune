#!/usr/bin/env node
// Studio image panel: Generate says why it is off, price-only models are marked,
// and a model that lists resolutions gets a Resolution picker that is sent.
// Mocked server; never submits a job (any POST to /jobs fails the run).
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

// What GET /api/desk/engines/<id>/models?kind=image really returns (2026-11-14).
const HF = [
  { model_id: 'gpt_image_2_5', label: 'GPT Image 2.5 (image)', render_supported: true },
  { model_id: 'soul_2', label: 'Soul 2 (portraits, fashion)', render_supported: true },
  { model_id: 'nano_banana', label: 'Nano Banana (image)', render_supported: true },
  { model_id: 'nano_banana_pro', label: 'Nano Banana Pro', render_supported: false, resolutions: ['1k', '2k', '4k'] },
].map(m => ({ kind: 'image', inputs: {}, price: { state: 'unknown' }, quote_supported: true, resolutions: [], ...m }));
const GOOGLE = [
  { model_id: 'gemini-3.1-flash-image', label: 'Gemini 3.1 Flash Image', resolutions: ['1K', '2K', '4K'] },
  { model_id: 'gemini-3.1-flash-lite-image', label: 'Gemini 3.1 Flash Lite Image', resolutions: ['1K'] },
].map(m => ({ kind: 'image', inputs: {}, price: { state: 'unknown' }, quote_supported: false, render_supported: true, ...m }));
// A priced model that lists resolutions: reaches the estimate route.
const SOUL_KEY = [{ model_id: 'soul-std', label: 'Soul 2 (image)', kind: 'image', inputs: {}, price: { state: 'quoted' },
  quote_supported: false, render_supported: true, resolutions: ['2K', '4K'] }];

const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 950 } });
    const page = await context.newPage();
    const errors = [], estimates = [], paid = [];
    let failEstimate = false;
    page.on('pageerror', e => errors.push(`${e.message} @ ${(e.stack || '').split('\n').slice(1, 3).join(' ')}`));
    const engines = [
      { id: 'higgsfield_mcp', label: 'Higgsfield', currency: 'credits', connected: { ready: true }, model_discovery: true, models: [] },
      { id: 'google', label: 'Google', connected: { ready: true }, model_discovery: true, models: [] },
      { id: 'higgsfield', label: 'Higgsfield (API key)', connected: { ready: true }, model_discovery: true, models: [] },
    ];
    await page.route('**/*', async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (assets[path]) return route.fulfill({ contentType: assets[path][0], body: assets[path][1] });
      if (path === '/api/desk/engines') return json({ engines });
      if (path.endsWith('/models/quote')) return json({ price: { state: 'quoted', credits: 3 }, note: 'Text-only quote.' });
      if (path.endsWith('/models')) {
        const id = path.split('/')[4];
        return json({ models: id === 'google' ? GOOGLE : id === 'higgsfield' ? SOUL_KEY : HF, cached: false });
      }
      if (path === '/api/desk/engines/estimate') {
        const body = req.postDataJSON();
        estimates.push(body);
        const allowed = body.engine_id === 'higgsfield' ? ['2K', '4K'] : null;
        if (allowed && !allowed.includes(body.resolution)) return json({ error: `resolution must be one of ${JSON.stringify(allowed)}` }, 400);
        if (failEstimate) return json({ error: 'The engine refused the price check' }, 502);
        return json({ currency: 'credits', estimate: { credits: 1, usd: 0.067 }, refusal: null });
      }
      if (path === '/api/desk/engines/jobs') { paid.push(path); return route.abort(); }
      if (path === '/api/projects') return json([]);
      if (path === '/api/local-auth/status') return json({ configured: true });
      return json({});
    });
    await page.goto('http://mc.smoke.test');
    await deskLoaded(page);
    await page.waitForFunction(() => window.DeskV1Engines);
    const mount = async () => {
      await page.evaluate(() => {
        for (const child of document.body.children) child.style.display = 'none';
        document.getElementById('image-panel')?.remove();
        document.body.insertAdjacentHTML('beforeend', '<div id="image-panel"></div>');
        window.DeskV1Engines.mountImageGenerate(document.getElementById('image-panel'), { projectId: 'p1', key: 'k' + Math.random() });
      });
      await page.waitForSelector('[data-eng-prompt]');
    };
    await mount();
    const why = () => page.locator('[data-eng-why]');
    const btn = page.locator('[data-eng-render-btn]');

    // Empty prompt.
    check(await btn.isDisabled(), `${width}: Generate is off with no prompt`);
    check(await why().getAttribute('data-code') === 'no_prompt', `${width}: empty prompt says why`);

    // Model select marks the price-only model and only that one.
    const labels = await page.locator('[data-eng-model] option').allTextContents();
    check(labels.find(l => l.startsWith('Nano Banana Pro'))?.endsWith('· price only'), `${width}: non-renderable model is marked "· price only" (${labels.join(' | ')})`);
    check(labels.filter(l => l.includes('price only')).length === 1, `${width}: renderable models carry no mark`);

    // Renderable Nano Banana with a prompt: Generate is on, no reason shown, no resolution picker.
    await page.selectOption('[data-eng-model]', 'nano_banana');
    await page.fill('[data-eng-prompt]', 'a red apple');
    await page.locator('[data-eng-prompt]').dispatchEvent('change');
    await page.waitForSelector('[data-eng-usd]');
    check(await btn.isEnabled(), `${width}: renderable Nano Banana with a prompt enables Generate`);
    check(await why().count() === 0, `${width}: no reason line while Generate is on`);
    check(await page.locator('[data-eng-resolution]').count() === 0, `${width}: a model with no resolutions has no Resolution picker`);
    check(estimates.at(-1) && !('resolution' in estimates.at(-1)), `${width}: sign-in model's estimate carries no resolution`);

    // Price-only Nano Banana Pro: off, says so, names the models that work.
    await page.selectOption('[data-eng-model]', 'nano_banana_pro');
    await page.waitForSelector('[data-eng-why][data-code="not_renderable"]');
    const text = await why().textContent();
    check(await btn.isDisabled(), `${width}: Generate is off for the price-only model`);
    check(text.includes('Nano Banana Pro') && text.includes('Nano Banana (image)') && text.includes('Soul 2 (portraits, fashion)')
      && !text.includes('Models it can render:' + ' Nano Banana Pro'), `${width}: reason names the model and those that can render (${text})`);

    // Google: resolution picker from the model's list, first value pre-selected.
    await page.selectOption('[data-eng-engine]', 'google');
    await page.waitForSelector('[data-eng-resolution]');
    check((await page.locator('[data-eng-resolution] option').allTextContents()).join() === '1K,2K,4K', `${width}: Gemini Flash offers 1K, 2K, 4K`);
    check(await page.locator('[data-eng-resolution]').inputValue() === '1K', `${width}: first (cheapest) resolution is pre-selected`);
    await page.selectOption('[data-eng-model]', 'gemini-3.1-flash-lite-image');
    check((await page.locator('[data-eng-resolution] option').allTextContents()).join() === '1K', `${width}: Flash Lite offers 1K only`);
    // Google's discovered price is unknown with no free quote: the panel says so.
    await page.waitForSelector('[data-eng-why][data-code="no_price"]');
    check(await btn.isDisabled(), `${width}: unknown-price Google model stays off, with a reason`);

    // A priced model with resolutions: the picker is sent, a bad value is shown as text.
    await page.selectOption('[data-eng-engine]', 'higgsfield');
    await page.waitForSelector('[data-eng-resolution]');
    await page.waitForSelector('[data-eng-usd]');
    check(estimates.at(-1).resolution === '2K', `${width}: estimate sends the pre-selected resolution (${estimates.at(-1)?.resolution})`);
    check(await btn.isEnabled(), `${width}: Generate on once the resolution is sent`);
    await page.selectOption('[data-eng-resolution]', '4K');
    await page.waitForFunction(() => document.querySelector('[data-eng-estimate]')?.dataset.state === 'ok');
    check(estimates.at(-1).resolution === '4K', `${width}: changing the picker re-prices with the new resolution`);
    failEstimate = true;
    await page.selectOption('[data-eng-resolution]', '2K');
    await page.waitForSelector('[data-eng-estimate][data-state="refused"]');
    const shown = await page.locator('[data-eng-estimate] [data-eng-reason]').innerText();
    check(shown.includes('The engine refused the price check') && await page.locator('[data-eng-reason]').isVisible(), `${width}: estimate error is visible text (${shown})`);
    check(await btn.isDisabled() && await why().getAttribute('data-code') === 'estimate_failed', `${width}: Generate off with a reason after a failed estimate`);

    check(paid.length === 0, `${width}: nothing was submitted`);
    check(errors.length === 0, `${width}: zero page errors (${errors.join('; ')})`);
    await context.close();
  }
} finally {
  await browser.close();
}
process.exitCode = failed ? 1 : 0;
