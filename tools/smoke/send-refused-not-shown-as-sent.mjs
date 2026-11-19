#!/usr/bin/env node
// CV2 (docs/_review/2026-11-16_04_frontend.md): when POST /agent/send answers
// {ok:false} with anything other than allowance_blocked (409 revive refused,
// 502 delivered:false, 500 dispatch failed ...) sendFollowup fell out of its
// failure branch into the success path: no toast, empty composer, and the
// optimistic bubble kept class `agent-echo` so it looked like a delivered
// message. The catch path (network error) already does all three; the two
// failure paths disagreed. Also: an answered AskUserQuestion card stayed
// "answered" with the qid pinned in _answeredQuestionIds, so after a refused
// answer the form could never come back and the agent sat parked.
import assert from 'node:assert/strict';
import { launch, scenario, send, runner, pid, sid, message } from './_send-harness.mjs';

const browser = await launch();
const t = runner('send-refused-not-shown-as-sent');
const settle = page => page.waitForTimeout(1500);

const refusals = [
  ['502 delivered:false', { status: 502, ok: false, delivered: false, error: 'The message did not reach the session' }, /did not reach the session/],
  ['409 revive refused', { status: 409, ok: false, error: 'revive refused' }, /revive refused/],
  ['500 dispatch failed', { status: 500, ok: false, error: 'dispatch failed' }, /dispatch failed/],
];
try {
  for (const [name, reply, toastRe] of refusals) {
    await t.step(`refused send (${name}): echo marked failed, text back in composer, toast shown`, async () => {
      await scenario(browser, { reply }, async ({ page, input, output, seen, errors }) => {
        await send(page);
        await page.waitForFunction(() => window.smokeStreams.length > 0);
        await settle(page);
        assert.equal(seen.posts, 1, 'one POST');
        assert.equal(await output.locator('.agent-echo').count(), 0,
          'the refused message must not keep the delivered-looking agent-echo class');
        assert.equal(await output.locator('.agent-echo-failed').count(), 1, 'echo marked failed');
        assert.match(await output.locator('.agent-echo-failed').innerText(), /not sent|failed|blocked/i);
        assert.equal(await input.inputValue(), message, 'composer restored so the user can retry');
        const toasts = await page.evaluate(() => smokeToasts);
        assert.ok(toasts.some(x => toastRe.test(x)), `toast shows the server error, got ${JSON.stringify(toasts)}`);
        assert.deepEqual(errors(), []);
      });
    });
  }

  const questions = [{ header: 'Pick', question: 'Which one?', options: [{ label: 'A' }, { label: 'B' }] }];
  await t.step('refused answer to a question re-arms the form instead of leaving the agent parked', async () => {
    await scenario(browser, {
      reply: { status: 502, ok: false, delivered: false, error: 'The message did not reach the session' },
      question: { id: 'q1', questions },
    }, async ({ page, output, input }) => {
      await page.evaluate(({ pid, sid, questions }) => {
        agentStatusCache[sid].waitingForQuestion = true;
        agentStatusCache[sid].pendingQuestions = [{ questions, question_id: 'q1' }];
        renderAgentQuestion(sid, pid, questions, 'q1');
      }, { pid, sid, questions });
      await output.locator('.agent-question[data-qid="q1"] .agent-question-chip[data-label="A"]').click();
      await page.waitForFunction(() => window.smokeStreams.length > 0);
      await settle(page);
      assert.equal(await page.evaluate(({ sid }) => (_answeredQuestionIds[sid] || new Set()).has('q1'), { sid }), false,
        'the qid must leave _answeredQuestionIds so renderAgentQuestion can draw the form again');
      assert.equal(await output.locator('.agent-question[data-qid="q1"]:not(.answered)').count(), 1,
        'an unanswered form for q1 is back on screen');
      assert.equal(await output.locator('.agent-question.answered').count(), 0, 'no stale answered card');
      assert.equal(await input.inputValue(), '', 'the machine-built answer directive is not dumped in the composer');
      const toasts = await page.evaluate(() => smokeToasts);
      assert.ok(toasts.some(x => /did not reach the session/.test(x)), `toast shown, got ${JSON.stringify(toasts)}`);
    });
  });
} finally {
  t.done();
  await browser.close();
}
