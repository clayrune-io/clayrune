#!/usr/bin/env node
// Independent MC-1070 module + real shared passcode modal. Route/worker wiring
// is deliberately absent until MC-1068 lands; every network response is mocked.
import { readFileSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const assets = loadStaticJsCss(root);
const html = readFileSync(resolve(root, 'static/index.html'), 'utf8');
let bad = 0;
function check(cond, message) {
  console[cond ? 'log' : 'error'](`  ${cond ? 'PASS' : 'FAIL'} ${message}`);
  if (!cond) bad++;
}
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    const ctx = await browser.newContext({ viewport: { width, height: 950 } });
    const page = await ctx.newPage();
    const errors = [], estimates = [], posts = [];
    let bump = 0, prepared = 0, creditPricing = false, denyQuote = false;
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      const json = (body, code = 200) => route.fulfill({ status: code, contentType: 'application/json', body: JSON.stringify(body) });
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (assets[path]) return route.fulfill({ contentType: assets[path][0], body: assets[path][1] });
      if (path === '/api/projects') return json([]);
      if (path === '/api/local-auth/status') return json({ configured: true });
      if (path === '/api/desk/engines/render/estimate') {
        const body = req.postDataJSON(); estimates.push(body);
        const n = body.scene_ids.length;
        return json({ estimate: creditPricing ? { usd: 0, credits: n * 6 + bump } : { usd: n + bump, picture_pending: true },
          plan: { clips: n }, refusal: denyQuote ? { message: 'The selected render exceeds the job limit.' } : null });
      }
      if (path === '/api/desk/engines/renders' && req.method() === 'POST') {
        const body = req.postDataJSON(); posts.push(body);
        if (body.passcode !== 'smoke-only-passcode') return json({ error: 'bad_passcode' }, 403);
        if (prepared > body.shown_total) return json({ code: 'render_price_changed', error: `The price is now $${prepared}. Press Render again to accept.`,
          estimate: { usd: prepared }, plan: { clips: body.scene_ids.length } }, 409);
        return json({ render: { render_id: 'rnd-subset', status: 'rendering', scene_ids: body.scene_ids } }, 201);
      }
      return route.abort();
    });
    await page.goto('http://mc.smoke.test/');
    await page.waitForFunction(() => window.DeskV1Store && window.humanProofFetch);
    await page.evaluate(async () => {
      window.deskV1Open();
      const body = document.getElementById('desk-v1-body');
      body.innerHTML = `<ol data-scenes><li class="desk-v1-sb-scene" data-scene-id="s1">Scene 1</li><li class="desk-v1-sb-scene" data-scene-id="s2">Scene 2</li><li class="desk-v1-sb-scene" data-scene-id="s3">Scene 3</li></ol><div data-subset-toolbar></div>`;
      const { mountSceneRenderControls } = await import('/static/js/desk-v1-render-scenes.js');
      window.subset = mountSceneRenderControls(body.querySelector('[data-scenes]'), body.querySelector('[data-subset-toolbar]'), {
        request: scene_ids => ({ owner: { kind: 'studio', id: 'smoke-subsets' }, engine_id: 'google', model_id: 'veo', aspect_ratio: '16:9', scene_ids }),
        onRender: render => { window.lastSubset = render; },
        onBusy: on => { window.subsetBusy = on; },
      });
    });
    const one = page.locator('[data-scene-id="s2"] [data-scene-render-one]');
    const selected = page.locator('[data-scene-render-selected]');
    const modal = () => page.locator('[data-modal-id^="__human-proof-"]');
    const confirm = async () => {
      await modal().locator('input').fill('smoke-only-passcode');
      await modal().locator('.btn-add').click();
      await modal().waitFor({ state: 'detached' });
    };
    await one.click();
    await page.waitForFunction(() => document.querySelector('[data-scene-id="s2"] [data-scene-render-one]').textContent.includes('$1'));
    check(posts.length === 0 && estimates.at(-1).scene_ids.join() === 's2', `${width}: first single click prices only its scene, before any approval/spend`);
    await one.click(); await modal().waitFor();
    check((await modal().textContent()).includes('1 selected scene for $1'), `${width}: passcode names the exact subset and price`);
    check(await page.evaluate(() => window.subsetBusy) && await one.isDisabled(), `${width}: shared panel is told the subset approval is busy`);
    await modal().locator('.btn-secondary').click();
    await page.waitForFunction(() => document.querySelector('[data-scene-render-note]').textContent.includes('Nothing was sent'));
    check(posts.length === 0, `${width}: cancelling passcode sends no render`);
    check(!await page.evaluate(() => window.subsetBusy), `${width}: cancelled approval releases shared busy state`);
    prepared = 4;
    await one.click(); await modal().waitFor(); await confirm();
    await page.waitForFunction(() => document.querySelector('[data-scene-render-note]').textContent.includes('Press Render again'));
    check(posts.length === 1 && posts[0].shown_total === 1 && posts[0].scene_ids.join() === 's2', `${width}: higher prepared subset price stops at 409`);
    await one.click(); await modal().waitFor();
    check((await modal().textContent()).includes('$4'), `${width}: next approval shows prepared price`);
    await confirm();
    await page.waitForFunction(() => window.lastSubset);
    check(posts.length === 2 && posts[1].shown_total === 4 && posts[1].idempotency_key !== posts[0].idempotency_key, `${width}: prepared price accepted only after another passcode approval`);
    prepared = 0;
    await page.locator('[data-scene-id="s3"] [data-scene-render-select]').check();
    await page.locator('[data-scene-id="s1"] [data-scene-render-select]').check();
    await page.waitForFunction(() => document.querySelector('[data-scene-render-selected]').textContent.includes('$2'));
    check(estimates.at(-1).scene_ids.join() === 's1,s3', `${width}: selection is priced in storyboard order`);
    bump = 1;
    await selected.click();
    await page.waitForFunction(() => document.querySelector('[data-scene-render-note]').textContent.includes('price changed'));
    check(posts.length === 2 && await modal().count() === 0, `${width}: changed free quote needs another click before passcode`);
    await selected.click(); await modal().waitFor(); await confirm();
    check(posts.length === 3 && posts[2].scene_ids.join() === 's1,s3' && posts[2].shown_total === 3, `${width}: selected render sends precisely two scenes through the real shared gate`);
    await page.evaluate(() => window.subset.refresh());
    await page.waitForFunction(() => document.querySelector('[data-scene-render-selected]').textContent.includes('$3'));
    check(!(await one.textContent()).includes('$4'), `${width}: storyboard refresh discards stale single-scene confirmation`);
    await page.locator('[data-scene-id="s1"] [data-scene-render-select]').uncheck();
    await page.locator('[data-scene-id="s3"] [data-scene-render-select]').uncheck();
    check(await selected.isDisabled() && (await selected.textContent()).includes('(0)'), `${width}: empty selection cannot render`);
    creditPricing = true; bump = 0; denyQuote = true;
    await page.evaluate(() => window.subset.refresh());
    await one.click();
    await page.waitForFunction(() => document.querySelector('[data-scene-render-note]').textContent.includes('job limit'));
    check(posts.length === 3 && !await one.isDisabled(), `${width}: refused subset cannot spend, but permits repricing after a limit correction`);
    denyQuote = false;
    await one.click();
    await page.waitForFunction(() => document.querySelector('[data-scene-render-note]').textContent.includes('6 credits'));
    await one.click(); await modal().waitFor();
    check((await modal().textContent()).includes('6 credits'), `${width}: plan-credit subset approval uses credits, not dollars`);
    await confirm();
    check(posts.length === 4 && posts[3].shown_total === 6, `${width}: plan-credit subset sends the displayed six-credit amount`);
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${width}: no viewport overflow`);
    check(errors.length === 0, `${width}: no browser exceptions (${errors.join('; ')})`);
    mkdirSync(resolve(root, '_scratch/mc1070'), { recursive: true });
    await page.screenshot({ path: resolve(root, `_scratch/mc1070/controls-${width}.png`), fullPage: true });
    await ctx.close();
  }
} finally { await browser.close(); }
console.log(`\nMC-1070 independent controls: fails=${bad}`);
process.exitCode = bad ? 1 : 0;
