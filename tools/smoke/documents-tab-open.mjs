#!/usr/bin/env node
/**
 * MC-939 regression: clicking a document in the Documents tab must OPEN it.
 *
 * WHY THIS EXISTS
 * ---------------
 * The Plans→Documents rename left one identifier behind: the viewer's filename
 * line still read `planPath`, a variable that no longer existed in the renamed
 * `openDocFromHistory(docPath, …)`. Every click threw
 * `ReferenceError: planPath is not defined` — the fetch had already succeeded,
 * so the backend looked healthy and the row simply did nothing. Nothing in the
 * suite exercised the click path, only the endpoint.
 *
 * Drives the REAL running app (localhost:5199) through the real tab menu, so
 * the onclick attribute wiring is exercised as shipped.
 *
 * RUN: node documents-tab-open.mjs
 */
import { chromium } from 'playwright';
import { mkdtempSync } from 'node:fs'; import { tmpdir } from 'node:os'; import { join } from 'node:path';

const ok = (m) => console.log('  \u2713 ' + m);
let bad = 0;
const fail = (m) => { console.error('  \u2717 ' + m); bad++; };

const ctx = await chromium.launchPersistentContext(mkdtempSync(join(tmpdir(), 'mc939-')), {
  args: ['--remote-debugging-port=9790'], viewport: { width: 1500, height: 950 },
});
try {
  const page = ctx.pages()[0] || await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.goto('http://localhost:5199/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 20000 });

  let pid = null;
  for (const c of await page.$$('#projects-col .card')) {
    if ((await c.innerText()).toLowerCase().includes('mission control')) { await c.click(); break; }
  }
  pid = await page.evaluate(() => {
    const el = document.querySelector('[id^="documents-list-"]');
    return el ? el.id.replace('documents-list-', '')
              : (document.querySelector('.modal-menu-dropdown')?.id || '').replace('modal-menu-', '');
  });
  pid ? ok(`opened project "${pid}"`) : fail('could not resolve the open project id');

  // Switch to Documents through the real menu path, not by calling the renderer.
  await page.evaluate((p) => window._mcMenuSwitchTab(p, 'documents'), pid);
  await page.waitForSelector('.plan-history-card', { timeout: 20000 });
  const cards = await page.$$eval('.plan-history-card', (e) => e.length);
  cards > 0 ? ok(`Documents tab rendered ${cards} rows`) : fail('no document rows rendered');

  const kinds = await page.$$eval('.doc-kind-badge', (e) => [...new Set(e.map(x => x.textContent.trim()))]);
  kinds.includes('DOC') ? ok(`rows carry kind badges (${kinds.join(', ')})`)
    : fail(`expected a DOC badge, saw ${JSON.stringify(kinds)}`);

  const before = await page.$$eval('.plan-viewer-content', (e) => e.length);
  pageErrors.length = 0;
  await page.locator('.plan-card-body').first().click();
  await page.waitForTimeout(2500);
  const after = await page.$$eval('.plan-viewer-content', (e) => e.length);

  after > before ? ok('clicking a document OPENS the viewer')
    : fail(`viewer did not open (${before} \u2192 ${after})`);
  pageErrors.length === 0 ? ok('no uncaught error on the click path')
    : pageErrors.forEach((e) => fail('uncaught on click: ' + e));

  const body = await page.$$eval('.plan-viewer-content', (els) =>
    els.length ? els[els.length - 1].innerText.slice(0, 400) : '');
  body.trim().length > 40 ? ok('viewer rendered real content')
    : fail(`viewer opened but is empty: ${JSON.stringify(body)}`);
  !/Failed to load document/i.test(body) ? ok('content loaded (not the failure placeholder)')
    : fail('viewer shows "Failed to load document."');
} finally {
  await ctx.close();
}
console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
