#!/usr/bin/env node
// Medium and low findings from the frontend review of window dragging
// (docs/_review/2026-11-16_04_frontend.md, ids IN* in
// static/js/interactions.js). One step per finding; each step names the id it
// pins. Hermetic: the real SPA served from memory, no request leaves the page.
import assert from 'node:assert/strict';
import { launch, scenario, runner, pid } from './_send-harness.mjs';

const browser = await launch();
const t = runner('interactions-review-medlow');

// A point on the modal header that is not on a control, so a press there
// starts a window drag.
async function headerGrip(page) {
  return page.evaluate(({ pid }) => {
    const win = document.querySelector(`.modal-window[data-modal-id="${pid}"]`);
    const r = win.querySelector('.modal-header').getBoundingClientRect();
    const y = Math.round(r.top + r.height / 2);
    for (let x = Math.round(r.left + 8); x < r.right - 8; x += 6) {
      const el = document.elementFromPoint(x, y);
      if (el && el.closest('.modal-header') && !el.closest('input, button, select, textarea, a, [contenteditable]')) return { x, y };
    }
    return null;
  }, { pid });
}

try {
  // IN1: the drag tear-off cleared the snap but left is-maximized on the
  // window, mc-modal-maximized on body and the header button on "Restore
  // down"; unSnap and the resize path, which it says it mirrors, clear all three.
  await t.step('IN1 dragging a maximized window free clears every maximized marker', async () => {
    await scenario(browser, {}, async ({ page, errors }) => {
      await page.locator(`.modal-window[data-modal-id="${pid}"] .modal-maximize`).click();
      await page.waitForFunction(({ pid }) =>
        document.querySelector(`.modal-window[data-modal-id="${pid}"]`).classList.contains('is-maximized'), { pid });
      const before = await page.evaluate(() => document.body.classList.contains('mc-modal-maximized'));
      assert.equal(before, true, 'maximizing marks body');
      const g = await headerGrip(page);
      assert.ok(g, 'found a grip point on the header');
      await page.mouse.move(g.x, g.y);
      await page.mouse.down();
      await page.mouse.move(g.x + 10, g.y + 120, { steps: 6 });
      await page.mouse.move(g.x - 200, g.y + 260, { steps: 6 });
      await page.mouse.up();
      const after = await page.evaluate(({ pid }) => {
        const win = document.querySelector(`.modal-window[data-modal-id="${pid}"]`);
        const btn = win.querySelector('.modal-maximize');
        return { maximized: win.classList.contains('is-maximized'), snapped: win.classList.contains('is-snapped'),
          body: document.body.classList.contains('mc-modal-maximized'), title: btn ? btn.title : '' };
      }, { pid });
      assert.equal(after.snapped, false, 'the window is free');
      assert.equal(after.maximized, false, 'is-maximized is gone with the snap');
      assert.equal(after.body, false, 'body no longer raises the modal layer over the sidebar');
      assert.doesNotMatch(after.title, /restore/i, 'the header button offers Maximize again');
      assert.deepEqual(errors(), []);
    });
  });
} finally {
  t.done();
  await browser.close();
}
