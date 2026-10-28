// One vocabulary and one social skeleton, using the real branch SPA and fake
// accounts. No live credential, named profile or external service is touched.
import assert from 'node:assert/strict';
import {readFileSync,mkdirSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
import {ConnectionWords as W} from '../../static/js/desk-v1-connection-words.js';
import {makeServer,newPage} from './connection-language-fixture.mjs';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const shots=resolve(root,'_scratch/connections_language');mkdirSync(shots,{recursive:true});
const retired=/\b(?:Verified|Working|Connected|Browser set up, not read yet|Sign-in required|Saved[;,] cannot run yet|Edit settings|Change connection|Check it now|Check connection|Check the connection)\b/;
for(const name of ['connection-status','reads-off','post-permission','connections','engines','connect-purpose']) {
 const source=readFileSync(resolve(root,`static/js/desk-v1-${name}.js`),'utf8');
 // Comments may describe historical backend evidence; only executable UI copy
 // is constrained. camelCase DOM APIs and backend machine keys are not labels.
 const code=source.replace(/\/\*[\s\S]*?\*\//g,'').split('\n').filter(l=>!l.trimStart().startsWith('//')).join('\n');
 assert.ok(!retired.test(code),`${name} contains retired display copy`);
 assert.match(source,/import .*ConnectionWords/);
}
for(const input of ['verified','working','registered','pending_runtime','sign_in_required','vault_locked','partial','setup_failed']) {
 assert.ok(Object.values(W.status).includes(W.from(input).word),input);
}
assert.equal(W.state('waiting','LinkedIn').word,'Waiting on LinkedIn');
const browser=await chromium.launch();
try {
 for(const [width,height] of [[390,844],[1440,1000]]) {
  const srv=makeServer({services:[{id:'instagram',name:'www.instagram.com',link:'https://www.instagram.com',browser_connection:{browser_profile:'main',read:false,post:false,status:{state:'verified'}}}]});
  srv.engines=[{id:'google',label:'Google',currency:'usd',connected:{ready:true,state:'ready'},job_limit_usd:10,models:[]}];
  srv.baseline=process.argv.some(a=>a==='--before' || a.startsWith('--before='));
  const {ctx,page,pageErrors}=await newPage(browser,{srv,width,height,wizard:true});
  if(srv.baseline) {
   for(const id of ['ch-li-page','service:instagram','engine:google']) {
    await page.click(`[data-conn-tile="${id}"]`);
    await page.screenshot({animations:'disabled',path:resolve(shots,`before_${id.replaceAll(':','_')}_${width}.png`)});
   }
   await ctx.close();continue;
  }
  const review=await page.evaluate(()=>{
   const ch={id:'review-account',platform:'linkedin',label:'Page',capability:'direct',publish:{ready:false,reason:'LinkedIn app review pending (w_organization_social)'}};
   const t=document.createElement('template');t.innerHTML=DeskV1ConnectionStatus.accountHTML(ch,null);
   return {word:DeskV1ConnectionStatus.accountStatus(ch,null).word,next:t.content.querySelector('[data-connection-next]').textContent,details:t.content.querySelector('details').textContent};
  });
  assert.equal(review.word,'Waiting on LinkedIn');assert.equal(review.next,'Wait for LinkedIn to finish reviewing this connection.');assert.match(review.details,/w_organization_social/);
  for(const id of ['ch-x-ron','ch-x-clayrune','ch-li-page','ch-yt-clayrune','ch-reddit','ch-discord-community','service:instagram','engine:google','mcp:global::steady-remote']) {
   await page.click(`[data-conn-tile="${id}"]`);
   const panel=page.locator(`[data-conn-detail="${id}"]`);
   await panel.waitFor();
   const chip=panel.locator('[data-conn-status], [data-engine-status]').first();
   assert.ok(Object.values(W.status).includes((await chip.textContent()).trim()),`${id}: chip`);
   assert.equal(await panel.locator('[data-connection-details][open]').count(),0,`${id}: collapsed Details`);
   const buttons=(await panel.locator('button').allTextContents()).map(x=>x.trim()).filter(x=>['Check now','Edit','Sign in again','Remove'].includes(x));
   assert.deepEqual(buttons,id==='ch-li-page'?['Check now','Edit','Sign in again','Remove']:['Check now','Edit','Remove'],`${id}: ordered actions`);
   if(!id.startsWith('engine:') && !id.startsWith('mcp:')) {
    assert.deepEqual(await panel.locator('[data-connection-permission]').evaluateAll(ns=>ns.map(n=>n.dataset.connectionPermission)),['read','post']);
    assert.equal(await panel.locator('[data-connection-permission="post"]').getAttribute('data-on'),'false');
   }
   if(id==='service:instagram') {
    assert.equal(await panel.getByRole('button',{name:'Edit',exact:true}).isDisabled(),true);
    assert.equal(await panel.locator('[data-connection-permission="read"]').getAttribute('data-on'),'false');
    assert.match(await panel.textContent(),/unavailable for this saved sign-in/);
   }
   if(['ch-li-page','service:instagram','engine:google'].includes(id)) {
    await panel.locator('[data-connection-details] > summary').waitFor({state:'visible'});
    await panel.getByRole('button',{name:'Check now',exact:true}).waitFor({state:'visible'});
    await page.screenshot({animations:'disabled',path:resolve(shots,`after_${id.replaceAll(':','_')}_${width}.png`)});
   }
  }
  assert.equal(srv.log.filter(r=>/browser-profile\/check$|\/verify$|remote\/check$/.test(r.path)).length,0,'opening cards performs no checks');
  await page.click('[data-conn-tile="ch-x-ron"]');
  await page.click('[data-bc-check]');
  await page.waitForFunction(()=>document.querySelector('[data-conn-account="ch-x-ron"] [data-conn-status]')?.textContent.trim()==='Ready');
  assert.equal(srv.log.filter(r=>/browser-profile\/check$/.test(r.path)).length,1);
  assert.equal(srv.log.find(r=>/browser-profile\/check$/.test(r.path)).body.browser_profile,'ron-x');
  await page.click('[data-conn-tile="engine:google"]');srv.browserResult='check_failed';
  await page.click('[data-saved-check]');
  await page.waitForFunction(()=>document.querySelector('[data-conn-tile="engine:google"] [data-conn-tile-status]')?.textContent.trim()==='Not working');
  assert.equal((await page.locator('[data-engine-status]').textContent()).trim(),'Not working','engine tile and detail agree after a failed check');
  srv.browserResult='verified';await page.click('[data-saved-check]');
  await page.waitForFunction(()=>document.querySelector('[data-conn-tile="engine:google"] [data-conn-tile-status]')?.textContent.trim()==='Ready');
  await page.evaluate(async()=>{
   const engine=(await DeskV1Engines.list()).find(e=>e.id==='google');
   engine.connected={ready:false,state:'vault_locked'};deskV1Nav('connections');
  });
  await page.waitForFunction(()=>document.querySelector('[data-conn-tile="engine:google"] [data-conn-tile-status]')?.textContent.trim()==='Not working');
  assert.equal((await page.locator('[data-engine-status]').textContent()).trim(),'Not working','a previous passing check cannot hide a subsequently locked vault');
  assert.equal((await page.locator('[data-saved-check-status]').textContent()).trim(),'','a stale passing check is not repeated beside a locked connection');
  const overflow=await page.evaluate(()=>{const n=document.querySelector('[data-connections]');return n.scrollWidth-n.clientWidth;});
  assert.ok(overflow<=0,`no horizontal overflow at ${width}`);
  assert.deepEqual(pageErrors.filter(m=>!/aborted|net::ERR|Failed to fetch|EventSource/i.test(m)),[]);
  console.log(`PASS vocabulary, permissions, ordered actions, human-started check and layout at ${width}`);
  await ctx.close();
 }
} finally {await browser.close();}
