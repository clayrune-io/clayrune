// Desk v1 — Studio's Recent list: delete a row, with Undo.
// Window-bridged module, no `import` (ground rule 1). desk-v1-studio.js owns the
// list and hands this module a `ctx` of accessors; everything about deleting a row
// (who may be deleted, the bin button, the local + server removal, the Undo) lives
// here so the Studio file does not grow.
//
// A row is one of six things (`row.src`):
//   item     what Studio made this session (a draft, or a saved image/video)
//   board    a draft the server holds a storyboard for (live)
//   file     a library file (live)
//   article  a standalone article draft (desk-v1-studio-article.js)
//   family   a campaign piece that is rendering (never deletable: attached)
//   fixture  a demo row
// Delete is IMMEDIATE (no confirm) and goes through `DeskV1Store.write`, so it is a
// destructive command on the Desk's command bus: the header Undo button and
// Ctrl/Cmd+Z bring it back. Demo mode never calls the server. Live, the server
// moves the draft (and the pictures only it uses) into a trash entry and answers a
// token; Undo posts that token and everything returns exactly as it was
// (mc/desk_studio_items.py).
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // What the server says is in use (live only; GET /api/desk/studio/usage). Empty
  // until it lands: the server refuses a delete it should not allow, so a late or
  // failed read only means the control is not pre-disabled.
  let _usage = { rendering: [], files: {} };

  function loadUsage() {
    const store = window.DeskV1Store;
    if (!store || !store.live()) return Promise.resolve();
    return store.api('GET', '/api/desk/studio/usage').then((u) => {
      _usage = { rendering: (u && u.rendering) || [], files: (u && u.files) || {} };
    }).catch((e) => { console.warn('[desk] could not read what Studio items are in use:', e && e.message ? e.message : e); });
  }

  // Why a row cannot be deleted right now, or '' when it can.
  function blockedReason(row, ctx) {
    if (row.state === 'rendering' || _usage.rendering.includes(row.id)) return 'Rendering, wait for it to finish';
    if (row.campaignId) return `Attached to ${ctx.campaignTitle(row.campaignId)}, detach it there first`;
    if (!ctx.isLive()) return '';
    if (row.src === 'item' && row.state === 'saved' && !row.path) return 'Saving, wait a moment';
    const use = row.path ? _usage.files[row.path] : null;
    if (use && use.attached) return `Attached to ${use.attached.campaign_title}, detach it there first`;
    if (use && use.draft) return `Used in the storyboard "${use.draft.title || 'New video'}", delete that draft instead`;
    if (use && use.piece_board) return 'Used in a campaign storyboard, detach it there first';
    return '';
  }

  const BIN_SVG = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14M10 10v7M14 10v7"/></svg>';

  // The bin, a SIBLING of the row's open button (a button inside a button is
  // invalid HTML). `aria-disabled`, not `disabled`, so a keyboard or screen-reader
  // user can still land on it and hear why it does nothing.
  function buttonHTML(row, ctx) {
    const why = blockedReason(row, ctx);
    const label = `Delete ${row.title} · ${row.kind}`;
    return `<button type="button" class="desk-v1-studio-recent-del" data-studio-recent-del="${esc(row.id)}" aria-label="${esc(label)}"` +
      (why ? ` aria-disabled="true" data-blocked="${esc(why)}" title="${esc(why)}"` : ' title="Delete"') + `>${BIN_SVG}</button>`;
  }

  // ── Removing a row locally, remembering how to put it back ────────────────
  function _take(arr, pred) {
    const i = arr.findIndex(pred);
    if (i < 0) return null;
    return { arr, i, entry: arr.splice(i, 1)[0] };
  }
  function _localRemove(row, ctx) {
    const taken = [];
    const lib = ctx.lib();
    const push = (t) => { if (t) taken.push(t); };
    if (row.src === 'item' || row.src === 'board' || row.src === 'fixture') {
      push(_take(ctx.items(), (it) => it.id === row.id));
      push(_take(lib.boards, (b) => b.id === row.id));
      push(_take(ctx.fixtureRecent(), (r) => r.id === row.id));
    }
    if (row.src === 'article') push(_take(ctx.articles(), (a) => a.id === row.id));
    if (row.path) {
      push(_take(lib.recent, (r) => r.path === row.path));
      push(_take(ctx.items(), (it) => it.path === row.path));
    }
    // Put back newest-first-removed-last so each index is valid again.
    return () => { for (let k = taken.length - 1; k >= 0; k--) taken[k].arr.splice(taken[k].i, 0, taken[k].entry); };
  }

  // Which server call a live row needs: a draft (storyboard) or a library file.
  function _serverDelete(row) {
    const store = window.DeskV1Store;
    if (row.src === 'article') {
      // A draft the page never saved has nothing on the server: gone is gone.
      return store.api('DELETE', `/api/desk/studio/articles/${encodeURIComponent(row.id)}`)
        .then((out) => Object.assign({}, out, { article: true }))
        .catch((e) => { if (e && e.status === 404) return { token: null }; throw e; });
    }
    const isDraft = row.src === 'board' || (row.src === 'item' && row.kind === 'video' && row.state === 'draft');
    const call = isDraft
      ? store.api('DELETE', `/api/desk/studio/${encodeURIComponent(row.id)}`)
      : store.api('DELETE', `/api/desk/studio/files?path=${encodeURIComponent(row.path)}`);
    // A draft the page never saved has nothing on the server: gone is gone.
    return call.catch((e) => { if (isDraft && e && e.status === 404) return { token: null }; throw e; });
  }

  function _focusAfter(ctx, index) {
    const el = ctx.el();
    if (!el) return;
    const opens = el.querySelectorAll('[data-studio-recent-row]');
    const next = opens[Math.min(index, opens.length - 1)] || el.querySelector('[data-studio-new="video"]');
    if (next) next.focus({ preventScroll: true });
  }

  function deleteRow(row, ctx, index) {
    const why = blockedReason(row, ctx);
    if (why) { window.DeskV1Kit.toast(why); return Promise.resolve({ ok: false, error: why }); }
    let restore = () => {};
    const live = ctx.isLive();
    return window.DeskV1Store.write({
      label: `Deleted “${row.title}”`,
      destructive: true,
      apply: () => { restore = _localRemove(row, ctx); ctx.repaint(); },
      unapply: () => { restore(); },
      repaint: () => ctx.repaint(),
      request: async () => {
        const out = live ? await _serverDelete(row) : null;
        if (live) await ctx.reload();
        return out;
      },
      undoRequest: async (out) => {
        if (!live || !out || !out.token) return;
        const base = out.article ? '/api/desk/studio/articles/trash' : '/api/desk/studio/trash';
        await window.DeskV1Store.api('POST', `${base}/${out.token}/restore`);
        await ctx.reload();
      },
    }).then((res) => { ctx.repaint(); _focusAfter(ctx, index); return res; });
  }

  // Wire every bin in `el`. `rows` is what the list was painted from, in order.
  function wire(el, rows, ctx) {
    el.querySelectorAll('[data-studio-recent-del]').forEach((btn) => {
      btn.onclick = (ev) => {
        ev.stopPropagation();
        const id = btn.dataset.studioRecentDel;
        const index = rows.findIndex((r) => r.id === id);
        if (index < 0) return;
        deleteRow(rows[index], ctx, index);
      };
    });
  }

  window.DeskV1StudioDelete = { loadUsage, blockedReason, buttonHTML, wire, deleteRow };
})();
