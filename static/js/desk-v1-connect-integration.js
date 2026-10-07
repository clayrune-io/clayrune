// The only production activation of Connect. Each screen owns its own module.
import { ConnectCopy } from './desk-v1-connect-copy.js';
ConnectCopy.enable();
const W = window.DeskV1ConnectWizard;
W?.setEnabled(true);
window.DeskV1ConnectionStatus?.setReopen((_target, repaint) => {
  W.close();
  window.DeskV1ConnTiles.select('add');
  repaint();
});
