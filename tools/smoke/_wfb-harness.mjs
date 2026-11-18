// Shared harness for the workflow-builder defect smokes (MC-1179 WB2/WB3/WB4).
// Real SPA in headless Chromium, every request answered in-process (no server,
// no network). The workflow/schedule stores are in memory so a Save round-trips:
// POST/PUT /api/workflows return the stored record with an id, schedule POSTs
// are counted. Writes are recorded in `rec` so a smoke can assert on them.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
export const PID = 'smoke_wfb';

export async function startBuilder({ workflows = [] } = {}) {
  const assets = loadStaticJsCss(root);
  const origin = 'http://mc.smoke.test';
  const store = workflows.map(w => JSON.parse(JSON.stringify(w)));
  const rec = { workflowWrites: [], schedulePosts: [], schedulePuts: [], scheduleDeletes: [], schedules: [] };
  const errors = [];
  const projects = [PID].map(id => ({
    id, name: id, status: 'active', domain: 'general', description: '',
    summary: '', current_task: 'Idle', next_action: '', activity_log: [], backlog: [],
    project_path: '/smoke/' + id, provider: 'claude', use_streaming_agent: true,
  }));
  const people = ['builder', 'reviewer'].map(name => ({
    name, scope: 'global', agent_name: name, display_name: name, avatar: '', engine: { provider: 'claude' },
  }));
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
  page.setDefaultTimeout(5000);
  page.on('pageerror', e => errors.push(e.message));
  page.on('dialog', d => d.accept());
  await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const body = () => { try { return JSON.parse(req.postData() || '{}'); } catch (e) { return {}; } };
    if (req.url() === 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs') {
      return route.fulfill({ contentType: 'text/javascript', body: 'export default { initialize() {} };' });
    }
    if (path === '/') return route.fulfill({ contentType: 'text/html', body: readFileSync(resolve(root, 'static/index.html'), 'utf8') });
    if (assets[path]) return route.fulfill({ contentType: assets[path][0], body: assets[path][1] });
    if (path === '/api/projects') return json(projects);
    if (path === '/api/config') return json({});
    if (path === '/api/floor') return json({ rooms: [], quiet: [], counts: {},
      bench: people.map(p => ({ ...p, display: p.name, provider: 'claude', rooms: [] })) });
    if (path === '/api/characters') return json(people);
    if (path === '/api/workflows' && method === 'GET') return json(store);
    if (path === '/api/workflows' && method === 'POST') {
      const wf = { ...body(), id: 'wf-new-' + (store.length + 1), format: 2 };
      rec.workflowWrites.push({ method, body: body() });
      store.push(wf);
      return json({ ok: true, workflow: wf });
    }
    const wfM = /^\/api\/workflows\/([^/]+)$/.exec(path);
    if (wfM && method === 'PUT') {
      rec.workflowWrites.push({ method, body: body() });
      const i = store.findIndex(w => w.id === wfM[1]);
      const wf = { ...body(), id: wfM[1], format: 2 };
      if (i >= 0) store[i] = wf; else store.push(wf);
      return json({ ok: true, workflow: wf });
    }
    if (wfM && method === 'GET') return json(store.find(w => w.id === wfM[1]) || {}, store.some(w => w.id === wfM[1]) ? 200 : 404);
    if (path === '/api/schedules' && method === 'GET') return json(rec.schedules);
    if (path === '/api/schedules' && method === 'POST') {
      const s = { ...body(), id: 'sched-' + (rec.schedulePosts.length + 1) };
      rec.schedulePosts.push(s); rec.schedules.push(s);
      return json(s);
    }
    const sM = /^\/api\/schedules\/([^/]+)$/.exec(path);
    if (sM && method === 'PUT') { const s = { ...body(), id: sM[1] }; rec.schedulePuts.push(s); return json(s); }
    if (sM && method === 'DELETE') { rec.scheduleDeletes.push(sM[1]); return json({ ok: true }); }
    if (path === `/api/project/${PID}/workflows`) return json({ workflows: store });
    if (path === `/api/project/${PID}/agent/log` || path === `/api/project/${PID}/conversations`) return json([]);
    return route.abort();
  });
  await page.goto(origin, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card');
  await page.evaluate(pid => {
    window.openProjectModal(pid);
    const win = document.querySelector(`.modal-window[data-modal-id="${pid}"]`);
    win.querySelector('.modal-content').style.width = '1480px';
    window.centerModalElement(win);
    window.switchModalTab(pid, 'workflows');
  }, PID);
  await page.waitForSelector(`#wfb-inline-host-${PID}`);
  // Save goes through the passcode modal (humanProofFetch); the smoke has no
  // passcode, so answer the same call shape straight from the stubbed routes.
  await page.evaluate(() => {
    window.humanProofFetch = async (url, opts) => {
      const r = await fetch(url, opts);
      return { ok: r.ok, status: r.status, body: await r.json().catch(() => ({})) };
    };
  });

  const open = (workflowId) => page.evaluate(async ({ id, pid }) => {
    await window.openWorkflowBuilder(id, pid);
  }, { id: workflowId, pid: PID });
  const def = () => page.evaluate(() => JSON.parse(JSON.stringify(window._wfEntry()._wf.def)));
  const wf = () => page.evaluate(() => {
    const st = window._wfEntry()._wf;
    return { def: JSON.parse(JSON.stringify(st.def)), dirty: st.dirty, linkedSchedule: st.linkedSchedule,
             undo: (st._undo || []).length, redo: (st._redo || []).length };
  });

  let passed = 0, failed = 0;
  async function check(label, fn) {
    try { await fn(); passed++; console.log('PASS ' + label); }
    catch (e) { failed++; console.error('FAIL ' + label + ': ' + e.message); }
  }
  async function finish(name) {
    await check('no uncaught page errors', async () => {
      if (errors.length) throw new Error(errors.join(' | '));
    });
    await browser.close();
    console.log(`${name}: ${passed} passed, ${failed} failed`);
    process.exit(failed ? 1 : 0);
  }
  // Put the server-side store (and recorded writes) back to the fixtures, so a
  // check that saves cannot leak its saved state into the next one.
  const reseed = () => {
    store.length = 0; store.push(...workflows.map(w => JSON.parse(JSON.stringify(w))));
    for (const k of Object.keys(rec)) rec[k].length = 0;
  };
  return { page, rec, store, open, def, wf, check, finish, errors, reseed, pid: PID };
}
