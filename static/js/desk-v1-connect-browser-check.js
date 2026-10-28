// One human-started check controller for every browser sign-in.
import { ConnectCopy as C } from './desk-v1-connect-copy.js';
const states=new Map(), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fresh=()=>({status:null,busy:false,opened:false,asked:false,prompt:false,deferred:false,timer:null,node:null,active:true,started:0});
function clear(key) {const s=states.get(key);if(s){s.active=false;clearTimeout(s.timer);states.delete(key);}}
function html(key) {return `<div class="desk-v1-cs-fill-row" data-browser-check="${esc(key)}"></div>`;}
function bind(root,key,target,ctx,{own,onStatus,status,compact=false}={}) {
 const node=root.querySelector(`[data-browser-check="${CSS.escape(key)}"]`);if(!node)return;
 const wanted=target();
 const signature=JSON.stringify(wanted);
 let s=states.get(key);
 if(s && s.signature!==signature){clear(key);s=null;}
 if(!s){s=fresh();s.signature=signature;s.status=status || null;states.set(key,s);}
 s.node=node;
 const alive=()=>s.active && states.get(key)===s && s.node?.isConnected && JSON.stringify(target())===signature;
 const paint=()=>{
  if(!alive())return;
  const label=s.status ? C.status(s.status.state) : C.words.notChecked;
  s.node.innerHTML=`<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-bc-check ${s.busy?'disabled':''}>${s.busy?C.words.checking:C.words.checkConnection}</button>
   ${compact?'':`<span class="desk-v1-conn-status" data-bc-state="${esc(s.status?.state || 'not_connected')}" data-state="${s.status?.state==='verified'?'ok':'needs'}" role="status">${esc(label)}</span>`}
   ${!compact && s.status?.state==='verified'?`<span class="desk-v1-rules-hint">${C.words.browserProof}</span>`:''}
   ${!compact && s.error?`<span role="alert">${esc(s.error)}</span>`:''}
   ${s.prompt || s.deferred?`<div data-bc-confirm>${s.prompt?`<p>${C.words.noAutoCheck} ${C.words.signedInQuestion}</p>`:''}<button type="button" class="desk-v1-conn-btn" data-bc-yes>${C.words.yesSignedIn}</button>${s.prompt?`<button type="button" class="desk-v1-conn-btn" data-bc-no>${C.words.notYet}</button>`:''}</div>`:''}`;
  s.node.querySelector('[data-bc-check]').onclick=()=>{
   if(JSON.stringify(target())!==signature){bind(root,key,target,ctx,{own,onStatus,compact});root.querySelector(`[data-browser-check="${CSS.escape(key)}"] [data-bc-check]`)?.click();return;}
   s.started=Date.now();s.error='';
   if(own && !s.owned){own(key,()=>{if(states.get(key)===s)clear(key);},'account');s.owned=true;}
   run(false,false);
  };
  s.node.querySelector('[data-bc-yes]')?.addEventListener('click',()=>{s.prompt=false;s.deferred=false;run(true,false);});
  s.node.querySelector('[data-bc-no]')?.addEventListener('click',()=>{s.prompt=false;s.deferred=true;paint();});
 };
 async function run(confirmed,automatic) {
  if(!alive() || s.busy)return;
  clearTimeout(s.timer);s.busy=true;paint();
  try {
   const check=!(s.status?.verifiable===false && s.status?.state==='not_checked' && !confirmed);
   const out=await ctx.api('POST','/api/desk/connect/browser-profile/check',{...wanted,confirmed,check});
   if(!alive())return;
   s.status=out;s.error='';
   onStatus?.(out);
   if(out.needs_signin && !s.opened) {
    if(typeof window.openBrowserPane!=='function')throw new Error(C.words.paneUnavailable);
    // A known service supplies its reviewed sign-in URL; an unknown service
    // opens exactly the address the person entered. Never guess another reader.
    await window.openBrowserPane(out.signin_url,null,null,wanted.browser_profile);
    if(!alive())return;
    s.opened=true;
   }
   if(s.opened && out.verifiable===false && (!s.asked || (confirmed && out.needs_signin))) {s.asked=true;s.prompt=true;}
   else if(s.opened && out.verifiable && out.needs_signin && Date.now()-s.started<600000) s.timer=setTimeout(()=>run(false,true),2000);
  } catch(e) {
   if(!alive())return;
   s.error=e.message || C.words.checkFailed;s.status={state:'check_failed',message:s.error};onStatus?.(s.status);
  } finally {s.busy=false;paint();}
 }
 paint();
}
window.DeskV1ConnectBrowserCheck={html,bind,clear};
