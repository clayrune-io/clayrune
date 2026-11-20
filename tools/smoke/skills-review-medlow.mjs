#!/usr/bin/env node
// Medium and low findings from the frontend review of the skills panel
// (docs/_review/2026-11-16_04_frontend.md, ids SK*). One step per finding;
// each step names the id it pins. Hermetic: the real SPA served from memory,
// no request leaves the page.
import assert from 'node:assert/strict';
import { launch, scenario, runner } from './_send-harness.mjs';

const browser = await launch();
const t = runner('skills-review-medlow');
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

try {
  // SK6: loadAllSkills returned at once while a load was in flight, and read
  // the filter only at its start. Ticking "Include archived" (or picking a
  // project) during a load was dropped: the box stayed ticked over a list
  // that held no archived skill.
  await t.step('SK6 a skills filter change made during a load is applied when the load ends', async () => {
    await scenario(browser, {}, async ({ page }) => {
      const seen = [];
      await page.route('**/api/skills**', async (route) => {
        seen.push(new URL(route.request().url()).search);
        await sleep(250);
        return route.fulfill({ json: [] });
      });
      await page.evaluate(async () => {
        window._allSkillsFilter.project = 'all';
        window._allSkillsFilter.includeArchived = false;
        const first = window.loadAllSkills();
        await new Promise(r => setTimeout(r, 60));
        window._allSkillsFilter.includeArchived = true;      // the checkbox's onchange
        await window.loadAllSkills();
        await first;
      });
      await sleep(700);
      assert.ok(seen.some(q => /include_archived=true/.test(q)),
        `no request carried the archived filter: ${JSON.stringify(seen)}`);
    });
  });

  await t.step('SK6 a personas filter change made during a load is applied when the load ends', async () => {
    await scenario(browser, {}, async ({ page }) => {
      const seen = [];
      await page.route('**/api/characters**', async (route) => {
        seen.push(new URL(route.request().url()).search);
        await sleep(250);
        return route.fulfill({ json: [] });
      });
      await page.evaluate(async () => {
        window._allPersonasFilter.project = 'all';
        const first = window.loadAllPersonas();
        await new Promise(r => setTimeout(r, 60));
        window._allPersonasFilter.project = 'picked_project';   // the select's onchange
        await window.loadAllPersonas();
        await first;
      });
      await sleep(700);
      assert.ok(seen.some(q => /project_id=picked_project/.test(q)),
        `no request asked for the picked project: ${JSON.stringify(seen)}`);
    });
  });

  // SK6, the opposite fault in the Browse dialog: no guard and no sequence
  // check, so a slow search that finished after a later, faster one painted
  // its older results over the newer ones.
  await t.step('SK6 a slow earlier Browse search does not overwrite a later one', async () => {
    await scenario(browser, {}, async ({ page }) => {
      await page.route('**/api/skills/search**', async (route) => {
        const q = new URL(route.request().url()).searchParams.get('q');
        if (q === 'old') await sleep(500);
        return route.fulfill({ json: [{ name: 'result-for-' + q, scope: 'global', description: '', score: 1 }] });
      });
      await page.route('**/api/skills', route => route.fulfill({ json: [] }));
      await page.evaluate(async () => {
        window.openSkillImportBrowse();
        const id = '__skill_import_browse';
        const box = document.querySelector('#sib-query');
        box.value = 'old';
        const slow = window._doSkillImportBrowseSearch(id);
        await new Promise(r => setTimeout(r, 60));
        box.value = 'new';
        await window._doSkillImportBrowseSearch(id);
        await slow;
      });
      const text = await page.locator('#sib-results').innerText();
      assert.match(text, /result-for-new/);
      assert.doesNotMatch(text, /result-for-old/);
    });
  });
} finally {
  t.done();
  await browser.close();
}
