// MC-1098: actual worktree SPA, intercepted fixture API; never contacts live data.
import assert from 'node:assert/strict';
import {readFileSync, mkdirSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, resolve} from 'node:path';
import {chromium} from 'playwright';
import {execFileSync} from 'node:child_process';
import {loadFixtures, workspaceFromFixtures} from './desk-v1-fixture-api.mjs';
import {loadStaticJsCss} from './_static.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const STATIC = loadStaticJsCss(ROOT);
const HTML = readFileSync(resolve(ROOT, 'static/index.html'), 'utf8');
const SHOTS = resolve(ROOT, '_scratch/mc1098');
mkdirSync(SHOTS, {recursive:true});
const ORIGIN = 'http://mc.smoke.test', PASSCODE = 'fixture-passcode';
const GOOGLE = JSON.parse(execFileSync(process.env.MC_PYTHON || 'python', ['-c',
  "import json; from mc.desk_connect import resolve, type_view; r=resolve.resolve('https://aistudio.google.com'); r.pop('service'); print(json.dumps({**r, **type_view.project_service('google_ai')}))"], {cwd:ROOT,encoding:'utf8'}));
const check = (value, text) => { assert.ok(value, text); console.log(`PASS ${text}`); };

function server() {
  const fx = loadFixtures();
  const connections = ['npm','github','remote','pypi'].map((ecosystem, i) => ({
    ecosystem, scope:i === 1 ? 'project':'global', project_id:i === 1 ? 'p1':null,
    server_name:i === 1 ? 'youtube-mcp':`${ecosystem}-server`, state:i === 1 ? 'setup_failed':'registered',
    package:i === 1 ? 'https://github.com/ZubeidHendricks/youtube-mcp-server':'fixture-package',
    message:i === 1 ? 'Saved connection is not registered.':'Registered; not checked.',
    version:'1.0.0', credentials:[],
  }));
  const account = {...fx.channels.find(c => c.platform === 'youtube'), id:'test-account', identity:'fixture', label:'YouTube account', capability:'none', preview:false, publish:{ready:false}};
  const services = [{id:'reference', name:'Saved service', link:'https://example.com', credential:{}},
    {id:'browser', name:'Browser service', link:'https://example.com', browser_connection:{browser_profile:'fixture-profile',status:{state:'registered'}}}];
  const engines = ['google','higgsfield_mcp'].map(id => ({id, label:id === 'google' ? 'Google AI':'Higgsfield',
    auth:id === 'google' ? {kind:'api_key'} : {kind:'oauth',service:'higgsfield'},
    connected:{ready:true}, models:[], job_limit_usd:5, job_limit_credits:5,
    currency:id === 'google' ? 'usd':'credits'}));
  return {fx,connections,accounts:[account],services,engines,log:[],refuse:false};
}

async function setup(browser, width) {
  const context = await browser.newContext({viewport:{width,height:width === 390 ? 844:900}});
  const page = await context.newPage(), srv = server(), errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), path = new URL(req.url()).pathname, method = req.method();
    const json = (body, status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
    if (path === '/') return route.fulfill({contentType:'text/html',body:HTML});
    if (STATIC[path]) return route.fulfill({contentType:STATIC[path][0],body:STATIC[path][1]});
    if (path === '/api/projects') return json(srv.fx.projects.map(p => ({...p,status:'active',domain:'general',project_path:'/fixture/'+p.id,roster:[],backlog:[],use_streaming_agent:true})));
    if (path === '/api/config') return json({desk_v1:true,desk_v1_live:true,user_timezone:''});
    if (path === '/api/characters') return json([]);
    if (path === '/api/local-auth/status') return json({configured:true});
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null; try { body = req.postDataJSON(); } catch (_) {}
    srv.log.push({path,method,body});
    if (path === '/api/desk/workspace') return json({...workspaceFromFixtures(srv.fx),accounts:srv.accounts,pieces:[],studio:{}});
    if (path === '/api/desk/accounts' && method === 'GET') return json(srv.accounts);
    if (path === '/api/desk/services' && method === 'GET') return json(srv.services);
    if (path === '/api/desk/engines' && method === 'GET') return json({engines:srv.engines});
    if (path.includes('/engagement/coverage/')) return json({coverage:[]});
    if (path === '/api/desk/connect/custom/connections' && method === 'POST') return json({connections:srv.connections});
    if (path === '/api/desk/connect/types') return json(GOOGLE);
    if (method === 'PUT' && path === '/api/desk/engines/google/connection') {
      if (body?.passcode !== PASSCODE) return json({error:'bad_passcode'},403);
      srv.engines.find(e => e.id === 'google').connected = {ready:true};
      return json({ok:true,connection:{ready:true}});
    }
    if (method === 'DELETE') {
      if ((path.includes('/custom/') || path.includes('/engines/')) && body?.passcode !== PASSCODE) return json({error:'bad_passcode'},403);
      if (srv.refuse) return json({error:'Connection is in use.'},409);
      if (path === '/api/desk/connect/custom/connections') srv.connections = srv.connections.filter(r => !(r.server_name === body.server_name && r.scope === body.scope && r.project_id === (body.project_id || null)));
      else if (path.includes('/accounts/')) srv.accounts = srv.accounts.filter(r => !path.endsWith('/'+r.id));
      else if (path.includes('/services/')) srv.services = srv.services.filter(r => !path.endsWith('/'+r.id));
      else if (path.includes('/engines/')) srv.engines.forEach(e => {if (path.includes('/'+e.id+'/')) e.connected = {ready:false,state:'disconnected'};});
      else return json({error:'unexpected removal'},500);
      return json({ok:true});
    }
    return route.abort();
  });
  await page.goto(ORIGIN, {waitUntil:'domcontentloaded'});
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.desk-v1-shell');
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-conn-tile="mcp:project:p1:youtube-mcp"]');
  await page.waitForSelector('[data-conn-tile="engine:google"]');
  return {context,page,srv,errors};
}

const deletes = srv => srv.log.filter(r => r.method === 'DELETE');
async function select(page, key) {
  await page.locator(`[data-conn-tile="${key}"]`).click();
  await page.locator('[data-connection-remove]').waitFor();
}
async function passcode(page, text=PASSCODE) {
  const input = page.locator('input[id^="hp-passcode-"]');
  await input.waitFor(); await input.fill(text); await input.press('Enter');
}

const browser = await chromium.launch();
try {
  for (const width of [1440,390]) {
    const {context,page,srv,errors} = await setup(browser,width);
    const key = 'mcp:project:p1:youtube-mcp';
    await select(page,key);
    await page.locator('[data-connection-remove]').scrollIntoViewIfNeeded();
    await page.screenshot({path:resolve(SHOTS,`remove_${width}.png`)});
    await page.click('[data-connection-remove]');
    check(deletes(srv).length === 0, `${width}: opening Remove writes nothing`);
    const sheet = page.locator('.desk-v1-rules-confirm-overlay');
    check((await sheet.innerText()).includes('Your saved login stays in the Vault.'), `${width}: shared confirmation explains saved login`);
    await page.screenshot({path:resolve(SHOTS,`confirm_${width}.png`)});
    await page.click('[data-confirm-decline]');
    check(deletes(srv).length === 0 && await page.locator(`[data-conn-tile="${key}"]`).count(), `${width}: cancelling keeps the failed entry`);
    await page.click('[data-connection-remove]'); await page.keyboard.press('Escape');
    check(deletes(srv).length === 0, `${width}: Escape cancels without a write`);
    await page.click('[data-connection-remove]'); await page.click('[data-confirm-accept]');
    await page.locator('input[id^="hp-passcode-"]').waitFor();
    check(deletes(srv).length === 0, `${width}: confirming still requires the passcode`);
    await page.getByRole('button',{name:'Cancel',exact:true}).last().click();
    await page.waitForFunction(() => !document.querySelector('[data-connection-remove]').disabled);
    check(deletes(srv).length === 0, `${width}: cancelling the passcode leaves the row`);
    await page.click('[data-connection-remove]'); await page.click('[data-confirm-accept]');
    await passcode(page,'wrong');
    await page.getByText('Wrong dashboard passcode.',{exact:true}).waitFor();
    check(await page.locator(`[data-conn-tile="${key}"]`).count(), `${width}: wrong passcode keeps the row`);
    await passcode(page);
    await page.locator(`[data-conn-tile="${key}"]`).waitFor({state:'detached'});
    const request = deletes(srv).at(-1);
    check(request.body.server_name === 'youtube-mcp' && request.body.project_id === 'p1', `${width}: removal targets the exact failed connection`);

    for (const [kind,id,human] of [['account','test-account',false],['service','reference',false],['service','browser',false],
      ['mcp','global::npm-server',true],['mcp','global::remote-server',true],['mcp','global::pypi-server',true],
      ['engine','google',true],['engine','higgsfield_mcp',true]]) {
      const target = kind === 'account' ? id : `${kind}:${id}`;
      await select(page,target);
      const before = deletes(srv).length;
      await page.click('[data-connection-remove]');
      check(deletes(srv).length === before, `${width}: ${kind} ${id} waits for confirmation`);
      await page.click('[data-confirm-decline]');
      check(deletes(srv).length === before, `${width}: ${kind} ${id} cancels`);
      if (kind === 'account') {
        srv.refuse = true;
        await page.click('[data-connection-remove]'); await page.click('[data-confirm-accept]');
        await page.locator('[data-connection-remove-status]').filter({hasText:'Connection is in use.'}).waitFor();
        check(await page.locator(`[data-conn-tile="${target}"]`).count(), `${width}: refused removal keeps the account`);
        srv.refuse = false;
      }
      await page.click('[data-connection-remove]'); await page.click('[data-confirm-accept]');
      if (human) await passcode(page);
      await page.locator(`[data-conn-tile="${target}"]`).waitFor({state:'detached'});
      check(true, `${width}: ${kind} ${id} disappears after server success`);
    }
    check(!srv.log.some(r => /secrets|oauth.*disconnect/.test(r.path)), `${width}: no credential deletion or provider sign-out`);
    await page.click('[data-conn-add-tile]');
    await page.fill('[data-cfw-input]', 'https://aistudio.google.com');
    await page.click('[data-cfw-primary]');
    await page.locator('[data-cfw-option="api"] input').check();
    await page.click('[data-cfw-primary]');
    await page.locator('[data-engine-reconnect]').waitFor({timeout:4000});
    check(!(await page.locator('[data-cfa-field]').count()), `${width}: reconnect reuses the saved login without credential inputs`);
    await page.click('[data-cfw-primary]');
    // The production integration can omit inapplicable engine permissions.
    if (await page.locator('[data-cfw]').getAttribute('data-cfw-step') === 'permissions') await page.click('[data-cfw-primary]');
    await page.waitForFunction(() => document.querySelector('[data-cfw-primary]')?.textContent.trim() === 'Save');
    check(!srv.log.some(r => r.method === 'PUT'), `${width}: reconnect review writes nothing`);
    await page.click('[data-cfw-primary]');
    await passcode(page);
    await page.waitForFunction(() => document.querySelector('[data-cfw]')?.dataset.cfwStep === 'result');
    check(srv.log.filter(r => r.method === 'PUT' && r.path === '/api/desk/engines/google/connection').length === 1,
      `${width}: explicit Add service Save reconnects the engine once`);
    check(!srv.log.some(r => r.path === '/api/desk/connect/commit'), `${width}: reconnect never submits a credential draft`);
    check(!errors.length, `${width}: no page errors: ${errors.join('; ')}`);
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${width}: no page overflow`);
    await context.close();
  }
} finally { await browser.close(); }
