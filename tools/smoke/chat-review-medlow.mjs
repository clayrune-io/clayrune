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

  // CV10: the rail row's sub-line was built from esc()'d parts and then passed
  // through esc() again, so a latest message with a quote, & or < showed its
  // entities as text.
  await t.step('CV10 the rail sub-line shows the latest message once-escaped', async () => {
    await scenario(browser, {}, async ({ page }) => {
      const sub = await page.evaluate(({ pid }) => {
        const c = conversationsCache[pid][0];
        c.first_user = 'Earlier prompt';
        c.last_user = 'use "strict" & a<b';
        refreshModalById(pid);
        return [...document.querySelectorAll('.conv-sub')].map(e => e.textContent).join('\n');
      }, { pid });
      assert.match(sub, /use "strict" & a<b/, `raw characters on screen, got ${JSON.stringify(sub)}`);
      assert.doesNotMatch(sub, /&quot;|&amp;|&lt;/, 'no entity text on screen');
    });
  });

  // CV11: _composerNoneLabel returned pre-escaped text and the face card
  // escaped it again, so a default persona shown as `R&D` read
  // `Project default (R&amp;D)` on the card and in its tooltip.
  await t.step('CV11 the "Project default" face card names the persona once-escaped', async () => {
    await scenario(browser, {}, async ({ page }) => {
      await page.route('**/api/characters*', route => route.fulfill({ json: [
        { name: 'rnd', scope: 'global', display_name: 'R&D', description: 'x' }] }));
      await page.evaluate(({ pid }) => {
        const p = allProjects.find(x => x.id === pid);
        p.default_character = 'global:rnd';
        window.agentConvNew = window.agentConvNew || {};
        agentConvNew[pid] = true;
        window.reloadCharacters(pid);
      }, { pid });
      const none = page.locator('.ces-person-card.ces-person-none');
      await none.waitFor();
      assert.equal((await none.locator('.ces-person-name').textContent()).trim(), 'Project default (R&D)');
      assert.equal(await none.getAttribute('title'), 'Project default (R&D)');
    });
  });

  // CV12: confirmSweep counted an item as closed unless the fetch itself
  // rejected, so a 404 for an item deleted since the scan was reported closed.
  await t.step('CV12 the sweep toast counts only the items the server closed', async () => {
    await scenario(browser, {}, async ({ page }) => {
      const notes = [];
      await page.route('**/topics/backlog-sweep', route => route.fulfill({ json: { matches: [
        { id: 'bl-kept', text: 'still there', confidence: 'high' },
        { id: 'bl-gone', text: 'deleted since the scan', confidence: 'high' }] } }));
      await page.route('**/backlog/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path.endsWith('/note')) { notes.push(path); return route.fulfill({ json: { ok: true } }); }
        if (path.endsWith('/bl-gone')) return route.fulfill({ status: 404, json: { error: 'not found' } });
        return route.fulfill({ json: { ok: true } });
      });
      await page.evaluate(async ({ pid }) => {
        await window.sweepBacklog(pid);
        await window.confirmSweep(pid);
      }, { pid });
      const toasts = await page.evaluate(() => smokeToasts);
      assert.ok(toasts.some(x => /Closed 1 backlog item from the sweep/.test(x)), `one closed, got ${JSON.stringify(toasts)}`);
      assert.equal(notes.filter(n => n.includes('bl-gone')).length, 0, 'no "closed" note is posted for the refused item');
      assert.equal(notes.filter(n => n.includes('bl-kept')).length, 1);
    });
  });

  // CV13: the chat header fetched the plan file for its title from inside the
  // render, with no in-flight guard and no record of a miss, so a plan with no
  // `# ` heading (or a deleted one, 404) was refetched on every modal rebuild.
  await t.step('CV13 a plan file with no title is fetched once, not on every rebuild', async () => {
    await scenario(browser, {}, async ({ page }) => {
      let hits = 0;
      await page.route('**/agent/plan-file*', route => { hits++; return route.fulfill({ status: 404, json: { error: 'gone' } }); });
      for (let i = 0; i < 4; i++) {
        await page.evaluate(({ sid }) => { agentStatusCache[sid].planFile = '/smoke/plans/no-heading.md'; refreshModal(); }, { sid });
        await page.waitForTimeout(150);
      }
      assert.equal(await page.locator('.btn-plan-file').count(), 1, 'the plan button is drawn');
      assert.equal(hits, 1, 'one title probe for one plan file across four rebuilds');
      // A different plan file for the same chat is a new question.
      await page.evaluate(({ sid }) => { agentStatusCache[sid].planFile = '/smoke/plans/second.md'; refreshModal(); }, { sid });
      await page.waitForTimeout(150);
      assert.equal(hits, 2, 'a changed plan file is probed again');
      // The hit path is unchanged: a heading becomes the button's label.
      await page.route('**/agent/plan-file*', route => route.fulfill({ json: { content: ['# Ship the fix', '', 'body'].join(String.fromCharCode(10)) } }));
      await page.evaluate(({ sid }) => { agentStatusCache[sid].planFile = '/smoke/plans/third.md'; refreshModal(); }, { sid });
      await page.waitForFunction(() => /Ship the fix/.test(document.querySelector('.btn-plan-file')?.textContent || ''));
    });
  });

  // CV18: when the status poll replaces the buffer with a longer server copy,
  // the earlier parts prepended by "Load earlier conversation" are dropped with
  // it, but the loaded count stayed, so the control offered the next older part
  // and the nearest one could not be loaded again. The stream-reset twin
  // (_settleHistoryReplay) already resets the count.
  await t.step('CV18 a buffer replaced by the status poll resets the loaded-earlier count', async () => {
    await scenario(browser, {}, async ({ page }) => {
      const longer = ['> Ron: Earlier prompt', 'Earlier reply', '> Ron: next', 'reply two', 'reply three', 'reply four'];
      await page.route('**/agent/status', route => route.fulfill({ json: { sessions: [
        { session_id: sid, status: 'idle', log_lines: longer }] } }));
      const count = await page.evaluate(async ({ pid, sid }) => {
        agentOutputBuffers[sid] = ['(earlier part)', '> Ron: Earlier prompt', 'Earlier reply'];
        rolloverLoadedCounts[sid] = 1;
        await window.fetchAgentStatus(pid);
        return [agentOutputBuffers[sid].length, rolloverLoadedCounts[sid] || 0];
      }, { pid, sid });
      assert.deepEqual(count, [6, 0], 'buffer replaced by the server copy, and the count of loaded parts with it');
    });
  });

  // CV17: openConversation awaited /reconstruct and then switched to that chat
  // without checking it was still the one the user last asked for. Click a slow
  // finished chat, then a cached one: the second opened at once and the first
  // response then pulled the pane back.
  await t.step('CV17 a late reconstruct reply does not steal the view from the chat opened after it', async () => {
    await scenario(browser, {}, async ({ page }) => {
      await page.route('**/session/mc-slow/reconstruct', async route => {
        await new Promise(r => setTimeout(r, 700));
        return route.fulfill({ json: { task: 'Slow one', started_at: '', claude_session_id: 'cs-slow',
          log_lines: ['> Ron: slow chat', 'slow reply'], log_line_ts: [] } });
      });
      await page.evaluate(({ pid, sid }) => {
        window.openConversation(pid, 'cs-slow', 'mc-slow', false);              // slow, not cached
        setTimeout(() => window.openConversation(pid, 'native-send-harness', sid, true), 60);  // cached
      }, { pid, sid });
      await page.waitForTimeout(1500);
      assert.equal(await page.evaluate(({ pid }) => activeAgentTab[pid], { pid }), sid,
        'the chat clicked last stays on screen');
    });
  });

  await t.step('CV17 a lone reconstruct still opens its chat', async () => {
    await scenario(browser, {}, async ({ page }) => {
      await page.route('**/session/mc-slow/reconstruct', route => route.fulfill({ json: {
        task: 'Slow one', started_at: '', claude_session_id: 'cs-slow', log_lines: ['> Ron: slow chat', 'slow reply'], log_line_ts: [] } }));
      await page.evaluate(({ pid }) => window.openConversation(pid, 'cs-slow', 'mc-slow', false), { pid });
      await page.waitForFunction(({ pid }) => activeAgentTab[pid] === 'mc-slow', { pid });
    });
  });
} finally {
  t.done();
  await browser.close();
}
