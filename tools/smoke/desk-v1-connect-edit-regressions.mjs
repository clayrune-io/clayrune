// MC-1118: each review finding has a separately runnable production-UI regression.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {chromium} from 'playwright';
import {newPage} from './connection-language-fixture.mjs';
import {server,edit,answer,remote} from './saved-edit-fixture.mjs';

const which=process.argv.find(a=>a.startsWith('--case='))?.split('=')[1];
const browser=await chromium.launch();
let failures=0;
try {for(const test of ['attachment','engine','prefix','site','label']) {
 if(which && which!==test) continue;
 const srv=server(), base=srv.handle;
 const generic={id:'generic',name:'Example',link:'https://example.com',browser_connection:{service:null,browser_profile:'main',read:false,post:false}};
 srv.services.push(generic);
 const policies=[];
 srv.handle=async req=>{
  const {path,method,req:r,J}=req;
  let b;try {b=r.postDataJSON();} catch {b=null;}
  if(path==='/api/desk/connect/custom/saved-draft' && b.server_name==='steady-remote') {await J({...remote,credentials:[{...remote.credentials[0],prefix:''}]});return true;}
  if(path==='/api/desk/connect/permissions/commit') {policies.push(b.draft);srv.log.push({path,method,body:b});srv.policy={state:'explicit',...b.draft};await J({policy:srv.policy});return true;}
  if(path==='/api/desk/connect/commit') {srv.log.push({path,method,body:b});await J({ok:true,service:{id:b.draft.url.includes('higgsfield')?'higgsfield':'google_ai'},method:'api_key',account_id:null,status:{state:'key_stored'}});return true;}
  if(path==='/api/desk/connect/browser-profile/commit' || path.includes('/browser-profile/')) {srv.log.push({path,method,body:b});await J({ok:true,connection_id:generic.id,...b.draft,status:{state:'not_checked'}});return true;}
  return base(req);
 };
 const {ctx,page,pageErrors}=await newPage(browser,{srv,width:1440,height:1000});
 try {
  if(test==='attachment') {
   await edit(page,'service:instagram');await page.fill('[data-site-identity]','example');
   assert.equal(await page.locator('[data-cfp-box="read"]').isDisabled(),true);
   assert.match(await page.locator('[data-cfw-option="read"]').innerText(),/Reading isn't available for Instagram yet/);
   await page.click('[data-cfw-primary]');await page.waitForSelector('[data-cfw-step="review"]');
   await page.click('[data-cfw-primary]');await answer(page);
   // Answer each independent permission prompt until Result appears.
   for(let i=0;i<3;i++) {await page.waitForSelector('[data-cfw-step="result"], input[id^="hp-passcode-"]');if(await page.locator('[data-cfw-step="result"]').count()) break;await answer(page);}
   await page.waitForSelector('[data-cfw-step="result"]');
   execFileSync(process.env.MC_PYTHON || 'python',['-c',`
import json, tempfile
from pathlib import Path
from mc import desk, desk_engagement as eng
from mc.desk_connect import saved_site_account as sites, permission_policy as policy
from mc.desk_engagement_poll import _require_read
from mc.desk_connect.permission_check import PermissionDenied
with tempfile.TemporaryDirectory() as tmp:
 desk.STORE_PATH=Path(tmp)/'desk.json'
 store=desk._read_store()
 store.setdefault('services',{})['saved']={'id':'saved','link':'https://www.instagram.com','browser_connection':{'service':None,'browser_profile':'main'}}
 desk._write_store(store)
 aid=sites.attach('saved',{'identity':'example'})['account_id']
 for draft in json.loads(input()):
  draft['account_id']=aid
  policy.commit('regression-read-policy',draft)
 reader=eng.Reader();reader.via='pane'
 for cap in ['mentions','post_metrics']:
  try: _require_read(aid,'instagram',reader,cap)
  except PermissionDenied: pass
  else: raise AssertionError('attachment must retain default-deny')
 assert policy.read_policy(desk._read_store()['accounts'][aid])['read'] is False
 assert policy.read_policy(desk._read_store()['accounts'][aid])['post'] is False
`],{input:JSON.stringify(policies),encoding:'utf8'});
   assert.equal(policies.length,0,'unsupported Read never writes consent');
   assert.deepEqual(srv.agentRead.main.domains,['youtube.com'],'unsupported Read never grants a browser domain');
  }
  if(test==='engine') {
   for(const [id,field] of [['google',null],['higgsfield','secret'],['higgsfield','key_id']]) {
    await edit(page,`engine:${id}`);await page.click('[data-cfw-back]');await page.waitForSelector('[data-cfw-step="setup"]');
    if(field) {
     const box=page.locator(`[data-cfs-field="${field}"]`);await box.locator('[data-cfs-mode]').selectOption('saved');await box.locator('[data-cfs-name]').selectOption('example.token');
    } else await page.check('[data-cfa-field="allow_unattended"]');
    await page.click('[data-cfw-primary]');await page.waitForSelector('[data-cfw-step="permissions"]');await page.click('[data-cfw-primary]');await page.waitForSelector('[data-cfw-step="review"]');
    if(field) await page.check('[data-cfs-confirm-replace]');
    await page.click('[data-cfw-primary]');await answer(page);await page.waitForSelector('[data-cfw-step="result"]');
    const fields=srv.log.filter(r=>r.path==='/api/desk/connect/commit').at(-1).body.draft.fields;
    assert.equal(fields.edit_saved,true,'saved engines send an explicit update');
    assert.deepEqual(fields[field==='secret'?'key_id':'secret'],{keep:true},'the untouched credential half is preserved');
    await page.click('[data-cfw-primary]');
   }
  }
  if(test==='prefix') {
   await edit(page,'mcp:global::steady-remote');await page.click('[data-cfw-back]');
   for(let i=0;i<4;i++) await page.click('[data-cfw-primary]');
   await page.waitForSelector('[data-rs-origin]');
   assert.equal(srv.log.filter(r=>r.path.endsWith('/remote/review')).at(-1).body.credentials[0].prefix,'','an empty prefix must reach backend review');
  }
  if(test==='site') {
   for(const [selector,value] of [['[data-bp-profile]','other-profile'],['[data-bp-name]','Renamed Example']]) {
    await edit(page,'service:generic');await page.click('[data-cfw-back]');await page.waitForSelector('[data-cfw-step="setup"]');await page.fill(selector,value);
    await page.click('[data-cfw-primary]');await page.click('[data-cfw-primary]');await page.waitForSelector('[data-cfw-step="review"]');await page.click('[data-cfw-primary]');await answer(page);await page.waitForSelector('[data-cfw-step="result"]');
    const write=srv.log.filter(r=>r.path.includes('/browser-profile/')).at(-1);
    assert.equal(write.method,'PATCH','Edit must update rather than create');
    assert.equal(write.path,'/api/desk/connect/browser-profile/generic');
    await page.click('[data-cfw-primary]');
   }
  }
  if(test==='label') assert.equal(await page.locator('[data-conn-recheck]').innerText(),'Check now');
  assert.deepEqual(pageErrors.filter(m=>!/aborted|net::ERR|Failed to fetch|EventSource/i.test(m)),[]);
  console.log(`PASS MC-1118 ${test}`);
 } catch(e) {failures++;console.error(`FAIL MC-1118 ${test}: ${e.stack}`);} finally {await ctx.close();}
}} finally {await browser.close();}
process.exitCode=failures?1:0;
