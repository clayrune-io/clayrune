// Missing Post consent links back to the shared Permissions editor.
import { ConnectionWords as W } from './desk-v1-connection-words.js';
(function () {
  const CODE = 'post_permission';
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // The Connections row's notice. It carries no button of its own: the account's one Edit is the way in.
  function noticeHTML(ch) {
    if (!ch || !ch.post_permission || ch.post_permission.code !== CODE) return '';
    return `<div class="desk-v1-rules-hint" data-post-permission="${esc(ch.id)}">${W.reasons.postOff}</div>`;
  }

  // A version the publisher refused for want of Post: a button to the account, in place of "Approving again retries it".
  function failed(version) { return !!(version && version.failure && version.failure.code === CODE); }
  function linkHTML(version) {
    return failed(version)
      ? ` <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-pp-goto="${esc(version.channelId)}">${W.buttons.edit}</button>` : '';
  }
  function bindLink(root) {
    const b = root && root.querySelector('[data-pp-goto]');
    if (b) b.addEventListener('click', () => window.deskV1Nav && window.deskV1Nav('connections', { account: b.getAttribute('data-pp-goto') }));
  }

  window.DeskV1PostPermission = { CODE, noticeHTML, failed, linkHTML, bindLink };
})();
