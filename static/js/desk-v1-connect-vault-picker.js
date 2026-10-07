// Metadata-only picker shared by sign-in and information-only credentials.
import { ConnectCopy as C } from './desk-v1-connect-copy.js';
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function metadata(response, service = '') {
  return (response?.secrets || []).filter(s => s.scope === 'global' || !s.scope)
    .filter(s => !service || s.username && s.entry_type === 'login' &&
      (s.name.toLowerCase() === service || s.name.toLowerCase().startsWith(service + '.')))
    .map(s => ({name:s.name, username:s.username || '', scope:s.scope || 'global', allow_unattended:s.allow_unattended}));
}
function html(rows, selected, attribute, label = C.words.credential) {
  return `<label class="desk-v1-conn-add-field">${esc(label)}<select class="desk-v1-rules-textinput" ${attribute}>
    <option value="">${esc(C.words.choose)}</option>${rows.map(s=>`<option value="${esc(s.name)}" ${s.name===selected?'selected':''}>${esc(s.name)}${s.username?` (${esc(s.username)})`:''}</option>`).join('')}</select></label>`;
}
function fields(userAttribute, passwordAttribute, reference = false) {
  return `<label class="desk-v1-conn-add-field" ${reference?'data-ref-user-label':''}>${C.words.username}<input class="desk-v1-rules-textinput" ${userAttribute} maxlength="200" autocomplete="off" autocapitalize="off"></label><label class="desk-v1-conn-add-field">${reference?C.words.secretValue:C.words.password}<input class="desk-v1-rules-textinput" type="password" ${passwordAttribute} maxlength="8192" autocomplete="new-password"></label>`;
}
window.DeskV1ConnectVaultPicker = {metadata, html, fields};
