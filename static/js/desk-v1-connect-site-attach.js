// Old saved browser sites enter the same Permissions screen. Only Review attaches
// an account, with its own passcode; the Permissions owner applies wishes after it.
import { ConnectCopy as C } from './desk-v1-connect-copy.js';
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let S = null;
const matches = sel => sel.type === 'account' && sel.variant === 'saved-site';
function target() {
  const account = S.account ? {id:S.account} : {new:{identity:S.identity.trim() || 'unselected'}};
  const t = {kind:'account', account, identity:S.identity.trim()};
  for (const name of ['Permissions','Summary']) window[`DeskV1Connect${name}Step`].setTarget(t);
}
function restore(site, info) { S = {site, info, identity:'', account:'', choices:[]}; target(); }
function html(c) {
  if (!S || !matches(c.api.sel)) return '';
  S.choices = (c.api.ctx.channels?.() || []).filter(a=>a.platform === S.info.service.id);
  return `<fieldset class="desk-v1-cfw-group desk-v1-site-attach" data-site-attach><legend class="desk-v1-cfw-dtitle">${C.words.account}</legend>
    <p>${C.words.attachAccountHelp}</p>
    <label class="desk-v1-conn-add-field">${C.words.account}<select class="desk-v1-rules-textinput" data-site-account><option value="">${C.words.newAccount}</option>${S.choices.map(a=>`<option value="${esc(a.id)}" ${S.account===a.id?'selected':''}>${esc(a.label || a.identity)}</option>`).join('')}</select></label>
    ${S.account ? '' : `<label class="desk-v1-conn-add-field">${C.words.accountHandle}<input class="desk-v1-rules-textinput" data-site-identity value="${esc(S.identity)}" maxlength="80" placeholder="@yourname" autocapitalize="off" spellcheck="false"></label>`}
    </fieldset>`;
}
const problem = () => S && !S.account && !S.identity.trim() ? C.words.handleRequired : '';
async function save() {
  if (problem()) return {ok:false,error:problem()};
  const res = await window.humanProofFetch(`/api/desk/connect/saved-site/${encodeURIComponent(S.site.id)}/account`,
    {method:'POST',body:JSON.stringify({account:S.account ? {id:S.account}:{identity:S.identity.trim()},source:{url:S.site.link,browser_profile:S.site.browser_connection.browser_profile,signin_method:S.site.browser_connection.signin_method || 'other'}})},
    {title:C.words.saveConnection,description:C.words.saveProof});
  return !res ? {cancelled:true} : res.ok ? {ok:true,result:res.body} : {ok:false,error:res.body?.error || C.words.notSaved};
}
window.DeskV1ConnectPermissionsStep.registerSection({
  html, matches:c=>!!S && matches(c.api.sel), problem:c=>S && matches(c.api.sel) ? problem() : '',
  bind:(root,c)=>{
    if (!S || !matches(c.api.sel)) return;
    root.querySelector('[data-site-account]')?.addEventListener('change',e=>{S.account=e.target.value;target();c.api.repaint();});
    root.querySelector('[data-site-identity]')?.addEventListener('input',e=>{S.identity=e.target.value;target();root.querySelector('[data-cfw-primary]').disabled=!!problem();});
  },
  reading:()=>S ? {via:'pane',profile:S.site.browser_connection.browser_profile}:null,
  summary:()=>({pending:false,lines:S?[`Account: ${S.account ? S.choices.find(a=>a.id===S.account)?.identity : S.identity.trim()}`]:[]}),
  apply:async()=>({status:'unchanged'}), outcome:()=>'', discard:()=>{S=null;},
});
window.DeskV1ConnectSummaryStep.registerBranch({id:'saved-site',word:C.words.currentConnection,match:matches,
  problem,save,accountId:r=>r.account_id,
  factsHTML:()=>`<dl class="desk-v1-cf-facts"><div><dt>${C.words.account}</dt><dd>${esc(S.account ? S.choices.find(a=>a.id===S.account)?.identity : S.identity)}</dd></div><div><dt>${C.words.browserProfile}</dt><dd>${esc(S.site.browser_connection.browser_profile)}</dd></div></dl>`,
  result:{rows:()=>[[C.words.connectionLabel,C.words.saved,'connection']],actions:()=>[]},
});
window.DeskV1ConnectWizard.registerScreen({id:'saved-site-setup',step:'setup',match:matches,
  title:()=>C.screens.setup[0],copy:()=>C.screens.setup[1],body:()=>`<p>${esc(S?.site.link)}</p><p>${C.words.browserProfile}: ${esc(S?.site.browser_connection.browser_profile)}</p>`,
  primary:()=>({label:C.words.continue}),
});
export const SiteAttach = {restore};
