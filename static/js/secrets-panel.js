// ── Secrets vault ────────────────────────────────────────────────────────────
//
// The UI half of mc/secrets_store.py. This panel exists for one reason: a
// credential must reach the server WITHOUT passing through the agent. Typing a
// password into the chat composer puts it in the transcript — and from there in
// MEMORY.md and possibly a distilled skill, permanently. This form posts
// browser → server directly, so the agent never sees the value.
//
// There is deliberately no way to read a value back: the API has no
// plaintext-returning route, so "reveal" is not implementable here by design.
// Editing a secret's metadata leaves the value untouched (PATCH with no value).

let _secretsAuditOpen = false;

function _secTimeAgo(iso) {
  if (!iso) return 'never';
  const ms = Date.now() - new Date(iso).getTime();
  if (isNaN(ms)) return 'never';
  const s = Math.floor(ms / 1000);
  if (s < 60) return 'just now';
  const m = Math.floor(s / 60);
  if (m < 60) return m + 'm ago';
  const h = Math.floor(m / 60);
  if (h < 24) return h + 'h ago';
  return Math.floor(h / 24) + 'd ago';
}

async function openSecretsVault() {
  const modalId = '__secrets';
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    refreshSecretsList();
    return;
  }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 820);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">&#x1F510; Secrets</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div style="padding:4px 24px 20px 28px">
      <div style="font-size:12px;color:var(--text-faint);line-height:1.55;margin-bottom:12px">
        Passwords and tokens agents can <em>use</em> without ever seeing them.
        Type credentials here — never in the chat, where they'd be saved to the
        transcript forever.
      </div>
      <div id="secrets-keywarn"></div>
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px">
        <span id="secrets-count" class="memory-hint" style="margin:0">Loading…</span>
        <div style="display:flex;gap:6px">
          <button class="btn-header-action" style="padding:5px 12px;font-size:11px"
                  onclick="toggleSecretsAudit()" id="secrets-audit-btn">Access log</button>
          <button class="btn-add" style="padding:5px 12px;font-size:11px"
                  onclick="openSecretEditor(null)">Add secret</button>
        </div>
      </div>
      <div id="secrets-list"></div>
      <div id="secrets-audit" style="display:none;margin-top:16px"></div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);

  await refreshSecretsList();
}

async function refreshSecretsList() {
  const list = document.getElementById('secrets-list');
  const countEl = document.getElementById('secrets-count');
  const warnEl = document.getElementById('secrets-keywarn');
  if (!list) return;
  let data;
  try {
    const res = await fetch(API_BASE + '/api/secrets');
    data = await res.json();
    if (data.error) throw new Error(data.error);
  } catch (e) {
    list.innerHTML = `<div class="process-empty">Could not read the vault: ${esc(e.message)}</div>`;
    return;
  }

  // A file-backed master key is readable by anything running as this user,
  // whereas the OS keyring is at least gated by the login session. Say so.
  if (warnEl) {
    warnEl.innerHTML = data.key_at_rest_warning ? `
      <div style="padding:8px 12px;margin-bottom:12px;border:1px solid var(--border);
                  border-left:3px solid var(--warn,#c9852a);border-radius:4px;
                  font-size:11px;color:var(--text-faint);line-height:1.5">
        ${esc(data.key_at_rest_warning)}
      </div>` : '';
  }

  const secrets = data.secrets || [];
  if (countEl) countEl.textContent = secrets.length
    ? `${secrets.length} secret${secrets.length === 1 ? '' : 's'}`
    : 'No secrets yet';

  if (!secrets.length) {
    list.innerHTML = `<div class="process-empty">
      Nothing stored. Add one, then reference it in a task as
      <code>{{secret:name}}</code> — the server fills it in at the moment of use.
    </div>`;
    return;
  }

  list.innerHTML = secrets.map(s => {
    const scopeBadge = s.scope === 'global'
      ? '<span style="font-size:10px;color:var(--text-faint)">all projects</span>'
      : `<span style="font-size:10px;color:var(--text-faint)">only ${esc(s.scope)}</span>`;
    const totpBadge = s.kind === 'totp' ? `
      <span title="Two-factor code generator${s.issuer ? ' — ' + esc(s.issuer) : ''}"
            style="font-size:10px;padding:1px 6px;border:1px solid var(--border);
                   border-radius:99px;color:var(--text-faint)">2FA code</span>` : '';
    const attended = s.allow_unattended ? '' : `
      <span title="Steward and scheduled runs are refused this credential"
            style="font-size:10px;padding:1px 6px;border:1px solid var(--border);
                   border-radius:99px;color:var(--text-faint)">attended only</span>`;
    const used = s.use_count
      ? `used ${s.use_count}&times;, last ${esc(_secTimeAgo(s.last_used_at))}`
      : 'never used';
    return `
      <div style="display:flex;align-items:flex-start;gap:12px;padding:10px 0;
                  border-bottom:1px solid var(--border)">
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <code style="font-size:13px;color:var(--text);font-family:var(--mono)">${esc(s.name)}</code>
            ${scopeBadge}${totpBadge}${attended}
          </div>
          ${s.description ? `<div style="font-size:11px;color:var(--text-faint);margin-top:3px">${esc(s.description)}</div>` : ''}
          <div style="font-size:10px;color:var(--text-faint);margin-top:3px">${used}</div>
        </div>
        <div style="display:flex;gap:6px;flex-shrink:0">
          <button class="btn-header-action" style="padding:4px 10px;font-size:11px"
                  title="Copy the placeholder to paste into a task"
                  onclick="copySecretPlaceholder('${esc(s.name)}')">Copy ref</button>
          <button class="btn-header-action" style="padding:4px 10px;font-size:11px"
                  onclick="openSecretEditor('${esc(s.name)}')">Edit</button>
          <button class="btn-header-action" style="padding:4px 10px;font-size:11px"
                  onclick="deleteSecret('${esc(s.name)}')">Delete</button>
        </div>
      </div>`;
  }).join('');
}

function copySecretPlaceholder(name) {
  const ref = '{{secret:' + name + '}}';
  navigator.clipboard.writeText(ref)
    .then(() => showToast('Copied ' + ref))
    .catch(() => showToast('Copy failed — the reference is ' + ref, 5000));
}

async function deleteSecret(name) {
  if (!confirm(`Delete "${name}"?\n\nAnything referencing {{secret:${name}}} will start failing.`)) return;
  try {
    const res = await fetch(API_BASE + '/api/secrets/' + encodeURIComponent(name), { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    showToast('Deleted ' + name);
  } catch (e) {
    showToast('Delete failed: ' + e.message, 4000);
  }
  refreshSecretsList();
}

async function toggleSecretsAudit() {
  const box = document.getElementById('secrets-audit');
  const btn = document.getElementById('secrets-audit-btn');
  if (!box) return;
  _secretsAuditOpen = !_secretsAuditOpen;
  box.style.display = _secretsAuditOpen ? 'block' : 'none';
  if (btn) btn.textContent = _secretsAuditOpen ? 'Hide log' : 'Access log';
  if (!_secretsAuditOpen) return;

  box.innerHTML = '<div class="memory-hint">Loading…</div>';
  let records = [];
  try {
    const res = await fetch(API_BASE + '/api/secrets/audit?limit=80');
    records = (await res.json()).records || [];
  } catch (e) {
    box.innerHTML = `<div class="process-empty">Could not read the log: ${esc(e.message)}</div>`;
    return;
  }
  if (!records.length) {
    box.innerHTML = '<div class="process-empty">No access recorded yet.</div>';
    return;
  }
  box.innerHTML = `
    <div style="font-size:11px;color:var(--text-faint);margin-bottom:6px">
      Every read, change and refusal. Values are never recorded.
    </div>
    <table class="process-table">
      <thead><tr><th>When</th><th>Event</th><th>Secret</th><th>Used by</th><th>Why refused</th></tr></thead>
      <tbody>${records.map(r => `
        <tr>
          <td style="white-space:nowrap">${esc(_secTimeAgo(r.ts))}</td>
          <td>${esc(r.event || '')}${r.unattended ? ' <span style="color:var(--text-faint)">(unattended)</span>' : ''}</td>
          <td><code style="font-size:11px">${esc(r.name || '')}</code></td>
          <td>${esc(r.consumer || '—')}${r.project ? ' / ' + esc(r.project) : ''}</td>
          <td style="color:var(--text-faint)">${esc(r.reason || '')}</td>
        </tr>`).join('')}</tbody>
    </table>`;
}

// ── Editor ───────────────────────────────────────────────────────────────────

async function openSecretEditor(name) {
  const isNew = !name;
  const modalId = '__secret-edit';
  if (openModals.has(modalId)) closeModalById(modalId);

  let existing = null;
  if (!isNew) {
    try {
      const res = await fetch(API_BASE + '/api/secrets');
      existing = ((await res.json()).secrets || []).find(s => s.name === name) || null;
    } catch (e) { /* fall through to a blank form */ }
  }

  const realProjects = (allProjects || []).filter(p => !isIncognitoProject(p));
  const curScope = (existing && existing.scope) || 'global';
  const projOptions = realProjects.map(p =>
    `<option value="${esc(p.id)}"${p.id === curScope ? ' selected' : ''}>${esc(p.name || p.id)}</option>`
  ).join('');
  const scopeIsProject = curScope !== 'global';
  const allowUnattended = existing ? existing.allow_unattended !== false : true;

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 620);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">
        &#x1F510; ${isNew ? 'Add secret' : 'Edit: ' + esc(name)}
      </span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div style="padding:4px 24px 20px 28px;display:flex;flex-direction:column;gap:14px">

      <div style="font-size:11px;color:var(--text-faint);line-height:1.55;
                  padding:8px 12px;border:1px solid var(--border);border-radius:4px">
        This goes straight from your browser to this machine. It is encrypted
        with a key held in your OS keychain and stored outside the repo, so it
        is never committed and never reaches the agent's transcript.
      </div>

      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">1. Name</label>
        <input type="text" id="sec-name" value="${esc(name || '')}" ${isNew ? '' : 'readonly'}
          placeholder="reddit.password" autocomplete="off" spellcheck="false"
          style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);
                 border:1px solid var(--border);border-radius:4px;color:var(--text);
                 font-family:var(--mono);${isNew ? '' : 'opacity:0.6'}">
        <div style="font-size:10px;color:var(--text-faint);margin-top:3px">
          Lowercase, dot-namespaced. Referenced in tasks as
          <code>{{secret:reddit.password}}</code>.
        </div>
      </div>

      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">
          2. Value ${isNew ? '' : '<span style="opacity:.7">— leave blank to keep the current one</span>'}
        </label>
        <div style="display:flex;gap:6px">
          <input type="password" id="sec-value" autocomplete="off" spellcheck="false"
            placeholder="${isNew ? 'paste from your password manager' : 'unchanged'}"
            style="flex:1;padding:6px 10px;font-size:13px;background:var(--surface2);
                   border:1px solid var(--border);border-radius:4px;color:var(--text);
                   font-family:var(--mono)">
          <button class="btn-header-action" style="padding:4px 10px;font-size:11px"
                  onclick="_secToggleReveal()" id="sec-reveal">Show</button>
        </div>
        <div style="font-size:10px;color:var(--text-faint);margin-top:3px">
          Once saved it cannot be displayed again — there is no route that hands
          a value back. Rotate it here if you lose it.
        </div>
        <div style="font-size:10px;color:var(--text-faint);margin-top:5px;line-height:1.5">
          <strong>For 2FA:</strong> paste an <code>otpauth://</code> setup link
          (the "can't scan the QR?" text on the enrolment page) and it becomes a
          code generator. Google Authenticator's
          <em>Transfer accounts &rarr; Export</em> link
          (<code>otpauth-migration://</code>) imports every account at once.
          <div style="margin-top:3px">
            Storing the 2FA seed next to the password does put both factors in
            one place. For a bank or a registrar, consider leaving 2FA off here
            and letting the agent ask you.
          </div>
        </div>
      </div>

      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">3. What it's for</label>
        <input type="text" id="sec-desc" value="${esc((existing && existing.description) || '')}"
          placeholder="Reddit account used for launch posts" autocomplete="off"
          style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);
                 border:1px solid var(--border);border-radius:4px;color:var(--text)">
      </div>

      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">4. Who can use it</label>
        <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
          <label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer">
            <input type="radio" name="sec-scope" value="global" ${scopeIsProject ? '' : 'checked'}
                   onchange="_secScopeChanged()"> Every project
          </label>
          <label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer">
            <input type="radio" name="sec-scope" value="project" ${scopeIsProject ? 'checked' : ''}
                   onchange="_secScopeChanged()" ${realProjects.length ? '' : 'disabled'}> One project
          </label>
          <select id="sec-project" style="padding:4px 8px;font-size:12px;background:var(--surface2);
                  border:1px solid var(--border);border-radius:4px;color:var(--text);
                  max-width:220px;${scopeIsProject ? '' : 'display:none'}">
            ${projOptions || '<option value="">No projects</option>'}
          </select>
        </div>
      </div>

      <div>
        <label style="display:flex;align-items:flex-start;gap:8px;font-size:12px;cursor:pointer">
          <input type="checkbox" id="sec-unattended" ${allowUnattended ? 'checked' : ''} style="margin-top:2px">
          <span>
            5. Usable by unattended runs
            <div style="font-size:10px;color:var(--text-faint);margin-top:2px">
              Uncheck for anything you don't want the steward or a scheduled job
              touching while you're away.
            </div>
          </span>
        </label>
      </div>

      <div id="sec-status" style="font-size:11px;color:var(--text-faint);min-height:14px"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn-secondary" onclick="closeModalById('${modalId}')">Cancel</button>
        <button class="btn-add" id="sec-save" onclick="saveSecret('${modalId}', ${isNew})">
          ${isNew ? 'Save secret' : 'Save changes'}
        </button>
      </div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
  const first = document.getElementById(isNew ? 'sec-name' : 'sec-value');
  if (first) first.focus();
}

function _secToggleReveal() {
  const input = document.getElementById('sec-value');
  const btn = document.getElementById('sec-reveal');
  if (!input || !btn) return;
  const hidden = input.type === 'password';
  input.type = hidden ? 'text' : 'password';
  btn.textContent = hidden ? 'Hide' : 'Show';
}

function _secScopeChanged() {
  const sel = document.getElementById('sec-project');
  const isProject = document.querySelector('input[name="sec-scope"]:checked')?.value === 'project';
  if (sel) sel.style.display = isProject ? '' : 'none';
}

async function saveSecret(modalId, isNew) {
  const status = document.getElementById('sec-status');
  const btn = document.getElementById('sec-save');
  const nameEl = document.getElementById('sec-name');
  const valueEl = document.getElementById('sec-value');
  const name = (nameEl?.value || '').trim();
  const value = valueEl?.value || '';
  const scopeIsProject = document.querySelector('input[name="sec-scope"]:checked')?.value === 'project';
  const scope = scopeIsProject ? (document.getElementById('sec-project')?.value || '') : 'global';

  const fail = (msg) => { if (status) { status.textContent = msg; status.style.color = 'var(--danger,#c0553f)'; } };
  // A Google Authenticator export names its own accounts, so the name field is
  // not required (and would be meaningless) for that path.
  const isBulkImport = /^otpauth-migration:\/\//i.test(value.trim());
  if (!name && !isBulkImport) return fail('Give it a name.');
  if (isNew && !value) return fail('Paste the value you want stored.');
  if (scopeIsProject && !scope) return fail('Pick a project, or choose “Every project”.');

  const body = {
    name,
    description: document.getElementById('sec-desc')?.value || '',
    scope,
    allow_unattended: !!document.getElementById('sec-unattended')?.checked,
  };
  // On edit, an empty value means "keep the current one" — the PATCH route
  // re-seals the existing value rather than us ever holding it here.
  if (value) body.value = value;

  if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
  if (status) { status.textContent = ''; status.style.color = 'var(--text-faint)'; }
  try {
    // A Google Authenticator export carries MANY accounts, so it can't go
    // through the single-secret path — route it to the importer instead of
    // storing the whole payload as one useless blob.
    if (/^otpauth-migration:\/\//i.test(value.trim())) {
      const r = await fetch(API_BASE + '/api/secrets/import-authenticator', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          uri: value.trim(), commit: true, scope,
          allow_unattended: body.allow_unattended,
        })
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || r.statusText);
      if (valueEl) valueEl.value = '';
      closeModalById(modalId);
      showToast(`Imported ${d.imported.length} account(s) from Google Authenticator`);
      refreshSecretsList();
      return;
    }
    const res = isNew
      ? await fetch(API_BASE + '/api/secrets', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...body, value })
        })
      : await fetch(API_BASE + '/api/secrets/' + encodeURIComponent(name), {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    // Drop the plaintext from the DOM the moment it is no longer needed.
    if (valueEl) valueEl.value = '';
    closeModalById(modalId);
    showToast(isNew ? `Saved — reference it as {{secret:${name}}}` : 'Saved ' + name);
    refreshSecretsList();
  } catch (e) {
    fail(e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = isNew ? 'Save secret' : 'Save changes'; }
  }
}


// ── Interop: re-expose for inline / generated-on*= callers. All runtime-only.
//    `openSecretsVault` ← sidebarNav('secrets'); the rest ← the modals' own
//    generated on*= handlers. `_secTimeAgo` stays module-private.
window.openSecretsVault = openSecretsVault;
window.refreshSecretsList = refreshSecretsList;
window.openSecretEditor = openSecretEditor;
window.saveSecret = saveSecret;
window.deleteSecret = deleteSecret;
window.copySecretPlaceholder = copySecretPlaceholder;
window.toggleSecretsAudit = toggleSecretsAudit;
window._secToggleReveal = _secToggleReveal;
window._secScopeChanged = _secScopeChanged;
