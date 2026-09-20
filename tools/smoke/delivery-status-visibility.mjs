#!/usr/bin/env node
/** Real Agent Log recovery-status regression against isolated checkout assets. */
import http from 'node:http';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const SMOKE_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(SMOKE_DIR, '..', '..');
const registerURL = process.env.MC_TEST_PROCESS_REGISTER_URL || '';
const registerProject = process.env.MC_TEST_PROCESS_PROJECT || '';
const registrations = [];
async function registerOwnedPID(pid, name, command) {
  if (!registerURL) return;
  if (!registerProject) throw new Error('MC_TEST_PROCESS_PROJECT is required when process registration is enabled');
  const response = await fetch(registerURL, {method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify({pid, name, project_id: registerProject, command})});
  let body = {};
  try { body = await response.json(); } catch (_) { /* fail below */ }
  if (!response.ok || body.ok !== true) throw new Error(`process registration failed for ${pid}: HTTP ${response.status}`);
  registrations.push({pid, name});
}
const rows = Array.from({length: 26}, (_, i) => ({
  table: 'outbox', event_id: `e-${i}`, parent_session_id: 'parent-p',
  state: i % 2 ? 'blocked' : 'pending', attempts: i,
  reason_code: 'parent_unavailable', reason: 'Parent is unavailable or busy.',
  recovery_required: false, created_at: i,
}));
const pageHTML = `<!doctype html><head><link rel="stylesheet" href="/static/css/app.css"></head><body><main class="modal-window" data-modal-id="p"><div id="app"></div></main><script>
let modalActiveTab = {}; let agentLogCache = {}; let pendingResumeId = {};
let agentStatusCache = {}; let continueInputOpen = {}; let API_BASE = '';
let activeProjectId = 'p';
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function _getProviderCaps(){return {emits_usage:false,emits_num_turns:false,emits_cost:false,supports_session_resume:false}}
function _providerBadge(){return ''} function getDefaultResumeId(){return ''}
function refreshModal(){const p={id:activeProjectId,project_path:'/tmp/'+activeProjectId}; document.querySelector('#app').innerHTML=window.agentLogPanelHTML(p)}
function refreshModalById(id){if(id===activeProjectId) refreshModal()}
window.refreshModal=refreshModal; window.refreshModalById=refreshModalById; window.API_BASE='';
</script><script type="module" src="/static/js/agent-log.js"></script><script type="module" src="/static/js/agent-console.js"></script>`;

let server = null;
let browserServer = null;
let browser = null;
let page = null;
try {
server = http.createServer((req, res) => {
  const path = decodeURIComponent((req.url || '').split('?')[0]);
  if (path === '/') { res.writeHead(200, {'content-type': 'text/html'}); return res.end(pageHTML); }
  if (path.startsWith('/static/js/agent-log.js') || path.startsWith('/static/js/agent-console.js')) {
    const file = resolve(REPO_ROOT, path.slice(1));
    res.writeHead(200, {'content-type': 'text/javascript'}); return res.end(readFileSync(file));
  }
  if (path === '/static/css/app.css') {
    res.writeHead(200, {'content-type': 'text/css'}); return res.end(readFileSync(resolve(REPO_ROOT, 'static/css/app.css')));
  }
  if (path.includes('/agent/log')) { res.writeHead(200, {'content-type': 'application/json'}); return res.end('[]'); }
  if (path.includes('/status-list')) {
    if (path.includes('/error/')) { res.writeHead(503); return res.end('{}'); }
    const url = new URL(`http://stub${req.url}`);
    const offset = Number(url.searchParams.get('offset') || 0);
    const usage = path.includes('/unknown/')
      ? {status: 'unknown', reason_code: 'usage_unavailable', reason: 'Payload usage is unavailable; warning state is unknown.'}
      : path.includes('/disabled/')
        ? {status: 'ok', payload_bytes: 4, row_count: 1, warning_bytes: 0, warning: false}
        : {status: 'ok', payload_bytes: 1073741824, row_count: 3, warning_bytes: 1073741824, warning: true};
    const send = () => { res.writeHead(200, {'content-type': 'application/json'}); res.end(JSON.stringify({items: rows.slice(offset, offset + 25), total: 26, limit: 25, offset, usage})); };
    return offset === 0 ? setTimeout(send, 80) : send();
  }
  res.writeHead(404); res.end();
});

await new Promise(resolveServer => server.listen(0, '127.0.0.1', resolveServer));
await registerOwnedPID(process.pid, 'Delegation visibility smoke server', 'node tools/smoke/delivery-status-visibility.mjs');
browserServer = await chromium.launchServer({headless: true});
await registerOwnedPID(browserServer.process().pid, 'Delegation visibility smoke Chromium', 'Playwright Chromium for delivery visibility smoke');
browser = await chromium.connect({wsEndpoint: browserServer.wsEndpoint()});
page = await browser.newPage();
const pageErrors = [];
page.on('pageerror', error => pageErrors.push(String(error)));
  await page.goto(`http://127.0.0.1:${server.address().port}/`, {waitUntil: 'load', timeout: 10000});
  await page.waitForFunction(() => typeof window.switchModalTab === 'function' &&
    typeof window.loadDeliveryStatus === 'function', null, {timeout: 5000});
  await page.evaluate(() => { activeProjectId = 'p'; modalActiveTab.p = 'agent-log'; refreshModal(); window.switchModalTab('p', 'agent-log'); });
  await page.waitForSelector('.delivery-status-section', {timeout: 5000});
  const firstText = await page.locator('.delivery-status-section').innerText();
  if (!firstText.toLowerCase().includes('26 items')) throw new Error(`first page did not render: ${firstText.slice(0, 200)}`);
  if (!firstText.toLowerCase().includes('advisory warning') || !firstText.includes('never blocks dispatch')) throw new Error('WARN-ONLY usage advisory did not render');
  const firstPage = await page.locator('.delivery-status-pending,.delivery-status-blocked').count();
  if (firstPage !== 25) throw new Error(`expected 25 first-page rows, got ${firstPage}`);
  await page.getByRole('button', {name: 'Next ›'}).click();
  await page.waitForFunction(() => document.querySelectorAll('.delivery-status-pending,.delivery-status-blocked').length === 1, null, {timeout: 5000});
  await page.evaluate(() => { activeProjectId = 'error'; modalActiveTab.error = 'agent-log'; refreshModal(); window.switchModalTab('error', 'agent-log'); });
  await page.waitForFunction(() => document.querySelector('#app')?.innerText.includes('Delivery status could not be read.'), null, {timeout: 5000});
  await page.evaluate(() => { activeProjectId = 'unknown'; modalActiveTab.unknown = 'agent-log'; refreshModal(); window.switchModalTab('unknown', 'agent-log'); });
  await page.waitForFunction(() => document.querySelector('#app')?.innerText.includes('warning state is unknown'), null, {timeout: 5000});
  const unknownUsage = (await page.locator('#app').innerText()).includes('warning state is unknown');
  await page.evaluate(() => { activeProjectId = 'disabled'; modalActiveTab.disabled = 'agent-log'; refreshModal(); window.switchModalTab('disabled', 'agent-log'); });
  await page.waitForFunction(() => document.querySelector('#app')?.innerText.includes('Advisory warning disabled.'), null, {timeout: 5000});
  const disabledWarning = (await page.locator('#app').innerText()).includes('Advisory warning disabled.');
  await page.evaluate(() => { activeProjectId = 'p'; modalActiveTab.p = 'agent-log'; refreshModal(); window.loadDeliveryStatus('p', 0); window.loadDeliveryStatus('p', 25); });
  await page.waitForFunction(() => { const text = document.querySelector('#app')?.innerText.toLowerCase() || ''; return text.includes('e-25') && !text.includes('e-0'); }, null, {timeout: 5000});
  await page.evaluate(() => { window.loadDeliveryStatus('p', 0); activeProjectId = 'error'; modalActiveTab.error = 'agent-log'; refreshModal(); window.switchModalTab('error', 'agent-log'); });
  await page.waitForFunction(() => document.querySelector('#app')?.innerText.includes('Delivery status could not be read.'), null, {timeout: 5000});
  await new Promise(resolveDelay => setTimeout(resolveDelay, 120));
  if (!(await page.locator('#app').innerText()).includes('Delivery status could not be read.')) throw new Error('stale prior-project response replaced current project error view');
  if (pageErrors.length) throw new Error(pageErrors.join('; '));
  const viewports = [];
  for (const viewport of [{name: 'desktop', width: 1280, height: 900}, {name: 'mobile', width: 390, height: 844}]) {
    await page.setViewportSize(viewport);
    await page.evaluate(() => { activeProjectId = 'p'; modalActiveTab.p = 'agent-log'; refreshModal(); window.switchModalTab('p', 'agent-log'); });
    await page.waitForSelector('.delivery-status-section', {timeout: 5000});
    const box = await page.locator('.delivery-status-section').boundingBox();
    if (!box || box.width <= 0 || box.height <= 0) throw new Error(`${viewport.name} recovery panel is not visible`);
    const layout = await page.evaluate(() => {
      const buttons = [...document.querySelectorAll('.delivery-status-section button')];
      return {scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth,
        buttonsWithinViewport: buttons.every(button => { const r = button.getBoundingClientRect(); return r.left >= 0 && r.right <= window.innerWidth; })};
    });
    if (layout.scrollWidth > layout.clientWidth + 1 || !layout.buttonsWithinViewport) throw new Error(`${viewport.name} recovery layout overflows or clips pagination: ${JSON.stringify(layout)}`);
    viewports.push({name: viewport.name, width: Math.round(box.width), height: Math.round(box.height), ...layout});
  }
  console.log(JSON.stringify({ok: true, firstPage, secondPage: 1, errorState: true, unknownUsage, disabledWarning, handler: await page.evaluate(() => typeof window.loadDeliveryStatus), viewports, registrations}));
} finally {
  if (browser) await browser.close();
  if (browserServer) await browserServer.close();
  if (server) await new Promise(resolveClose => server.close(resolveClose));
}
