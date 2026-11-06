#!/usr/bin/env node
/**
 * Desk campaign chat on a NEW campaign (Ron 2026-11-05): he started a campaign from
 * Home, sent Posy a message, Posy answered ("Posy answered. Nothing was changed."),
 * he clicked the pop-out and the SENT message was back in the box. 866a479b had
 * closed the draft-resurrection path on an EXISTING campaign; this drives the same
 * thing on a campaign created through the real "New campaign" path (draft id,
 * server POST/PATCH, workspace refreshes) and prints the box value at each step.
 *
 * RUN   cd tools/smoke && node desk-v1-campaign-chat-new.mjs [--verbose]
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const STATIC = loadStaticJsCss(REPO_ROOT);
const VERBOSE = process.argv.includes('--verbose');

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
let GID = 0;

function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: p.id === 'clayrune' ? 'global:claydo' : null, state: 'active', product_docs: null, product_materials: ['README.md'] } }));
  const srv = { log: [], threads: {}, campaigns: fx.campaigns.map((c) => JSON.parse(JSON.stringify(c))), projects, fx, reply: null, gate: null };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects, campaigns: srv.campaigns.map((c) => JSON.parse(JSON.stringify(c))) });
  return srv;
}

async function newPage(browser, srv) {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();
    const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return J(srv.fx.projects.map((p) => ({
      id: p.id, name: p.name, status: 'active', domain: 'general', emoji: '🧪', description: '', summary: '', current_task: 'Idle',
      next_action: '', blocked: false, blocked_reason: null, activity_log: [], backlog: [], project_path: '/smoke/' + p.id,
      last_updated: '2026-09-09T00:00:00Z', last_updated_relative: 'today', last_completed: null, live_agent: null, display_order: 0,
      provider: 'claude', use_streaming_agent: true, roster: [],
    })));
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true, user_timezone: '' });
    if (path === '/api/characters') return J([{ name: 'claydo', scope: 'global', agent_name: 'Claydo', avatar: '' }]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* no body */ }
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/research-capabilities') return J({ web: true, project_read: true, toolless: false, label: 'Can search the web and read the project folder (read-only)' });
    if (path === '/api/desk/campaigns' && method === 'POST') { srv.campaigns.push({ ...body, project_id: body.projectId || null }); return J(body, 201); }
    let m = path.match(/^\/api\/desk\/campaigns\/([^/]+)$/);
    if (m && method === 'PATCH') {
      const c = srv.campaigns.find((x) => x.id === m[1]);
      if (!c) return J({ error: 'campaign not found' }, 404);
      Object.assign(c, body); if ('projectId' in body) c.project_id = body.projectId;
      return J(c);
    }
    if (m && method === 'DELETE') { srv.campaigns = srv.campaigns.filter((c) => c.id !== m[1]); return J({ ok: true }); }
    m = path.match(/^\/api\/desk\/campaigns\/([^/]+)\/chat$/);
    if (m) {
      const id = m[1];
      if (method === 'GET') return J({ thread: (srv.threads[id] || []).map((t) => ({ ...t })) });
      if (srv.gate) await srv.gate;
      const r = srv.reply(body);
      if (r.status) return J({ error: r.error }, r.status);
      const mk = (role, text) => ({ id: 'tn' + (++GID), role, text, at: '2026-11-05T10:00:00Z', scope: body.scope || null, agent: role === 'agent' ? 'Claydo' : null, suggested: null, suggest_error: null });
      const turns = [mk('user', body.message), mk('agent', r.reply)];
      (srv.threads[id] = srv.threads[id] || []).push(...turns);
      return J({ agent: { ref: 'global:claydo', name: 'Claydo' }, provider: 'claude', model: 'sonnet', turns });
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors };
}

const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });
const INPUT = '#desk-v1-camp-posy-input';
const value = (page) => page.inputValue(INPUT);

async function newCampaign(browser, { large, label }) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, srv);
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  // The real path: the same call Home's "New campaign" and the project card make.
  await page.evaluate(() => window.deskV1NewCampaignInProject('clayrune'));
  await settle(page, () => !!document.querySelector('.desk-v1-campaign [data-camp-chat]'));
  const trail = [];
  const snap = async (step) => { const v = await value(page); trail.push([step, v]); return v; };

  const text = 'Hi Posy, I want to start a new campaign using the latest and greatest desk features. The goal is 30 installs.';
  srv.reply = () => ({ reply: 'Thirty installs in a month is doable. A few questions first.' });
  await page.fill(INPUT, '');
  await page.locator(INPUT).pressSequentially(text);
  await snap('typed');
  if (large) await page.click('[data-chat-pop]');
  await page.click('[data-posy-send="desk-v1-camp-posy-input"]');
  await settle(page, () => /answered/.test((document.querySelector('[data-camp-chat-status]') || {}).textContent || ''));
  await snap('answered');
  if (!large) await page.click('[data-chat-pop]');
  await snap('popped');
  await page.waitForTimeout(1500);   // watch() ticks, store refreshes
  await snap('after1.5s');
  await page.evaluate(() => window.deskV1Render());
  await snap('rerender');
  if (VERBOSE) console.log('    ' + trail.map(([s, v]) => `${s}=${JSON.stringify(v.slice(0, 20))}`).join('  '));
  const after = trail.slice(1);
  after.every(([, v]) => v === '') ? ok(`${label}: box stays empty after the answer, the pop-out and a repaint`)
    : fail(`${label}: sent message came back: ${trail.map(([s, v]) => `${s}=${JSON.stringify(v.slice(0, 20))}`).join('  ')}`);
  pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e)).forEach((e) => fail('page error: ' + e));
  await ctx.close();
}


// The probe in desk-v1-campaign-chat.js (_noteReentry): names HOW the sent text came back, so the next
// real occurrence is diagnosable. It must stay silent for ordinary typing and for a failed send.
async function reentryProbe(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, srv);
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await page.evaluate(() => window.deskV1NewCampaignInProject('clayrune'));
  await settle(page, () => !!document.querySelector('.desk-v1-campaign [data-camp-chat]'));
  const sent = 'Probe message one';
  const recs = () => page.evaluate(() => (window.__deskChatReentry || []).map((r) => ({ how: r.how, inputType: r.inputType })));
  const send = async (reply) => {
    srv.reply = reply;
    let release; srv.gate = new Promise((r) => { release = r; });
    await page.locator(INPUT).pressSequentially(sent);
    await page.press(INPUT, 'Enter');
    await settle(page, () => !!document.querySelector('[data-chat-thinking]'));
    release(); srv.gate = null;
    await settle(page, () => !document.querySelector('[data-chat-thinking]') && !document.querySelector('#desk-v1-camp-posy-input').disabled);
  };
  await send(() => ({ reply: 'Fine.' }));
  const typedAgain = async () => { await page.locator(INPUT).pressSequentially(sent); };
  await typedAgain();
  (await recs()).length === 0 ? ok('probe silent while the same words are typed again key by key') : fail('probe fired on ordinary typing');
  await page.fill(INPUT, ''); await page.$eval(INPUT, (e) => e.dispatchEvent(new Event('input')));
  // A trusted whole-text insert (what dictation / an input method commit looks like): synthetic events lose their inputType.
  const cdp = await ctx.newCDPSession(page);
  await page.focus(INPUT);
  await cdp.send('Input.insertText', { text: sent });
  const afterInput = await recs();
  afterInput.length === 1 && afterInput[0].how === 'input' && afterInput[0].inputType === 'insertText'
    ? ok('a whole-text input jump after the answer is recorded with its inputType') : fail('input jump not recorded: ' + JSON.stringify(afterInput));
  await page.$eval(INPUT, (e) => { e.value = ''; e.dispatchEvent(new Event('input')); e.blur(); });
  await page.$eval(INPUT, (e, t) => { e.value = t; }, sent);   // a bare value, no input event
  await page.focus(INPUT);
  const afterFocus = await recs();
  afterFocus.length === 2 && afterFocus[1].how === 'focus' ? ok('a bare value that appears on focus is recorded as "focus"') : fail('focus re-entry not recorded: ' + JSON.stringify(afterFocus));
  await page.fill(INPUT, ''); await page.$eval(INPUT, (e) => e.dispatchEvent(new Event('input')));
  const before = (await recs()).length;
  await send(() => ({ status: 503, error: 'Controlled failure' }));
  const restored = await value(page), after = (await recs()).length;
  restored === sent && after === before ? ok('a failed send restores the request without tripping the probe') : fail(`failed send tripped the probe or lost the text: value=${JSON.stringify(restored)} status=${JSON.stringify(await page.textContent('[data-camp-chat-status]'))} posts=${srv.log.filter((r) => r.method === 'POST').length} records ${before}->${after}`);
  await ctx.close();
}

const browser = await chromium.launch();
try {
  console.log('live ON: new campaign, send then pop out');
  await newCampaign(browser, { large: false, label: 'new campaign, send inline then pop out' });
  await newCampaign(browser, { large: true, label: 'new campaign, send from the large view' });
  console.log('live ON: re-entry probe'); await reentryProbe(browser);
} catch (e) { fail('harness error: ' + (e && e.stack || e)); }
await browser.close();
if (bad) { console.error(`\n❌ FAIL — ${bad} case(s)`); process.exit(1); }
console.log("\n✅ PASS — a new campaign's sent message never returns to the box.");
