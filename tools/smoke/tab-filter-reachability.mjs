#!/usr/bin/env node
/**
 * Confirm-then-fix: the project modal's "Filter..." box for Agent Log,
 * Documents and Activity was rendered inside .modal-tab-bar, which is
 * display:none !important at every breakpoint (those tabs are reached via
 * the menu dropdown, not the hidden bar) — so the filter existed in the DOM
 * but was permanently unreachable, on desktop and mobile alike.
 *
 * Fix moved each tab's filter input into that tab's own toolbar (same
 * pattern as the Backlog tab's .backlog-search, MC-955), scoped via
 * ".modal-tab-content.active .modal-tab-search" so applyTabFilter/
 * clearTabSearch always touch the ON-SCREEN input, not whichever tab
 * happens to render first in the DOM.
 *
 * Drives the REAL running app (localhost:5199) at desktop (1440x900) and
 * mobile (390x844).
 *
 * RUN: node tab-filter-reachability.mjs
 */
import { chromium } from 'playwright';
import { mkdtempSync } from 'node:fs'; import { tmpdir } from 'node:os'; import { join } from 'node:path';

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

async function openProjectAndGetId(page) {
  await page.goto('http://localhost:5199/', { waitUntil: 'domcontentloaded' });
  // Desktop renders `.card` tiles into #projects-col; mobile (<=960px)
  // replaces the grid with `.mc-chat-row` rows instead — same container,
  // disjoint child class, never both at once (see floor.js HIRE_TILE_SEL).
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 20000 });
  for (const c of await page.$$('#projects-col .card, #projects-col .mc-chat-row')) {
    if ((await c.innerText()).toLowerCase().includes('mission control')) { await c.click(); break; }
  }
  await page.waitForTimeout(600);
  return page.evaluate(() => {
    const el = document.querySelector('[id^="modal-menu-"]');
    return el ? el.id.replace('modal-menu-', '') : null;
  });
}

async function checkTab(page, pid, tab, entrySelector, label) {
  await page.evaluate((args) => window._mcMenuSwitchTab(args.p, args.t), { p: pid, t: tab });
  await page.waitForTimeout(400);

  const input = page.locator('.modal-tab-content.active .modal-tab-search input');
  const count = await input.count();
  if (count !== 1) { fail(`${label}: expected exactly 1 filter input, found ${count}`); return; }

  const box = await input.boundingBox();
  const display = await input.evaluate((el) => getComputedStyle(el).display);
  if (display === 'none' || !box || box.width === 0 || box.height === 0) {
    fail(`${label}: filter input not visible (display=${display}, box=${JSON.stringify(box)})`);
    return;
  }
  ok(`${label}: filter visible (display=${display}, box=${Math.round(box.width)}x${Math.round(box.height)})`);

  const total = await page.$$eval(entrySelector, (e) => e.length);
  if (total === 0) { ok(`${label}: no entries to filter (presence already verified)`); return; }

  await input.fill('zzzznomatchzzzz');
  await page.waitForTimeout(300);
  const visibleAfter = await page.$$eval(entrySelector, (e) => e.filter((x) => getComputedStyle(x).display !== 'none').length);
  visibleAfter === 0 ? ok(`${label}: nonsense query hides all ${total} entries`) : fail(`${label}: expected 0 visible after filtering, got ${visibleAfter}`);

  await input.fill('');
  await page.waitForTimeout(300);
  const visibleCleared = await page.$$eval(entrySelector, (e) => e.filter((x) => getComputedStyle(x).display !== 'none').length);
  visibleCleared === total ? ok(`${label}: clearing restores all ${total} entries`) : fail(`${label}: expected ${total} after clear, got ${visibleCleared}`);
}

async function runAtViewport(width, height, label) {
  console.log(`\n-- ${label} (${width}x${height}) --`);
  const ctx = await chromium.launchPersistentContext(mkdtempSync(join(tmpdir(), 'tabfilter-')), {
    args: ['--remote-debugging-port=0'], viewport: { width, height },
  });
  try {
    const page = ctx.pages()[0] || await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

    const pid = await openProjectAndGetId(page);
    pid ? ok(`opened project "${pid}"`) : fail('could not resolve the open project id');
    if (!pid) return;

    await checkTab(page, pid, 'agent-log', '.agent-log-entry', 'agent-log');
    await checkTab(page, pid, 'activity', '.log-entry', 'activity');
    await checkTab(page, pid, 'documents', '.plan-history-card', 'documents');

    pageErrors.length === 0 ? ok('no uncaught JS errors')
      : pageErrors.forEach((e) => fail('uncaught: ' + e));
  } finally {
    await ctx.close();
  }
}

await runAtViewport(1440, 900, 'desktop');
await runAtViewport(390, 844, 'mobile');

console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
