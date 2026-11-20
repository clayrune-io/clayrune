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

  // CV8: the status poll took any JSON body. A 401 {error:'auth_required'} from
  // a lapsed passcode session parsed as "no sessions", and the detach pass then
  // marked every idle chat of the project STOPPED and closed its stream.
  await t.step('CV8 an error reply to the status poll does not detach the open chats', async () => {
    await scenario(browser, {}, async ({ page, errors }) => {
      await page.route('**/agent/status', route =>
        route.fulfill({ status: 401, json: { error: 'auth_required' } }));
      const status = await page.evaluate(async ({ pid, sid }) => {
        agentStatusCache[sid].status = 'idle';
        agentHistory.find(h => h.sessionId === sid).status = 'idle';
        await window.fetchAgentStatus(pid);
        return [agentStatusCache[sid].status, agentHistory.find(h => h.sessionId === sid).status];
      }, { pid, sid });
      assert.deepEqual(status, ['idle', 'idle'], 'an auth error is not "the server forgot every session"');
      assert.deepEqual(errors(), []);
    });
  });

  await t.step('CV8 a real reply that no longer lists the session still detaches it', async () => {
    await scenario(browser, {}, async ({ page }) => {
      await page.route('**/agent/status', route => route.fulfill({ json: { sessions: [] } }));
      const status = await page.evaluate(async ({ pid, sid }) => {
        agentStatusCache[sid].status = 'idle';
        agentHistory.find(h => h.sessionId === sid).status = 'idle';
        await window.fetchAgentStatus(pid);
        return agentStatusCache[sid].status;
      }, { pid, sid });
      assert.equal(status, 'stopped', 'the keep-window-open detach is unchanged for a genuine empty list');
    });
  });

  // CV9: 250 lines of fetchAgentStatus ran inside `catch(e) {}`. A TypeError on
  // one session row ended the pass with nothing logged anywhere.
  await t.step('CV9 a throw inside the status poll is logged, not swallowed whole', async () => {
    await scenario(browser, {}, async ({ page }) => {
      const warnings = [];
      page.on('console', m => { if (m.type() === 'warning') warnings.push(m.text()); });
      await page.route('**/agent/status', route => route.fulfill({ json: { sessions: [] } }));
      await page.evaluate(async ({ pid }) => {
        window.updateAgentMailHoldNotices = () => { throw new Error('boom-cv9'); };
        await window.fetchAgentStatus(pid);
      }, { pid });
      assert.ok(warnings.some(w => /agent\/status/.test(w) && /boom-cv9/.test(w)),
        `the failure reaches the console, got ${JSON.stringify(warnings)}`);
    });
  });
} finally {
  t.done();
  await browser.close();
}
