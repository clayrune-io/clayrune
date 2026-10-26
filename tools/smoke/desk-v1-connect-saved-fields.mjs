// MC-1113: real Setup field adapter and wizard, mocked metadata/sign-in/Save only.
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { chromium } from 'playwright';
import { makeServer, newPage, toSetup, toReview, X_NEW } from './desk-v1-connect-api-step.mjs';

const shots = resolve(import.meta.dirname, '../../_scratch/mc1113');
mkdirSync(shots, { recursive: true });
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    const srv = makeServer();
    srv.secrets = [{ name: 'x_app', scope: 'global', kind: 'password' },
      { name: 'other-project', scope: 'private-project', kind: 'password' },
      { name: 'second_app', scope: 'global', kind: 'password' }];
    const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height: width === 390 ? 844 : 900 });
    await toSetup(page, 'x.com', X_NEW);
    const field = (key, attr) => `[data-cfs-field="${key}"] [data-cfs-${attr}]`;
    await page.fill('[data-cfa-field="client_id"]', 'typed-value-to-clear');
    await page.selectOption(field('client_id', 'mode'), 'saved');
    assert.equal(await page.inputValue('[data-cfa-field="client_id"]'), '');
    await page.waitForSelector(field('client_id', 'name') + ' option[value="x_app"]', { state: 'attached' });
    assert.equal(await page.locator(field('client_id', 'name') + ' option[value="other-project"]').count(), 0);
    await page.selectOption(field('client_id', 'name'), 'x_app');
    await page.selectOption(field('client_id', 'part'), 'username');
    await page.selectOption(field('client_secret', 'mode'), 'saved');
    await page.selectOption(field('client_secret', 'name'), 'x_app');
    await page.selectOption(field('client_secret', 'part'), 'value');
    assert.deepEqual(await page.evaluate(() => window.DeskV1ConnectAdapter.read().fields), {
      client_id: { name: 'x_app', part: 'username' }, client_secret: { name: 'x_app', part: 'value' }, allow_unattended: true,
    });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: resolve(shots, `saved_fields_${width}.png`) });
    await page.click('[data-cfw-primary]');
    await page.waitForSelector('[data-cfh-start]');
    // Stub the passcode UX only, preserving humanProofFetch's request body contract.
    await page.evaluate(() => {
      window.humanProofFetch = async (url, init) => {
        const body = JSON.parse(init.body);
        const r = await fetch(url, { method: init.method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...body, passcode: 'right-passcode' }) });
        return { ok: r.ok, status: r.status, body: await r.json() };
      };
      window.openBrowserPane = async () => {};
    });
    await page.click('[data-cfh-start]');
    await page.waitForSelector('[data-cfh-state="held"]');
    const start = srv.log.find((r) => r.path.endsWith('/start-held'));
    assert.deepEqual(start.body.hold, { client_id: { name: 'x_app', part: 'username' }, client_secret: { name: 'x_app', part: 'value' } });
    await page.click('[data-api-back-fields]');
    await page.selectOption(field('client_id', 'part'), 'value');
    await page.selectOption(field('client_id', 'part'), 'username');
    await page.click('[data-cfw-primary]');
    await page.waitForSelector('[data-cfh-state="failed"]');
    assert.match(await page.textContent('[data-cfh-state="failed"]'), /changed the app/);
    assert(srv.log.some((r) => r.path.endsWith('/cancel')));
    await page.click('[data-cfh-start]');
    await page.waitForSelector('[data-cfh-state="held"]');
    await toReview(page);
    const summary = await page.evaluate(() => window.DeskV1ConnectApiStep.reviewHTML());
    assert.match(summary, /x_app/);
    assert.match(summary, /username or key ID/);
    const result = await page.evaluate(() => window.DeskV1ConnectApiStep.commit());
    assert.equal(result.ok, true);
    const saved = srv.log.find((r) => r.path.endsWith('/commit'));
    assert.deepEqual(saved.body.draft.fields.client_id, { name: 'x_app', part: 'username' });
    assert.deepEqual(saved.body.draft.fields.client_secret, { name: 'x_app', part: 'value' });
    assert.deepEqual(pageErrors, []);
    await ctx.close();
    console.log(`PASS saved-field Setup, metadata scope, held invalidation, review and Save at ${width}px`);
  }
} finally { await browser.close(); }
