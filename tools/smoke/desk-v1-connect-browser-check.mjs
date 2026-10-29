// MC-1074 all-service sign-in UI; mocked provider outcomes, real wizard modules.
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';
const root=resolve(import.meta.dirname,'../..'),origin='http://mc.smoke.test',staticFiles=loadStaticJsCss(root),shots=resolve(root,'_scratch/check_connection');
mkdirSync(shots,{recursive:true});
const types=JSON.parse(execFileSync('python',['-c',`import json
from mc.desk_connect import registry, resolve, type_view
out={}
for text in ['x.com','linkedin.com','youtube.com','facebook.com','tiktok.com','reddit.com','snapchat.com','github.com','example.com']:
 got=resolve.resolve(text);svc=got.pop('service') or registry.lookup(got['host']);out[text]={**got,**type_view.project_service(svc['id'] if svc else None)}
print(json.dumps(out))`],{cwd:root,encoding:'utf8',env:{...process.env,PYTHONIOENCODING:'utf-8'}}));
const browser=await chromium.launch({headless:true});
try {
 const evidence=JSON.parse(execFileSync('python',['-c',`import json
from mc.desk_connect.browser_check import EVIDENCE_JS
from mc.desk_connect import registry
print(json.dumps({'js':EVIDENCE_JS,'checks':{s:registry.profile(s).get('signin_check') for s in ['x','linkedin','youtube','facebook','tiktok','reddit','snapchat','github']}}))`],{cwd:root,encoding:'utf8'}));
 for(const [service,spec] of Object.entries(evidence.checks)){
  if(!spec){assert.equal(service,'snapchat');console.log('SKIP snapchat: no sign-in check in its profile');continue;}
  const page=await browser.newPage();
  const controls={x:'<button data-testid="SideNav_AccountSwitcher_Button">Account</button><a data-testid="AppTabBar_Home_Link">Home</a>',linkedin:'<button class="global-nav__me">Me</button><a href="/messaging/">Messaging</a>',youtube:'<button id="avatar-btn">Account</button>',facebook:'<div aria-label="Account controls and settings">Me</div>',tiktok:'<a data-e2e="profile-icon">Profile</a>',reddit:'<a href="/settings/account">Account</a>',github:'<input id="user_profile_name">'};
  await page.setContent(controls[service]);
  const expr=evidence.js.replace('SELECTORS',JSON.stringify(spec.selectors));
  assert.deepEqual((await page.evaluate(expr)).matches,spec.selectors.map(()=>true));
  for(const style of ['display:none','opacity:0','position:absolute;left:-10000px']){
   await page.setContent(`<div style="${style}">${controls[service]}</div>`);
   assert.deepEqual((await page.evaluate(expr)).matches,spec.selectors.map(()=>false));
  }
  await page.setContent('<p>Sign in</p>');assert.deepEqual((await page.evaluate(expr)).matches,spec.selectors.map(()=>false));
  await page.close();console.log(`PASS ${service}: real DOM evidence excludes hidden and signed-out controls`);
 }
 for(const width of [1440,390]) for(const service of Object.keys(types)) for(const flow of (['example.com','snapchat.com'].includes(service)?['manual']:['already','signin','failed'])) {
  const page=await browser.newPage({viewport:{width,height:900}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.setDefaultTimeout(8000);
  await page.route('**/*',route=>{
   const path=new URL(route.request().url()).pathname;
   if(staticFiles[path])return route.fulfill({contentType:staticFiles[path][0],body:staticFiles[path][1]});
   return route.fulfill({contentType:'text/html',body:`<html><head><link rel="stylesheet" href="/static/css/app.css">${['kit','connections','connect-wizard','connect-summary-step','connect-login-step','connect-flow'].map(n=>`<link rel="stylesheet" href="/static/css/desk-v1-${n}.css">`).join('')}<style>body{display:block;height:auto}#root{max-width:800px;margin:auto;padding:16px}</style></head><body><main id="root"></main><script type="module">
    for(const name of ['copy','wizard','vault-picker','signin','browser-check','login-step','permissions-step','summary-step','browser-profile'])await import('/static/js/desk-v1-connect-'+name+'.js');
    DeskV1ConnectCopy.enable();DeskV1ConnectWizard.setEnabled(true);window.ready=true;
   </script></body></html>`});
  });
  await page.goto(origin);await page.waitForFunction(()=>window.ready);
  await page.evaluate(({info,flow})=>{
   window.calls=[];window.panes=[];window.signedIn=flow==='already';window.fail=flow==='failed';window.manual=flow==='manual';
   window.openBrowserPane=(...a)=>panes.push(a);
   const ctx={live:true,channels:()=>[],api:async(method,url,body)=>{
    if(url==='/api/browser/profiles')return {profiles:[{name:'main'}]};
    if(url==='/api/secrets')return {secrets:[]};
    if(url==='/api/desk/connect/signin/options')return {logins:[],routes:[],vault_locked:false};
    if(url==='/api/desk/connect/browser-profile/check'){
     calls.push(body);
     const state=fail?'check_failed':manual?(body.confirmed||body.check===false?'not_checked':'sign_in_required'):signedIn?'verified':'check_failed';
     return {state,verifiable:!manual,needs_signin:state==='sign_in_required'||(!fail&&state==='check_failed'),failure:fail?'unavailable':'signed_out',signin_url:info.url};
    }return {};
   },repaint:()=>{const r=document.querySelector('#root');r.innerHTML=DeskV1ConnectWizard.html(ctx);DeskV1ConnectWizard.bind(r,ctx);}};
   const native=['x','linkedin'].includes(info.service.id),variant=native?info.picker.find(t=>t.id==='signin').variants[0].id:'browser-profile';
   DeskV1ConnectWizard.resume(info,{service:info.service.id || info.host,type:'signin',variant,account:native?'new-account':null},'setup');
   if(native)DeskV1ConnectLoginStep.setTarget({kind:info.service.id==='x'?'account':'member',account:{new:{identity:'Example'}}});
   ctx.repaint();
  },{info:types[service],flow});
  if(['x.com','linkedin.com'].includes(service)){
   await page.check('[data-lg-mode][value="manual"]');
   await page.waitForFunction(()=>[...document.querySelector('[data-lg-profile]').options].some(o=>o.value==='main'));
   await page.selectOption('[data-lg-profile]','main');
  }else await page.fill('[data-bp-profile]','main');
  assert.equal(await page.locator('[data-bc-check]').textContent(),'Check now');
  assert.equal(await page.evaluate(()=>calls.length),0);
  await page.click('[data-bc-check]');
  if(flow==='already'){
   await page.waitForSelector('[data-bc-state="verified"]');assert.equal(await page.evaluate(()=>panes.length),0);
   await page.click('[data-bc-check]');await page.waitForFunction(()=>calls.length===2);
   await page.waitForSelector('[data-bc-state="verified"]');
  }else if(flow==='signin'){
   await page.waitForFunction(()=>panes.length===1);
   await page.evaluate(()=>{window.signedIn=true;});
   await page.waitForSelector('[data-bc-state="verified"]');
   assert.ok((await page.evaluate(()=>calls.length))>=2);assert.equal(await page.evaluate(()=>panes.length),1);
  }else if(flow==='failed'){
   await page.waitForSelector('[data-bc-state="check_failed"]');assert.equal(await page.evaluate(()=>panes.length),0);
  }else {
   await page.waitForSelector('[data-bc-confirm]');
   await page.screenshot({path:resolve(shots,`unknown-prompt-${width}.png`),fullPage:true});
   assert.equal(await page.locator('[data-bc-confirm]').count(),1);
   if(width===390){
    await page.click('[data-bc-no]');
    assert.equal(await page.locator('[data-bc-confirm] p').count(),0);
    assert.equal(await page.locator('[data-bc-yes]').count(),1);
   }
   await page.click('[data-bc-yes]');await page.waitForSelector('[data-bc-state="not_checked"]');
   assert.equal(await page.locator('[data-bc-state]').textContent(),'Not checked yet');
   assert.equal(await page.locator('[data-bc-state="verified"]').count(),0);
   await page.click('[data-bc-check]');await page.waitForFunction(()=>calls.length===3);
   assert.equal(await page.locator('[data-bc-confirm]').count(),0);
   assert.equal(await page.evaluate(()=>panes.length),1);
  }
  const calls=await page.evaluate(()=>window.calls);assert.ok(calls.every(c=>c.browser_profile==='main'));
  await page.screenshot({path:resolve(shots,`${service}-${flow}-${width}.png`),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.evaluate(()=>DeskV1ConnectWizard.close());const count=await page.evaluate(()=>calls.length);await page.waitForTimeout(2100);assert.equal(await page.evaluate(()=>calls.length),count);
  // Reopening Result/detail must show its metadata status without a probe.
  const savedState=flow==='manual'?'not_checked':flow==='failed'?'check_failed':'verified';
  await page.evaluate(state=>{
   const root=document.querySelector('#root');root.innerHTML=DeskV1ConnectBrowserCheck.html('saved-fixture');
   DeskV1ConnectBrowserCheck.bind(root,'saved-fixture',()=>({url:'https://example.com',browser_profile:'main'}),{api:()=>{throw new Error('render ran a check');}},{status:{state,verifiable:state!=='not_checked',needs_signin:false}});
  },savedState);
  assert.equal(await page.locator('[data-bc-state]').getAttribute('data-bc-state'),savedState);
  assert.equal(await page.evaluate(()=>calls.length),count);
  if(service==='example.com') {
   await page.evaluate(()=>{
    const root=document.querySelector('#root');root.innerHTML=DeskV1ConnectBrowserCheck.html('unavailable-fixture');
    DeskV1ConnectBrowserCheck.bind(root,'unavailable-fixture',()=>({url:'https://example.com',browser_profile:'bad..profile'}),{api:()=>{throw new Error('render ran a check');}},{status:{state:'unknown',verifiable:false,needs_signin:false}});
   });
   assert.equal(await page.locator('[data-bc-state]').textContent(),'Not checked yet');
   assert.equal(await page.locator('[data-bc-state="verified"]').count(),0);
  }
  assert.deepEqual(errors,[]);await page.close();console.log(`PASS ${service} ${flow} ${width}`);
 }
}finally{await browser.close();}
