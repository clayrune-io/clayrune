#!/usr/bin/env node
/**
 * MC-946: "waiting on background task" on the Floor figure and in the chat header.
 *
 * A dispatched child whose spawner callback is held on a genuine background
 * job sits `idle` between turns; the Floor said "idle — between turns" and the
 * chat header said nothing, so it looked like nothing was running. The server
 * now sends `bg_wait` on /api/floor figures and /agent/status sessions.
 *
 * The page is served from THIS checkout's static/ (the live :5199 server may
 * be running other code); /api/floor is stubbed with one figure per case.
 * Everything else passes through to :5199.
 *
 * RUN: node tools/smoke/bg-wait-status.mjs
 */
import { chromium } from 'playwright';
import { readFileSync, existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { mkdtempSync } from 'node:fs'; import { tmpdir } from 'node:os';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const ok = (m) => console.log('  ✓ ' + m);
let bad = 0; const fail = (m) => { console.error('  ✗ ' + m); bad++; };

const MIME = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' };
function fig(id, extra) {
  return {
    session_id: id, claude_session_id: 'c-' + id, state: 'idle', reason: null, activity: '',
    bg_wait: '', task: 'run the suite', character: null, name: 'Tobin', name_from: 'default',
    avatar: '', provider: 'claude', model: 'claude-sonnet-5-5', model_from: 'own',
    started_at: '', age: '1m', subagents: [], ...extra,
  };
}
const FLOOR = {
  counts: { figures: 2, rooms: 1, quiet: 0 }, quiet: [], bench: [], activity_states: true,
  rooms: [{ id: 'p1', name: 'Proj', color: '', emoji: '', figures: [
    fig('held', { bg_wait: 'waiting on background task: npm test' }),
    fig('plain', {}),
  ] }],
};

const ctx = await chromium.launchPersistentContext(mkdtempSync(join(tmpdir(), 'bgwait-')), {
  args: ['--remote-debugging-port=9843'], viewport: { width: 1400, height: 900 },
});
try {
  const p = ctx.pages()[0] || await ctx.newPage();
  const errors = [];
  p.on('pageerror', (e) => errors.push(e.message || String(e)));
  // Serve the page and its scripts from this checkout.
  await p.route(/localhost:5199\/(static\/.*|)(\?.*)?$/, (r) => {
    const u = new URL(r.request().url());
    const rel = u.pathname === '/' ? 'static/index.html' : u.pathname.slice(1);
    const f = join(ROOT, rel);
    if (!existsSync(f)) return r.continue();
    const ext = f.slice(f.lastIndexOf('.'));
    return r.fulfill({ status: 200, body: readFileSync(f), contentType: MIME[ext] || 'application/octet-stream',
                       headers: { 'cache-control': 'no-store' } });
  });
  await p.route('**/api/floor', (r) => r.fulfill({ json: FLOOR }));
  await p.goto('http://localhost:5199/', { waitUntil: 'domcontentloaded' });
  await p.waitForSelector('#projects-col .card', { timeout: 20000 });
  await p.evaluate(() => window.openFloor());
  await p.waitForSelector('.fl-fig', { timeout: 10000 });

  const lines = await p.evaluate(() => Object.fromEntries(
    [...document.querySelectorAll('.fl-fig')].map(el => [el.dataset.flSession, el.querySelector('.fl-act').textContent.trim()])));
  lines.held === 'waiting on background task: npm test'
    ? ok(`Floor figure with a held callback reads "${lines.held}"`)
    : fail(`held figure line is "${lines.held}"`);
  lines.plain === 'idle — between turns'
    ? ok('a figure with nothing held still reads "idle — between turns"')
    : fail(`plain idle figure line is "${lines.plain}"`);

  // Chat header: paintBgWait writes the label, clears only what it wrote.
  const header = await p.evaluate(() => {
    const span = document.createElement('span');
    span.id = 'agent-activity-s1'; document.body.appendChild(span);
    window.paintBgWait('s1', 'waiting on background task: npm test');
    const painted = span.textContent;
    window.paintBgWait('s1', '');
    const cleared = span.textContent;
    span.textContent = '[tool: Bash]';          // the tool ticker shares this span
    window.paintBgWait('s1', '');
    return { painted, cleared, ticker: span.textContent };
  });
  header.painted === 'waiting on background task: npm test' ? ok('chat header paints the label')
    : fail(`header painted "${header.painted}"`);
  header.cleared === '' ? ok('chat header clears it when nothing is held')
    : fail(`header not cleared: "${header.cleared}"`);
  header.ticker === '[tool: Bash]' ? ok('the tool-line ticker in the same span is left alone')
    : fail(`ticker clobbered: "${header.ticker}"`);
  errors.length ? fail('page errors: ' + errors.slice(0, 3).join(' | ')) : ok('no page errors');
} finally {
  await ctx.close();
}
console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
