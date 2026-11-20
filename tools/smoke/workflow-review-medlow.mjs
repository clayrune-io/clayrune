#!/usr/bin/env node
// Medium and low findings from the frontend review of the workflow builder
// (docs/_review/2026-11-16_04_frontend.md, ids WB*). One step per finding;
// each step names the id it pins. Hermetic: the real SPA served from memory,
// no request leaves the page.
import assert from 'node:assert/strict';
import { launch, scenario, runner, pid } from './_send-harness.mjs';

const browser = await launch();
const t = runner('workflow-review-medlow');

try {
  // WB16: loadWorkflows handed [] to the tab sync when /api/workflows was not
  // OK. The sync saw the mounted workflow missing from the list and replaced
  // the canvas being edited with "No workflows involve this project yet".
  await t.step('WB16 an error reply to /api/workflows does not resync a built Workflows tab as empty', async () => {
    await scenario(browser, {}, async ({ page, errors }) => {
      let status = 200;
      await page.route('**/api/workflows', route => route.fulfill({ status, json: status === 200 ? [] : { error: 'auth_required' } }));
      await page.evaluate(({ pid }) => {
        window.smokeSyncs = [];
        window._wfSyncTabsForProject = (p, list) => {
          window.smokeSyncs.push(list.length);
          document.getElementById('wfb-clayrune-section-' + p).dataset.wfBuilt = '1';
        };
        window.switchModalTab(pid, 'workflows');
      }, { pid });
      await page.waitForFunction(() => window.smokeSyncs.length >= 1);
      status = 401;
      const after = await page.evaluate(async ({ pid }) => {
        const before = window.smokeSyncs.length;
        await window.loadWorkflows(pid);
        return window.smokeSyncs.length - before;
      }, { pid });
      assert.equal(after, 0, 'a refused list is not synced over a built section');
      assert.deepEqual(errors(), []);
    });
  });

  await t.step('WB16 a tab that never loaded still gets its empty state on an error reply', async () => {
    await scenario(browser, {}, async ({ page }) => {
      await page.route('**/api/workflows', route => route.fulfill({ status: 500, json: { error: 'boom' } }));
      await page.evaluate(({ pid }) => {
        window.smokeSyncs = [];
        window._wfSyncTabsForProject = (p, list) => { window.smokeSyncs.push(list.length); };
        window.switchModalTab(pid, 'workflows');
      }, { pid });
      await page.waitForFunction(() => window.smokeSyncs.length >= 1);
      assert.deepEqual(await page.evaluate(() => window.smokeSyncs.slice(0, 1)), [0]);
    });
  });
} finally {
  t.done();
  await browser.close();
}
