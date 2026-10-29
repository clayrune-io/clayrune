// Rehydrate the saved public setup, never an approval. The existing Review builds
// a new card and Save still requires both its approval and the passcode.
export function packageState(d) {
  return {pkg:d.ecosystem === 'github' ? `${d.package.replace(/\/tree\/[^/]+\/?$/,'').replace(/\.git\/?$/,'').replace(/\/$/,'')}/tree/${d.version}` : `${d.package}@${d.version}`,
    entry:d.entry || '', args:(d.args || []).join('\n'), name:d.server_name, command:d.command || '',
    view:'details', savedGithub:d.ecosystem === 'github', savedPin:d.version};
}
export async function stageSavedGithub(api, state) {
  const staged=await window.DeskV1ConnectGithub.stage(api,state.pkg);
  if(staged.sha!==state.savedPin) throw new Error('The repository version changed. Return to Setup and review it again.');
  return staged;
}
export function remoteState(d) {
  return {url:d.url, protocol:d.protocol, name:d.server_name, auth:d.auth,
    issuer:d.issuer || '', scopes:(d.scopes || []).join(' '), creds:d.credentials.map(c=>({header:c.header,prefix:c.prefix ?? '',vault:c.vault}))};
}
