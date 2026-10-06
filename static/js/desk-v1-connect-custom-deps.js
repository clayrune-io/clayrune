// Desk v1 — Connections: the dependency and install-script part of the approval card of "Add your own
// MCP server" (docs/DESK_SERVICE_PROFILES_SPEC.md section 6.2; slice U2b). Rendered inside the card of
// static/js/desk-v1-connect-custom.js, which owns the Review and Save calls. Window-bridged module, no
// `import` (ground rule 1). Server side: mc/desk_connect/custom_npm_card.py.
//
// It draws what the server resolved, nothing of its own: every dependency with its exact name, version,
// sha512 and place, every install script with its exact body, and a checkbox per script that is OFF
// until ticked. Ticking one is a new Review (the server rebuilds the card with that script approved, a
// new fingerprint, and the approval box clears); this module only reports the ticked ids through
// `onTick`. A script whose body cannot be shown exactly has no checkbox: it cannot be approved.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function _size(bytes) { return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round((bytes || 0) / 1024))} KB`; }

  function _depRowHTML(d) {
    return `
            <li data-cu-dep="${esc(d.path)}">
              <div><strong>${esc(d.name)}</strong> <code>${esc(d.version)}</code>${d.deprecated ? ' <span class="desk-v1-cu-dep-flag" data-cu-dep-deprecated>deprecated</span>' : ''}</div>
              <div class="desk-v1-cu-dep-line">Digest <code data-cu-dep-digest>${esc(d.integrity)}</code></div>
              <div class="desk-v1-cu-dep-line">Placed at <code>${esc(d.path)}</code>; ${esc(d.licence || 'licence not stated')}; ${esc(_size(d.size_bytes))} download, ${esc(_size(d.unpacked_bytes))} unpacked</div>
            </li>`;
  }

  function _depSummary(c) {
    const t = c.dependency_totals || {};
    return `${t.count} dependency package${t.count === 1 ? '' : 's'} installed with it, ${_size(t.download_bytes)} download, ${_size(t.unpacked_bytes)} unpacked`;
  }

  function _depsHTML(c) {
    return `
          <details class="desk-v1-cu-deps" data-cu-deps open>
            <summary>${esc(_depSummary(c))}</summary>
            <div class="desk-v1-rules-hint">Each is installed at exactly this version and only if its archive has exactly this digest. A newer version, another registry or another file with the same version is never used.</div>
            <ul class="desk-v1-cu-dep-list">${c.dependencies.map(_depRowHTML).join('')}</ul>
          </details>`;
  }

  function _scriptHTML(s) {
    const label = `${s.package} ${s.version}: ${s.script}`;
    const where = s.path ? `in <code>${esc(s.path)}</code>` : 'in the package itself';
    if (!s.approvable) {
      return `
            <li data-cu-script="${esc(s.id)}" data-cu-script-approvable="false">
              <div><strong>${esc(label)}</strong> ${where}. Not run, and it cannot be approved: ${esc(s.reason)}.</div>
              <pre class="desk-v1-cu-script-body" data-cu-script-body>${esc(s.body_escaped || '')}</pre>
            </li>`;
    }
    return `
            <li data-cu-script="${esc(s.id)}" data-cu-script-approvable="true">
              <label class="desk-v1-cf-check"><input type="checkbox" data-cu-script-tick="${esc(s.id)}" ${s.approved ? 'checked' : ''}>
                <span><strong>${esc(label)}</strong> ${where}. ${s.approved ? 'Runs once at Save.' : 'Off: it is not run.'}</span></label>
              <pre class="desk-v1-cu-script-body" data-cu-script-body>${esc(s.body)}</pre>
            </li>`;
  }

  function _scriptsHTML(c) {
    if (!(c.scripts || []).length) return '';
    return `
          <div class="desk-v1-cu-scripts" data-cu-scripts>
            <div class="desk-v1-rules-group-title">Install scripts (off unless you tick them)</div>
            <div class="desk-v1-rules-hint" data-cu-scripts-note>${esc(c.scripts_note)}</div>
            <ul class="desk-v1-cu-script-list">${c.scripts.map(_scriptHTML).join('')}</ul>
          </div>`;
  }

  function _notRunHTML(c) {
    const skipped = (c.optional_not_installed || []).map((s) => `<li data-cu-optional-skipped><code>${esc(s.package)}</code> lists optional packages that are not installed: ${esc((s.optional || []).join(', '))}</li>`);
    const native = (c.native_build_not_run || []).map((p) => `<li data-cu-native-skipped><code>${esc(p || 'the package itself')}</code> has a native build step that is not run</li>`);
    if (!skipped.length && !native.length) return '';
    return `<ul class="desk-v1-cu-changes" data-cu-not-installed>${skipped.concat(native).join('')}</ul>`;
  }

  // The part of the card between the facts and the risks; '' for a package that needs nothing.
  function html(c) {
    if (!c || (!(c.dependencies || []).length && !(c.scripts || []).length && !(c.optional_not_installed || []).length
      && !(c.native_build_not_run || []).length)) return '';
    return `<div class="desk-v1-cu-dependencies" data-cu-dependencies>
          ${(c.dependencies || []).length ? _depsHTML(c) : ''}
          ${_scriptsHTML(c)}
          ${_notRunHTML(c)}
        </div>`;
  }

  // onTick(ids): the ids of every script ticked after this change.
  function bind(root, onTick) {
    const boxes = root.querySelectorAll('[data-cu-script-tick]');
    boxes.forEach((b) => b.addEventListener('change', () => {
      onTick(Array.from(boxes).filter((x) => x.checked).map((x) => x.dataset.cuScriptTick));
    }));
  }

  // `parts`: the same rows without the <details> around them, for the Connect wizard's package step
  // (static/js/desk-v1-connect-package-step.js), which paginates them inside its own one Details.
  window.DeskV1ConnectCustomDeps = { html, bind, parts: { dep: _depRowHTML, depSummary: _depSummary, script: _scriptHTML, notRun: _notRunHTML } };
})();
