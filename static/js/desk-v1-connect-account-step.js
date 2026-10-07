// Destination selection is a Setup substep, never a separate connection flow.
import { ConnectCopy as C } from './desk-v1-connect-copy.js';
const W = window.DeskV1ConnectWizard;
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let kind = '', account = '', identity = '', query = '';
function accounts(api) {
  return (api.ctx.channels?.() || []).filter(a => a.platform === api.info.service.id &&
    (api.info.service.id !== 'linkedin' || (a.browser_setup?.account_kind || a.account_kind || 'organization') === kind) &&
    `${a.label || ''} ${a.identity || ''}`.toLowerCase().includes(query.toLowerCase()));
}
function handoff(api, target) {
  api.select({account:target.account.id || `new:${target.kind}:${target.account.new.identity}`});
  for (const name of ['Login','Api','Permissions','Summary']) window[`DeskV1Connect${name}Step`]?.setTarget(target);
  api.go('setup');
}
W?.registerScreen({ id:'account-setup', step:'setup',
  match:(sel,info)=>!sel.account && ['x','linkedin'].includes(info?.service?.id) && ['signin','api'].includes(sel.type),
  title:()=>C.screens.setup[0], copy:()=>C.screens.setup[1],
  body:api=> {
    if (api.info.service.id === 'linkedin' && !kind) return `<div class="desk-v1-cfw-options">${['member','organization'].map(k=>`<label class="desk-v1-cfw-option"><input type="radio" name="destination" data-account-kind="${k}"><span>${C.words[k]}</span></label>`).join('')}</div>`;
    return `<label class="desk-v1-conn-add-field">${C.words.existingAccount}<input type="search" class="desk-v1-rules-textinput" data-account-search value="${esc(query)}"></label>
      ${api.paged('accounts',[...accounts(api),{id:'new',label:C.words.newAccount}],a=>`<label class="desk-v1-cfw-option"><input type="radio" name="account" data-account-pick="${esc(a.id)}" ${account===a.id?'checked':''}><span>${esc(a.label || a.identity)}</span></label>`)}
      ${account==='new'?`<label class="desk-v1-conn-add-field">${C.words.identity}<input type="text" class="desk-v1-rules-textinput" data-account-identity value="${esc(identity)}" maxlength="80"></label>`:''}`;
  },
  primary:api=>({label:C.words.continue,disabled:api.info.service.id==='linkedin'&&!kind || !(account && (account!=='new'||identity.trim())),
    run:()=>{ const k = api.info.service.id==='x'?'account':kind;
      const existing = accounts(api).find(a=>a.id===account);
      if (account!=='new'&&!existing) return false;
      handoff(api,{kind:k,account:account==='new'?{new:{identity:identity.trim()}}:{id:existing.id},identity:account==='new'?identity.trim():existing.identity});return false;
    }}),
  bind:(root,api)=> {
    root.querySelectorAll('[data-account-kind]').forEach(r=>r.addEventListener('change',()=>{kind=r.dataset.accountKind;api.repaint();}));
    root.querySelector('[data-account-search]')?.addEventListener('input',e=>{query=e.target.value;api.repaint();document.querySelector('[data-account-search]')?.focus({preventScroll:true});});
    root.querySelectorAll('[data-account-pick]').forEach(r=>r.addEventListener('change',()=>{account=r.dataset.accountPick;api.repaint();}));
    root.querySelector('[data-account-identity]')?.addEventListener('input',e=>{identity=e.target.value;root.querySelector('[data-cfw-primary]').disabled=!identity.trim();});
  },
  discard:()=>{kind='';account='';identity='';query='';},
});
