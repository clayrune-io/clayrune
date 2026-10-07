// One options page, regardless of whether the service ships a profile.
import { ConnectCopy as C } from './desk-v1-connect-copy.js';
import { kindOf, optionCopy, suggestionCopy } from './desk-v1-connect-option-copy.js';
const W = window.DeskV1ConnectWizard;
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const unknown = info => !!info && !info.service?.recognised;
const D = () => window.DeskV1ConnectDiscover;
const serviceName = info => info?.service?.label || info?.host || 'the service';
function rows(api) {
  const items = [], name = serviceName(api.info);
  if (unknown(api.info)) {
    const seen = new Set();
    for (const suggestion of discovery().answer?.options || []) {
      const family = /browser|signin|oauth|password/.test(suggestion.method) ? 'signin' : /mcp/.test(suggestion.method) ? 'mcp' : 'api';
      if (seen.has(family)) continue;
      seen.add(family);
      // Evidence has no executable adapter. Selecting it edits information;
      // it never turns a classifier suggestion into an authorized connection.
      items.push({type:'reference',variant:`suggestion-${family}`,...suggestionCopy(family,name)});
    }
    items.push({type:'reference',variant:'reference',...optionCopy('information',name)});
    return items;
  }
  for (const type of api.info?.picker || []) {
    for (const variant of type.variants) {
      if (variant.kind.startsWith('custom_')) continue;
      items.push({ type: type.id, variant: variant.id, ...optionCopy(kindOf(type.id, variant.setup.signs_in), name) });
    }
  }
  // Manual software setup is the same for every service, never a discovered adapter.
  items.push({ type: 'mcp', variant: 'custom-npm', ...optionCopy('software', name) });
  items.push({ type: 'reference', variant: 'reference', ...optionCopy('information', name) });
  return items;
}
function select(api, item) { api.select({type:item.type, variant:item.variant.startsWith('suggestion-') ? 'api-details' : item.variant}); }
function discovery() { return D()?.state() || {}; }
function _discoveryStatus(api) {
  if (!unknown(api.info)) return '';
  const d = discovery(), a = d.answer;
  const text = d.busy ? C.words.investigating : d.error ? C.words.failed : !a?.options?.length ? C.words.empty : a.incomplete ? C.words.incomplete : C.words.suggestions;
  return `<p role="status" data-options-status>${esc(text)}</p>`;
}
W?.registerScreen({ id:'connection', step:'connection', title:()=>C.screens.connection[0], copy:()=>C.screens.connection[1],
  body: api => discovery().busy && unknown(api.info) ? `${_discoveryStatus(api)}<button type="button" class="desk-v1-conn-btn" data-options-cancel>${C.words.cancel}</button>` : `${_discoveryStatus(api)}<div class="desk-v1-cfw-options" role="radiogroup" aria-label="${C.screens.connection[0]}">${rows(api).slice(0,4).map(item=>`<label class="desk-v1-cfw-option" data-cfw-option="${item.type}"><input type="radio" name="cfw-type" data-cfw-type value="${esc(item.variant)}" ${api.sel.variant===item.variant?'checked':''}><span class="desk-v1-cfw-option-text"><span class="desk-v1-cfw-option-label">${esc(item.label)}</span><span class="desk-v1-cfw-option-sentence" data-cfw-option-sentence>${esc(item.sentence)}</span></span></label>`).join('')}</div>`,
  details: api => {
    const d = discovery();
    const evidence = unknown(api.info) ? [d.error || '', ...(d.answer?.options || []).map(o => `${o.title}: ${o.usage || o.guidance || ''} ${o.evidence || ''} ${o.evidence_url || ''}`), d.answer?.warning || '', ...(d.answer?.problems || []).map(p=>p.message)] : [];
    return [...rows(api).slice(4).map(item=>`<button type="button" class="desk-v1-conn-btn" data-options-extra="${esc(item.variant)}">${esc(item.label)}</button>`),
      `<button type="button" class="desk-v1-conn-btn" data-options-manual="custom-npm">${C.words.package}</button>`,
      `<button type="button" class="desk-v1-conn-btn" data-options-manual="custom-remote">${C.words.remote}</button>`,
      `<button type="button" class="desk-v1-conn-btn" data-options-api>Save API details (reference only)</button>`,
      ...evidence.filter(Boolean).map(t=>`<p>${esc(t)}</p>`),
      ...(api.info?.details?.unavailable || []).map(v=>`<p>${esc(v.title)}: ${esc(v.explanation?.text || C.words.referenceOnly)}</p>`),
      ...(api.info?.details?.delivery || []).map(v=>`<p>${esc(v.title)}: ${esc(v.text)}</p>`),
    ].join('');
  },
  primary:api=>({label:C.words.continue, disabled:discovery().busy || !(rows(api).some(r=>r.variant===api.sel.variant) || api.sel.variant==='api-details')}),
  bind:(root,api)=> {
    const cancel = () => D().cancel({...api.info,service:null,input_kind:'url'}, {api:api.ctx.api,repaint:api.repaint});
    root.querySelector('[data-options-cancel]')?.addEventListener('click',cancel);
    if (unknown(api.info) && discovery().busy) root.querySelector('[data-cfw-back]')?.addEventListener('click',cancel);
    root.querySelectorAll('[data-cfw-type]').forEach(r=>r.addEventListener('change',()=>{select(api,rows(api).find(i=>i.variant===r.value)); root.querySelector('[data-cfw-primary]').disabled=false;}));
    root.querySelectorAll('[data-options-extra]').forEach(b=>b.addEventListener('click',()=>{select(api,rows(api).find(i=>i.variant===b.dataset.optionsExtra));api.go('setup');}));
    root.querySelectorAll('[data-options-manual]').forEach(b=>b.addEventListener('click',()=>{api.select({type:'mcp',variant:b.dataset.optionsManual});api.go('setup');}));
    root.querySelector('[data-options-api]')?.addEventListener('click',()=>{api.select({type:'reference',variant:'api-details'});api.go('setup');});
  },
});
window.DeskV1ConnectOptionsStep = { unknown };
