#!/usr/bin/env node
/**
 * First-run chooser sign-in, against a REAL second Clayrune server instance
 * (not the http.createServer stub onboarding-multiselect-browser.mjs uses) —
 * real index.html, real static/js/*.js, real Flask routing for everything
 * except the auth surface itself.
 *
 * W6 follow-up (Dave, 2026-09-18): show each selected vendor row runs its
 * REAL sign-in route and flips to "signed in", covering claude/gemini/qwen
 * end to end, and codex for state DETECTION only (no sign-in click — Codex
 * is out of allowance until 2026-09-24; install/auth-detection code paths
 * don't spend it, but this smoke still never clicks its Sign in button).
 *
 * Isolation: MC_PORT + disposable MC_DATA_DIR, and — the mistake disclosed
 * in docs/_journal/provider-live/w6-smoke-evidence.md — MC_REMOTE_ENABLED=0
 * is now set BEFORE the child process starts, so this run makes zero
 * requests to clayrune.io (verified below by aborting and recording any
 * request whose host isn't 127.0.0.1/localhost).
 *
 * Faked at the HTTP layer via Playwright route interception, never hitting
 * the real route handlers:
 *   - GET  /api/agent/providers                        (controls the
 *     before/after auth_status the UI reads — real health_check() would
 *     require an actual completed login to observe a transition)
 *   - POST /api/agent/provider/<name>/login-launch      (would otherwise
 *     open a REAL OS terminal and invoke the real CLI's login flow)
 * Everything else — index.html, static/js/*.js, static/css/*.css, /api/config,
 * /api/projects, /api/characters, /api/walkthrough/sample-project — is served
 * by the REAL running server, proving genuine Flask asset delivery + routing,
 * which the fixture-only stub smoke does not exercise.
 *
 * RUN
 *   cd tools/smoke && node first-run-signin-flow.mjs
 * Exit 0 = all assertions hold; 1 = a real regression, a page error, or an
 * attempted non-loopback network call.
 */
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, appendFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const PORT = Number(process.env.MC_SMOKE_PORT || 5311);
// Unique per run, never reused: a prior run's saveSetting('default_provider',
// ...) persists into config.json, which then makes the provider-choice step
// auto-skip on the NEXT run against the same dir (its own skip condition —
// reproduced directly: title jumped straight to "Choose your level"). A
// fixed dir + rmSync-before-run also hit Windows EPERM on a back-to-back
// re-run (a file handle briefly outliving the killed process) even with
// retries; a fresh dir sidesteps both problems. _scratch/ is gitignored —
// stale runs' dirs are cheap to leave behind.
const RUN_ID = `${Date.now()}-${process.pid}`;
const DATA_DIR = resolve(REPO_ROOT, '_scratch', `mc_signin_smoke_data_${RUN_ID}`);
const LOG_FILE = resolve(REPO_ROOT, '_scratch', `mc_signin_smoke_server_${RUN_ID}.log`);
const PYTHON = resolve(REPO_ROOT, '.venv', 'Scripts', 'python.exe');

const registerURL = process.env.MC_TEST_PROCESS_REGISTER_URL || 'http://localhost:5199/api/processes/register';
const registerProject = process.env.MC_TEST_PROCESS_PROJECT || 'mission_control';
async function registerOwned(pid, name, command) {
  try {
    const response = await fetch(registerURL, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ pid, name, project_id: registerProject, command }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok || body.ok !== true) console.error(`process registration failed for pid ${pid}: ${response.status}`);
  } catch (e) { console.error(`process registration unreachable for pid ${pid}: ${e}`); }
}

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

// Mutable fixture — the whole point of faking this endpoint is a controllable
// before/after transition, not a snapshot of whatever's really installed on
// the box running this smoke.
const providers = [
  { name: 'claude', display_name: 'Claude Code', installed: true, auth_status: 'not_logged_in', in_use: false, default: false, install_hint: '' },
  { name: 'gemini', display_name: 'Gemini CLI', installed: true, auth_status: 'not_logged_in', in_use: false, default: false, install_hint: '' },
  { name: 'qwen', display_name: 'Qwen Code', installed: true, auth_status: 'not_logged_in', in_use: false, default: false, install_hint: '' },
  { name: 'codex', display_name: 'Codex CLI', installed: true, auth_status: 'not_logged_in', in_use: false, default: false, install_hint: '' },
];
const loginLaunchCalls = [];
const externalRequests = [];

let serverProc, browser, exitCode = 1;
try {
  if (!existsSync(PYTHON)) throw new Error(`worktree venv python not found: ${PYTHON}`);
  mkdirSync(DATA_DIR, { recursive: true });

  serverProc = spawn(PYTHON, ['server.py'], {
    cwd: REPO_ROOT,
    env: {
      ...process.env,
      MC_PORT: String(PORT),
      MC_DATA_DIR: DATA_DIR,
      MC_REMOTE_ENABLED: '0', // set BEFORE the child starts — the isolation this doc requires
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  serverProc.stdout.on('data', (d) => appendFileSync(LOG_FILE, d));
  serverProc.stderr.on('data', (d) => appendFileSync(LOG_FILE, d));
  // spawn() without shell:true gives the REAL python.exe PID directly — no
  // bash-$!-vs-actual-PID mismatch (measured in the same session: bash's `&
  // echo $!` reported a PID that owned nothing; the real listener was found
  // only via Get-NetTCPConnection).
  await registerOwned(serverProc.pid, 'W6 signin-flow smoke: second MC instance', `MC_PORT=${PORT} MC_DATA_DIR=${DATA_DIR} MC_REMOTE_ENABLED=0 ${PYTHON} server.py`);

  const origin = `http://127.0.0.1:${PORT}`;
  let up = false;
  for (let i = 0; i < 30 && !up; i++) {
    try { const r = await fetch(origin + '/api/config'); if (r.ok) up = true; } catch (e) { /* not up yet */ }
    if (!up) await new Promise((r) => setTimeout(r, 500));
  }
  if (!up) throw new Error(`second instance on :${PORT} never came up — see ${LOG_FILE}`);
  ok(`real second instance up on :${PORT} (MC_REMOTE_ENABLED=0, own MC_DATA_DIR)`);

  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

  // Static, credential-free asset hosts index.html links directly (Google
  // Fonts CSS) — not the safety property this guard exists for. Anything
  // else non-loopback (a vendor OAuth endpoint, clayrune.io) still fails.
  const STATIC_ASSET_HOSTS = new Set(['fonts.googleapis.com', 'fonts.gstatic.com']);
  await page.route('**/*', (route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.hostname !== '127.0.0.1' && url.hostname !== 'localhost') {
      if (STATIC_ASSET_HOSTS.has(url.hostname)) return route.continue();
      externalRequests.push(req.url());
      return route.abort();
    }
    if (url.port !== String(PORT)) return route.continue(); // e.g. Playwright's own chrome-error pages
    const path = url.pathname;
    if (path === '/api/agent/providers' && req.method() === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ providers, default: providers.find((p) => p.default)?.name || '' }) });
    }
    const loginLaunchMatch = path.match(/^\/api\/agent\/provider\/([^/]+)\/login-launch$/);
    if (loginLaunchMatch && req.method() === 'POST') {
      const name = loginLaunchMatch[1];
      loginLaunchCalls.push(name);
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
    }
    return route.continue(); // real server: index.html, static/js, static/css, /api/config, /api/projects, ...
  });

  await page.goto(origin + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 }).catch(() => {});
  // Do NOT call startWalkthrough() here: a genuinely fresh install (0 real
  // projects, no default_provider — exactly this fixture) auto-starts the
  // tour itself ~600ms after boot continuation. Calling it manually AND
  // letting the auto-start fire later reset wtStep back to 0 mid-flow — the
  // "Welcome to Clayrune" regression this exact ordering produced when the
  // clicks below landed after that second, unwanted startWalkthrough() call.
  await page.waitForSelector('#wt-overlay', { timeout: 5000 });
  let title = await page.locator('#wt-overlay .wt-title').innerText();
  if (title !== 'Which AI do you work with?') {
    await page.evaluate(() => window.wtNext());
    await page.waitForTimeout(150);
    title = await page.locator('#wt-overlay .wt-title').innerText();
  }
  if (title !== 'Which AI do you work with?') throw new Error(`provider-choice step did not render (got: ${title})`);
  ok('provider-choice step rendered against the real second instance');

  async function readRows() {
    return page.evaluate(() => {
      const overlay = document.getElementById('wt-overlay');
      return Array.from(overlay.querySelectorAll('input[type=checkbox][name="wt-provider"]')).map((cb) => {
        const label = cb.closest('label');
        const row = label.parentElement;
        const spans = Array.from(label.querySelectorAll('span'));
        return {
          name: cb.value,
          stateText: (spans[1] || {}).textContent || '',
          hasSignInBtn: !!row.querySelector('button[onclick*="settingsProviderTerminalLogin"]'),
        };
      });
    });
  }

  // Select claude, gemini, qwen — NOT codex (detection-only per Dave's scope).
  for (const name of ['claude', 'gemini', 'qwen']) {
    await page.locator(`#wt-overlay input[name="wt-provider"][value="${name}"]`).check();
  }
  await page.locator('#wt-overlay input[name="wt-provider-default"]').first().check(); // claude, first selected
  await page.waitForTimeout(50);

  const before = await readRows();
  const codexBefore = before.find((r) => r.name === 'codex');
  if (!codexBefore || !/not signed in/i.test(codexBefore.stateText))
    fail(`codex should show its real (faked) not-signed-in state without any click, got: ${JSON.stringify(codexBefore)}`);
  else ok('codex state detected and rendered correctly (not_logged_in) — no sign-in click, per Codex allowance scope');
  for (const name of ['claude', 'gemini', 'qwen']) {
    const row = before.find((r) => r.name === name);
    if (!row || !/not signed in/i.test(row.stateText) || !row.hasSignInBtn)
      fail(`${name} should show "not signed in" with a Sign in button before login, got: ${JSON.stringify(row)}`);
    else ok(`${name} shows "not signed in" with a Sign in button before login`);
  }

  // Click each selected vendor's real Sign in button, one at a time, and
  // flip that vendor's fixture auth_status right after — simulating the
  // out-of-band terminal login settingsProviderTerminalLogin's own toast
  // tells the user to go complete.
  for (const name of ['claude', 'gemini', 'qwen']) {
    const clickResult = await page.evaluate((n) => {
      const overlay = document.getElementById('wt-overlay');
      if (!overlay) return 'no-overlay';
      const cb = overlay.querySelector(`input[type=checkbox][name="wt-provider"][value="${n}"]`);
      if (!cb) return 'no-checkbox';
      const label = cb.closest('label');
      if (!label) return 'no-label';
      const row = label.parentElement;
      const btn = row.querySelector('button[onclick*="settingsProviderTerminalLogin"]');
      if (!btn) return 'no-signin-btn';
      btn.click();
      return 'clicked';
    }, name);
    if (clickResult !== 'clicked') {
      const dump = await page.evaluate(() => {
        const overlay = document.getElementById('wt-overlay');
        if (!overlay) return 'NO OVERLAY AT ALL';
        const title = (overlay.querySelector('.wt-title') || {}).textContent || '(no title)';
        const boxes = Array.from(overlay.querySelectorAll('input[type=checkbox]')).map((c) => `${c.name}=${c.value}`);
        return `title="${title}" checkboxes=[${boxes.join(', ')}]`;
      });
      fail(`could not click ${name}'s Sign in button: ${clickResult} — overlay state: ${dump}`);
    }
    await page.waitForTimeout(80);
    providers.find((p) => p.name === name).auth_status = 'ok';
  }
  if (loginLaunchCalls.length !== 3 || new Set(loginLaunchCalls).size !== 3)
    fail(`expected exactly one login-launch call per selected vendor (claude, gemini, qwen), got: ${JSON.stringify(loginLaunchCalls)}`);
  else ok(`each selected vendor's Sign in button called the real login-launch route once: ${loginLaunchCalls.join(', ')}`);

  // "Check setup status" — the real wtRefreshProviders() the UI exposes —
  // re-fetches /api/agent/providers and must flip all three to "signed in".
  await page.evaluate(() => window.wtRefreshProviders());
  await page.waitForTimeout(80);
  const after = await readRows();
  for (const name of ['claude', 'gemini', 'qwen']) {
    const row = after.find((r) => r.name === name);
    if (!row || !/signed in/i.test(row.stateText) || row.hasSignInBtn)
      fail(`${name} should read "signed in" with no Sign in button after refresh, got: ${JSON.stringify(row)}`);
    else ok(`${name} flipped to "signed in" after Check setup status, Sign in button gone`);
  }
  const codexAfter = after.find((r) => r.name === 'codex');
  if (!codexAfter || !/not signed in/i.test(codexAfter.stateText))
    fail(`codex (never clicked) must stay untouched by the other vendors' refresh, got: ${JSON.stringify(codexAfter)}`);
  else ok('codex state unchanged by the other vendors\' sign-in — detection-only scope held');

  // The whole point: Next must now unblock (default chosen, all three
  // selected vendors installed+signed-in).
  await page.evaluate(() => window.wtNext());
  await page.waitForTimeout(100);
  const nextTitle = await page.locator('#wt-overlay .wt-title').innerText().catch(() => '');
  if (nextTitle === 'Which AI do you work with?') fail('Next stayed blocked after all selected vendors signed in and a default chosen');
  else ok(`Next unblocked and advanced past provider-choice (now: "${nextTitle}")`);

  if (externalRequests.length) fail(`non-loopback network request(s) attempted — real OAuth/tunnel leak: ${externalRequests.join(', ')}`);
  else ok('zero non-loopback network requests (no real OAuth, no clayrune.io tunnel/enrollment call)');
  if (pageErrors.length) fail('uncaught exception(s): ' + pageErrors.join(' | '));

  exitCode = bad === 0 ? 0 : 1;
  console.log(exitCode === 0
    ? '\n✅ PASS — real sign-in route wiring for claude/gemini/qwen, codex detection-only, zero external calls.'
    : `\n❌ FAIL — ${bad} problem(s).`);
} catch (err) {
  console.error('❌ FAIL — smoke harness error:', err && err.stack ? err.stack : err);
} finally {
  if (browser) await browser.close().catch(() => {});
  if (serverProc && serverProc.pid) {
    try { process.kill(serverProc.pid, 'SIGTERM'); } catch (e) { /* already gone */ }
  }
  process.exit(exitCode);
}
