#!/usr/bin/env node
/**
 * Agent-mail actions model, PULL signup path (MC-1166 U3). Pure Node, no browser:
 * static/js/agent-mail-actions-model.js is evaluated against a stub `window`.
 * Pins: each Signup.status state maps to the right control, no legacy
 * review/verify/proof control is offered for pull, a held view in an
 * HTTP-200 answer is a failure, malformed data fails closed (no controls), and the
 * legacy path is untouched.
 *
 * RUN: node tools/smoke/agent-mail-actions-model.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import vm from 'node:vm';

const __dirname = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(__dirname, '..', '..', 'static', 'js', 'agent-mail-actions-model.js'), 'utf8');
const win = {};
vm.runInNewContext(src, { window: win });
const M = win.AgentMailActionsModel;

let bad = 0;
const check = (cond, msg) => { console.log((cond ? '  ✓ ' : '  ✗ ') + msg); if (!cond) bad++; };
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const view = (o) => ({ status: 200, body: { protocol: 'pull', pull_status: {}, signup: {
  state: 'pending', reason: '', enabled: false, install_id: 'i', flow_id: 'f', address: 'm1@clayrune.io',
  expires_ms: 1, verification_state: null, ...o } } });
const controls = (o) => M.plan(view(o)).controls;
const FORBIDDEN = ['review', 'confirm', 'verify', 'proof', 'retry-revocations'];

console.log('plan: signup states');
check(same(controls({ state: 'not_enrolled' }), ['setup']), 'not_enrolled -> setup');
check(same(controls({ state: 'preparing' }), ['resume']), 'preparing -> resume');
check(same(controls({ state: 'start_pending' }), ['resume']), 'start_pending -> resume');
for (const vs of [null, 'queued', 'accepted', 'uncertain', 'failed']) {
  check(same(controls({ state: 'pending', verification_state: vs }), ['check']), 'pending/' + vs + ' -> check verification');
}
check(M.plan(view({ state: 'pending', verification_state: 'queued' })).text.includes('queued'), 'queued text says queued');
check(/may or may not/.test(M.plan(view({ state: 'pending', verification_state: 'uncertain' })).text), 'uncertain text says so');
check(/could not be sent/.test(M.plan(view({ state: 'pending', verification_state: 'failed' })).text), 'failed text says so');
check(same(controls({ state: 'verified' }), ['enable']), 'verified -> enable');
check(same(controls({ state: 'enable_pending' }), ['enable']), 'enable_pending -> enable (finish)');
check(same(controls({ state: 'active', enabled: true }), ['disable']), 'active -> Disable');
const exp = M.plan(view({ state: 'expired', reason: 'flow_expired' }));
check(exp.controls.length === 0 && /30-minute/.test(exp.text), 'expired -> no control, plain reason');
check(controls({ state: 'cancelled' }).length === 0, 'cancelled -> no control');
const held = M.plan(view({ state: 'held', reason: 'vault_locked' }));
check(held.state === 'held' && held.controls.length === 0 && /vault is locked/.test(held.text), 'held -> no control, plain reason');
const heldPending = M.plan(view({ state: 'pending', reason: 'seal_missing' }));
check(heldPending.state === 'held' && heldPending.controls.length === 0, 'reason on a pending view holds it (no check/enable)');
const every = ['not_enrolled', 'preparing', 'start_pending', 'pending', 'verified', 'enable_pending', 'active', 'expired', 'cancelled', 'held']
  .flatMap((s) => controls({ state: s, enabled: s === 'active', reason: s === 'held' ? 'vault_locked' : '' }));
check(!every.some((c) => FORBIDDEN.includes(c)), 'no review/confirm/verify/proof/retry-revocations for these pull states');

console.log('plan: malformed fails closed');
const closed = (r, msg) => { const p = M.plan(r); check(p.state === 'error' && p.controls.length === 0 && !!p.text, msg); };
closed(view({ state: 'bogus' }), 'unknown state');
closed(view({ state: 'active', enabled: false }), 'active but enabled not true');
closed(view({ state: 'active', enabled: 'yes' }), 'active with non-boolean enabled');
closed(view({ state: 'held', reason: '' }), 'held with no reason');
closed(view({ state: 5 }), 'non-string state');
closed({ status: 200, body: { protocol: 'pull' } }, 'no signup object');
closed({ status: 200, body: { protocol: 'pull', signup: [] } }, 'signup is an array');
closed({ status: 200, body: { protocol: 'pull', signup: null } }, 'signup is null');
check(M.plan({ status: 200, body: { protocol: 'push', signup: { state: 'verified' } } }).protocol !== 'pull', 'unknown protocol value is not treated as pull');
check(!('text' in M.plan({ status: 200, body: { stage: 'not_enrolled' } })), 'legacy answer (no discriminator) still takes the legacy path');
check(same(M.plan({ status: 200, body: { stage: 'active', ownership: true, now_ms: 1, ownership_expires_ms: 9 } }).controls, ['proof', 'enable', 'disable']), 'legacy active plan unchanged');
check(same(M.plan({ status: 503, body: null }).controls, ['disable']), 'legacy 503 still offers disable');

console.log('outcome: pull POST answers');
// POST answers are FLAT, exactly as the route sends them: jsonify({'protocol': 'pull', **signup_view}).
const flat = (o) => ({ protocol: 'pull', state: 'pending', reason: '', enabled: false, install_id: 'i', flow_id: 'f',
  address: 'm1@clayrune.io', expires_ms: 1, verification_state: null, ...o });
const post = (action, o, status = 200) => M.outcome(action, { ok: status === 200, status, body: flat(o) });
check(post('enable', { state: 'active', enabled: true }).tone === 'good', 'enable -> active+enabled is success');
check(post('enable', { state: 'held', reason: 'stale_generation' }).tone === 'bad', 'HTTP 200 + held is a failure, never success');
check(post('enable', { state: 'active', enabled: true, activation_attempts: 1, activation_pending: false }).text === 'Agent mail is enabled.', 'flat active+enabled enable answer is reported good');
check(post('check', { state: 'pending', reason: 'connection_lost' }).tone === 'bad', 'flat answer with a reason set is bad');
check(M.outcome('enable', { ok: true, status: 200, body: view({ state: 'active', enabled: true }).body }).tone === 'bad', 'GET-shaped (nested signup) body is not accepted as a POST answer');
check(M.plan(view({ state: 'held', reason: 'service_unavailable' })).text.includes('mail service'), 'service_unavailable has plain words');
check(post('enable', { state: 'verified' }).tone === 'bad', 'enable answered "verified" is not announced as success');
check(post('enable', { state: 'active', enabled: false }).tone === 'bad', 'enable answered active without enabled is a failure');
check(post('resume', { state: 'pending', verification_state: 'accepted' }).tone === 'good', 'resume pending is progress');
check(post('check', { state: 'held', reason: 'connection_lost' }).text.includes('could not be reached'), 'check refused text is plain words');
check(post('start', { state: 'bogus' }).tone === 'bad', 'malformed POST answer is a failure');
check(post('check', { state: 'expired', reason: 'flow_expired' }).tone === 'bad', 'check answered expired is not success-toned');
check(post('check', { state: 'cancelled' }).tone === 'bad', 'check answered cancelled is not success-toned');
const committed = M.outcome('enable', { ok: false, status: 503, body: { protocol: 'pull', error: 'storage_unavailable', activation: 'committed' } });
check(committed.tone === 'warn' && /Press Enable again/.test(committed.text), '503 activation committed -> retry enable');
check(M.outcome('enable', { ok: false, status: 409, body: { error: 'deployment_unconfigured' } }).tone === 'bad', '409 refusal still bad');
check(M.outcome('start', { ok: true, status: 200, body: { stage: 'pending' } }).text === 'Setup started. The next step is shown below.', 'legacy 200 outcome unchanged');
check(M.ACTIONS.check && M.ACTIONS.check.label === 'Check verification', 'check action is labelled Check verification');

console.log(bad ? '\nFAIL: ' + bad + ' failed' : '\nPASS');
process.exit(bad ? 1 : 0);
