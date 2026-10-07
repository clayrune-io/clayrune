// Browser verification display plugs into the account facets; publishing stays separate.
import { ConnectCopy as C } from './desk-v1-connect-copy.js';
function facet(ch,f) {
 const status=ch.browser_setup?.status;
 return status ? {...f,key:status.state==='verified'?'ok':'saved',word:C.status(status.state),detail:status.state==='verified'?C.words.browserProof:status.message || ''} : null;
}
function target(ch) {
 const setup=ch.browser_setup;
 if(!setup?.refs?.browser_profile)return null;
 return setup.url ? {url:setup.url,browser_profile:setup.refs.browser_profile} : null;
}
function html(ch) {return target(ch) ? window.DeskV1ConnectBrowserCheck?.html('account-'+ch.id) || '' : '';}
function bind(row,ch,repaint) {
 if(!target(ch))return;
 window.DeskV1ConnectBrowserCheck?.bind(row,'account-'+ch.id,()=>target(ch),{api:window.DeskV1Store.api},{status:ch.browser_setup.status,onStatus:out=>{ch.browser_setup.status=out;repaint();}});
}
window.DeskV1BrowserAccount={facet,html,bind};
