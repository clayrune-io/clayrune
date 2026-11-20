#!/usr/bin/env node
// Medium and low findings from the frontend review of the browser pane
// (docs/_review/2026-11-16_04_frontend.md, ids BP* in
// static/js/browser-pane.js). One step per finding; each step names the id it
// pins. Hermetic: the real SPA served from memory, no request leaves the page.
// BP3 is pinned in popout-window.mjs, which already boots a pop-out window.
import assert from 'node:assert/strict';
import { launch, scenario, runner, pid } from './_send-harness.mjs';

const browser = await launch();
const t = runner('browser-pane-review-medlow');

try {
  // BP2: openBrowserPane detaches the old view, awaits a status or launch
  // request, then builds #mc-browser-pane. Nothing after the await checked the
  // call was still the newest, so two overlapping calls each appended a pane
  // and the second overwrote the first one's stream handle without closing it.
  await t.step('BP2 two overlapping opens leave one pane and one stream', async () => {
    await scenario(browser, {}, async ({ page, errors }) => {
      let streams = 0;
      await page.route('**/browser/status', async route => {
        await new Promise(r => setTimeout(r, 150));
        await route.fulfill({ json: { sessions: [{ session_id: 'bp-smoke', status: 'running', url: 'https://example.test/' }] } });
      });
      await page.route('**/api/browser/stream**', route => { streams++; return route.fulfill({ contentType: 'text/event-stream', body: ': hold\n\n' }); });
      await page.route('**/api/browser/input', route => route.fulfill({ json: { ok: true } }));
      const r = await page.evaluate(async ({ pid }) => {
        const a = window.openBrowserPane(null, pid, 'bp-smoke');
        const b = window.openBrowserPane(null, pid, 'bp-smoke');
        await Promise.all([a, b]);
        await new Promise(r => setTimeout(r, 300));
        return { panes: document.querySelectorAll('[id="mc-browser-pane"]').length };
      }, { pid });
      assert.equal(r.panes, 1, 'one pane on screen');
      assert.ok(streams <= 1, `one stream opened (got ${streams})`);
      await page.evaluate(() => window.closeBrowserPane && document.querySelectorAll('[id="mc-browser-pane"]').forEach(n => n.remove()));
      assert.deepEqual(errors(), []);
    });
  });
} finally {
  t.done();
  await browser.close();
}
