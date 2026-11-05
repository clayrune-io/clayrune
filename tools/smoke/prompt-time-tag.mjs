#!/usr/bin/env node
// Real SPA with hermetic API/SSE fixtures; no running server or live agent.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const html = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const files = loadStaticJsCss(root);
const shots = resolve(root, '_scratch/prompt_time_tag');
mkdirSync(shots, { recursive: true });
const pid = 'prompt_times', sid = 'prompt-times-session';
const stamp = '[Now 2026-11-05 08:04 PST · 2h since your last reply]\nSince your last reply:\n- A dispatched session finished\n- A backlog item changed\n\n';
const lines = [
  '> Ron: Yesterday’s request', 'Yesterday’s reply.',
  `> Ron: ${stamp}Show a small time beside each prompt.`,
  'Each tag uses the prompt’s recorded time.',
  `> Ron: ${stamp}An older prompt without a recorded timestamp.`,
  '> Ron: A malformed date must stay untagged.',
  '> Ron: First line\nSecond line with עברית and <script>literal text</script>',
];
const times = ['2026-11-05T07:27:00Z', '2026-11-05T07:28:00Z',
  '2026-11-05T17:27:00Z', '2026-11-05T17:28:00Z', null, 'bad-date', '2026-11-05T17:29:00Z'];
const project = {
  id: pid, name: 'Prompt times', status: 'active', domain: 'general', emoji: '🕒',
  description: '', summary: '', current_task: 'Idle', next_action: '',
  blocked: false, activity_log: [], backlog: [], project_path: '/smoke/prompt-times',
  last_updated: times[2], display_order: 0, provider: 'claude', use_streaming_agent: true,
};
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    const ctx = await browser.newContext({
      viewport: { width, height: 1000 }, timezoneId: 'America/Los_Angeles', hasTouch: width === 390,
    });
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => {
      window.smokeStreams = [];
      window.EventSource = class {
        constructor(url) { this.url = url; window.smokeStreams.push(this); }
        close() {}
      };
    });
    let resolveSend;
    await page.route('**/*', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
      if (path === '/api/projects') return route.fulfill({ json: [project] });
      if (path === '/api/config') return route.fulfill({ json: {} });
      if (path === '/api/characters') return route.fulfill({ json: [] });
      if (path.endsWith('/agent/followup')) return route.fulfill({ json: { ok: true } });
      // No diagrams in this fixture; avoid a CDN import error on hermetic boot.
      if (route.request().url().startsWith('https://cdn.jsdelivr.net/npm/mermaid@')) {
        return route.fulfill({ contentType: 'text/javascript', body: 'export default {initialize(){},async run(){}};' });
      }
      if (path.endsWith('/agent/send')) {
        const result = await new Promise(resolve => { resolveSend = resolve; });
        return route.fulfill({ json: result });
      }
      return route.abort();
    });
    await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(({ pid, sid, lines, times }) => {
      agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Prompt times',
        task: 'Prompt times', status: 'idle', startedAt: times[0] });
      agentStatusCache[sid] = { status: 'idle', task: 'Prompt times', projectId: pid,
        startedAt: times[0], claudeSessionId: 'native-prompt-times' };
      agentOutputBuffers[sid] = lines.slice();
      agentOutputTimestamps[sid] = times.slice();
      conversationsCache[pid] = [{ claude_session_id: 'native-prompt-times', mc_session_id: sid,
        character: null, status: 'idle', turns: 5, label: 'Prompt times', mtime: 1000 }];
      openProjectModal(pid);
      openConversation(pid, 'native-prompt-times', sid, true);
    }, { pid, sid, lines, times });
    const out = page.locator(`#agent-output-${sid}`);
    await out.waitFor();
    const readPrompts = () => out.locator('.agent-line-prompt').evaluateAll(nodes => nodes.map(n => ({
      text: n.textContent, time: n.querySelector('time')?.textContent || '',
      iso: n.querySelector('time')?.dateTime || '', title: n.querySelector('time')?.title || '',
    })));
    let prompts = await readPrompts();
    assert.deepEqual(prompts.map(p => p.time), ['23:27', '09:27', '', '', '09:29']);
    assert.equal(prompts[1].iso, times[2].replace('Z', '.000Z'));
    assert.match(prompts[1].title, /11\/5\/2026.*9:27:00 AM/);
    assert(!prompts[1].text.includes('[Now') && !prompts[1].text.includes('Since your last reply:'));
    assert(!prompts[2].text.includes('[Now'), 'an embedded stamp cannot supply a missing line timestamp');
    assert(prompts[1].text.includes('Show a small time beside each prompt.'));
    assert(prompts[4].text.includes('<script>literal text</script>'));
    assert.equal(await out.locator('.chat-date-divider').count(), 2);
    console.log(`PASS ${width}: cold own timestamps, local dates, absent/invalid dates, multiline escaping and clock stripping`);

    const stripping = await page.evaluate(({ stamp }) => ({
      absent: [undefined, null, false, '', 'invalid', true].map(t => promptTimeTagHTML(t)),
      alone: stripSysPreamble('> Ron: Since your last reply:\n- My own list'),
      repeated: stripSysPreamble(`> Ron: ${stamp}One\n${stamp}Two`),
    }), { stamp });
    assert(stripping.absent.every(t => t === ''));
    assert(stripping.alone.includes('Since your last reply:\n- My own list'));
    assert.equal(stripping.repeated, '> Ron: One\nTwo');

    await page.evaluate(sid => _repaintAgentOutput(sid), sid);
    assert.deepEqual((await readPrompts()).map(p => p.time), prompts.map(p => p.time));
    assert.equal(await out.locator('.chat-date-divider').count(), 2);
    const unchanged = await page.evaluate(sid => agentOutputBuffers[sid], sid);
    assert.deepEqual(unchanged, lines);
    // Legacy repaint uses startedAt for its divider only, never for a prompt tag.
    const legacy = await page.evaluate(({ pid, ts }) => {
      const id = 'legacy-undated';
      agentStatusCache[id] = { projectId: pid, startedAt: ts };
      agentOutputBuffers[id] = ['> Ron: No date on this line'];
      const host = document.createElement('div');
      host.id = `agent-output-${id}`;
      document.body.appendChild(host);
      _repaintAgentOutput(id);
      const result = { times: host.querySelectorAll('time').length, dividers: host.querySelectorAll('.chat-date-divider').length };
      host.remove();
      return result;
    }, { pid, ts: times[0] });
    assert.deepEqual(legacy, { times: 0, dividers: 1 });
    console.log(`PASS ${width}: replay preserves timestamps/buffers/dividers; legacy session anchor cannot date a prompt`);

    await page.evaluate(({ pid, sid }) => openChatSearch(pid, sid), { pid, sid });
    await page.locator(`#chat-search-input-${sid}`).fill('Show a small time');
    await page.waitForFunction(sid => document.getElementById(`chat-search-count-${sid}`)?.textContent.includes('1 of 1'), sid);
    assert.equal(await out.locator('mark.mc-chat-search-current').textContent(), 'Show a small time');
    assert.equal((await readPrompts())[1].time, '09:27');
    await page.evaluate(sid => closeChatSearch(sid), sid);
    console.log(`PASS ${width}: chat search highlights prompt text without losing its time tag`);

    // Real stream handler, including its missing-ts behavior and echo removal.
    await page.evaluate(({ pid, sid }) => connectAgentStream(pid, sid), { pid, sid });
    const emit = async msg => page.evaluate(({ sid, msg }) => {
      const stream = smokeStreams.find(s => s.url.includes(sid));
      if (!stream?.onmessage) throw new Error('agent stream handler missing');
      stream.onmessage({ data: JSON.stringify(msg) });
    }, { sid, msg });
    await emit({ type: 'output', text: `> Ron: ${stamp}A live prompt.`, ts: '2026-11-05T17:31:00Z' });
    assert.equal((await readPrompts()).at(-1).time, '09:31');
    await emit({ type: 'output', text: '> Ron: A live prompt with no ts.' });
    assert.equal((await readPrompts()).at(-1).time, '');
    await page.evaluate(({ pid, sid }) => {
      document.getElementById(`agent-followup-${sid}`).value = 'An optimistic prompt.';
      sendFollowup(pid, sid);
    }, { pid, sid });
    const optimistic = await out.locator('.agent-echo time').getAttribute('datetime');
    assert(Math.abs(Date.now() - Date.parse(optimistic)) < 10000);
    await page.waitForFunction(() => document.querySelector('.agent-echo'));
    // The server acknowledges with its own timestamp, replacing client time.
    await emit({ type: 'output', text: '> Ron: An optimistic prompt.', ts: '2026-11-05T17:32:00Z' });
    assert.equal(await out.locator('.agent-echo').count(), 0);
    assert.equal((await readPrompts()).at(-1).time, '09:32');
    await page.waitForTimeout(50);
    assert.equal(typeof resolveSend, 'function');
    resolveSend({ ok: true, session_id: sid });
    await page.waitForTimeout(100);
    console.log(`PASS ${width}: SSE timestamp and missing-ts cases; optimistic client time replaced by server time`);

    await page.evaluate(({ pid, sid }) => approvePlan(pid, sid), { pid, sid });
    const approvalTime = await out.locator('.agent-echo time').getAttribute('datetime');
    assert(Math.abs(Date.now() - Date.parse(approvalTime)) < 10000);
    await emit({ type: 'output', text: '> Ron: Plan approved, please continue.', ts: '2026-11-05T17:33:00Z' });
    assert.equal(await out.locator('.agent-echo').count(), 0);
    console.log(`PASS ${width}: plan-approval optimistic prompt also uses client time`);

    await page.evaluate(({ pid, sid }) => {
      hideTypingIndicator(sid);
      // Rebuild from buffered history to verify cold rendering after live events.
      refreshModal();
      document.getElementById(`agent-output-${sid}`).scrollTop = 0;
    }, { pid, sid });
    await page.waitForTimeout(150);
    const bounds = await out.evaluate(el => ({
      width: el.clientWidth, scroll: el.scrollWidth,
      tags: [...el.querySelectorAll('.agent-line-prompt time')].every(t => {
        const r = t.getBoundingClientRect(), b = t.closest('.agent-line-prompt').getBoundingClientRect();
        return r.left >= b.left && r.right <= b.right && r.width > 0;
      }),
    }));
    assert(bounds.scroll <= bounds.width + 1, JSON.stringify(bounds));
    assert(bounds.tags, 'tag escaped prompt bounds');
    assert.equal(errors.length, 0, errors.join('\n'));
    await page.screenshot({ path: resolve(shots, `chat-${width}.png`) });
    console.log(`PASS ${width}: no horizontal overflow/page errors; screenshot _scratch/prompt_time_tag/chat-${width}.png`);
    await ctx.close();
  }
} finally {
  await browser.close();
}
