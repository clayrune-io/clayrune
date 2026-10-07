// GitHub identification shares the package screens and their immutable Save.
import { ConnectCopy as C } from './desk-v1-connect-copy.js';
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function matches(value) { return /^https?:\/\/github\.com\/[a-z\d._-]+\/[a-z\d._-]+(?:\.git)?(?:\/tree\/[^/]+)?\/?$/i.test(value.trim()); }
async function stage(api, url) { return api.ctx.api('POST','/api/desk/connect/custom/github/stage',{url}); }
function commandHTML(value, missing) { return `${missing?`<div role="status">${C.words.commandMissing}</div>`:''}<label class="desk-v1-conn-add-field">${C.words.command}<input class="desk-v1-rules-textinput" data-pk-command-input value="${esc(value)}" autocomplete="off" spellcheck="false"></label>`; }
window.DeskV1ConnectGithub={matches,stage,commandHTML};
