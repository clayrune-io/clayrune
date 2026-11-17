// MC-1183 / WB1: real builder, open inspector, hermetic HTTP and static assets.
// The ordinary canvas smoke closes the inspector before its second drop.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const assets = loadStaticJsCss(root);
const origin = 'http://mc.smoke.test';
const pid = 'smoke_persona';
const projects = [pid, 'slow_project'].map(id => ({
  id, name: id, status: 'active', domain: 'general', description: '',
  summary: '', current_task: 'Idle', next_action: '', activity_log: [], backlog: [],
  project_path: '/smoke/' + id, provider: 'claude', use_streaming_agent: true,
}));
const people = ['builder', 'reviewer'].map(name => ({
  name, scope: 'global', agent_name: name, display_name: name,
  avatar: '', engine: { provider: 'claude' },
}));
const workflow = {
  id: 'wf-persona', format: 2, name: 'Persona sync', description: '', enabled: false,
  trigger: { type: 'manual' }, edges: [],
  nodes: [{ name: 'original', type: 'agent', project_id: pid,
    character: 'global:builder', prompt: 'Original prompt', outcomes: [], x: 250, y: 200 }],
};
let releaseCharacters;
const delayedCharacters = new Promise(resolve => { releaseCharacters = resolve; });
const errors = [];
let passed = 0, failed = 0;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
page.setDefaultTimeout(5000);
page.on('pageerror', e => errors.push(e.message));
page.on('dialog', d => d.accept());
await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
await page.route('**/*', async route => {
  const req = route.request(), url = new URL(req.url()), path = url.pathname;
  const json = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  if (req.url() === 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs') {
    return route.fulfill({ contentType: 'text/javascript', body: 'export default { initialize() {} };' });
  }
  if (path === '/') return route.fulfill({ contentType: 'text/html', body: readFileSync(resolve(root, 'static/index.html'), 'utf8') });
  if (assets[path]) return route.fulfill({ contentType: assets[path][0], body: assets[path][1] });
  if (path === '/api/projects') return json(projects);
  if (path === '/api/config') return json({});
  if (path === '/api/floor') return json({ rooms: [], quiet: [], counts: {},
    bench: people.map(p => ({ ...p, display: p.name, provider: 'claude', rooms: [] })) });
  if (path === '/api/characters') {
    if (url.searchParams.get('project_id') === 'slow_project') {
      await delayedCharacters;
    }
    return json(people);
  }
  if (path === '/api/workflows') return json([workflow]);
  if (path === '/api/workflows/wf-persona') return json(workflow);
  if (path === '/api/schedules') return json([]);
  if (path === `/api/project/${pid}/workflows`) return json({ workflows: [] });
  if (path === `/api/project/${pid}/agent/log` || path === `/api/project/${pid}/conversations`) return json([]);
  // No live requests or writes: every unneeded API/CDN request is aborted.
  return route.abort();
});

async function ready() {
  await page.waitForFunction(() => !!document.querySelector('#wfb-inspector .wfb-persona option[value="global:builder"]'));
}

async function reset(editPrompt = true) {
  await page.evaluate(async pid => {
    window._wfLearn.discard(pid);
    await window.openWorkflowBuilder('wf-persona', pid);
    window._wfOpenInspector('original');
  }, pid);
  await ready();
  await page.evaluate(() => window._wfTogglePromptOpen(null, 'original'));
  await ready();
  if (editPrompt) await page.locator('#wfb-inspector .wfb-prompt').fill('Keep this typed prompt');
}

async function preserved(expectedCount = 2, character = 'global:builder', prompt = 'Keep this typed prompt') {
  const state = await page.evaluate(() => window._wfEntry()._wf.def);
  assert.equal(state.nodes.length, expectedCount);
  assert.equal(state.nodes.find(n => n.name === 'original').character, character);
  assert.equal(state.nodes.find(n => n.name === 'original').prompt, prompt);
  return state;
}

async function check(label, action) {
  try { await action(); passed++; console.log('PASS ' + label); }
  catch (e) { failed++; console.error('FAIL ' + label + ': ' + e.message); }
}

async function dragPerson() {
  const row = page.locator('.wfb-palette-person').filter({ hasText: 'reviewer' }).first();
  await row.scrollIntoViewIfNeeded();
  const box = await row.boundingBox();
  const target = await page.evaluate(() => {
    const vp = document.getElementById('wfb-canvas-viewport'), r = vp.getBoundingClientRect();
    for (let y = r.bottom - 40; y > r.top + 30; y -= 30) {
      for (let x = r.right - 40; x > r.left + 30; x -= 30) {
        const hit = document.elementFromPoint(x, y);
        if (hit && vp.contains(hit) && !hit.closest('.wfb-node, .wfb-trigger-box')) return { x, y };
      }
    }
    throw new Error('No visible empty canvas');
  });
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(target.x, target.y, { steps: 15 });
  await page.mouse.up();
}

try {
  await page.goto(origin, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card');
  await page.evaluate(pid => {
    window.openProjectModal(pid);
    const win = document.querySelector(`.modal-window[data-modal-id="${pid}"]`);
    win.querySelector('.modal-content').style.width = '1480px';
    window.centerModalElement(win);
    window.switchModalTab(pid, 'workflows');
  }, pid);
  await page.waitForSelector(`#wfb-inline-host-${pid}`);

  await check('palette drag preserves the open step persona and typed prompt', async () => {
    await reset();
    await dragPerson();
    await ready();
    const state = await preserved();
    assert.equal(state.nodes[1].character, 'global:reviewer');
  });

  for (const from of ['original', '__trigger__']) {
    await check(`${from} port insertion preserves the open step persona`, async () => {
      // Trigger + separately skips syncing typed fields (WB6, out of scope).
      // Here assert WB1 against an already-stored prompt and persona.
      await reset(from !== '__trigger__');
      await page.evaluate(from => {
        window._wfPortPlusClick({ stopPropagation() {}, currentTarget: document.getElementById('wfb-canvas-viewport') }, from, null);
      }, from);
      await page.locator('.wfb-popover-person').filter({ hasText: 'reviewer' }).click();
      await ready();
      const state = await preserved(2, 'global:builder', from === '__trigger__' ? 'Original prompt' : 'Keep this typed prompt');
      assert.equal(state.nodes[1].character, 'global:reviewer');
      if (from === 'original') assert.equal(state.edges[0].from, 'original');
      else assert.ok(state.trigger.entry.includes(state.nodes[1].name));
    });
  }

  await check('duplication preserves both the original and copied persona', async () => {
    await reset();
    await page.evaluate(() => window._wfDuplicateNode('original'));
    await ready();
    const state = await preserved();
    assert.equal(state.nodes[1].character, 'global:builder');
    assert.equal(state.nodes[1].prompt, 'Keep this typed prompt');
  });

  await check('a deliberate persona change still syncs', async () => {
    await reset();
    await page.locator('#wfb-inspector .wfb-persona').selectOption('global:reviewer');
    await page.evaluate(() => window._wfDuplicateNode('original'));
    await ready();
    const state = await preserved(2, 'global:reviewer');
    assert.equal(state.nodes[1].character, 'global:reviewer');
  });

  await check('deliberately choosing Project default still clears the persona', async () => {
    await reset();
    await page.locator('#wfb-inspector .wfb-persona').selectOption('');
    await page.evaluate(() => window._wfDuplicateNode('original'));
    await ready();
    const state = await preserved(2, '');
    assert.equal(state.nodes[1].character, '');
  });

  await check('a pending project roster cannot clear the persona on inspector close', async () => {
    await reset();
    const requested = page.waitForRequest(req => {
      const url = new URL(req.url());
      return url.pathname === '/api/characters' && url.searchParams.get('project_id') === 'slow_project';
    });
    await page.locator('#wfb-inspector .wfb-project').selectOption('slow_project');
    await requested;
    assert.equal(await page.locator('#wfb-inspector .wfb-persona').inputValue(), '');
    await page.evaluate(() => window._wfCloseInspector());
    await preserved(1);
    releaseCharacters();
  });

  await check('no uncaught page errors', async () => assert.deepEqual(errors, []));
} finally {
  releaseCharacters();
  await browser.close();
}
console.log(`workflow-persona-sync: ${passed} passed, ${failed} failed`);
process.exitCode = failed ? 1 : 0;
