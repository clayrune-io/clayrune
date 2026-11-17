// Desk v1 loading state (MC-1205). One small module, no imports, no dependency
// on the rest of the Desk, so it can be loaded at dashboard boot and painted
// before the Desk family itself has arrived (desk-load.js uses it for that).
//
// WHAT IT IS FOR. The Desk's home page used to be a header and one grey line,
// "Loading your workspace...", until the single workspace read finished
// (docs/_review, MC-1205). This paints the home page's own frame instead - column
// headings, the project block, the rows - as placeholder bars, so the page is
// visibly there and visibly loading, and a failed read shows its own error with
// a Try again inside that frame.
//
// It paints only from what is already in memory (no fetch), so it can run in
// the same frame as the click.
//
// `data-store-gate` + the "Loading your workspace..." sentence are kept on the
// region: the shell and the smokes look for them, and a screen reader needs the
// words, the bars are decoration.
(function () {
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  const COLUMNS = ['CAMPAIGN', 'STAGE', 'GOAL PROGRESS', 'PACE', 'NEXT POST', 'NEEDS YOU'];

  function _bar(w) { return `<span class="desk-v1-loading-bar" style="width:${w}"></span>`; }

  function _rowHTML(i) {
    const widths = ['72%', '58%', '66%'];
    return `<div class="desk-v1-loading-row" aria-hidden="true">
      ${_bar(widths[i % widths.length])}${_bar('60%')}${_bar('80%')}${_bar('44%')}${_bar('52%')}${_bar('36%')}
    </div>`;
  }

  // The home board frame with placeholder rows, for the "loading" kind.
  function boardLoadingHTML(message) {
    return `<div class="desk-v1-loading-board" data-store-gate="loading" role="status" aria-live="polite" aria-busy="true">
      <div class="desk-v1-loading-caption">${esc(message || 'Loading your workspace…')}</div>
      <div class="desk-v1-loading-head" aria-hidden="true">${COLUMNS.map((c) => `<div>${c}</div>`).join('')}</div>
      <div class="desk-v1-loading-block" aria-hidden="true">
        <div class="desk-v1-loading-block-head">${_bar('160px')}</div>
        ${_rowHTML(0)}${_rowHTML(1)}${_rowHTML(2)}
      </div>
    </div>`;
  }

  // The same frame with the failure in it. `data-store-retry` is what the shell
  // wires; it re-runs the whole load, there is nothing smaller to retry.
  function boardErrorHTML(message) {
    return `<div class="desk-v1-loading-board" data-store-gate="error" role="alert">
      <div class="desk-v1-loading-head" aria-hidden="true">${COLUMNS.map((c) => `<div>${c}</div>`).join('')}</div>
      <div class="desk-v1-loading-error">
        <div class="desk-v1-loading-error-msg">${esc(message)}</div>
        <button type="button" class="desk-v1-stub-link desk-v1-loading-retry" data-store-retry>Try again</button>
      </div>
    </div>`;
  }

  // Paint the home page's frame into `body` for a store gate ({kind, message}).
  // Returns false for a gate that is neither loading nor error, so the caller
  // keeps its own wording for those. The crumb's buttons come from the home
  // module (it owns them) when it has loaded; New campaign is held back until
  // the workspace is there, because a draft made against an empty store would
  // be thrown away by the load that follows.
  function paintHome(body, gate) {
    if (!body || !gate || (gate.kind !== 'loading' && gate.kind !== 'error')) return false;
    const region = gate.kind === 'error' ? boardErrorHTML(gate.message) : boardLoadingHTML(gate.message);
    body.innerHTML = `<div class="desk-v1-home desk-v1-loading-home">${region}</div>`;
    if (typeof window.deskV1HomeCrumbTools === 'function') {
      window.deskV1HomeCrumbTools();
      const host = document.getElementById('desk-v1-crumb-tools');
      const create = host && host.querySelector('.desk-v1-home-newcamp-page-btn');
      if (create) {
        create.disabled = true;
        create.title = 'Available once your workspace has loaded';
      }
    }
    return true;
  }

  window.DeskV1Loading = { boardLoadingHTML, boardErrorHTML, paintHome };
})();
