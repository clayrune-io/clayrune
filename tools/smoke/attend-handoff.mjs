#!/usr/bin/env node
/**
 * MC-994 (backlog fb822042) — the attended-handoff control.
 *
 * A dispatched/scheduled chat is fenced by steward/fence.py even when a human
 * is actively reading it (2026-09-28 incident: a scheduled Dave handed its
 * thread to a new chat via POST /agent/dispatch at Ron's request, and the
 * fence blocked `git push origin master` even after Ron approved it — "this
 * conversation is not supposed to be guarded"). The fix is a human-click
 * control in the chat header: a "Guarded" pill on any chat whose trigger_type
 * is in the unattended set, with a click that POSTs .../attend-once.
 *
 * MC-994 follow-up (2026-09-28, Ron: "every time the gate opens only for
 * that single iteration, that is the safer approach"): the click grants a
 * ONE-SHOT pass, not a permanent unlock — trigger_type stays unattended the
 * whole time, and the pill shows "Allowed once (10 min)" only while the
 * pass is open, reverting to Guarded once it's spent or expires.
 *
 * MC-994 follow-up, Fenn finding 1 (2026-09-28): the click no longer fires
 * the grant directly — it opens a "Allow once" modal that re-collects the
 * local dashboard passcode (a forgeable Origin-header check was the only
 * gate before), so this smoke also drives that modal before the pill flips.
 *
 * This smoke stubs the server entirely (page.route, no real MC process) —
 * the point is the CLIENT render + click wiring, not the server route (that
 * has its own request-level tests in tests/test_agent_routes.py). Checked at
 * both desktop (1400px) and mobile (390px), since the pill lives in the same
 * shared header markup both layouts render (conversation.js's tabContent).
 *
 * 2026-10-09 (Ron): the modal also offers "Allow 10 min" (mode: window). The
 * pill then reads "Open until HH:MM" with a Re-arm control that POSTs
 * .../attend-once/revoke (no passcode) and drops back to Guarded. Set
 * ATTEND_SHOTS=<dir> to write the modal / open-pill screenshots there.
 *
 * RUN: cd tools/smoke && node attend-handoff.mjs
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const PROJECTS_JSON = readFileSync(resolve(__dirname, 'fixtures', 'projects.json'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_alpha';

const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));

const SHOTS = process.env.ATTEND_SHOTS || '';

const DAVE = { name: 'dave', agent_name: 'Dave', scope: 'global', avatar: 'fig:guard' };

async function runOnce(viewport, label) {
  console.log(`\n── ${label} (${viewport.width}x${viewport.height}) ──`);
  const ok = (m) => console.log('  ✓ ' + m);
  let bad = 0;
  const fail = (m) => { console.error('  ✗ ' + m); bad++; };
  const check = (cond, pass, failMsg) => (cond ? ok(pass) : fail(failMsg));
  let attendOnceCalls = 0;
  let revokeCalls = 0;
  let lastSentMode;
  let passExpiresAt = null;
  let passMode = 'once';

  const SESSIONS = () => ({
    mcDispatch: {
      session_id: 'mcDispatch', status: 'idle',
      trigger_type: 'dispatch',
      claude_session_id: 'csidDispatch', started_at: '2026-09-28T21:00:00Z',
      task: 'Please push the fix we discussed', character: DAVE,
      log_lines: ['> Ron: please push the fix we discussed', 'On it.'],
      attend_once_pass: passExpiresAt ? { open: true, expires_at: passExpiresAt, mode: passMode } : null,
    },
  });

  let browser, exitCode = 1;
  try {
    browser = await chromium.launch();
    const page = await (await browser.newContext({ viewport })).newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

    await page.route('**/*', (route) => {
      const req = route.request();
      const url = new URL(req.url());
      const path = url.pathname;
      const json = (o) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(o) });
      if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
      const hit = STATIC[path];
      if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
      if (path === '/api/projects') {
        const patched = JSON.parse(PROJECTS_JSON);
        patched[0].project_path = '/smoke/alpha';
        return json(patched);
      }
      if (path === '/api/config') return json({});
      if (/\/agent\/status$/.test(path)) return json({ sessions: Object.values(SESSIONS()) });
      if (req.method() === 'POST' && /\/agent\/mcDispatch\/attend-once$/.test(path)) {
        attendOnceCalls++;
        const sent = JSON.parse(req.postData() || '{}');
        if (sent.passcode !== 'smoke-test-passcode') {
          return route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ error: 'bad_passcode' }) });
        }
        lastSentMode = sent.mode;
        passMode = sent.mode === 'window' ? 'window' : 'once';
        passExpiresAt = new Date(Date.now() + 10 * 60 * 1000).toISOString();
        return json({ ok: true, session_id: 'mcDispatch', status: 'granted', expires_at: passExpiresAt, mode: passMode });
      }
      if (req.method() === 'POST' && /\/agent\/mcDispatch\/attend-once\/revoke$/.test(path)) {
        revokeCalls++;
        const was = passExpiresAt ? passMode : null;
        passExpiresAt = null;
        return json({ ok: true, session_id: 'mcDispatch', status: was ? 'revoked' : 'none_open', mode: was });
      }
      return route.abort();
    });

    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    // '#projects-col .card' (the desktop grid tile) is CSS-hidden at the
    // mobile breakpoint, so wait on the boot signal both layouts share
    // instead of a desktop-only selector.
    await page.waitForFunction(() => typeof window.openProjectModal === 'function', null, { timeout: 15000 });

    await page.evaluate((pid) => {
      conversationsCache[pid] = [
        { claude_session_id: 'csidDispatch', mc_session_id: 'mcDispatch', live: true, status: 'idle',
          label: 'Please push the fix we discussed',
          character: { name: 'dave', agent_name: 'Dave', scope: 'global', avatar: 'fig:guard' },
          trigger_type: 'dispatch', turns: 2, mtime: 100 },
      ];
      agentLogCache[pid] = [];
      openProjectModal(pid);
      setRailMode(pid, 'channel');
    }, PID);
    const sc = `.modal-window[data-modal-id="${PID}"] `;
    await page.waitForSelector(sc + '.channel-row', { timeout: 10000 });

    await page.locator(`${sc}.channel-row`).filter({ hasText: 'Dave' }).first().click();
    await page.waitForFunction(() => agentStatusCache['mcDispatch'] && agentStatusCache['mcDispatch'].triggerType === 'dispatch', null, { timeout: 5000 });

    const pill = () => page.locator('.attend-pill');
    await page.waitForSelector('.attend-pill.guarded', { timeout: 5000 });
    check(await pill().count() === 1, 'exactly one attend pill rendered',
      `expected 1 attend pill, got ${await pill().count()}`);
    check((await pill().first().innerText()).toLowerCase().includes('guarded'),
      'pill reads Guarded before the click', `pill text: ${await pill().first().innerText()}`);
    // Ron 2026-10-10: the pill offers both modes, so it must not name just one.
    check(/unlock/i.test(await pill().first().innerText()) && !/allow once/i.test(await pill().first().innerText()),
      'pill CTA reads Unlock, not "Allow once"', `pill text: ${await pill().first().innerText()}`);
    const guardedTitle = await pill().first().getAttribute('title');
    check(/browser-pane/i.test(guardedTitle) && /HTTP/.test(guardedTitle) && /shell/.test(guardedTitle) && /fresh chat/i.test(guardedTitle),
      'guarded tooltip states unlock limits and the fresh-chat path', `guarded tooltip: ${guardedTitle}`);

    await pill().first().click();
    // Passcode gate (Fenn finding 1): the click opens a modal instead of
    // firing the grant directly — punch through it like a human would.
    const modalId = '__attend-once_mcDispatch';
    await page.waitForSelector(`.modal-window[data-modal-id="${modalId}"]`, { timeout: 5000 });
    const limits = page.locator(`.modal-window[data-modal-id="${modalId}"] .attend-unlock-limits`);
    const limitsText = await limits.innerText();
    const limitsRect = await limits.boundingBox();
    check(await limits.isVisible() && limitsRect && limitsRect.x >= 0 && limitsRect.y >= 0
      && limitsRect.x + limitsRect.width <= viewport.width && limitsRect.y + limitsRect.height <= viewport.height
      && /browser-pane/.test(limitsText) && /HTTP/.test(limitsText) && /curl/.test(limitsText)
      && /chaining/.test(limitsText) && /shell symbols/.test(limitsText) && /interpreter/.test(limitsText)
      && /fresh chat yourself/.test(limitsText) && !limitsText.includes('\u2014'),
      'unlock caveat is visible inside the viewport with actual limits and fresh-chat guidance', `caveat: ${limitsText}`);
    // Wrong passcode: the server refuses, the modal says so, nothing flips.
    await page.fill(`#ao-passcode-mcDispatch`, 'wrong-passcode');
    await page.click(`.modal-window[data-modal-id="${modalId}"] button.btn-add`);
    await page.waitForFunction(() => /wrong/i.test((document.getElementById('ao-status-mcDispatch') || {}).textContent || ''), null, { timeout: 5000 });
    check(await page.locator('.attend-pill.guarded').count() === 1 && await page.locator('.attend-pill.attended').count() === 0,
      'wrong passcode leaves the pill Guarded', 'pill flipped on a wrong passcode');
    check(await page.locator(`.modal-window[data-modal-id="${modalId}"]`).count() === 1,
      'modal stays open after a wrong passcode', 'modal closed on a wrong passcode');
    // The modal offers both modes; Enter / the primary button stay "once".
    check(await page.locator(`.modal-window[data-modal-id="${modalId}"] button.attend-window-btn`).innerText() === 'Allow 10 min',
      'modal offers an "Allow 10 min" button', 'no Allow 10 min button in the modal');
    check(await page.locator(`.modal-window[data-modal-id="${modalId}"] button.btn-add`).innerText() === 'Allow once',
      'primary button is still "Allow once"', 'primary button text changed');
    // .btn-secondary used to have no rule, so Cancel / Allow 10 min rendered
    // as bare browser buttons (light grey face) on the dark modal.
    const secBg = await page.locator(`.modal-window[data-modal-id="${modalId}"] button.attend-window-btn`)
      .evaluate((b) => getComputedStyle(b).backgroundColor);
    check(secBg !== 'rgb(239, 239, 239)' && secBg !== 'rgb(240, 240, 240)',
      'Allow 10 min is styled, not a bare browser button', `background ${secBg}`);
    if (SHOTS) {
      await page.fill(`#ao-passcode-mcDispatch`, 'xxxxxxxx');
      await page.screenshot({ path: `${SHOTS}/attend_modal_${viewport.width}.png` });
    }
    await page.fill(`#ao-passcode-mcDispatch`, 'smoke-test-passcode');
    await page.press(`#ao-passcode-mcDispatch`, 'Enter');
    await page.waitForFunction(() => document.querySelector('.attend-pill.attended'), null, { timeout: 5000 });
    check(lastSentMode === 'once', `Enter grants mode once (sent ${lastSentMode})`, `Enter sent mode ${lastSentMode}`);
    check(attendOnceCalls === 2, `one refused + one granted POST .../attend-once (got ${attendOnceCalls})`, `attend-once POST count wrong: ${attendOnceCalls}`);
    check(await page.locator('.attend-pill.guarded').count() === 0, 'guarded pill is gone after the click',
      'guarded pill still present after the click');
    check((await page.locator('.attend-pill.attended').first().innerText()).toLowerCase().includes('allowed once'),
      'pill reads Allowed once after the click', `pill text: ${await page.locator('.attend-pill.attended').first().innerText()}`);
    check(await page.evaluate(() => agentStatusCache['mcDispatch'].triggerType) === 'dispatch',
      'client-side triggerType stays dispatch — the pass never touches it',
      `agentStatusCache triggerType: ${await page.evaluate(() => agentStatusCache['mcDispatch'].triggerType)}`);
    check(await page.evaluate(() => !!(agentStatusCache['mcDispatch'].attendOncePass && agentStatusCache['mcDispatch'].attendOncePass.open)),
      'client-side attendOncePass marked open after the click',
      `attendOncePass: ${await page.evaluate(() => JSON.stringify(agentStatusCache['mcDispatch'].attendOncePass))}`);

    // Fenn finding 6: the fence spends (or the clock expires) the pass
    // server-side; the idle chat's bounded poll must flip the pill back.
    passExpiresAt = null;
    const flippedBack = await page.waitForFunction(() => document.querySelector('.attend-pill.guarded')
      && !document.querySelector('.attend-pill.attended'), null, { timeout: 9000 }).then(() => true, () => false);
    check(flippedBack, 'spent/expired pass flips the pill back to Guarded via the status poll',
      'pill still shows Allowed once 9s after the server dropped the pass');

    // ── "Allow 10 min": window pill, Re-arm, back to Guarded ──────────────
    await page.waitForSelector('.attend-pill.guarded', { timeout: 5000 });
    await page.locator('.attend-pill.guarded').first().click();
    await page.waitForSelector(`.modal-window[data-modal-id="${modalId}"]`, { timeout: 5000 });
    await page.fill(`#ao-passcode-mcDispatch`, 'smoke-test-passcode');
    await page.click(`.modal-window[data-modal-id="${modalId}"] button.attend-window-btn`);
    await page.waitForSelector('.attend-pill.attend-window', { timeout: 5000 });
    check(lastSentMode === 'window', `Allow 10 min sends mode window (sent ${lastSentMode})`, `sent mode ${lastSentMode}`);
    const winText = await page.locator('.attend-pill.attend-window').first().innerText();
    check(/^\S?\s*Open until \d{1,2}:\d{2}/.test(winText.trim()) && /Re-arm/.test(winText),
      `window pill reads "Open until HH:MM" with a Re-arm control (${winText.replace(/\s+/g, ' ')})`,
      `window pill text: ${winText}`);
    check(await page.locator('.attend-pill.guarded').count() === 0, 'no Guarded pill while the window is open',
      'Guarded pill rendered during an open window');
    // The pill must fit the 390px header without horizontal overflow.
    const fits = await page.evaluate(() => {
      const el = document.querySelector('.attend-pill.attend-window');
      const r = el.getBoundingClientRect();
      return r.left >= 0 && r.right <= window.innerWidth && document.documentElement.scrollWidth <= window.innerWidth;
    });
    check(fits, 'window pill fits inside the viewport, no horizontal scroll', 'window pill overflows the viewport');
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/attend_window_pill_${viewport.width}.png` });
    // Still open after a status poll (server truth carries mode).
    await page.waitForTimeout(5600);
    check(await page.locator('.attend-pill.attend-window').count() === 1, 'window pill survives the status poll',
      'window pill vanished after a poll');
    await page.locator('.attend-pill.attend-window .attend-rearm').click();
    await page.waitForSelector('.attend-pill.guarded', { timeout: 5000 });
    check(revokeCalls === 1, `Re-arm POSTs revoke once (got ${revokeCalls})`, `revoke POST count: ${revokeCalls}`);
    check(await page.locator('.attend-pill.attended').count() === 0, 'Re-arm returns the pill to Guarded',
      'attended pill still shown after Re-arm');

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    uncaught.length ? uncaught.forEach((e) => fail('uncaught page error: ' + e)) : ok('no uncaught page errors');
    exitCode = bad ? 1 : 0;
  } catch (err) {
    console.error('❌ harness error:', err && err.stack ? err.stack : err);
    exitCode = 1;
  } finally {
    if (browser) await browser.close().catch(() => {});
  }
  return exitCode;
}

let overallBad = 0;
overallBad += await runOnce({ width: 1440, height: 900 }, 'desktop');
overallBad += await runOnce({ width: 390, height: 844 }, 'mobile 390px');

console.log(overallBad ? `\nFAILED` : '\nALL PASS');
process.exit(overallBad ? 1 : 0);
