#!/usr/bin/env node
// Hermetic real-SPA send/revive regression. Never sends to a live agent.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const html = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const files = loadStaticJsCss(root);
const pid = 'send_timeout', sid = 'send-timeout-session';
const message = 'Wake this session\nKeep this second line.';
const history = [`> Ron: ${message}`, 'Earlier reply'];
const project = {
  id: pid, name: 'Send timeout', status: 'active', domain: 'general', emoji: '',
  description: '', summary: '', current_task: 'Idle', next_action: '',
  blocked: false, activity_log: [], backlog: [], project_path: '/smoke/send-timeout',
  last_updated: '2026-11-11T00:00:00Z', display_order: 0, provider: 'claude', use_streaming_agent: true,
};
let passed = 0;
const browser = await chromium.launch();

async function scenario(name, options, check) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  try {
    const page = await ctx.newPage();
    page.setDefaultTimeout(20000);
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => {
      window.smokeStreams = [];
      window.EventSource = class {
        constructor(url) { this.url = url; window.smokeStreams.push(this); }
        close() { this.closed = true; }
      };
    });
    let sending = false, delivered = false, probes = 0, posts = 0;
    let resolveSend;
    await page.route('**/*', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
      if (path === '/api/projects') return route.fulfill({ json: [project] });
      if (path === '/api/config') return route.fulfill({ json: {} });
      if (path === '/api/characters') return route.fulfill({ json: [] });
      if (route.request().url().startsWith('https://cdn.jsdelivr.net/npm/mermaid@')) {
        return route.fulfill({ contentType: 'text/javascript', body: 'export default {initialize(){},async run(){}};' });
      }
      if (path.endsWith('/agent/status')) {
        if (sending) {
          probes++;
          if (options.hangProbe) return new Promise(() => {});
          if (options.failProbe) return route.fulfill({ status: 503, json: {} });
        }
        return route.fulfill({ json: { sessions: [{
          session_id: sid, status: sending ? (options.serverStatus || 'running') : 'error',
          log_lines: delivered ? [...history, `> Ron: ${message}`] : history,
        }] } });
      }
      if (path.endsWith('/agent/send')) {
        posts++;
        sending = true;
        delivered = !!options.delivered;
        if (options.networkError) return route.abort('failed');
        if (options.reply) return route.fulfill({ json: options.reply });
        await new Promise(resolve => { resolveSend = resolve; });
        delivered = true;
        return route.fulfill({ json: { ok: true, session_id: sid } });
      }
      return route.abort();
    });
    await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(({ pid, sid, history }) => {
      agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Send timeout',
        task: 'Parked', status: 'error', startedAt: '2026-11-10T00:00:00Z' });
      agentStatusCache[sid] = { status: 'error', task: 'Parked', projectId: pid,
        claudeSessionId: 'native-send-timeout', startedAt: '2026-11-10T00:00:00Z' };
      agentOutputBuffers[sid] = history.slice();
      agentServerLines[sid] = history.length;
      conversationsCache[pid] = [{ claude_session_id: 'native-send-timeout', mc_session_id: sid,
        character: null, status: 'error', turns: 1, label: 'Parked', mtime: 1000 }];
      openProjectModal(pid);
      openConversation(pid, 'native-send-timeout', sid, true);
      window.smokeToasts = [];
      window.showToast = text => window.smokeToasts.push(text);
    }, { pid, sid, history });
    await page.waitForSelector(`#agent-followup-${sid}`);
    if (options.clock) await page.clock.install();
    const streamsAtSend = await page.evaluate(() => window.smokeStreams.length + 1);
    await page.evaluate(({ pid, sid, message }) => {
      document.getElementById(`agent-followup-${sid}`).value = message;
      window.sendFollowup(pid, sid);
    }, { pid, sid, message });
    await page.waitForFunction(() => window.smokeStreams.length > 0);
    // Wait for the POST handler, not just the synchronous optimistic paint.
    while (!sending) await page.waitForTimeout(10);
    const input = page.locator(`#agent-followup-${sid}`);
    const output = page.locator(`#agent-output-${sid}`);
    await check({ page, input, output, resolveSend: () => resolveSend(), probes: () => probes, streamsAtSend });
    assert.equal(posts, 1, 'one user send must produce exactly one POST');
    assert.deepEqual(errors, [], 'no uncaught runtime errors');
    passed++;
    console.log(`PASS ${name}`);
  } finally {
    await ctx.close();
  }
}

const recovered = async ({ page, streamsAtSend }) => {
  await page.waitForFunction(({ sid, n }) => window.smokeStreams.length > n && agentStatusCache[sid].status === 'running',
    { sid, n: streamsAtSend });
};
try {
  await scenario('15s revive: pending after 12s, no failure, cleared on 200', {}, async ({ page, input, output, resolveSend, probes }) => {
    // Real elapsed delay, exercising the production 12s timer without overrides.
    const reply = new Promise(resolve => setTimeout(() => { resolveSend(); resolve(); }, 15000));
    await output.locator('.agent-echo-pending').waitFor({ timeout: 14500 });
    assert.match(await output.locator('.agent-echo-pending').innerText(), /waking session/);
    assert.equal(await output.locator('.agent-echo-failed').count(), 0);
    assert.equal(await input.inputValue(), '');
    assert(probes() >= 1);
    await reply;
    await page.waitForFunction(() => !document.querySelector('.agent-echo-pending'));
    assert.equal(await output.locator('.agent-echo-failed').count(), 0);
    assert.deepEqual(await page.evaluate(() => smokeToasts), []);
  });
  await scenario('hanging status probe: abort after 12s + 5s and retain retry text', { hangProbe: true }, async ({ page, input, output }) => {
    await output.locator('.agent-echo-failed').waitFor({ timeout: 19000 });
    assert.match(await output.locator('.agent-echo-failed').innerText(), /failed to send/);
    assert.equal(await output.locator('.agent-echo-pending').count(), 0);
    await page.waitForFunction(({ sid, message }) => document.getElementById(`agent-followup-${sid}`).value === message,
      { sid, message }, { timeout: 10000 });
    assert.equal(await input.inputValue(), message);
    assert.match((await page.evaluate(() => smokeToasts)).at(-1), /Send timed out/);
  });
  await scenario('healthy probe: absolute 120s hard cap still aborts and heals status', { clock: true }, async args => {
    const { page, input, output } = args;
    await page.clock.runFor(12100);
    await output.locator('.agent-echo-pending').waitFor();
    await page.clock.runFor(108000);
    await output.locator('.agent-echo-failed').waitFor();
    await recovered(args);
    await page.waitForFunction(({ sid, message }) => document.getElementById(`agent-followup-${sid}`).value === message, { sid, message });
    assert.equal(await input.inputValue(), message);
  });
  await scenario('failed probe: abort and recover SSE/status', { failProbe: true, clock: true }, async ({ page, output, streamsAtSend }) => {
    await page.clock.runFor(12100);
    await output.locator('.agent-echo-failed').waitFor();
    await page.waitForFunction(n => smokeStreams.length > n, streamsAtSend);
  });
  await scenario('network error after delivery: reconcile clears failure and never restores input', { networkError: true, delivered: true }, async args => {
    await recovered(args);
    await args.page.waitForFunction(() => !document.querySelector('.agent-echo-failed'));
    assert.equal(await args.input.inputValue(), '');
    assert.deepEqual(await args.page.evaluate(() => smokeToasts), []);
  });
  await scenario('abort after delivery: reconcile clears failure and never restores input', { clock: true, delivered: true }, async args => {
    await args.page.clock.runFor(12100);
    await args.output.locator('.agent-echo-pending').waitFor();
    await args.page.clock.runFor(108000);
    await recovered(args);
    await args.page.waitForFunction(() => !document.querySelector('.agent-echo-failed'));
    assert.equal(await args.input.inputValue(), '');
    assert.deepEqual(await args.page.evaluate(() => smokeToasts), []);
  });
  await scenario('network error: old identical prompt cannot acknowledge this send', { networkError: true }, async args => {
    await recovered(args);
    await args.page.waitForFunction(({ sid }) => !!document.getElementById(`agent-followup-${sid}`).value, { sid });
    assert.equal(await args.input.inputValue(), message);
    assert.equal(await args.output.locator('.agent-echo-failed').count(), 1);
    assert.match((await args.page.evaluate(() => smokeToasts)).at(-1), /Send failed:/);
  });
  await scenario('server rejection also reconnects and reconciles', { reply: { ok: false, error: 'revive refused' } }, async args => {
    await recovered(args);
    assert.equal(await args.input.inputValue(), '', 'preserve ordinary rejection composer behavior');
    const before = args.probes();
    const sessions = await args.page.evaluate(async ({ pid, sid }) => {
      const sessions = await Promise.all([_reconcileAgentBuffer(pid, sid), _reconcileAgentBuffer(pid, sid)]);
      return sessions.map(s => s?.session_id);
    }, { pid, sid });
    assert.deepEqual(sessions, [sid, sid], 'concurrent recovery callers both receive server truth');
    assert.equal(args.probes(), before + 1, 'concurrent recovery shares its status request');
  });
  await scenario('allowance rejection retains blocked card and retry input', { reply: { ok: false, allowance_blocked: true, error: 'No fallback' } }, async args => {
    await recovered(args);
    await args.output.locator('.guardian-banner').waitFor();
    assert.match(await args.output.locator('.agent-echo-failed').innerText(), /\[blocked\]/);
    assert.equal(await args.input.inputValue(), message);
  });
  await scenario('rejection restores status pill even when server/cache stayed failed', {
    serverStatus: 'error', reply: { ok: false, error: 'still failed' },
  }, async ({ page, streamsAtSend }) => {
    await page.waitForFunction(n => smokeStreams.length > n, streamsAtSend);
    await page.waitForFunction(() => document.querySelector('.agent-panel .agent-status-label.error'));
  });
  console.log(`\n${passed} passed, 0 failed`);
} catch (error) {
  console.error(error);
  console.log(`\n${passed} passed, 1 failed`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
