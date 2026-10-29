// Browser-profile saved-service display. No execution or permissions editor.
import { ConnectCopy as C } from './desk-v1-connect-copy.js';
import { ConnectionWords as W } from './desk-v1-connection-words.js';
import { accountHTML } from './desk-v1-connection-account.js';
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function browserServiceStatus(s) {return W.from(s.browser_connection.status?.state);}
function html(s) {
 const st=browserServiceStatus(s), ch={...s,label:s.name};
 const details=`<p>${esc(s.link)}</p><p>${C.words.browserProfile}: ${esc(s.browser_connection.browser_profile)}</p>`;
 const t=document.createElement('template');
 t.innerHTML=accountHTML(ch,null,{site:true,status:st,details});
 t.content.querySelector('[data-connection-check]').outerHTML=window.DeskV1ConnectBrowserCheck.html('service-'+s.id);
 return `<div class="desk-v1-conn-row" data-conn-service="${esc(s.id)}" data-browser-service="${esc(s.id)}"><div class="desk-v1-conn-head"><span class="desk-v1-channel-badge">${esc(s.name)}</span>${window.DeskV1StatusSignin?.serviceWord(s,st) || `<span class="desk-v1-conn-status" data-conn-status data-state="${st.key}">${esc(st.word)}</span>`}</div>${t.innerHTML}</div>`;
}
function bind(root,s,repaint) {window.DeskV1StatusSignin?.bindService(root,s);root.querySelector('[data-cs-reopen]')?.addEventListener('click',()=>window.DeskV1ConnectionStatus.reopen({kind:'service',id:'service:'+s.id,record:s},repaint));window.DeskV1ConnectBrowserCheck?.bind(root,'service-'+s.id,()=>({url:s.link,browser_profile:s.browser_connection.browser_profile}),{api:window.DeskV1Store.api},{compact:true,status:s.browser_connection.status,onStatus:out=>{s.browser_connection.status=out;repaint();}});}
window.DeskV1BrowserService={status:browserServiceStatus,html,bind};
