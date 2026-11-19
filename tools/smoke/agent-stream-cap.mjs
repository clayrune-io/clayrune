#!/usr/bin/env node
// FE5 (docs/_review/2026-11-16_04_frontend.md): every running session held an
// EventSource whether or not anyone was looking, with no ceiling. Chromium
// allows 6 connections per origin, so six running sessions left the tab unable
// to make any other request. static/js/agent-stream-cap.js caps live streams at
// MAX_LIVE_AGENT_STREAMS (4) and drops the least recently viewed chat's stream
// when a chat on screen needs one.
//
// Six projects, each with one running session. EventSource is replaced by a
// recorder, so "open" means created and not closed. The poll is hermetic
// (Playwright routes); nothing talks to a live server or agent.
//
//   node tools/smoke/agent-stream-cap.mjs            # the fix
//   node tools/smoke/agent-stream-cap.mjs --no-cap   # the same page with the
//                                                    # guard removed = old behaviour
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const html = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const files = loadStaticJsCss(root);
const noCap = process.argv.includes('--no-cap');
const N = 6;
const projects = Array.from({ length: N }, (_, i) => ({
  id: `p${i}`, name: `P${i}`, status: 'active', domain: 'general', emoji: '', description: '',
  summary: '', current_task: '', next_action: '', blocked: false, activity_log: [], backlog: [],
  project_path: `/smoke/p${i}`, last_updated: '2026-11-11T00:00:00Z', display_order: i,
  provider: 'claude', use_streaming_agent: true,
}));

let passed = 0, failed = 0;
async function step(name, fn) {
  try { await fn(); passed++; console.log(`PASS ${name}`); }
  catch (e) { failed++; console.log(`FAIL ${name}\n  ${String(e.message || e).split('\n').join('\n  ')}`); }
}
const check = (cond, msg) => { if (!cond) throw new Error(msg); };

const browser = await chromium.launch();
try {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  page.setDefaultTimeout(20000);
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(() => {
    window.smokeStreams = [];
    window.EventSource = class {
      constructor(url) { this.url = url; this.closed = false; window.smokeStreams.push(this); }
      close() { this.closed = true; }
    };
  });
  // Each poll of a session returns one more line, so a chat that has no stream
  // still moves forward through the status poll.
  const polls = {};
  await page.route('**/*', async route => {
    const u = new URL(route.request().url());
    const path = u.pathname;
    if (path === '/') return route.fulfill({ contentType: 'text/html', body: html });
    if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
    if (path === '/api/projects') return route.fulfill({ json: projects });
    if (path === '/api/config') return route.fulfill({ json: {} });
    if (path === '/api/characters') return route.fulfill({ json: [] });
    if (u.href.startsWith('https://cdn.jsdelivr.net/npm/mermaid@')) {
      return route.fulfill({ contentType: 'text/javascript', body: 'export default {initialize(){},async run(){}};' });
    }
    const m = path.match(/^\/api\/project\/(p\d+)\/agent\/status$/);
    if (m) {
      polls[m[1]] = (polls[m[1]] || 0) + 1;
      const lines = ['> go', ...Array.from({ length: polls[m[1]] }, (_, i) => `working ${i}`)];
      return route.fulfill({ json: { sessions: [{ session_id: `s-${m[1]}`, status: 'running',
        log_lines: lines, task: 'go' }] } });
    }
    return route.abort();
  });
  await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  if (noCap) await page.evaluate(() => { delete window.admitAgentStream; });

  const open = () => page.evaluate(() => smokeStreams.filter(s => !s.closed).map(s => s.url.match(/session=([^&]+)/)[1]));
  const poll = pid => page.evaluate(id => fetchAgentStatus(id), pid);
  const view = pid => page.evaluate(id => {
    openProjectModal(id);
    switchAgentTab(id, `s-${id}`);
  }, pid);
  const settle = () => page.waitForTimeout(400);

  await step('six running sessions, nobody looking: at most 4 streams open', async () => {
    for (let i = 0; i < N; i++) await poll(`p${i}`);
    await settle();
    const o = await open();
    check(o.length === 4, `${o.length} streams open: ${o.join(', ')} (expected 4)`);
  });

  await step('the on-screen chat has a stream, and the count stays 4', async () => {
    await view('p5');
    await settle();
    const o = await open();
    check(o.includes('s-p5'), `s-p5 is on screen but has no stream; open: ${o.join(', ')}`);
    check(o.length === 4, `${o.length} streams open: ${o.join(', ')} (expected 4)`);
  });

  await step('viewing another chat swaps a stream, the on-screen ones are kept, count stays 4', async () => {
    await view('p4');
    await settle();
    const o = await open();
    check(o.includes('s-p4') && o.includes('s-p5'), `both on-screen chats need a stream; open: ${o.join(', ')}`);
    check(o.length === 4, `${o.length} streams open: ${o.join(', ')} (expected 4)`);
  });

  await step('a chat that lost its stream is never over the cap across repeated polls', async () => {
    for (let round = 0; round < 3; round++) for (let i = 0; i < N; i++) await poll(`p${i}`);
    await settle();
    const o = await open();
    check(o.length === 4, `${o.length} streams open after 3 poll rounds: ${o.join(', ')} (expected 4)`);
    check(o.includes('s-p4') && o.includes('s-p5'), `on-screen chats lost a stream: ${o.join(', ')}`);
  });

  await step('a dropped chat gets its stream back on view, resuming from its cursor (no gap)', async () => {
    const o = await open();
    const dropped = Array.from({ length: N }, (_, i) => `s-p${i}`).filter(s => !o.includes(s));
    check(dropped.length === 2, `expected 2 chats without a stream, got ${dropped.join(', ')}`);
    const target = dropped[0];
    const pid = target.slice(2);
    const before = await page.evaluate(sid => ({ cursor: agentServerLines[sid], lines: agentOutputBuffers[sid].length }), target);
    check(before.lines > 1 && before.cursor === before.lines,
      `the poll should have kept ${target} current while it had no stream: cursor ${before.cursor}, buffer ${before.lines}`);
    await view(pid);
    await settle();
    const after = await open();
    check(after.includes(target), `${target} is on screen again but has no stream; open: ${after.join(', ')}`);
    check(after.length === 4, `${after.length} streams open: ${after.join(', ')} (expected 4)`);
    const url = await page.evaluate(sid => smokeStreams.filter(s => s.url.includes(`session=${sid}`)).pop().url, target);
    const since = Number(url.match(/since=(\d+)/)[1]);
    check(since >= before.cursor, `reopened at since=${since}, behind the cursor ${before.cursor}: the server would replay lines already drawn`);
    check(since <= before.lines + 1, `reopened at since=${since}, ahead of the ${before.lines} buffered lines: lines would be skipped`);
  });

  await step('no page errors', async () => { check(!errors.length, errors.join(' | ')); });
  await ctx.close();
} finally {
  await browser.close();
}
console.log(`\nagent-stream-cap${noCap ? ' (guard removed)' : ''}: ${passed} passed, ${failed} failed`);
if (failed) process.exitCode = 1;
