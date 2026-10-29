// Grid navigation and layout; saved-permissions/live-read-pages cover its editor.
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {makeServer,newPage} from './connection-language-fixture.mjs';
const browser=await chromium.launch();
try {
 for(const [width,height] of [[1440,1000],[390,844],[344,760]]) {
  const srv=makeServer();const {ctx,page,pageErrors}=await newPage(browser,{srv,width,height,wizard:true});
  assert.equal(await page.locator('[data-conn-detail]').count(),0);assert.equal(await page.locator('[data-conn-add-tile]').count(),1);
  assert.equal(await page.locator('[data-conn-add-tile]').evaluate(n=>n===n.parentElement.lastElementChild),true);
  assert.equal(await page.locator('[data-conn-add-tile]').evaluate(n=>getComputedStyle(n).borderTopStyle),'solid');
  const columns=await page.locator('[data-conn-tile]').evaluateAll(ns=>ns.filter(n=>Math.round(n.getBoundingClientRect().top)===Math.round(ns[0].getBoundingClientRect().top)).length);
  assert.ok(width===1440?columns>=3:columns===2,`tile columns at ${width}`);
  assert.ok((await page.locator('[data-conn-tile] img').evaluateAll(ns=>ns.map(n=>n.complete && n.naturalWidth>0))).every(Boolean));
  for(const id of ['ch-x-ron','ch-li-page','ch-x-clayrune','ch-reddit']) {
   await page.click(`[data-conn-tile="${id}"]`);assert.equal(await page.locator('[data-conn-detail]').count(),1);
   assert.equal(await page.locator('[data-conn-account]').getAttribute('data-conn-account'),id);assert.equal(await page.locator('[data-connection-permission]').count(),2);
   assert.equal(await page.locator('[data-readvia-row]').count(),0,'no duplicate editor');
   await page.waitForFunction(h=>{const r=document.querySelector('[data-conn-detail]')?.getBoundingClientRect();return r && r.top<h && r.bottom>0;},height);
   assert.ok((await page.evaluate(()=>{const n=document.querySelector('[data-connections]');return [document.documentElement.scrollWidth-innerWidth,n.scrollWidth-n.clientWidth];})).every(n=>n<=0));
  }
  await page.click('[data-conn-tile="ch-reddit"]');assert.equal(await page.locator('[data-conn-detail]').count(),0);
  await page.click('[data-conn-tile="ch-x-ron"]');await page.click('[data-conn-detail-close]');assert.equal(await page.locator('[data-conn-detail]').count(),0);
  assert.deepEqual(pageErrors.filter(e=>!/aborted|net::ERR|Failed to fetch|EventSource/i.test(e)),[]);
  console.log(`PASS grid, marks, single selection and summary layout at ${width}`);await ctx.close();
 }
} finally {await browser.close();}
