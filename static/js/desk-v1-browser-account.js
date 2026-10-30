// Browser verification display plugs into the account facets; publishing stays separate.
import { ConnectCopy as C } from './desk-v1-connect-copy.js';
import { ConnectionWords as W } from './desk-v1-connection-words.js';
function facet(ch,f) {
 const status=ch.browser_setup?.status;
 return status ? {...f,key:W.from(status.state).key,word:C.status(status.state),detail:status.state==='verified'?C.words.browserProof:status.message || ''} : null;
}
function target(ch) {
 const setup=ch.browser_setup;
 const profile=setup?.refs?.browser_profile || ch.browser_profile;
 if(!profile)return null;
 const url=setup?.url || {x:'https://x.com',linkedin:'https://www.linkedin.com',instagram:'https://www.instagram.com',youtube:'https://www.youtube.com',reddit:'https://www.reddit.com',tiktok:'https://www.tiktok.com'}[ch.platform];
 return url ? {url,browser_profile:profile} : null;
}
function html(ch) {return target(ch) ? window.DeskV1ConnectBrowserCheck?.html('account-'+ch.id) || '' : '';}
function bind(row,ch,repaint) {
 if(!target(ch))return;
 window.DeskV1ConnectBrowserCheck?.bind(row,'account-'+ch.id,()=>target(ch),{api:window.DeskV1Store.api},{compact:true,status:ch.browser_setup?.status,onStatus:out=>{ch.browser_setup=ch.browser_setup || {refs:{browser_profile:ch.browser_profile}};ch.browser_setup.status=out;repaint();}});
}
window.DeskV1BrowserAccount={facet,target,html,bind};
