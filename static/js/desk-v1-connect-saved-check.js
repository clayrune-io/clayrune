// Human-click checks for saved provider connections, independently of Add service.
import { ConnectCopy as C } from './desk-v1-connect-copy.js';
const providers = Object.freeze({higgsfield:['higgsfield','api_key'],higgsfield_mcp:['higgsfield','oauth'],
  google:['google_ai','api_key'],openai:['openai','api_key']});
const checks = new Map();
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function html(engine, existing) {
  const t=document.createElement('template');t.innerHTML=existing;
  // Connection changes always return to the one Add service experience.
  t.content.querySelectorAll('[data-engine-signin], [data-engine-guide], [data-guide]').forEach(n=>n.remove());
  const saved=!!engine.connected?.ready, supported=providers[engine.id], state=checks.get(engine.id);
  return t.innerHTML+`<div class="desk-v1-conn-actions"><button type="button" class="desk-v1-conn-btn" data-saved-change>${C.words.change}</button>
    ${saved&&supported?`<button type="button" class="desk-v1-conn-btn" data-saved-check ${state?.busy?'disabled':''}>${C.words.check}</button>`:''}
    <span role="status" data-saved-check-status>${esc(state?.busy?C.words.checking:state?.error?C.words.checkFailed:state?.answer?C.status(state.answer.state):'')}</span></div>`;
}
function bind(root, engine, repaint) {
  root.querySelector('[data-saved-change]')?.addEventListener('click',()=>{window.DeskV1AddService.reset();window.DeskV1ConnTiles.select('add');repaint();});
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
window.DeskV1ConnectSavedCheck={html,bind};
