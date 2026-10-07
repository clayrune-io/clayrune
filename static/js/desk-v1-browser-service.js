// Browser-profile saved-service display. No execution or permissions editor.
import { ConnectCopy as C } from './desk-v1-connect-copy.js';
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function status(s) {return {key:'saved',word:C.words.notChecked};}
function html(s) {return `<div data-browser-service="${esc(s.id)}"><h3>${esc(s.name)}</h3><p>${esc(s.link)}</p><p>${C.words.browserProfile}: ${esc(s.browser_connection.browser_profile)}</p><p>${C.words.browserNoExecution}</p><p role="status">${status(s).word}</p></div>`;}
window.DeskV1BrowserService={status,html,bind:()=>{}};
