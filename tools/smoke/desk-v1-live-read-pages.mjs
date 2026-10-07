#!/usr/bin/env node
/** MC-1062 follow-up: production wizard, actual type projections, 1440/390.
 * Reopen prefill, pane/app choice, pages_needed, draft-only edits, Review/Save,
 * refusal and retry, untouched preservation, account isolation, viewport fit.
 * Screenshots: _scratch/connect_reading/{youtube,linkedin}_{1440,390}.png.
 */
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const STATIC = loadStaticJsCss(REPO_ROOT);
const SHOTS = resolve(REPO_ROOT, '_scratch', 'connect_reading');
mkdirSync(SHOTS, { recursive: true });
const TYPES = JSON.parse(execFileSync(process.env.MC_PYTHON || 'python', ['-c', `
import json
from mc.desk_connect import resolve, type_view
out={}
for name in ['x', 'linkedin', 'youtube', 'instagram']:
    got=resolve.resolve('https://'+name+'.com', own_hosts=('mc.smoke.test',))
    service=got.pop('service')
    out['https://'+name+'.com']={**got, **type_view.project_service(service['id'] if service else None)}
print(json.dumps(out))
`], {cwd: REPO_ROOT, encoding:'utf8', env:{...process.env, PYTHONIOENCODING:'utf-8'}}));

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));

const NOT_READY = { ready: false, reason: 'read-only account: the Desk reads it and does not publish there', secret: null, unattended_ok: null };
const ADDR = 'https://studio.youtube.com/channel/UC123/comments';

function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  const srv = { log: [], refuse: null, coverage: [], fx };
  srv.accounts = fx.channels.filter((c) => ['x', 'linkedin', 'blog'].includes(c.platform))
    .map((c) => ({ ...JSON.parse(JSON.stringify(c)), publish: { ready: true, reason: null, secret: null, unattended_ok: null } }));
  srv.accounts.push(
    { id: 'ch-yt', platform: 'youtube', identity: 'UC123', label: 'YouTube · UC123', capability: 'none', voice: '', browser_profile: 'yt-main', connected: false, publish: NOT_READY },
    { id: 'ch-ig', platform: 'instagram', identity: 'clayrune', label: 'Instagram · clayrune', capability: 'none', voice: '', connected: false, publish: NOT_READY });
  srv.accounts.find((a) => a.platform === 'linkedin').read_via = 'api';      // saved before the option was removed
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects, accounts: srv.accounts, pieces: [] });
  return srv;
}

async function open(browser, srv, viewport) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();
    const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return J(srv.fx.projects.map((p) => ({
      id: p.id, name: p.name, status: 'active', domain: 'general', emoji: '🧪', description: '', summary: '', current_task: 'Idle',
      next_action: '', blocked: false, blocked_reason: null, activity_log: [], backlog: [], project_path: '/smoke/' + p.id,
      last_updated: '2026-09-09T00:00:00Z', last_updated_relative: 'today', last_completed: null, live_agent: null, display_order: 0,
      provider: 'claude', use_streaming_agent: true, distiller_mode: 'proposed', distiller_min_recurrence: 3,
      distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3, distiller_max_explorations_per_session: 3,
      distiller_min_turns: 5, distiller_skip_errors: true, roster: [],
    })));
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true, user_timezone: '' });
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (path === '/api/browser/agent-read') return J({ profiles: {} });
    if (path === '/api/browser/profiles') return J({ profiles: [] });
    if (/^\/api\/desk\/engagement\/coverage\//.test(path)) return srv.coverageError ? J({error:srv.coverageError},503) : J({ project_id: 'p', coverage: srv.coverage });
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/engines') return J({ engines: [] });
    if (path === '/api/desk/services') return J([]);
    if (path === '/api/desk/connect/types') {
      if (srv.delayTypes === body.input) await new Promise(resolve => { srv.resumeTypes=resolve; srv.typesRequested(); });
      return J(TYPES[body.input]);
    }
    if (path === '/api/desk/connect/suggest') return J({ suggestions:[] });
    if (path === '/api/desk/connect/purposes') return J({ accounts:[] });
    if (/^\/api\/desk\/connect\/permissions\//.test(path)) return J({ policy:{ state:'legacy', scopes:[] } });
    if (path === '/api/desk/accounts' && method === 'GET') return J(srv.accounts);
    const m = path.match(/^\/api\/desk\/accounts\/([^/]+)$/);
    if (m && method === 'PATCH') {
      const acc = srv.accounts.find((x) => x.id === m[1]);
      if (!acc) return J({ error: 'account not found' }, 404);
      if (srv.refuse) return J({ error: srv.refuse }, 400);
      const { project_id: _p, ...rest } = body;
      Object.assign(acc, rest);
      if (Array.isArray(acc.read_pages) && !acc.read_pages.length) delete acc.read_pages;
      return J(acc);
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-connections] [data-conn-tile]', { timeout: 6000 });
  return { ctx, page, pageErrors };
}

const selectTile = async (page, id) => {
  if ((await page.getAttribute(`[data-conn-tile="${id}"]`, 'aria-pressed')) !== 'true') await page.click(`[data-conn-tile="${id}"]`);
  await page.waitForSelector(`[data-conn-detail="${id}"] [data-conn-account="${id}"]`, { timeout: 6000 });
};
const patches = (srv, id) => srv.log.filter((r) => r.method === 'PATCH' && r.path === `/api/desk/accounts/${id}`);
const realErrors = (errs) => errs.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));


async function reopen(page, id) {
  await selectTile(page,id);
  await page.click(`[data-conn-account="${id}"] [data-cs-reopen]`);
  await page.waitForSelector('[data-cfw-step="permissions"] [data-reading-settings]');
  await page.waitForFunction(() => !document.querySelector('[data-cfw-primary]').disabled || !!document.querySelector('[data-reading-choice-needed]'));
}
async function next(page, step) {
  await page.click('[data-cfw-primary]');
  await page.waitForSelector(`[data-cfw-step="${step}"]`);
}
async function save(page) { await next(page,'review'); await next(page,'result'); }
const writes = srv => srv.log.filter(r => !['GET'].includes(r.method) && !['/api/desk/connect/types','/api/desk/connect/purposes','/api/desk/connect/suggest','/api/desk/connect/custom/connections'].includes(r.path));
async function run(browser, viewport, label) {
  console.log(label);
  const srv = makeServer();
  const {ctx,page,pageErrors} = await open(browser,srv,viewport);
  await selectTile(page,'ch-yt');
  check(!(await page.$('[data-readvia-row], [data-readpages-for]')), 'account card has no second reading editor');
  await reopen(page,'ch-yt');
  check(await page.inputValue('[data-reading-profile]')==='yt-main', 'reopen pre-fills the account profile');
  check(await page.locator('[data-reading-via="pane"]').isChecked() && !(await page.$('[data-reading-via="api"]')), 'YouTube defaults to pane, without an app option');
  check(!(await page.$('[data-reading-pages]')), 'activity addresses are not asked before discovery needs them');
  check(writes(srv).length===0,'opening Permissions sends no write',JSON.stringify(writes(srv)));
  await save(page);
  check(writes(srv).length===0,'an untouched saved account sends no write');

  srv.coverage=[{platform:'youtube',state:'not_connected',pages:{status:'pages_needed',reason:'not found'}}];
  await reopen(page,'ch-yt');
  await page.waitForSelector('[data-reading-pages]');
  await page.locator('[data-cfw]').evaluate(e=>e.scrollIntoView({block:'start'}));
  await page.locator('[data-cfw-primary]').scrollIntoViewIfNeeded();
  await page.screenshot({path:resolve(SHOTS,`youtube_${viewport.width}.png`)});
  await page.fill('[data-reading-address]','http://studio.youtube.com/channel/UC123/comments');
  await page.click('[data-reading-add]');
  check((await page.textContent('[data-reading-invalid]')).includes('https://') && await page.locator('[data-cfw-primary]').isDisabled(), 'non-https address is refused and cannot Continue');
  check(writes(srv).length===0,'invalid address sends nothing');
  await page.fill('[data-reading-address]',ADDR);
  await page.click('[data-reading-add]');
  check(writes(srv).length===0,'Add address only changes the draft');
  await next(page,'review');
  check((await page.textContent('[data-sum-row="permissions"]')).includes(ADDR),'Review shows the chosen activity address');
  await page.click('[data-cfw-back]');
  await page.waitForSelector('[data-cfw-step="permissions"] [data-reading-remove]');
  check((await page.textContent('[data-reading-pages]')).includes(ADDR),'Back preserves the activity draft');
  check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),`Permissions fits ${viewport.width}px`);
  await page.locator('[data-reading-add]').scrollIntoViewIfNeeded();
  const rect = await page.locator('[data-reading-address]').boundingBox();
  check(rect.x>=0 && rect.x+rect.width<=viewport.width,'activity address field fits the viewport');
  await save(page);
  const sent = patches(srv,'ch-yt');
  check(sent.length===1 && JSON.stringify(sent[0].body.read_pages)===JSON.stringify([{role:'activity',url:ADDR}]) && typeof sent[0].body.project_id==='string', 'Save sends the account read_pages PATCH with its project');
  check(!('read_via' in sent[0].body) && !('browser_profile' in sent[0].body), 'page save preserves untouched method and profile');
  check((await page.textContent('[data-sum-row="permissions"]')).includes('Saved'), 'Result reports reading settings saved');

  await reopen(page,'ch-yt');
  check((await page.textContent('[data-reading-pages]')).includes(ADDR),'reopen pre-fills saved pages');
  await page.click('[data-reading-remove]');
  await page.fill('[data-reading-profile]','yt-other');
  check(patches(srv,'ch-yt').length===1,'Remove and profile edits remain drafts until Save');
  srv.refuse='saved browser profile does not exist';
  await save(page);
  check((await page.textContent('[data-sum-banner="partial"]')).includes(srv.refuse), 'refused PATCH shows the exact server error as a partial result');
  check(srv.accounts.find(a=>a.id==='ch-yt').read_pages.length===1,'refused save preserves the stored list');
  srv.refuse=null;
  await page.click('[data-sum-act="perms"]');
  await page.waitForFunction(() => !document.querySelector('[data-sum-banner="partial"]'));
  const removed = patches(srv,'ch-yt').at(-1).body;
  check(removed.read_pages.length===0 && removed.browser_profile==='yt-other', 'retry saves the unchanged removal/profile draft');
  check(!('read_via' in removed),'retry does not change the reading method');

  const li = srv.accounts.find(a=>a.platform==='linkedin');
  li.browser_profile='main';
  await page.evaluate(id => { window.DeskV1Store.state().channels.find(a=>a.id===id).browser_profile='main'; },li.id);
  await reopen(page,li.id);
  check(await page.locator('[data-cfw-primary]').isDisabled() && !!(await page.$('[data-reading-choice-needed]')) && !(await page.$('[data-reading-via="api"]')), 'LinkedIn stored as app asks for an explicit choice');
  check(patches(srv,li.id).length===0 && li.read_via==='api','opening LinkedIn does not move its saved reading method');
  await page.click('[data-reading-via="pane"]');
  check(await page.inputValue('[data-reading-profile]')==='main','LinkedIn retains its saved browser profile');
  await page.locator('[data-cfw]').evaluate(e=>e.scrollIntoView({block:'start'}));
  await page.locator('[data-cfw-primary]').scrollIntoViewIfNeeded();
  await page.screenshot({path:resolve(SHOTS,`linkedin_${viewport.width}.png`)});
  await next(page,'review');
  check((await page.textContent('[data-sum-row="permissions"]')).includes('Browser sign-in (no charge)'), 'Review shows the explicit LinkedIn pane choice');
  await next(page,'result');
  check(patches(srv,li.id).length===1 && patches(srv,li.id)[0].body.read_via==='pane','Save records the explicit LinkedIn choice');
  await reopen(page,li.id);
  check(await page.locator('[data-reading-via="pane"]').isChecked() && !(await page.$('[data-reading-choice-needed]')),'next reopen pre-fills the chosen method');

  const x = srv.accounts.find(a=>a.platform==='x');
  await reopen(page,x.id);
  check(await page.locator('[data-reading-via="pane"]').isChecked() && !!(await page.$('[data-reading-via="api"]')),'X offers both routes and defaults to pane');
  await page.click('[data-reading-via="api"]');
  check(!(await page.$('[data-reading-profile], [data-reading-pages]')),'app choice hides pane-only settings');
  await save(page);
  check(patches(srv,x.id).length===1 && patches(srv,x.id)[0].body.read_via==='api','X app choice saves only on Review');
  await reopen(page,x.id);
  check(await page.locator('[data-reading-via="api"]').isChecked(),'X app choice pre-fills on reopen');
  await reopen(page,'ch-ig');
  check(await page.locator('[data-reading-via="pane"]').isChecked() && !(await page.$('[data-reading-via="api"]')) && !(await page.$('[data-reading-pages]')),'switching accounts clears the prior account draft and coverage');
  srv.coverageError='coverage temporarily unavailable';
  await selectTile(page,'ch-yt');
  await page.click('[data-conn-account="ch-yt"] [data-cs-reopen]');
  await page.waitForSelector('[data-reading-retry]');
  check(await page.locator('[data-cfw-primary]').isDisabled() && (await page.textContent('[data-reading-settings]')).includes(srv.coverageError),'coverage errors block Continue and show the server error');
  srv.coverageError=null;
  await page.click('[data-reading-retry]');
  await page.waitForFunction(()=>!document.querySelector('[data-cfw-primary]').disabled);
  check(!!(await page.$('[data-reading-pages]')), 'coverage retry restores the activity-page section');
  srv.delayTypes='https://youtube.com';
  const requested = new Promise(resolve => { srv.typesRequested=resolve; });
  await page.evaluate(() => {
    const original=window.DeskV1Store.api;
    window.DeskV1Store.api=async (...args) => { const r=await original(...args); if(args[1]==='/api/desk/connect/types') window.__lookupDone=true; return r; };
  });
  await selectTile(page,'ch-yt');
  await page.click('[data-conn-account="ch-yt"] [data-cs-reopen]');
  await requested;
  await selectTile(page,'ch-ig');
  srv.delayTypes=null; srv.resumeTypes();
  await page.waitForFunction(()=>window.__lookupDone);
  check(await page.evaluate(()=>window.DeskV1ConnTiles.selected()==='ch-ig') && !(await page.$('[data-cfw]')), 'late account lookup cannot reopen a different tile');
  check(!writes(srv).some(r => /browser|commit|verify|publish/.test(r.path)), 'reading settings never grant permission, sign in, verify, or publish');
  realErrors(pageErrors).length ? realErrors(pageErrors).forEach(e=>fail('page error: '+e)) : ok('no uncaught page errors');
  await ctx.close();
}
const browser = await chromium.launch();
try {
  await run(browser,{width:1440,height:900},'one wizard, 1440');
  await run(browser,{width:390,height:844},'one wizard, 390');
} catch(e) {fail('harness error: '+(e.stack || e));}
await browser.close();
if(bad) {console.error(`FAIL: ${bad}`);process.exit(1);}
console.log('PASS: saved account reading choices and activity pages live in the one wizard.');
