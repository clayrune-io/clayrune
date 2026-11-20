#!/usr/bin/env node
// Medium and low findings from the frontend review of the phone layer
// (docs/_review/2026-11-16_04_frontend.md, ids MB* in static/js/mobile.js).
// One step per finding; each step names the id it pins. Hermetic: the real
// SPA served from memory, no request leaves the page.
import assert from 'node:assert/strict';
import { launch, scenario, runner, pid } from './_send-harness.mjs';

const browser = await launch();
const t = runner('mobile-review-medlow');

try {
  // MB1: _syncBottomBarContext snapshotted the bottom bar's innerHTML on its
  // first call and wrote the snapshot back on every return to the dashboard.
  // The unread badge lives in that bar, so each return replaced the live
  // badge with whatever it showed at the first render.
  await t.step('MB1 the Inbox badge keeps its count when a project is closed on a phone', async () => {
    await scenario(browser, {}, async ({ page, errors }) => {
      await page.setViewportSize({ width: 400, height: 800 });
      const r = await page.evaluate(({ pid }) => {
        window._syncBottomBarContext();           // a project is focused: bar hidden
        const hidden = document.getElementById('bottom-tab-bar').classList.contains('mc-bar-hidden');
        const node = document.getElementById('inbox-badge');
        node.textContent = '3'; node.hidden = false;   // what setInboxBadge(3) writes
        closeModalById(pid);
        window._syncBottomBarContext();           // back on the dashboard
        const b = document.getElementById('inbox-badge');
        return { hidden, text: b.textContent, shown: !b.hidden, sameNode: b === node,
          barBack: !document.getElementById('bottom-tab-bar').classList.contains('mc-bar-hidden') };
      }, { pid });
      assert.deepEqual(r, { hidden: true, text: '3', shown: true, sameNode: true, barBack: true });
      assert.deepEqual(errors(), []);
    });
  });
} finally {
  t.done();
  await browser.close();
}
