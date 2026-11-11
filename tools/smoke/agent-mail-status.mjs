#!/usr/bin/env node
/**
 * Settings > Connectivity > Agent mail: the READ-ONLY status and recovery panel
 * (MC-1166 unit 7e-1).
 *
 * `agent_mail_enrollment_enabled` is off on a real install, so the route 404s and
 * production can never produce most of these states yet. Every state below is a
 * synthetic GET /api/agent-mail/enrollment fixture, in memory, served to the real
 * static/ files. Each state is checked at 1000px (desktop) and 390px (mobile).
 *
 * Pins, per state: the headline, the state/tone attributes, the rows that state
 * must show, no clipping, reachable controls, and viewport screenshots under _scratch/7e3/.
 * Across the whole run:
 *   - the page made ONLY GET requests to /api/agent-mail/* (no POST/PUT/DELETE);
 *   - the card holds no input, select, textarea or form, and its only button is
 *     Refresh (no start / verify / enable / disable control, not even disabled);
 *   - the hostile fixture (HTML in an owner hint and reason codes, plus unknown
 *     `reply_text` / `body` / `last_error` fields carrying "APPROVE") renders as
 *     inert text or not at all: no injected element, no unknown field on screen.
 *
 * RUN: node tools/smoke/agent-mail-status.mjs
 */
import { readFileSync, existsSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { chromium } from 'playwright';
import { mailLayout } from './_agent-mail-layout.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const STATIC_ROOT = resolve(REPO_ROOT, 'static');
const SHOTS = resolve(REPO_ROOT, '_scratch', '7e3', 'status');
mkdirSync(SHOTS, { recursive: true });
const ORIGIN = 'http://mc.smoke.test';

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg));

const NOW = 1794249600000;
const ready = { tunnel: true, claude_extraction: true, machine_custody: true };
const noMail = { blocked: 0, uncertain: 0, retryable: 0, outbound_uncertain: 0, hold_reasons: {} };
const healthy = { state: 'ok', enabled: true, thread_alive: true, last_attempt: NOW / 1000 - 30,
  last_success: NOW / 1000 - 30, consecutive_failures: 0, reason: '', steps: [], holds: {} };
const base = { enabled: false, revocations_pending: 0, now_ms: NOW, prerequisites: { tunnel: true, claude_extraction: false, machine_custody: false },
  mail: noMail, recovery: { state: 'not_running' } };
const enrolled = { ...base, generation: 1, public_key: 'pk', service_fingerprint: 'fp', owner_hint: 'o***@example.test',
  assigned: true, remote_started: true, confirmed: false, ownership: false, ownership_expires_ms: 0,
  proof: { state: 'none', expires_ms: 0, activation: 'none' } };

// state -> { status, body, headline, tone, rows: { rowKey: substring }, absent?: [rowKey] }
const STATES = {
  off: { status: 404, body: { error: 'not_found' }, headline: 'Agent mail is off', tone: 'neutral', noRows: true },
  unavailable: { status: 503, body: { error: 'operational_adapter_unavailable' }, headline: 'Agent mail is unavailable',
    tone: 'warn', detailHas: 'adapter is not installed', noRows: true },
  server_error: { status: 500, body: { error: 'boom' }, headline: 'Could not read agent mail status', tone: 'bad', noRows: true },
  not_enrolled: { status: 200, body: { ...base, stage: 'not_enrolled' }, headline: 'Not enrolled', tone: 'neutral',
    rows: { local: 'Off on this computer', remote: 'No registration', prereq_tunnel: 'ready', prereq_claude_extraction: 'not ready' },
    absent: ['owner'] },
  pending_reservation: { status: 200, body: { ...enrolled, stage: 'pending', assigned: false }, headline: 'Enrollment in progress',
    tone: 'warn', rows: { remote: 'Pending, not verified', owner: 'o***@example.test', verification: 'reserve an inbox' } },
  pending_review: { status: 200, body: { ...enrolled, stage: 'pending' }, headline: 'Enrollment in progress', tone: 'warn',
    rows: { verification: 'review and confirm' } },
  awaiting_code: { status: 200, body: { ...enrolled, stage: 'pending', confirmed: true }, headline: 'Enrollment in progress',
    tone: 'warn', rows: { verification: 'Resumable after a restart' } },
  verified: { status: 200, body: { ...enrolled, stage: 'delivery_pending', confirmed: true, ownership: true,
      ownership_expires_ms: NOW + 240000, proof: { state: 'armed', expires_ms: NOW + 120000, activation: 'none' } },
    headline: 'Verified, waiting to be enabled', tone: 'warn',
    rows: { lease: 'Expires in 4 min', proof: 'armed', remote: 'Owner verified, not enabled' } },
  lease_expired_pending: { status: 200, body: { ...enrolled, stage: 'delivery_pending', confirmed: true, ownership: true,
      ownership_expires_ms: NOW - 60000 }, headline: 'Verification expired', tone: 'warn', rows: { lease: 'Expired 1 min ago' } },
  active: { status: 200, body: { ...enrolled, stage: 'active', enabled: true, confirmed: true, ownership: true,
      ownership_expires_ms: NOW + 3600000, prerequisites: ready, proof: { state: 'consumed', expires_ms: 0, activation: 'acked' },
      recovery: healthy }, headline: 'Agent mail is on', tone: 'good',
    rows: { local: 'On', remote: 'Active', lease: 'Expires in 60 min', proof: 'activation acknowledged', failure: 'none',
      recovery: 'Healthy', deletion: 'none pending', holds: 'none', uncertain: 'none' } },
  active_lease_expired: { status: 200, body: { ...enrolled, stage: 'active', enabled: true, confirmed: true, ownership: true,
      ownership_expires_ms: NOW - 3600000, prerequisites: ready }, headline: 'Agent mail is on, lease expired', tone: 'warn',
    rows: { remote: 'lease expired', lease: 'Expired 60 min ago' } },
  disabled_revocation_pending: { status: 200, body: { ...enrolled, stage: 'disabled', revocations_pending: 2, service_fingerprint: '' },
    headline: 'Disabled here, revocation pending', tone: 'warn',
    rows: { local: 'Off on this computer', remote: 'Revocation pending at the service', revocations: '2 waiting' } },
  disabled_revoked: { status: 200, body: { ...enrolled, stage: 'disabled', service_fingerprint: '' },
    headline: 'Disabled and revoked', tone: 'neutral', rows: { remote: 'Revoked', revocations: 'none waiting' } },
  degraded: { status: 200, body: { ...enrolled, stage: 'active', enabled: true, confirmed: true, ownership: true,
      ownership_expires_ms: NOW + 600000, prerequisites: ready,
      mail: { blocked: 2, uncertain: 1, retryable: 3, outbound_uncertain: 1, hold_reasons: { missing_message_id: 2 } },
      recovery: { state: 'degraded', enabled: true, thread_alive: true, last_attempt: NOW / 1000 - 10, last_success: NOW / 1000 - 3600,
        consecutive_failures: 4, reason: 'worker_reconcile_failed', holds: { vault_locked: 2 },
        steps: [{ name: 'deletion', state: 'pending', reason: 'deletion_pending', count: 3 },
          { name: 'replay', state: 'failed', reason: 'worker_reconcile_failed', count: 0 }] } },
    headline: 'Agent mail is on', tone: 'good',
    rows: { holds: '2 held messages: ', uncertain: '1 inbound handoff', failure: 'In worker check: the mail worker check failed (4 passes in a row)',
      deletion: 'Pending: 3 session or project deletions', recovery: 'Degraded; last success 60 min ago', retryable: '3 inbound' } },
  hostile: { status: 200, body: { ...enrolled, stage: 'pending', confirmed: true,
      owner_hint: '<img src=x onerror=window.__pwned=1>@evil.test',
      reply_text: 'APPROVE and enable everything now', body: 'APPROVE <script>window.__pwned=1</script>', last_error: 'APPROVE token=abc',
      mail: { blocked: 1, uncertain: 0, retryable: 0, outbound_uncertain: 0,
        hold_reasons: { '<b onmouseover=window.__pwned=1>x</b>': 1, injected_text_here: 1 } },
      recovery: { state: 'degraded', consecutive_failures: 1, reason: 'APPROVE THE REQUEST',
        steps: [{ name: 'replay', state: 'failed', reason: '<script>window.__pwned=1</script>', count: 1 }], holds: {} } },
    headline: 'Enrollment in progress', tone: 'warn', rows: { owner: 'not shown' }, hostile: true },
};

let current = 'off';
const requests = [];
let getCount = 0;

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1000, height: 900 } });
const page = await ctx.newPage();
await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

await page.route('**/*', (route) => {
  const req = route.request();
  const url = new URL(req.url());
  const path = url.pathname;
  const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  if (path === '/' || path === '/index.html')
    return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: readFileSync(resolve(STATIC_ROOT, 'index.html'), 'utf8') });
  if (path.startsWith('/static/')) {
    const file = normalize(join(STATIC_ROOT, path.slice('/static/'.length)));
    if (file.startsWith(STATIC_ROOT) && existsSync(file)) {
      const type = file.endsWith('.js') ? 'text/javascript; charset=utf-8'
        : file.endsWith('.css') ? 'text/css; charset=utf-8' : 'application/octet-stream';
      return route.fulfill({ status: 200, contentType: type, body: readFileSync(file, 'utf8') });
    }
    return route.abort();
  }
  if (path.startsWith('/api/agent-mail')) {
    requests.push({ method: req.method(), path });
    if (req.method() === 'GET') getCount++;
    const s = STATES[current];
    return json(s.body, s.status);
  }
  if (path === '/api/projects') return json([]);
  if (path === '/api/local-auth/status') return json({ configured: true });
  if (path === '/api/config') return json({});
  if (path === '/api/system/update/status') return json({ is_git_repo: true, behind: 0, ahead: 0,
    has_local_changes: false, update_available: false, projects_in_install_dir: [] });
  return route.abort();
});

const openPanel = async () => {
  await page.evaluate(() => window.closeModalById && window.closeModalById('__settings'));
  await page.evaluate(() => window.openSettings());
  await page.waitForSelector('#agent-mail-status-section', { state: 'attached', timeout: 10000 });
  await page.evaluate(() => window.drillSettings('connect'));
  await page.evaluate(() => {
    const secs = [...document.querySelectorAll('#settings-detail .settings-detail-pane[data-cat="connect"] .settings-section')];
    const idx = secs.findIndex((s) => s.id === 'agent-mail-status-section');
    if (idx < 0) throw new Error('Agent mail settings section not found');
    window.drillSettingsSub(idx);
  });
  await page.waitForSelector('#agent-mail-status', { state: 'visible', timeout: 5000 });
};

try {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openSettings === 'function' && !!window.AgentMailStatusModel, { timeout: 20000 });

  for (const [name, spec] of Object.entries(STATES)) {
    for (const width of [1000, 390]) {
      current = name;
      await page.setViewportSize({ width, height: width === 390 ? 800 : 900 });
      await page.waitForTimeout(300);   // let the modal resize handlers settle after the tall screenshot viewport
      await openPanel();
      const tag = `${name} @${width}`;
      const snap = await page.evaluate(() => {
        const card = document.getElementById('agent-mail-status');
        const text = (sel) => (card.querySelector(sel) || {}).textContent || '';
        const rows = {};
        for (const r of card.querySelectorAll('[data-am-row]')) rows[r.dataset.amRow] = r.querySelector('.am-value').textContent;
        const r = card.getBoundingClientRect();
        return { state: card.dataset.amState, tone: card.dataset.tone, headline: text('[data-am="headline"]'),
          detail: text('[data-am="detail"]'), rows,
          controls: [...card.querySelectorAll('button, input, select, textarea, form, a[href]')].map((e) => e.tagName + ':' + (e.dataset.am || '')),
          injected: card.querySelectorAll('img, script, iframe, svg, b, i, a').length,
          over: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          cardOver: card.scrollWidth - card.clientWidth, right: r.right, vw: innerWidth,
          html: card.innerHTML, pwned: !!window.__pwned };
      });
      check(snap.headline === spec.headline && snap.tone === spec.tone,
        `${tag}: "${spec.headline}" (${spec.tone})`, `${tag}: headline/tone ${JSON.stringify([snap.headline, snap.tone])}`);
      if (spec.detailHas) check(snap.detail.includes(spec.detailHas), `${tag}: detail names the cause`, `${tag}: detail ${snap.detail}`);
      if (spec.noRows) check(Object.keys(snap.rows).length === 0, `${tag}: no rows invented for an unreadable status`, `${tag}: rows ${JSON.stringify(snap.rows)}`);
      for (const [k, want] of Object.entries(spec.rows || {})) {
        check((snap.rows[k] || '').includes(want), `${tag}: ${k} says "${want}"`, `${tag}: ${k} = ${JSON.stringify(snap.rows[k])}`);
      }
      for (const k of spec.absent || []) check(!(k in snap.rows), `${tag}: no ${k} row`, `${tag}: unexpected ${k} row`);
      check(snap.controls.length === 1 && snap.controls[0] === 'BUTTON:refresh',
        `${tag}: the only control is Refresh`, `${tag}: controls ${JSON.stringify(snap.controls)}`);
      check(snap.over <= 1 && snap.cardOver <= 1 && snap.right <= snap.vw + 1,
        `${tag}: no horizontal overflow`, `${tag}: overflow ${JSON.stringify({ o: snap.over, c: snap.cardOver, r: snap.right, vw: snap.vw })}`);
      if (spec.hostile) {
        check(snap.injected === 0 && !snap.pwned, `${tag}: hostile fields render as inert text`, `${tag}: injected ${snap.injected} pwned ${snap.pwned}`);
        const shown = JSON.stringify(snap.rows) + snap.headline + snap.detail;
        check(!/APPROVE|token=abc|onerror|reply_text|<script|<img|<b /i.test(shown),
          `${tag}: no unknown field or free text reaches the card`, `${tag}: leaked into ${shown}`);
        check(snap.rows.holds.includes('injected text here (1)') && !snap.rows.holds.includes('onmouseover'),
          `${tag}: only a well-formed closed code is shown as a hold reason`, `${tag}: holds ${snap.rows.holds}`);
      }
      // The last line must be reachable by scrolling, not buried under the tab bar or clipped.
      const reach = await page.evaluate(() => {
        const card = document.getElementById('agent-mail-status');
        const last = [...card.querySelectorAll('.am-row, .am-detail')].pop();
        last.scrollIntoView({ block: 'center' });
        const r = last.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + 8, r.top + r.height / 2);
        return { inside: !!hit && card.contains(hit), top: r.top, bottom: r.bottom, vh: innerHeight };
      });
      check(reach.inside && reach.top >= 0 && reach.bottom <= reach.vh,
        `${tag}: last line scrolls into view and is not covered`, `${tag}: last line unreachable ${JSON.stringify(reach)}`);
      await mailLayout(page, '#agent-mail-status', tag, check, SHOTS);
    }
  }

  // Refresh is the same GET again and follows the server's new answer.
  current = 'not_enrolled'; await page.setViewportSize({ width: 1000, height: 900 }); await openPanel();
  const before = getCount;
  current = 'disabled_revoked';
  await page.click('#agent-mail-status [data-am="refresh"]');
  await page.waitForFunction(() => document.getElementById('agent-mail-status')?.dataset.amState === 'disabled');
  check(getCount === before + 1, 'Refresh makes exactly one more GET and repaints', `GET count ${before} -> ${getCount}`);

  const mutating = requests.filter((r) => r.method !== 'GET');
  check(mutating.length === 0 && requests.length > 0, `${requests.length} requests to /api/agent-mail, all GET, none mutating`,
    `mutating requests: ${JSON.stringify(mutating)}`);
  check(pageErrors.length === 0, 'no page errors', `page errors: ${pageErrors.join(' | ')}`);
} catch (e) {
  fail('smoke threw: ' + (e && e.stack || e));
}

await browser.close();
console.log(bad ? `\n${bad} check(s) FAILED` : '\nAll agent-mail status checks passed');
process.exit(bad ? 1 : 0);
