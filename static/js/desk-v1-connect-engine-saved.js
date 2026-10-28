// Names-only engine state. Unchanged Save retains the existing connection; a
// changed field still goes through the provider's Review/replacement/passcode.
export function savedEngineConnector(connector, entries) {
  const name=connector.fields.find(f=>f.present && f.vault)?.vault;
  const saved=entries.find(e=>e.name===name);
  return {...connector,fields:connector.fields.map(f=>f.key==='allow_unattended' && typeof saved?.allow_unattended==='boolean'
    ? {...f,default:saved.allow_unattended}:f)};
}
export function restoreEngine(info, selection, connector, engine) {
  return {info, sel:selection, connector, method:selection.method,
    loaded:`${info.service.id}:${selection.method}`, url:info.url || info.input,
    stage:'fields', savedEngine:engine};
}
export function engineNote(state) {
  return state.savedEngine ? `<p data-engine-saved>${window.DeskV1ConnectCopy.words.currentConnection}</p>` : '';
}
export function unchangedEngine(state) {
  if (!state.savedEngine) return null;
  const a = window.DeskV1ConnectAdapter;
  const read = a?.mounted() ? a.read() : {fields:{}};
  if (read.error || Object.entries(read.fields).some(([k,v])=> {
    if (k === 'confirm_replace') return false;
    const f = state.connector.fields.find(f=>f.key===k);
    return f?.kind !== 'checkbox' || v !== !!f.default;
  })) return null;
  return {ok:true,result:{ok:true,service:state.info.service.id,method:state.method,account_id:null,
    unchanged:true,status:{state:state.savedEngine.connected?.ready ? 'verified':state.savedEngine.connected?.state || 'not_checked'}}};
}
