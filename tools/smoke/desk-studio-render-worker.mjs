#!/usr/bin/env node
// Real Studio engine panel, scripted async records. No vendor, passcode or spend.
import { readFileSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const assets = loadStaticJsCss(root);
const html = readFileSync(resolve(root, 'static/index.html'), 'utf8');
mkdirSync(resolve(root, '_scratch/render_worker'), { recursive: true });
let checks = 0;
function check(value, label) { if (!value) throw new Error(label); checks++; console.log(`PASS ${label}`); }
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    const ctx = await browser.newContext({ viewport: { width, height: 950 } });
    const page = await ctx.newPage();
    const posts = [], proofs = [];
    const owner = { kind: 'studio', id: 'worker-smoke' };
    let record = null, polls = 0, cancel = false, prepared = 79, board = 'v1';
    const quote = () => ({ estimate: { usd: 0, credits: 72, picture_pending: true },
      plan: { clips: 2, total_credits: 72, scenes: [] }, refusal: null, board_fingerprint: board });
    await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
    await page.route('**/*', async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (assets[path]) return route.fulfill({ contentType: assets[path][0], body: assets[path][1] });
      if (path === '/api/projects') return json([]);
      if (path === '/api/local-auth/status') return json({ configured: true });
      if (path === '/api/desk/engines') return json({ engines: [{ id: 'higgsfield_mcp', label: 'Higgsfield', currency: 'credits', connected: { ready: true }, job_limit_credits: 100,
        models: [{ model_id: 'seedance', label: 'Seedance', kind: 'video', aspect_ratios: ['16:9', '9:16'] }] }] });
      if (path === '/api/desk/engines/render/estimate') return json(quote());
      if (path === '/api/desk/engines/renders' && req.method() === 'GET') return json({ render: record });
      if (path === '/api/desk/engines/renders' && req.method() === 'POST') {
        const body = req.postDataJSON(); posts.push(body); polls = 0;
        record = { render_id: `rnd-${posts.length}`, status: 'pending', submission_state: 'pending',
          owner, engine_id: body.engine_id, model_id: body.model_id, aspect_ratio: body.aspect_ratio,
          progress: { ready: 0, total: 2 }, scenes: [], outputs: [], cost_credits: 0,
          submittedRequest: body, boardAtClick: board };
        return json({ render: record, replay: false }, 201);
      }
      if (path.startsWith('/api/desk/engines/renders/rnd-')) {
        polls++;
        if (polls === 1) Object.assign(record, { status: 'working', submission_state: 'working' });
        else if (record.submittedRequest.shown_total < prepared) {
          const body = record.submittedRequest;
          Object.assign(record, { status: 'held', submission_state: 'held', hold: {
            kind: 'render_price_changed', code: 'render_price_changed',
            message: `Price with your pictures is ${prepared} credits (was ${body.shown_total}). Press Render again to accept.`,
            prepared_total: prepared, shown_total: body.shown_total, currency: 'credits',
            estimate: { usd: 0, credits: prepared }, plan: { clips: 2, total_credits: prepared },
            board_fingerprint: record.boardAtClick,
            request: { owner: { id: owner.id, kind: owner.kind }, engine_id: body.engine_id, model_id: body.model_id, aspect_ratio: body.aspect_ratio } } });
        } else if (polls === 2) Object.assign(record, { status: 'rendering', submission_state: 'submitted' });
        else Object.assign(record, { status: 'ready', progress: { ready: 2, total: 2 } });
        return json({ render: record });
      }
      return route.abort();
    });
    await page.exposeFunction('smokeProof', proof => { proofs.push(proof); return !cancel; });
    const mount = async (enabled = true) => {
      await page.goto('http://mc.smoke.test/');
      await page.waitForFunction(() => window.DeskV1Engines && window.DeskV1Store);
      await page.evaluate(owner => {
        window.humanProofFetch = async (url, options, proof) => {
          if (!await window.smokeProof(proof)) return null;
          const response = await fetch(url, { ...options, headers: { 'Content-Type': 'application/json' } });
          return { ok: response.ok, status: response.status, body: await response.json() };
        };
        window.deskV1Open();
        const host = document.createElement('div');
        document.getElementById('desk-v1-body').replaceChildren(host);
        window.DeskV1Engines.mountVideoRender(host, { owner });
      }, owner);
      await page.waitForSelector('[data-eng-estimate][data-state="ok"]');
      if (enabled) await page.waitForFunction(() => {
        const button = document.querySelector('[data-eng-render-btn]');
        return button && !button.disabled;
      });
    };
    const waitStatus = status => page.waitForSelector(`[data-eng-render-status][data-status="${status}"]`);
    await mount();
    await page.click('[data-eng-render-btn]'); await waitStatus('pending');
    check(await page.locator('[data-eng-render-btn]').isDisabled(), `${width}: pending disables duplicate Render`);
    await waitStatus('working');
    check(/Working/.test(await page.locator('[data-eng-render-status]').innerText()), `${width}: uploading progress is plain`);
    await waitStatus('held');
    check(posts.length === 1 && /79 credits/.test(await page.locator('[data-eng-render-btn]').innerText()), `${width}: poll adopts higher price without another submit`);
    check(!await page.locator('[data-eng-error]').count(), `${width}: held explanation appears once`);
    await page.locator('[data-eng-render-status]').scrollIntoViewIfNeeded();
    await page.screenshot({ path: resolve(root, `_scratch/render_worker/held-${width}.png`) });
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${width}: held panel fits viewport`);
    await mount();
    check(/79 credits/.test(await page.locator('[data-eng-render-btn]').innerText()) && posts.length === 1, `${width}: reload restores offer without spending`);
    cancel = true; await page.click('[data-eng-render-btn]');
    await page.waitForFunction(() => /passcode was not entered/.test(document.querySelector('[data-eng-error]')?.textContent || ''));
    check(posts.length === 1 && /79 credits/.test(await page.locator('[data-eng-render-btn]').innerText()), `${width}: cancelled passcode retains offer and sends nothing`);
    cancel = false; prepared = 85; await page.click('[data-eng-render-btn]'); await waitStatus('pending'); await waitStatus('held');
    check(posts[1].shown_total === 79 && /85 credits/.test(await page.locator('[data-eng-render-btn]').innerText()), `${width}: another increase holds again`);
    await page.click('[data-eng-reprice]');
    await page.waitForFunction(() => /72 credits/.test(document.querySelector('[data-eng-render-btn]')?.textContent || ''));
    check(/72 credits/.test(await page.locator('[data-eng-render-btn]').innerText()), `${width}: explicit repricing clears offer`);
    await mount();
    check(/72 credits/.test(await page.locator('[data-eng-render-btn]').innerText()), `${width}: reload preserves explicit price dismissal`);
    // A changed board on reload must not resurrect an older offer.
    await page.evaluate(() => sessionStorage.clear());
    board = 'edited-v2'; await mount();
    check(/72 credits/.test(await page.locator('[data-eng-render-btn]').innerText()), `${width}: changed storyboard rejects saved offer`);
    await page.click('[data-eng-render-btn]'); await waitStatus('pending'); await waitStatus('held');
    check(/85 credits/.test(await page.locator('[data-eng-render-btn]').innerText()), `${width}: edited board gets its own held quote`);
    await page.click('[data-eng-render-btn]'); await waitStatus('pending'); await waitStatus('rendering');
    check(posts.at(-1).shown_total === 85 && /85 credits/.test(proofs.at(-1).description), `${width}: new passcode prompt approves shown price`);
    check(/Submitted/.test(await page.locator('[data-eng-render-status]').innerText()), `${width}: submitted progress is plain`);
    await waitStatus('ready');
    check(await page.locator('[data-eng-render-btn]').isEnabled(), `${width}: ready enables next render`);
    // Persisted failure is shown as failure rather than a stuck spinner.
    record = { ...record, status: 'failed', submission_state: 'failed', hold: null,
      failure: { message: 'Higgsfield could not be reached. Nothing was sent.' } };
    await mount(); await waitStatus('failed');
    check(/Nothing was sent/.test(await page.locator('[data-eng-failure]').innerText()), `${width}: failure survives reload`);
    record = { ...record, status: 'held', submission_state: 'held', failure: null,
      hold: { kind: 'submit_uncertain', message: 'The engine may have accepted this job; check its dashboard before retrying.' } };
    const before = posts.length;
    await mount(false); await waitStatus('held');
    check(await page.locator('[data-eng-render-btn]').isDisabled() && posts.length === before, `${width}: uncertain submission never offers a paid retry`);
    await ctx.close();
  }
  console.log(`${checks} checks passed`);
} finally { await browser.close(); }
