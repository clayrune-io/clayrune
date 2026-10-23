// Real SPA, Vault, shared checker and attention surfaces; no real account or vault.
import assert from 'node:assert/strict';
import {readFileSync, mkdirSync} from 'node:fs';
import {resolve} from 'node:path';
import {chromium} from 'playwright';
import {loadStaticJsCss} from './_static.mjs';
const root=resolve(import.meta.dirname,'../..'), files=loadStaticJsCss(root), origin='http://mc.smoke.test';
const browser=await chromium.launch({headless:true});
const shots=resolve(root,'_scratch/mc1105');mkdirSync(shots,{recursive:true});
let cases=0;
try {
 for(const width of [1440,390]) for(const locked of [false,true]) {
  const page=await browser.newPage({viewport:{width,height:900}}), checks=[], errors=[];
  let expired=true, pass=false;
  const row={id:'browser-one',source:'account',source_id:'acc-one',name:'TikTok <person>',service:'tiktok',
    url:'https://www.tiktok.com',browser_profile:'main',signin_method:'phone_code',read_only:true};
  page.on('pageerror', e=>errors.push(e.message));
  await page.route('**/*', route=>{
   const path=new URL(route.request().url()).pathname;
   const json=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
   if(files[path])return route.fulfill({contentType:files[path][0],body:files[path][1]});
   if(path==='/')return route.fulfill({contentType:'text/html',body:readFileSync(resolve(root,'static/index.html'),'utf8')});
   if(path==='/api/projects')return json([]);
   if(path==='/api/config')return json({});
   if(path==='/api/secrets')return json({secrets:[],locked});
   if(path==='/api/secrets/vault-lock')return json({state:locked?'locked':'unlocked'});
   if(path==='/api/secrets/browser-accounts')return json({browser_accounts:[{...row,needs_signin:expired}],
    signin_requests:expired?[{service:'tiktok',name:row.name,message:'Sign in again',profiles:['main']}]:[]});
   if(path==='/api/desk/connect/browser-profile/check'){
    checks.push(JSON.parse(route.request().postData()));
    if(pass){expired=false;return json({state:'verified',verifiable:true,needs_signin:false});}
    return json({state:'check_failed',failure:'signed_out',verifiable:true,needs_signin:true,signin_url:'https://www.tiktok.com/login'});
   }
   return route.abort();
  });
  await page.goto(origin,{waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>typeof openSecretsVault==='function' && typeof renderWaitingOnYou==='function');
  await page.evaluate(async()=>{window.panes=[];window.openBrowserPane=(...args)=>{panes.push(args);};await BrowserAccounts.refresh();renderFeed();renderWaitingOnYou();});
  assert.equal(await page.locator('#waiting-on-you [data-project-id="__browser_signin__"]').count(),1);
  assert.equal(await page.locator('#feed-entries [data-project-id="__browser_signin__"]').count(),1);
  assert.equal(checks.length,0,'metadata refresh must never probe');
  // Exercise the real desktop/mobile shared click dispatcher.
  await page.locator('#waiting-on-you [data-project-id="__browser_signin__"]').evaluate(el=>el.click());
  await page.waitForSelector('[data-browser-account="browser-one"]');
  const vault=page.locator('#browser-accounts-list');
  assert.match(await vault.innerText(),/TikTok <person>[\s\S]*Phone code[\s\S]*main[\s\S]*Sign in again/);
  assert.equal(await vault.locator('button').count(),1,'no edit, delete or Copy ref');
  assert.equal(checks.length,0,'render must not probe');
  await page.locator('[data-bc-check]').click();
  await page.waitForFunction(()=>panes.length===1);
  assert.equal(checks.length,1);
  assert.deepEqual(checks[0],{url:row.url,browser_profile:'main',confirmed:false,check:true});
  assert.equal(await page.evaluate(()=>panes[0][3]),'main');
  pass=true;
  await page.waitForSelector('[data-bc-state="verified"]');
  await page.waitForFunction(()=>browserSigninAttentionItems().length===0);
  assert.equal(await page.locator('#waiting-on-you [data-project-id="__browser_signin__"]').count(),0);
  assert.equal(await page.locator('[data-browser-signin-request]').textContent(),'');
  assert.equal(await page.evaluate(()=>document.querySelector('#browser-accounts-list').scrollWidth > document.querySelector('#browser-accounts-list').clientWidth),false);
  await page.screenshot({path:resolve(shots,`vault-${width}-${locked?'locked':'open'}.png`),fullPage:true});
  // Closing the Vault stops the shared recheck controller through node ownership.
  const n=checks.length;
  await page.evaluate(()=>closeModalById('__secrets'));
  await page.waitForTimeout(2200);
  assert.equal(checks.length,n);
  assert.deepEqual(errors,[]);
  cases++;await page.close();
  console.log(`PASS browser accounts ${width} locked=${locked}: readonly, attention, pane, clear, no probes on render`);
 }
} finally {await browser.close();}
console.log(`${cases} browser-account UI cases passed`);
