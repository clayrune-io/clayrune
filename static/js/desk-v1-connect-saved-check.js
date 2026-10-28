// Human-click checks for saved provider connections, independently of Add service.
import { ConnectCopy as C } from './desk-v1-connect-copy.js';
import { ConnectionWords as W } from './desk-v1-connection-words.js';
const providers = Object.freeze({higgsfield:['higgsfield','api_key'],higgsfield_mcp:['higgsfield','oauth'],
  google:['google_ai','api_key'],openai:['openai','api_key']});
const checks = new Map();
function readiness(id) {const s=checks.get(id);return s?.error?W.state('broken'):s?.answer?W.from(s.answer.state):null;}
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function html(engine, existing) {
  const t=document.createElement('template');t.innerHTML=existing;
  // Connection changes always return to the one Add service experience.
  t.content.querySelectorAll('[data-engine-signin], [data-engine-guide], [data-guide]').forEach(n=>n.remove());
  const saved=!!engine.connected?.ready, supported=providers[engine.id], state=saved?checks.get(engine.id):null;
  const row=t.content.querySelector('[data-conn-engine]'), main=row.querySelector('.desk-v1-conn-main');
  const remove=row.querySelector('[data-connection-remove]')?.outerHTML || '';
  row.querySelector('.desk-v1-conn-actions')?.remove();
  const header=document.createElement('div');header.className='desk-v1-conn-head';
  header.append(main.querySelector('strong'),main.querySelector('[data-engine-status]'));
  row.prepend(header);
  const details=document.createElement('details');details.dataset.connectionDetails='';
  details.className='desk-v1-conn-main';
  const summary=document.createElement('summary');summary.textContent=W.labels.details;details.append(summary);
  const limit=main.querySelector('.desk-v1-engine-limit');
  while(main.firstChild) details.append(main.firstChild);
  main.remove();
  const observed=saved?readiness(engine.id):null, sentence=document.createElement('p');
  sentence.textContent=engine.connected?.state==='vault_locked'?W.reasons.locked:engine.connected?.state==='needs_signin'?W.next.signin:observed?W.next[observed.kind]:saved?W.next.ready:W.next.broken;
  row.append(sentence);if(limit)row.append(limit);row.append(details);
  row.insertAdjacentHTML('beforeend',`<div class="desk-v1-conn-actions" data-connection-actions>
    <button type="button" class="desk-v1-conn-btn" data-saved-check ${!saved || !supported || state?.busy?'disabled':''}>${W.buttons.check}</button>
    <button type="button" class="desk-v1-conn-btn" data-saved-change>${W.buttons.edit}</button>
    ${engine.connected?.state==='needs_signin' && engine.auth?.kind==='oauth'?`<button type="button" class="desk-v1-conn-btn" data-engine-signin>${W.buttons.signin}</button>`:''}
    ${remove}<span role="status" data-saved-check-status>${esc(state?.busy?W.buttons.checking:state?.error?C.words.checkFailed:state?.answer?C.status(state.answer.state):'')}</span></div>`);
  return t.innerHTML;
}
function bind(root, engine, repaint) {
  const edit=()=>window.DeskV1ConnectionStatus.reopen({kind:'engine',id:engine.id,record:engine},repaint);
  root.querySelector('[data-saved-change]')?.addEventListener('click',edit);
  root.querySelector('[data-saved-check]')?.addEventListener('click',async()=>{
    if(checks.get(engine.id)?.busy || !engine.connected?.ready || !providers[engine.id]) return;
    checks.set(engine.id,{busy:true});repaint();
    try {
      const [service,method]=providers[engine.id];
      const response=await fetch('/api/desk/connect/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({service,method,check:true})});
      const answer=await response.json();if(!response.ok) throw new Error(answer.error||'check failed');
      checks.set(engine.id,{answer});
    } catch(e) {checks.set(engine.id,{error:true});console.warn('[connect-check] saved connection check failed: '+e);}
    repaint();
  });
}
window.DeskV1ConnectSavedCheck={html,bind,readiness};
