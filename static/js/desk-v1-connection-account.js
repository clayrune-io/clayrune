// Shared social-account detail. Reading saved policy never grants permission,
// checks a sign-in, opens a pane or changes an existing human-only gate.
import { ConnectionWords as W } from './desk-v1-connection-words.js';
import { ConnectionRemoval as Remove } from './desk-v1-connection-remove.js';
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const policies = new Map();
const savedPolicy = id => policies.get(id)?.policy || (!window.DeskV1Store.live()?{state:'legacy'}:null);
export function invalidatePermissions(id) { if (id) policies.delete(id); else policies.clear(); }
function policy(ch, repaint) {
  if (policies.has(ch.id)) return;
  if (!window.DeskV1Store.live()) {policies.set(ch.id,{policy:{state:'legacy'}});return;}
  const entry={loading:true};policies.set(ch.id,entry);
  window.DeskV1Store.api('GET',`/api/desk/connect/permissions/${encodeURIComponent(ch.id)}`)
    .then(out=>{
      if(!out?.policy || !['explicit','legacy','invalid'].includes(out.policy.state))throw new Error(W.reasons.failed);
      if(policies.get(ch.id)===entry)policies.set(ch.id,{policy:out.policy});
    })
    .catch(e=>{console.warn('[connection-permissions] '+e);if(policies.get(ch.id)===entry)policies.set(ch.id,{failed:true});})
    .then(repaint);
}
function permissionRows(ch, cov, site) {
  if (site) return ['read','post'].map(op=>({op,on:false,reason:W.reasons.noAccount}));
  const entry=policies.get(ch.id), p=savedPolicy(ch.id);
  if (!p) return ['read','post'].map(op=>({op,on:false,reason:op==='post' && ch.post_permission?W.reasons.postOff:entry?.failed?W.reasons.failed:W.reasons.loading}));
  // Legacy is intentionally not consent: the executors retain their previous
  // behavior. Display that effective permission and disclose its origin below.
  const legacy=p.state==='legacy';
  const gated=!!window.DeskV1ReadsOff?.gateOf(cov);
  const readable=ch.platform!=='blog' && !!(ch.browser_profile || ch.browser_setup?.refs?.browser_profile || ch.read_via==='api');
  const postable=['direct','manual','assisted'].includes(ch.capability) && !ch.preview;
  return ['read','post'].map(op=>{
    const offered=op==='read'?readable:postable;
    const allowed=legacy || p.state==='explicit' && p[op]===true;
    const blocked=op==='read'?gated:!!ch.post_permission;
    const on=offered && allowed && !blocked;
    return {op,on,reason:on?'':p.state==='invalid'?W.reasons.failed:!offered?W.reasons.unavailable:op==='read'?W.reasons.readOff:W.reasons.postOff};
  });
}
function permissionsHTML(ch,cov,site) {
  return `<section data-connection-permissions><h3>${W.labels.permissions}</h3>${permissionRows(ch,cov,site).map(r=>`<div class="desk-v1-cs-row" data-connection-permission="${r.op}" data-on="${r.on}"><strong>${W.labels[r.op]}</strong> <span>${r.on?W.labels.on:W.labels.off}</span>${r.reason?`<p class="desk-v1-rules-hint" ${r.op==='post' && ch.post_permission?`data-post-permission="${esc(ch.id)}"`:''}>${esc(r.reason)}</p>`:''}</div>`).join('')}</section>`;
}
export function accountHTML(ch,cov,{site=false,details='',status}={}) {
  const S=window.DeskV1ConnectionStatus, st=status || S.accountStatus(ch,cov);
  const live=window.DeskV1Store.live() && !ch.preview;
  const browserTarget=live && !site && window.DeskV1BrowserAccount?.target(ch);
  const canCheck=!!browserTarget;
  const p=savedPolicy(ch.id);
  const list=site?[]:Object.values(S.facets(ch,cov)).filter(Boolean);
  // Keep route bindings for real sign-in flows, but offer only routes that
  // actually need sign-in. Their labels come from the same button vocabulary.
  const signin=live && st.kind==='signin'?list.filter(f=>f.key==='reauth' && !f.locked).map(f=>`<div data-cs-facet="${esc(f.id)}">${window.DeskV1AccountSignin?.html(ch,f)||''}</div>`).join(''):'';
  const messages=live?list.map(f=>window.DeskV1AccountSignin?.html(ch,f,true)||'').join(''):'';
  const off=window.DeskV1ReadsOff?.gateOf(cov);
  const sentence=site?W.reasons.noAccount:off?W.reasons.readOff:list.some(f=>f.locked)?W.reasons.locked:st.kind==='waiting'?W.waiting(W.serviceName(ch.platform)):!canCheck && st.kind==='unchecked'?W.reasons.noCheck:W.next[st.kind];
  return `<div class="desk-v1-cs" data-cs-account="${esc(ch.id)}">
    <p data-connection-next>${esc(sentence)}</p>
    ${permissionsHTML(ch,cov,site)}
    <details data-connection-details><summary>${W.labels.details}</summary>${details}
      ${p?.scopes?.length?`<p>${W.labels.scope}: <code>${esc(JSON.stringify(p.scopes))}</code></p>`:''}
      ${p?.state==='legacy'?`<p>${W.reasons.legacy}</p>`:''}
    </details>
    <div data-account-signin-messages>${messages}</div>
    <div class="desk-v1-conn-actions" data-connection-actions>
      ${browserTarget?window.DeskV1BrowserAccount.html(ch):`<button type="button" class="desk-v1-conn-btn" data-connection-check ${!canCheck || site?'disabled':''}>${W.buttons.check}</button>`}
      <button type="button" class="desk-v1-conn-btn" data-cs-reopen="${esc(ch.id)}">${W.buttons.edit}</button>
      ${signin}${Remove.html(ch.label || ch.name)}
    </div>
  </div>`;
}
export function bindAccount(row,ch,repaint) {
  policy(ch,repaint);
}
