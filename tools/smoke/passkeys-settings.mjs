#!/usr/bin/env node
/**
 * Settings -> Connectivity -> "Passkeys" (docs/PASSKEYS_SPEC.md, slice 1).
 *
 * WHY THIS EXISTS
 * ----------------
 * Pins the browser half of passkey enrollment: the panel lists credentials,
 * "Add a passkey" goes through the passcode modal, calls
 * navigator.credentials.create, and posts a well-formed credential to the
 * finish route; "Revoke" asks, goes through the passcode modal and sends
 * DELETE; a caller the server says cannot enroll sees the reason and no button.
 *
 * Hermetic, same idiom as settings-backup-schedule.mjs: the SPA is served from
 * THIS checkout and the passkey routes are canned in memory, so it proves the
 * panel and the bytes a browser produces, NOT the server's verification (that is
 * tests/test_passkeys_*.py). The page runs at http://localhost:5199, a secure
 * context, so Chrome exposes navigator.credentials. A Chrome DevTools virtual
 * authenticator answers the create() call; it is a software authenticator, so
 * this is not evidence about any real device or biometric.
 *
 * Set PASSKEY_SMOKE_DUMP=<file> to write the credential the browser produced and
 * the challenge it answered, for an offline check against the Python verifier.
 *
 * RUN: node tools/smoke/passkeys-settings.mjs
 */
import { readFileSync, existsSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { randomBytes } from 'node:crypto';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const STATIC_ROOT = resolve(REPO_ROOT, 'static');
const ORIGIN = 'http://localhost:5199';

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const b64url = (buf) => Buffer.from(buf).toString('base64url');

let canEnroll = true;
let credentials = [];
let pendingCeremony = null;          // { id, challenge }
let finishBodies = [];
let optionsBodies = [];
let deleteCalls = [];

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1000, height: 900 } });
const page = await ctx.newPage();
await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

const cdp = await ctx.newCDPSession(page);
await cdp.send('WebAuthn.enable');
await cdp.send('WebAuthn.addVirtualAuthenticator', { options: {
  protocol: 'ctap2', transport: 'internal', hasResidentKey: true, hasUserVerification: true,
  isUserVerified: true, automaticPresenceSimulation: true } });

page.on('dialog', (d) => d.accept().catch(() => {}));   // the Revoke confirm()

await page.route('**/*', async (route) => {
  const req = route.request();
  const url = new URL(req.url());
  const path = url.pathname;
  const method = req.method();
  const json = (body, status = 200) => route.fulfill({
    status, contentType: 'application/json', body: JSON.stringify(body) });

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

  if (path === '/api/passkeys' && method === 'GET') return json({
    available: true, rp_id: 'localhost', origin: ORIGIN, can_enroll_here: canEnroll,
    enroll_blocked_reason: canEnroll ? null : 'not_loopback_peer',
    enroll_blocked_message: canEnroll ? '' : 'Passkeys can only be added or revoked from the host computer itself.',
    enrolled: credentials.length > 0, credentials, active_count: credentials.filter((c) => !c.revoked_at).length,
    max_active: 10 });

  if (path === '/api/passkeys/register/options' && method === 'POST') {
    const body = JSON.parse(req.postData() || '{}');
    optionsBodies.push(body);
    const challenge = randomBytes(32);
    pendingCeremony = { id: 'cer-' + randomBytes(6).toString('hex'), challenge: b64url(challenge) };
    return json({ ceremony_id: pendingCeremony.id, expires_in: 120, options: {
      rp: { name: 'Clayrune', id: 'localhost' },
      user: { id: b64url(randomBytes(32)), name: 'clayrune-owner', displayName: 'Clayrune owner' },
      challenge: pendingCeremony.challenge,
      pubKeyCredParams: [{ type: 'public-key', alg: -7 }, { type: 'public-key', alg: -257 }],
      timeout: 60000, attestation: 'none',
      authenticatorSelection: { residentKey: 'preferred', userVerification: 'required' },
      excludeCredentials: credentials.filter((c) => !c.revoked_at).map((c) => ({ type: 'public-key', id: c.id })) } });
  }

  if (path === '/api/passkeys/register/finish' && method === 'POST') {
    const body = JSON.parse(req.postData() || '{}');
    finishBodies.push(body);
    credentials.push({ id: body.credential.id, label: optionsBodies.at(-1).label || 'Passkey 2026-10-04',
      rp_id: 'localhost', created_at: '2026-10-04T10:00:00+00:00', last_used_at: null, revoked_at: null,
      transports: body.credential.response.transports || [] });
    return json({ ok: true, credential: credentials.at(-1) });
  }

  if (path.startsWith('/api/passkeys/') && method === 'DELETE') {
    const id = decodeURIComponent(path.slice('/api/passkeys/'.length));
    deleteCalls.push({ id, body: JSON.parse(req.postData() || '{}') });
    const c = credentials.find((x) => x.id === id);
    if (c) c.revoked_at = '2026-10-04T11:00:00+00:00';
    return json({ ok: true, credential: c });
  }
  return route.abort();
});

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

try {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openSettings === 'function', { timeout: 20000 });
  const hasApi = await page.evaluate(() => !!(navigator.credentials && window.PublicKeyCredential));
  hasApi ? ok('page is a secure context: navigator.credentials is available')
    : fail('navigator.credentials missing (not a secure context?)');

  // ── 1. Empty list, Add button present ───────────────────────────────────
  await openConnectivity();
  const empty = await page.textContent('#passkeys-host');
  /No passkeys registered/.test(empty) ? ok('empty state: "No passkeys registered"')
    : fail(`empty state text wrong: "${empty}"`);
  (await page.$('[data-pk-act="enroll"]')) ? ok('"Add a passkey" is offered when the server says this caller can enroll')
    : fail('Add button missing');

  // ── 2. Enroll through the passcode modal and the virtual authenticator ──
  await page.fill('#passkeys-label', 'Smoke laptop');
  await page.click('[data-pk-act="enroll"]');
  await answerPasscodeModal();
  await page.waitForFunction(() => /Passkey added/.test(document.getElementById('passkeys-host')?.textContent || ''),
    { timeout: 10000 });
  ok('enroll: panel reports "Passkey added."');
  (optionsBodies.length === 1 && optionsBodies[0].passcode === 'smoke-dash-passcode' && optionsBodies[0].label === 'Smoke laptop')
    ? ok('options request carried the retyped passcode and the label')
    : fail(`options body wrong: ${JSON.stringify(optionsBodies)}`);

  const fin = finishBodies[0];
  if (!fin) fail('finish route was never called');
  else {
    const cred = fin.credential;
    const client = JSON.parse(Buffer.from(cred.response.clientDataJSON, 'base64url').toString('utf8'));
    (fin.ceremony_id === pendingCeremony.id) ? ok('finish carries the ceremony id from options') : fail('ceremony id mismatch');
    (client.type === 'webauthn.create' && client.challenge === pendingCeremony.challenge && client.origin === ORIGIN)
      ? ok(`clientDataJSON: type webauthn.create, the issued challenge, origin ${ORIGIN}`)
      : fail(`clientDataJSON wrong: ${JSON.stringify(client)}`);
    (cred.type === 'public-key' && cred.id === cred.rawId && cred.id.length > 10 && !/[+/=]/.test(cred.id))
      ? ok('credential id is base64url and equals rawId') : fail(`credential id shape wrong: ${cred.id}`);
    const att = Buffer.from(cred.response.attestationObject, 'base64url');
    att.length > 40 ? ok(`attestationObject present (${att.length} bytes)`) : fail('attestationObject missing');
    Array.isArray(cred.response.transports) ? ok(`transports reported: ${JSON.stringify(cred.response.transports)}`)
      : fail('transports not an array');
    if (process.env.PASSKEY_SMOKE_DUMP) {
      writeFileSync(process.env.PASSKEY_SMOKE_DUMP, JSON.stringify({ challenge: pendingCeremony.challenge,
        origin: ORIGIN, rp_id: 'localhost', credential: cred }));
    }
  }
  const row = await page.$('.pk-row[data-pk-id] .pk-name');
  (row && (await row.textContent()) === 'Smoke laptop') ? ok('list shows the new passkey by name')
    : fail('new passkey not listed');

  // ── 3. Revoke ───────────────────────────────────────────────────────────
  await page.click('[data-pk-act="revoke"]');
  await answerPasscodeModal();
  await page.waitForFunction(() => /Passkey revoked/.test(document.getElementById('passkeys-host')?.textContent || ''),
    { timeout: 10000 });
  (deleteCalls.length === 1 && deleteCalls[0].id === finishBodies[0].credential.id
    && deleteCalls[0].body.passcode === 'smoke-dash-passcode')
    ? ok('revoke sent DELETE for that credential with the retyped passcode') : fail(`DELETE wrong: ${JSON.stringify(deleteCalls)}`);
  const struck = await page.$('.pk-row.pk-revoked');
  (struck && !(await page.$('[data-pk-act="revoke"]'))) ? ok('revoked row is struck through and has no Revoke button')
    : fail('revoked row not rendered as revoked');

  // ── 4. A caller the server refuses sees why, and no Add button ──────────
  canEnroll = false;
  await openConnectivity();
  const blocked = await page.textContent('#passkeys-blocked');
  /host computer/.test(blocked) ? ok(`blocked caller sees the server's reason: "${blocked.trim()}"`)
    : fail(`blocked message wrong: "${blocked}"`);
  !(await page.$('[data-pk-act="enroll"]')) ? ok('no Add button for a caller that cannot enroll') : fail('Add button shown to a blocked caller');

  pageErrors.length === 0 ? ok('no uncaught page errors throughout')
    : pageErrors.forEach((e) => fail('uncaught: ' + e));
} finally {
  await ctx.close();
  await browser.close();
}
if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('\npasskeys-settings: all checks passed');
