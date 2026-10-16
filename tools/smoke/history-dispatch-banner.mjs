// MC-1033: completed children have lineage in /conversations, not /agent/status.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
import {loadStaticJsCss} from './_static.mjs';
import {historySpawner} from '../../static/js/conversation-lineage.js';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const assets=loadStaticJsCss(root);
const projects=JSON.parse(readFileSync(resolve(root,'tools/smoke/fixtures/projects.json'),'utf8'));
const pid=projects[0].id;
projects[0].project_path='/smoke';
const row=(sid,csid,parent)=>({mc_session_id:sid,claude_session_id:csid,first_user:sid,status:'completed',live:false,
 resumable:true,provider:'claude',source:'agent',character:{name:'rusk',agent_name:'Rusk'},spawned_by_session_id:parent});
const rows=[row('child','child-transcript','parent-recorded'),row('unknown','unknown-transcript',''),
 row('shared','older-transcript','older-parent'),row('shared','newer-transcript','newer-parent')];
assert.equal(historySpawner('shared',{claudeSessionId:'older-transcript'},rows),'older-parent');
assert.equal(historySpawner('shared',{},rows),''); // cannot pick between recorded parents
assert.equal(historySpawner('unknown',{},rows),'');
assert.equal(historySpawner('child',{spawnedBySessionId:'live-parent'},rows),'live-parent');
const browser=await chromium.launch();
try {
 for(const width of [1440,390]) {
  const page=await browser.newPage({viewport:{width,height:844}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',r=>{
   const req=r.request(),path=new URL(req.url()).pathname;
   const json=body=>r.fulfill({contentType:'application/json',body:JSON.stringify(body)});
   if(req.method()!=='GET') return r.abort();
   if(path==='/')return r.fulfill({contentType:'text/html',body:readFileSync(resolve(root,'static/index.html'),'utf8')});
   if(assets[path])return r.fulfill({contentType:assets[path][0],body:assets[path][1]});
   if(req.url().includes('mermaid.esm.min.mjs'))return r.fulfill({contentType:'text/javascript',body:'export default {initialize(){},run:async()=>{}};'});
   if(path==='/api/projects')return json(projects);
   if(path==='/api/config')return json({});
   if(path.endsWith('/conversations'))return json(rows);
   if(path.endsWith('/agent/status'))return json({sessions:[]});
   const mc=path.match(/\/session\/(child|unknown|shared|offpage)\/reconstruct$/);
   const cs=path.match(/\/transcript\/([^/]+)\/reconstruct$/);
   if(mc||cs)return json({task:'completed child',claude_session_id:mc?`${mc[1]}-transcript`:cs[1],
     spawned_by_session_id:mc?.[1]==='offpage'?'offpage-parent':'',
     log_lines:['> User: build','Finished work.','[read-only history]'],read_only:true});
   if(path.startsWith('/api/'))return json([]);
   return r.abort();
  });
  await page.goto('http://mc.smoke.test/');
  await page.evaluate(p=>openProjectModal(p),pid);
  await page.waitForSelector('.conv-row[data-mcsid="child"]');
  // Rail click follows the real history reconstruction path (no status seed).
  await page.locator('.conv-row[data-mcsid="child"]').first().click();
  await page.waitForSelector('#dispatched-by-child');
  assert.match(await page.locator('#dispatched-by-child').innerText(),/Dispatched by.*parent-r/);
  assert.equal(await page.evaluate(()=>agentStatusCache.child.spawnedBySessionId),'');
  await page.evaluate(p=>refreshModalById(p),pid);
  assert.equal(await page.locator('#dispatched-by-child').count(),1);
  const composer=page.locator('#agent-followup-child');
  const box=await composer.boundingBox();
  assert(box&&box.y>=0&&box.y+box.height<=844,`composer clipped at width ${width}`);
  // CSID-keyed history twin of a shared MC id must take THAT transcript's parent.
  await page.evaluate(p=>openConversation(p,'older-transcript','shared',false),pid);
  await page.evaluate(p=>openConversation(p,'newer-transcript','shared',false),pid);
  await page.waitForSelector('#dispatched-by-newer-transcript');
  assert.match(await page.locator('#dispatched-by-newer-transcript').innerText(),/newer-pa/);
  // The recorded child need not be in the conversation list's first page.
  await page.evaluate(p=>openConversation(p,'','offpage',false),pid);
  await page.waitForSelector('#dispatched-by-offpage');
  assert.equal(await page.evaluate(()=>agentStatusCache.offpage.spawnedBySessionId),'offpage-parent');
  await page.evaluate(p=>openConversation(p,'unknown-transcript','unknown',false),pid);
  await page.waitForSelector('#agent-output-unknown');
  assert.equal(await page.locator('#dispatched-by-unknown').count(),0);
  assert.deepEqual(errors,[]);
  console.log(`PASS ${width}: rail child banner, rebuild, composer fit, CSID twin lineage, and missing-lineage refusal.`);
  await page.close();
 }
} finally {await browser.close();}
