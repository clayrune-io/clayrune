#!/usr/bin/env node
// MC-1062/12: real unknown/reference wizard modules, real type projection, fake
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
const SHOT_DIR = resolve(REPO_ROOT, '_scratch', 'reference_screens');
mkdirSync(SHOT_DIR, { recursive: true });
const STATIC = loadStaticJsCss(REPO_ROOT);

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));

const TYPES = JSON.parse(execFileSync(process.env.MC_PYTHON || 'python', ['-c', `
import json, sys
sys.path.insert(0, ${JSON.stringify(REPO_ROOT)})
from mc.desk_connect import registry, resolve, type_view
out = {}
for text in ['plausible.io', 'other.example.com', 'x.com', 'linkedin.com']:
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
  const srv = { log: [], fx, commitMode: 'ok', discoverMode: 'found', detectMode: 'complete', pending: [], saves: new Map() };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })), accounts: [], pieces: [] });
  return srv;
}

async function newPage(browser, { srv, width, height }) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  const pageErrors = [];
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
    if (path === '/api/secrets' && method === 'GET') return J({secrets: [{name: 'existing.key', scope: 'mission_control', allow_unattended: false}]});
    if (!path.startsWith('/api/desk/')) return route.abort();
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: [] });
    if (path === '/api/desk/services' && method === 'GET') return J([]);
    if (path === '/api/desk/accounts' && method === 'GET') return J([]);
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

const realErrors = (pageErrors) => pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
const shot = (page, name, w) => page.screenshot({ path: resolve(SHOT_DIR, `connect_unknown_step_${name}_${w}.png`) });
async function waitStep(page, step) { await page.waitForSelector(`[data-cfw][data-cfw-step="${step}"]`, { timeout: 4000 }); }
const logOf = (srv, re) => srv.log.filter((r) => re.test(r.path));
const repaint = (page) => page.evaluate(() => window.deskV1RenderConnections(document.querySelector('[data-connections]').parentElement));

async function rules(page, label) {
  const m = await page.evaluate(() => {
    const root = document.querySelector('[data-cfw]');
    const words = Array.from(root.querySelectorAll('[data-cfw-copy]')).map((e) => e.textContent.trim().split(/\s+/).filter(Boolean).length).reduce((a, b) => a + b, 0);
    return { words, details: root.querySelectorAll('details').length, nested: root.querySelectorAll('details details, form form').length,
      forms: root.querySelectorAll('form').length, primaries: root.querySelectorAll('[data-cfw-primary]').length };
  });
  check(m.words <= 25, `${label}: ${m.words} explanatory words (<= 25)`, `${label}: ${m.words} explanatory words`);
  check(m.details <= 1 && m.nested === 0, `${label}: at most one Details, nothing nested (${m.details})`, `${label}: details ${m.details}, nested ${m.nested}`);
  check(m.forms === 1 && m.primaries <= 1, `${label}: one form, ${m.primaries} primary action`, `${label}: forms ${m.forms}, primaries ${m.primaries}`);
}

async function fits(page, label) {
  const m = await page.evaluate(() => {
    const scroller = document.querySelector('[data-connections]');
    const out = { docOver: document.documentElement.scrollWidth - window.innerWidth, paneOver: scroller ? scroller.scrollWidth - scroller.clientWidth : 0, vw: window.innerWidth, vh: window.innerHeight, controls: [] };
    for (const sel of ['[data-cfw-primary]', '[data-cfw-back]']) {
      const b = document.querySelector(sel);
      if (!b) continue;
      b.scrollIntoView({ block: 'nearest' });
      const r = b.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      out.controls.push({ sel, left: r.left, right: r.right, top: r.top, bottom: r.bottom, reachable: !!hit && (hit === b || b.contains(hit)) });
    }
    return out;
  });
  check(m.docOver <= 0 && m.paneOver <= 0, `${label}: no horizontal scroll (page +${m.docOver}, pane +${m.paneOver})`, `${label}: horizontal overflow ${JSON.stringify(m)}`);
  for (const c of m.controls) check(c.reachable && c.left >= 0 && c.right <= m.vw + 0.5 && c.top >= 0 && c.bottom <= m.vh + 1, `${label}: ${c.sel} is inside the ${m.vw}x${m.vh} window and not covered`, `${label}: ${JSON.stringify(c)}`);
}

const primary = (p) => p.click('[data-cfw-primary]');
const screen = (p, id) => p.waitForSelector(`[data-cfw-screen="${id}"]`);
const prompt = async (p, code) => { await p.fill('input[id^="hp-passcode-"]', code); await p.click('.modal-content .btn-add'); };
const writes = (s) => s.log.filter((r) => r.path === '/api/desk/connect/commit');
async function restart(page, address = 'plausible.io') {
  await page.evaluate(() => window.DeskV1ConnectWizard.close()); await repaint(page);
  await screen(page, 'service'); await page.fill('[data-cfw-input]', address); await primary(page); await waitStep(page, 'connection');
}
async function path(page, value) {
  await page.waitForFunction(()=>!window.DeskV1ConnectDiscover.state().busy);
  if(value==='reference') { await page.check('[data-cfw-type][value="reference"]'); await primary(page); }
  else { await page.click('[data-cfw-details] > summary');await page.click(value==='api'?'[data-options-api]':'[data-options-manual="custom-npm"]'); }
  await waitStep(page,'setup');
}
async function referenceReview(page) { await primary(page); await screen(page, 'reference-permissions'); await primary(page); await screen(page, 'reference-review'); }
async function run(browser, width, height) {
  console.log(`\nUnknown/reference ${width}x${height}`);
  const srv = makeServer(); const {ctx,page,pageErrors} = await newPage(browser,{srv,width,height});
  check(await page.evaluate(() => window.DeskV1ConnectWizard.enabled()), 'shared flow activated on fresh boot');
  await page.evaluate(() => window.DeskV1ConnectWizard.setEnabled(true)); await page.click('[data-conn-add-tile]');
  await screen(page,'service');
  await page.fill('[data-cfw-input]','Unknown Name'); await primary(page); await page.waitForSelector('[data-cfw-msg="error"]');
  check(/Paste its address/.test(await page.textContent('[data-cfw-msg]')) && logOf(srv,/discover|detect/).length===0,'unknown name asks for an address; no guessed domain or lookup');
  for(const mode of ['pending','failed','empty','incomplete']) {
    srv.discoverMode=mode;await restart(page);
    if(mode==='pending') {
      await page.waitForSelector('[data-options-cancel]');
      check(await page.locator('[data-cfw] input').count()===0,'automatic investigation has no setup form');
      await page.click('[data-options-cancel]');await srv.pending.shift()();
      await page.waitForFunction(()=>!window.DeskV1ConnectDiscover.state().busy);
      check(await page.evaluate(()=>window.DeskV1ConnectDiscover.state().answer===null),'cancel ignores late investigation answer');
    } else {
      await page.waitForFunction(()=>!window.DeskV1ConnectDiscover.state().busy);
      check(await page.locator('[data-cfw-type][value="reference"]').count()===1,mode+' investigation retains information-only fallback');
    }
    if(mode==='failed') check(await page.evaluate(()=>window.DeskV1ConnectDiscover.state().error.includes('timed out')),'exact investigation failure retained in Details');
    await rules(page,'Shared options '+mode);await fits(page,'Shared options '+mode);
  }
  await page.click('[data-cfw-details] > summary');
  check(await page.locator('[data-cfw] img, [data-cfw] a').count()===0 && !(await page.evaluate(()=>window.__injected)),'hostile suggestions escaped; never linked or executed');
  srv.discoverMode='found';
  await restart(page);await path(page,'mcp');await screen(page,'package-setup');
  check(logOf(srv,/custom\/review|custom\/commit/).length===0,'manual software setup performs no installation');
  await restart(page);await page.waitForFunction(()=>!window.DeskV1ConnectDiscover.state().busy);await page.click('[data-cfw-details] > summary');await page.click('[data-options-manual="custom-remote"]');await screen(page,'remote-setup');
  check(logOf(srv,/remote\/review|remote\/check/).length===0,'manual server setup does not contact target');

  // API evidence is editable, retains provenance and saves via 12b only.
  await restart(page); await path(page,'api'); await screen(page,'reference-setup');
  srv.detectMode='failed'; await page.click('[data-ref-detect]'); await page.waitForSelector('[data-ref-detect-status="failed"]');
  srv.detectMode='none'; await page.click('[data-ref-detect]'); await page.waitForSelector('[data-ref-detect-status="none"]');
  check(await page.locator('[data-ref-alt]').count()===0,'failed and empty detection stay distinct; no invented alternative');
  srv.detectMode='pending'; await page.click('[data-ref-detect]');
  for(let i=0;i<100&&!srv.pendingDetect;i++) await new Promise((r)=>setTimeout(r,10));
  await page.fill('[data-ref-field="source"]','https://api.plausible.io/new');
  const late = page.waitForResponse((r)=>r.url().endsWith('/api/desk/connect/detect'));
  await srv.pendingDetect(); await (await late).finished(); await page.evaluate(()=>new Promise(requestAnimationFrame));
  check(await page.locator('[data-ref-alt]').count()===0 && !(await page.locator('[data-ref-detect]').isDisabled()),'source edit clears stale suggestions, ignores pending answer and permits another detection');
  await page.click('[data-cfw-details] > summary'); await page.selectOption('[data-ref-select="kind"]','api_spec');
  await page.fill('[data-ref-field="source"]','https://api.plausible.io/spec');
  await page.click('[data-cfw-details] > summary'); await page.fill('[data-ref-spec-slot] textarea','{"openapi":"3.0.0","paths":{}}');
  srv.detectMode='incomplete'; await page.click('[data-ref-detect]'); await page.waitForSelector('[data-ref-detect-status="incomplete"]');
  check(logOf(srv,/\/detect$/).at(-1).body.text.includes('openapi'),'OpenAPI document is sent only to explicit detection');
  await page.click('[data-ref-alt]'); await page.waitForSelector('[data-ref-field="address"]');
  await page.fill('[data-ref-field="address"]','https://api.plausible.io/edited');
  await rules(page,'API parameters'); await fits(page,'API parameters'); await shot(page,'api',width);
  await primary(page); await page.selectOption('[data-ref-select="mode"]','existing');
  await page.waitForFunction(() => !/^Loading/.test(document.querySelector('[data-ref-vault]')?.textContent || 'Loading'));
  await page.fill('[data-ref-field="vaultName"]','existing.key'); await referenceReview(page);
  const before=writes(srv).length;
  await primary(page);
  await page.waitForSelector('input[id^="hp-passcode-"]');
  check(writes(srv).length===before,'Save sends nothing before its own passcode');
  await page.click('.modal-content .btn-secondary'); await screen(page,'reference-review');
  check(writes(srv).length===before,'cancelled passcode sends nothing');
  await primary(page); await prompt(page,'wrong'); await page.waitForFunction(() => document.querySelector('input[id^="hp-passcode-"]')?.closest('.modal-content')?.textContent.includes('Wrong dashboard passcode'));
  check(srv.saves.size===0,'real guard refusal re-prompts and preserves draft without a save');
  await page.click('.modal-content .btn-secondary');
  srv.commitMode='refuse'; await primary(page); await prompt(page,PASSCODE); await page.waitForFunction(() => document.querySelector('[data-cfw-msg]')?.textContent.includes('Record could not'));
  srv.commitMode='ok'; await primary(page); await prompt(page,PASSCODE); await screen(page,'reference-result');
  const saved=writes(srv).at(-1).body;
  check(writes(srv).slice(before).every((r)=>r.body.request_id===saved.request_id),'wrong-code/refusal/retry retain unchanged request identity');
  check(saved.draft.reference_draft.fields.address.value==='https://api.plausible.io/edited' && saved.draft.reference_draft.fields.address.provenance==='user_input' && saved.draft.reference_draft.fields.auth_type.provenance==='openapi','12b receives edited address and retained detected provenance');
  check(['transport','credential_names','placements','scopes'].every((k)=>saved.draft.reference_draft.fields[k].provenance==='openapi'),'transport, credential parameters and scopes retain the actual detector provenance');
  check(JSON.stringify(saved.draft.credential)===JSON.stringify({name:'existing.key',existing:true}) && /not connected/.test(await page.textContent('[data-cfw-body]')) && /Not checked/.test(await page.textContent('[data-cfw-body]')),'existing name reference saves as reference-only, never Connected or Verified');
  await shot(page,'result',width); await fits(page,'Result');

  // New credentials stay in kept DOM across Back, get their vault policy on
  // Permissions, and are cleared by kind/type/service changes and Close.
  await restart(page); await path(page,'reference'); await page.selectOption('[data-ref-select="mode"]','new');
  await page.fill('[data-ref-field="vaultName"]','new.key'); await page.fill('[data-ref-secret]',SECRET);
  await primary(page); await screen(page,'reference-permissions'); await page.uncheck('[data-ref-unattended]');
  await page.click('[data-cfw-back]'); await screen(page,'reference-setup');
  check(await page.inputValue('[data-ref-secret]')===SECRET && !JSON.stringify(await page.evaluate(()=>window.DeskV1ConnectWizard.state())).includes(SECRET),'Back preserves password only in kept DOM, never wizard state');
  await referenceReview(page); await primary(page); await prompt(page,PASSCODE); await screen(page,'reference-result');
  check(writes(srv).at(-1).body.draft.credential.allow_unattended===false && !await page.locator('[data-ref-secret]').count(),'new credential saved only by final gate with explicit vault policy');
  await restart(page); await path(page,'reference'); await page.selectOption('[data-ref-select="mode"]','new'); await page.fill('[data-ref-secret]',SECRET);
  await page.selectOption('[data-ref-select="entry"]','login');
  check(await page.inputValue('[data-ref-secret]')==='','credential-kind change drops incompatible value');
  await page.fill('[data-ref-secret]',SECRET);
  await page.evaluate(()=>{window.__oldSecret=document.querySelector('[data-ref-secret]');window.DeskV1ConnectWizard.close();}); await repaint(page);
  check(await page.evaluate(()=>window.__oldSecret.value)==='','Close clears held credential DOM');

  // PyPI is another draft, never the npm installer.
  await page.fill('[data-cfw-input]','plausible.io'); await primary(page); await path(page,'api');
  await page.click('[data-cfw-details] > summary'); await page.selectOption('[data-ref-select="kind"]','pypi');
  await page.fill('[data-ref-field="source"]','example-client'); srv.detectMode='complete'; await page.click('[data-ref-detect]'); await page.waitForSelector('[data-ref-alt]'); await page.click('[data-ref-alt]'); await primary(page); await referenceReview(page);
  await primary(page); await prompt(page,PASSCODE); await screen(page,'reference-result');
  check(writes(srv).at(-1).body.draft.reference_draft.fields.package.value==='example-client==1.2.3','PyPI suggestions save labelled package metadata only');
  check(logOf(srv,/verify|purpose\/commit|custom\/commit|remote\/check/).length===0,'no executor, probe, permission mutation, install or spending call');

  // Cached asynchronous results cannot change a newly edited service.
  await restart(page); await page.click('[data-cfw-back]'); srv.holdTypes=true;
  await page.fill('[data-cfw-input]','other.example.com'); await primary(page);
  for(let i=0;i<100&&!srv.pendingTypes;i++) await new Promise(r=>setTimeout(r,10));
  await page.fill('[data-cfw-input]','x.com'); await srv.pendingTypes();
  await page.waitForFunction(()=>!document.querySelector('[data-cfw-primary]')?.disabled);
  check(await page.locator('[data-cfw-screen="service"]').count()===1,'late types answer ignored after service edit');
  // Known LinkedIn exposes the same honest reference-only API fallback.
  await restart(page,'linkedin.com'); await page.click('[data-cfw-details] > summary'); await page.click('[data-options-api]'); await screen(page,'reference-setup');
  check(await page.evaluate(()=>window.DeskV1ConnectWizard.state().sel.type==='reference'),'unavailable built-in app is information only');
  await page.evaluate(()=>document.documentElement.style.fontSize='200%'); await fits(page,'Reference at 200% text'); await page.evaluate(()=>document.documentElement.style.fontSize='');
  check(realErrors(pageErrors).length===0,'no runtime page errors',realErrors(pageErrors).join(' | '));
  await ctx.close();
}
const browser=await chromium.launch();
try { await run(browser,1440,900); await run(browser,390,844); }
catch(e) { fail('smoke aborted: '+(e.stack||e)); }
finally { await browser.close(); }
console.log(bad ? `${bad} check(s) failed` : 'All connect-unknown-step checks passed');
process.exit(bad?1:0);
