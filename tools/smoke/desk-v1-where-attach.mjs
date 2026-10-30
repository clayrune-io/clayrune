// Real production SPA; only API responses are fixture-owned. No live Desk writes.
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {execFileSync} from 'node:child_process';
import {mkdirSync} from 'node:fs';
import {resolve} from 'node:path';
import {newPage} from './connection-language-fixture.mjs';
import {server,answer,writes} from './saved-edit-fixture.mjs';
const before=process.argv.includes('--baseline');
const shots=resolve('_scratch/where_attach');mkdirSync(shots,{recursive:true});
const catalog=JSON.parse(execFileSync('python',['-c','import json; from flask import Flask; from mc.blueprints.desk_connect_routes import bp; app=Flask(__name__); app.register_blueprint(bp); print(json.dumps(app.test_client().get("/api/desk/connect/services").get_json()["services"]))'],{encoding:'utf8'}));
const browser=await chromium.launch();
try {for(const [width,height] of [[1440,1000],[390,844]]) {
 const srv=server(), original=srv.handle;
 let failAttach=false, failTypes=false, holdTypes=null, holdAccounts=null;
 srv.handle=async args=>{
  if(args.path==='/api/desk/connect/services') {await args.J({services:catalog});return true;}
  if(args.path==='/api/desk/connect/types') {
   if(holdTypes) await holdTypes;
   if(failTypes) {failTypes=false;await args.J({error:'Fixture setup unavailable'},503);return true;}
  }
  if(args.path==='/api/desk/accounts' && holdAccounts) await holdAccounts;
  if(args.path==='/api/desk/connect/saved-site/instagram/account' && failAttach) {
   failAttach=false;await args.J({error:'Fixture attachment unavailable'},503);return true;
  }
  if(args.method==='PATCH' && args.path.startsWith('/api/desk/campaigns/')) {
   srv.log.push({path:args.path,method:args.method,body:args.req.postDataJSON()});
   await args.J({ok:true});return true;
  }
  return original(args);
 };
 const {ctx,page,pageErrors}=await newPage(browser,{srv,width,height});
 page.setDefaultTimeout(10000);
 const where=async()=>{
  await page.evaluate(()=>window.deskV1GotoCampaignPanel('where',{campaignId:'camp-1'}));
  await page.waitForSelector('[data-where-service="instagram"]');
 };
 const shot=async name=>page.screenshot({path:resolve(shots,`${name}_${before?'before':'after'}_${width}.png`),animations:'disabled'});
 await where();
 await page.locator('[data-where-service="instagram"]').scrollIntoViewIfNeeded();
 if(!before) {
  const tile=page.locator('[data-where-service="instagram"]');
  assert.match(await tile.innerText(),/Needs account/);
  assert.match(await tile.innerText(),/Link the Instagram account it is signed in as/);
  assert.doesNotMatch(await tile.innerText(),/Ready|undefined/);
  assert.equal(await tile.getAttribute('tabindex'),null,'unattached site cannot be placed');
 }
 await page.locator('[data-where-service="instagram"]').scrollIntoViewIfNeeded();
 await shot('where');
 await page.click('[data-where-service-open="instagram"]');
 if(before) {
  await page.waitForSelector('[data-browser-service="instagram"]');
  assert.equal(await page.locator('[data-site-attach]').count(),0,'Where stops at detail, requiring another Edit click');
  await shot('landing');
  await page.getByRole('button',{name:'Edit',exact:true}).click();
 }
 await page.waitForSelector('[data-site-attach]');
 await page.waitForFunction(()=>!document.querySelector('[data-cfp-loading]'));
 await page.locator('[data-site-attach]').scrollIntoViewIfNeeded();
 await shot('attach');
 assert.equal(await page.locator('[data-cfw-primary]').isDisabled(),true,'human must choose the account');
 assert.equal(writes(srv).length,0,'opening does not attach or grant access');
 if(before) {console.log(`BASELINE ${width}: Ready tile -> detail -> Edit -> legacy Instagram attach renders`);await ctx.close();continue;}
 await where();
  failTypes=true;
  await page.click('[data-where-service-open="instagram"]');
  await page.locator('#toast-container').getByText('Fixture setup unavailable').waitFor();
  assert.equal(await page.locator('[data-site-attach]').count(),0,'failed setup stays on selected detail');
  assert.equal(writes(srv).length,0);
  await where();
  let releaseTypes;holdTypes=new Promise(r=>{releaseTypes=r;});
  const pending=page.waitForRequest(r=>new URL(r.url()).pathname==='/api/desk/connect/types');
  await page.click('[data-where-service-open="instagram"]');await pending;
  await where();
  const resolved=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/desk/connect/types');
  releaseTypes();holdTypes=null;await resolved;
  await page.waitForTimeout(80);
  assert.equal(await page.locator('[data-where]').count(),1,'late handoff leaves the current page alone');
  assert.equal(await page.locator('[data-site-attach]').count(),0);
 await page.click('[data-where-service-open="instagram"]');
 await page.waitForSelector('[data-site-attach]');
 await page.waitForFunction(()=>!document.querySelector('[data-cfp-loading]'));
 await page.fill('[data-site-identity]','@example');
 assert.equal(await page.locator('[data-cfp-box="read"]').isChecked(),false);
 assert.equal(await page.locator('[data-cfp-box="post"]').isChecked(),false);
 await page.click('[data-cfw-primary]');await page.waitForSelector('[data-cfw-step="review"]');
 await page.click('[data-cfw-primary]');await answer(page,true);
 await page.waitForFunction(()=>!window.DeskV1ConnectSummaryStep.state().saving);
 assert.equal(writes(srv).length,0,'cancelled passcode attaches nothing');
 failAttach=true;
 await page.click('[data-cfw-primary]');await answer(page);
 await page.getByText('Fixture attachment unavailable',{exact:false}).waitFor();
 assert.equal(srv.services.find(s=>s.id==='instagram').account_id,undefined,'failed attachment retains missing-account source');
 await page.click('[data-cfw-primary]');await answer(page);
 await page.waitForSelector('[data-cfw-step="result"]');
 await page.waitForFunction(()=>window.DeskV1Store.state().channels.some(c=>c.id==='attached-instagram'));
 assert.equal(writes(srv).length,1,'only successful fixture attach writes; no permission changes');
 assert.deepEqual(srv.agentRead.main.domains,['youtube.com']);
 let releaseAccounts;holdAccounts=new Promise(r=>{releaseAccounts=r;});
 const refreshStarted=page.waitForRequest(r=>new URL(r.url()).pathname==='/api/desk/accounts');
 await page.click('[data-cfw-primary]');await refreshStarted;
 await page.evaluate(()=>window.deskV1GotoCampaignPanel('where',{campaignId:'camp-1'}));
 const source=page.locator('[data-where-source][data-channel-id="attached-instagram"]');
 await source.waitFor();
 await page.waitForFunction(()=>!document.querySelector('[data-where-service="instagram"]'));
 assert.equal(await source.getAttribute('tabindex'),'0','saved account gains placement without reloading');
 const refreshed=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/desk/engines');
 releaseAccounts();holdAccounts=null;await refreshed;
 await page.waitForTimeout(80);
 assert.equal(await page.locator('[data-where]').count(),1,'late post-save Connections refresh cannot overwrite Where');
 await source.scrollIntoViewIfNeeded();await shot('attached');
 const box=await source.boundingBox();
 await page.mouse.move(box.x+box.width/2,box.y+25);await page.mouse.down();
 await page.mouse.move(box.x+box.width/2+16,box.y+40,{steps:4});
 await page.waitForSelector('.pd-ghost');
 const board=page.locator('[data-where-board]');await board.scrollIntoViewIfNeeded();
 const dest=await board.boundingBox(), body=await page.locator('#desk-v1-camp-tabbody').boundingBox();
 const top=Math.max(dest.y,body.y), bottom=Math.min(dest.y+dest.height,body.y+body.height);
 await page.mouse.move(dest.x+Math.min(dest.width/2,150),top+(bottom-top)/2,{steps:12});
 await page.mouse.up();
 await page.waitForSelector('.desk-v1-where-col[data-channel-id="attached-instagram"]');
 await page.waitForFunction(()=>window.DeskV1Store.state().campaigns.find(c=>c.id==='camp-1').plan.accounts.includes('attached-instagram'));
 assert.ok(srv.log.some(r=>r.method==='PATCH' && r.body?.plan?.accounts?.includes('attached-instagram')),'drag persists campaign placement');
 assert.equal(srv.log.filter(r=>/\/browser\/(launch|input)|\/verify$|\/check$/.test(r.path)).length,0,'never uses the saved browser session');
 assert.deepEqual(pageErrors.filter(m=>!/aborted|net::ERR|Failed to fetch|EventSource/i.test(m)),[]);
 console.log(`PASS ${width}: direct legacy attach, truthful tile, empty handle, cancellation, failed save retry, no grants, refreshed account and pointer drag`);
 await ctx.close();
}}
finally {await browser.close();}
