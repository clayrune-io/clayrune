// Browser-profile saved-service display. No execution or permissions editor.
import { ConnectCopy as C } from './desk-v1-connect-copy.js';
import { ConnectionRemoval as Remove } from './desk-v1-connection-remove.js';
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function browserServiceStatus(s) {const st=s.browser_connection.status;return {key:st?.state==='verified'?'ok':'saved',word:st ? C.status(st.state) : C.words.notChecked};}
function html(s) {return `<div class="desk-v1-conn-row" data-conn-service="${esc(s.id)}" data-browser-service="${esc(s.id)}"><div class="desk-v1-conn-head"><span class="desk-v1-channel-badge">${esc(s.name)}</span>${Remove.html(s.name)}</div><p>${esc(s.link)}</p><p>${C.words.browserProfile}: ${esc(s.browser_connection.browser_profile)}</p><p>${C.words.browserNoExecution}</p><p role="status">${browserServiceStatus(s).word}</p>${window.DeskV1ConnectBrowserCheck?.html('service-'+s.id) || ''}</div>`;}
function bind(root,s,repaint) {window.DeskV1ConnectBrowserCheck?.bind(root,'service-'+s.id,()=>({url:s.link,browser_profile:s.browser_connection.browser_profile}),{api:window.DeskV1Store.api},{status:s.browser_connection.status,onStatus:out=>{s.browser_connection.status=out;repaint();}});}
window.DeskV1BrowserService={status:browserServiceStatus,html,bind};
