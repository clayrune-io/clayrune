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
const POLICY_NOTE = 'PowerShell script policy was Restricted; set to RemoteSigned for your user account.';
const pageHTML = `<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/app.css"><body><main id="app"></main><script>
let API_BASE=''; let _agentProviders=${JSON.stringify(providers)}; let _globalConfig={};
const advancedFlags={}, ADV_FEATURES=[]; function esc(s){return String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]))}
function showToast(){} function showDesktop(){} function refreshSilent(){} function refreshAuthStatus(){}
async function saveSetting(k,v){_globalConfig[k]=v;}
async function _ensureAgentProviders(){const r=await fetch('/api/agent/providers'); _agentProviders=await r.json();}
const browserLoginCalls=[]; function stubTerminalLogin(name){browserLoginCalls.push(name);} window.browserLoginCalls=browserLoginCalls;
</script><script src="/static/js/first-run.js"></script><script src="/static/js/provider-auth.js"></script><script>window.settingsProviderTerminalLogin=stubTerminalLogin;</script><script>startFirstRun();</script>`;

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
    if (path === '/api/agent/providers/install-launch' && req.method === 'POST') {
      let raw = ''; req.on('data', c => { raw += c; }); req.on('end', () => {
        const names = JSON.parse(raw || '{}').names || [];
        installCalls.push(...names);
        providers.forEach(p => { if (names.includes(p.name)) p.installed = true; });
        res.writeHead(200, {'content-type': 'application/json'});
        res.end(JSON.stringify({ok: true, installed: names, unsupported: [],
          execution_policy: {action: 'set', effective: 'Restricted', message: POLICY_NOTE}}));
      });
      return;
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
  await page.getByRole('button', {name: 'Next'}).click();
  // F2: the gate names every unfinished vendor and its exact problem.
  const gate = await page.locator('#setup-provider-validation').innerText();
  if (!gate.includes('Codex: not installed') || !gate.includes('Claude: not installed')) throw new Error(`incomplete selection advanced or gate did not name vendors: ${gate}`);
  await page.getByRole('button', {name: 'Install selected'}).click();
  await page.waitForFunction(() => /PowerShell script policy/.test(document.getElementById('prov-install-msg-codex')?.textContent || ''));
  // F6: the policy note the server returned is shown next to the install message.
  const note = await page.locator('#prov-install-msg-claude').innerText();
  if (!note.includes('PowerShell script policy')) throw new Error(`policy note not shown: ${note}`);
  await page.evaluate(() => providerRefreshAll());
  await page.waitForTimeout(40);
  if (installCalls.length !== 2 || new Set(installCalls).size !== 2) throw new Error(`selected install calls incorrect: ${installCalls}`);
  if (singleInstallCalls.length) throw new Error(`per-vendor install route used instead of the batch: ${singleInstallCalls}`);
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
  await page.getByRole('button', {name: 'Next'}).click();
  const qwenGate = await page.locator('#setup-provider-validation').innerText();
  if (!qwenGate.includes('Qwen Code: not signed in')) throw new Error(`gate did not name unsigned Qwen: ${qwenGate}`);
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
  if (pageErrors.length) throw new Error(pageErrors.join('; '));
  console.log(JSON.stringify({ok: true, envCalls, installCalls, singleInstallCalls, loginCalls: await page.evaluate(() => browserLoginCalls), overflow, registrations}));
} finally {
  if (browser) await browser.close();
  if (browserServer) await browserServer.close();
  if (server) await new Promise(resolveClose => server.close(resolveClose));
}
