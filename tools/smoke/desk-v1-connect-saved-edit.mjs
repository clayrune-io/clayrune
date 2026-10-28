// Production Edit activation against hermetic metadata and passcode endpoints.
// No live account, named profile, external server or credential is accessed.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdirSync} from 'node:fs';
import {resolve} from 'node:path';
import {chromium} from 'playwright';
import {makeServer,newPage} from './connection-language-fixture.mjs';
const shots=resolve('_scratch/saved_connections');mkdirSync(shots,{recursive:true});
const types=JSON.parse(execFileSync(process.env.MC_PYTHON || 'python',['-c',`
import json
from mc.desk_connect import registry, resolve, type_view
out={}
for text in ['https://www.instagram.com','https://aistudio.google.com','https://higgsfield.ai','https://example.com']:
    got=resolve.resolve(text)
    svc=got.pop('service') or registry.lookup(got['host'])
    out[text]={**got,**type_view.project_service(svc['id'] if svc else None)}
print(json.dumps(out))
`],{encoding:'utf8'}));
const site={id:'instagram',name:'www.instagram.com',link:'https://www.instagram.com',browser_connection:{service:null,browser_profile:'main',read:false,post:false,status:{state:'verified'}}};
const engine={id:'google',label:'Google',currency:'usd',connected:{ready:true,state:'ready'},job_limit_usd:10,models:[]};
const keyConnector={service:'google_ai',signs_in:false,fields:[{key:'secret',label:'Gemini API key',kind:'secret',vault:'gemini-api',present:true,required:true},{key:'allow_unattended',label:'Scheduled and unattended runs may use it',kind:'checkbox',default:true}]};
const pairConnector={service:'higgsfield',signs_in:false,fields:[{key:'key_id',label:'API key ID',kind:'text',vault:'higgsfield',saved_entry:true,present:true,required:true},{key:'secret',label:'API key secret',kind:'secret',vault:'higgsfield',present:true,required:true},{key:'allow_unattended',label:'Scheduled and unattended runs may use it',kind:'checkbox',default:true}]};
const remote={ecosystem:'remote',server_name:'steady-remote',scope:'global',project_id:null,url:'https://mcp.example.com/mcp',protocol:'sse',auth:'header',issuer:'',scopes:[],credentials:[{header:'Authorization',prefix:'Bearer ',vault:'example.token'}]};
const npm={ecosystem:'npm',server_name:'steady-mcp',scope:'project',project_id:'p1',package:'harmless-mcp',version:'1.2.3',entry:'dist/index.js',args:['--quiet'],credentials:[{env:'TOKEN',vault:'example.token'}]};
const github={...npm,ecosystem:'github',server_name:'saved-github',package:'https://github.com/example/server',version:'a'.repeat(40),command:'node',args:['dist/index.js'],entry:''};
const card={request_id:'fixture-request-id',fingerprint:'sha256:'+'b'.repeat(64),title:'saved server',server_name:'steady-mcp',protocol:'stdio',command:['node','dist/index.js'],package:{version:'1.2.3',pinned:true},credentials:[],reach:{scope:'project',who:'Agents in this project only.'},risks:[],scripts:[],required_steps:[],limitations:[]};
const writes=srv=>srv.log.filter(r=>r.method!=='GET' && !/connect\/(types|inspect|purposes|signin\/options|custom\/(connections|saved-draft|review|remote\/review|github\/stage))$/.test(r.path));
const prompt=p=>p.waitForSelector('input[id^="hp-passcode-"]');
async function answer(p,cancel=false) {const input=await prompt(p),id=await input.getAttribute('id');if(cancel) await p.click('.modal-content .btn-secondary');else {await p.fill(`#${id}`,'test-passcode');await p.click('.modal-content .btn-add');}await p.waitForSelector(`#${id}`,{state:'detached'});}
function server() {
 const srv=makeServer({services:[structuredClone(site)]});srv.production=true;srv.engines=[structuredClone(engine),{...engine,id:'higgsfield_mcp',label:'Higgsfield',auth:{kind:'oauth',service:'higgsfield'}},{...engine,id:'higgsfield',label:'Higgsfield API key'}];
 srv.fx.projects.push({id:'p1',name:'Saved project'});
 srv.connections.push({...npm,server_name:'saved-github',ecosystem:'github',state:'registered',package:github.package,version:github.version});
 srv.agentRead={main:{enabled:true,domains:['youtube.com']}};
 srv.handle=async ({path,method,req,J})=>{
  const send=async body=>{await J(body);return true;};
  let b;try{b=req.postDataJSON();}catch{b=null;}
  if(path==='/api/desk/connect/types') {srv.log.push({path,method,body:b});return send(types[b.input]);}
  if(path==='/api/desk/connect/inspect') {srv.log.push({path,method,body:b});return send({url:b.input,options:b.input.includes('higgsfield')?[{method:'oauth',connector:{service:'higgsfield',fields:[],signs_in:true}},{method:'api_key',connector:pairConnector}]:[{method:'api_key',connector:keyConnector}]});}
  if(path==='/api/desk/connect/custom/saved-draft') {srv.log.push({path,method,body:b});if(srv.failDraft){await J({error:'fixture read failure'},500);return true;} return send(b.server_name==='saved-github'?github:b.server_name==='steady-remote'?remote:npm);}
  if(path==='/api/desk/connect/custom/github/stage') {srv.log.push({path,method,body:b});return send({stage_id:'fixture-stage',sha:github.version,command:'node',args:['dist/index.js'],credentials:[]});}
  if(path==='/api/desk/connect/custom/review' || path==='/api/desk/connect/custom/remote/review') {srv.log.push({path,method,body:b});return send({...card,server_name:b.server_name,...(path.includes('/remote/') ? {url:b.url,protocol:b.protocol,auth:{type:b.auth},contact:{text:'Nothing is contacted before Save.',before_save:false},verification:{purpose_verified:false,text:'A check only lists tools.'},command:{command:'python',args:[]},recipient:'mcp.example.com',reach:{scope:b.scope,who:'Agents in every project.'}}:{})});}
  if(path==='/api/secrets') return send({secrets:[{name:'gemini-api',scope:'global',allow_unattended:false},{name:'higgsfield',scope:'global',allow_unattended:false},{name:'example.token',scope:'global'}]});
  if(path==='/api/secrets/vault-lock') return send({state:'unlocked',configured:true});
  if(path==='/api/browser/profiles') return send({profiles:[{name:'main'}]});
  if(path==='/api/browser/agent-read') return send({profiles:srv.agentRead});
  if(path==='/api/desk/connect/purposes') return send({accounts:[]});
  if(path==='/api/desk/connect/saved-site/instagram/account') {
   srv.log.push({path,method,body:b});assert.equal(b.passcode,'test-passcode');
   assert.deepEqual(b.source,{url:site.link,browser_profile:'main',signin_method:'other'});
   if(!srv.accounts.find(a=>a.id==='attached-instagram')) srv.accounts.push({id:'attached-instagram',platform:'instagram',identity:b.account.identity.replace(/^@/,''),label:'Instagram account',capability:'assisted',browser_profile:'main'});
   srv.services[0].account_id='attached-instagram';return send({ok:true,account_id:'attached-instagram',unchanged:false});
  }
  if(path==='/api/browser/profiles/main/agent-read' && method==='PUT') {srv.log.push({path,method,body:b});assert.equal(b.passcode,'test-passcode');srv.agentRead.main={enabled:b.enabled,domains:b.domains};return send({ok:true,...srv.agentRead.main});}
  return false;
 };
 return srv;
}
async function edit(page,id) {
 if(await page.locator(`[data-conn-tile="${id}"]`).getAttribute('aria-pressed') !== 'true') await page.click(`[data-conn-tile="${id}"]`);
 await page.getByRole('button',{name:'Edit',exact:true}).click();
 await page.waitForSelector('[data-cfw-step="permissions"]');
 await page.waitForFunction(()=>!document.querySelector('[data-cfp-loading]'));
}
async function capture(page,name) {
 await page.evaluate(()=>{const wizard=document.querySelector('[data-cfw]');if(wizard){wizard.scrollIntoView({block:'start'});wizard.querySelector('.desk-v1-cfw-scroll')?.scrollTo(0,0);}});
 await page.screenshot({path:resolve(shots,name),animations:'disabled'});
}
const browser=await chromium.launch();
try {for(const [width,height] of [[390,844],[1440,1000]]) {
 const srv=server();const {ctx,page,pageErrors}=await newPage(browser,{srv,width,height});
 await page.click('[data-conn-tile="service:instagram"]');
 assert.equal(await page.getByRole('button',{name:'Edit',exact:true}).isEnabled(),true);
 await page.screenshot({path:resolve(shots,`instagram-card-${width}.png`),animations:'disabled'});
 await edit(page,'service:instagram');
 assert.deepEqual(await page.locator('[data-cfp-box]').evaluateAll(ns=>ns.map(n=>n.dataset.cfpBox)),['read','post']);
 assert.equal(await page.locator('[data-cfw-primary]').isDisabled(),true,'no guessed handle');
 assert.equal(await page.locator('[data-cfp-box="read"]').isEnabled(),true);
 assert.equal(await page.locator('[data-cfp-box="post"]').isDisabled(),true,'no invented publishing route');
 await page.fill('[data-site-identity]','@example');await page.check('[data-cfp-box="read"]');
 await capture(page,`instagram-permissions-${width}.png`);
 assert.equal(writes(srv).length,0);
 await page.click('[data-cfw-primary]');await page.waitForSelector('[data-cfw-step="review"]');
 await page.click('[data-cfw-primary]');await answer(page,true);
 await page.waitForFunction(()=>!DeskV1ConnectSummaryStep.state().saving);
 assert.equal(writes(srv).length,0,'cancel attachment sends nothing');
 await page.click('[data-cfw-primary]');await answer(page);await answer(page);
 await page.waitForSelector('[data-cfw-step="result"]');
 assert.equal(srv.log.filter(r=>r.path.endsWith('/account')).length,1);
 assert.deepEqual(srv.agentRead.main.domains,['youtube.com','instagram.com']);
 assert.equal(srv.log.filter(r=>/browser.*(launch|input|check)/.test(r.path)).length,0);
 await page.click('[data-cfw-primary]');
 await page.waitForSelector('[data-conn-tile="attached-instagram"]');
 await page.waitForSelector('[data-conn-tile="service:instagram"]',{state:'detached'});
 assert.equal(await page.locator('[data-conn-tile="service:instagram"]').count(),0,'attached reference no longer duplicates its account');
 await edit(page,'engine:google');
 console.log(`${width}: engine Permissions`);
 assert.match(await page.locator('[data-cfw]').innerText(),/no separate Read or Post permission/);
 await capture(page,`engine-permissions-${width}.png`);
 await page.click('[data-cfw-back]');await page.waitForSelector('[data-cfw-step="setup"]');
 assert.equal(await page.locator('[data-cfs-mode]').inputValue(),'stored');
 assert.equal(await page.locator('[data-cfa-field="allow_unattended"]').isChecked(),false,'saved unattended policy is preserved');
 await capture(page,`engine-setup-${width}.png`);
 await page.click('[data-cfw-primary]');await page.click('[data-cfw-primary]');
 await page.waitForSelector('[data-cfw-step="review"]');
 const before=writes(srv).length;
 await page.click('[data-cfw-primary]');await page.waitForSelector('[data-cfw-step="result"]');
 console.log(`${width}: unchanged engine saved`);
 assert.equal(writes(srv).length,before,'unchanged engine does not rewrite credentials or reauthorize');
 await page.click('[data-cfw-primary]');
 await edit(page,'engine:google');await page.click('[data-cfw-back]');
 await page.waitForSelector('[data-cfw-step="setup"]');
 await page.selectOption('[data-cfs-mode]','saved');await page.selectOption('[data-cfs-name]','example.token');
 await page.click('[data-cfw-primary]');await page.waitForSelector('[data-cfw-step="permissions"]');await page.click('[data-cfw-primary]');
 await page.waitForSelector('[data-cfw-step="review"]');
 assert.equal(await page.locator('[data-cfs-confirm-replace]').isChecked(),false,'restored engine replacement needs explicit confirmation');
 await page.check('[data-cfs-confirm-replace]');
 await page.click('[data-cfw-primary]');await answer(page,true);
 assert.equal(writes(srv).length,before,'cancelled engine replacement sends no write');
 await page.click('[data-conn-tile="engine:google"]');
 await edit(page,'engine:higgsfield_mcp');await page.click('[data-cfw-back]');
 console.log(`${width}: OAuth engine Setup`);
 await page.waitForSelector('[data-cfw-step="setup"]');await page.click('[data-cfw-primary]');await page.click('[data-cfw-primary]');
 await page.waitForSelector('[data-cfw-step="review"]');await page.click('[data-cfw-primary]');await page.waitForSelector('[data-cfw-step="result"]');await page.click('[data-cfw-primary]');
 await edit(page,'engine:higgsfield');await page.click('[data-cfw-back]');
 await page.waitForSelector('[data-cfw-step="setup"]');
 assert.deepEqual(await page.locator('[data-cfs-mode]').evaluateAll(ns=>ns.map(n=>n.value)),['stored','stored'],'saved key ID and secret need no retyping');
 assert.equal(await page.locator('[data-cfa-field="key_id"]').isVisible(),false,'stored key ID offers no ignored typed input');
 await page.click('[data-cfw-primary]');await page.waitForSelector('[data-cfw-step="permissions"]');await page.click('[data-cfw-primary]');
 await page.waitForSelector('[data-cfw-step="review"]');await page.click('[data-cfw-primary]');await page.waitForSelector('[data-cfw-step="result"]');await page.click('[data-cfw-primary]');
 for(const [id,draft] of [['mcp:project:p1:steady-mcp',npm],['mcp:global::steady-remote',remote],['mcp:project:p1:saved-github',github]]) {
  console.log(`${width}: ${id}`);
  await edit(page,id);
  assert.equal(await page.locator('[data-cfp-scope]:checked').inputValue(),draft.scope);
  if(draft.scope==='project') assert.equal(await page.locator('[data-cfp-project]').inputValue(),'p1');
  await page.click('[data-cfw-back]');await page.waitForSelector('[data-cfw-step="setup"]');
  if(draft.ecosystem==='remote') assert.equal(await page.locator('[data-rs-url]').inputValue(),draft.url);
  else {assert.match(await page.locator('[data-cfw]').innerText(),new RegExp(draft.ecosystem==='github'?'github.com':'harmless-mcp'));assert.equal(await page.locator('[data-pk-args]').inputValue(),draft.args.join('\n'));}
  // Return from saved Setup through Permissions to the existing immutable Review.
  if(draft.ecosystem==='remote') {
   await page.click('[data-cfw-primary]');await page.click('[data-cfw-primary]');await page.click('[data-cfw-primary]');
  } else await page.click('[data-cfw-primary]');
  await page.waitForSelector('[data-cfw-step="permissions"]');await page.click('[data-cfw-primary]');
  await page.waitForSelector(draft.ecosystem==='remote'?'[data-rs-origin]':'[data-pk-origin]');
  const review=srv.log.filter(r=>r.path.endsWith('/review')).at(-1);
  assert.ok(review,'review used the existing owner');
  assert.equal(review.body.server_name,draft.server_name);
  if(draft.ecosystem==='remote') {assert.equal(review.body.auth,'header');assert.deepEqual(review.body.credentials,remote.credentials);}
  else {assert.deepEqual(review.body.credentials,npm.credentials);assert.deepEqual(review.body.args,draft.args);}
  assert.equal(await page.locator('[data-pk-approve]:checked, [data-rs-approve]:checked').count(),0,'no restored approval');
  await page.click('[data-cfw-primary]');
  const approval=page.locator(draft.ecosystem==='remote'?'[data-rs-approve]':'[data-pk-approve]');
  await approval.waitFor();assert.equal(await approval.isChecked(),false);
  assert.equal(await page.locator('[data-cfw-primary]').isDisabled(),true,'saved draft still needs approval');
  await approval.check();assert.equal(await page.locator('[data-cfw-primary]').isEnabled(),true);
  const beforeSave=writes(srv).length;
  await page.click('[data-cfw-primary]');await answer(page,true);
  assert.equal(writes(srv).length,beforeSave,'cancelled saved MCP commit sends nothing: '+JSON.stringify(writes(srv).map(r=>r.path)));
  await page.click('[data-conn-tile="engine:google"]');
 }
 srv.failDraft=true;await page.click('[data-conn-tile="mcp:project:p1:steady-mcp"]');await page.getByRole('button',{name:'Edit',exact:true}).click();
 await page.waitForTimeout(100);
 assert.equal(await page.locator('[data-cfw]').count(),0,'failed draft stays on the saved card');
 assert.equal(srv.log.filter(r=>/\/verify$|\/check$|\/start-held$|\/browser\/(launch|input)$/.test(r.path)).length,0);
 assert.deepEqual(pageErrors.filter(m=>!/aborted|net::ERR|Failed to fetch|EventSource/i.test(m)),[]);
 console.log(`PASS saved attachment, engine/MCP Edit, cancellation, preserved draft and gates at ${width}`);
 await ctx.close();
}} finally {await browser.close();}
