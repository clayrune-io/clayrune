// Where's missing-account action enters the existing human-only attach flow.
import { reopenSaved } from './desk-v1-connect-saved-reopen.js';

window.DeskV1ConnectSiteHandoff = async function (el, id, repaint) {
  const key = 'service:' + id;
  const current = () => el.isConnected && !!el.querySelector('[data-connections]') && window.DeskV1ConnTiles.selected() === key;
  try {
    // Connections may still be loading on a direct entry from Where.
    await window.DeskV1Services.load(true);
    if (!current()) return;
    if (window.DeskV1Services.loadError()) throw new Error(window.DeskV1Services.loadError());
    const record = window.DeskV1Services.byId(id);
    if (!record?.browser_connection) throw new Error('This saved connection is no longer available.');
    await reopenSaved({ kind: 'service', id: key, record }, repaint, current);
  } catch (e) {
    if (current()) window.DeskV1Kit.toast(e.message || 'The saved connection could not be opened.');
  }
};
