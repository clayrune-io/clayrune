#!/usr/bin/env node
/**
 * Resumed Codex chat: the user's own message must render in place.
 *
 * WHY THIS EXISTS
 * ---------------
 * Backlog 30e6a946 (phone): a cold-resumed Codex chat showed the read-only
 * reconstruct, its "send a message to resume" banner, then the new turn's
 * replies, but NOT the user's own `> Ron: ...` message, although it was in
 * the server's log_lines.
 *
 * ROOT CAUSE: a cold-resumed non-Claude revive does not seed log_lines with
 * the prior history (Claude's does, and bumps log_epoch), so the server's
 * array is just the new turn while the client cursor sits at the length of
 * the reconstruct. The stream answers `since > len` with `reset`; the replay
 * of the turn's first lines (`> Ron: ...`, notice) settled through
 * _mergeShorterHistory, whose "shorter copy" guard found no shared line
 * (the screen ends on the banner) and kept the screen with nothing appended.
 * Every line after the replay streamed in normally, which is why only the
 * prompt went missing.
 *
 * Serves static/js + static/css from THIS checkout over the live page; every
 * non-GET is aborted, so nothing touches real sessions.
 *
 * RUN: node codex-resume-render.mjs   (needs MC on localhost:5199, or MC_SMOKE_BASE)
 */
import { chromium } from 'playwright';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, m) => (cond ? ok(m) : fail(m));

const PID = '__codexresume__';
const BANNER = '[— read-only history; send a message to resume this session —]';
const HISTORY = [
  '\n> Ron: earlier question\n',
  'earlier answer one',
  '\n> Ron: second question\n',
  'earlier answer two',
  BANNER,
];
const PROMPT = '> Ron: OK, so after all that we got to 56% prediction rate. Do you think there are more ways to further up the number?';
// The seven log_lines of the live case, in server order.
const TURN = [
  PROMPT,
  '[codex notice] Codex hook-trust review bypassed so Clayrune guardrail hooks can run (expected).',
  'COMMENTARY: the 56.1% result was 129 correct calls out of 230 accepted forecasts.',
  '[tool: web_search]',
  '[tool: shell] powershell -Command "script one"',
  '[tool: shell] powershell -Command "script two"',
  'FINAL: possibly, but 56.1% was the selected early-2026 result.',
];

const browser = await chromium.launch();
async function runCase(label, viewport) {
  console.log(`\n== ${label} (${viewport.width}x${viewport.height}) ==`);
  const SID = 'codexresume' + viewport.width;
  const page = await browser.newPage({ viewport });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message || String(e)));
  await page.route('**/*', (route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (req.method() !== 'GET') return route.abort();
    if (url.hostname === 'localhost' && /^\/static\/(js|css)\//.test(url.pathname)) {
      const f = join(ROOT, url.pathname);
      if (existsSync(f)) {
        return route.fulfill({ status: 200,
          contentType: url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript; charset=utf-8',
          body: readFileSync(f, 'utf8') });
      }
    }
    return route.continue();
  });
  await page.goto(process.env.MC_SMOKE_BASE || 'http://localhost:5199/', { waitUntil: 'domcontentloaded' });
  const bridged = await page.waitForFunction(
    () => typeof window._mergeShorterHistory === 'function' && typeof window.connectAgentStream === 'function'
      && typeof window.appendAgentLine === 'function',
    null, { timeout: 20000 }).then(() => true).catch(() => false);
  check(bridged, 'checkout JS loaded');
  if (!bridged) throw new Error('no bridge');

  // The screen as the read-only reconstruct leaves it, then the optimistic echo.
  await page.evaluate(({ pid, sid, hist }) => {
    const wrap = document.createElement('div');
    wrap.className = 'agent-chat';
    const out = document.createElement('div');
    out.className = 'agent-output';
    out.id = `agent-output-${sid}`;
    wrap.appendChild(out);
    document.body.appendChild(wrap);
    agentOutputBuffers[sid] = hist.slice();
    agentOutputTimestamps[sid] = hist.map(() => null);
    agentServerLines[sid] = hist.length;   // cursor = reconstruct length
    hist.forEach((l) => window.appendAgentLine(sid, l, false));
    const echo = document.createElement('div');
    echo.className = 'agent-line agent-line-prompt agent-echo';
    echo.textContent = '> OK, so after all that we got to 56% prediction rate. Do you think there are more ways to further up the number?';
    out.appendChild(echo);
    window.EventSource = class {
      constructor(u) { const m = /[?&]session=([^&]+)/.exec(u); if (m) (window.__crES ||= {})[decodeURIComponent(m[1])] = this; }
      close() {}
    };
    window.connectAgentStream(pid, sid);
  }, { pid: PID, sid: SID, hist: HISTORY });
  const emit = (m) => page.evaluate(({ msg, sid }) => window.__crES[sid].onmessage({ data: JSON.stringify(msg) }), { msg: m, sid: SID });

  // The server's answer to since=<reconstruct length> on a 2-line array.
  await emit({ type: 'reset' });
  for (let i = 0; i < 2; i++) await emit({ type: 'output', text: TURN[i], line_index: i + 1 });
  await page.waitForTimeout(700);                 // replay settles
  for (let i = 2; i < TURN.length; i++) await emit({ type: 'output', text: TURN[i], line_index: i + 1 });

  const lines = await page.evaluate((sid) => ({
    dom: [...document.querySelectorAll(`#agent-output-${sid} .agent-line`)].map((e) => e.textContent.trim()),
    echo: document.querySelectorAll(`#agent-output-${sid} .agent-echo`).length,
    buf: (agentOutputBuffers[sid] || []).map((l) => l.trim()),
    cursor: agentServerLines[sid],
  }), SID);
  const count = (arr, s) => arr.filter((l) => l.includes(s)).length;
  const promptText = PROMPT.trim();
  check(count(lines.dom, promptText) === 1, `the user's message renders exactly once (dom=${count(lines.dom, promptText)})`);
  check(count(lines.buf, promptText) === 1, `the user's message is in the buffer exactly once (buf=${count(lines.buf, promptText)})`);
  check(lines.echo === 0, `the optimistic echo was replaced, not left beside it (echo nodes=${lines.echo})`);
  check(count(lines.dom, 'COMMENTARY:') === 1 && count(lines.dom, 'FINAL:') === 1, 'each reply renders exactly once (no live/history double render)');
  const ix = (s) => lines.dom.findIndex((l) => l.includes(s));
  check(ix(BANNER) >= 0 && ix(BANNER) < ix(promptText) && ix(promptText) < ix('COMMENTARY:') && ix('COMMENTARY:') < ix('FINAL:'),
    'order: history, banner, your message, commentary, final answer');
  check(lines.cursor === TURN.length, `cursor follows the server's array (cursor=${lines.cursor})`);

  // Visible, inside the viewport width (the phone symptom was absent).
  const box = await page.evaluate(({ sid, txt }) => {
    const el = [...document.querySelectorAll(`#agent-output-${sid} .agent-line`)].find((e) => e.textContent.includes(txt));
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height), right: Math.round(r.right), vw: window.innerWidth };
  }, { sid: SID, txt: promptText });
  check(!!box && box.h > 0 && box.w > 0 && box.right <= box.vw + 1, `your message has a box inside the viewport (${JSON.stringify(box)})`);

  const ours = errors.filter((e) => /history|_merge|_settle|appendAgentLine|connectAgentStream/i.test(e));
  check(ours.length === 0, `no page errors from the render path (${errors.length} total)`);
  if (ours.length) console.error(ours.join('\n'));
  await page.close();
}
try {
  await runCase('desktop', { width: 1200, height: 800 });
  await runCase('mobile', { width: 390, height: 844 });
} catch (e) {
  fail(`smoke aborted: ${e.message}`);
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(bad ? 1 : 0);
