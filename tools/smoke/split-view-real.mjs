#!/usr/bin/env node
/**
 * Split view against a REAL running Clayrune server with REAL agents.
 *
 * Dispatches two cheap fixture conversations (ALPHA, BRAVO) into the project
 * you name, then drives the dashboard: open ALPHA, split BRAVO beside it, and
 * send a message from each pane. Passes only if each pane shows its own
 * conversation's history, the agent's reply STREAMS into the pane that sent
 * it (no reload), the server's log for that session holds the message, and
 * the other pane is untouched. The hermetic half is
 * split-view-idle-conversation.mjs.
 *
 * Costs four short model turns. By default the page is served this checkout's
 * static/ (so a branch can be tested before merge) over the live API.
 *
 * RUN
 *   SPLIT_PROJECT=<project id> node split-view-real.mjs
 * ENV
 *   SPLIT_PROJECT   required: a project with a valid project_path you don't mind adding two chats to
 *   MC_BASE         default http://localhost:5199
 *   SPLIT_MODEL     default claude-haiku-4-5-20251001
 *   SERVE_CHECKOUT  default 1; 0 = use whatever static the server itself serves
 *   SPLIT_SHOT      optional screenshot path
 */
import { chromium } from 'playwright';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const BASE = process.env.MC_BASE || 'http://localhost:5199';
const PID = process.env.SPLIT_PROJECT;
const MODEL = process.env.SPLIT_MODEL || 'claude-haiku-4-5-20251001';
const SERVE_CHECKOUT = process.env.SERVE_CHECKOUT !== '0';
const SHOT = process.env.SPLIT_SHOT || '';
if (!PID) { console.error('SPLIT_PROJECT is required (a project id on this server).'); process.exit(2); }

const N = Date.now().toString(36).slice(-5).toUpperCase();
const W = { A: `ALPHA${N}`, B: `BRAVO${N}`, C: `CHARLIE${N}`, D: `DELTA${N}` };
const ask = (w) => `Split-view smoke. Reply with exactly the single word ${w} and nothing else.`;

const ok = (m) => console.log('  ✓ ' + m);
const note = (m) => console.log('  ! ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (c, p, f) => (c ? ok(p) : fail(f));
const count = (hay, needle) => (hay || '').split(needle).length - 1;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const api = async (path, body) => {
  const r = await fetch(BASE + path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: BASE }, body: JSON.stringify(body) } : {});
  return r.json();
};
const serverLog = async (sid) => {
  const s = ((await api(`/api/project/${PID}/agent/status`)).sessions || []).find((x) => x.session_id === sid);
  return s ? { status: s.status, text: (s.log_lines || []).join('\n') } : null;
};
const waitServer = async (sid, word, ms = 120000) => {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const s = await serverLog(sid);
    if (s && s.status === 'idle' && count(s.text, word) >= 2) return true;
    await sleep(1500);
  }
  return false;
};

let browser, exitCode = 1;
const fixtures = [];
try {
  const sa = await api(`/api/project/${PID}/agent/dispatch`, { task: ask(W.A), model: MODEL });
  const sb = await api(`/api/project/${PID}/agent/dispatch`, { task: ask(W.B), model: MODEL });
  if (!sa.session_id || !sb.session_id) throw new Error('dispatch failed: ' + JSON.stringify([sa, sb]));
  const A = sa.session_id, B = sb.session_id;
  fixtures.push(A, B);
  ok(`dispatched fixtures A=${A} B=${B}`);
  check(await waitServer(A, W.A) && await waitServer(B, W.B), 'both fixture chats answered and are idle', 'fixture chats never finished their first turn');

  browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1600, height: 950 } })).newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const sendReplies = [];
  page.on('response', async (r) => {
    if (r.request().method() === 'POST' && /\/agent\/send$/.test(new URL(r.url()).pathname)) {
      sendReplies.push({ req: JSON.parse(r.request().postData() || '{}'), res: await r.json().catch(() => ({})) });
    }
  });
  if (SERVE_CHECKOUT) {
    await page.route('**/*', (route) => {
      const u = new URL(route.request().url());
      const f = u.pathname === '/' ? resolve(ROOT, 'static', 'index.html')
        : u.pathname.startsWith('/static/') ? resolve(ROOT, u.pathname.slice(1)) : null;
      if (f && existsSync(f)) {
        const ct = f.endsWith('.js') ? 'text/javascript' : f.endsWith('.css') ? 'text/css' : f.endsWith('.html') ? 'text/html' : undefined;
        return route.fulfill({ status: 200, contentType: ct, body: readFileSync(f) });
      }
      return route.continue();
    });
  }
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 30000 });
  await page.evaluate((pid) => openProjectModal(pid), PID);
  await page.waitForSelector(`.conv-row[data-mcsid="${A}"]`, { timeout: 30000 });
  await page.waitForSelector(`.conv-row[data-mcsid="${B}"]`, { timeout: 30000 });

  await page.click(`.conv-row[data-mcsid="${A}"]`);
  await page.waitForFunction(({ pid, A }) => activeAgentTab[pid] === A, { pid: PID, A }, { timeout: 10000 }).catch(() => {});
  check(await page.evaluate((pid) => activeAgentTab[pid], PID) === A, 'chat A open as the primary pane', 'clicking row A did not open it');

  const btn = await page.evaluate((B) => {
    const b = document.querySelector(`.conv-row[data-mcsid="${B}"] .conv-split`);
    return b ? parseFloat(getComputedStyle(b).opacity) : null;
  }, B);
  check(btn !== null && btn > 0, `row B shows a visible split button (opacity ${btn})`, `row B split button missing or invisible (${btn})`);

  await page.click(`.conv-row[data-mcsid="${B}"] .conv-split`);
  await page.waitForSelector(`.agent-split-pane[data-sid="${B}"]`, { timeout: 10000 }).catch(() => {});
  await page.waitForFunction((B) => (document.querySelector(`#agent-output-${B}`)?.innerText || '').length > 0, B, { timeout: 10000 }).catch(() => {});

  const paneText = (sid) => page.evaluate((sid) => document.querySelector(`.agent-split-pane[data-sid="${sid}"] .agent-output`)?.innerText || '', sid);
  const state = () => page.evaluate((pid) => ({ active: activeAgentTab[pid], split: splitAgentTab[pid],
    panes: [...document.querySelectorAll('.agent-split-pane')].map((p) => p.dataset.sid),
    labels: [...document.querySelectorAll('.agent-split-label')].map((l) => Math.round(l.getBoundingClientRect().width)) }), PID);

  let st = await state();
  check(st.active === A && st.split === B && st.panes.length === 2, 'split view open: A | B', `split did not open: ${JSON.stringify(st)}`);
  check(st.labels.every((w) => w > 0), `pane titles visible (${st.labels.join('px, ')}px)`, `a pane title is 0px wide: ${st.labels}`);
  let ta = await paneText(A), tb = await paneText(B);
  check(tb.includes(W.B) && !tb.includes(W.A), 'pane 2 renders conversation B’s history', `pane 2 history wrong: ${JSON.stringify(tb.slice(-160))}`);
  check(ta.includes(W.A) && !ta.includes(W.B), 'pane 1 still renders conversation A’s history', `pane 1 history wrong: ${JSON.stringify(ta.slice(-160))}`);

  const sendFrom = async (sid, word, otherSid) => {
    const before = sendReplies.length;
    await page.fill(`#agent-followup-${sid}`, ask(word));
    await page.press(`#agent-followup-${sid}`, 'Enter');
    await page.waitForFunction(({ sid, word }) => ((document.querySelector(`#agent-output-${sid}`)?.innerText || '').split(word).length - 1) >= 2,
      { sid, word }, { timeout: 120000 }).catch(() => {});
    const r = sendReplies[before];
    check(r && r.req.session_id === sid, `Send in pane ${sid} POSTed session_id=${sid}`, `Send in pane ${sid} posted ${r && r.req.session_id}`);
    if (r && r.res.session_id && r.res.session_id !== sid) {
      note(`server answered under a NEW session id ${r.res.session_id} (route ${r.res.route}) — possible chat session forking, not split view`);
    }
    const mine = await paneText(sid), other = await paneText(otherSid);
    check(count(mine, word) >= 2, `agent reply ${word} streamed into the sending pane (no reload)`, `reply ${word} never appeared in pane ${sid}: ${JSON.stringify(mine.slice(-160))}`);
    check(!other.includes(word), 'other pane untouched', `${word} leaked into pane ${otherSid}`);
    // Turn end must reach the pane too: once the server says idle, the pane's
    // "thinking" dots must clear (a split pane that never hears turn_complete
    // would sit on them forever).
    const idle = await waitServer(sid, word, 60000);
    const dotsGone = await page.waitForFunction((sid) => !document.getElementById(`typing-${sid}`), sid, { timeout: 10000 }).then(() => true).catch(() => false);
    check(idle && dotsGone, `pane ${sid} cleared its typing indicator after the turn ended`, `pane ${sid} still shows typing dots after server idle=${idle}`);
    const sMine = await serverLog(sid), sOther = await serverLog(otherSid);
    check(sMine && sMine.text.includes(word), `server log for ${sid} holds ${word}`, `server log for ${sid} lacks ${word}`);
    check(sOther && !sOther.text.includes(word), `server log for ${otherSid} does not`, `server log for ${otherSid} also holds ${word}`);
  };

  await sendFrom(B, W.C, A);
  await sendFrom(A, W.D, B);

  st = await state();
  check(st.active === A && st.split === B && st.panes.length === 2, 'split layout survived both sends', `layout changed after sends: ${JSON.stringify(st)}`);
  // A live page also loads CDN modules (mermaid); a network blip there is not a split-view defect.
  const uncaught = errors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.length ? uncaught.forEach((e) => fail('page error: ' + e)) : ok('no uncaught page errors');
  if (SHOT) { await page.screenshot({ path: SHOT }); ok('screenshot: ' + SHOT); }
  exitCode = bad ? 1 : 0;
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  for (const sid of fixtures) await api(`/api/project/${PID}/agent/stop`, { session_id: sid }).catch(() => {});
}
console.log(exitCode ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(exitCode);
