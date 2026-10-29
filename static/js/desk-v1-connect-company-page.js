// The LinkedIn destination is a human's address, never an account-label guess.
// This draft joins the reading section's existing account PATCH on Save.
const LABEL = 'Company Page admin address';
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function companyId(value) {
  if (!value) return '';
  let url;
  try { url = new URL(value); } catch (_) { return null; }
  const authority = value.match(/^[^:]+:\/\/([^/?#]+)/)?.[1] || '';
  // Same destination shape as desk_pane_activity_scope.is_company_admin,
  // with the numeric ID and plain HTTPS requirements of account PATCH.
  if (url.protocol !== 'https:' || !['linkedin.com', 'www.linkedin.com'].includes(url.hostname)
      || authority.includes('@') || /[^\x00-\x7f]/.test(authority)
      || /[\s\\\x00-\x1f\x7f]/.test(value) || value.length > 2048) return null;
  // Reject literal and encoded dot segments even if they stay within admin.
  const path = value.replace(/^[^:]+:\/\/[^/?#]+/, '').split(/[?#]/)[0];
  if (path.replace(/%2e/ig, '.').split('/').some(p => p === '.' || p === '..')) return null;
  return url.pathname.match(/^\/company\/([0-9]{1,20})\/admin(?:\/|$)/)?.[1] ?? null;
}
export function createCompanyPageDraft(account) {
  const saved = account?.read_pages || [];
  let original = saved.find(p => p.role === 'activity' && /\/company\/[^/]+\/admin(?:\/|$)/.test(p.url))?.url || '';
  let value = original, originalId = account?.organization_id || '', touched = false;
  let savedKind = account?.browser_setup?.account_kind || account?.account_kind;
  const problem = () => companyId(value) === null
    ? 'Enter a LinkedIn Company Page admin HTTPS address with a numeric company ID, such as https://www.linkedin.com/company/123456/admin/notifications/.' : '';
  // An address saved by the older editor may still have a member connection.
  // A human Save must repair that case even when the address was not retyped.
  const changed = () => value !== original || (touched && companyId(value) !== originalId)
    || (!!original && !!savedKind && savedKind !== 'organization');
  function html() {
    const error = problem();
    return `<div data-reading-company-page><label class="desk-v1-conn-add-field">${LABEL}
      <input type="url" class="desk-v1-rules-textinput" data-reading-company-address value="${esc(value)}" maxlength="2048" aria-describedby="reading-company-help reading-company-error" aria-invalid="${!!error}" placeholder="https://www.linkedin.com/company/123456/admin/notifications/">
      </label><p class="desk-v1-cfw-fact-text" id="reading-company-help">Optional. Leave empty for your personal profile. For a Company Page, paste its admin activity address.</p>
      <div role="alert" id="reading-company-error" data-reading-company-error>${esc(error)}</div></div>`;
  }
  function bind(root, _repaint, sync) {
    root.querySelector('[data-reading-company-address]')?.addEventListener('input', e => {
      value = e.target.value.trim(); touched = true;
      const error = problem();
      root.querySelector('[data-reading-company-error]').textContent = error;
      e.target.setAttribute('aria-invalid', String(!!error));
      sync();
    });
  }
  return { html, bind, problem, clearInput:() => {},
    draft:() => changed() && !problem() ? { organization_id:companyId(value), read_pages:value ? [{ role:'activity', url:value }] : [] } : {},
    summary:() => [`${LABEL}: ${value || 'Personal profile'}`],
    accept:() => { original = value; originalId = companyId(value); savedKind = originalId ? 'organization' : 'member'; touched = false; },
  };
}
