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
];
const installCalls = [], loginCalls = [];
const pageHTML = `<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/app.css"><body><main id="app"></main><script>
let API_BASE=''; let _agentProviders=${JSON.stringify(providers)}; let _globalConfig={};
const advancedFlags={}, ADV_FEATURES=[]; function esc(s){return String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]))}
function showDesktop(){} function refreshSilent(){} function refreshAuthStatus(){}
async function saveSetting(k,v){_globalConfig[k]=v;}
async function _ensureAgentProviders(){const r=await fetch('/api/agent/providers'); _agentProviders=await r.json();}
const browserLoginCalls=[]; function settingsProviderTerminalLogin(name){browserLoginCalls.push(name);} window.settingsProviderTerminalLogin=settingsProviderTerminalLogin; window.browserLoginCalls=browserLoginCalls;
</script><script src="/static/js/walkthrough.js"></script><script>startWalkthrough();</script>`;

let server, browserServer, browser, page;
try {
  server = http.createServer((req, res) => {
    const path = decodeURIComponent((req.url || '').split('?')[0]);
    if (path === '/') { res.writeHead(200, {'content-type': 'text/html'}); return res.end(pageHTML); }
    if (path === '/static/js/walkthrough.js') { res.writeHead(200, {'content-type': 'text/javascript'}); return res.end(readFileSync(resolve(root, 'static/js/walkthrough.js'))); }
    if (path === '/static/css/app.css') { res.writeHead(200, {'content-type': 'text/css'}); return res.end(readFileSync(resolve(root, 'static/css/app.css'))); }
    if (path === '/api/agent/providers') { res.writeHead(200, {'content-type': 'application/json'}); return res.end(JSON.stringify(providers)); }
    if (path.startsWith('/api/agent/provider/') && path.endsWith('/install-launch')) {
      const name = path.split('/')[4]; installCalls.push(name);
      const p = providers.find(x => x.name === name); if (p) p.installed = true;
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
  await page.getByRole('button', {name: 'Start Tour'}).click();
  await page.waitForSelector('#wt-overlay input[name="wt-provider"]');
  const checks = page.locator('#wt-overlay input[name="wt-provider"]');
  await page.locator('#wt-overlay input[name="wt-provider"][value="codex"]').check();
  await page.locator('#wt-overlay input[name="wt-provider"][value="claude"]').check();
  await page.locator('#wt-overlay input[name="wt-provider-default"]').first().check();
  await page.getByRole('button', {name: 'Next'}).click();
  if (!(await page.locator('#wt-provider-validation').innerText()).includes('install every selected')) throw new Error('incomplete selection advanced');
  await page.getByRole('button', {name: 'Install selected'}).click();
  await page.evaluate(() => wtRefreshProviders());
  await page.waitForTimeout(40);
  if (installCalls.length !== 2 || new Set(installCalls).size !== 2) throw new Error(`selected install calls incorrect: ${installCalls}`);
  const signIn = page.getByRole('button', {name: 'Sign in'}).first();
  if (await signIn.count()) await page.evaluate(() => document.querySelector('#wt-overlay button[onclick^="settingsProviderTerminalLogin"]').click());
  if (!(await page.evaluate(() => browserLoginCalls.length))) throw new Error(`sign-in action was not wired; body=${await page.locator('#wt-overlay').innerText()}`);
  providers.forEach(p => { if (p.name === 'codex' || p.name === 'claude') p.auth_status = 'ok'; });
  await page.evaluate(() => wtRefreshProviders()); await page.waitForTimeout(40);
  await page.getByRole('button', {name: 'Next'}).click();
  if (!(await page.locator('#wt-overlay .wt-title').innerText()).includes('Choose your level')) throw new Error('completed onboarding did not advance');
  await page.setViewportSize({width: 390, height: 844});
  await page.evaluate(() => wtBack()); await page.waitForTimeout(30);
  const overflow = await page.evaluate(() => ({scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth}));
  if (overflow.scrollWidth > overflow.clientWidth + 1) throw new Error(`mobile onboarding overflow: ${JSON.stringify(overflow)}`);
  if (pageErrors.length) throw new Error(pageErrors.join('; '));
  console.log(JSON.stringify({ok: true, installCalls, loginCalls: await page.evaluate(() => browserLoginCalls), overflow, registrations}));
} finally {
  if (browser) await browser.close();
  if (browserServer) await browserServer.close();
  if (server) await new Promise(resolveClose => server.close(resolveClose));
}
