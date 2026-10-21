#!/usr/bin/env node
// Product engine panel, deterministic saved/absent OpenAI connections and held
// quote responses. All HTTP is intercepted; this is not authenticated acceptance.
import { readFileSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const assets = loadStaticJsCss(root);
const html = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const shots = resolve(root, '_scratch/mc1067/shots');
mkdirSync(shots, { recursive: true });
let failed = 0, checks = 0;
const check = (yes, label) => { checks++; console[yes ? 'log' : 'error'](`${yes ? 'PASS' : 'FAIL'} ${label}`); if (!yes) failed++; };
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    for (const scenario of ['saved', 'absent', 'empty']) {
      const context = await browser.newContext({ viewport: { width, height: 950 } });
      const page = await context.newPage();
      const errors = [], requests = [], pending = [];
      let hold = false, failNext = false, quoteCount = 0, heldSignal, heldTarget;
      const heldRequest = (target) => { heldTarget = target; return new Promise(resolve => { heldSignal = resolve; }); };
      page.on('pageerror', error => errors.push(error.stack));
      const engines = [
        { id: 'higgsfield_mcp', label: 'Higgsfield (sign in)', currency: 'credits', connected: { ready: true }, model_discovery: true,
          models: [{ model_id: 'kling3_0', label: 'Kling 3.0', kind: 'video', inputs: {} }] },
        { id: 'openai', label: 'OpenAI image', connected: { ready: scenario !== 'absent', exists: scenario !== 'absent' }, model_discovery: true,
          models: [{ model_id: 'gpt-image-2.5-sunburst', label: 'GPT Image 2.5', kind: 'image', inputs: {} }] },
      ];
      await page.route('**/*', async route => {
        const req = route.request(), url = new URL(req.url()), path = url.pathname;
        const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
        if (path === '/') return route.fulfill({ contentType: 'text/html', body: html });
        if (assets[path]) return route.fulfill({ contentType: assets[path][0], body: assets[path][1] });
        if (path.startsWith('/api/desk/engines')) requests.push({ path, method: req.method(), body: req.postData() ? req.postDataJSON() : null });
        if (path === '/api/desk/engines') return json({ engines });
        if (path === '/api/desk/engines/renders') return json({ render: null });
        if (path.endsWith('/models')) {
          const kind = url.searchParams.get('kind'), hf = path.includes('higgsfield_mcp');
          const models = scenario === 'empty' && !hf ? [] : [{
            model_id: hf ? (kind === 'video' ? 'kling3_0' : 'gpt_image_2_5') : kind === 'video' ? 'sora-2' : 'gpt-image-new',
            label: hf ? (kind === 'video' ? 'Kling 3.0' : 'GPT Image 2.5') : kind === 'video' ? 'sora-2' : 'gpt-image-new', kind,
            inputs: {}, price: { state: 'unknown' }, quote_supported: hf, render_supported: hf,
          }];
          return json({ models, cached: false });
        }
        if (path === '/api/desk/engines/render/estimate') {
          quoteCount++;
          if (hold) await new Promise(release => {
            pending.push(release);
            if (quoteCount === heldTarget) heldSignal();
          });
          if (failNext) { failNext = false; return json({ error: 'Fixture vendor quote failed' }, 502); }
          return json({ estimate: { credits: 12, usd: 0 }, plan: { clips: 2 } });
        }
        if (path === '/api/desk/engines/estimate') return json({ error: 'No saved connection' }, 409);
        if (path === '/api/projects') return json([]);
        if (path === '/api/local-auth/status') return json({ configured: true });
        // Never fall through to a network vendor or real mutation.
        if (req.method() !== 'GET') return route.abort();
        return json({});
      });
      await page.goto('http://mc.smoke.test');
      await page.waitForFunction(() => window.DeskV1Engines);
      await page.evaluate(() => {
        for (const child of document.body.children) child.style.display = 'none';
        document.body.insertAdjacentHTML('beforeend', '<div id="fixture-host"><div id="scene-root"><div class="desk-v1-sb-scene" data-scene-id="s1">One</div></div><div id="panel"></div></div>');
        window.DeskV1Engines.mountVideoRender(document.getElementById('panel'), {
          owner: { kind: 'studio', id: 'fixture' }, projectId: 'fixture-project',
          getStoryboard: () => ({ scenes: [{ id: 's1' }, { id: 's2' }] }), sceneRoot: document.getElementById('scene-root'),
        });
      });
      await page.waitForSelector('[data-eng-estimate][data-state="ok"]');
      const options = await page.locator('[data-eng-engine] option').evaluateAll(items => items.map(item => item.value));
      check(options.includes('openai') === (scenario === 'saved'), `${width}/${scenario}: OpenAI video requires saved discovery with a video model`);
      check(requests.some(r => r.path === '/api/desk/engines/openai/models') === (scenario !== 'absent'), `${width}/${scenario}: no discovery without saved connection`);
      if (scenario === 'saved') {
        await page.selectOption('[data-eng-engine]', 'openai');
        await page.waitForSelector('[data-eng-estimate][data-state="unknown"]');
        check(await page.locator('[data-eng-model]').inputValue() === 'sora-2', `${width}: discovered video enters image-only curated service`);
        check(await page.locator('[data-eng-estimate]').innerText().then(t => t.includes('Price unknown')), `${width}: OpenAI quote stays unknown`);
        check(await page.locator('[data-eng-render-btn]').isDisabled() && await page.locator('[data-scene-render-one]').isDisabled(), `${width}: OpenAI whole/subset Render stay disabled`);
        await page.screenshot({ path: resolve(shots, `openai-saved-video-${width}.png`) });
        await page.selectOption('[data-eng-engine]', 'higgsfield_mcp');
        await page.waitForSelector('[data-eng-estimate][data-state="ok"]');

        hold = true;
        const before = quoteCount;
        const started = heldRequest(before + 1);
        await page.click('[data-eng-reprice]');
        await started;
        check(await page.locator('[data-eng-reprice]').isDisabled(), `${width}: Price again disabled during outstanding whole-board quote`);
        check(await page.locator('[data-eng-render-btn]').isDisabled(), `${width}: pending quote cannot Render`);
        // On the original code a real enabled button click starts a second
        // request. Await that request explicitly to preserve fail-before evidence.
        if (!(await page.locator('[data-eng-reprice]').isDisabled())) {
          const duplicate = heldRequest(before + 2);
          await page.click('[data-eng-reprice]');
          await duplicate;
        } else {
          await page.locator('[data-eng-reprice]').evaluate(button => { button.click(); button.click(); });
        }
        check(quoteCount === before + 1, `${width}: repeated Price again clicks do not start duplicate quotes (observed ${quoteCount - before})`);
        check(await page.locator('[data-eng-engine]').isEnabled(), `${width}: engine picker remains usable during quote`);
        await page.screenshot({ path: resolve(shots, `quote-pending-${width}.png`) });
        // Changing model/service still works; stale quote must not overwrite it.
        await page.selectOption('[data-eng-engine]', 'openai');
        await page.waitForSelector('[data-eng-estimate][data-state="unknown"]');
        hold = false;
        pending.splice(0).forEach(release => release());
        await page.waitForLoadState('networkidle');
        check(await page.locator('[data-eng-estimate]').getAttribute('data-state') === 'unknown', `${width}: old quote cannot overwrite new service`);
        await page.selectOption('[data-eng-engine]', 'higgsfield_mcp');
        await page.waitForSelector('[data-eng-estimate][data-state="ok"]');
        const settled = quoteCount;
        await page.click('[data-eng-reprice]');
        await page.waitForSelector('[data-eng-estimate][data-state="ok"]');
        check(quoteCount === settled + 1 && await page.locator('[data-eng-reprice]').isEnabled(), `${width}: explicit fresh quote works after completion`);
        failNext = true;
        await page.click('[data-eng-reprice]');
        await page.waitForSelector('[data-eng-estimate][data-state="refused"]');
        check(await page.locator('[data-eng-reprice]').isEnabled() && await page.locator('[data-eng-render-btn]').isDisabled(), `${width}: failed quote restores Price again and keeps Render disabled`);
        await page.click('[data-eng-reprice]');
        await page.waitForSelector('[data-eng-estimate][data-state="ok"]');
        check(await page.locator('[data-eng-reprice]').isEnabled(), `${width}: explicit retry recovers after a failed quote`);
      }
      await page.evaluate(() => {
        document.getElementById('fixture-host').innerHTML = '<div id="image"></div>';
        window.DeskV1Engines.mountImageGenerate(document.getElementById('image'), { projectId: 'fixture-project' });
      });
      await page.waitForSelector('[data-eng-engine]');
      const images = await page.locator('[data-eng-engine] option').evaluateAll(items => items.map(item => item.value));
      check(images.includes('openai') === (scenario !== 'empty'), `${width}/${scenario}: absent image connection remains visible; empty catalogue invents nothing`);
      if (scenario !== 'empty') {
        await page.selectOption('[data-eng-engine]', 'openai');
        await page.fill('[data-eng-prompt]', 'Fixture landscape');
        await page.locator('[data-eng-prompt]').dispatchEvent('change');
        await page.waitForSelector(`[data-eng-estimate][data-state="${scenario === 'absent' ? 'refused' : 'unknown'}"]`);
        check(await page.locator('[data-eng-render-btn]').isDisabled(), `${width}/${scenario}: image Generate remains gated`);
        check((await page.locator('[data-eng-not-connected]').count() > 0) === (scenario === 'absent'), `${width}/${scenario}: missing saved connection is explicit`);
        await page.screenshot({ path: resolve(shots, `openai-${scenario}-image-${width}.png`) });
      }
      check(!requests.some(r => r.method !== 'GET' && !r.path.endsWith('/estimate')), `${width}/${scenario}: zero render/job/approval calls`);
      check(errors.length === 0, `${width}/${scenario}: no page errors (${errors.join('; ')})`);
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${width}/${scenario}: no horizontal overflow`);
      await context.close();
    }
  }
} finally { await browser.close(); }
console.log(`Checks=${checks} failures=${failed}`);
process.exitCode = failed ? 1 : 0;
