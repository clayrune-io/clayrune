#!/usr/bin/env node
/**
 * Desk v1 — reopen a saved X account, switch Connection to the developer app, and tick Post (MC-1114), the browser
 * half, against a fake server, at 1440 and 390 wide. static/js/desk-v1-connect-signin-reuse.js and the API Setup
 * screen (desk-v1-connect-api-step.js); the server half is tests/test_desk_connect_signin_reuse.py.
 *
 * The harness is desk-v1-connect-api-step.mjs's (its server, page and fixture screens); this file adds only the
 * answer to GET /api/desk/connect/signin-reuse/<account> and a Save that says the sign-in was kept.
 *
 *   1. Kept        the saved account holds a sign-in for the saved app: Setup says so, Continue goes straight to
 *                  Permissions (no "Authorize X"), and Save sends the draft with the account and NO held sign-in.
 *                  Nothing is signed in: no start-held, no browser pane. The Save is still its own passcode.
 *   2. Not kept    the server says no: Continue opens "Authorize X", as before; no note.
 *   3. Other app   a Client ID typed here is a different app: Authorize X, as before.
 *   4. New account never asks the reuse question (nothing saved to keep).
 *
 * RUN   cd tools/smoke && node desk-v1-connect-signin-reuse.mjs
 */
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { chromium } from 'playwright';
import { makeServer, newPage, toSetup, toReview, X_NEW } from './desk-v1-connect-api-step.mjs';

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));
const PASSCODE = 'right-passcode';
const X_EXISTING = { kind: 'account', identity: 'ronx', account: { id: 'acct-saved' } };
const logOf = (srv, re) => srv.log.filter((r) => re.test(r.path));
const prompt = (page, code) => page.fill('input[id^="hp-passcode-"]', code).then(() => page.click('.modal-content .btn-add'));

// Later routes win in Playwright; anything not matched falls back to the harness's server.
async function answer(page, srv, reusable) {
  await page.route('**/api/desk/connect/signin-reuse/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    srv.log.push({ method: 'GET', path, body: null });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ reusable, reason: reusable ? null : 'this account is not signed in with a working sign-in' }) });
  });
  await page.route('**/api/desk/connect/commit', (route) => {
    let body = null;
    try { body = route.request().postDataJSON(); } catch (_) { /* none */ }
    srv.log.push({ method: 'POST', path: '/api/desk/connect/commit', body });
    if (!body || body.passcode !== PASSCODE) return route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ error: 'bad_passcode' }) });
    return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ ok: true, duplicate: false, reused_signin: !body.draft.held, service: { id: 'x', label: 'X' }, method: body.draft.method, account_id: 'acct-saved', stored: [], status: { state: 'saved' } }) });
  });
}

async function run(browser, width, height) {
  console.log(`\n== ${width}px ==`);

  // ── 1: the saved sign-in is kept ──────────────────────────────────────
  {
    const srv = makeServer(); srv.present = true;
    const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
    await answer(page, srv, true);
    await toSetup(page, 'x.com', X_EXISTING, 'api', true);
    await page.waitForSelector('[data-api-reuse]', { timeout: 4000 });
    check(/already signed in with this app/.test(await page.textContent('[data-api-reuse]')), 'Setup says the account is already signed in with this app', 'no reuse note');
    check(logOf(srv, /signin-reuse\/acct-saved$/).length === 1, 'the question is asked once, for the saved account', 'reuse asks: ' + logOf(srv, /signin-reuse/).length);
    await page.click('[data-cfw-primary]');
    await page.waitForSelector('[data-cfw][data-cfw-step="permissions"]', { timeout: 4000 });
    check((await page.$$('[data-cfh], [data-cfh-start]')).length === 0, 'Continue skips "Authorize X": no sign-in view', 'the authorize view opened');
    await page.click('[data-cfw-primary]');
    await page.waitForSelector('[data-cfw][data-cfw-step="review"]', { timeout: 4000 });
    await page.click('[data-cfw-primary]');
    await page.waitForSelector('input[id^="hp-passcode-"]');
    check(logOf(srv, /connect\/commit/).length === 0, 'Save is still its own passcode prompt', 'commit sent before the passcode');
    await prompt(page, PASSCODE);
    await page.waitForFunction(() => window.__fx.saves.length === 1, null, { timeout: 3000 });
    const d = logOf(srv, /connect\/commit/)[0].body.draft;
    check(d.account_id === 'acct-saved' && !d.held && !d.new_login, 'the draft names the saved account and carries no held sign-in', 'draft: ' + JSON.stringify({ ...d, fields: Object.keys(d.fields) }));
    check(await page.evaluate(() => window.__fx.saves[0].ok === true && window.__fx.saves[0].result.reused_signin === true), 'success carries reused_signin', 'save result: ' + JSON.stringify(await page.evaluate(() => window.__fx.saves[0])));
    check(logOf(srv, /start-held|verify|purpose|publish/).length === 0 && (await page.evaluate(() => window.__fx.panes.length)) === 0, 'nothing was signed in: no start-held, no browser pane, no probe', 'extra requests: ' + logOf(srv, /start-held|verify|purpose|publish/).map((r) => r.path));
    check(pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e)).length === 0, 'no page errors', 'page errors: ' + pageErrors.join(' | '));
    await ctx.close();
  }

  // ── 2: the server says no ─────────────────────────────────────────────
  {
    const srv = makeServer(); srv.present = true;
    const { ctx, page } = await newPage(browser, { srv, width, height });
    await answer(page, srv, false);
    await toSetup(page, 'x.com', X_EXISTING, 'api', true);
    await page.waitForSelector('[data-cfa-host]');
    await page.waitForFunction(() => window.DeskV1ConnectSigninReuse && !window.DeskV1ConnectSigninReuse.reusable('acct-saved'));
    check((await page.$$('[data-api-reuse]')).length === 0, 'no note when the sign-in cannot be kept', 'a note was shown');
    await page.click('[data-cfw-primary]');
    await page.waitForSelector('[data-cfh-start]', { timeout: 4000 });
    check((await page.textContent('[data-cfw-title]')).trim() === 'Authorize X', 'Continue opens "Authorize X", as before', 'title: ' + await page.textContent('[data-cfw-title]'));
    await ctx.close();
  }

  // ── 3: a different app typed here ─────────────────────────────────────
  {
    const srv = makeServer();
    const { ctx, page } = await newPage(browser, { srv, width, height });
    await answer(page, srv, true);
    await toSetup(page, 'x.com', X_EXISTING, 'api', true);
    await page.waitForSelector('[data-cfa-field="client_id"]');
    await page.fill('[data-cfa-field="client_id"]', 'another-app-client-id');
    await page.click('[data-cfw-primary]');
    await page.waitForSelector('[data-cfh-start]', { timeout: 4000 });
    check(logOf(srv, /start-held/).length === 0, 'a typed Client ID is another app: Authorize X opens, nothing started yet', 'start-held went out early');
    await ctx.close();
  }

  // ── 4: a new account has nothing to keep ──────────────────────────────
  {
    const srv = makeServer();
    const { ctx, page } = await newPage(browser, { srv, width, height });
    await answer(page, srv, true);
    await toSetup(page, 'x.com', X_NEW);
    await page.waitForSelector('[data-cfa-host]');
    await page.fill('[data-cfa-field="client_id"]', 'client-id-abc-123');
    await page.click('[data-cfw-primary]');
    await page.waitForSelector('[data-cfh-start]', { timeout: 4000 });
    check(logOf(srv, /signin-reuse/).length === 0, 'a new account never asks the reuse question', 'reuse asked for a new account');
    await ctx.close();
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const browser = await chromium.launch();
  try {
    await run(browser, 1440, 900);
    await run(browser, 390, 844);
  } catch (e) {
    fail('smoke aborted: ' + (e && e.stack ? e.stack : e));
  } finally {
    await browser.close();
  }
  console.log(bad ? `\n${bad} check(s) failed` : '\nAll connect-signin-reuse checks passed');
  process.exit(bad ? 1 : 0);
}
