#!/usr/bin/env node
/**
 * The Floor must keep polling after a RENDER error, not only a FETCH error.
 *
 * WHY THIS EXISTS
 * ---------------
 * 920b05b fixed the fetch half: refreshFloor()'s catch block now calls
 * _floorSchedulePoll (floor.js:443) before returning. But the timer is still
 * scheduled on the LAST line of the function (floor.js:475), and everything
 * between the fetch and that line — the whole board build, floor.js:447-473 —
 * can throw. refreshFloor is async and NOTHING handles its rejection:
 * openFloor:57 awaits it inside an async fn nobody catches, and the
 * setInterval callback at :498 calls it with no .catch.
 *
 * So a throw during the FIRST render leaves floorTimer null forever. The board
 * never polls again, and — worse than the fetch case — the counts header is
 * written at :447-452 BEFORE the body at :471, so the user sees a truthful
 * "N live · N rooms" above a silently EMPTY body. It does not look broken. It
 * looks like nobody is working.
 *
 * window.avatarHTML (floor.js:128 per figure, :305 per bench card) is the
 * realistic trigger: an unguarded cross-module global, where floor.js:165
 * guards the equivalent _providerModelChoices call with a typeof check.
 *
 * MUST FAIL on any commit where refreshFloor can exit without scheduling.
 * NOTE: tools/smoke/floor-poll-survives-error.mjs PASSES on this bug — it only
 * poisons the fetch. That is why this second test exists.
 *
 * RUN: node tools/smoke/floor-poll-survives-render-error.mjs
 */
import { chromium } from 'playwright';
import { mkdtempSync } from 'node:fs'; import { tmpdir } from 'node:os'; import { join } from 'node:path';

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0; const fail = (m) => { console.error('  ✗ ' + m); bad++; };

const ctx = await chromium.launchPersistentContext(mkdtempSync(join(tmpdir(), 'flrender-')), {
  args: ['--remote-debugging-port=9842'], viewport: { width: 1400, height: 900 },
});
try {
  const p = ctx.pages()[0] || await ctx.newPage();
  let calls = 0;
  await p.route('**/api/floor', (r) => { calls++; return r.continue(); });
  await p.goto('http://localhost:5199/', { waitUntil: 'domcontentloaded' });
  await p.waitForSelector('#projects-col .card', { timeout: 20000 });

  // Break ONE cross-module global the render path calls per figure, then open
  // the board. This is a module-load-order race made deterministic.
  await p.evaluate(() => {
    window.__origAvatarHTML = window.avatarHTML;
    window.avatarHTML = () => { throw new TypeError('avatarHTML is not a function'); };
  });
  await p.evaluate(() => { try { window.openFloor(); } catch (e) {} });
  await p.waitForTimeout(2000);
  const c1 = calls;
  c1 >= 1 ? ok(`board fetched /api/floor once (calls=${c1})`)
          : fail('the board never called /api/floor at all');

  // The silent-empty-board symptom: header claims figures the body never drew.
  const dom = await p.evaluate(() => ({
    counts: document.getElementById('floor-counts')?.textContent || '',
    body: (document.getElementById('floor-body')?.innerText || '').trim(),
  }));
  if (/\d+ live/.test(dom.counts) && !dom.counts.startsWith('0 live') && dom.body === '') {
    fail(`silent empty board: header says "${dom.counts}" over an EMPTY body with no error message`);
  } else {
    ok('no silent-empty-board state (header and body agree, or an error is shown)');
  }

  // Repair the global. From this instant the app is completely healthy — a
  // board that is still alive MUST recover on its own.
  await p.evaluate(() => { window.avatarHTML = window.__origAvatarHTML; });
  await p.waitForTimeout(15000);
  calls > c1
    ? ok(`board kept polling after the render throw (${c1} → ${calls})`)
    : fail(`board FROZE after one render throw (still ${calls} calls) after 15s of a healthy app — refreshFloor exited before _floorSchedulePoll (floor.js:475)`);

  const figs = await p.evaluate(() => document.querySelectorAll('.fl-fig').length);
  const apiFigs = await p.evaluate(async () =>
    ((await (await fetch('/api/floor')).json()).rooms || [])
      .reduce((n, r) => n + r.figures.length, 0));
  figs === apiFigs
    ? ok(`board recovered on its own: ${figs} figures, matching /api/floor`)
    : fail(`board shows ${figs} figures but /api/floor has ${apiFigs} — never recovered`);
} finally {
  await ctx.close();
}
console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
