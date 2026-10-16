// MC-1074 generic browser reference: real screen/summary registry, fake network.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';
const root=resolve(import.meta.dirname,'../..'), origin='http://mc.smoke.test';
const files=loadStaticJsCss(root), shots=resolve(root,'_scratch/check_connection');
mkdirSync(shots,{recursive:true});
const types=JSON.parse(execFileSync('python',['-c',`import json
from mc.desk_connect import registry, resolve, type_view
out={}
for text in ['youtube.com','example.com']:
 got=resolve.resolve(text);svc=got.pop('service') or registry.lookup(got['host']);out[text]={**got,**type_view.project_service(svc['id'] if svc else None)}
print(json.dumps(out))`],{cwd:root,encoding:'utf8',env:{...process.env,PYTHONIOENCODING:'utf-8'}}));
const browser=await chromium.launch({headless:true});
try {
 for(const width of [1440,390]) for(const service of ['youtube.com','example.com']) {
  const page=await browser.newPage({viewport:{width,height:900}}), errors=[], writes=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>{
   const path=new URL(route.request().url()).pathname;
   if(files[path]) return route.fulfill({contentType:files[path][0],body:files[path][1]});
   return route.fulfill({contentType:'text/html',body:`<html><head><link rel="stylesheet" href="/static/css/app.css">${['kit','connections','connect-wizard','connect-summary-step','connect-login-step','connect-flow'].map(n=>`<link rel="stylesheet" href="/static/css/desk-v1-${n}.css">`).join('')}<style>body{display:block;height:auto}#root{max-width:800px;padding:16px;margin:auto}</style></head><body><main id="root"></main><script type="module">
    await import('/static/js/desk-v1-service-icons.js');
    for(const name of ['copy','wizard','permissions-step','summary-step','browser-check','browser-profile']) await import('/static/js/desk-v1-connect-'+name+'.js');
    DeskV1ConnectCopy.enable();DeskV1ConnectWizard.setEnabled(true);window.ready=true;
   </script></body></html>`});
  });
  await page.goto(origin);await page.waitForFunction(()=>window.ready);
  await page.evaluate(info=>{
   window.calls=[];window.openBrowserPane=(...args)=>calls.push(args);
   window.humanProofFetch=async(url,opts)=>{window.saved={url,body:JSON.parse(opts.body)};return {ok:true,body:{ok:true,kind:info.service.makes_account?'account':undefined,connection_id:'svc-example',browser_profile:saved.body.draft.browser_profile,profile_state:'new',name:saved.body.draft.name,url:saved.body.draft.url}};};
   const ctx={live:true,channels:()=>[],api:async(method,url)=>url==='/api/browser/profiles'?{profiles:[{name:'main'}]}:{},repaint:()=>{const r=document.querySelector('#root');r.innerHTML=DeskV1ConnectWizard.html(ctx);DeskV1ConnectWizard.bind(r,ctx);}};
   DeskV1ConnectWizard.resume(info,{service:info.service.id || info.host,type:'signin',variant:'browser-profile'},'setup');ctx.repaint();
  },types[service]);
  assert.equal(await page.inputValue('[data-bp-profile]'),types[service].picker.flatMap(t=>t.variants).find(v=>v.id==='browser-profile').setup.default_profile);
  const profile=service==='example.com' && width===390?'a.'+'b'.repeat(62):'main';
  await page.fill('[data-bp-profile]',profile);
  const makesAccount=types[service].service.makes_account===true;
  if(makesAccount){ // a pane-only site is saved as an ACCOUNT: the name box is its handle and starts empty
   assert.equal(await page.inputValue('[data-bp-name]'),'');
   assert.match(await page.locator('label:has([data-bp-name])').textContent(),/Account handle/);
   assert.equal(await page.locator('[data-cfw-primary]').isDisabled(),true);
   await page.fill('[data-bp-name]','@clayrune');
  }
  assert.equal(await page.locator('[data-cfw-title]').textContent(),'Connection details');
  await page.screenshot({path:resolve(shots,`generic-${service}-${width}.png`),fullPage:true});
  await page.click('[data-cfw-primary]');assert.equal(await page.locator('[data-cfw-title]').textContent(),'Permissions');
  assert.equal(await page.evaluate(()=>window.saved),undefined);
  await page.click('[data-cfw-primary]');assert.equal(await page.locator('[data-cfw-title]').textContent(),'Review');
  await page.click('[data-cfw-primary]');
  try {await page.waitForSelector('[data-cfw-step="result"]',{timeout:5000});}
  catch(e) {console.log(await page.locator('#root').textContent(),errors,await page.evaluate(()=>window.saved));throw e;}
  const saved=await page.evaluate(()=>window.saved);
  assert.equal(saved.url,'/api/desk/connect/browser-profile/commit');
  assert.deepEqual(Object.keys(saved.body.draft).sort(),['browser_profile','name','url']);
  assert.equal(saved.body.draft.browser_profile,profile);
  if(makesAccount){assert.equal(saved.body.draft.name,'@clayrune');assert.match(await page.locator('[data-cfw-step="result"]').textContent(),/Added as an account/);}
  assert.equal(await page.locator('[data-sum-verified="yes"]').count(),0);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);await page.close();
  console.log(`PASS generic ${service} ${width}: setup, permissions, review, save, honest result`);
 }
} finally {await browser.close();}
