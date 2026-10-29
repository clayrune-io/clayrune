// Saved Edit smoke; all browser and API state is hermetic.
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {newPage} from './connection-language-fixture.mjs';
import {resolve} from 'node:path';
import {server,edit,answer,capture,writes,npm,remote,github} from './saved-edit-fixture.mjs';
const shots=resolve('_scratch/saved_connections');
const browser=await chromium.launch();
try {for(const [width,height] of [[390,844],[1440,1000]]) {
 const srv=server();const {ctx,page,pageErrors}=await newPage(browser,{srv,width,height});
 await page.click('[data-conn-tile="service:instagram"]');
 assert.equal(await page.getByRole('button',{name:'Edit',exact:true}).isEnabled(),true);
 await page.screenshot({path:resolve(shots,`instagram-card-${width}.png`),animations:'disabled'});
 await edit(page,'service:instagram');
 assert.deepEqual(await page.locator('[data-cfp-box]').evaluateAll(ns=>ns.map(n=>n.dataset.cfpBox)),['read','post']);
 assert.equal(await page.locator('[data-cfw-primary]').isDisabled(),true,'no guessed handle');
 assert.equal(await page.locator('[data-cfp-box="read"]').isDisabled(),true);
 assert.match(await page.locator('[data-cfw-option="read"]').innerText(),/Reading isn't available for Instagram yet/);
 assert.equal(await page.locator('[data-cfp-box="post"]').isDisabled(),true,'no invented publishing route');
 await page.fill('[data-site-identity]','@example');
 await capture(page,`instagram-permissions-${width}.png`);
 assert.equal(writes(srv).length,0);
 await page.click('[data-cfw-primary]');await page.waitForSelector('[data-cfw-step="review"]');
 await page.click('[data-cfw-primary]');await answer(page,true);
 await page.waitForFunction(()=>!DeskV1ConnectSummaryStep.state().saving);
 assert.equal(writes(srv).length,0,'cancel attachment sends nothing');
 await page.click('[data-cfw-primary]');await answer(page);
 await page.waitForSelector('[data-cfw-step="result"]');
 assert.equal(srv.log.filter(r=>r.path.endsWith('/account')).length,1);
 assert.deepEqual(srv.agentRead.main.domains,['youtube.com']);
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
