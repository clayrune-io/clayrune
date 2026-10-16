// MC-713: real service-worker update with six open HTTP/1 agent SSE streams.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFileSync, existsSync} from 'node:fs';
import {resolve, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
const root=resolve(dirname(fileURLToPath(import.meta.url)), '../..');
let version=1;
const streams=new Set();
const sessions=Array.from({length:6},(_,i)=>({session_id:`swstream${i}`,status:'running',task:'SW smoke',log_lines:[],process_alive:true}));
const server=createServer((req,res)=>{
 const path=new URL(req.url,'http://localhost').pathname;
 if(path.endsWith('/agent/stream')) {
  res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'});
  res.write('data: {"type":"heartbeat"}\n\n'); streams.add(res);
  req.on('close',()=>streams.delete(res)); return;
 }
 if(path==='/sw.js') {
  res.writeHead(200,{'Content-Type':'text/javascript','Cache-Control':'no-store'});
  return res.end(`// version ${version}\nself.addEventListener('install',()=>self.skipWaiting());self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));`);
 }
 if(path.startsWith('/api/')) {
  let body={};
  if(path==='/api/projects') body=[];
  if(path.endsWith('/agent/status')) body={sessions};
  res.writeHead(200,{'Content-Type':'application/json'});return res.end(JSON.stringify(body));
 }
 const file=resolve(root,'static',path==='/'?'index.html':path.replace(/^\/static\//,''));
 if(!file.startsWith(resolve(root,'static'))||!existsSync(file)) {res.writeHead(404);return res.end();}
 res.writeHead(200,{'Content-Type':file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.json')?'application/json':'text/html'});
 res.end(readFileSync(file));
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const origin=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch();
try {
 const page=await browser.newPage();
 await page.goto(origin);
 await page.waitForFunction(()=>typeof forceUpdateServiceWorker==='function');
 await page.evaluate(async()=>{await navigator.serviceWorker.register('/sw.js');await navigator.serviceWorker.ready;});
 await page.waitForFunction(()=>!!navigator.serviceWorker.controller);
 await page.evaluate(()=>{window.__toasts=[];window.showToast=(s)=>window.__toasts.push(s);});
 const fill=async()=>{
  await page.evaluate(()=>{
   for(let i=0;i<6;i++) {
    const sid=`swstream${i}`;
    agentStatusCache[sid]={status:'running',projectId:'swsmoke'};
    connectAgentStream('swsmoke',sid);
   }
  });
  await page.waitForFunction(()=>Object.values(agentEventSources).length===6&&Object.values(agentEventSources).every(es=>es.readyState===1));
 };
 await fill();
 const started=Date.now();
 await page.evaluate(()=>forceUpdateServiceWorker());
 assert(Date.now()-started<12000,'update hit the timeout');
 assert((await page.evaluate(()=>window.__toasts)).includes('Already up to date.'));
 await page.waitForFunction(()=>Object.values(agentEventSources).length===6&&Object.values(agentEventSources).every(es=>es.readyState===1));
 console.log('PASS: six real streams open; unchanged SW completes with toast; streams reconnect.');
 version=2;
 await Promise.all([page.waitForEvent('domcontentloaded'),page.evaluate(()=>forceUpdateServiceWorker()).catch(e=>{if(!/context.*destroyed/i.test(e.message))throw e;})]);
 console.log('PASS: changed SW activates and reloads with streams open.');
 // Simulate an update blocked by streams in another tab: bounded failure,
 // no permanent disabled button, and local streams resume.
 await page.waitForFunction(()=>typeof forceUpdateServiceWorker==='function');
 await fill();
 await page.evaluate(()=>{
  window.__toasts=[];window.showToast=s=>window.__toasts.push(s);
  navigator.serviceWorker.getRegistration=async()=>({update:()=>new Promise(()=>{})});
  const btn=document.createElement('button');btn.id='installed-app-update-btn';document.body.appendChild(btn);
 });
 await page.evaluate(()=>forceUpdateServiceWorker());
 assert((await page.evaluate(()=>window.__toasts)).some(s=>s.includes('timed out')));
 assert.equal(await page.locator('#installed-app-update-btn').isDisabled(),false);
 await page.waitForFunction(()=>Object.values(agentEventSources).length===6);
 console.log('PASS: blocked update times out clearly, re-enables action and resumes streams.');
} finally {
 await browser.close();for(const res of streams)res.end();
 await new Promise(r=>server.close(r));
}
