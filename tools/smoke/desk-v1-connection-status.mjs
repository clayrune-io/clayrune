// Readiness, retained route evidence and existing MCP human gates.
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {makeServer,newPage} from './connection-language-fixture.mjs';
const browser=await chromium.launch();
try {
 for(const [width,height] of [[1440,1000],[390,844]]) {
  const srv=makeServer();const {ctx,page,pageErrors}=await newPage(browser,{srv,width,height,wizard:true});
  const word=id=>page.locator(`[data-conn-tile="${id}"] [data-conn-tile-status]`).textContent();
  for(const [id,expected] of Object.entries({'ch-x-ron':'Ready','ch-x-clayrune':'Ready','ch-li-page':'Needs sign-in','ch-yt-clayrune':'Not checked yet','ch-reddit':'Not checked yet','ch-discord-community':'Not checked yet'}))assert.equal((await word(id)).trim(),expected,id);
  await page.click('[data-conn-tile="ch-x-clayrune"]');
  assert.equal(await page.locator('[data-connection-details][open]').count(),0);
  await page.click('[data-connection-details] > summary');const facts=await page.locator('[data-connection-details]').textContent();
  for(const name of ['clayrune-x','x.clayrune','clayrune-oauth','x-api'])assert.ok(facts.includes(name),name);
  assert.equal(await page.locator('[data-as-signin]').count(),0);
  await page.evaluate(()=>{window.__editor=null;DeskV1ConnectionStatus.setReopen(t=>{window.__editor=t;});});await page.click('[data-cs-reopen]');
  assert.deepEqual(await page.evaluate(()=>({kind:__editor.kind,id:__editor.id})),{kind:'account',id:'ch-x-clayrune'});
  for(const [name,status] of Object.entries({'steady-mcp':'Not checked yet','drifted-mcp':'Not working','pending-mcp':'Not working','failed-mcp':'Not working','nokey-mcp':'Not working','gone-mcp':'Not working'}))assert.equal((await word('mcp:project:p1:'+name)).trim(),status);
  for(const [name,status] of Object.entries({'steady-remote':'Ready','moved-remote':'Not working','fresh-remote':'Not checked yet'}))assert.equal((await word('mcp:global::'+name)).trim(),status);
  await page.click('[data-conn-tile="mcp:project:p1:drifted-mcp"]');await page.click('[data-connection-details] > summary');
  assert.match(await page.locator('[data-cs-drift]').textContent(),/package\/dist\/index.js/);assert.equal(await page.locator('[data-cs-check-run]').isDisabled(),true);
  await page.click('[data-conn-tile="mcp:global::moved-remote"]');await page.click('[data-connection-details] > summary');
  assert.match(await page.locator('[data-cs-diff]').textContent(),/delete_everything/);
  const calls=()=>srv.log.filter(r=>/remote\/(check|adopt)$/.test(r.path));assert.equal(calls().length,0);
  await page.click('[data-cs-adopt]');await page.waitForSelector('input[id^="hp-passcode-"]');assert.equal(calls().length,0);
  await page.fill('input[id^="hp-passcode-"]','right-passcode');await page.click('.modal-content .btn-add');
  await page.waitForFunction(()=>document.querySelector('[data-conn-tile="mcp:global::moved-remote"] [data-conn-tile-status]')?.textContent.trim()==='Ready');
  assert.equal(calls()[0].body.server_name,'moved-remote');assert.equal(calls()[0].body.scope,'global');assert.match(calls()[0].body.observed,/^sha256:/);
  await page.click('[data-conn-tile="mcp:global::fresh-remote"]');assert.equal(calls().filter(r=>/check$/.test(r.path)).length,0);
  await page.click('[data-cs-check-run]');
  await page.waitForFunction(()=>document.querySelector('[data-conn-tile="mcp:global::fresh-remote"] [data-conn-tile-status]')?.textContent.trim()==='Ready');
  assert.equal(calls().filter(r=>/check$/.test(r.path)).length,1);assert.equal(calls().find(r=>/check$/.test(r.path)).body.server_name,'fresh-remote');
  assert.deepEqual(pageErrors.filter(e=>!/aborted|net::ERR|Failed to fetch|EventSource/i.test(e)),[]);
  console.log(`PASS readiness, route identities, editor target, drift and passcode-gated adoption at ${width}`);await ctx.close();
 }
} finally {await browser.close();}
