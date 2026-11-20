#!/usr/bin/env node
// Medium and low findings from the frontend review of the dashboard shell
// (docs/_review/2026-11-16_04_frontend.md, ids FE* in static/index.html). One
// step per finding; each step names the id it pins. Hermetic: the real SPA
// served from memory, no request leaves the page.
import assert from 'node:assert/strict';
import { launch, scenario, runner, pid } from './_send-harness.mjs';

const browser = await launch();
const t = runner('dashboard-review-medlow');

// Put one backlog item on the harness project and show the Backlog tab.
async function openBacklog(page) {
  await page.route(`**/api/project/${pid}/backlog`, route => route.fulfill({ json: [
    { id: 'bl000001', text: 'Old text', status: 'open', priority: 'normal' }] }));
  await page.evaluate(async ({ pid }) => {
    window.saveBacklogText = () => { window.smokeBlurSaves = (window.smokeBlurSaves || 0) + 1; };
    await window.refreshProjectBacklog(pid);
    window.switchModalTab(pid, 'backlog');
  }, { pid });
  await page.waitForSelector('.backlog-text[contenteditable]');
}

try {
  // FE15: backlog text (and a social draft body) is a contenteditable saved on
  // blur. refreshModalById stepped aside for an open menu and for the chat
  // composer, but not for a focused contenteditable, so the rebuild that lands
  // every few seconds while an agent runs replaced the element mid-word.
  await t.step('FE15 a modal rebuild waits while a backlog item is being edited', async () => {
    await scenario(browser, {}, async ({ page, errors }) => {
      await openBacklog(page);
      const field = page.locator('.backlog-text[contenteditable]');
      await field.click();
      await page.keyboard.type(' plus my edit');
      await page.evaluate(() => { document.querySelector('.backlog-text').dataset.smokeNode = 'original'; });
      await page.evaluate(({ pid }) => { refreshModalById(pid); refreshModal(); }, { pid });
      const after = await page.evaluate(() => {
        const el = document.querySelector('.backlog-text');
        return { same: el.dataset.smokeNode === 'original', focused: document.activeElement === el, text: el.textContent };
      });
      assert.equal(after.same, true, 'the element being typed in is not replaced');
      assert.equal(after.focused, true, 'focus and caret stay in the field');
      assert.match(after.text, /plus my edit/, 'what was typed is still there');
      assert.deepEqual(errors(), []);
    });
  });

  await t.step('FE15 the rebuild runs again once the edit is left', async () => {
    await scenario(browser, {}, async ({ page }) => {
      await openBacklog(page);
      await page.locator('.backlog-text[contenteditable]').click();
      await page.evaluate(() => { document.querySelector('.backlog-text').dataset.smokeNode = 'original'; });
      await page.evaluate(() => document.activeElement.blur());
      await page.evaluate(({ pid }) => refreshModalById(pid), { pid });
      assert.equal(await page.evaluate(() => document.querySelector('.backlog-text').dataset.smokeNode || ''), '',
        'with nothing focused the Backlog tab is rebuilt as before');
    });
  });
} finally {
  t.done();
  await browser.close();
}
