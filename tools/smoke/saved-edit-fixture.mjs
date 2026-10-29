// Production Edit activation against hermetic metadata and passcode endpoints.
// No live account, named profile, external server or credential is accessed.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdirSync} from 'node:fs';
import {resolve} from 'node:path';
import {makeServer} from './connection-language-fixture.mjs';
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
export const remote={ecosystem:'remote',server_name:'steady-remote',scope:'global',project_id:null,url:'https://mcp.example.com/mcp',protocol:'sse',auth:'header',issuer:'',scopes:[],credentials:[{header:'Authorization',prefix:'Bearer ',vault:'example.token'}]};
export const npm={ecosystem:'npm',server_name:'steady-mcp',scope:'project',project_id:'p1',package:'harmless-mcp',version:'1.2.3',entry:'dist/index.js',args:['--quiet'],credentials:[{env:'TOKEN',vault:'example.token'}]};
export const github={...npm,ecosystem:'github',server_name:'saved-github',package:'https://github.com/example/server',version:'a'.repeat(40),command:'node',args:['dist/index.js'],entry:''};
const card={request_id:'fixture-request-id',fingerprint:'sha256:'+'b'.repeat(64),title:'saved server',server_name:'steady-mcp',protocol:'stdio',command:['node','dist/index.js'],package:{version:'1.2.3',pinned:true},credentials:[],reach:{scope:'project',who:'Agents in this project only.'},risks:[],scripts:[],required_steps:[],limitations:[]};
export const writes=srv=>srv.log.filter(r=>r.method!=='GET' && r.path!=='/api/diag/viewport' && !/connect\/(types|inspect|purposes|signin\/options|custom\/(connections|saved-draft|review|remote\/review|github\/stage))$/.test(r.path));
const prompt=p=>p.waitForSelector('input[id^="hp-passcode-"]');
export async function answer(p,cancel=false) {const input=await prompt(p),id=await input.getAttribute('id');if(cancel) await p.click('.modal-content .btn-secondary');else {await p.fill(`#${id}`,'test-passcode');await p.click('.modal-content .btn-add');}await p.waitForSelector(`#${id}`,{state:'detached'});}
export function server() {
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
export async function edit(page,id) {
 if(await page.locator(`[data-conn-tile="${id}"]`).getAttribute('aria-pressed') !== 'true') await page.click(`[data-conn-tile="${id}"]`);
 await page.getByRole('button',{name:'Edit',exact:true}).click();
 await page.waitForSelector('[data-cfw-step="permissions"]');
 await page.waitForFunction(()=>!document.querySelector('[data-cfp-loading]'));
}
export async function capture(page,name) {
 await page.evaluate(()=>{const wizard=document.querySelector('[data-cfw]');if(wizard){wizard.scrollIntoView({block:'start'});wizard.querySelector('.desk-v1-cfw-scroll')?.scrollTo(0,0);}});
 await page.screenshot({path:resolve(shots,name),animations:'disabled'});
}
