// A saved account re-enters the one wizard with its own non-secret data.
let sequence = 0;
export async function reopenAccount(target, repaint) {
  if (target.kind !== 'account' || !target.record?.platform || target.record.platform === 'blog') return false;
  const ch = target.record, W = window.DeskV1ConnectWizard;
  const n = ++sequence;
  const info = await window.DeskV1Store.api('POST', '/api/desk/connect/types', { input:`https://${ch.platform}.com` });
  if (n !== sequence || window.DeskV1ConnTiles.selected() !== ch.id) return true;
  W.resume(info, { service:info.service?.id || ch.platform, type:'account', variant:'saved-account', account:ch.id });
  const t = { kind:ch.browser_setup?.account_kind || ch.account_kind || (ch.platform === 'linkedin' ? 'organization' : 'account'), account:{ id:ch.id }, identity:ch.identity };
  for (const name of ['Permissions', 'Summary']) window[`DeskV1Connect${name}Step`]?.setTarget(t);
  window.DeskV1ConnTiles.select('add'); repaint();
  return true;
}
