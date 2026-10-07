// Generic browser setup joins the existing screen and summary registries.
import { ConnectCopy as C } from './desk-v1-connect-copy.js';
const W = window.DeskV1ConnectWizard;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let S;
function clear() { S = {name:'', profile:'', profiles:null, info:null, request:null, result:null}; }
clear();
const matches = (sel, info) => sel.type === 'signin' && !['x','linkedin'].includes(info?.service?.id);
const profileName = () => S.profile.trim();
// Full name validation belongs to browser_routes on Save/check. Do not maintain
// another regex here that can reject a profile the browser already supports.
const problem = () => !S.name.trim() ? C.words.nameRequired : !profileName() ? C.words.profileRequired : '';
function init(api) {
  S.info = api.info;
  if (!S.name) S.name = api.info.service?.label || api.info.host;
  if (!S.profile) S.profile = api.info.picker?.flatMap(t=>t.variants).find(v=>v.id==='browser-profile')?.setup?.default_profile || 'main';
}
function controls() {
  return `<label class="desk-v1-conn-add-field">${C.words.name}<input type="text" class="desk-v1-rules-textinput" data-bp-name value="${esc(S.name)}" maxlength="80"></label>
    <label class="desk-v1-conn-add-field">${C.words.browserProfile}<input type="text" class="desk-v1-rules-textinput" data-bp-profile value="${esc(S.profile)}" maxlength="64" list="bp-profiles" autocapitalize="off" spellcheck="false"></label>
    <datalist id="bp-profiles">${(S.profiles || []).map(p=>`<option value="${esc(p)}"></option>`).join('')}</datalist>`;
}
async function commit() {
  if (problem()) return {ok:false,error:problem()};
  S.request ||= window.crypto?.randomUUID ? crypto.randomUUID() : `bp-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  const res = await window.humanProofFetch('/api/desk/connect/browser-profile/commit', {
    method:'POST', body:JSON.stringify({request_id:S.request,draft:{name:S.name.trim(),url:S.info.url,browser_profile:profileName()}}),
  },{title:C.words.saveConnection,description:C.words.saveProof});
  if (!res) return {cancelled:true};
  if (!res.ok) return {ok:false,error:res.body?.error || C.words.notSaved};
  S.result=res.body;return {ok:true,result:res.body};
}
W?.registerScreen({id:'browser-profile-setup',step:'setup',match:matches,
  title:()=>C.screens.setup[0],copy:()=>C.screens.setup[1],
  body:api=>{init(api);return controls()+`<p>${C.words.manualHelp}</p>`+(window.DeskV1ConnectBrowserCheck?.html('generic-setup') || '');},
  primary:()=>({label:C.words.continue,disabled:!!problem()}),
  bind:(root,api)=>{
    const current=S;
    if (S.profiles===null) { S.profiles=[]; api.ctx.api('GET','/api/browser/profiles').then(r=>{if(current===S){
      S.profiles=(r.profiles || []).map(p=>p.name);
      api.repaint();
    }}).catch(e=>console.warn('[browser-profile] profiles failed: '+e.message)); }
    root.querySelector('[data-cfw-primary]').disabled=!!problem();
    for(const [field,key] of [['name','name'],['profile','profile']]) root.querySelector(`[data-bp-${field}]`)?.addEventListener('input',e=>{S[key]=e.target.value;S.request=null;root.querySelector('[data-cfw-primary]').disabled=!!problem();});
    window.DeskV1ConnectBrowserCheck?.bind(root,'generic-setup',()=>({url:S.info.url,browser_profile:profileName()}),api.ctx,{own:api.own});
  },discard:clear,
});
window.DeskV1ConnectSummaryStep?.registerBranch({id:'browser-profile',word:C.words.signin,match:matches,
  problem,save:commit,accountId:()=>'',risks:()=>[C.words.browserNoExecution],
  factsHTML:()=>`<dl class="desk-v1-cf-facts"><div><dt>${C.words.name}</dt><dd>${esc(S.name)}</dd></div><div><dt>${C.words.browserProfile}</dt><dd>${esc(profileName())}</dd></div></dl>`,
  result:{rows:(res,st)=>[['Connection',C.words.saved,'connection'],['Browser profile',esc(res.browser_profile),'profile'],['Status',esc(st ? C.status(st.state) : C.words.notChecked),'status']],
    extra:()=>window.DeskV1ConnectBrowserCheck?.html('generic-result') || '',actions:()=>[],
    bind:(root,res,api)=>window.DeskV1ConnectBrowserCheck?.bind(root,'generic-result',()=>({url:res.url,browser_profile:res.browser_profile}),api.ctx,{own:api.own,status:res.status,onStatus:out=>{window.DeskV1ConnectSummaryStep.setStatus(out);api.repaint();}})},
});
window.DeskV1ConnectBrowserProfile={matches,controls,profileName};
