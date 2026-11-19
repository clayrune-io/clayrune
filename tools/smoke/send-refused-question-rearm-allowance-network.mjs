#!/usr/bin/env node
// Backlog 453fd14f: sendFollowup has three failure exits. The 409/502/500 exit
// calls settleRefusedSend (covered by send-refused-not-shown-as-sent.mjs), which
// re-arms the AskUserQuestion card the send had answered. The other two did not:
//   - allowance path (POST answers {ok:false, allowance_blocked:true}) returned early
//   - network path (fetch throws / is aborted) went straight to its own catch block
// so after either one the agent was still waiting, but the card stayed "answered"
// with its qid pinned in _answeredQuestionIds and was never drawn again.
// One check per path: answer a card, make the send fail that way, assert the form
// is back unanswered and that clicking it sends a second POST.
import assert from 'node:assert/strict';
import { launch, scenario, runner, pid, sid } from './_send-harness.mjs';

const browser = await launch();
const t = runner('send-refused-question-rearm-allowance-network');
const settle = page => page.waitForTimeout(1500);
const questions = [{ header: 'Pick', question: 'Which one?', options: [{ label: 'A' }, { label: 'B' }] }];

const paths = [
  ['allowance refusal', {
    reply: { status: 409, ok: false, allowance_blocked: true,
      error: 'allowance exhausted', refusal_message: 'Out of allowance for this engine' },
  }],
  ['network failure (fetch throws)', { networkError: true }],
];

try {
  for (const [name, failure] of paths) {
    await t.step(`${name} on an answered question re-arms the form`, async () => {
      await scenario(browser, { ...failure, question: { id: 'q1', questions } },
        async ({ page, output, seen, errors }) => {
          await page.evaluate(({ pid, sid, questions }) => {
            agentStatusCache[sid].waitingForQuestion = true;
            agentStatusCache[sid].pendingQuestions = [{ questions, question_id: 'q1' }];
            renderAgentQuestion(sid, pid, questions, 'q1');
          }, { pid, sid, questions });
          const chipA = output.locator('.agent-question[data-qid="q1"] .agent-question-chip[data-label="A"]');
          await chipA.click();
          await page.waitForFunction(() => window.smokeStreams.length > 0);
          await settle(page);
          assert.equal(seen.posts, 1, 'the answer was POSTed once');
          assert.equal(await page.evaluate(({ sid }) => (_answeredQuestionIds[sid] || new Set()).has('q1'), { sid }), false,
            'the qid must leave _answeredQuestionIds so renderAgentQuestion can draw the form again');
          assert.equal(await output.locator('.agent-question.answered').count(), 0, 'no stale answered card');
          assert.equal(await output.locator('.agent-question[data-qid="q1"]:not(.answered)').count(), 1,
            'an unanswered form for q1 is back on screen');
          await chipA.click();
          await page.waitForFunction(() => true);
          await settle(page);
          assert.equal(seen.posts, 2, 'the re-armed card can be answered again (second POST)');
          assert.deepEqual(errors(), []);
        });
    });
  }
} finally {
  t.done();
  await browser.close();
}
