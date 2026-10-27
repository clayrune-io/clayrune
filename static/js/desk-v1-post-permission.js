// Desk v1 — the missing-Post-consent notice and its link to the Permissions step (MC-1114).
// The server says it two ways: an account row carries `post_permission` while the account is
// connected but Post is not ticked, and a version the publisher refused for want of it carries
// `failure.code === 'post_permission'`. Either way the fix is the same screen: the account's
// Permissions step, which the saved account's "Change how this is connected" opens. This module
// only points there; ticking Post stays the person's act on that step.
(function () {
  const CODE = 'post_permission';
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // The Connections row for the account, with its Permissions one click away.
  function noticeHTML(ch) {
    if (!ch || !ch.post_permission || ch.post_permission.code !== CODE) return '';
    return `<div class="desk-v1-rules-hint" data-post-permission="${esc(ch.id)}">Agents cannot post through this account yet: Post is not ticked.
      <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-pp-open="${esc(ch.id)}">Open Permissions</button></div>`;
  }
  function bindAccount(row, ch, repaint) {
    const b = row.querySelector('[data-pp-open]');
    const CS = window.DeskV1ConnectionStatus;
    if (b && CS && CS.reopen) b.addEventListener('click', () => CS.reopen({ kind: 'account', id: ch.id, record: ch }, repaint));
  }

  // A version the publisher refused for want of Post: a button to the account, in place of "Approving again retries it".
  function failed(version) { return !!(version && version.failure && version.failure.code === CODE); }
  function linkHTML(version) {
    return failed(version)
      ? ` <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-pp-goto="${esc(version.channelId)}">Open this account’s Permissions</button>` : '';
  }
  function bindLink(root) {
    const b = root && root.querySelector('[data-pp-goto]');
    if (b) b.addEventListener('click', () => window.deskV1Nav && window.deskV1Nav('connections', { account: b.getAttribute('data-pp-goto') }));
  }

  window.DeskV1PostPermission = { CODE, noticeHTML, bindAccount, failed, linkHTML, bindLink };
})();
