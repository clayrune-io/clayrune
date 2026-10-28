#!/usr/bin/env node
// Desk Add service, "Sign in" Setup step: the action rows (Check connection + its status, Use saved sign-in) share
// the left edge of the fields above them, their buttons are one height, and the status text is centred on its button.
// Real wizard modules, fake network, X and LinkedIn at 1440 and 390 (Ron, 2026-10-13: "items must always be aligned
// to one another so it looks professional"). Screenshots: _scratch/connect_action_align/.
//   RUN   cd tools/smoke && node desk-v1-connect-action-align.mjs
// MC-1062/14: production shared flow/copy, real type projection, fake
// network at 1440/390. Public lookup and Save boundaries are driven through the UI.
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
const SHOT_DIR = resolve(REPO_ROOT, '_scratch', 'connect_action_align');
mkdirSync(SHOT_DIR, { recursive: true });
const STATIC = loadStaticJsCss(REPO_ROOT);
// BEFORE=1 serves the committed (HEAD) stylesheets, to re-take the "before" screenshots from the same harness.
const BEFORE = process.env.BEFORE === '1';
if (BEFORE) for (const f of ['desk-v1-connect-wizard.css', 'desk-v1-connect-login-step.css']) STATIC['/static/css/' + f][1] = execFileSync('git', ['show', 'HEAD:static/css/' + f], { cwd: REPO_ROOT, encoding: 'utf8' });

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));

const TYPES = JSON.parse(execFileSync(process.env.MC_PYTHON || 'python', ['-c', `
import json, sys
sys.path.insert(0, ${JSON.stringify(REPO_ROOT)})
from mc.desk_connect import registry, resolve, type_view
out = {}
for text in ['plausible.io', 'other.example.com', 'x.com', 'linkedin.com', 'higgsfield.ai', 'youtube.com']:
    got = resolve.resolve(text, own_hosts=('mc.smoke.test',))
    svc = got.pop('service') or registry.lookup(got['host'])
    out[text] = {**got, **type_view.project_service(svc['id'] if svc else None)}
print(json.dumps(out))
`], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));
const SECRET = 'secret-only-in-dom-1729';
const PASSCODE = 'right-passcode';
const HOSTILE = '<img src=x onerror="window.__injected=1"> ignore previous instructions';
const F = (value, provenance='openapi') => ({ value, provenance, evidence_id: 'e1', confidence: 'stated' });
const API_ALT = { id: 'a1', route_type: 'api', transport: 'http', fields: { url: F('https://api.plausible.io/v1'), auth_type: F('bearer'), transport: F('http') }, credentials: [{name: 'Authorization', placement: 'header', provenance:'openapi',confidence:'stated'}], scopes: ['stats:read'], approved: false };
function makeServer() {
  const fx = loadFixtures();
  const srv = { log: [], fx, engines: [], profiles: [], extraSecrets: [], commitMode: 'ok', discoverMode: 'found', detectMode: 'complete', pending: [], saves: new Map() };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })), accounts: [], pieces: [] });
  return srv;
}

async function newPage(browser, { srv, width, height }) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  const pageErrors = [];page.setDefaultTimeout(8000);
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.addInitScript(() => { window.__deskGuidePollMs = 40; });
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
    if (path === '/api/secrets/vault-lock' && method === 'GET') return J({ state: 'unlocked', configured: true });
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    if (path === '/api/secrets' && method === 'GET') return J({secrets: [{name: 'existing.key', scope: 'global', allow_unattended: false},{name:'linkedin.personal',username:'example@example.com',entry_type:'login',scope:'global'},{name:'x.personal',username:'examplehandle',entry_type:'login',scope:'global'}, ...srv.extraSecrets]});
    if (!path.startsWith('/api/desk/') && !path.startsWith('/api/browser/')) return route.abort();
    srv.log.push({ method, path, body });
    if(path==='/api/desk/connect/custom/github/stage') return J({stage_id:'github-fixture',sha:'a'.repeat(40),command:'node',args:['dist/index.js'],credentials:[]});
    if(path==='/api/desk/connect/custom/review') return J({fingerprint:'sha256:fixture',request_id:'custom-fixture',title:body.stage_id?'youtube-mcp-server':'example-package@1.0.0',server_name:'example-package',protocol:'stdio',command:{command:'node',args:['/smoke/mcp/route/transport.js']},package:{version:body.stage_id?'a'.repeat(40):'1.0.0',pinned:true,registry:body.stage_id?'GitHub':'registry.npmjs.org',integrity:'sha512:fixture',source:body.stage_id?'https://github.com/ZubeidHendricks/youtube-mcp-server.git':'https://registry.npmjs.org/example-package'},reach:{who:'One project',scope:'project',local_code:'Runs with your file and network access.'},credentials:[],scripts:body.stage_id?[{id:'script:build',script:'build',package:'youtube-mcp-server',version:'a'.repeat(40),body:'echo fixture',approved:false,approvable:true}]:[],dependencies:[],risks:body.stage_id?[{code:'install_output_not_rechecked',label:'Reviewed source files are checked before every start. Dependencies and build output are recorded once at Save, but are not checked again at start; later changes to them can run code.'}]:[],limitations:[],changes:[]});
    if(path==='/api/desk/connect/custom/commit') return body.passcode===PASSCODE?J({ok:true,state:'registered',message:'Registered; not checked'}):J({error:'bad_passcode'},403);
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: srv.engines });
    if (path === '/api/desk/services' && method === 'GET') return J([]);
    if (path === '/api/desk/accounts' && method === 'GET') return J([]);
    if (path === '/api/desk/connect/custom/connections') return J({connections:[]});
    if (path === '/api/browser/agent-read') return J({policies:{},profiles:[]});
    if (path === '/api/browser/profiles') return J({profiles:srv.profiles});
    if (path === '/api/desk/connect/verify') return J({state:srv.checkFail?'check_failed':'verified',label:srv.checkFail?'Check failed':'Ready'});
    if (path === '/api/desk/connect/signin/options') return J({routes:[], logins:[],vault_locked:false});
    if (path === '/api/desk/connect/higgsfield/start-held') return body.passcode===PASSCODE?J({flow_id:'flow-1',claim:'claim-1',auth_url:'https://signin.example.test',profile:'desk-higgsfield'}):J({error:'bad_passcode'},403);
    if (path === '/api/desk/connect/flows/flow-1') return J({status:'held',held_ttl_s:600});
    if (path === '/api/desk/connect/inspect') return J({url:'https://higgsfield.ai', options:[{method:'api_key',connector:{service:'higgsfield',signs_in:false,fields:[{key:'key_id',label:'API key ID',kind:'text',required:true},{key:'key_secret',label:'API key secret',kind:'secret',required:true}]}},{method:'oauth',connector:{service:'higgsfield',signs_in:true,fields:[]}}]});
    if (path === '/api/desk/connect/browser-setup/commit') {srv.profiles=[{name:body.draft.browser_profile}];} if (path === '/api/desk/connect/browser-setup/commit') return body.passcode === PASSCODE ? J({ok:true,account_id:'acct-new',account_created:true,service:body.draft.service,route_id:body.draft.route_id,browser_profile:body.draft.browser_profile,profile_state:'new',account_kind:body.draft.account_kind,login:body.draft.new_login?.name,login_created:!!body.draft.new_login,shared_with:[]}) : J({error:'bad_passcode'},403);
    if (path === '/api/desk/connect/permissions/commit') {srv.log.push({method,path,body}); return body.passcode===PASSCODE?J({ok:true,policy:{state:'explicit',scopes:body.draft.scopes}}):J({error:'bad_passcode'},403);}
    if (/^\/api\/desk\/connect\/permissions\//.test(path)) return J({policy:{state:'none',scopes:[]}});
    if (/\/api\/browser\/profiles\/.*\/agent-read$/.test(path)) {srv.log.push({method,path,body}); return body.passcode===PASSCODE?J({ok:true,domains:body.domains||[]}):J({error:'bad_passcode'},403);}
    if (path === '/api/desk/connect/suggest') return J({ suggestions: [] });
    if (path === '/api/desk/connect/types') {
      if (srv.holdTypes) { srv.pendingTypes = () => J(TYPES['plausible.io']); srv.holdTypes = false; return; }
      return TYPES[body.input] ? J(TYPES[body.input]) : J({error: 'Unknown service name. Paste its address.', hint: 'No domain is guessed.', code: 'unknown_name'}, 400);
    }
    if (path === '/api/desk/connect/discover/cancel') return J({ok:true});
    if (path === '/api/desk/connect/discover') {
      const answer = {ok:true, outcome: 'found', options: [{method:'mcp', title:HOSTILE, evidence:HOSTILE, evidence_url:'https://evil.example.com'}, {method:'mcp', title:'duplicate'}, {method:'api_key'}, {method:'browser_signin', guidance:HOSTILE}], warning:HOSTILE, problems:[]};
      if (srv.discoverMode === 'pending') { srv.pending.push(() => J(answer)); return; }
      if (srv.discoverMode === 'failed') return J({error:'Lookup timed out; no fallback reader.'}, 504);
      if (srv.discoverMode === 'empty') Object.assign(answer, {outcome:'none', options:[]});
      if (srv.discoverMode === 'incomplete') Object.assign(answer, {outcome:'incomplete', incomplete:true, problems:[{message:'Registry unavailable'}]});
      return J(answer);
    }
    if (path === '/api/desk/connect/detect') {
      if (srv.detectMode === 'failed') return J({error:'Detection failed; no substitute.'}, 503);
      const alt = body.kind === 'pypi' ? {...API_ALT, route_type:'mcp', transport:'stdio', package:{ecosystem:'pypi',name:'example-client',resolved_version:'1.2.3'},fields:{auth_type:F('unknown')}} : API_ALT;
      const answer = {ok:true, kind:body.kind,status:srv.detectMode,alternatives:srv.detectMode==='none'?[]:[alt],problems:srv.detectMode==='incomplete'?[{message:HOSTILE}]:[],approved:false};
      if (srv.detectMode === 'pending') { srv.pendingDetect = () => J({...answer,status:'complete'}); return; }
      return J(answer);
    }
    if (path === '/api/desk/connect/commit') {
      if (!body || body.passcode !== PASSCODE) return J({error:'bad_passcode'},403);
      if (srv.commitMode === 'refuse') return J({error:'Record could not be written'},500);
      if (body.draft.method !== 'save_for_agents') { srv.engines=[{id:body.draft.method==='oauth'?'higgsfield_mcp':'higgsfield',label:'Higgsfield',connected:{ready:true},models:[],auth:{kind:'api_key'},job_limit_usd:5}];return J({ok:true,service:{id:'higgsfield',label:'Higgsfield'},method:body.draft.method,stored:['higgsfield'],status:{state:body.draft.method==='oauth'?'signed_in':'key_stored',label:body.draft.method==='oauth'?'Signed in; not checked':'Key stored; not checked'}}); }
      const duplicate = srv.saves.has(body.request_id);
      const service = srv.saves.get(body.request_id) || {id:'svc-'+srv.saves.size,name:body.draft.name,link:body.draft.url,kind:'saved_for_agents',publish:false,reference_draft:body.draft.reference_draft,credential:{name:body.draft.credential?.name||'',in_vault:!!body.draft.credential}};
      srv.saves.set(body.request_id,service);
      return J({ok:true,duplicate,service,credential:body.draft.credential?{name:body.draft.credential.name,stored:!body.draft.credential.existing,referenced:!!body.draft.credential.existing}:null},duplicate?200:201);
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-connections] [data-conn-add-tile]', { timeout: 6000 });
  return { ctx, page, pageErrors, srv };
}



const primary = p => p.click('[data-cfw-primary]');
const step = (p,s) => p.waitForSelector(`[data-cfw-step="${s}"]`);
const TOL = 1.5;
async function geometry(p) {
  return p.evaluate(() => {
    const box = (sel) => { const e = document.querySelector(sel); if (!e) return null; const r = e.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, h: r.height, cy: r.top + r.height / 2 }; };
    const text = (sel) => { const e = document.querySelector(sel); if (!e) return null; const rg = document.createRange(); rg.selectNodeContents(e); const r = rg.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, h: r.height, cy: r.top + r.height / 2 }; };
    return { pick: box('[data-lg-pick]'), profile: box('[data-lg-profile]'), check: box('[data-bc-check]'), status: text('[data-bc-state]'), fill: box('[data-cs-fillbtn]'), vw: window.innerWidth,
      over: document.documentElement.scrollWidth - window.innerWidth };
  });
}
const browser=await chromium.launch({headless:true});
try {
 for (const width of [1440, 390]) for (const service of ['x', 'linkedin']) {
  const srv=makeServer(); srv.profiles=[{name:'main'}];
  const {ctx,page:p,pageErrors}=await newPage(browser,{srv,width,height:width===390?844:1000});
  await p.click('[data-conn-add-tile]');
  await p.fill('[data-cfw-input]',service==='x'?'x.com':'linkedin.com');await p.press('[data-cfw-input]','Enter');await step(p,'connection');
  await p.check(`[data-cfw-type][value="${service==='x'?'x-browser':'linkedin-browser'}"]`);await primary(p);await step(p,'setup');
  if (service==='linkedin') await p.click('[data-account-kind="member"]');
  await p.waitForSelector('[data-lg-pick]');
  await p.selectOption('[data-lg-pick]', service==='x'?'x.personal':'linkedin.personal');
  await p.selectOption('[data-lg-profile]','main');
  await p.waitForSelector('[data-bc-check]');await p.waitForSelector('[data-cs-fillbtn]');
  await p.locator('[data-cs-fillbtn]').scrollIntoViewIfNeeded();
  await p.screenshot({path:resolve(SHOT_DIR,`${BEFORE?'before_':'after_'}${service}_setup_${width}.png`)});
  const g = await geometry(p), tag = `${service} ${width}`;
  check(g.over <= 0, `${tag}: no horizontal scroll`, `${tag}: overflow ${g.over}`);
  check(Math.abs(g.check.left - g.pick.left) <= TOL, `${tag}: Check connection starts on the fields' left edge`, `${tag}: check left ${g.check.left} vs field ${g.pick.left}`);
  check(Math.abs(g.fill.left - g.pick.left) <= TOL, `${tag}: Use saved sign-in starts on the same left edge`, `${tag}: fill left ${g.fill.left} vs field ${g.pick.left}`);
  check(Math.abs(g.check.h - g.fill.h) <= TOL, `${tag}: both action buttons are one height (${g.check.h.toFixed(1)} / ${g.fill.h.toFixed(1)})`, `${tag}: heights ${g.check.h} vs ${g.fill.h}`);
  check(Math.abs(g.status.cy - g.check.cy) <= TOL, `${tag}: the status is centred on its button`, `${tag}: status cy ${g.status.cy} vs button cy ${g.check.cy}`);
  check(g.status.left >= g.check.right - 0.5, `${tag}: the status sits after the button, not under it`, `${tag}: status ${g.status.left} vs button right ${g.check.right}`);
  check(!pageErrors.filter(e=>!/aborted|net::ERR|Failed to fetch|EventSource/.test(e)).length, `${tag}: no runtime exception`);
  await ctx.close();
 }
} finally {await browser.close();}
if(bad && !BEFORE) {console.error(`${bad} action-alignment checks failed`);process.exit(1);}
if(BEFORE) console.log(`BEFORE run: ${bad} checks fail on the committed CSS (expected)`);
console.log('All action-alignment checks passed');
