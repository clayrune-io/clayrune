// The service/route data, never a service-name exception, determines Read availability.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {readUnavailable} from '../../static/js/desk-v1-connect-read-support.js';

const cases=JSON.parse(execFileSync(process.env.MC_PYTHON || 'python',['-c',`
import json
from mc.desk_connect import registry, route_readiness, type_view
out=[]
for profile in registry.registry()['snapshot']['profiles']:
 for kind in profile['account_kinds']:
  supported=any(route_readiness.executor(profile['service_id'],r['id'],'read_own') and
   r['transport']!='mcp' and any(c['purpose']=='read_own' and c['account_kind']==kind['id'] and
   c['status'] in route_readiness.COVERED for c in r['coverage']) for r in profile['routes'])
  out.append({'info':type_view.project(profile),'kind':kind['id'],'supported':bool(supported)})
print(json.dumps(out))
`],{encoding:'utf8'}));
for(const {info,kind,supported} of cases) assert.equal(!readUnavailable(info,kind),supported,`${info.service.label}/${kind}`);
for (const kind of ['member', 'organization']) {
 const li = cases.find(c => c.info.service.id === 'linkedin' && c.kind === kind);
 assert.equal(readUnavailable(li.info, kind), '', `fresh LinkedIn ${kind} has a working pane route`);
}
const instagram = cases.find(c => c.info.service.id === 'instagram');
assert.equal(readUnavailable(instagram.info, instagram.kind), '', 'Instagram declares a pane read_own route (c2d9a245)');
// TikTok has no Read route in the registry (supported=false above); it carries the unavailable path.
const tiktok = cases.find(c => c.info.service.id === 'tiktok');
assert.equal(tiktok.supported, false, 'TikTok has no Read route; pick another unsupported service if this changes');
assert.match(readUnavailable(tiktok.info, tiktok.kind), /Reading isn't available for TikTok yet/);
assert.equal(readUnavailable(tiktok.info, tiktok.kind, true), '', 'a stale TikTok grant remains revocable');

const variant={route_id:'test-browser',transport:'browser',runtime:{purposes:{read_own:'pane'}},coverage:[{purpose:'read_own',account_kind:'account',status:'claimed'}]};
const info=v=>({service:{label:'A new service'},picker:[{variants:[v]}]});
assert.equal(readUnavailable(info(variant),'account'),'');
assert.match(readUnavailable(info({...variant,runtime:{purposes:{}}}),'account'),/Reading isn't available for A new service yet/);
assert.ok(readUnavailable(info({...variant,coverage:[]}),'account'));
assert.ok(readUnavailable(info({...variant,transport:'mcp'}),'account'));
assert.ok(readUnavailable(info(variant),'organization'));
assert.ok(readUnavailable(info({...variant,coverage:[{...variant.coverage[0],status:'unknown'}]}),'account'));
assert.ok(readUnavailable({host:'example.com',picker:[]},'account'));
console.log(`PASS read availability: ${cases.length} production service/kind pairs and missing coverage/executor, MCP, unknown and new-service cases`);
