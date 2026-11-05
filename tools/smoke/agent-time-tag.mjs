#!/usr/bin/env node
// Real checkout HTML/JS, hermetic APIs and the actual SSE handler. No agent runs.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const html = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const files = loadStaticJsCss(root);
const shots = resolve(root, '_scratch/agent_time_tag');
mkdirSync(shots, { recursive: true });
const pid = 'agent_times', sid = 'agent-times-session';
const t = minute => `2026-11-05T17:${minute}:00Z`;
const lines = ['> Ron: Yesterday', 'Yesterday reply.',
  '> Ron: Show reply times', 'AGENT first paragraph.', 'Second paragraph.',
  '[tool: Read]', 'Undated start.', 'First valid line.', 'Back-to-back reply.',
  '> Ron: Null history', 'No recorded time; literal 09:41 stays text.',
  '> Ron: Invalid history', 'Invalid date.',
  '[agent-read-request:smoke-card]', 'After a card.',
  '| Name | Value |', '| --- | --- |', '| Result | Good |',
  'After the table.', 'Next day reply.'];
const times = ['2026-11-05T07:25:00Z', '2026-11-05T07:26:00Z',
  t('27'), t('28'), t('29'), t('30'), null, t('31'), t('32'),
  t('33'), null, t('34'), 'bad-date', t('35'), t('36'), t('37'), t('37'), t('37'), t('38'),
  '2026-11-06T08:01:00Z'];
const project = { id: pid, name: 'Agent times', status: 'active', domain: 'general', emoji: '🕒',
  description: '', summary: '', current_task: 'Idle', next_action: '', blocked: false,
  activity_log: [], backlog: [], project_path: '/smoke/agent-times', last_updated: t('27'),
  display_order: 0, provider: 'claude', use_streaming_agent: true };
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    const ctx = await browser.newContext({ viewport: { width, height: 1000 },
      timezoneId: 'America/Los_Angeles', hasTouch: width === 390 });
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
    await page.route('**/*', route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
      if (path === '/api/projects') return route.fulfill({ json: [project] });
      if (path === '/api/config') return route.fulfill({ json: {} });
      if (path === '/api/characters') return route.fulfill({ json: [] });
      if (path.endsWith('/session/restored-agent-times/reconstruct')) return route.fulfill({ json: {
        log_lines: lines, log_line_ts: times, task: 'Restored agent times',
        started_at: times[0], claude_session_id: 'restored-native-times',
      } });
      if (path.endsWith('/transcript/older-part/full-buffer')) return route.fulfill({ json: {
        log_lines: ['> Ron: Earlier part', 'Earlier part reply.'],
        log_line_ts: ['2026-11-04T16:00:00Z', '2026-11-04T16:01:00Z'],
      } });
      if (route.request().url().startsWith('https://cdn.jsdelivr.net/npm/mermaid@')) {
        return route.fulfill({ contentType: 'text/javascript', body: 'export default {initialize(){},async run(){}};' });
      }
      return route.abort();
    });
    await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(({ pid, sid, lines, times }) => {
      agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Agent times',
        task: 'Agent times', status: 'idle', startedAt: times[0] });
      agentStatusCache[sid] = { status: 'idle', task: 'Agent times', projectId: pid,
        startedAt: times[0], claudeSessionId: 'native-agent-times' };
      agentOutputBuffers[sid] = lines.slice();
      agentOutputTimestamps[sid] = times.slice();
      conversationsCache[pid] = [{ claude_session_id: 'native-agent-times', mc_session_id: sid,
        character: null, status: 'idle', turns: 5, label: 'Agent times', mtime: 1000 }];
      openProjectModal(pid);
      openConversation(pid, 'native-agent-times', sid, true);
    }, { pid, sid, lines, times });
    const out = page.locator(`#agent-output-${sid}`);
    await out.waitFor();
    const tags = () => out.locator('.agent-time-tag').evaluateAll(nodes => nodes.map(n => ({
      label: n.dataset.label, iso: n.dateTime, title: n.title, host: n.parentElement.textContent,
    })));
    let cold = await tags();
    assert.deepEqual(cold.map(n => n.label), ['23:26', '09:28', '09:31', '09:36', '00:01']);
    assert.equal(cold[2].host, 'Undated start.');
    assert.equal(cold[2].iso, t('31').replace('Z', '.000Z'));
    assert.match(cold[2].title, /11\/5\/2026.*9:31:00 AM/);
    assert.equal(await out.locator('.chat-date-divider').count(), 3);
    console.log(`PASS ${width}: cold run grouping, first valid timestamp, adjacent replies share tag, null/invalid, local day/card/tool boundaries`);

    await page.evaluate(sid => _repaintAgentOutput(sid), sid);
    assert.deepEqual(await tags(), cold, 'replay matches cold render exactly');
    assert.deepEqual(await page.evaluate(sid => agentOutputBuffers[sid], sid), lines);
    // Both Codex and Qwen reconstruction use this provider-independent buffer contract.
    for (const provider of ['codex', 'qwen']) {
      await page.evaluate(({ sid, provider }) => {
        agentStatusCache[sid].provider = provider;
        _repaintAgentOutput(sid);
      }, { sid, provider });
      assert.deepEqual(await tags(), cold, `${provider} transcript replay`);
    }
    const legacy = await page.evaluate(({ pid }) => {
      const id = 'legacy-agent-times';
      agentStatusCache[id] = { projectId: pid, startedAt: '2026-11-05T17:00:00Z' };
      agentOutputBuffers[id] = ['Undated legacy reply.'];
      const host = document.createElement('div'); host.id = `agent-output-${id}`;
      document.body.appendChild(host); _repaintAgentOutput(id);
      const count = host.querySelectorAll('time').length; host.remove(); return count;
    }, { pid });
    assert.equal(legacy, 0, 'session anchor never dates a reply');
    console.log(`PASS ${width}: replay, Codex/Qwen buffers, unchanged source, legacy session fallback excluded`);

    await page.evaluate(({ pid, sid }) => openChatSearch(pid, sid), { pid, sid });
    await page.locator(`#chat-search-input-${sid}`).fill('First valid line');
    await page.waitForFunction(sid => document.getElementById(`chat-search-count-${sid}`)?.textContent.includes('1 of 1'), sid);
    assert.equal(await out.locator('mark.mc-chat-search-current').textContent(), 'First valid line');
    assert.deepEqual(await tags(), cold);
    await page.evaluate(sid => closeChatSearch(sid), sid);

    // Native select/copy/paste of a whole tagged bubble, including its <time>.
    const copied = await out.locator('[data-agent-narration]').filter({ hasText: 'AGENT first paragraph.' }).evaluate(el => {
      const range = document.createRange(); range.selectNodeContents(el);
      const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range);
      return { selection: sel.toString(), inner: el.innerText, text: el.textContent };
    });
    assert.deepEqual(copied, { selection: 'AGENT first paragraph.', inner: 'AGENT first paragraph.', text: 'AGENT first paragraph.' });
    const mod = process.platform === 'darwin' ? 'Meta' : 'Control';
    await page.keyboard.press(`${mod}+c`);
    const composer = page.locator(`#agent-followup-${sid}`);
    await composer.click(); await page.keyboard.press(`${mod}+v`);
    assert.equal(await composer.inputValue(), 'AGENT first paragraph.');
    await composer.fill('');
    await page.evaluate(() => getSelection().removeAllRanges());
    console.log(`PASS ${width}: chat search and native copy/paste exclude metadata`);

    await page.evaluate(({ pid, sid }) => connectAgentStream(pid, sid), { pid, sid });
    const emit = msg => page.evaluate(({ sid, msg }) => {
      const stream = smokeStreams.find(s => s.url.includes(sid));
      if (!stream?.onmessage) throw new Error('stream handler missing');
      stream.onmessage({ data: JSON.stringify(msg) });
    }, { sid, msg });
    await emit({ type: 'output', text: '> Ron: Streaming', ts: t('40') });
    await emit({ type: 'output', text: 'Live first line.', ts: t('41') });
    await out.locator('.agent-time-tag').last().evaluate(el => { window.stableAgentTime = el; });
    await emit({ type: 'output', text: 'Live second line.\nLive third line.', ts: t('42') });
    assert.equal((await tags()).at(-1).label, '09:41');
    assert.equal((await tags()).at(-1).host, 'Live first line.');
    assert(await page.evaluate(() => stableAgentTime.isConnected), 'streaming does not replace or move the first tag');
    await emit({ type: 'output', text: '[tool: Read]', ts: t('43') });
    await emit({ type: 'output', text: 'Live undated line.' });
    const beforeValid = (await tags()).length;
    await emit({ type: 'output', text: 'Live later dated line.', ts: t('44') });
    assert.equal((await tags()).length, beforeValid + 1);
    assert.equal((await tags()).at(-1).host, 'Live undated line.');
    assert.equal((await tags()).at(-1).label, '09:44');
    await emit({ type: 'output', text: '> Ron: No timestamps anywhere', ts: t('45') });
    await emit({ type: 'output', text: 'Unknown live reply.' });
    await emit({ type: 'output', text: 'Invalid live timestamp.', ts: 'bad-date' });
    assert.equal((await tags()).length, beforeValid + 1);
    const live = await tags();
    await page.evaluate(sid => _repaintAgentOutput(sid), sid);
    assert.deepEqual(await tags(), live, 'buffer reconstruction retains streaming timestamps');
    console.log(`PASS ${width}: real SSE, stable first tag, null then valid, entirely undated run and reconstruction`);

    await page.evaluate(({ pid, sid }) => {
      conversationsCache[pid][0].rolled_from = ['older-part'];
      _restoreRolloverButton(sid);
    }, { pid, sid });
    await page.evaluate(({ pid, sid }) => loadEarlierConversationPart(pid, sid), { pid, sid });
    assert.equal((await tags())[0].label, '08:01');
    assert.equal((await tags())[0].host, 'Earlier part reply.');
    assert.deepEqual((await tags()).slice(1), live);
    console.log(`PASS ${width}: rollover full-buffer replay preserves own timestamps`);

    await page.evaluate(sid => {
      hideTypingIndicator(sid);
      document.getElementById(`agent-output-${sid}`).scrollTop = 0;
    }, sid);
    const bounds = await out.evaluate(el => ({ width: el.clientWidth, scroll: el.scrollWidth,
      visible: [...el.querySelectorAll('.agent-time-tag')].every(t => {
        const r = t.getBoundingClientRect(), b = t.parentElement.getBoundingClientRect();
        return r.width > 0 && r.left >= b.left && r.right <= b.right;
      }) }));
    assert(bounds.visible && bounds.scroll <= bounds.width + 1, JSON.stringify(bounds));
    assert.deepEqual(errors, []);
    await page.screenshot({ path: resolve(shots, `chat-${width}.png`) });
    console.log(`PASS ${width}: bounds/page errors and screenshot _scratch/agent_time_tag/chat-${width}.png`);

    // Timestamped rendering regressions: these must exercise tags alongside
    // wrappers/cards, rather than only running legacy undated fixtures.
    const draft = 'The reply timestamp uses the first recorded line in a contiguous narration run and stays fixed while additional paragraphs stream into the conversation.';
    const regressionLines = ['> Ron: Draft', draft, '[stop-hook-redo:length]', draft,
      '> Ron: Plan', 'First plan step.', 'Second plan step.', '[tool: ExitPlanMode]',
      '> Ron: Diagram', 'Before diagram.', '```mermaid', 'flowchart LR', 'A --> B', '```', 'After diagram.',
      '> Ron: Attachments', 'Download [file:/smoke/report.md|Report]', '/smoke/preview.png', '[browser:https://example.com]',
      '> Ron: Table', '| Name | Value |', '| --- | --- |', '| Result | Good |'];
    const regressionTimes = regressionLines.map((_, i) => t(String(10 + i).padStart(2, '0')));
    const renderFixture = async () => page.evaluate(({ pid, sid, regressionLines, regressionTimes }) => {
      agentOutputBuffers[sid] = regressionLines.slice();
      agentOutputTimestamps[sid] = regressionTimes.slice();
      agentStatusCache[sid].provider = 'claude';
      agentStatusCache[sid].waitingForPlanApproval = false;
      document.getElementById(`agent-output-${sid}`).remove();
      refreshModal();
    }, { pid, sid, regressionLines, regressionTimes });
    await renderFixture();
    assert.equal(await out.locator('.draft-block .agent-time-tag').count(), 1);
    assert.equal(await out.locator('.draft-block').evaluate(el => el.open), false);
    await out.locator('.draft-block summary').click();
    assert.equal(await out.locator('.draft-block').evaluate(el => el.open), true);
    assert.equal(await out.locator('.plan-show-btn').count(), 1);
    assert.equal(await out.locator('.plan-hidden-block .agent-time-tag').count(), 1);
    assert.equal(await out.locator('.mermaid-block').getAttribute('data-source'), 'flowchart LR\nA --> B');
    assert.equal(await out.locator('.hl-file-link').getAttribute('title'), '/smoke/report.md');
    assert.equal(await out.locator('.agent-img-wrap').count(), 1);
    assert((await out.innerText()).includes('[browser:'), 'browser marker preserved for its existing enhancer');
    const coldRegressions = await tags();
    await page.evaluate(sid => _repaintAgentOutput(sid), sid);
    assert.deepEqual(await tags(), coldRegressions, 'tagged draft/plan/diagram/table live replay equals cold');
    assert.equal(await out.locator('.draft-block .agent-time-tag').count(), 1);
    assert.equal(await out.locator('.mermaid-block').getAttribute('data-source'), 'flowchart LR\nA --> B');
    // Growing tables repaint their bodies; metadata must survive that paint.
    await page.evaluate(({ sid, ts }) => appendAgentLine(sid, '| New row | More |', ts), { sid, ts: t('59') });
    const metadata = rows => rows.map(({ host, ...time }) => time);
    assert.deepEqual(metadata(await tags()), metadata(coldRegressions));
    assert.match((await tags()).at(-1).host, /New rowMore/);
    // Question cards are DOM-delivered rather than buffered; dedupe remains
    // DOM-based, and the next narration starts its own run.
    await page.evaluate(({ pid, sid, ts }) => {
      const qs = [{ header: 'Choice', question: 'Continue?', options: [{ label: 'Yes' }, { label: 'No' }] }];
      renderAgentQuestion(sid, pid, qs, 'smoke-time-question');
      renderAgentQuestion(sid, pid, qs, 'smoke-time-question');
      appendAgentLine(sid, 'After question card.', ts);
    }, { pid, sid, ts: t('58') });
    assert.equal(await out.locator('.agent-question[data-qid="smoke-time-question"]').count(), 1);
    assert.equal((await tags()).at(-1).label, '09:58');
    assert.equal((await tags()).at(-1).host, 'After question card.');
    assert.deepEqual(errors, []);
    console.log(`PASS ${width}: tagged draft expand, plan collapse, diagrams, file/image/browser markers, table repaint and question-card dedupe/boundary`);
    await page.evaluate(pid => openConversation(pid, 'restored-native-times', 'restored-agent-times', false), pid);
    const restored = page.locator('#agent-output-restored-agent-times');
    await restored.waitFor();
    assert.deepEqual(await restored.locator('.agent-time-tag').evaluateAll(nodes => nodes.map(n => ({
      label: n.dataset.label, iso: n.dateTime, title: n.title, host: n.parentElement.textContent,
    }))), cold, 'real reconstruct fetch adopts timestamps and renders the same tags');
    assert.deepEqual(errors, []);
    console.log(`PASS ${width}: actual openConversation reconstruct API path restores line timestamps`);
    await ctx.close();
  }
} finally { await browser.close(); }
