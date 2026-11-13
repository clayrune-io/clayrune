import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { Miniflare, Log, LogLevel } from 'miniflare';
import { retain } from '../pull-retention.js';
import { PullLedger } from '../pull-ledger.js';
import { SQL_NOW } from '../sql-clock.js';

const DAY = 86400000, MONTH = 30 * DAY, NOW = 1800000000000;
const I = '1'.repeat(32), H = 'a'.repeat(64), FLOW = '2'.repeat(32);
const id = (n) => n.toString(16).padStart(32,'0');
const hash = (n) => n.toString(16).padStart(64,'0');
const dir = new URL('../migrations/', import.meta.url);
const sources = [readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'),
  ...readdirSync(dir).filter((n) => /^\d+.*\.sql$/.test(n)).sort().map((n) => readFileSync(new URL(n,dir),'utf8'))];
class D1 {
  constructor() {
    this.sql = new DatabaseSync(':memory:'); this.sql.exec('PRAGMA foreign_keys=ON');
    for (const sql of sources) this.sql.exec(sql);
    this.queries = [];
  }
  prepare(sql) {
    const db = this.sql; this.queries.push(sql);
    return { sql, args: [], bind(...args) { assert.ok(args.length <= 100); this.args = args; return this; },
      async first() { return db.prepare(sql).get(...this.args) ?? null; },
      async all() { return {success:true,results:db.prepare(sql).all(...this.args)}; },
      runSync() {
        const s = db.prepare(sql);
        return s.columns().length ? {success:true,results:s.all(...this.args),meta:{changes:0}}
          : {success:true,meta:{changes:Number(s.run(...this.args).changes)}};
      } };
  }
  async batch(statements) {
    if (this.beforeBatch) await this.beforeBatch(statements);
    this.sql.exec('BEGIN IMMEDIATE');
    try {
      const r = statements.map((s,n) => { this.inBatch?.(s,n); return s.runSync(); });
      this.sql.exec('COMMIT'); return r;
    } catch (error) { this.sql.exec('ROLLBACK'); throw error; }
  }
}
function insert(f, table, row) {
  const keys = Object.keys(row);
  f.db.sql.prepare(`INSERT INTO ${table}(${keys.join(',')}) VALUES(${keys.map(() => '?').join(',')})`).run(...Object.values(row));
}
const rows = (f, table) => f.db.sql.prepare(`SELECT * FROM ${table} ORDER BY rowid`).all().map((r) => ({...r}));
const one = (f, table) => rows(f,table)[0];
const run = (f) => retain({ledger:f.ledger});
function seed(f, install = I) {
  insert(f,'pull_installs',{install_id:install,generation:7,owner_generation:3,state:'active',owner_hash:H,
    owner_address:'Owner@example.com',address_local:'m'+install.slice(3),key_id:'key',public_key:'k'.repeat(43),
    consent_version:'v1',epoch:4,created_ms:NOW,updated_ms:NOW});
  insert(f,'pull_generations',{install_id:install,generation:7,owner_generation:3,owner_hash:H,key_id:'key',
    public_key:'k'.repeat(43),fence_hash:H,flow_id:FLOW,state:'current',created_ms:NOW});
}
function fixture(t) {
  const f = {db:new D1(),time:NOW}; t.after(() => f.db.sql.close());
  f.db.sql.function('strftime',(fmt,at) => {
    assert.equal(at,'now'); return fmt === '%s' ? String(Math.floor(f.time/1000))
      : `${String(Math.floor(f.time/1000)%60).padStart(2,'0')}.${String(f.time%1000).padStart(3,'0')}`;
  });
  f.ledger = new PullLedger({db:f.db,clock:() => f.time}); seed(f);
  f.scope = {install_id:I,generation:7,owner_generation:3,epoch:4};
  return f;
}
async function family(f, name) {
  for (let n = 0; n < 30; n++) { const r = await run(f); assert.ok(r.scanned <= 100); if (r.family === name) return r; }
  assert.fail(`family ${name} did not receive a turn`);
}
function token(f, n = 1, expires = f.time, extra = {}) {
  insert(f,'pull_tokens',{token_hash:hash(n),install_id:I,flow_id:FLOW,purpose:'signup',generation:7,
    owner_generation:3,owner_hash:H,binding_digest:H,state:'pending',created_ms:expires-600000,expires_ms:expires,...extra});
}
function session(f, n = 1, expires = f.time, extra = {}) {
  insert(f,'pull_browser_sessions',{session_hash:hash(n),csrf_hash:H,install_id:I,flow_id:FLOW,purpose:'confirmation',
    generation:7,owner_generation:3,state:'csrf',created_ms:expires-600000,expires_ms:expires,...extra});
}
function flow(f, extra = {}) {
  insert(f,'pull_flows',{flow_id:FLOW,install_id:I,purpose:'owner_change',expected_generation:7,expected_owner_generation:3,
    candidate_key_id:'key',candidate_public_key:'k'.repeat(43),owner_hash:H,flow_secret_hash:H,fence_hash:H,
    input_digest:H,state:'completed',consent_version:'v1',manifest_sequence:1,manifest_digest:H,
    created_ms:NOW-600000,expires_ms:NOW,...extra});
}
function change(f, extra = {}) {
  flow(f);
  insert(f,'pull_owner_changes',{change_id:id(5),install_id:I,flow_id:FLOW,old_owner_hash:H,new_owner_hash:hash(9),
    old_owner_sealed:new Uint8Array([1]),new_owner_sealed:new Uint8Array([2]),expected_generation:6,expected_owner_generation:2,
    state:'completed',created_ms:NOW-600000,expires_ms:NOW,completed_ms:NOW,revert_until_ms:NOW+7*DAY,...extra});
}
function control(f, n = 1, extra = {}) {
  insert(f,'pull_control_outbox',{outbox_id:id(n),install_id:I,flow_id:FLOW,template:'signup',target_hash:H,
    idempotency_key:`control-${n}`,envelope_sealed:new Uint8Array([3]),seal_key_id:'old-key',state:'queued',
    next_attempt_ms:NOW,created_ms:NOW-600000,expires_ms:NOW,...extra});
}
function thread(f, n = 1, expires = NOW+90*DAY, extra = {}) {
  insert(f,'pull_intents',{install_id:I,generation:7,intent_id:`intent-${n}`,owner_generation:3,owner_hash:H,
    input_digest:H,thread_id:`thread-${n}`,message_id:`sent-${n}@example.com`,state:'accepted',created_ms:NOW,...extra});
  insert(f,'pull_threads',{install_id:I,generation:7,thread_id:`thread-${n}`,owner_generation:3,intent_id:`intent-${n}`,
    token_hash:hash(n),message_id:`sent-${n}@example.com`,expires_ms:expires,dedupe_until_ms:expires+MONTH});
}
function operation(f, extra = {}) {
  insert(f,'pull_operations',{install_id:I,generation:7,path:'/v1/pull/send',op_id:id(1),input_digest:H,
    result_json:'{}',status:200,created_ms:NOW,expires_ms:NOW+MONTH,...extra});
}
const AUTH = {ingress:'cloudflare',spf:'pass',dkim:'pass',dmarc:'pass',aligned:'spf+dkim'};
const incoming = {thread_id:'thread-1',incoming_message_id:'reply@example.com',text:'private body',auth:AUTH};

test('inert module, invalid API and corrupt cursor fail closed without starting work', async (t) => {
  await assert.rejects(() => retain({}), /invalid_request/);
  const f = fixture(t);
  assert.equal(one(f,'pull_sweep_cursors'),undefined);
  assert.deepEqual(await run(f),{family:'control',scanned:0,changed:0});
  f.db.sql.exec("UPDATE pull_sweep_cursors SET cursor_id='bad'");
  await assert.rejects(() => run(f), /storage_unavailable/);
  assert.equal(one(f,'pull_sweep_cursors').cursor_id,'bad');
});

test('tokens retain duplicate receipts 30d after expiry; sessions expire first at exact deadlines', async (t) => {
  const f = fixture(t); token(f,1,NOW-MONTH,{state:'consumed',result_json:'{"receipt":"kept"}'});
  session(f,1,NOW,{token_hash:hash(1)});
  f.time = NOW-1;
  await family(f,'tokens'); assert.equal(one(f,'pull_tokens').result_json,'{"receipt":"kept"}');
  f.time = NOW;
  await family(f,'sessions'); assert.equal(rows(f,'pull_browser_sessions').length,0);
  await family(f,'tokens'); assert.equal(rows(f,'pull_tokens').length,0);
  token(f,2,NOW+1000); await family(f,'tokens'); assert.equal(one(f,'pull_tokens').state,'pending');
  f.time += 1000; await family(f,'tokens'); assert.equal(one(f,'pull_tokens').state,'expired');
});

test('an unexpected live FK child postpones token retirement without pinning its page', async (t) => {
  const f = fixture(t); token(f,1,NOW-MONTH); token(f,2,NOW-MONTH);
  session(f,1,NOW+1000,{token_hash:hash(1)});
  await family(f,'tokens'); assert.deepEqual(rows(f,'pull_tokens').map((r) => r.token_hash),[hash(1)]);
  f.time += 1000; await family(f,'tokens'); assert.equal(rows(f,'pull_tokens').length,0);
});

test('budget, nonce, ticket and owner-slot deadlines never release active scope or early quota', async (t) => {
  const f = fixture(t);
  insert(f,'pull_budgets',{bucket:'uncertain-send-charge',count:9,expires_ms:NOW+1});
  insert(f,'pull_nonces',{direction:'install-to-pull',install_id:I,generation:7,key_id:'key',nonce:id(1),expires_ms:NOW+1});
  insert(f,'pull_tickets',{ticket_hash:H,ticket_id:id(1),op_id:id(2),install_id:I,generation:7,owner_generation:3,
    key_id:'key',state:'consumed',created_ms:NOW-1000,expires_ms:NOW+1});
  for (const role of ['active','pending','revert']) insert(f,'pull_owner_slots',{owner_hash:H,install_id:I,
    role,flow_id:FLOW,until_ms:role==='active'?null:NOW+1});
  for (const name of ['nonces','tickets','budgets','slots']) await family(f,name);
  assert.equal(one(f,'pull_budgets').count,9); assert.equal(rows(f,'pull_owner_slots').length,3);
  f.time++;
  for (const name of ['nonces','tickets','budgets','slots']) await family(f,name);
  for (const table of ['pull_nonces','pull_tickets','pull_budgets']) assert.equal(rows(f,table).length,0);
  assert.equal(one(f,'pull_owner_slots').role,'active');
});

test('lost sending lease stays uncertain, never requeued/refunded; expiry removes only the envelope', async (t) => {
  const f = fixture(t); control(f,1,{state:'sending',lease_id:id(8),lease_until_ms:NOW+1,expires_ms:NOW+1000,attempts:1});
  insert(f,'pull_budgets',{bucket:'charged',count:1,expires_ms:NOW+DAY});
  await family(f,'control'); assert.equal(one(f,'pull_control_outbox').state,'sending');
  f.time++; await family(f,'control');
  let r = one(f,'pull_control_outbox'); assert.equal(r.state,'uncertain'); assert.ok(r.envelope_sealed);
  assert.equal(r.lease_id,null); assert.equal(r.attempts,1); assert.equal(one(f,'pull_budgets').count,1);
  f.time = NOW+1000; await family(f,'control'); r = one(f,'pull_control_outbox');
  assert.equal(r.state,'uncertain'); assert.equal(r.envelope_sealed,null);
  f.time = NOW+1000+MONTH-1; await family(f,'control'); assert.ok(one(f,'pull_control_outbox'));
  f.time++; await family(f,'control'); assert.equal(one(f,'pull_control_outbox'),undefined);
});

test('expired and accepted control envelopes are purged, preserving provider acceptance and failure metadata', async (t) => {
  const f = fixture(t);
  control(f,1,{state:'accepted',envelope_sealed:null,provider_id:'positive',expires_ms:NOW+1});
  control(f,2,{state:'failed',attempts:3});
  control(f,3,{expires_ms:NOW+1});
  await family(f,'control');
  assert.deepEqual(rows(f,'pull_control_outbox').map((r) => [r.state,r.envelope_sealed,r.provider_id]),
    [['accepted',null,'positive'],['expired',null,''],['queued',new Uint8Array([3]),'']]);
  f.db.sql.exec("UPDATE pull_installs SET state='deleted',owner_address=NULL");
  await family(f,'control'); assert.ok(rows(f,'pull_control_outbox').every((r) => r.envelope_sealed===null));
});

test('deleted/new-owner history preserves old-owner revert secrets, token, slot and decrypt-key metadata until deadline', async (t) => {
  const f = fixture(t); change(f,{created_ms:NOW-40*DAY,expires_ms:NOW-39*DAY});
  token(f,1,NOW+7*DAY,{purpose:'revert',change_id:id(5)});
  insert(f,'pull_owner_slots',{owner_hash:H,install_id:I,role:'revert',flow_id:FLOW,until_ms:NOW+7*DAY});
  control(f,1,{template:'old_notice',change_id:id(5),state:'failed',expires_ms:NOW-31*DAY,
    created_ms:NOW-32*DAY,finished_ms:NOW-31*DAY});
  f.db.sql.exec("UPDATE pull_installs SET state='deleted',owner_address=NULL");
  await family(f,'changes'); await family(f,'control');
  assert.ok(one(f,'pull_owner_changes').old_owner_sealed);
  assert.equal(one(f,'pull_owner_changes').new_owner_sealed,null);
  assert.equal(one(f,'pull_control_outbox').seal_key_id,'old-key');
  f.time = NOW+7*DAY-1; await family(f,'changes'); assert.ok(one(f,'pull_owner_changes').old_owner_sealed);
  f.time++; await family(f,'changes'); await family(f,'control'); await family(f,'slots'); await family(f,'tokens');
  assert.equal(one(f,'pull_owner_changes').old_owner_sealed,null);
  assert.equal(one(f,'pull_control_outbox'),undefined); assert.equal(one(f,'pull_owner_slots'),undefined);
  assert.equal(one(f,'pull_tokens').state,'expired');
  f.time = NOW+365*DAY-1; await family(f,'changes'); assert.ok(one(f,'pull_owner_changes'));
  f.time++; await family(f,'changes'); assert.equal(one(f,'pull_owner_changes'),undefined);
});

test('pending/cancelled change secrets purge at expiry/cancellation; reverted switches still count for a year', async (t) => {
  for (const state of ['pending','cancelled','expired','reverted']) {
    await t.test(state,async (t) => {
      const f = fixture(t); change(f,{state,completed_ms:state==='reverted'?NOW:null,expires_ms:NOW+1,revert_until_ms:null});
      await family(f,'changes');
      assert.equal(Boolean(one(f,'pull_owner_changes').old_owner_sealed),state==='pending');
      f.time++; await family(f,'changes');
      assert.equal(one(f,'pull_owner_changes').old_owner_sealed,null);
      assert.equal(one(f,'pull_owner_changes').new_owner_sealed,null);
      f.time = NOW+MONTH+1; await family(f,'changes');
      assert.equal(Boolean(one(f,'pull_owner_changes')),state==='reverted');
    });
  }
});

test('completed new-owner sealed value waits for a live control envelope, but never beyond revert expiry', async (t) => {
  const f = fixture(t); change(f); control(f,1,{change_id:id(5),expires_ms:NOW+8*DAY});
  await family(f,'changes'); assert.ok(one(f,'pull_owner_changes').new_owner_sealed);
  f.time = NOW+7*DAY; await family(f,'changes');
  assert.equal(one(f,'pull_owner_changes').old_owner_sealed,null); assert.equal(one(f,'pull_owner_changes').new_owner_sealed,null);
});

test('deletion purges pending change private data without manufacturing a revert window', async (t) => {
  const f=fixture(t); change(f,{state:'pending',completed_ms:null,revert_until_ms:null,expires_ms:NOW+DAY});
  f.db.sql.exec("UPDATE pull_installs SET state='deleted',owner_address=NULL");
  await family(f,'changes');
  assert.equal(one(f,'pull_owner_changes').old_owner_sealed,null);
  assert.equal(one(f,'pull_owner_changes').new_owner_sealed,null);
});

test('operation receipts honor 30d, stored expiry, flow and generation thread replay horizons', async (t) => {
  const f = fixture(t); thread(f,1,NOW+90*DAY); flow(f,{expires_ms:NOW+121*DAY}); operation(f);
  f.time = NOW+MONTH; await family(f,'operations'); assert.ok(one(f,'pull_operations'));
  f.time = NOW+120*DAY; await family(f,'operations'); assert.ok(one(f,'pull_operations'));
  f.time = NOW+151*DAY-1; await family(f,'operations'); assert.ok(one(f,'pull_operations'));
  f.time++; await family(f,'operations'); assert.equal(one(f,'pull_operations'),undefined);
  operation(f,{created_ms:f.time-MONTH,expires_ms:f.time+1});
  await family(f,'operations'); assert.ok(one(f,'pull_operations'));
  f.time++; await family(f,'operations'); assert.equal(one(f,'pull_operations'),undefined);
});

test('30d detailed cleanup keeps late SMTP replay/ack evidence until thread+30d, then retires in FK-safe order', async (t) => {
  const f = fixture(t); thread(f);
  const e = await f.ledger.enqueue(f.scope,incoming);
  const o = await f.ledger.offer(f.scope,e.event_id);
  const a = await f.ledger.ack(f.scope,{event_id:e.event_id,event_digest:e.event_digest,body_digest:e.body_digest,
    lease_id:o.lease_id,local_receipt_id:id(8)});
  f.time = NOW+MONTH-1; await family(f,'events'); assert.ok(one(f,'pull_events'));
  f.time++; await family(f,'events'); assert.equal(one(f,'pull_events'),undefined);
  const replay = await f.ledger.enqueue(f.scope,incoming);
  assert.equal(replay.duplicate,true); assert.equal(replay.event_id,e.event_id);
  assert.equal(one(f,'pull_receipts').receipt_id,a.receipt_id);
  assert.equal(rows(f,'pull_dedupe').length,1); assert.equal(rows(f,'pull_events').length,0);
  f.time = NOW+120*DAY-1;
  for (const name of ['receipts','offers','dedupe','threads']) await family(f,name);
  assert.ok(one(f,'pull_receipts')); assert.ok(one(f,'pull_threads')); assert.ok(one(f,'pull_intents'));
  f.time++;
  // Attempt parent retirement first: guards retain parents until their children go.
  await family(f,'dedupe'); await family(f,'threads');
  for (const name of ['receipts','offers','events','dedupe','threads']) await family(f,name);
  for (const table of ['pull_receipts','pull_offers','pull_events','pull_dedupe','pull_threads','pull_intents']) assert.equal(rows(f,table).length,0,table);
  assert.deepEqual(f.db.sql.prepare('PRAGMA foreign_key_check').all(),[]);
});

test('independent offer/receipt deadlines and unexpired ingress holds postpone parent retirement', async (t) => {
  const f = fixture(t); thread(f);
  const e = await f.ledger.enqueue(f.scope,incoming), o = await f.ledger.offer(f.scope,e.event_id);
  await f.ledger.ack(f.scope,{event_id:e.event_id,event_digest:e.event_digest,body_digest:e.body_digest,
    lease_id:o.lease_id,local_receipt_id:id(8)});
  f.db.sql.exec(`UPDATE pull_receipts SET dedupe_until_ms=${NOW+121*DAY}; UPDATE pull_offers SET dedupe_until_ms=${NOW+122*DAY}`);
  insert(f,'pull_ingress_holds',{hold_id:id(4),install_id:I,generation:7,owner_generation:3,thread_id:'thread-1',
    raw_digest:H,reason:'invalid_message_id',received_ms:NOW,expires_ms:NOW+123*DAY});
  f.time = NOW+120*DAY;
  for (const name of ['receipts','offers','events','dedupe','threads']) await family(f,name);
  assert.ok(one(f,'pull_dedupe')); assert.ok(one(f,'pull_threads'));
  f.time += 2*DAY;
  for (const name of ['receipts','offers','dedupe','threads']) await family(f,name);
  assert.equal(one(f,'pull_dedupe'),undefined); assert.ok(one(f,'pull_threads'));
});

test('live or unretired detailed events are never cascade-purged by metadata cleanup', async (t) => {
  const f = fixture(t); thread(f); await f.ledger.enqueue(f.scope,incoming);
  f.time = NOW+130*DAY;
  for (const name of ['events','dedupe','threads']) await family(f,name);
  assert.equal(one(f,'pull_events').text,'private body'); assert.ok(one(f,'pull_dedupe'));
});

test('uncertain/accepted send evidence is retained through the immutable thread horizon without resubmission', async (t) => {
  const f = fixture(t); thread(f,1,NOW+90*DAY,{state:'uncertain',provider_id:'late-positive',finished_ms:NOW+1});
  f.time = NOW+120*DAY-1; await family(f,'threads');
  assert.equal(one(f,'pull_intents').state,'uncertain'); assert.equal(one(f,'pull_intents').provider_id,'late-positive');
  f.time++; await family(f,'threads'); assert.equal(one(f,'pull_intents'),undefined);
});

test('fixed-pass keysets survive restart, complete despite continuous appends, and revisit newly due heads', async (t) => {
  const f = fixture(t); seed(f,id(2)); seed(f,id(3));
  for (let n=1;n<=203;n++) token(f,n,NOW-MONTH+1,{install_id:[I,id(2),id(3)][n%3]});
  assert.equal((await family(f,'tokens')).scanned,100);
  f.ledger = new PullLedger({db:f.db,clock:()=>{throw new Error('Worker clock is not expiry authority');}});
  f.time++;
  for (let n=204;n<=303;n++) token(f,n,NOW-MONTH);
  assert.equal((await family(f,'tokens')).scanned,100);
  for (let n=304;n<=403;n++) token(f,n,NOW-MONTH);
  assert.equal((await family(f,'tokens')).scanned,3);
  assert.deepEqual(JSON.parse(one(f,'pull_sweep_cursors').cursor_id)[3],[0,0]);
  // The first100 were unexpired on first visit. Appends did not prevent wrap.
  await family(f,'tokens');
  assert.ok(rows(f,'pull_tokens').every((r)=>Number.parseInt(r.token_hash,16)>100));
  assert.equal(rows(f,'pull_tokens').length,200);
});

test('large control queues cannot starve another metadata family', async (t) => {
  const f = fixture(t); for(let n=1;n<=250;n++) control(f,n,{expires_ms:NOW+DAY}); session(f);
  assert.equal((await run(f)).scanned,100);
  assert.equal((await family(f,'sessions')).scanned,1); assert.equal(one(f,'pull_browser_sessions'),undefined);
  assert.equal(rows(f,'pull_control_outbox').length,250);
});

test('competing pages use durable cursor CAS; failures roll back cleanup and cursor together', async (t) => {
  const f = fixture(t); control(f); let raced=false;
  f.db.beforeBatch=async()=>{if(!raced){raced=true;await run(f);}};
  assert.deepEqual(await run(f),{family:'control',scanned:0,changed:0});
  assert.equal(one(f,'pull_control_outbox').state,'expired');
  const before = {...one(f,'pull_sweep_cursors')}; f.db.beforeBatch=null;
  change(f,{state:'cancelled',completed_ms:null,revert_until_ms:null});
  f.db.inBatch=(_s,n)=>{if(n===2)throw new Error('simulated disk failure');};
  await assert.rejects(()=>run(f),/storage_unavailable/);
  assert.deepEqual(one(f,'pull_sweep_cursors'),before); assert.ok(one(f,'pull_owner_changes').old_owner_sealed);
  f.db.inBatch=null; await run(f); assert.equal(one(f,'pull_owner_changes').old_owner_sealed,null);
});

test('D1 evaluates deadlines at mutation time after the prepared page waited', async (t) => {
  const f=fixture(t); control(f);
  // Use a future deadline and advance SQL time only after candidate preparation.
  f.db.sql.exec(`UPDATE pull_control_outbox SET expires_ms=${NOW+1}`);
  f.db.beforeBatch=async()=>{f.time=NOW+1;};
  await run(f); assert.equal(one(f,'pull_control_outbox').state,'expired');
});

test('audit expiry/cap compaction is bounded, preserves newest10000 and keeps daily overflow <=30d', async (t) => {
  const f=fixture(t);
  f.db.sql.exec(`WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<10005)
    INSERT INTO pull_audit(install_id,at_ms,code) SELECT '${I}',${NOW},'safe' FROM n`);
  insert(f,'pull_audit_overflow',{day_ms:NOW-MONTH,count:9});
  assert.equal((await family(f,'audit')).scanned,100);
  assert.equal(rows(f,'pull_audit').length,10000);
  assert.equal(f.db.sql.prepare('SELECT count FROM pull_audit_overflow WHERE day_ms=?').get(Math.floor(NOW/DAY)*DAY).count,5);
  await family(f,'overflow'); assert.equal(rows(f,'pull_audit_overflow').length,1);
  f.time=NOW+MONTH;
  await family(f,'audit'); assert.equal(rows(f,'pull_audit').length,9900);
  await family(f,'overflow'); assert.equal(one(f,'pull_audit_overflow'),undefined);
});

test('actual generated reference checks and FK plans never scan state tables', async (t) => {
  const f=fixture(t);
  for(let n=0;n<16;n++) await run(f);
  for(const sql of f.db.queries.filter((s)=>/^(?:DELETE|UPDATE|INSERT INTO pull_audit_overflow)/.test(s))) {
    // Thread/intent DELETE has one bounded json_each list parameter.
    const args = Array.from({length:(sql.match(/\?/g)??[]).length},()=> '[]');
    const p=f.db.sql.prepare(`EXPLAIN QUERY PLAN ${sql}`).all(...args).map((r)=>r.detail).join('\n');
    assert.doesNotMatch(p,/\bSCAN (?:s|o|f|g|t|c|d|h|i|pull_browser_sessions|pull_threads|pull_dedupe|pull_events|pull_offers|pull_receipts)\b/,p);
  }
});

test('Miniflare D1 executes the real module with0006, persisted cursors and foreign keys', async (t) => {
  const mf=new Miniflare({modules:true,script:"export default {fetch(){return new Response('unused')}}",d1Databases:['DB'],
    log:new Log(LogLevel.ERROR)}); t.after(()=>mf.dispose());
  const db=await mf.getD1Database('DB');
  // Match existing D1 bootstrap fixtures: preserve trigger bodies while giving
  // exec one complete source per line (it is line-oriented).
  for(const source of sources) await db.exec(source.replace(/--[^\n]*/g,'').replace(/[\r\n]/g,' '));
  const ledger=new PullLedger({db});
  await db.prepare("INSERT INTO pull_budgets VALUES('expired',2,0)").run();
  for(let n=0;n<16;n++) await retain({ledger});
  assert.equal(await db.prepare('SELECT * FROM pull_budgets').first(),null);
  assert.equal((await db.prepare("SELECT * FROM pull_sweep_cursors WHERE task='metadata'").first()).cursor_ms,0);
  assert.deepEqual((await db.prepare('PRAGMA foreign_key_check').all()).results,[]);
  assert.ok((await db.prepare(`SELECT ${SQL_NOW} AS now`).first()).now>0);
});
