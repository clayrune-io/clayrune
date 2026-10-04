// The vault credential form, shared (Desk connect-by-URL spec, "Reuse and file
// ownership"): the Secrets panel's editor and the Connections "Add service" flow
// draw the SAME fields from here, so a rule or a label changes in one place.
//
// What is shared: the entry types and their wording, the name rule and its
// "Use <slug>" fix, the field markup (`fieldsHtml`), and the redraw that follows a
// type or engine-preset change (`render`). What is NOT shared: saving. The panel
// creates, edits (PATCH) and bulk-imports through its own passcode prompt; the
// connect flow hands the values to its single final Save. Nothing here talks to the
// server, and a value typed into the password field lives only in that input: this
// module never copies it into a variable, storage, a draft or a log (`read` returns
// it to the caller who asked, once, and `clear` empties the input).
//
// Every id is `<prefix>-<field>` (the panel is `sec`, the flow `cf`), so two forms
// can never answer to each other's elements. Window-bridged, no `import`.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // What each entry type shows. `user` null = the type has no username slot. A
  // key pair's Key ID rides the entry's username slot (so {{user:name}} and the
  // Desk connectors keep working); the form just labels it for what it is.
  const TYPES = {
    login: {
      label: 'Login',
      user: { label: 'Username', required: false, placeholder: 'ron@example.com',
        help: 'The other half of a login. Referenced as <code>{{user:NAME}}</code>, and shown in the list so two accounts on the same site stay apart. Not encrypted — it is an identifier, not a credential.' },
      value: { label: 'Password', placeholder: 'paste from your password manager' },
      twofa: true, namePh: 'reddit.password', descPh: 'Reddit account used for launch posts',
    },
    api_key: {
      label: 'API key', user: null,
      value: { label: 'API key', placeholder: 'paste the API key' },
      twofa: false, namePh: 'openai.api-key', descPh: 'OpenAI key for the weekly digest',
    },
    api_key_pair: {
      label: 'API key pair',
      user: { label: 'Key ID', required: true, placeholder: 'paste the key ID',
        help: 'The public half of the pair, stored beside the secret as one entry. Referenced as <code>{{user:NAME}}</code>. Not encrypted — it is an identifier, not a credential.' },
      value: { label: 'Key secret', placeholder: 'paste the key secret' },
      twofa: false, namePh: 'higgsfield.key', descPh: 'Higgsfield key for video generation',
    },
    token: {
      label: 'Token', user: null,
      value: { label: 'Token', placeholder: 'paste the token' },
      twofa: false, namePh: 'github.token', descPh: 'GitHub token for the release script',
    },
  };
  const TYPE_ORDER = ['login', 'api_key', 'api_key_pair', 'token'];

  // Same rule as mc/secrets_store.py _NAME_RE. The server stays the authority; this
  // only catches the common slip (capitals, spaces) before the passcode prompt.
  const NAME_RE = /^[a-z0-9][a-z0-9._-]{0,63}$/;

  // 'IIElevenlabs key' -> 'iielevenlabs-key'. '' when nothing usable survives.
  function slug(raw) {
    return String(raw || '').trim().toLowerCase()
      .replace(/[^a-z0-9._-]+/g, '-')
      .replace(/^[^a-z0-9]+/, '')
      .slice(0, 64)
      .replace(/[-._]+$/, '');
  }

  function nameProblem(name) {
    if (NAME_RE.test(name)) return '';
    const bits = [];
    if (/[A-Z]/.test(name)) bits.push('capital letters');
    if (/\s/.test(name)) bits.push('spaces');
    if (/[^A-Za-z0-9._\-\s]/.test(name)) bits.push('other symbols');
    if (name.length > 64) bits.push('more than 64 characters');
    if (/^[^A-Za-z0-9]/.test(name)) bits.push('a leading symbol');
    return 'A secret name cannot contain ' + (bits.join(', ') || 'those characters')
      + '. Use lowercase letters, digits, "." "-" "_".';
  }

  function $(p, field) { return document.getElementById(`${p}-${field}`); }

  // The field markup. o = {
  //   p            id prefix
  //   isNew, name, lockName   the name field's value and whether it is read-only
  //   existing     the stored entry being edited (username, description, scope, allow_unattended) or null
  //   projects     [{id,name}] for the "Who can use it" row, or null to leave that row out (the entry is global)
  //   numbered     false = no "1. 2. 3." labels (the connect flow numbers its own steps)
  //   handlers     {type(t) -> inline JS, reveal, scope} for hosts that wire inline handlers; null = the host binds
  //   intro        false = leave out the "goes straight from your browser" notice
  // }
  function fieldsHtml(o) {
    const p = o.p;
    const existing = o.existing || null;
    const isNew = !!o.isNew;
    const numbered = o.numbered !== false;
    const step = numbered ? '<span class="sec-step"></span>' : '';
    const H = o.handlers || null;
    const typeChips = TYPE_ORDER.map((t) => `
    <label style="display:flex;align-items:center;gap:6px;font-size:12px;padding:5px 12px;
                  border:1px solid var(--border);border-radius:99px;cursor:pointer">
      <input type="radio" name="${p}-type" value="${t}" ${H ? `onchange="${H.type(t)}"` : ''}
             style="margin:0"> ${esc(TYPES[t].label)}
    </label>`).join('');
    const projects = o.projects || null;
    const curScope = (existing && existing.scope) || 'global';
    const scopeIsProject = curScope !== 'global';
    const projOptions = (projects || []).map((pr) =>
      `<option value="${esc(pr.id)}"${pr.id === curScope ? ' selected' : ''}>${esc(pr.name || pr.id)}</option>`).join('');
    const allowUnattended = existing ? existing.allow_unattended !== false : true;
    const name = o.name || '';
    const lockName = !!o.lockName;
    const scopeRow = projects ? `
      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">${step}Who can use it</label>
        <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
          <label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer">
            <input type="radio" name="${p}-scope" value="global" ${scopeIsProject ? '' : 'checked'}
                   ${H ? `onchange="${H.scope}"` : ''}> Every project
          </label>
          <label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer">
            <input type="radio" name="${p}-scope" value="project" ${scopeIsProject ? 'checked' : ''}
                   ${H ? `onchange="${H.scope}"` : ''} ${projects.length ? '' : 'disabled'}> One project
          </label>
          <select id="${p}-project" style="padding:4px 8px;font-size:12px;background:var(--surface2);
                  border:1px solid var(--border);border-radius:4px;color:var(--text);
                  max-width:220px;${scopeIsProject ? '' : 'display:none'}">
            ${projOptions || '<option value="">No projects</option>'}
          </select>
        </div>
      </div>` : '';
    const intro = o.intro === false ? '' : `
      <div style="font-size:11px;color:var(--text-faint);line-height:1.55;
                  padding:8px 12px;border:1px solid var(--border);border-radius:4px">
        This goes straight from your browser to this machine. It is encrypted
        with a key held in your OS keychain and stored outside the repo, so it
        is never committed and never reaches the agent's transcript.
      </div>`;
    return `<div data-sf-root="${p}" style="display:contents">${intro}

      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">${step}Type</label>
        <div id="${p}-type-row" role="radiogroup" aria-label="Credential type"
             style="display:flex;gap:6px;flex-wrap:wrap">${typeChips}</div>
        <div id="${p}-type-help" hidden style="font-size:10px;color:var(--text-faint);margin-top:3px">
          Set by the engine this credential belongs to.
        </div>
      </div>

      <div>
        <label for="${p}-name" style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">${step}Name</label>
        <input type="text" id="${p}-name" value="${esc(name)}" ${lockName ? 'readonly' : ''}
          placeholder="reddit.password" autocomplete="off" spellcheck="false"
          style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);
                 border:1px solid var(--border);border-radius:4px;color:var(--text);
                 font-family:var(--mono);${lockName ? 'opacity:0.6' : ''}">
        <div id="${p}-name-help" style="font-size:10px;color:var(--text-faint);margin-top:3px">
          Lowercase letters, digits, <code>.</code> <code>-</code> <code>_</code> only
          &mdash; no spaces or capitals, up to 64 characters, starting with a letter or digit.
          Referenced in tasks as <code>{{secret:reddit.password}}</code>.
        </div>
        <div id="${p}-name-fix" hidden style="font-size:11px;margin-top:5px;line-height:1.5;
             color:var(--danger,#c0553f)"></div>
      </div>

      <div id="${p}-user-block">
        <label id="${p}-user-label" for="${p}-user" style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">
          ${step}<span id="${p}-user-name">Username</span> <span id="${p}-user-opt" style="opacity:.7">— optional</span>
        </label>
        <input type="text" id="${p}-user" value="${esc((existing && existing.username) || '')}"
          placeholder="ron@example.com" autocomplete="off" spellcheck="false"
          style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);
                 border:1px solid var(--border);border-radius:4px;color:var(--text);
                 font-family:var(--mono)">
        <div id="${p}-user-help" style="font-size:10px;color:var(--text-faint);margin-top:3px">
          The other half of a login. Referenced as <code>{{user:${esc(name || 'name')}}}</code>,
          and shown in the list so two accounts on the same site stay apart.
          Not encrypted — it is an identifier, not a credential.
        </div>
      </div>

      <div>
        <label for="${p}-value" style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">
          ${step}<span id="${p}-value-name">Value</span> ${isNew ? '' : '<span style="opacity:.7">— leave blank to keep the current one</span>'}
        </label>
        <div style="display:flex;gap:6px">
          <input type="password" id="${p}-value" autocomplete="off" spellcheck="false"
            placeholder="${isNew ? 'paste from your password manager' : 'unchanged'}"
            style="flex:1;min-width:0;padding:6px 10px;font-size:13px;background:var(--surface2);
                   border:1px solid var(--border);border-radius:4px;color:var(--text);
                   font-family:var(--mono)">
          <button type="button" class="btn-header-action" style="padding:4px 10px;font-size:11px"
                  ${H ? `onclick="${H.reveal}"` : ''} id="${p}-reveal">Show</button>
        </div>
        <div style="font-size:10px;color:var(--text-faint);margin-top:3px">
          Once saved it cannot be displayed again — there is no route that hands
          a value back. Rotate it here if you lose it.
        </div>
        <div id="${p}-preset-hint" hidden style="font-size:10px;color:var(--text-faint);margin-top:5px;line-height:1.5"></div>
        <div id="${p}-2fa-help" style="font-size:10px;color:var(--text-faint);margin-top:5px;line-height:1.5">
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
        <label for="${p}-desc" style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">${step}What it's for</label>
        <input type="text" id="${p}-desc" value="${esc((existing && existing.description) || '')}"
          placeholder="Reddit account used for launch posts" autocomplete="off"
          style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);
                 border:1px solid var(--border);border-radius:4px;color:var(--text)">
      </div>
${scopeRow}
      <div>
        <label style="display:flex;align-items:flex-start;gap:8px;font-size:12px;cursor:pointer">
          <input type="checkbox" id="${p}-unattended" ${allowUnattended ? 'checked' : ''} style="margin-top:2px">
          <span>
            ${step}Usable by unattended runs
            <div style="font-size:10px;color:var(--text-faint);margin-top:2px">
              Uncheck for anything you don't want the steward or a scheduled job
              touching while you're away.
            </div>
          </span>
        </label>
      </div></div>`;
  }

  // Redraw the open form from `state` = {type, preset, isNew}. A preset (a Desk
  // generation engine's credential spec) keeps the engine's own wording over the
  // type's. Safe to call repeatedly: it always writes every labelled node, so
  // nothing from a previous type or preset sticks.
  function render(p, state) {
    const preset = state.preset;
    const T = TYPES[state.type] || TYPES.login;
    const hasUser = !!T.user;
    const userBlock = $(p, 'user-block');
    if (userBlock) userBlock.hidden = !hasUser;
    const set = (field, text) => { const el = $(p, field); if (el) el.textContent = text; };
    const uLabel = (preset && preset.username_label) || (T.user && T.user.label) || 'Username';
    const uRequired = preset && preset.username_label ? !!preset.username_required : !!(T.user && T.user.required);
    set('user-name', uLabel);
    set('user-opt', uRequired ? ' — required' : ' — optional');
    const user = $(p, 'user');
    if (user) user.placeholder = preset && preset.username_label ? `paste the ${preset.username_label}` : (T.user ? T.user.placeholder : '');
    const userHelp = $(p, 'user-help');
    if (userHelp) {
      userHelp.hidden = !!preset || !hasUser;
      userHelp.innerHTML = hasUser ? T.user.help.replace('NAME', esc((($(p, 'name') && $(p, 'name').value) || '').trim() || 'name')) : '';
    }
    set('value-name', preset ? preset.secret_label : T.value.label);
    const value = $(p, 'value');
    if (value) value.placeholder = !state.isNew ? 'unchanged'
      : (preset ? `paste the ${preset.secret_label}` : T.value.placeholder);
    const twoFa = $(p, '2fa-help');
    if (twoFa) twoFa.hidden = !!preset || !T.twofa || !!state.noTwoFa;   // noTwoFa: a host that cannot import an otpauth seed
    const nameEl = $(p, 'name');
    if (nameEl) nameEl.placeholder = T.namePh;
    const desc = $(p, 'desc');
    if (desc) desc.placeholder = preset ? 'Desk generation engine (renders for the Studio)' : T.descPh;
    // The type chips: the chosen one is checked; a preset locks the whole group.
    document.querySelectorAll(`input[name="${p}-type"]`).forEach((r) => {
      r.checked = r.value === state.type;
      r.disabled = !!preset;
      const chip = r.closest('label');
      if (chip) {
        chip.style.borderColor = r.checked ? 'var(--accent)' : 'var(--border)';
        chip.style.color = r.checked ? 'var(--accent)' : 'var(--text)';
        chip.style.opacity = r.disabled && !r.checked ? '0.4' : '1';
        chip.style.cursor = r.disabled ? 'default' : 'pointer';
      }
    });
    const typeHelp = $(p, 'type-help');
    if (typeHelp) typeHelp.hidden = !preset;
    const hint = $(p, 'preset-hint');
    if (hint) {
      hint.hidden = !preset;
      hint.textContent = '';
      if (preset) {
        hint.append(preset.hint || '');
        if (preset.url) {
          const a = document.createElement('a');
          a.href = preset.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
          a.textContent = preset.url.replace(/^https?:\/\//, '');
          hint.append(' ', a);
        }
      }
    }
    const root = document.querySelector(`[data-sf-root="${p}"]`);
    if (root) {
      let n = 0;
      root.querySelectorAll('.sec-step').forEach((el) => {
        if (el.closest(`#${p}-user-block`) && !hasUser) return;
        el.textContent = `${++n}. `;
      });
    }
  }

  // An engine preset locks its type; with none, the human's pick stands.
  // Dropping the preset (the name typed no longer matches an engine) gives back
  // the type the human had picked before it locked one.
  function applyPreset(p, state, preset) {
    if (preset && !state.preset) state.typeBefore = state.type;
    if (!preset && state.preset) state.type = state.typeBefore;
    state.preset = preset || null;
    if (preset && TYPES[preset.entry_type]) state.type = preset.entry_type;
    render(p, state);
  }

  // A type chip was picked. Fields the new type does not have are cleared, so a
  // username typed under Login cannot ride along into an API key save.
  function setType(p, state, type) {
    if (state.preset || !TYPES[type] || type === state.type) { render(p, state); return; }
    state.type = type;
    if (!TYPES[type].user) { const u = $(p, 'user'); if (u) u.value = ''; }
    render(p, state);
  }

  function toggleReveal(p) {
    const input = $(p, 'value');
    const btn = $(p, 'reveal');
    if (!input || !btn) return;
    const hidden = input.type === 'password';
    input.type = hidden ? 'text' : 'password';
    btn.textContent = hidden ? 'Hide' : 'Show';
  }

  function scopeChanged(p) {
    const sel = $(p, 'project');
    const isProject = document.querySelector(`input[name="${p}-scope"]:checked`)?.value === 'project';
    if (sel) sel.style.display = isProject ? '' : 'none';
  }

  function useSlug(p, s) {
    const el = $(p, 'name');
    if (!el) return;
    el.value = s;
    el.dispatchEvent(new Event('input', { bubbles: true }));   // re-run preset lookup + live check
    el.focus();
  }

  // Shows the rule violation (and the lowercase suggestion) under the field. Returns
  // the problem text, '' when the name is fine or the field is not editable.
  function checkName(p) {
    const el = $(p, 'name');
    const fix = $(p, 'name-fix');
    if (!el || !fix) return '';
    const name = el.value.trim();
    const problem = (el.readOnly || !name) ? '' : nameProblem(name);
    el.style.borderColor = problem ? 'var(--danger,#c0553f)' : '';
    if (!problem) { fix.hidden = true; fix.textContent = ''; return ''; }
    const s = slug(name);
    fix.hidden = false;
    fix.textContent = problem + ' ';
    if (s && s !== name && NAME_RE.test(s)) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn-header-action';
      b.style.cssText = 'padding:2px 8px;font-size:11px;margin-left:2px;font-family:var(--mono)';
      b.textContent = 'Use ' + s;
      b.onclick = () => useSlug(p, s);
      fix.appendChild(b);
    }
    return problem;
  }

  // Bind a host that does not use inline handlers: type chips, Show/Hide, live name check.
  function bind(p, state, on) {
    const root = document.querySelector(`[data-sf-root="${p}"]`);
    if (!root) return;
    root.querySelectorAll(`input[name="${p}-type"]`).forEach((r) => r.addEventListener('change', () => {
      setType(p, state, r.value);
      if (on && on.typeChanged) on.typeChanged();
    }));
    const reveal = $(p, 'reveal');
    if (reveal) reveal.addEventListener('click', () => toggleReveal(p));
    const name = $(p, 'name');
    if (name) {
      name.addEventListener('input', () => { checkName(p); render(p, state); if (on && on.nameChanged) on.nameChanged(); });
    }
  }

  // What the form holds, or {error, field}. `p` form, `state` its type state. The
  // password is read from the input and handed to the caller; nothing is kept here.
  function read(p, state) {
    const T = TYPES[state.type] || TYPES.login;
    const name = (($(p, 'name') || {}).value || '').trim();
    const value = ($(p, 'value') || {}).value || '';
    if (!name) return { error: 'Give the credential a name.', field: 'name' };
    const problem = checkName(p);
    if (problem) return { error: problem, field: 'name' };
    if (!value) return { error: `Paste the ${(T.value.label).toLowerCase()}.`, field: 'value' };
    const username = !T.user ? '' : ((($(p, 'user') || {}).value) || '').trim();
    if (T.user && T.user.required && !username) return { error: `Enter the ${T.user.label}.`, field: 'user' };
    return {
      name, value, username, entry_type: state.type,
      description: (($(p, 'desc') || {}).value || '').trim(),
      allow_unattended: !!($(p, 'unattended') || {}).checked,
    };
  }

  // What the form holds MINUS the secret, for a review screen: {name, entry_type,
  // username, description, allow_unattended, hasValue}. Never returns the value.
  function meta(p, state) {
    const T = TYPES[state.type] || TYPES.login;
    return {
      name: (($(p, 'name') || {}).value || '').trim(), entry_type: state.type,
      username: !T.user ? '' : ((($(p, 'user') || {}).value) || '').trim(),
      description: (($(p, 'desc') || {}).value || '').trim(),
      allow_unattended: !!($(p, 'unattended') || {}).checked,
      hasValue: !!(($(p, 'value') || {}).value),
    };
  }

  // Empty the secret input (and put it back to hidden) the moment it is not needed.
  function clear(p) {
    const v = $(p, 'value');
    if (v) { v.value = ''; v.type = 'password'; }
    const b = $(p, 'reveal');
    if (b) b.textContent = 'Show';
  }

  window.SecretForm = { TYPES, TYPE_ORDER, NAME_RE, slug, nameProblem, fieldsHtml, render, applyPreset, setType,
    toggleReveal, scopeChanged, useSlug, checkName, bind, read, meta, clear };
})();
