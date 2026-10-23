import assert from 'node:assert/strict';

// Hold A at the network boundary until B (or a reopened A) has rendered.
export async function checkProjectPreferenceRaces(page, fixtureView) {
  for (const outcome of ['success', 'error', 'dismiss', 'reopen']) {
    const a = `race_a_${outcome}`, b = `race_b_${outcome}`;
    await page.evaluate(({a, b}) => {
      allProjects.push({id: a, name: 'Delayed A'}, {id: b, name: 'Current B'});
      window.EnginePreferences.invalidate();
    }, {a, b});
    let first = true, release;
    const held = new Promise(resolve => { release = resolve; });
    const pattern = '**/api/engine/preferences?project_id=race_*';
    const handler = route => {
      const id = new URL(route.request().url()).searchParams.get('project_id');
      if (id === a && first) { first = false; release(route); return; }
      return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(fixtureView(id))});
    };
    await page.route(pattern, handler);
    await page.evaluate(a => { window.__pendingProjectOpen = window.EnginePreferences.openProject(a); }, a);
    const delayed = await held;
    await page.evaluate(() => window.closeAgentSettingsDialog());
    if (outcome !== 'dismiss') {
      await page.evaluate(() => window.EnginePreferences.invalidate());
      await page.evaluate(id => window.EnginePreferences.openProject(id), outcome === 'reopen' ? a : b);
    }
    const before = await page.locator('#agent-settings-box').innerHTML();
    await delayed.fulfill({status: outcome === 'success' || outcome === 'dismiss' ? 200 : 500,
      contentType: 'application/json', body: JSON.stringify(outcome === 'success' || outcome === 'dismiss'
        ? fixtureView(a) : {error: 'Delayed A failed'})});
    await page.evaluate(() => window.__pendingProjectOpen);
    assert.equal(await page.locator('#agent-settings-box').innerHTML(), before, `${outcome}: stale A cannot rewrite the editor`);
    if (outcome === 'dismiss') {
      assert.equal(await page.locator('#agent-settings-overlay').evaluate(el => el.classList.contains('visible')), false);
    } else {
      const current = outcome === 'reopen' ? a : b;
      assert.ok(await page.locator(`#engine-pref-${current}-codex-model`).isVisible());
      assert.match(await page.locator(`#engine-pref-${current}-codex-model`).getAttribute('onchange'), new RegExp(current));
    }
    await page.evaluate(() => window.closeAgentSettingsDialog());
    await page.unroute(pattern, handler);
  }
}
