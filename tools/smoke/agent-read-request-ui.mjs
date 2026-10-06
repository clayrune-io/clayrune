#!/usr/bin/env node
/**
 * MC-1059 pieces A and B, the two screens. Hermetic: real index.html + static/, every /api route mocked.
 *
 * A — the agent-read request card (static/js/agent-read-card.js). The server posts one
 *     `[agent-read-request:<id>]` line into an agent's chat when a read was refused; the card asks the
 *     server for its words. Asserts:
 *       - the marker line renders one card, "<agent> wants to read <site> through <profile>", with
 *         Allow once / Always allow / Ignore, and the raw marker is not visible;
 *       - the words come from the server, not the line: an unknown id renders "expired", no buttons;
 *       - Ignore sends one POST {decision:'ignore'} with NO passcode and settles the card;
 *       - Allow once and Always allow open the passcode prompt, and the POST carries the passcode;
 *         cancelling the prompt sends nothing and leaves the card answerable;
 *       - the card reloaded from a cold render (a transcript restored via agentPanelHTML) shows the same
 *         card, and a settled request shows its outcome, not buttons;
 *       - a 409 (answered elsewhere) refreshes the card to the real state.
 *
 * B — Desk Connections (static/js/desk-v1-connect-agent-read.js). A browser-pane connection's detail
 *     shows which sites agents may read through its saved profile and edits them through the same
 *     passcode-gated PUT. Asserts: the sites list from GET /api/browser/agent-read, Remove and Allow site
 *     each PUT {enabled, domains} with the passcode, cancelling the prompt sends nothing, a refused PUT
 *     says so in place and keeps the list, and an API-read connection shows no such block.
 *
 * RUN   cd tools/smoke && node agent-read-request-ui.mjs
 * Screenshots go to MC_SMOKE_SHOT_DIR if set.  Exit 0 = all checks pass; 1 = a check failed / harness error.
 */
import { readFileSync, readdirSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { seedDeskV1Fixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const ASSETS_DIR = resolve(REPO_ROOT, 'assets');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const SHOT_DIR = process.env.MC_SMOKE_SHOT_DIR || '';
if (SHOT_DIR) mkdirSync(SHOT_DIR, { recursive: true });

const MIME = { '.webp': 'image/webp', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml' };
const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));
for (const f of readdirSync(ASSETS_DIR)) {
  const ext = f.slice(f.lastIndexOf('.'));
  if (MIME[ext]) STATIC[`/assets/${f}`] = [MIME[ext], readFileSync(resolve(ASSETS_DIR, f))];
}

const PROJECT = {
  id: 'smoke_arc', name: 'Agent read smoke', status: 'active', domain: 'general', emoji: '🧪',
  description: '', summary: '', current_task: 'Idle', next_action: '', blocked: false, blocked_reason: null,
  activity_log: [], backlog: [], project_path: '/smoke/arc', last_updated: '2026-10-06T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null, display_order: 0,
  provider: 'claude', use_streaming_agent: true, roster: [],
  distiller_mode: 'proposed', distiller_min_recurrence: 3, distiller_max_topics_per_session: 3,
  distiller_max_preferences_per_session: 3, distiller_max_explorations_per_session: 3,
  distiller_min_turns: 5, distiller_skip_errors: true,
};

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, m) => (cond ? ok(m) : fail(m));

// ── mock server state ────────────────────────────────────────────────────────
const RID = { pend: 'aa11bb22cc33', once: 'bb22cc33dd44', always: 'cc33dd44ee55', ign: 'dd44ee55ff66', race: 'ee55ff66aa77', done: 'ff66aa77bb88', gone: '0a1b2c3d4e5f' };
const requests = {
  [RID.pend]: { id: RID.pend, state: 'pending', agent_name: 'Tobin', domain: 'linkedin.com', profile: 'main' },
  [RID.once]: { id: RID.once, state: 'pending', agent_name: 'Tobin', domain: 'reddit.com', profile: 'main' },
  [RID.always]: { id: RID.always, state: 'pending', agent_name: 'Dave', domain: 'x.com', profile: 'main' },
  [RID.ign]: { id: RID.ign, state: 'pending', agent_name: 'Wren', domain: 'news.ycombinator.com', profile: 'main' },
  [RID.race]: { id: RID.race, state: 'pending', agent_name: 'Tobin', domain: 'github.com', profile: 'main' },
  [RID.done]: { id: RID.done, state: 'always', agent_name: 'Dave', domain: 'linkedin.com', profile: 'main' },
};
const decisions = [];      // {id, body}
const puts = [];           // {name, body}
const srv = { policy: { main: { enabled: true, domains: ['linkedin.com', 'x.com'] } }, refusePut: false };

async function fulfill(route) {
  const req = route.request();
  const path = new URL(req.url()).pathname;
  const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  const hit = STATIC[path];
  if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
  if (path === '/api/projects') return J([PROJECT]);
  if (path === '/api/config') return J({ desk_v1: true, user_timezone: '' });
  if (path === '/api/characters') return J([]);
  if (path === '/api/local-auth/status') return J({ configured: true });
  let m = path.match(/^\/api\/browser\/agent-read\/requests\/([a-f0-9]+)$/);
  if (m) return requests[m[1]] ? J(requests[m[1]]) : J({ error: 'this request has expired or is unknown' }, 404);
  m = path.match(/^\/api\/browser\/agent-read\/requests\/([a-f0-9]+)\/decision$/);
  if (m && req.method() === 'POST') {
    const body = JSON.parse(req.postData() || '{}');
    decisions.push({ id: m[1], body });
    const rec = requests[m[1]];
    if (m[1] === RID.race) { rec.state = 'ignored'; return J({ error: 'this request was already answered (ignored)', state: 'ignored' }, 409); }
    if (body.decision !== 'ignore' && body.passcode !== 'smoke-passcode') return J({ error: 'passcode_required' }, 403);
    const state = { allow_once: 'allowed_once', always: 'always', ignore: 'ignored' }[body.decision];
    rec.state = state;
    return J({ ok: true, state });
  }
  m = path.match(/\/api\/desk\/presence\/[^/]+\/accounts\/([^/]+)\/read$/);
  if (m && req.method() === 'PATCH') {
    const body = JSON.parse(req.postData() || '{}');
    return J({ channel_id: m[1], platform: body.platform, read_via: body.read_via || 'pane' });
  }
  if (path === '/api/browser/agent-read') return J({ profiles: srv.policy });
  if (path === '/api/browser/profiles') return J({ profiles: [{ name: 'main', size_mb: 1, last_used: null, in_use_by: null }] });
  m = path.match(/^\/api\/browser\/profiles\/([^/]+)\/agent-read$/);
  if (m && req.method() === 'PUT') {
    const body = JSON.parse(req.postData() || '{}');
    puts.push({ name: m[1], body });
    if (srv.refusePut || body.passcode !== 'smoke-passcode') return J({ error: srv.refusePut ? 'this action needs a human' : 'passcode_required' }, 403);
    srv.policy[m[1]] = { enabled: !!body.enabled, domains: body.domains };
    return J({ ok: true, profile: m[1], enabled: !!body.enabled, domains: body.domains });
  }
  return route.abort();
}

async function answerPrompt(page, passcode, { cancel = false } = {}) {
  const win = await page.waitForSelector('[data-modal-id^="__human-proof-"]', { timeout: 5000 }).catch(() => null);
  if (!win) return false;
  await page.evaluate(({ passcode, cancel }) => {
    const modalId = document.querySelector('[data-modal-id^="__human-proof-"]').dataset.modalId;
    if (cancel) { window._hpCancel(modalId); return; }
    document.getElementById(`hp-passcode-${modalId}`).value = passcode;
    window._hpSubmit(modalId);
  }, { passcode, cancel });
  return true;
}

async function newPage(browser, viewport) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
  await page.route('**/*', fulfill);
  await seedDeskV1Fixtures(page);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  return { ctx, page, pageErrors };
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();

  // ── A: the card in the chat ────────────────────────────────────────────────
  console.log('A — the request card');
  const { ctx, page, pageErrors } = await newPage(browser, { width: 1280, height: 1000 });

  // Live path: lines streamed into a chat output, as a real turn does.
  await page.evaluate((rid) => {
    const modal = document.createElement('div');
    modal.dataset.modalId = 'smoke_arc';
    modal.style.cssText = 'position:relative;width:760px;background:var(--surface);padding:8px';
    const out = document.createElement('div');
    out.id = 'agent-output-arc_sid';
    out.className = 'agent-output';
    modal.appendChild(out);
    document.body.prepend(modal);
    window.appendAgentLine('arc_sid', 'I could not read that page; asking you.');
    for (const id of Object.values(rid)) window.appendAgentLine('arc_sid', `[agent-read-request:${id}]`);
  }, RID);
  await page.waitForSelector(`#agent-output-arc_sid .agent-read-card-mount[data-rid="${RID.gone}"] .agent-read-card`, { timeout: 5000 }).catch(() => {});
  const card = (rid) => page.locator(`#agent-output-arc_sid .agent-read-card-mount[data-rid="${rid}"]`);

  check(await page.locator('#agent-output-arc_sid .agent-read-card').count() === Object.keys(RID).length, 'each marker line rendered one card');
  const title = (await card(RID.pend).locator('.agent-read-card-title').innerText()).replace(/\s+/g, ' ');
  check(title === 'Tobin wants to read linkedin.com through main', `the card says who, which site and which login (got "${title}")`);
  check(await card(RID.pend).locator('[data-arc]').allInnerTexts().then((t) => t.join('|') === 'Allow once|Always allow|Ignore'), 'the three buttons are Allow once / Always allow / Ignore');
  const visibleMarker = await page.evaluate(() => [...document.querySelectorAll('#agent-output-arc_sid .agent-line')].filter((e) => /\[agent-read-request:/.test(e.textContent) && e.offsetParent !== null).length);
  check(visibleMarker === 0, 'the raw marker line is not visible');
  check(await card(RID.gone).locator('[data-arc]').count() === 0
        && /expired/i.test(await card(RID.gone).locator('[data-arc-msg]').innerText()), 'an unknown id renders "expired" with no buttons (the words come from the server)');
  check(await card(RID.done).locator('[data-arc]').count() === 0
        && /Always allowed/.test(await card(RID.done).locator('[data-arc-msg]').innerText()), 'a settled request shows its outcome, not buttons');
  if (SHOT_DIR) await card(RID.pend).screenshot({ path: resolve(SHOT_DIR, 'agent-read-card.png') });

  // Ignore: one POST, no passcode, no prompt.
  await card(RID.ign).locator('[data-arc="ignore"]').click();
  await card(RID.ign).locator('.agent-read-card[data-state="ignored"]').waitFor({ timeout: 4000 }).catch(() => {});
  const ign = decisions.filter((d) => d.id === RID.ign);
  check(ign.length === 1 && ign[0].body.decision === 'ignore' && !('passcode' in ign[0].body), 'Ignore sent one POST {decision:"ignore"} with no passcode');
  check(await page.locator('[data-modal-id^="__human-proof-"]').count() === 0, 'Ignore opened no passcode prompt');
  check(await card(RID.ign).locator('[data-arc]').count() === 0 && /Ignored/.test(await card(RID.ign).locator('[data-arc-msg]').innerText()), 'Ignore settles the card');

  // Cancelling the prompt sends nothing and leaves the card answerable.
  const before = decisions.length;
  await card(RID.pend).locator('[data-arc="allow_once"]').click();
  check(await answerPrompt(page, '', { cancel: true }), 'Allow once opens the passcode prompt');
  await page.waitForTimeout(300);
  check(decisions.length === before, 'cancelling the prompt sent nothing');
  check(await card(RID.pend).locator('[data-arc="allow_once"]:not([disabled])').count() === 1, 'the card is still answerable after cancel');

  // Allow once with the passcode.
  await card(RID.once).locator('[data-arc="allow_once"]').click();
  await answerPrompt(page, 'smoke-passcode');
  await card(RID.once).locator('.agent-read-card[data-state="allowed_once"]').waitFor({ timeout: 4000 }).catch(() => {});
  const once = decisions.filter((d) => d.id === RID.once);
  check(once.length === 1 && once[0].body.decision === 'allow_once' && once[0].body.passcode === 'smoke-passcode', 'Allow once POSTed {decision:"allow_once"} with the passcode');
  check(/Allowed once/.test(await card(RID.once).locator('[data-arc-msg]').innerText()), 'the card shows "Allowed once"');

  // Always allow with the passcode.
  await card(RID.always).locator('[data-arc="always"]').click();
  await answerPrompt(page, 'smoke-passcode');
  await card(RID.always).locator('.agent-read-card[data-state="always"]').waitFor({ timeout: 4000 }).catch(() => {});
  const alw = decisions.filter((d) => d.id === RID.always);
  check(alw.length === 1 && alw[0].body.decision === 'always' && alw[0].body.passcode === 'smoke-passcode', 'Always allow POSTed {decision:"always"} with the passcode');

  // 409: answered elsewhere. The card reloads to the real state.
  await card(RID.race).locator('[data-arc="allow_once"]').click();
  await answerPrompt(page, 'smoke-passcode');
  await card(RID.race).locator('.agent-read-card[data-state="ignored"]').waitFor({ timeout: 4000 }).catch(() => {});
  check(await card(RID.race).locator('.agent-read-card[data-state="ignored"]').count() === 1, 'a 409 refreshes the card to the state the server holds');

  // Cold render: the same marker inside a restored transcript.
  const cold = await page.evaluate((rid) => {
    const holder = document.createElement('div');
    holder.innerHTML = window.agentReadCardPlaceholderHTML(rid);
    document.body.appendChild(holder);
    window.mountAgentReadCards(holder);
    return new Promise((res) => setTimeout(() => res(holder.querySelector('.agent-read-card-title') ? holder.querySelector('.agent-read-card-title').textContent.replace(/\s+/g, ' ').trim() : ''), 500));
  }, RID.done);
  check(cold === 'Dave wants to read linkedin.com through main', `the cold-render placeholder mounts the same card (got "${cold}")`);

  // ── B: Desk Connections ───────────────────────────────────────────────────
  console.log('B — the Connections block');
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-connections] [data-conn-tile]', { timeout: 4000 });
  await page.click('[data-conn-tile="ch-x-ron"]');
  await page.waitForSelector('[data-conn-detail="ch-x-ron"] [data-agentread-site]', { timeout: 4000 });
  const sites = () => page.$$eval('[data-conn-detail="ch-x-ron"] [data-agentread-site]', (els) => els.map((e) => e.dataset.agentreadSite));
  check(JSON.stringify(await sites()) === JSON.stringify(['linkedin.com', 'x.com']), `the connection lists the sites the profile allows (${JSON.stringify(await sites())})`);
  if (SHOT_DIR) await page.screenshot({ path: resolve(SHOT_DIR, 'connections-agent-read.png') });

  // Cancel sends nothing.
  await page.fill('[data-conn-detail="ch-x-ron"] [data-agentread-input]', 'reddit.com');
  await page.click('[data-conn-detail="ch-x-ron"] [data-agentread-add]');
  check(await answerPrompt(page, '', { cancel: true }), 'Allow site opens the passcode prompt');
  await page.waitForTimeout(300);
  check(puts.length === 0, 'cancelling the prompt sent no PUT');

  // Add.
  await page.click('[data-conn-detail="ch-x-ron"] [data-agentread-add]');
  await answerPrompt(page, 'smoke-passcode');
  await page.waitForSelector('[data-conn-detail="ch-x-ron"] [data-agentread-site="reddit.com"]', { timeout: 4000 }).catch(() => {});
  check(puts.length === 1 && puts[0].name === 'main' && puts[0].body.enabled === true
        && JSON.stringify(puts[0].body.domains) === JSON.stringify(['linkedin.com', 'x.com', 'reddit.com']) && puts[0].body.passcode === 'smoke-passcode',
        `Allow site PUT the whole list with the passcode (${JSON.stringify(puts[0] && puts[0].body)})`);
  check((await sites()).includes('reddit.com'), 'the new site shows in the list');

  // Remove.
  await page.click('[data-conn-detail="ch-x-ron"] [data-agentread-remove="x.com"]');
  await answerPrompt(page, 'smoke-passcode');
  await page.waitForSelector('[data-conn-detail="ch-x-ron"] [data-agentread-site="x.com"]', { state: 'detached', timeout: 4000 }).catch(() => {});
  check(puts.length === 2 && JSON.stringify(puts[1].body.domains) === JSON.stringify(['linkedin.com', 'reddit.com']), 'Remove PUT the list without the site');
  check(!(await sites()).includes('x.com'), 'the removed site is gone from the list');

  // A refused PUT says so in place and keeps the list.
  srv.refusePut = true;
  await page.click('[data-conn-detail="ch-x-ron"] [data-agentread-remove="reddit.com"]');
  await answerPrompt(page, 'smoke-passcode');
  await page.waitForFunction(() => /Could not save/.test((document.querySelector('[data-conn-detail="ch-x-ron"] [data-agentread-status]') || {}).textContent || ''), null, { timeout: 4000 }).catch(() => {});
  check(/Could not save it: this action needs a human/.test(await page.textContent('[data-conn-detail="ch-x-ron"] [data-agentread-status]')), 'a refused PUT says so in place');
  check((await sites()).includes('reddit.com'), 'a refused PUT leaves the list as it was');
  srv.refusePut = false;

  // A connection reading through the paid API has no such block.
  await page.click('[data-conn-detail="ch-x-ron"] [data-readvia="api"]');
  await page.waitForFunction(() => !document.querySelector('[data-conn-detail="ch-x-ron"] [data-agentread-for]'), null, { timeout: 4000 }).catch(() => {});
  check(await page.locator('[data-conn-detail="ch-x-ron"] [data-agentread-for]').count() === 0, 'a connection reading through the API shows no agent-read block');
  await page.click('[data-conn-detail="ch-x-ron"] [data-readvia="pane"]');
  await page.waitForSelector('[data-conn-detail="ch-x-ron"] [data-agentread-site]', { timeout: 4000 }).catch(() => {});
  check(await page.locator('[data-conn-detail="ch-x-ron"] [data-agentread-for]').count() === 1, 'switching back to the browser pane shows it again');

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail('uncaught: ' + e));
  await ctx.close();
  exitCode = bad === 0 ? 0 : 1;
  console.log(exitCode === 0 ? '\n✅ PASS — agent-read request card + Connections block.' : `\n❌ FAIL — ${bad} check(s).`);
} catch (err) {
  console.error('❌ FAIL — smoke harness error:', err && err.stack ? err.stack : err);
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
