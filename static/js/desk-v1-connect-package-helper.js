// Suggestions stay in this module until the user accepts them into empty fields.
import {ConnectCopy as C} from './desk-v1-connect-copy.js';
const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export function createPackageHelper() {
  let open=false, busy=false, question='', content=null, error='', generation=0, applied=false;
  function reset() { generation++;open=false;busy=false;question='';content=null;error='';applied=false; }
  function html() {
    if (!open) return `<button type="button" class="desk-v1-cf-link" data-pk-help-open>${C.words.packageHelp}</button>`;
    const suggestions=content?.suggestions;
    const rows=suggestions ? [
      suggestions.command && [C.words.command, suggestions.command],
      suggestions.entry && [C.words.packageFile, suggestions.entry],
      suggestions.args?.length && [C.words.arguments, suggestions.args.join('\n')],
      suggestions.credentials?.length && [C.words.credentialNames, suggestions.credentials.join(', ')],
    ].filter(Boolean) : [];
    return `<section class="desk-v1-cfw-fact" data-pk-helper aria-label="${C.words.packageHelp}">
      <h4>${C.words.packageHelp}</h4><p class="desk-v1-cfw-fact-text">${C.words.packageHelpIntro}</p>
      <label class="desk-v1-conn-add-field">${C.words.packageQuestion}<textarea class="desk-v1-rules-textinput" data-pk-help-question rows="2" maxlength="1200" aria-describedby="pk-helper-question-help">${esc(question)}</textarea><span class="desk-v1-cfw-fact-text" id="pk-helper-question-help">${C.words.packageQuestionHelp}</span></label>
      <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-pk-help-ask ${busy?'disabled':''}>${busy?C.words.checking:C.words.packageAsk}</button>
      ${busy?`<p role="status"><span class="desk-v1-cfk-spinner" aria-hidden="true"></span>${C.words.packageHelpBusy}</p>`:''}
      ${error?`<p role="alert">${esc(error)}</p>`:''}
      ${content?`<p class="desk-v1-cfw-fact-text" data-pk-help-answer data-connect-value>${esc(content.answer)}</p><p class="desk-v1-cfw-fact-text" data-connect-value>${esc(content.warning)}</p>
      ${rows.map(([label,value])=>`<div class="desk-v1-cfw-fact"><div class="desk-v1-cfw-fact-head">${esc(label)}</div><div class="desk-v1-cfw-fact-text" data-pk-help-value data-connect-value>${esc(value)}</div></div>`).join('')}
      ${rows.length?`<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-pk-help-accept ${busy||applied?'disabled':''}>${C.words.packageUseSuggestions}</button>`:''}`:''}
      ${applied?`<p role="status">${C.words.packageHelpApplied}</p>`:''}
    </section>`;
  }
  function bind(root, {api, request, accept}) {
    root.querySelector('[data-pk-help-open]')?.addEventListener('click',()=>{open=true;api.repaint();});
    root.querySelector('[data-pk-help-question]')?.addEventListener('input',event=>{question=event.target.value;});
    root.querySelector('[data-pk-help-ask]')?.addEventListener('click',async()=>{
      if (busy) return;
      const gen=++generation, body={...request(), question:question.trim()||C.words.packageDefaultQuestion, previous_answer:content?.answer||''};
      busy=true;error='';applied=false;api.repaint();
      try {
        const response=await api.ctx.api('POST','/api/desk/connect/custom/setup-help',body);
        if(gen!==generation) return;
        if(!response?.content?.answer || !response.content.suggestions) throw new Error(C.words.packageHelpFailed);
        content=response.content;
      } catch(e) { if(gen===generation) error=e.message||C.words.packageHelpFailed; }
      finally { if(gen===generation){busy=false;api.repaint();} }
    });
    root.querySelector('[data-pk-help-accept]')?.addEventListener('click',()=>{
      if (!busy && content && !applied) { accept(content.suggestions);applied=true;api.repaint(); }
    });
  }
  return {html,bind,reset};
}
