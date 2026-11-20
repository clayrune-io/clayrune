#!/usr/bin/env node
// Medium and low findings from the frontend review of Claydo
// (docs/_review/2026-11-16_04_frontend.md, ids CD*). One step per finding;
// each step names the id it pins. Hermetic: the real SPA served from memory,
// no request leaves the page.
import assert from 'node:assert/strict';
import { launch, scenario, runner } from './_send-harness.mjs';

const browser = await launch();
const t = runner('claydo-review-medlow');

const SESSION = {
  mode: 'character', minimized: false, draft: '',
  history: [
    { role: 'user', text: 'a terse code reviewer' },
    { role: 'assistant', ready: 'character-ready', text:
      'Here is the draft.\n```\n---\nname: reviewer\n'
      + 'description: reviews diffs for bugs\n---\nYou review diffs. Be terse.\n```' },
  ],
};

// Restore a finished character interview and walk to its save panel, the way
// a user does: ready card -> "Open in editor" -> "Save character...".
async function openSavePanel(page, posts) {
  await page.route('**/api/characters', (route) => {
    if (route.request().method() !== 'POST') return route.fulfill({ json: [] });
    const body = JSON.parse(route.request().postData() || '{}');
    posts.push(body);
    if (!body.overwrite) return route.fulfill({ status: 409, json: { error: 'exists' } });
    return route.fulfill({ json: { name: body.name, scope: body.scope } });
  });
  await page.route('**/api/local-auth/status', route => route.fulfill({ json: { configured: true } }));
  await page.route('**/api/agent/providers', route => route.fulfill({ json: { providers: [] } }));
  await page.route('**/api/avatars', route => route.fulfill({ json: { figures: [], prefix: 'fig:' } }));
  // Not a reload: leaving the page saves the session, and with no Claydo
  // window open that save removes the key again.
  await page.evaluate(async (st) => {
    localStorage.setItem('mc_claydo_session', JSON.stringify(st));
    await window.restoreClaydoSession();
  }, SESSION);
  // DOM clicks, as boot-smoke's identity guard does: the harness leaves a
  // project modal open and Claydo's window is not hit-testable behind it.
  const domClick = (sel) => page.evaluate((q) => document.querySelector(q).click(), sel);
  await page.waitForSelector('[data-modal-id="__claydo"] .claydo-ready-card .claydo-ready-btn.accent', { state: 'attached' });
  await domClick('[data-modal-id="__claydo"] .claydo-ready-card .claydo-ready-btn.accent');
  await page.waitForSelector('[data-act="save"]', { state: 'attached' });
  await domClick('[data-act="save"]');
  await page.waitForSelector('.claydo-save-panel #claydo-save-go', { state: 'attached' });
}

// Save, then answer the human-proof passcode prompt the save goes through.
async function pressSave(page, expectedPosts, posts) {
  await page.evaluate(() => document.querySelector('.claydo-save-panel #claydo-save-go').click());
  await page.waitForSelector('[data-modal-id^="__human-proof-"]', { state: 'attached' });
  await page.evaluate(() => {
    const win = document.querySelector('[data-modal-id^="__human-proof-"]');
    const id = win.dataset.modalId;
    document.getElementById(`hp-passcode-${id}`).value = 'smoke-dash-passcode';
    window._hpSubmit(id);
  });
  await page.waitForFunction(() => !document.querySelector('[data-modal-id^="__human-proof-"]'));
  for (let i = 0; i < 40 && posts.length < expectedPosts; i++) await page.waitForTimeout(50);
  await page.waitForTimeout(150);
}

try {
  // CD1: a 409 armed `overwrite` for the whole panel. Nothing disarmed it when
  // the name changed, so the next save replaced a DIFFERENT existing character
  // with no second warning.
  await t.step('CD1 "Overwrite existing" applies only to the name it was armed for', async () => {
    await scenario(browser, {}, async ({ page }) => {
      const posts = [];
      await openSavePanel(page, posts);
      const label = () => page.$eval('.claydo-save-panel #claydo-save-go', el => el.textContent.trim());
      const first = await label();
      await pressSave(page, 1, posts);
      assert.equal(posts.length, 1);
      assert.equal(posts[0].name, 'reviewer');
      assert.equal(posts[0].overwrite, false);
      assert.equal(await label(), 'Overwrite existing', 'the 409 arms the button');

      await page.evaluate(() => {
        const el = document.querySelector('.claydo-save-panel #claydo-save-name');
        el.value = 'auditor';
        el.dispatchEvent(new Event('input', { bubbles: true }));
      });
      assert.equal(await label(), first, 'retyping the name puts the button back');
      await pressSave(page, 2, posts);
      assert.equal(posts[1].name, 'auditor');
      assert.equal(posts[1].overwrite, false, 'a different name is not overwritten on the old warning');
      assert.equal(await label(), 'Overwrite existing', 'the new name gets its own warning');

      await pressSave(page, 3, posts);
      assert.equal(posts[2].name, 'auditor');
      assert.equal(posts[2].overwrite, true, 'saving the same name again still overwrites');
    });
  });
} finally {
  t.done();
  await browser.close();
}
