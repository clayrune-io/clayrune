#!/usr/bin/env node
// Medium and low findings from the frontend review of the chat panel
// (docs/_review/2026-11-16_04_frontend.md, ids CV*). One step per finding; each
// step names the id it pins. Hermetic: the real SPA served from memory, no live
// agent, no request leaves the page.
import assert from 'node:assert/strict';
import { launch, scenario, runner, pid, sid } from './_send-harness.mjs';

const browser = await launch();
const t = runner('chat-review-medlow');

try {
  // CV6: switchChatModel awaited the POST and never looked at it, so a 404
  // "session not found" or a 400 "invalid model id" still toasted "Model
  // pinned" and left the optimistic pin on the pill; the next message then ran
  // on the default model.
  await t.step('CV6 a refused model pin is rolled back and says why', async () => {
    await scenario(browser, {}, async ({ page, errors }) => {
      await page.route('**/agent/*/model', route =>
        route.fulfill({ status: 404, json: { error: 'session not found' } }));
      const after = await page.evaluate(async ({ pid, sid }) => {
        agentStatusCache[sid].pinnedModel = 'claude-old-pin';
        await window.switchChatModel(pid, sid, 'claude-new-pin');
        return agentStatusCache[sid].pinnedModel;
      }, { pid, sid });
      const toasts = await page.evaluate(() => smokeToasts);
      assert.equal(after, 'claude-old-pin', 'the optimistic pin is rolled back to what the server still holds');
      assert.ok(!toasts.some(x => /Model pinned|Following the default/.test(x)),
        `no success toast on a refused request, got ${JSON.stringify(toasts)}`);
      assert.ok(toasts.some(x => /session not found/.test(x)), `toast carries the server's reason, got ${JSON.stringify(toasts)}`);
      assert.deepEqual(errors(), []);
    });
  });

  await t.step('CV6 an accepted model pin still toasts success and keeps the pin', async () => {
    await scenario(browser, {}, async ({ page }) => {
      await page.route('**/agent/*/model', route => route.fulfill({ json: { ok: true } }));
      const after = await page.evaluate(async ({ pid, sid }) => {
        await window.switchChatModel(pid, sid, 'claude-new-pin');
        return agentStatusCache[sid].pinnedModel;
      }, { pid, sid });
      const toasts = await page.evaluate(() => smokeToasts);
      assert.equal(after, 'claude-new-pin');
      assert.ok(toasts.some(x => /Model pinned/.test(x)), `success toast, got ${JSON.stringify(toasts)}`);
    });
  });
} finally {
  t.done();
  await browser.close();
}
