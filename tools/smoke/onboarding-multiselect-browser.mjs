#!/usr/bin/env node
/** Real Chromium smoke for first-run provider onboarding.
 * No provider CLIs or package managers are run; the install/auth endpoints are
 * local deterministic stubs. Process registration is opt-in and records only
 * this Node process and its owned Playwright Chromium.
 */
import http from 'node:http';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';

const root = fileURLToPath(new URL('../..', import.meta.url));
const registerURL = process.env.MC_TEST_PROCESS_REGISTER_URL || '';
const registerProject = process.env.MC_TEST_PROCESS_PROJECT || '';
const registrations = [];
async function registerOwned(pid, name, command) {
  if (!registerURL) return;
  if (!registerProject) throw new Error('MC_TEST_PROCESS_PROJECT is required');
  const response = await fetch(registerURL, {method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify({pid, name, project_id: registerProject, command})});
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.ok !== true) throw new Error(`process registration failed: ${response.status}`);
  registrations.push({pid, name});
}

const providers = [
  {name: 'codex', display_name: 'Codex', installed: false, auth_status: 'unknown'},
  {name: 'claude', display_name: 'Claude', installed: false, auth_status: 'unknown'},
  {name: 'gemini', display_name: 'Gemini', installed: true, auth_status: 'not_logged_in'},
  {name: 'qwen', display_name: 'Qwen Code', installed: true, auth_status: 'not_logged_in'},
];
const envCalls = [];
const installCalls = [], loginCalls = [], singleInstallCalls = [];
let installLaunchCalls = 0;
const POLICY_NOTE = 'PowerShell script policy was Restricted; set to RemoteSigned for your user account.';
const pageHTML = `<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/app.css"><body><main id="app"></main><div class="modal-layer" id="modal-layer"></div><script>
let API_BASE=''; let _agentProviders=${JSON.stringify(providers)}; let _globalConfig={};
let _providerInstallMsg={}; let _providerInstallPolicyNoteText=''; let _providerInstallStatusUrl='';
let _providerInstallProgress={};
const advancedFlags={}, ADV_FEATURES=[]; function esc(s){return String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]))}
function showToast(){} function showDesktop(){} function refreshSilent(){} function refreshAuthStatus(){}
async function saveSetting(k,v){_globalConfig[k]=v;}
async function _ensureAgentProviders(){const r=await fetch('/api/agent/providers'); _agentProviders=await r.json();}
const browserLoginCalls=[]; function stubTerminalLogin(name){browserLoginCalls.push(name);} window.browserLoginCalls=browserLoginCalls;
// Stand-in for terminal.js's real openTerminalPopout: this smoke targets the
// NEW stacking/docking code first-run.js and app.css added around a live
// install terminal (clean-VM run 2026-09-24), not xterm.js itself (its CDN
// load always fails in this sandbox, same as mermaid's in other smokes, and
// is already covered by the "terminal opened" text assertions elsewhere).
// Same DOM footprint the real one leaves behind: a .modal-window carrying
// data-modal-id="__terminal_<id>" inside #modal-layer, positioned where the
// real centerModalElement() would put an untouched terminal — center of the
// viewport, which is exactly where the setup card also centers itself.
function openTerminalPopout(projectId, sessionId, command, isPty){
  const win=document.createElement('div');
  win.className='modal-window'; win.dataset.modalId='__terminal_'+sessionId;
  win.style.position='absolute'; win.style.width='500px'; win.style.height='320px';
  win.style.left='calc(50% - 250px)'; win.style.top='calc(50% - 160px)';
  win.innerHTML='<div class="modal-content" style="width:100%;height:100%;background:#0a0c10"></div>';
  document.getElementById('modal-layer').appendChild(win);
}
</script><script src="/static/js/first-run.js"></script><script src="/static/js/provider-auth.js"></script><script>window._realSettingsProviderTerminalLogin=settingsProviderTerminalLogin;window.settingsProviderTerminalLogin=stubTerminalLogin;</script><script>startFirstRun();</script>`;

let server, browserServer, browser, page;
try {
  server = http.createServer((req, res) => {
    const path = decodeURIComponent((req.url || '').split('?')[0]);
    if (path === '/') { res.writeHead(200, {'content-type': 'text/html'}); return res.end(pageHTML); }
    if (path === '/static/js/first-run.js') { res.writeHead(200, {'content-type': 'text/javascript'}); return res.end(readFileSync(resolve(root, 'static/js/first-run.js'))); }
    if (path === '/static/js/provider-auth.js') { res.writeHead(200, {'content-type': 'text/javascript'}); return res.end(readFileSync(resolve(root, 'static/js/provider-auth.js'))); }
    if (path === '/static/css/app.css') { res.writeHead(200, {'content-type': 'text/css'}); return res.end(readFileSync(resolve(root, 'static/css/app.css'))); }
    if (path === '/api/agent/providers') { res.writeHead(200, {'content-type': 'application/json'}); return res.end(JSON.stringify(providers)); }
    // F7: "Install selected" is ONE batch request for every selected vendor.
    // MC-959 (Bram, 14547ff): the real route now also returns status_url for
    // GET .../install-status. First call (session smoke-term-1) matches the
    // ORIGINAL smoke exactly: an untracked/unknown status feed (ok:false),
    // same as an older server without the route, so the pre-existing
    // fallback-to-DOM assertions below are unaffected. The second call
    // (smoke-term-2) is MC-959's own integration test further down: a real
    // FAILED-vendor, batch-finished status feed.
    if (path === '/api/agent/providers/install-launch' && req.method === 'POST') {
      let raw = ''; req.on('data', c => { raw += c; }); req.on('end', () => {
        const names = JSON.parse(raw || '{}').names || [];
        installCalls.push(...names);
        installLaunchCalls += 1;
        const sessionId = installLaunchCalls === 1 ? 'smoke-term-1' : 'smoke-term-2';
        if (installLaunchCalls === 1) providers.forEach(p => { if (names.includes(p.name)) p.installed = true; });
        res.writeHead(200, {'content-type': 'application/json'});
        res.end(JSON.stringify({ok: true, installed: names, unsupported: [], session_id: sessionId, command: 'install',
          execution_policy: installLaunchCalls === 1 ? {action: 'set', effective: 'Restricted', message: POLICY_NOTE} : null,
          status_url: `/api/agent/providers/install-status?session_id=${sessionId}`}));
      });
      return;
    }
    if (path === '/api/agent/providers/install-status') {
      const sid = new URL(req.url, 'http://x').searchParams.get('session_id');
      res.writeHead(200, {'content-type': 'application/json'});
      if (sid === 'smoke-term-2') {
        return res.end(JSON.stringify({ok: true, session_id: sid, running: false, exit_code: 1,
          vendors: [{name: 'codex', result: 'failed', installed: false, version: null}], failed: ['codex']}));
      }
      // smoke-term-1: not tracked here, same shape a server too old to have
      // remembered this session would return — the caller must fall back.
      return res.end(JSON.stringify({ok: false, error: 'unknown install session'}));
    }
    // Item 4: Gemini's real settingsProviderTerminalLogin hits this route —
    // pty:true + session_id is the shape that makes it open a terminal
    // pop-out (its account-picker needs a TUI) instead of a captured URL.
    if (path === '/api/agent/gemini/auth-login-remote' && req.method === 'POST') {
      res.writeHead(200, {'content-type': 'application/json'});
      return res.end(JSON.stringify({pty: true, session_id: 'smoke-signin-term', command: 'gemini'}));
    }
    // Qwen's only sign-in is a key: the same Settings save route flips it to ok.
    if (path === '/api/agent/provider/qwen/env' && req.method === 'POST') {
      let raw = ''; req.on('data', c => { raw += c; }); req.on('end', () => {
        const body = JSON.parse(raw || '{}'); envCalls.push({key: body.key, hasValue: !!body.value});
        if (body.value) providers.find(p => p.name === 'qwen').auth_status = 'ok';
        res.writeHead(200, {'content-type': 'application/json'}); res.end(JSON.stringify({ok: true, key: body.key}));
      });
      return;
    }
    if (path.startsWith('/api/agent/provider/') && path.endsWith('/install-launch')) {
      singleInstallCalls.push(path.split('/')[4]);
      res.writeHead(200, {'content-type': 'application/json'}); return res.end(JSON.stringify({ok: true}));
    }
    res.writeHead(404); res.end();
  });
  await new Promise(resolveServer => server.listen(0, '127.0.0.1', resolveServer));
  await registerOwned(process.pid, 'Provider onboarding browser smoke server', 'node tools/smoke/onboarding-multiselect-browser.mjs');
  browserServer = await chromium.launchServer({headless: true});
  await registerOwned(browserServer.process().pid, 'Provider onboarding smoke Chromium', 'Playwright Chromium onboarding smoke');
  browser = await chromium.connect({wsEndpoint: browserServer.wsEndpoint()});
  page = await browser.newPage({viewport: {width: 1280, height: 900}});
  const pageErrors = []; page.on('pageerror', e => pageErrors.push(String(e)));
  await page.goto(`http://127.0.0.1:${server.address().port}/`, {waitUntil: 'load'});
  await page.getByRole('button', {name: 'Get started'}).click();
  await page.waitForSelector('#setup-overlay input[name="setup-provider"]');
  const checks = page.locator('#setup-overlay input[name="setup-provider"]');
  await page.locator('#setup-overlay input[name="setup-provider"][value="codex"]').check();
  await page.locator('#setup-overlay input[name="setup-provider"][value="claude"]').check();
  await page.locator('#setup-overlay input[name="setup-provider-default"]').first().check();
  // Connections can't be passed with an unfinished vendor selected: Next is
  // disabled outright (not just gated on click), with a one-line reason
  // naming the first unfinished vendor.
  // Picking the default radio round-trips through applyDefaultProvider's own
  // async refresh (saveSetting + _ensureAgentProviders) before its repaint
  // fires — wait for the reason to settle past "Choose a default" rather
  // than reading it mid-flight.
  await page.waitForFunction(() => (document.querySelector('#setup-overlay .wt-next-reason')?.textContent || '').includes('not installed'));
  const nextBtn0 = page.getByRole('button', {name: 'Next'});
  if (!(await nextBtn0.isDisabled())) throw new Error('Next should stay disabled while codex+claude are both selected but not installed');
  const reason0 = await page.locator('#setup-overlay .wt-next-reason').innerText();
  if (!reason0.includes('not installed')) throw new Error(`disabled-Next reason did not name an unfinished vendor: ${reason0}`);
  await page.getByRole('button', {name: 'Install selected'}).click();
  await page.waitForFunction(() => /PowerShell script policy/.test(document.querySelector('.prov-install-policy-note')?.textContent || ''));
  // F6, revised 2026-09-24 (clean-VM run: the note repeated once per selected
  // vendor row, 4x with everything ticked): the policy note the server
  // returned is shown ONCE for the batch, not duplicated on every row.
  const sharedNote = await page.locator('#setup-overlay .prov-install-policy-note').innerText();
  if (!sharedNote.includes('PowerShell script policy')) throw new Error(`policy note not shown once for the batch: ${sharedNote}`);
  const codexMsg = await page.locator('#prov-install-msg-codex').innerText();
  const claudeMsg = await page.locator('#prov-install-msg-claude').innerText();
  if (codexMsg.includes('PowerShell script policy') || claudeMsg.includes('PowerShell script policy'))
    throw new Error(`policy note still repeated per row: codex="${codexMsg}" claude="${claudeMsg}"`);
  // Point 1 (clean-VM run 2026-09-24): the setup overlay used to paint over
  // the install terminal it just opened, so the user couldn't tell the
  // install was running. Assert the terminal window is ACTUALLY on top —
  // not just that a CSS rule exists — via elementFromPoint at its own center.
  await page.waitForSelector('.modal-window[data-modal-id="__terminal_smoke-term-1"]');
  const stacking = await page.evaluate(() => {
    const overlay = document.getElementById('setup-overlay');
    const modalLayer = document.getElementById('modal-layer');
    const termWin = document.querySelector('.modal-window[data-modal-id="__terminal_smoke-term-1"]');
    const rect = termWin.getBoundingClientRect();
    const topEl = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return {
      bodyLive: document.body.classList.contains('setup-terminal-live'),
      modalLayerZ: Number(getComputedStyle(modalLayer).zIndex),
      overlayZ: Number(getComputedStyle(overlay).zIndex),
      topElIsTerminal: !!topEl && termWin.contains(topEl),
    };
  });
  if (!stacking.bodyLive) throw new Error('body did not gain setup-terminal-live while the install terminal is running');
  if (!(stacking.modalLayerZ > stacking.overlayZ)) throw new Error(`modal layer (${stacking.modalLayerZ}) is not above the setup overlay (${stacking.overlayZ})`);
  if (!stacking.topElIsTerminal) throw new Error('setup overlay still covers the live install terminal at its own center point');
  // Gap 3 (Dave review, 2026-09-24): a failed/cancelled install used to poll
  // forever with setup-terminal-live stuck on, because the watch's stop
  // condition only fired on full success. Remove the terminal node while
  // codex+claude are STILL not installed+authed (real failure shape) and wait
  // for one real 4s poll tick — the watch must notice the terminal is gone
  // and drop the class on its own, with no further user action.
  await page.evaluate(() => document.querySelector('.modal-window[data-modal-id="__terminal_smoke-term-1"]').remove());
  await page.waitForFunction(() => !document.body.classList.contains('setup-terminal-live'), {timeout: 6000});
  await page.evaluate(() => providerRefreshAll());
  await page.waitForTimeout(40);
  if (installCalls.length !== 2 || new Set(installCalls).size !== 2) throw new Error(`selected install calls incorrect: ${installCalls}`);
  if (singleInstallCalls.length) throw new Error(`per-vendor install route used instead of the batch: ${singleInstallCalls}`);
  // Dave review, 2026-09-24 (gap 2): providerRefreshAll() -> window._setupRepaint()
  // rebuilds the WHOLE connections-step body via setupShow — exactly what the
  // 4s install-watch poll does on every tick. The policy note and per-row
  // messages used to be written straight into a DOM node providerInstallSelected
  // found once, so they read back empty after the very next repaint. Call the
  // same repaint path twice (2 simulated poll ticks) and assert both survive.
  for (let tick = 0; tick < 2; tick++) {
    await page.evaluate(() => providerRefreshAll());
    await page.waitForTimeout(40);
    const noteAfterPoll = await page.locator('#setup-overlay .prov-install-policy-note').innerText();
    if (!noteAfterPoll.includes('PowerShell script policy'))
      throw new Error(`policy note lost after poll tick ${tick + 1}: "${noteAfterPoll}"`);
  }
  // Item 2 (Ron's ask, 2026-09-24): the stale "A terminal opened to install
  // it. Once it finishes, click Check setup status" line must stop showing
  // once the row itself reports installed — Ron's screenshots showed it
  // stuck under Claude/Gemini/Qwen rows that were already fully installed.
  // The mock install-launch above already flipped codex to installed:true;
  // the poll ticks just refreshed _agentProviders from that server state, so
  // by now the message must be gone (old code required auth_status==='ok'
  // too, which an installed-but-not-yet-signed-in row never reaches).
  const codexMsgAfterPoll = await page.locator('#prov-install-msg-codex').innerText();
  if (codexMsgAfterPoll.trim()) throw new Error(`stale install message should clear once installed, still shows: "${codexMsgAfterPoll}"`);
  const signIn = page.getByRole('button', {name: 'Sign in'}).first();
  if (await signIn.count()) await page.evaluate(() => document.querySelector('#setup-overlay button[onclick^="settingsProviderTerminalLogin"]').click());
  if (!(await page.evaluate(() => browserLoginCalls.length))) throw new Error(`sign-in action was not wired; body=${await page.locator('#setup-overlay').innerText()}`);
  providers.forEach(p => { if (p.name === 'codex' || p.name === 'claude') p.auth_status = 'ok'; });
  await page.evaluate(() => providerRefreshAll()); await page.waitForTimeout(40);
  await page.getByRole('button', {name: 'Next'}).click();
  if (!(await page.locator('#setup-overlay .wt-title').innerText()).includes('A few essentials')) throw new Error('completed onboarding did not advance');
  // Clean-VM run 3 (C4): Qwen's only sign-in is DASHSCOPE_API_KEY. The setup row
  // must carry the SAME key field + save path Settings -> Providers uses, or a
  // user who picks Qwen can never get past the gate.
  await page.evaluate(() => setupBack()); await page.waitForTimeout(30);
  if (await page.locator('#setup-overlay #settings-prov-key-qwen').count()) throw new Error('key field shown for an unselected vendor');
  await page.locator('#setup-overlay input[name="setup-provider"][value="qwen"]').check();
  await page.locator('#setup-overlay input[name="setup-provider-default"]').first().waitFor();
  const nextBtn1 = page.getByRole('button', {name: 'Next'});
  if (!(await nextBtn1.isDisabled())) throw new Error('Next should stay disabled while Qwen is selected but not signed in');
  const qwenGate = await page.locator('#setup-overlay .wt-next-reason').innerText();
  if (!qwenGate.includes('Qwen Code: not signed in')) throw new Error(`disabled-Next reason did not name unsigned Qwen: ${qwenGate}`);
  const keyBox = page.locator('#setup-overlay #settings-prov-key-qwen');
  if (!(await keyBox.count())) throw new Error('setup Qwen row has no API-key field');
  if (!(await page.locator('#setup-overlay .prov-row[data-provider="qwen"]').innerText()).includes('DASHSCOPE_API_KEY')) throw new Error('Qwen key field is not labelled DASHSCOPE_API_KEY');
  await keyBox.fill('smoke-not-a-real-key');
  await page.locator('#setup-overlay .prov-row[data-provider="qwen"] .prov-row-extra button').click();
  await page.waitForFunction(() => /signed in/.test(document.querySelector('#setup-overlay .prov-row[data-provider="qwen"] .prov-row-state')?.textContent || ''));
  if (JSON.stringify(envCalls) !== JSON.stringify([{key: 'DASHSCOPE_API_KEY', hasValue: true}])) throw new Error(`key save did not use the Settings env route: ${JSON.stringify(envCalls)}`);
  await page.getByRole('button', {name: 'Next'}).click();
  if (!(await page.locator('#setup-overlay .wt-title').innerText()).includes('A few essentials')) throw new Error('signing Qwen in from setup did not clear the gate');
  await page.setViewportSize({width: 390, height: 844});
  await page.evaluate(() => setupBack()); await page.waitForTimeout(30);
  const overflow = await page.evaluate(() => ({scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth}));
  if (overflow.scrollWidth > overflow.clientWidth + 1) throw new Error(`mobile onboarding overflow: ${JSON.stringify(overflow)}`);
  // ── MC-959 integration (Bram's status feed, branch clayrune/agent/96ca59113cdd,
  // commit 14547ff — cherry-picked onto this branch to test against it, not
  // merged into this commit) ──────────────────────────────────────────────────
  // His route makes a FAILED install's terminal pop-out stay OPEN (not close)
  // so its output stays readable, so "terminal gone" can no longer signal
  // "batch is done" on its own — the batch's own GET .../install-status feed
  // is now that signal. Reset codex to "not installed" and relaunch just it;
  // the install-status stub above (session smoke-term-2) reports it FAILED
  // with the batch already finished on the very first poll.
  await page.setViewportSize({width: 1280, height: 900});
  providers.find(p => p.name === 'codex').installed = false;
  providers.find(p => p.name === 'codex').auth_status = 'not_logged_in';
  await page.evaluate(async () => { _agentProviders = null; await _ensureAgentProviders(); window._setupRepaint(); });
  await page.waitForFunction(() => (document.querySelector('#setup-overlay .wt-next-reason')?.textContent || '').includes('Codex'));
  await page.getByRole('button', {name: 'Install selected'}).click();
  await page.waitForFunction(() => document.body.classList.contains('setup-terminal-live'));
  await page.waitForFunction(() => (document.querySelector('#prov-install-msg-codex') || {}).textContent?.includes('Install failed'));
  // Regression (Ron, 2026-09-24): the OLD code dropped setup-terminal-live the
  // instant the batch reported "finished", even though MC-959 deliberately
  // leaves a FAILED install's terminal pop-out OPEN so its output stays
  // readable — the very next in-card click (any control that triggers
  // _setupRepaint/setupShow) then re-centered the card right on top of it.
  // Give the poll loop a full tick past "batch finished" (it must stop
  // POLLING here) and assert the terminal is STILL open and STILL visible —
  // visibility now tracks DOM presence, not the poll loop's own lifecycle.
  await page.waitForTimeout(4200);
  const stillLiveWithFailedTerminal = await page.evaluate(() => {
    const termWin = document.querySelector('.modal-window[data-modal-id^="__terminal_"]');
    return {terminalOpen: !!termWin, bodyLive: document.body.classList.contains('setup-terminal-live')};
  });
  if (!stillLiveWithFailedTerminal.terminalOpen) throw new Error('failed-install terminal unexpectedly removed by the smoke stub before the visibility check');
  if (!stillLiveWithFailedTerminal.bodyLive) throw new Error('setup-terminal-live was dropped while the FAILED install terminal is still open — regression of the 2026-09-24 fix');
  // The exact repro: click something inside the still-open card ("Check setup
  // status" is the real control Ron's report names) — this rebuilds
  // #setup-overlay from scratch via _setupRepaint -> setupShow. The rebuilt
  // card must still dock left / stay under the raised modal-layer.
  await page.getByRole('button', {name: 'Check setup status'}).first().click();
  await page.waitForTimeout(60);
  if (process.env.MC_SMOKE_SCREENSHOT) await page.screenshot({path: process.env.MC_SMOKE_SCREENSHOT});
  const afterClickStacking = await page.evaluate(() => {
    const overlay = document.getElementById('setup-overlay');
    const modalLayer = document.getElementById('modal-layer');
    const termWin = document.querySelector('.modal-window[data-modal-id^="__terminal_"]');
    const rect = termWin.getBoundingClientRect();
    const topEl = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return {
      bodyLive: document.body.classList.contains('setup-terminal-live'),
      modalLayerZ: Number(getComputedStyle(modalLayer).zIndex),
      overlayZ: Number(getComputedStyle(overlay).zIndex),
      topElIsTerminal: !!topEl && termWin.contains(topEl),
    };
  });
  if (!afterClickStacking.bodyLive) throw new Error('setup-terminal-live dropped after an in-card click while the failed-install terminal is still open');
  if (!(afterClickStacking.modalLayerZ > afterClickStacking.overlayZ)) throw new Error('modal layer no longer raised above the overlay after an in-card click');
  if (!afterClickStacking.topElIsTerminal) throw new Error('setup card re-covered the still-open failed-install terminal after an in-card click — the exact regression Ron hit');
  // Now the user actually closes the terminal (or it exits) — visibility must
  // clear once it is genuinely gone, not stay stuck on forever.
  await page.evaluate(() => document.querySelector('.modal-window[data-modal-id^="__terminal_"]').remove());
  await page.waitForFunction(() => !document.body.classList.contains('setup-terminal-live'), {timeout: 6000});
  if (installCalls.filter(n => n === 'codex').length < 2) throw new Error(`expected a second install-launch call for codex, got: ${installCalls}`);

  // ── Item 4 (Ron, 2026-09-24): Gemini's "Sign in" opens a real-PTY terminal
  // (its account-picker needs a TUI, via settingsProviderTerminalLogin's pty
  // branch) — the SAME visibility/docking mechanism the install path above
  // was just proven to have must cover a sign-in terminal too. Restore the
  // REAL sign-in function (stubbed out at page-load for the earlier
  // wiring-only assertions) and drive Gemini's row for real.
  // Gemini was never ticked earlier in this run (only codex/claude/qwen were)
  // — its Sign in button only renders for a selected row (opts.selected gates
  // showActions in setup mode), so select it first.
  await page.locator('#setup-overlay input[name="setup-provider"][value="gemini"]').check();
  await page.waitForSelector('#setup-overlay .prov-row[data-provider="gemini"] button[onclick^="settingsProviderTerminalLogin"]');
  await page.evaluate(() => { window.settingsProviderTerminalLogin = window._realSettingsProviderTerminalLogin; });
  await page.evaluate(() => document.querySelector('#setup-overlay .prov-row[data-provider="gemini"] button[onclick^="settingsProviderTerminalLogin"]').click());
  await page.waitForSelector('.modal-window[data-modal-id="__terminal_smoke-signin-term"]');
  await page.waitForFunction(() => document.body.classList.contains('setup-terminal-live'));
  const signinStacking = await page.evaluate(() => {
    const overlay = document.getElementById('setup-overlay');
    const modalLayer = document.getElementById('modal-layer');
    const termWin = document.querySelector('.modal-window[data-modal-id="__terminal_smoke-signin-term"]');
    const rect = termWin.getBoundingClientRect();
    const topEl = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return {
      modalLayerZ: Number(getComputedStyle(modalLayer).zIndex),
      overlayZ: Number(getComputedStyle(overlay).zIndex),
      topElIsTerminal: !!topEl && termWin.contains(topEl),
    };
  });
  if (!(signinStacking.modalLayerZ > signinStacking.overlayZ)) throw new Error('sign-in terminal: modal layer not raised above the setup overlay');
  if (!signinStacking.topElIsTerminal) throw new Error('sign-in terminal pop-out is covered by the setup overlay/card');
  // Same in-card-click repro as the install case above.
  await page.getByRole('button', {name: 'Check setup status'}).first().click();
  await page.waitForTimeout(60);
  const signinAfterClick = await page.evaluate(() => {
    const overlay = document.getElementById('setup-overlay');
    const termWin = document.querySelector('.modal-window[data-modal-id="__terminal_smoke-signin-term"]');
    if (!termWin) return {gone: true};
    const rect = termWin.getBoundingClientRect();
    const topEl = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return {gone: false, bodyLive: document.body.classList.contains('setup-terminal-live'), topElIsTerminal: !!topEl && termWin.contains(topEl)};
  });
  if (signinAfterClick.gone) throw new Error('sign-in terminal element unexpectedly removed by the smoke stub');
  if (!signinAfterClick.bodyLive) throw new Error('setup-terminal-live dropped after an in-card click while the sign-in terminal is still open');
  if (!signinAfterClick.topElIsTerminal) throw new Error('setup card re-covered the sign-in terminal after an in-card click');
  await page.evaluate(() => document.querySelector('.modal-window[data-modal-id="__terminal_smoke-signin-term"]').remove());
  await page.waitForFunction(() => !document.body.classList.contains('setup-terminal-live'), {timeout: 6000});

  // ── Item 2 (Ron's ask, 2026-09-24): per-vendor progress bar — queued ->
  // installing (indeterminate sweep + elapsed seconds, npm gives no percentage)
  // -> installed (green) / failed (red), driven ONLY by _providerInstallProgress
  // (itself populated from GET .../install-status's per-vendor result/started_at/
  // running_now — see _setupPollInstallStatus), never a client-side timer guess.
  // _renderProviderRow deletes a vendor's tracked progress once its OWN
  // installed+auth_status says fully done, so the four rows are reset to an
  // undone state first — otherwise claude/qwen (already signed in earlier in
  // this run) would drop their entry before _providerProgressHTML ever runs.
  await page.evaluate(() => {
    _agentProviders.forEach(p => { p.installed = false; p.auth_status = 'not_logged_in'; });
    _providerInstallProgress = {
      claude: {result: 'pending', started_at: null, running_now: false},                          // queued
      gemini: {result: 'pending', started_at: Math.floor(Date.now() / 1000) - 7, running_now: true}, // installing, ~7s in
      qwen: {result: 'ok', started_at: null, running_now: false},                                  // installed
      codex: {result: 'failed', started_at: null, running_now: false},                             // failed
    };
    window._repaintProviderRows();
  });
  await page.waitForTimeout(40);
  const progress = await page.evaluate(() => {
    const read = (name) => (document.getElementById(`prov-install-progress-${name}`) || {}).innerText || '';
    return {claude: read('claude'), gemini: read('gemini'), qwen: read('qwen'), codex: read('codex')};
  });
  if (!/Queued/.test(progress.claude)) throw new Error(`queued vendor did not render "Queued": "${progress.claude}"`);
  if (!/Installing.*7s/.test(progress.gemini)) throw new Error(`installing vendor did not render elapsed seconds: "${progress.gemini}"`);
  if (!/Installed/.test(progress.qwen)) throw new Error(`installed vendor did not render "Installed": "${progress.qwen}"`);
  if (!/Install failed/.test(progress.codex)) throw new Error(`failed vendor did not render "Install failed": "${progress.codex}"`);
  const barShapes = await page.evaluate(() => {
    const shape = (name) => {
      const fill = document.querySelector(`#prov-install-progress-${name} .prov-install-bar-fill`);
      return fill ? {color: getComputedStyle(fill).backgroundColor, indeterminate: fill.classList.contains('indeterminate')} : null;
    };
    return {gemini: shape('gemini'), qwen: shape('qwen'), codex: shape('codex')};
  });
  if (!barShapes.gemini.indeterminate) throw new Error('installing vendor bar is not the indeterminate sweep');
  if (barShapes.qwen.indeterminate || barShapes.codex.indeterminate) throw new Error('installed/failed bars must be static, not indeterminate');
  if (barShapes.qwen.color === barShapes.codex.color) throw new Error(`installed and failed bars rendered the same color: ${barShapes.qwen.color}`);
  // A vendor whose row itself is already fully done (installed AND signed in)
  // must not keep showing a stale progress sliver forever.
  await page.evaluate(() => { _agentProviders.find(p => p.name === 'qwen').installed = true; _agentProviders.find(p => p.name === 'qwen').auth_status = 'ok'; window._repaintProviderRows(); });
  await page.waitForTimeout(40);
  const qwenAfterDone = await page.evaluate(() => (document.getElementById('prov-install-progress-qwen') || {}).innerText || '');
  if (qwenAfterDone.trim()) throw new Error(`progress bar did not clear once the row itself reports installed+signed-in: "${qwenAfterDone}"`);

  if (pageErrors.length) throw new Error(pageErrors.join('; '));
  console.log(JSON.stringify({ok: true, envCalls, installCalls, singleInstallCalls, loginCalls: await page.evaluate(() => browserLoginCalls), overflow, registrations}));
} finally {
  if (browser) await browser.close();
  if (browserServer) await browserServer.close();
  if (server) await new Promise(resolveClose => server.close(resolveClose));
}
