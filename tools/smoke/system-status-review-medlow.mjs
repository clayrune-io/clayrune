#!/usr/bin/env node
// Medium and low findings from the frontend review of the system status
// module (docs/_review/2026-11-16_04_frontend.md, ids SS*). One step per
// finding; each step names the id it pins. Hermetic: the real SPA served
// from memory, no request leaves the page.
import assert from 'node:assert/strict';
import { launch, scenario, runner } from './_send-harness.mjs';

const real = await launch();
// The module's 60 s tick is a bare setInterval at load. Keep every callback
// registered at that delay so a step can fire the tick by hand.
const browser = {
  newContext: async (o) => {
    const ctx = await real.newContext(o);
    await ctx.addInitScript(() => {
      window.smokeMinuteTicks = [];
      const realSetInterval = window.setInterval;
      window.setInterval = (fn, ms, ...rest) => {
        if (ms === 60000 && typeof fn === 'function') window.smokeMinuteTicks.push(fn);
        return realSetInterval(fn, ms, ...rest);
      };
    });
    return ctx;
  },
};
const t = runner('system-status-review-medlow');
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const BREAKDOWN = {
  provider: 'claude', window_kind: '5h', window_scope: 'all',
  range_start: '2026-09-28T15:00:00+00:00', range_end: '2026-09-28T20:00:00+00:00',
  coverage_begins: '2026-08-01T00:00:00+00:00', empty_state: null,
  totals: {
    session_count: 3,
    tokens: { input_fresh: 1000, input_cache_write: 0, input_cache_read: 0, input_processed_total: 12000, output_tokens: 4000 },
    loc: { added: 120, deleted: 30 }, loc_unavailable_count: 1, token_coverage_unavailable_count: 0,
  },
  rankings: {
    dimension: 'character',
    rows: [{ label: 'proj-a', input_processed_total: 8000, output_tokens: 2500, added: 100, session_count: 2 }],
    unknown_count: 0, missing_data_count: 0,
  },
  dimension: 'project', sort_by: 'input',
  tokens_per_point: { status: 'ok', median: 50000, p10: 40000, p90: 60000, sample_count: 8 },
  bar_change: { status: 'ok', delta_pp: 10, earliest: '2026-09-28T15:00:00+00:00', latest: '2026-09-28T19:55:00+00:00' },
  segmented_bar: { status: 'ok', bar_change_pp: 10, estimated_pp: 6, unattributed_pp: 4, range_pp: [5, 7] },
};
const WINDOWS = { windows: [], coverage_begins: '2026-08-01T00:00:00+00:00' };
const USAGE = {
  available: true, top_models: [],
  provider_weekly_usage: {
    claude: { utilization: 41, resets_at: '2026-10-03T19:20:26+00:00' },
    codex: { utilization: 12, resets_at: '2026-10-03T19:20:26+00:00' },
  },
};

// Answer the usage routes and count the two expensive ones.
async function usageRoutes(page, { delay = 0 } = {}) {
  const calls = { breakdown: [], windows: 0 };
  await page.route('**/api/system/usage/breakdown**', async (route) => {
    calls.breakdown.push(new URL(route.request().url()).search);
    if (delay) await sleep(delay);
    return route.fulfill({ json: BREAKDOWN });
  });
  await page.route('**/api/system/usage/windows**', (route) => {
    calls.windows++;
    return route.fulfill({ json: WINDOWS });
  });
  await page.route('**/api/system/usage', route => route.fulfill({ json: USAGE }));
  await page.route('**/api/system/status', route => route.fulfill({ json: { captured_at: null } }));
  await page.route('**/api/system/status/refresh', route => route.fulfill({ json: { captured_at: null } }));
  return calls;
}
const minuteTick = (page) => page.evaluate(() => { window.smokeMinuteTicks.forEach(fn => fn()); });

try {
  // SS1: the Breakdown moved out of the popover's Usage tab into its own
  // report modal, but three callers still fetched it (a multi-second server
  // compute) for a popover that no longer draws it.
  await t.step('SS1 opening the status popover on its Usage tab does not fetch the Breakdown', async () => {
    await scenario(browser, {}, async ({ page }) => {
      const calls = await usageRoutes(page);
      await page.evaluate(() => {
        window._sysStatusSwitchTab('usage');
        window.toggleSysStatusPopover();
      });
      await sleep(300);
      assert.equal(calls.breakdown.length, 0, `breakdown fetched: ${JSON.stringify(calls.breakdown)}`);
      assert.equal(calls.windows, 0, 'windows fetched');
    });
  });

  await t.step('SS1 the popover Refresh on the Usage tab does not fetch the Breakdown', async () => {
    await scenario(browser, {}, async ({ page }) => {
      const calls = await usageRoutes(page);
      await page.evaluate(async () => {
        window._sysStatusSwitchTab('usage');
        await window.refreshSystemStatus();
      });
      await sleep(300);
      assert.equal(calls.breakdown.length, 0, `breakdown fetched: ${JSON.stringify(calls.breakdown)}`);
      assert.equal(calls.windows, 0, 'windows fetched');
    });
  });

  await t.step('SS1 the 60 s tick stops fetching the Breakdown once the report is closed', async () => {
    await scenario(browser, {}, async ({ page }) => {
      const calls = await usageRoutes(page);
      await page.evaluate(() => window.openUsageReport());
      await page.waitForSelector('#usage-report-surface .ub-controls');
      await sleep(200);
      assert.equal(calls.breakdown.length, 1, 'the report itself fetches once on open');
      // Control: while the report is open the tick keeps it current.
      await minuteTick(page);
      await sleep(200);
      assert.equal(calls.breakdown.length, 2, 'an open report is refreshed by the tick');
      await page.evaluate(() => window.closeModalById('__usage_report'));
      await minuteTick(page);
      await sleep(200);
      assert.equal(calls.breakdown.length, 2, 'the tick fetched the Breakdown with no report open');
    });
  });

  // SS1, visible half: every re-render replaced the report's markup, so a
  // dropdown the user had open closed under the pointer.
  await t.step('SS1 a re-render that changes nothing leaves the report controls in place', async () => {
    await scenario(browser, {}, async ({ page }) => {
      await usageRoutes(page);
      await page.evaluate(() => window.openUsageReport());
      await page.waitForSelector('#usage-report-surface .ub-controls');
      await sleep(200);
      const kept = await page.evaluate(() => {
        const sel = document.querySelector('#usage-report-surface select');
        window._rerenderSysStatusSurfaces();
        return sel.isConnected;
      });
      assert.equal(kept, true, 'the select was rebuilt by a re-render that changed nothing');
    });
  });

} finally {
  t.done();
  await real.close();
}
