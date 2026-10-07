#!/usr/bin/env node
// MC-1070 shared panel wiring + asynchronous subset holds, mocked APIs only.
import { readFileSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const assets = loadStaticJsCss(root), html = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const owner = { kind: 'studio', id: 'subset-flow' };
let bad = 0;
const check = (value, message) => { console[value ? 'log' : 'error'](`${value ? 'PASS' : 'FAIL'} ${message}`); if (!value) bad++; };
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    for (const surface of ['studio', 'director']) {
      const ctx = await browser.newContext({ viewport: { width, height: 980 } });
      const page = await ctx.newPage(), posts = [], errors = [], acks = [];
      let record = null, polls = 0, paid = 0;
      page.on('pageerror', e => errors.push(e.message));
      await page.addInitScript(() => {
        const later = window.setTimeout;
        window.setTimeout = (fn, ms, ...args) => later(fn, ms === 3000 ? 100 : ms, ...args);
      });
      const quote = ids => ({ estimate: { usd: 0, credits: (ids || ['s1', 's2', 's3']).length * 6, picture_pending: true },
        plan: { clips: (ids || ['s1', 's2', 's3']).length }, refusal: null,
        board_fingerprint: JSON.stringify(ids || ['s1', 's2', 's3']) });
      await page.route('**/*', async route => {
        const req = route.request(), path = new URL(req.url()).pathname;
        const json = (body, code = 200) => route.fulfill({ status: code, contentType: 'application/json', body: JSON.stringify(body) });
        if (path === '/') return route.fulfill({ contentType: 'text/html', body: html });
        if (assets[path]) return route.fulfill({ contentType: assets[path][0], body: assets[path][1] });
        if (path === '/api/projects') return json([]);
        if (path === '/api/local-auth/status') return json({ configured: true });
        if (path === '/api/desk/engines') return json({ engines: [{ id: 'higgsfield_mcp', label: 'Higgsfield', currency: 'credits', connected: { ready: true }, job_limit_credits: 100,
          models: [{ model_id: 'seedance', label: 'Seedance', kind: 'video', aspect_ratios: ['16:9'] }] }] });
        if (path === '/api/desk/engines/render/estimate') return json(quote(req.postDataJSON().scene_ids));
        if (path === '/api/desk/engines/renders' && req.method() === 'GET') return json({ render: record });
        if (path === '/api/desk/engines/renders' && req.method() === 'POST') {
          const body = req.postDataJSON(); posts.push(body);
          if (body.passcode !== 'smoke-only-passcode') return json({ error: 'bad_passcode' }, 403);
          polls = 0;
          record = { render_id: `rnd-${posts.length}`, owner, engine_id: body.engine_id, model_id: body.model_id, aspect_ratio: body.aspect_ratio,
            status: 'pending', submission_state: 'pending', progress: { ready: 0, total: body.scene_ids.length },
            scene_ids: body.scene_ids, reused_scene_ids: ['s1', 's2', 's3'].filter(id => !body.scene_ids.includes(id)), complete_storyboard: true,
            request: body, scenes: [], outputs: [], clips: [], hold: null };
          return json({ render: record }, 201);
        }
        if (path.endsWith('/acknowledge')) {
          acks.push(req.postDataJSON());
          record = { ...record, status: 'failed', submission_state: 'failed', hold: null,
            failure: { kind: 'submission_acknowledged', message: 'A new render is allowed. Existing clips are kept.' } };
          return json({ render: record });
        }
        if (path.startsWith('/api/desk/engines/renders/rnd-')) {
          if (record.hold?.kind === 'submit_uncertain' || record.failure?.kind === 'submission_acknowledged') return json({ render: record });
          polls++;
          if (polls === 1) record = { ...record, status: 'working', submission_state: 'working' };
          else if (record.request.shown_total < record.scene_ids.length * 8) {
            const n = record.scene_ids.length;
            record = { ...record, status: 'held', submission_state: 'held', hold: {
              kind: 'render_price_changed', code: 'render_price_changed', message: 'The selected picture price increased. Press Render again to accept.',
              estimate: { usd: 0, credits: n * 8 }, plan: { clips: n }, board_fingerprint: JSON.stringify(record.scene_ids),
              request: { owner: { id: owner.id, kind: owner.kind }, engine_id: record.engine_id, model_id: record.model_id,
                aspect_ratio: record.aspect_ratio, scene_ids: record.scene_ids } } };
          } else if (polls === 2) {
            paid += record.scene_ids.length;
            record = { ...record, status: 'rendering', submission_state: 'submitted' };
          } else record = { ...record, status: 'ready', progress: { ready: record.scene_ids.length, total: record.scene_ids.length }, cost_credits: record.scene_ids.length * 8 };
          return json({ render: record });
        }
        return route.abort();
      });
      const mount = async () => {
        await page.goto('http://mc.smoke.test/');
        await page.waitForFunction(() => window.DeskV1Engines && window.humanProofFetch);
        await page.evaluate(({ owner, surface }) => {
          window.deskV1Open();
          const body = document.getElementById('desk-v1-body');
          body.innerHTML = `<div class="${surface === 'studio' ? 'desk-v1-sb-scenes' : 'desk-v1-video-scenestrip'}" data-scene-test-root>${['s1', 's2', 's3'].map(id => `<div class="${surface === 'studio' ? 'desk-v1-sb-scene' : 'desk-v1-video-scene'}" style="flex-basis:33.333%" data-scene-id="${id}">${id}</div>`).join('')}</div><div data-engine-test-host></div>`;
          window.DeskV1Engines.mountVideoRender(body.querySelector('[data-engine-test-host]'), { owner, sceneRoot: body.querySelector('[data-scene-test-root]') });
        }, { owner, surface });
        await page.waitForSelector('[data-scene-render-one]');
        await page.waitForSelector('[data-eng-estimate][data-state="ok"]');
      };
      const one = () => page.locator('[data-scene-id="s2"] [data-scene-render-one]');
      const modal = () => page.locator('[data-modal-id^="__human-proof-"]');
      const status = word => page.waitForSelector(`[data-eng-render-status][data-status="${word}"]`);
      const approve = async () => { await modal().waitFor(); await modal().locator('input').fill('smoke-only-passcode'); await modal().locator('.btn-add').click(); await modal().waitFor({ state: 'detached' }); };
      const tag = `${surface} ${width}`;
      await mount(); await one().click();
      await page.waitForFunction(() => document.querySelector('[data-scene-id="s2"] [data-scene-render-one]').textContent.includes('6 credits'));
      await one().click(); await modal().waitFor();
      check(await page.locator('[data-eng-render-btn]').isDisabled(), `${tag}: subset proof disables full Render too`);
      await modal().locator('.btn-secondary').click();
      await page.waitForFunction(() => !document.querySelector('[data-scene-id="s2"] [data-scene-render-one]').disabled);
      check(posts.length === 0 && paid === 0, `${tag}: cancellation sends nothing`);
      await one().click(); await approve(); await status('held');
      await page.waitForFunction(() => document.querySelector('[data-scene-id="s2"] [data-scene-render-one]').textContent.includes('8 credits'));
      check(posts.length === 1 && paid === 0 && posts[0].scene_ids.join() === 's2', `${tag}: pending submission holds before any generation on selected higher price`);
      check((await page.locator('[data-eng-render-btn]').textContent()).includes('18 credits'), `${tag}: subset held price never replaces full-board quote`);
      await mount();
      await page.waitForFunction(() => document.querySelector('[data-scene-id="s2"] [data-scene-render-one]').textContent.includes('8 credits'));
      check(posts.length === 1 && paid === 0, `${tag}: reload restores saved subset offer without submitting`);
      await one().click(); await approve(); await status('ready');
      check(posts[1].shown_total === 8 && paid === 1 && posts[1].scene_ids.join() === 's2', `${tag}: second passcode accepts only selected scene at held amount`);
      check((await page.locator('[data-scene-render-summary]').textContent()).includes('2 existing'), `${tag}: result explains clip reuse`);
      await page.locator('[data-scene-id="s3"] [data-scene-render-select]').check();
      await page.locator('[data-scene-id="s1"] [data-scene-render-select]').check();
      await page.waitForFunction(() => document.querySelector('[data-scene-render-selected]').textContent.includes('12 credits'));
      await page.locator('[data-scene-render-selected]').click(); await approve(); await status('held');
      await page.waitForFunction(() => document.querySelector('[data-scene-render-selected]').textContent.includes('16 credits'));
      check(posts[2].scene_ids.join() === 's1,s3' && paid === 1, `${tag}: two-scene price and held record use canonical selection only`);
      await mount();
      await page.waitForFunction(() => document.querySelector('[data-scene-render-selected]').textContent.includes('16 credits'));
      check(await page.locator('[data-scene-render-select]:checked').count() === 2 && posts.length === 3 && paid === 1,
        `${tag}: reload restores multi-scene selection and held price without spending`);
      await page.locator('[data-scene-render-selected]').click(); await approve(); await status('ready');
      check(posts[3].shown_total === 16 && paid === 3, `${tag}: two selected clips generated once, other scene not repaid`);
      record = { ...record, status: 'held', submission_state: 'held', hold: { kind: 'submit_uncertain', message: 'Check the engine dashboard. Nothing was retried.' },
        clips: [{ path: 'desk/library/video/Generated/accepted.mp4', mime: 'video/mp4' }] };
      await mount();
      check(await one().isDisabled() && await page.locator('[data-eng-render-btn]').isDisabled(), `${tag}: uncertain hold blocks full and subset paid retry`);
      await page.locator('[data-eng-acknowledge]').click();
      await status('failed');
      check(acks.length === 1 && posts.length === 4 && paid === 3, `${tag}: free acknowledgement changes no paid submission`);
      check(await page.locator('[data-eng-output]').count() === 1, `${tag}: acknowledgement retains accepted clip`);
      await page.waitForFunction(() => !document.querySelector('[data-scene-id="s2"] [data-scene-render-one]').disabled);
      await one().click();
      await page.waitForFunction(() => document.querySelector('[data-scene-id="s2"] [data-scene-render-one]').textContent.includes('6 credits'));
      await one().click(); await modal().waitFor();
      check(posts.length === 4, `${tag}: new subset still requires passcode after acknowledgement`);
      await modal().locator('.btn-secondary').click();
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${tag}: controls fit viewport`);
      check(errors.length === 0, `${tag}: no browser exceptions ${errors.join('; ')}`);
      mkdirSync(resolve(root, '_scratch/mc1070'), { recursive: true });
      await page.screenshot({ path: resolve(root, `_scratch/mc1070/wired-${surface}-${width}.png`), fullPage: true });
      await ctx.close();
    }
  }
} finally { await browser.close(); }
console.log(`MC-1070 wired controls: fails=${bad}`);
process.exitCode = bad ? 1 : 0;
