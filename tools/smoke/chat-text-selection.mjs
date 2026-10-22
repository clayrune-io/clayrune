#!/usr/bin/env node
// Real mouse gestures against this checkout's SPA, cold/live output, modal
// refresh and server reconciliation. No synthetic Selection/Range writes.
import assert from 'node:assert/strict';
import { existsSync, readFileSync, mkdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { chromium, webkit } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const files = loadStaticJsCss(root);
let html = readFileSync(resolve(root, 'static/index.html'), 'utf8');
// Optional negative control: serve the old implementation without modifying
// the working tree, and require the same real gesture to survive its updates.
const baseline = process.argv.find(arg => arg.startsWith('--baseline='))?.slice(11);
if (baseline) {
  for (const name of ['index.html', 'js/conversation.js', 'js/resume-preview.js', 'js/rich-text.js']) {
    const body = execFileSync('git', ['show', `${baseline}:static/${name}`], { cwd: root, encoding: 'utf8' });
    if (name === 'index.html') html = body;
    else files[`/static/${name}`][1] = body;
  }
}
const pid = 'selection';
const lines = ['> Ron: USER highlight this sentence and copy it.',
  'AGENT highlight this answer and keep reading.',
  'CODE `const result = value + 1;` remains selectable.',
  'END another message for cross-bubble selection.'];
const project = { id: pid, name: 'Selection smoke', status: 'active', domain: 'general',
  emoji: 'S', description: '', summary: '', current_task: '', next_action: '',
  activity_log: [], backlog: [], project_path: '/smoke', provider: 'codex',
  use_streaming_agent: true, display_order: 0 };
const shotDir = resolve(root, '_scratch/mac-chat-selection');
mkdirSync(shotDir, { recursive: true });

for (const [engine, type] of [['chromium', chromium], ['webkit', webkit]]) {
  if (!existsSync(type.executablePath())) {
    console.log(`SKIP ${engine}: installed executable absent: ${type.executablePath()}`);
    continue;
  }
  const browser = await type.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    let serverSessions = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => {
      const path = new URL(route.request().url()).pathname;
      // Diagram CDN modules are unrelated to selection; keep the test offline.
      if (route.request().resourceType() === 'script' && !route.request().url().startsWith('http://selection.smoke.test/')) {
        return route.fulfill({ contentType: 'text/javascript', body: 'export default {initialize(){}};' });
      }
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
      const stubs = { '/api/projects': [project], '/api/config': {},
        '/api/engine/preferences': { providers: [] }, '/api/characters': [],
        [`/api/project/${pid}/agent/status`]: { sessions: serverSessions } };
      if (path in stubs) return route.fulfill({ contentType: 'application/json', body: JSON.stringify(stubs[path]) });
      return route.abort();
    });
    await page.goto('http://selection.smoke.test/');
    await page.waitForFunction(() => window.appendAgentLine && window._repaintAgentOutput && window.openProjectModal);
    await page.evaluate(project => {
      allProjects.push({ ...project, id: 'other-selection', name: 'Other project' });
      openProjectModal('other-selection');
      // Keep its real modal mounted outside the gesture area.
      openModals.get('other-selection').element.style.left = '2000px';
    }, project);
    const settle = () => page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
    const selected = () => page.evaluate(() => window.getSelection().toString());
    async function clear() { await page.mouse.click(8, 8); await settle(); }
    // Range is used ONLY to locate text glyphs, never to create the selection.
    async function point(selector, word, end = false) {
      return page.evaluate(({ selector, word, end }) => {
        const el = document.querySelector(selector);
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
          const at = node.textContent.indexOf(word);
          if (at < 0) continue;
          const range = document.createRange();
          range.setStart(node, at); range.setEnd(node, at + word.length);
          const rect = range.getBoundingClientRect();
          return { x: end ? rect.right - 1 : rect.left + 1, y: rect.top + rect.height / 2 };
        }
        throw Error(`Missing text ${word}`);
      }, { selector, word, end });
    }
    async function drag(selector, from, to, during = async () => {}) {
      const a = await point(selector, from), b = await point(selector, to, true);
      await page.mouse.move(a.x, a.y); await page.mouse.down();
      await during();
      await page.mouse.move(b.x, b.y, { steps: 18 }); await page.mouse.up();
      await settle();
      const text = await selected();
      assert(text.includes(from) && text.includes(to), `Gesture selected ${JSON.stringify(text)}`);
      return text;
    }
    for (const mode of ['cold', 'live', 'reopened']) {
      const sid = `selection-${mode}`;
      await page.evaluate(({ pid, sid, mode, lines }) => {
        closeModalById(pid);
        const rec = { projectId: pid, sessionId: sid, task: 'Selection', status: 'completed', provider: 'codex', startedAt: new Date().toISOString() };
        agentHistory.unshift(rec); agentStatusCache[sid] = { ...rec };
        agentOutputBuffers[sid] = mode === 'live' ? [] : lines.slice();
        agentOutputTimestamps[sid] = []; activeAgentTab[pid] = sid;
        openProjectModal(pid); activeAgentTab[pid] = sid; refreshModal();
        if (mode === 'live') for (const line of lines) { agentOutputBuffers[sid].push(line); appendAgentLine(sid, line, false); }
        if (mode === 'reopened') { closeModalById(pid); openProjectModal(pid); activeAgentTab[pid] = sid; refreshModal(); }
      }, { pid, sid, mode, lines });
      const sel = `#agent-output-${sid}`;
      await page.waitForSelector(sel); await settle();
      for (const [start, end] of [['USER', 'sentence'], ['AGENT', 'reading.'], ['const', '1;'], ['USER', 'END']]) {
        const text = await drag(sel, start, end, () => page.evaluate(pid => refreshModalById(pid), pid));
        const recovered = await page.evaluate(({ pid, sid }) => {
          window.__selectedNode = document.querySelector(`#agent-output-${sid}`);
          refreshModalById(pid);
          agentLogEpoch[sid] = 0;
          return [...agentOutputBuffers[sid], 'RECOVERED during selection'];
        }, { pid, sid });
        serverSessions = [{ session_id: sid, status: 'completed', log_lines: recovered, log_epoch: 1 }];
        await page.evaluate(async ({ pid, sid }) => {
          await _reconcileAgentBuffer(pid, sid);
          appendAgentLine(sid, 'STREAM keeps arriving', false);
        }, { pid, sid });
        await settle();
        assert.equal(await selected(), text, `${engine}/${mode}: refresh/reconcile/stream keeps selection`);
        assert(await page.evaluate(() => {
          const other = openModals.get('other-selection').element;
          const before = other.querySelector('.modal-content').firstElementChild;
          refreshModalById('other-selection');
          return before !== other.querySelector('.modal-content').firstElementChild;
        }), 'selection does not defer an unrelated project modal');
        assert.equal(await selected(), text, 'unrelated modal refresh keeps selected chat text');
        assert(await page.evaluate(sid => document.querySelector(`#agent-output-${sid}`) === window.__selectedNode, sid));
        if (start === 'USER' && end === 'END') await page.screenshot({ path: resolve(shotDir, `${engine}-${mode}.png`) });
        await page.keyboard.press('Control+c');
        const composer = page.locator(`#agent-followup-${sid}`);
        await composer.click(); await page.keyboard.press('Control+v');
        assert.equal(await composer.inputValue(), text, 'native copy/paste matches the dragged text');
        await composer.fill('');
        await clear();
        assert((await page.locator(sel).innerText()).includes('RECOVERED during selection'), 'deferred reconcile flushed after clearing');
      }
      const existingText = await drag(sel, 'AGENT', 'reading.');
      await page.evaluate(sid => {
        for (const row of ['| NEWTABLE | value |', '| --- | --- |', '| ROWA | one |']) appendAgentLine(sid, row, false);
      }, sid);
      assert.equal(await selected(), existingText, 'new table does not disturb earlier selected text');
      await clear();
      assert.equal(await page.locator(`${sel} .hl-table`).filter({ hasText: 'NEWTABLE' }).count(), 1);
      assert((await page.locator(`${sel} .hl-table`).filter({ hasText: 'NEWTABLE' }).innerText()).includes('ROWA'), 'deferred new table groups all incoming rows');
      await page.evaluate(sid => appendAgentLine(sid, 'Table boundary', false), sid);
      // Growing pipe tables replace their text nodes too.
      await page.evaluate(sid => {
        appendAgentLine(sid, '| TABLESELECT | value |', false);
        appendAgentLine(sid, '| --- | --- |', false);
        appendAgentLine(sid, '| FIRSTROW | one |', false);
      }, sid);
      await settle();
      const tableText = await drag(sel, 'TABLESELECT', 'FIRSTROW');
      await page.evaluate(sid => appendAgentLine(sid, '| NEWROW | two |', false), sid);
      await settle();
      assert.equal(await selected(), tableText, 'growing table preserves selection');
      await clear();
      assert((await page.locator(sel).innerText()).includes('NEWROW'), 'table refresh catches up after clear');
      await drag(sel, 'AGENT', 'reading.');
      // An intentional navigation takes precedence over the old highlight.
      await page.evaluate(pid => { newAgentTab(pid); }, pid);
      assert.equal(await page.locator(sel).count(), 0, 'new chat navigates despite old selection');
      await page.evaluate(({ pid, sid }) => { activeAgentTab[pid] = sid; agentConvNew[pid] = false; refreshModal(); }, { pid, sid });
      await page.waitForSelector(sel); await settle();
      // A queued rAF pin and a new burst must not move a nearly-bottom reader.
      await page.evaluate(sid => {
        const el = document.getElementById(`agent-output-${sid}`);
        el.style.height = '180px'; el.style.flex = 'none';
        for (let i = 0; i < 25; i++) appendAgentLine(sid, `FILLER ${i}`, false);
      }, sid);
      await settle();
      await page.evaluate(sid => {
        const el = document.getElementById(`agent-output-${sid}`);
        el.scrollTop = el.scrollHeight - el.clientHeight - 35;
      }, sid);
      const a = await point(sel, 'FILLER 23');
      const before = await page.locator(sel).evaluate(el => el.scrollTop);
      // Force a queued pin callback too, so checking only the append-time
      // near-bottom decision cannot make this pass.
      await page.evaluate(sid => document.addEventListener('mousedown', () => {
        _scheduleAgentPinScroll(sid, document.getElementById(`agent-output-${sid}`), false);
      }, { once: true }), sid);
      await page.mouse.move(a.x, a.y); await page.mouse.down();
      await page.evaluate(sid => { for (let i = 0; i < 12; i++) appendAgentLine(sid, `BURST ${i}`, false); }, sid);
      await settle();
      assert.equal(await page.locator(sel).evaluate(el => el.scrollTop), before, 'stream does not autoscroll during mouse-down');
      await page.mouse.move(a.x + 50, a.y, { steps: 8 }); await page.mouse.up();
      await clear();
      console.log(`PASS ${engine}/${mode}: 4 drags + copy/paste, new/growing tables, modal refresh, status/epoch reconcile, stream/queued scroll`);
    }
    assert.deepEqual(errors.filter(e => !/fetch|aborted|EventSource/i.test(e)), []);
  } finally { await browser.close(); }
}
