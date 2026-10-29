// Saved LinkedIn Company Page address: real Permissions/Review/Save, synthetic API only.
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
const SHOTS = resolve(REPO_ROOT, '_scratch', 'connect_company_page');
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

const COMPANY = 'https://www.linkedin.com/company/123456/admin/notifications/';
async function run(browser, viewport) {
  console.log(`Company Page address, ${viewport.width}px`);
  const srv = makeServer();
  const li = srv.accounts.find(a => a.platform === 'linkedin');
  Object.assign(li, { read_via:'pane', browser_profile:'main', browser_setup:{ service:'linkedin', route_id:'linkedin-browser', account_kind:'member', refs:{ browser_profile:'main' } } });
  const {ctx, page, pageErrors} = await open(browser, srv, viewport);
  const field = '[data-reading-company-address]';
  await reopen(page, li.id);
  check(await page.locator(field).isVisible() && await page.inputValue(field)==='', 'saved member has Company Page field even without coverage or pages');
  check(!(await page.$('[data-reading-address]')), 'LinkedIn has one destination editor');
  check((await page.textContent('[data-reading-company-page]')).includes('Leave empty for your personal profile'), 'empty field explains personal profile');
  check(writes(srv).length===0, 'opening sends no writes');
  await save(page);
  check(patches(srv,li.id).length===0, 'untouched empty field preserves the saved member');
  await reopen(page,li.id);
  for (const invalid of [
    'https://www.linkedin.com/company/clayrune/admin/notifications/',
    'http://www.linkedin.com/company/123/admin/',
    'https://evil.example/company/123/admin/',
    'https://www.linkedin.com/company/123/',
    'https://www.linkedin.com/notifications/',
    'https://evil.example@www.linkedin.com/company/123/admin/',
    'https://@www.linkedin.com/company/123/admin/',
    'https://ｗｗｗ.linkedin.com/company/123/admin/',
    'https://www.linkedin.com/company/123456789012345678901/admin/',
  ]) {
    await page.fill(field,invalid);
    check(await page.locator('[data-cfw-primary]').isDisabled() && /numeric company ID/.test(await page.textContent('[data-reading-company-error]')), `invalid address blocked: ${invalid}`);
  }
  check(patches(srv,li.id).length===0, 'invalid addresses send no PATCH');
  await page.fill(field,COMPANY);
  check(await page.locator('[data-cfw-primary]').isEnabled() && await page.textContent('[data-reading-company-error]')==='', 'valid numeric admin address enables Continue');
  await page.locator(field).scrollIntoViewIfNeeded();
  await page.click(field);
  const box=await page.locator(field).boundingBox();
  check(box.x>=0 && box.x+box.width<=viewport.width && await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth), 'field fits viewport');
  check(await page.locator(field).evaluate(e => { const b=e.getBoundingClientRect(); return e === document.elementFromPoint(b.x+b.width/2,b.y+b.height/2); }), 'field is exposed for pointer input above sticky controls');
  await page.screenshot({path:resolve(SHOTS,`company_${viewport.width}.png`)});
  await next(page,'review');
  check((await page.textContent('[data-sum-row="permissions"]')).includes(COMPANY), 'Review names the Company Page address');
  check(patches(srv,li.id).length===0, 'Continue changes only a draft');
  await page.click('[data-cfw-back]');
  await page.waitForSelector(field);
  check(await page.inputValue(field)===COMPANY, 'Back preserves the draft');
  srv.refuse='human-only update refused';
  await save(page);
  check((await page.textContent('[data-sum-banner="partial"]')).includes(srv.refuse) && !li.organization_id, 'failed Save shows reason and leaves account unchanged');
  srv.refuse=null;
  await page.click('[data-sum-act="perms"]');
  await page.waitForFunction(()=>!document.querySelector('[data-sum-banner="partial"]'));
  const sent=patches(srv,li.id).at(-1).body;
  check(sent.organization_id==='123456' && JSON.stringify(sent.read_pages)===JSON.stringify([{role:'activity',url:COMPANY}]), 'Save derives ID from address and PATCHes activity page together');
  check(!('browser_profile' in sent) && !('read_via' in sent) && !('connections' in sent), 'Save preserves untouched profile, method and member binding');
  await reopen(page,li.id);
  check(await page.inputValue(field)===COMPANY, 'reopen pre-fills saved company address');
  await save(page);
  const count=patches(srv,li.id).length;
  check(count===2, 'untouched company address sends no additional PATCH');
  await reopen(page,li.id);
  await page.fill(field,'');
  await next(page,'review');
  check((await page.textContent('[data-sum-row="permissions"]')).includes('Personal profile'), 'Review describes clearing as personal profile');
  await next(page,'result');
  const clear=patches(srv,li.id).at(-1).body;
  check(clear.organization_id==='' && JSON.stringify(clear.read_pages)==='[]', 'Clear PATCH removes both organization ID and activity pages');
  await reopen(page,li.id);
  check(await page.inputValue(field)==='', 'cleared field reopens empty');
  await page.fill(field,COMPANY);
  await reopen(page,'ch-yt');
  check(!(await page.$(field)), 'company editor is LinkedIn only');
  await reopen(page,li.id);
  check(await page.inputValue(field)==='', 'switching accounts discards unsaved company address');
  check(!writes(srv).some(r=>/commit|browser|verify|publish/.test(r.path)), 'address editor never changes consent, signs in, verifies or publishes');
  realErrors(pageErrors).forEach(e=>fail('page error: '+e));
  await ctx.close();
}
const browser=await chromium.launch();
try {
  await run(browser,{width:1440,height:900});
  await run(browser,{width:390,height:844});
} catch(e) { fail(e.stack || String(e)); }
await browser.close();
if(bad) process.exit(1);
console.log('PASS: saved LinkedIn Company Page address, Save and Clear.');
