#!/usr/bin/env node
// Real conversation live + cold render, using this checkout's SPA and no API writes.
import { chromium } from 'playwright';
import { readFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadStaticJsCss } from './_static.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const STATIC = loadStaticJsCss(ROOT);
const HTML = readFileSync(resolve(ROOT, 'static/index.html'), 'utf8');
const shots = resolve(ROOT, '_scratch/denied-chip');
mkdirSync(shots, { recursive: true });
const PID = 'denied_smoke';
const project = { id: PID, name: 'Transcript preview', status: 'active', domain: 'general',
  description: '', summary: '', current_task: 'Idle', next_action: '', activity_log: [], backlog: [],
  project_path: '/smoke', provider: 'claude', use_streaming_agent: true, display_order: 0 };
const XSS = '<img src=x onerror="window.deniedXss=1">';
const FIRST = '[denied: Bash] curl https://example.test/permission-check';
const SECOND = `[denied: Read] ${XSS} ${'long-path/'.repeat(22)}file.txt`;
let checks = 0;
function check(condition, message) {
  if (!condition) throw new Error(message);
  checks++;
  console.log('PASS ' + message);
}
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) for (const mode of ['live', 'cold']) for (const count of [1, 2]) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, hasTouch: width === 390 });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => {
      if (route.request().url().includes('/mermaid@')) return route.fulfill({
        contentType: 'text/javascript', body: 'export default { initialize() {}, run: async () => {} };' });
      const path = new URL(route.request().url()).pathname;
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: HTML });
      if (STATIC[path]) return route.fulfill({ contentType: STATIC[path][0], body: STATIC[path][1] });
      const data = path === '/api/projects' ? [project] : path === '/api/config' ? {} : path === '/api/characters' ? [] : null;
      if (data) return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
      return route.abort();
    });
    await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    const lines = ['> Ron: Check the permission boundary.', 'The check finished. Details are available below.',
      FIRST, ...(count === 2 ? [SECOND] : [])];
    await page.evaluate(({ pid, mode, lines }) => {
      const sid = 'denied_test';
      agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Transcript preview', task: 'Permission check', status: 'completed', startedAt: new Date().toISOString() });
      agentStatusCache[sid] = { projectId: pid, status: 'completed', task: 'Permission check', startedAt: new Date().toISOString() };
      agentOutputBuffers[sid] = mode === 'cold' ? lines : [];
      activeAgentTab[pid] = sid;
      openProjectModal(pid);
      activeAgentTab[pid] = sid;
      refreshModal();
      if (mode === 'live') lines.forEach(line => appendAgentLine(sid, line));
    }, { pid: PID, mode, lines });
    const out = page.locator('#agent-output-denied_test');
    const chip = out.locator('.denied-chip');
    const label = count === 1 ? '1 command blocked' : '2 commands blocked';
    const tag = `${mode} ${width}px ${count} denial(s)`;
    check(await chip.count() === 1, tag + ': one chip');
    check(await chip.locator('summary').innerText() === label, tag + ': count label');
    check(!await chip.evaluate(el => el.open), tag + ': closed by default');
    check(!await chip.locator('li').first().isVisible(), tag + ': commands hidden');
    check(await out.locator('.agent-line').filter({ hasText: '[denied:' }).count() === 0, tag + ': no raw denial bubble');
    if (mode === 'cold' && count === 2) {
      await out.scrollIntoViewIfNeeded();
      await page.screenshot({ path: resolve(shots, `collapsed-${width}.png`) });
    }
    if (width === 390) await chip.locator('summary').tap();
    else await chip.locator('summary').click();
    check(await chip.evaluate(el => el.open) && await chip.locator('li').count() === count, tag + ': expands');
    check(await chip.locator('code').first().innerText() === FIRST.split('] ')[1], tag + ': command preserved');
    if (count === 2) {
      check((await chip.locator('code').nth(1).innerText()).includes(XSS), tag + ': XSS remains literal text');
      check(await chip.locator('img').count() === 0 && !await page.evaluate(() => window.deniedXss), tag + ': no injected DOM or script');
    }
    check(await chip.evaluate(el => el.scrollWidth <= el.clientWidth && el.getBoundingClientRect().right <= innerWidth), tag + ': fits viewport');
    if (mode === 'cold' && count === 2) await page.screenshot({ path: resolve(shots, `expanded-${width}.png`) });
    await chip.locator('summary').focus();
    await page.keyboard.press('Enter');
    check(!await chip.evaluate(el => el.open), tag + ': keyboard collapses');

    // A cold trailing run can grow on the live path, including after opening.
    await page.evaluate(line => appendAgentLine('denied_test', line), SECOND);
    check(await chip.locator('li').count() === count + 1, tag + ': live continuation joins trailing run');
    // Mentions, blank lines and invisible stop markers must break consecutive runs.
    const separators = ['A mention of [denied: Bash] stays prose.', '', '[stop-hook-redo:other]'];
    for (const separator of separators) {
      await page.evaluate(({ separator, first }) => {
        appendAgentLine('denied_test', separator);
        appendAgentLine('denied_test', first);
      }, { separator, first: FIRST });
    }
    check(await chip.count() === 4, tag + ': each separator starts a fresh run');
    check((await out.innerText()).includes(separators[0]), tag + ': mid-sentence marker stays prose');
    // Exercise the same separators through the actual history rebuild.
    await page.evaluate(({ first, separators }) => {
      agentOutputBuffers.denied_test = [first, ...separators.flatMap(s => [s, first])];
      refreshModal();
    }, { first: FIRST, separators });
    check(await out.locator('.denied-chip').count() === 4, tag + ': cold separators match live');
    check(errors.length === 0, tag + ': no runtime errors: ' + errors.join(' | '));
    await context.close();
  }
  console.log(`PASS denied-chip: ${checks} checks; screenshots ${shots}`);
} finally {
  await browser.close();
}
