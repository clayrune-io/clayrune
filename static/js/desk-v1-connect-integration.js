// The only production activation of Connect. Each screen owns its own module.
import { ConnectCopy } from './desk-v1-connect-copy.js';
import { reopenAccount } from './desk-v1-connect-account-reopen.js';
ConnectCopy.enable();
const W = window.DeskV1ConnectWizard;
W?.setEnabled(true);
window.DeskV1ConnectionStatus?.setReopen(async (target, repaint) => {
  try { if (await reopenAccount(target, repaint)) return; }
  catch (e) { window.DeskV1Kit.toast(e.message || 'The saved connection could not be opened.'); return; }
  W.close();
  window.DeskV1ConnTiles.select('add');
  repaint();
});
