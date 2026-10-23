// Vault's read-only Desk accounts, plus the existing Needs you / Waiting on you lists.
import { SigninMethod } from './desk-v1-connect-signin-method.js';
import './desk-v1-connect-browser-check.js';

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let pending = [], pendingKey = '', inFlight = null;
const checks = new Set();

function clear() {
  for (const key of checks) window.DeskV1ConnectBrowserCheck.clear(key);
  checks.clear();
}

async function load() {
  if (inFlight) return inFlight;
  inFlight = (async () => {
    const r = await fetch(API_BASE + '/api/secrets/browser-accounts');
    const data = await r.json();
    if (!r.ok || data.error) throw new Error(data.error || 'Browser accounts could not be read.');
    pending = data.signin_requests || [];
    const key = JSON.stringify(pending);
    if (key !== pendingKey) {
      pendingKey = key;
      if (typeof renderFeed === 'function') renderFeed();
      if (typeof renderWaitingOnYou === 'function') renderWaitingOnYou();
    }
    return data;
  })();
  try { return await inFlight; } finally { inFlight = null; }
}

async function render(host) {
  if (!host) return;
  clear();
  host.textContent = 'Loading browser accounts…';
  try {
    const data = await load();
    if (!host.isConnected) return;
    const rows = data.browser_accounts || [];
    host.innerHTML = rows.length ? `<h3>Browser accounts</h3><p class="memory-hint">Saved in Desk. Read-only here; passwords and sign-in codes are never stored in these rows.</p>` + rows.map(row => `
      <div data-browser-account="${esc(row.id)}" style="padding:10px 0;border-bottom:1px solid var(--border);overflow-wrap:anywhere">
        <strong>${esc(row.name)}</strong> <span>${esc(SigninMethod.labels[row.signin_method] || SigninMethod.labels.other)}</span>
        <div class="memory-hint">${esc(row.url)}</div>
        <div>Browser profile: <code>${esc(row.browser_profile)}</code></div>
        <div data-browser-signin-request role="status">${row.needs_signin ? 'Sign in again' : ''}</div>
        ${window.DeskV1ConnectBrowserCheck.html('vault-' + row.id)}
      </div>`).join('') : '';
    const ctx = {api: async (method, url, body) => {
      const r = await fetch(API_BASE + url, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
      const out = await r.json();
      if (!r.ok) throw new Error(out.error || 'Connection check failed.');
      return out;
    }};
    for (const row of rows) {
      const node = host.querySelector(`[data-browser-account="${CSS.escape(row.id)}"]`);
      window.DeskV1ConnectBrowserCheck.clear('vault-' + row.id);
      checks.add('vault-' + row.id);
      window.DeskV1ConnectBrowserCheck.bind(node, 'vault-' + row.id,
        () => ({url:row.url, browser_profile:row.browser_profile}), ctx, {onStatus: async () => {
          try {
            if (inFlight) await inFlight; // a metadata fetch begun before the check cannot clear its new verdict
            const fresh = await load();
            const current = (fresh.browser_accounts || []).find(r => r.id === row.id);
            if (node.isConnected) node.querySelector('[data-browser-signin-request]').textContent = current?.needs_signin ? 'Sign in again' : '';
          } catch (e) { console.warn('[browser_accounts] refresh failed:', e.message); }
        }});
    }
  } catch (e) { host.textContent = e.message; }
}

window.BrowserAccounts = {render, clear, refresh:load};
window.browserSigninAttentionItems = () => pending.map(row => ({
  projectId:'__browser_signin__', project:row.name, domain:'', msg:'Sign in again',
  icon:'\u{1F511}', kind:'browser_signin', action:'Sign in again', sessionId:null,
}));
function poll() { load().catch(e => console.warn('[browser_accounts] metadata refresh failed:', e.message)); }
setTimeout(poll, 4000);
setInterval(poll, 30000); // local metadata only; never probes a vendor site
