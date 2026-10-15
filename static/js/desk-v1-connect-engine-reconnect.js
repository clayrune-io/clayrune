// A removed engine can reuse its shared login in the same Add service flow.
const ENGINES = {'google_ai:api_key':'google', 'openai:api_key':'openai',
  'higgsfield:api_key':'higgsfield', 'higgsfield:oauth':'higgsfield_mcp'};

function target(api, method) {
  const id = ENGINES[`${api.info?.service?.id}:${method}`];
  return (api.ctx.engines || []).find(e => e.id === id && e.connected?.state === 'disconnected') || null;
}

async function save(engine, service, method) {
  const response = await window.humanProofFetch(`/api/desk/engines/${encodeURIComponent(engine.id)}/connection`,
    {method:'PUT',body:'{}'}, {title:'Save service',description:'Re-enter your dashboard passcode to reconnect this service using its saved login.'});
  if (response === null) return {cancelled:true};
  if (!response.ok) return {ok:false,error:response.body?.message || response.body?.error || 'The connection could not be saved.'};
  const c = response.body.connection;
  return {ok:true,result:{ok:true,service,method,account_id:null,stored:[],
    status:{state:c?.ready ? (engine.auth?.kind === 'oauth' ? 'signed_in':'key_stored'):'not_connected', label:c?.reason || 'Saved; not checked'}}};
}

export const EngineReconnect = {target,save,
  sentence:'Use the saved login to reconnect this service when you save; your login stays in the Vault.'};
