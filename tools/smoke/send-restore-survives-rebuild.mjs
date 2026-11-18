#!/usr/bin/env node
// FE14 (docs/_review/2026-11-16_04_frontend.md): sendFollowup clears the composer
// and drops its textareaValues entry before the POST. When the send fails it
// puts the text back with a plain `inp.value = message`, which fires no `input`
// event, so textareaValues[id] stays unset. The next modal rebuild treats the
// cache as the source of truth ("if (_cachedVal === undefined) rec.node.value =
// ''") and the restored message vanishes. send-timeout.mjs asserts the restore
// but never rebuilds the modal afterwards, so it passed with the bug present.
import assert from 'node:assert/strict';
import { launch, scenario, send, runner, sid, message } from './_send-harness.mjs';

const browser = await launch();
const t = runner('send-restore-survives-rebuild');

try {
  await t.step('network error: restored text survives refreshModal()', async () => {
    await scenario(browser, { networkError: true }, async ({ page, input }) => {
      await send(page);
      await page.waitForFunction(({ sid, message }) => document.getElementById(`agent-followup-${sid}`).value === message,
        { sid, message });
      await page.evaluate(() => refreshModal());
      await page.waitForTimeout(300);
      assert.equal(await input.inputValue(), message, 'composer kept the restored message after a rebuild');
      assert.equal(await page.evaluate(({ sid }) => textareaValues[`agent-followup-${sid}`], { sid }), message,
        'textareaValues (the rebuild source of truth) carries the restored message');
    });
  });
  await t.step('allowance block: restored text survives refreshModal()', async () => {
    await scenario(browser, { reply: { ok: false, allowance_blocked: true, error: 'No fallback' } }, async ({ page, input }) => {
      await send(page);
      await page.waitForFunction(({ sid, message }) => document.getElementById(`agent-followup-${sid}`).value === message,
        { sid, message });
      await page.evaluate(() => refreshModal());
      await page.waitForTimeout(300);
      assert.equal(await input.inputValue(), message, 'composer kept the restored message after a rebuild');
    });
  });
  await t.step('control: a send that succeeds still leaves the composer empty after a rebuild', async () => {
    await scenario(browser, { reply: { ok: true, session_id: sid } }, async ({ page, input }) => {
      await send(page);
      await page.waitForTimeout(800);
      await page.evaluate(() => refreshModal());
      await page.waitForTimeout(300);
      assert.equal(await input.inputValue(), '');
    });
  });
} finally {
  t.done();
  await browser.close();
}
