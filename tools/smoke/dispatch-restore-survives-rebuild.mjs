#!/usr/bin/env node
// FE14 follow-up (docs/_review/2026-11-16_04_frontend.md, FE14): dispatchAgent
// (new conversation) clears the task box and drops its textareaValues entry
// before the POST. When the dispatch fails (server says {ok:false}, or the
// request errors) it put the task back with a plain `input.value = task`, which
// writes no cache entry, and the modal rebuild that follows treats the cache as
// the source of truth: the prompt the user had typed was wiped. Same defect as
// sendFollowup's restore, at resume-preview.js on both failure paths.
import assert from 'node:assert/strict';
import { launch, scenario, runner, pid } from './_send-harness.mjs';

const task = 'Investigate the flaky deploy.\nStart with the logs.';
const browser = await launch();
const t = runner('dispatch-restore-survives-rebuild');

async function failDispatch(options) {
  await scenario(browser, options, async ({ page, seen }) => {
    // The "new conversation" screen with its task box, as a user reaches it.
    await page.evaluate(pid => { agentConvNew[pid] = true; delete activeAgentTab[pid]; refreshModal(); }, pid);
    const box = page.locator(`#agent-task-${pid}`);
    await box.waitFor();
    await box.fill(task);                      // fires `input`, so the cache holds the text
    // The failure path ends in alert(); seeing it means the restore + rebuild ran.
    const alerted = page.waitForEvent('dialog').then(d => d.accept());
    await page.evaluate(pid => { dispatchAgent(pid); }, pid);
    await alerted;
    assert.equal(seen.dispatches, 1, 'the dispatch POST was made once');
    await page.evaluate(() => refreshModal());
    await page.waitForTimeout(300);
    await page.evaluate(() => refreshModal());
    await page.waitForTimeout(300);
    assert.equal(await page.locator(`#agent-task-${pid}`).inputValue(), task,
      'the task box kept the prompt after the failed dispatch and two rebuilds');
    assert.equal(await page.evaluate(pid => textareaValues[`agent-task-${pid}`], pid), task,
      'textareaValues (the rebuild source of truth) carries the restored prompt');
  });
}

try {
  await t.step('dispatch refused ({ok:false}): the prompt survives the rebuild', async () => {
    await failDispatch({ dispatchReply: { ok: false, error: 'Could not start' } });
  });
  await t.step('dispatch request errors: the prompt survives the rebuild', async () => {
    await failDispatch({ dispatchNetworkError: true });
  });
} finally {
  t.done();
  await browser.close();
}
