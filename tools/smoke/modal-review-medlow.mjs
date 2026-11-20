#!/usr/bin/env node
// Medium and low findings from the frontend review of the modal layer
// (docs/_review/2026-11-16_04_frontend.md, ids MM* in
// static/js/modal-manager.js). One step per finding; each step names the id
// it pins. Hermetic: the real SPA served from memory, no request leaves the
// page.
import assert from 'node:assert/strict';
import { launch, scenario, runner, pid } from './_send-harness.mjs';

const browser = await launch();
const t = runner('modal-review-medlow');

try {
  // MM1: the tray chip is created with id 'chip-' + modalId and was looked up
  // with getElementById('chip-' + CSS.escape(modalId)). getElementById takes a
  // literal id, and CSS.escape rewrites a digit-first id ('2048_game' becomes
  // '\32 048_game'), so the chip of such a project was never found again.
  await t.step('MM1 the tray chip of a digit-first project id is removed on restore and on close', async () => {
    await scenario(browser, {}, async ({ page, errors }) => {
      const r = await page.evaluate(({ pid }) => {
        const id = '2048_game';
        allProjects.push({ ...allProjects.find(p => p.id === pid), id, name: '2048 game' });
        openProjectModal(id);
        const chips = () => [...document.querySelectorAll('[id^="chip-"]')].filter(c => c.id === 'chip-' + id).length;
        minimizeModal(id);
        const minimized = chips();
        restoreModal(id);
        const restored = chips();
        minimizeModal(id);
        const again = chips();
        closeModalById(id);
        return { minimized, restored, again, closed: chips() };
      }, { pid });
      assert.deepEqual(r, { minimized: 1, restored: 0, again: 1, closed: 0 });
      assert.deepEqual(errors(), []);
    });
  });

  // MM2: the emoji picker opens over the profile dialog, and pickEmoji then
  // rebuilt the dialog from the saved record: a description, folder, backlog
  // key or persona choice typed but not yet saved was replaced without a word.
  await t.step('MM2 picking an emoji keeps what was typed in the profile dialog', async () => {
    await scenario(browser, {}, async ({ page, errors }) => {
      await page.route(`**/api/project/${pid}`, route => route.fulfill({ json: { ok: true } }));
      const r = await page.evaluate(async ({ pid }) => {
        openProjectProfileDialog(pid);
        document.getElementById('pfd-desc').value = 'typed, not saved';
        document.getElementById('pfd-key').value = 'ZZ';
        document.getElementById('pfd-path').value = '/typed/path';
        editProjectEmoji(pid);
        await pickEmoji('🎯');
        return ['pfd-desc', 'pfd-key', 'pfd-path'].map(id => document.getElementById(id).value);
      }, { pid });
      assert.deepEqual(r, ['typed, not saved', 'ZZ', '/typed/path']);
      assert.deepEqual(errors(), []);
    });
  });
} finally {
  t.done();
  await browser.close();
}
