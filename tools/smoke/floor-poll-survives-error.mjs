#!/usr/bin/env node
/**
 * The Floor must keep polling after a failed fetch.
 *
 * WHY THIS EXISTS
 * ---------------
 * refreshFloor() rendered an error and `return`ed — before the code that
 * schedules the poll timer. So ONE bad /api/floor response (a server restart,
 * a slow load) left the board frozen forever: it never asked again, and the
 * only recovery was closing and reopening it. Ron reported the Floor as
 * "inconsistent" for a full day; the payload was correct every time we checked
 * it directly, because the board had simply stopped asking.
 *
 * Drives the REAL app and fails the first /api/floor call on purpose.
 *
 * RUN: node floor-poll-survives-error.mjs
 */
import { chromium } from 'playwright';
import { mkdtempSync } from 'node:fs'; import { tmpdir } from 'node:os'; import { join } from 'node:path';

const ok = (m) => console.log('  \u2713 ' + m);
let bad = 0; const fail = (m) => { console.error('  \u2717 ' + m); bad++; };

const ctx = await chromium.launchPersistentContext(mkdtempSync(join(tmpdir(), 'flpoll-')), {
  args: ['--remote-debugging-port=9833'], viewport: { width: 1400, height: 900 },
});
try {
  const p = ctx.pages()[0] || await ctx.newPage();
  let calls = 0;
  await p.route('**/api/floor', (route) => {
    calls++;
    if (calls === 1) return route.abort('failed');   // the poisoned first fetch
    return route.continue();
  });
  await p.goto('http://localhost:5199/', { waitUntil: 'domcontentloaded' });
  await p.waitForSelector('#projects-col .card', { timeout: 20000 });

  await p.evaluate(() => window.openFloor && window.openFloor());
  await p.waitForTimeout(1500);
  calls >= 1 ? ok(`first /api/floor attempted and failed (calls=${calls})`)
             : fail('the board never called /api/floor at all');
  const errShown = await p.evaluate(() =>
    (document.getElementById('floor-body')?.innerText || '').includes('Could not read the floor'));
  errShown ? ok('error state rendered, as designed') : fail('expected the error message after a failed fetch');

  // The whole point: it must ask again without the user reopening anything.
  const before = calls;
  await p.waitForTimeout(9000);
  calls > before ? ok(`board kept polling after the failure (${before} \u2192 ${calls})`)
                 : fail(`board froze after one failed fetch (still ${calls} calls) \u2014 the reported bug`);

  const recovered = await p.evaluate(() =>
    !!document.querySelector('.fl-rooms') ||
    !(document.getElementById('floor-body')?.innerText || '').includes('Could not read the floor'));
  recovered ? ok('board recovered on its own once the endpoint answered')
            : fail('board never recovered despite the endpoint being healthy');
} finally {
  await ctx.close();
}
console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
