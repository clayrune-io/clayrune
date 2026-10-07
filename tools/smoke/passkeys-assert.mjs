#!/usr/bin/env node
/**
 * Settings -> Connectivity -> "Passkeys", slice 2a (docs/PASSKEYS_SPEC.md,
 * "Slice 2 prerequisites").
 *
 * WHY THIS EXISTS
 * ----------------
 * Once a passkey exists the server refuses passcode-only add and revoke
 * (`proof_required`). This pins the browser half of the replacement: add and
 * revoke first run a WebAuthn ASSERTION (navigator.credentials.get over the
 * assert/options challenge) and send it as `proof`, with no passcode prompt; a
 * refused or cancelled assertion is reported, never retried with the passcode;
 * lost-all recovery and the registry reset are passcode + confirm; and every
 * state in which passkeys are unavailable (locked vault, no vault passphrase,
 * unsigned / tampered / unreadable registry) says so honestly.
 *
 * Hermetic, same idiom as passkeys-settings.mjs: the SPA is served from THIS
 * checkout and the passkey routes are canned in memory, so this proves the panel
 * and the bytes it sends, NOT the server's verification (tests/test_passkeys_*.py).
 * navigator.credentials is a FAKE that records its calls and returns canned
 * results: it is not evidence about any real authenticator.
 *
 * RUN: node tools/smoke/passkeys-assert.mjs
 */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const STATIC_ROOT = resolve(REPO_ROOT, 'static');
const ORIGIN = 'http://localhost:5199';

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, bad_) => (cond ? ok(good) : fail(bad_));
const b64url = (arr) => Buffer.from(arr).toString('base64url');

const CHALLENGE = [9, 8, 7, 6, 5, 4, 3, 2, 1];
const EXISTING_ID = b64url([1, 2, 3]);

let listing = null;            // null = healthy listing built from `credentials`
let credentials = [];
let calls = [];                // { method, path, body }
let nextRegisterOptions = null; // override the register/options reply
let nextRecover = null;
let nextReset = null;

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1000, height: 1000 } });
const page = await ctx.newPage();
await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
await page.addInitScript(() => {
  // Fake WebAuthn: records every call and answers from `mode`.
  const bytes = (...v) => new Uint8Array(v).buffer;
  const enc = (s) => new TextEncoder().encode(s).buffer;
  window.__pk = { creates: [], gets: [], mode: 'ok', vault: [] };
  const fake = {
    async get(opts) {
      const pk = opts.publicKey;
      window.__pk.gets.push({
        challenge: Array.from(new Uint8Array(pk.challenge)),
        allow: (pk.allowCredentials || []).map((c) => Array.from(new Uint8Array(c.id))),
        uv: pk.userVerification,
      });
      if (window.__pk.mode === 'cancel') throw new DOMException('cancelled', 'NotAllowedError');
      return {
        id: 'AQID', rawId: bytes(1, 2, 3), type: 'public-key',
        response: { clientDataJSON: enc('{"type":"webauthn.get"}'), authenticatorData: bytes(7, 7, 7),
          signature: bytes(5, 5), userHandle: null },
        getClientExtensionResults() { return {}; },
      };
    },
    async create(opts) {
      window.__pk.creates.push({ challenge: Array.from(new Uint8Array(opts.publicKey.challenge)) });
      return {
        id: 'BAUG', rawId: bytes(4, 5, 6), type: 'public-key',
        response: { clientDataJSON: enc('{"type":"webauthn.create"}'), attestationObject: bytes(1, 1, 1),
          getTransports() { return ['internal']; } },
        getClientExtensionResults() { return {}; },
      };
    },
  };
  Object.defineProperty(navigator, 'credentials', { value: fake, configurable: true });
});
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

let dialogAnswer = true;
const dialogs = [];
page.on('dialog', (d) => { dialogs.push(d.message()); (dialogAnswer ? d.accept() : d.dismiss()).catch(() => {}); });

const activeCreds = () => credentials.filter((c) => !c.revoked_at);

await page.route('**/*', async (route) => {
  const req = route.request();
  const url = new URL(req.url());
  const path = url.pathname;
  const method = req.method();
  const json = (body, status = 200) => route.fulfill({
    status, contentType: 'application/json', body: JSON.stringify(body) });
  const body = () => { try { return JSON.parse(req.postData() || '{}'); } catch (_) { return {}; } };

  if (path === '/' || path === '/index.html')
    return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8',
      body: readFileSync(resolve(STATIC_ROOT, 'index.html'), 'utf8') });
  if (path.startsWith('/static/')) {
    const file = normalize(join(STATIC_ROOT, path.slice('/static/'.length)));
    if (file.startsWith(STATIC_ROOT) && existsSync(file)) {
      const type = file.endsWith('.js') ? 'text/javascript; charset=utf-8'
        : file.endsWith('.css') ? 'text/css; charset=utf-8' : 'application/octet-stream';
      return route.fulfill({ status: 200, contentType: type, body: readFileSync(file, 'utf8') });
    }
    return route.abort();
  }
  if (path === '/api/projects') return json([]);
  if (path === '/api/local-auth/status') return json({ configured: true });
  if (path === '/api/config' && method === 'GET') return json({});
  if (path === '/api/system/update/status') return json({ is_git_repo: true, behind: 0, ahead: 0,
    has_local_changes: false, update_available: false, projects_in_install_dir: [] });

  if (!path.startsWith('/api/passkeys')) return route.abort();
  calls.push({ method, path, body: body() });

  if (path === '/api/passkeys' && method === 'GET') {
    if (listing) return json(listing.body, listing.status);
    return json({
      available: true, rp_id: 'localhost', origin: ORIGIN, can_enroll_here: true,
      enroll_blocked_reason: null, enroll_blocked_message: '', enrolled: credentials.length > 0,
      credentials, active_count: activeCreds().length, max_active: 10,
      needs_passkey_to_change: activeCreds().length > 0 });
  }
  if (path === '/api/passkeys/assert/options') {
    const b = body();
    return json({ ceremony_id: 'assert-' + b.purpose, expires_in: 120, options: {
      challenge: b64url(CHALLENGE), rpId: 'localhost', timeout: 60000, userVerification: 'required',
      allowCredentials: activeCreds().map((c) => ({ type: 'public-key', id: c.id })) } });
  }
  if (path === '/api/passkeys/register/options') {
    if (nextRegisterOptions) { const r = nextRegisterOptions; nextRegisterOptions = null; return json(r.body, r.status); }
    return json({ ceremony_id: 'reg-1', expires_in: 120, options: {
      rp: { name: 'Clayrune', id: 'localhost' },
      user: { id: b64url([1]), name: 'clayrune-owner', displayName: 'Clayrune owner' },
      challenge: b64url([4, 4, 4]), pubKeyCredParams: [{ type: 'public-key', alg: -7 }],
      excludeCredentials: activeCreds().map((c) => ({ type: 'public-key', id: c.id })) } });
  }
  if (path === '/api/passkeys/register/finish') {
    credentials.push({ id: 'BAUG', label: 'Second key', created_at: '2026-10-06T10:00:00+00:00',
      last_used_at: null, revoked_at: null });
    return json({ ok: true, credential: credentials.at(-1) });
  }
  if (path === '/api/passkeys/recover') {
    if (nextRecover) { const r = nextRecover; nextRecover = null; return json(r.body, r.status); }
    const n = activeCreds().length;
    credentials.forEach((c) => { c.revoked_at = c.revoked_at || '2026-10-06T11:00:00+00:00'; });
    return json({ ok: true, revoked: n });
  }
  if (path === '/api/passkeys/reset') {
    if (nextReset) { const r = nextReset; nextReset = null; return json(r.body, r.status); }
    listing = null; credentials = [];
    return json({ ok: true, quarantine: 'quarantine-x', moved: 2 });
  }
  if (path.startsWith('/api/passkeys/') && method === 'DELETE') {
    const id = decodeURIComponent(path.slice('/api/passkeys/'.length));
    const c = credentials.find((x) => x.id === id);
    if (c) c.revoked_at = '2026-10-06T12:00:00+00:00';
    return json({ ok: true, credential: c });
  }
  return route.abort();
});

const reset = () => { calls = []; dialogs.length = 0; dialogAnswer = true; };
const callsTo = (method, path) => calls.filter((c) => c.method === method && c.path === path);
const noPasscodeModal = () => page.evaluate(() => !document.querySelector('[data-modal-id^="__human-proof-"]'));
const text = () => page.evaluate(() => document.getElementById('passkeys-host')?.textContent || '');

const answerPasscodeModal = async () => {
  await page.waitForSelector('[data-modal-id^="__human-proof-"]', { timeout: 5000 });
  await page.evaluate(() => {
    const el = document.querySelector('[data-modal-id^="__human-proof-"]');
    const modalId = el.dataset.modalId;
    document.getElementById(`hp-passcode-${modalId}`).value = 'smoke-dash-passcode';
    window._hpSubmit(modalId);
  });
};

const openConnectivity = async () => {
  await page.evaluate(() => window.closeModalById && window.closeModalById('__settings'));
  await page.evaluate(() => window.openSettings());
  await page.waitForSelector('#passkeys-section', { state: 'attached', timeout: 10000 });
  await page.evaluate(() => window.drillSettings('connect'));
  await page.evaluate(() => {
    const secs = [...document.querySelectorAll(
      '#settings-detail .settings-detail-pane[data-cat="connect"] .settings-section')];
    const idx = secs.findIndex((s) => s.id === 'passkeys-section');
    if (idx < 0) throw new Error('Passkeys settings section not found');
    window.drillSettingsSub(idx);
  });
  await page.waitForSelector('#passkeys-host .pk-meta, #passkeys-host .pk-foot', { state: 'visible', timeout: 5000 });
};
const waitText = (re, label) => page.waitForFunction(
  (src) => new RegExp(src).test(document.getElementById('passkeys-host')?.textContent || ''),
  re.source, { timeout: 8000 }).catch(() => { fail(`never saw ${label}`); });

try {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openSettings === 'function', { timeout: 20000 });

  // ── 1. Add with an assertion: no passcode ───────────────────────────────
  credentials = [{ id: EXISTING_ID, label: 'First key', created_at: '2026-10-01T10:00:00+00:00',
    last_used_at: null, revoked_at: null }];
  reset();
  await openConnectivity();
  check(/approve with one you already have/.test(await text()),
    'with a passkey present the panel says add/revoke need an existing passkey', 'no hint about approving with a passkey');
  await page.fill('#passkeys-label', 'Second key');
  await page.click('[data-pk-act="enroll"]');
  await waitText(/Passkey added/, '"Passkey added."');
  const ao = callsTo('POST', '/api/passkeys/assert/options');
  check(ao.length === 1 && ao[0].body.purpose === 'add' && !('credential_id' in ao[0].body),
    'add asked for an assert/options ceremony with purpose "add"', `assert/options wrong: ${JSON.stringify(ao)}`);
  const gets = await page.evaluate(() => window.__pk.gets);
  check(gets.length === 1 && JSON.stringify(gets[0].challenge) === JSON.stringify(CHALLENGE)
    && gets[0].uv === 'required' && gets[0].allow.length === 1,
    'navigator.credentials.get received the server challenge, user verification required, the active credential allowed',
    `get() call wrong: ${JSON.stringify(gets)}`);
  const ro = callsTo('POST', '/api/passkeys/register/options');
  const proof = ro[0] && ro[0].body.proof;
  check(ro.length === 1 && proof && proof.ceremony_id === 'assert-add'
    && proof.assertion.id === 'AQID' && proof.assertion.rawId === 'AQID' && proof.assertion.type === 'public-key'
    && proof.assertion.response.authenticatorData === b64url([7, 7, 7])
    && proof.assertion.response.signature === b64url([5, 5])
    && proof.assertion.response.clientDataJSON === b64url(Buffer.from('{"type":"webauthn.get"}')),
    'register/options carried proof {ceremony_id, assertion} in the server\'s base64url shape',
    `proof wrong: ${JSON.stringify(ro)}`);
  check(ro[0] && !('passcode' in ro[0].body) && ro[0].body.label === 'Second key' && await noPasscodeModal(),
    'no passcode was sent and no passcode prompt opened', 'a passcode prompt or field appeared on the passkey path');
  check(callsTo('POST', '/api/passkeys/register/finish').length === 1
    && (await page.evaluate(() => window.__pk.creates.length)) === 1,
    'create() and register/finish still ran after the assertion', 'enrolment did not finish');

  // ── 2. Revoke with an assertion ─────────────────────────────────────────
  reset();
  await openConnectivity();
  await page.click(`.pk-row[data-pk-id="${EXISTING_ID}"] [data-pk-act="revoke"]`);
  await waitText(/Passkey revoked/, '"Passkey revoked."');
  const ar = callsTo('POST', '/api/passkeys/assert/options');
  check(ar.length === 1 && ar[0].body.purpose === 'revoke' && ar[0].body.credential_id === EXISTING_ID,
    'revoke asked for a ceremony bound to that credential id', `revoke assert/options wrong: ${JSON.stringify(ar)}`);
  const del = callsTo('DELETE', '/api/passkeys/' + EXISTING_ID);
  check(del.length === 1 && del[0].body.proof && del[0].body.proof.ceremony_id === 'assert-revoke'
    && !('passcode' in del[0].body) && await noPasscodeModal(),
    'DELETE carried the assertion proof and no passcode', `DELETE wrong: ${JSON.stringify(del)}`);
  check(dialogs.length === 1 && /Revoke this passkey/.test(dialogs[0]),
    'the revoke confirm still asks first', `confirm dialogs: ${JSON.stringify(dialogs)}`);

  // ── 3. A refused assertion is shown, and the passcode is NOT offered ────
  credentials = [{ id: EXISTING_ID, label: 'First key', created_at: '2026-10-01T10:00:00+00:00',
    last_used_at: null, revoked_at: null }];
  reset();
  nextRegisterOptions = { status: 403, body: { error: 'invalid_assertion', message: 'The passkey was not accepted.' } };
  await openConnectivity();
  await page.click('[data-pk-act="enroll"]');
  await waitText(/The passkey was not accepted/, 'the server\'s refusal');
  check(await noPasscodeModal() && callsTo('POST', '/api/passkeys/register/finish').length === 0
    && (await page.evaluate(() => window.__pk.creates.length)) === 1,
    'a rejected assertion shows the server message, opens no passcode prompt, creates nothing',
    'refusal fell back to the passcode or kept going');

  // ── 4. A cancelled passkey prompt sends nothing ─────────────────────────
  reset();
  await page.evaluate(() => { window.__pk.mode = 'cancel'; });
  await openConnectivity();
  await page.click('[data-pk-act="enroll"]');
  await waitText(/cancelled or timed out\. Nothing was changed/, 'the cancelled-prompt message');
  check(callsTo('POST', '/api/passkeys/register/options').length === 0 && await noPasscodeModal(),
    'cancelling the passkey prompt sent no register/options and opened no passcode prompt',
    'a cancelled assertion still reached register/options');
  await page.evaluate(() => { window.__pk.mode = 'ok'; });

  // ── 5. Lost-all recovery: confirm, then passcode ────────────────────────
  reset();
  await openConnectivity();
  check(/Lost every passkey\?/.test(await text()) && !!(await page.$('[data-pk-act="recover"]')),
    'recovery is offered as a labelled secondary control', 'no recovery control');
  dialogAnswer = false;
  await page.click('[data-pk-act="recover"]');
  await page.waitForTimeout(300);
  check(callsTo('POST', '/api/passkeys/recover').length === 0 && await noPasscodeModal() && dialogs.length === 1
    && /every registered passkey/i.test(dialogs[0]),
    'declining the confirm stops before the passcode prompt', 'recovery went ahead after the confirm was declined');
  dialogAnswer = true;
  await page.click('[data-pk-act="recover"]');
  await answerPasscodeModal();
  await waitText(/Revoked 1 passkey\. The dashboard passcode applies again/, 'the recovery result');
  const rc = callsTo('POST', '/api/passkeys/recover');
  check(rc.length === 1 && rc[0].body.confirm === 'revoke-all-passkeys' && rc[0].body.passcode === 'smoke-dash-passcode',
    'recover sent the confirm string and the retyped passcode', `recover body wrong: ${JSON.stringify(rc)}`);

  // ── 6. Unavailable states, each with honest copy ────────────────────────
  const states = [
    ['passkey_vault_locked', 'Unlock your vault to use passkeys.', 'vault-open', null],
    ['passkey_vault_not_configured', 'Passkeys need a vault passphrase.', 'vault-set', null],
    ['passkey_store_unsigned', 'It may have been saved before signing existed, or put there from outside Clayrune; Clayrune cannot tell which. Reset it and enrol again.', null, 'reset'],
    ['passkey_store_tampered', 'The passkey registry failed its integrity check', null, 'reset'],
    ['passkey_store_unreadable', 'The passkey registry could not be read', null, 'reset'],
  ];
  for (const [code, copy, action, resettable] of states) {
    reset();
    listing = { status: 503, body: { error: code, message: 'server text' } };
    await openConnectivity();
    const t = (await text()).replace(/\s+/g, ' ');
    check(t.includes(copy), `${code}: shows "${copy.slice(0, 48)}..."`, `${code}: copy wrong: "${t}"`);
    check(!/[—–]/.test(t), `${code}: no em or en dash in the copy`, `${code}: dash in copy: "${t}"`);
    check(!(await page.$('[data-pk-act="enroll"]')), `${code}: no Add button`, `${code}: Add button shown`);
    if (action) check(!!(await page.$(`[data-pk-act="${action}"]`)), `${code}: has the ${action} button`, `${code}: ${action} button missing`);
    check(!!(await page.$('[data-pk-act="reset"]')) === !!resettable,
      resettable ? `${code}: offers Reset` : `${code}: does not offer Reset`, `${code}: Reset button wrong`);
  }

  // Lock recovery opens the shared unlock popup; unconfigured recovery opens setup.
  reset();
  await page.evaluate(() => {
    window.openVaultUnlock = () => window.__pk.vault.push('open');
    window.openVaultSetPassphrase = () => window.__pk.vault.push('set');
  });
  listing = { status: 503, body: { error: 'passkey_vault_locked' } };
  await openConnectivity();
  await page.click('[data-pk-act="vault-open"]');
  listing = { status: 503, body: { error: 'passkey_vault_not_configured' } };
  await openConnectivity();
  await page.click('[data-pk-act="vault-set"]');
  const vcalls = await page.evaluate(() => window.__pk.vault);
  check(JSON.stringify(vcalls) === '["open","set"]',
    '"Unlock vault" opens the unlock popup, "Set a vault passphrase" opens passphrase setup', `vault buttons called ${JSON.stringify(vcalls)}`);

  // A locked vault on a 200 listing (credentials readable, enrolment blocked) says the same.
  reset();
  listing = { status: 200, body: { available: true, rp_id: 'localhost', origin: ORIGIN, can_enroll_here: false,
    enroll_blocked_reason: 'vault_locked', enroll_blocked_message: 'raw server text', enrolled: true,
    credentials: [{ id: EXISTING_ID, label: 'First key', created_at: '2026-10-01', revoked_at: null }],
    active_count: 1, max_active: 10, needs_passkey_to_change: true } };
  await openConnectivity();
  check(/Unlock your vault to use passkeys\./.test(await text()) && !!(await page.$('[data-pk-act="vault-open"]')),
    'a locked vault on a normal listing shows the same Unlock copy', 'locked-vault listing not mapped');

  // ── 7. Reset: confirm, passcode, then a clean registry ──────────────────
  reset();
  listing = { status: 503, body: { error: 'passkey_store_tampered' } };
  await openConnectivity();
  dialogAnswer = false;
  await page.click('[data-pk-act="reset"]');
  await page.waitForTimeout(300);
  check(callsTo('POST', '/api/passkeys/reset').length === 0 && await noPasscodeModal()
    && /deletes every enrolled passkey/.test(dialogs[0] || ''),
    'declining the reset confirm sends nothing (the confirm says it deletes passkeys)', `reset went ahead: ${JSON.stringify(dialogs)}`);
  dialogAnswer = true;
  await page.click('[data-pk-act="reset"]');
  await answerPasscodeModal();
  await waitText(/The passkey registry was reset/, 'the reset result');
  const rs = callsTo('POST', '/api/passkeys/reset');
  check(rs.length === 1 && rs[0].body.confirm === 'reset-passkey-registry' && rs[0].body.passcode === 'smoke-dash-passcode',
    'reset sent the confirm string and the retyped passcode', `reset body wrong: ${JSON.stringify(rs)}`);
  check(/No passkeys registered/.test(await text()) && !!(await page.$('[data-pk-act="enroll"]')),
    'after the reset the panel is back to an empty, enrollable list', 'panel did not recover after reset');

  // A server refusal on reset (not the host) is reported.
  reset();
  listing = { status: 503, body: { error: 'passkey_store_tampered' } };
  nextReset = { status: 403, body: { error: 'host_only', message: 'Passkeys can only be managed from the host computer itself.' } };
  await openConnectivity();
  await page.click('[data-pk-act="reset"]');
  await answerPasscodeModal();
  await waitText(/only be managed from the host computer/, 'the host-only refusal');
  ok('a host-only refusal of the reset is shown as the server said it');

  pageErrors.length === 0 ? ok('no uncaught page errors throughout')
    : pageErrors.forEach((e) => fail('uncaught: ' + e));
} finally {
  await ctx.close();
  await browser.close();
}
if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('\npasskeys-assert: all checks passed');
