// Read-only saved setup handoff. A failed read stays on the selected card.
import { SiteAttach } from './desk-v1-connect-site-attach.js';
import { savedEngineConnector } from './desk-v1-connect-engine-saved.js';
const ENGINES = {google:['google_ai','https://aistudio.google.com','api_key'],openai:['openai','https://platform.openai.com','api_key'],
  higgsfield:['higgsfield','https://higgsfield.ai','api_key'],higgsfield_mcp:['higgsfield','https://higgsfield.ai','oauth']};
let seq = 0;
export async function reopenSaved(target, repaint, isCurrent = () => true) {
  if (!['service','engine','mcp'].includes(target.kind)) return false;
  const n=++seq, api=window.DeskV1Store.api, W=window.DeskV1ConnectWizard, r=target.record;
  let input, draft, connector, provider;
  if (target.kind === 'engine') {
    provider=ENGINES[r.id];
    if (!provider) throw new Error('This saved engine has no setup editor.');
    input=provider[1];
  } else if (target.kind === 'mcp') {
    draft=await api('POST','/api/desk/connect/custom/saved-draft',{scope:r.scope,project_id:r.project_id || null,server_name:r.server_name});
    input='https://example.com';
  } else input=r.link;
  const info=await api('POST','/api/desk/connect/types',{input});
  if (provider) {
    const inspect=await api('POST','/api/desk/connect/inspect',{input});
    connector=inspect.options?.find(o=>o.method===provider[2])?.connector;
    if (!connector) throw new Error('The saved setup could not be read.');
    if(connector.fields.some(f=>f.present && f.vault)) {
      const metadata=await api('GET','/api/secrets');
      connector=savedEngineConnector(connector,metadata.secrets || []);
    }
  }
  const selectedId=target.kind==='engine' ? `engine:${r.id}` : target.id;
  if (n!==seq || !isCurrent() || window.DeskV1ConnTiles.selected()!==selectedId) return true;
  if (provider) {
    const variant=info.picker?.flatMap(t=>t.variants).find(v=>v.setup?.via==='provider' && v.setup.method===provider[2]);
    if (!variant) throw new Error('The saved setup could not be found.');
    const sel={service:provider[0],type:variant.type,variant:variant.id,account:target.id,method:provider[2]};
    W.resume(info,sel);window.DeskV1ConnectApiStep.restore(info,sel,connector,r);
  } else if (draft) {
    const variant=draft.ecosystem==='remote'?'custom-remote':'custom-npm';
    W.resume(info,{service:info.service?.id,type:'mcp',variant,account:target.id});
    window.DeskV1ConnectPermissionsStep.restoreReach(draft);
    (draft.ecosystem==='remote'?window.DeskV1ConnectRemoteStep:window.DeskV1ConnectPackageStep).restore(draft);
  } else if (info.service?.makes_account) {
    W.resume(info,{service:info.service.id,type:'account',variant:'saved-site',account:target.id});SiteAttach.restore(r,info);
  } else {
    W.resume(info,{service:info.service?.id,type:'signin',variant:'browser-profile',account:target.id});
    window.DeskV1ConnectBrowserProfile.restore(r,info);
  }
  window.DeskV1ConnTiles.select('add');repaint();return true;
}
