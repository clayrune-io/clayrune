#!/usr/bin/env node
/**
 * Hivemind review / roles UI smoke (BUILTINS-4 HR-8a).
 *
 * Serves THIS checkout's static/index.html + static/js + static/css from disk
 * through Playwright page.route (never the :5199 server's assets) against
 * controlled /api/hivemind/* responses shaped like mc/hivemind_run_roles.py.
 * Every other request is aborted.
 *
 * Proves, at 1440px and 390px (mobile context):
 *   - requested character + effective engine, reviewer, exact reviewed commit,
 *     named checks and hold/report reason are on screen and inside the viewport
 *   - "workers complete, review pending", provisional, changes_requested,
 *     stale-claimed-accepted, analysis-only, no-changes, legacy and
 *     roster-without-roles never render a green (`.hmr-tone-ok`) badge
 *   - a genuinely accepted run does render exactly the green badge
 *   - reviewer count is shown apart from the worker count; runs modal sends
 *     role=reviewer and shows role counts / per-row role+character+engine
 *   - list rows (cross-project Hivemind view) carry the same stage chips
 *
 * Run (worktree: link the main checkout's tools/smoke/node_modules first):
 *   node tools/smoke/hivemind-roster.mjs
 * Screenshots: _scratch/hivemind_roster/<case>_<1440|390>_<top|detail>.png
 */
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const SHOTS = resolve(REPO_ROOT, '_scratch', 'hivemind_roster');
mkdirSync(SHOTS, { recursive: true });
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const PROJECTS_JSON = readFileSync(resolve(__dirname, 'fixtures', 'projects.json'), 'utf8');
const STATIC = loadStaticJsCss(REPO_ROOT);
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_alpha';
const SHA = 'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678';
const SHA2 = '0f1e2d3c4b5a69788796a5b4c3d2e1f001122334';
const TREE = 'c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00';

const ENG_A = { provider: 'codex', model: 'gpt-6', effort: 'high', session_id: 'sess_worker_aaaaaaaa' };
const ENG_B = { provider: 'claude', model: 'claude-opus-5-5', effort: 'medium', session_id: 'sess_worker_bbbbbbbb' };

function wsRow(id, title, outcome, extra = {}) {
  return { id, title, status: 'completed', dependencies: [], requested_character: id === 'ws_002' ? 'global:ui-fixer' : 'global:builder',
    assignment_attempt: 1, effective_engine: id === 'ws_002' ? ENG_B : ENG_A,
    integration: { outcome, stale: false, candidate_key: 'ck_' + id, reason: '' }, ...extra };
}
function review(ws, over = {}) {
  return { ws_id: ws, state: 'provisional', phase: 'reviewing', candidate_key: 'ck_' + ws, reviewed_commit: SHA,
    pre_merge_sha: SHA2, incoming_branch: 'clayrune/agent/' + ws, incoming_tip: SHA2, merged_tree: TREE,
    checks: { named: [{ name: 'hivemind-roster.mjs', ok: true }, { name: 'boot-smoke.mjs', ok: true }], ran: true, note: '' },
    reviewer: { session_id: 'sess_reviewer_1234567890', character: 'global:code-reviewer', engine: { provider: 'claude', model: 'claude-fable-5-1', effort: 'high' } },
    review_state: 'pending', verdict: '', hold_reason: '', deadline_at: 1790000000, report_path: 'docs/_journal/review-' + ws + '.md',
    recorded_at: '2026-11-01T10:00:00Z', ...over };
}
function roles(stage, over = {}) {
  const wss = [wsRow('ws_001', 'Wire the list chips', 'not_started'), wsRow('ws_002', 'Style the detail panel', 'not_started'), wsRow('ws_003', 'Write the smoke', 'not_started')];
  return { schema_version: 1, stage, accepted: stage === 'accepted', manifest_status: 'completed', as_of: '2026-11-01T10:05:00Z',
    reason: '', unverified_reasons: [], workstreams: wss,
    reviewer: { requested_character: 'global:code-reviewer', session_ids: ['sess_reviewer_1234567890', 'sess_reviewer_0987654321'], count: 2 },
    reviews: [], hold: null, counts: { workstreams: 3, accepted: 0, provisional: 0, skipped: 0, failed: 0 }, ...over };
}
function withOutcomes(r, outcomes) { r.workstreams.forEach((w, i) => { w.integration.outcome = outcomes[i] || w.integration.outcome; }); return r; }

const CASES = {
  hm_sub: { title: 'Disclosed substitutions', status: 'active', roster: true,
    roles: (() => {
      const r = roles('review_pending');
      Object.assign(r.workstreams[0], { requested_character: 'global:missing-worker',
        resolved_character: 'global:standin', substituted: true,
        substitution_step: 'role_fallback', substitution_reason: 'missing_at_attempt' });
      const rv = review('ws_001');
      Object.assign(rv.reviewer, { requested_character: 'global:missing-reviewer',
        resolved_character: '', substituted: true,
        substitution_step: 'tier_fallback', substitution_reason: 'never_defined' });
      r.reviews = [rv];
      return r;
    })(),
    expect: { text: ['requested global:missing-worker → ran as global:standin',
      'requested global:missing-reviewer → ran as generic worker'], green: 0 } },
  hm_wc: { title: 'Workers complete', status: 'completed', roster: true,
    roles: roles('workers_complete', { reviewer: { requested_character: 'global:code-reviewer', session_ids: [], count: 0 } }),
    expect: { text: ['Workers done · review pending', 'global:builder', 'codex · gpt-6 · high', 'global:ui-fixer', 'claude · claude-opus-5-5 · medium', 'No merged commit has been reviewed'], green: 0 } },
  hm_rp: { title: 'Review pending', status: 'active', roster: true,
    roles: (() => { const r = withOutcomes(roles('review_pending'), ['provisional', 'queued', 'queued']); r.reviews = [review('ws_001')]; r.counts.provisional = 1; return r; })(),
    expect: { text: ['Review pending', SHA, 'hivemind-roster.mjs', 'boot-smoke.mjs', 'passed', 'global:code-reviewer', 'Provisional · not accepted', '2 reviewers', '3 workers'], green: 0 } },
  hm_cr: { title: 'Changes requested', status: 'paused', roster: true,
    roles: (() => { const r = withOutcomes(roles('held'), ['provisional', 'queued', 'queued']);
      r.reviews = [review('ws_001', { review_state: 'changes_requested', verdict: 'changes_requested', hold_reason: 'Reviewer found an unescaped sha in the list chip',
        checks: { named: [{ name: 'hivemind-roster.mjs', ok: true }, { name: 'boot-smoke.mjs', ok: false }], ran: true, note: '' } })];
      r.hold = { kind: 'changes_requested', reason: 'Reviewer requested changes; later merges are stopped', ws_id: 'ws_001', checks: [{ name: 'boot-smoke.mjs', ok: false }] };
      r.reason = 'Reviewer requested changes; later merges are stopped'; r.counts.provisional = 1; return r; })(),
    expect: { text: ['Held · Reviewer requested changes', 'Changes requested', 'Reviewer found an unescaped sha in the list chip', 'FAILED', SHA], green: 0 } },
  hm_ok: { title: 'Accepted', status: 'completed', roster: true,
    roles: (() => { const r = withOutcomes(roles('accepted'), ['accepted', 'accepted', 'skipped']);
      r.reviews = [review('ws_001', { state: 'accepted', review_state: 'pass', verdict: 'pass' }), review('ws_002', { state: 'accepted', review_state: 'pass', verdict: 'pass', reviewed_commit: SHA2 })];
      r.counts = { workstreams: 3, accepted: 2, provisional: 0, skipped: 1, failed: 0 }; return r; })(),
    expect: { text: ['Accepted', 'Accepted · reviewed commit passed', SHA, SHA2], greenMin: 3 } },
  hm_stale: { title: 'Claimed accepted but stale', status: 'completed', roster: true,
    roles: (() => { const r = withOutcomes(roles('accepted'), ['accepted', 'accepted', 'accepted']);
      r.workstreams[0].integration.stale = true;
      r.reviews = [review('ws_001', { state: 'accepted', review_state: 'pass', verdict: 'pass' })]; return r; })(),
    expect: { text: ['Unverified', 'moved after integration pinned it', 'Not verified as accepted'], green: 0 } },
  hm_an: { title: 'Analysis only', status: 'completed', roster: true,
    roles: withOutcomes(roles('analysis_only'), ['not_applicable', 'not_applicable', 'not_applicable']),
    expect: { text: ['Analysis only · no code to review', 'Analysis only · no branch'], green: 0 } },
  hm_nc: { title: 'No changes', status: 'completed', roster: true,
    roles: withOutcomes(roles('no_changes'), ['skipped', 'skipped', 'skipped']),
    expect: { text: ['No changes merged', 'Skipped · no change'], green: 0 } },
  hm_legacy: { title: 'Legacy run', status: 'completed', roster: false, roles: null,
    expect: { text: ['Legacy run · no reviewer', 'no reviewer checked any merged commit'], green: 0 } },
  hm_nr: { title: 'Roster without roles', status: 'completed', roster: true, roles: null,
    expect: { text: ['Review state unavailable', 'returned no review state'], green: 0 } },
};

function manifest(id, c) {
  return { id, project_id: PID, title: c.title, goal: 'Smoke goal for ' + c.title, status: c.status, updated_at: new Date().toISOString(),
    config: c.roster ? { roster_version: 1, reviewer_character: 'global:code-reviewer' } : {} };
}
function detail(id) {
  const c = CASES[id];
  const wss = c.roles ? c.roles.workstreams.map((w) => ({ id: w.id, title: w.title, status: w.status, findings_count: 0, priority: 5, dependencies: [], description: '' }))
    : [{ id: 'ws_001', title: 'Legacy worker', status: 'completed', findings_count: 2, priority: 5, dependencies: [], description: '' }];
  const out = { manifest: manifest(id, c), workstreams: wss, recent_messages: [], decisions: [], open_questions: [], integration: null };
  if (c.roles) out.roles = c.roles;
  return out;
}
function listRow(id) {
  const c = CASES[id];
  const row = { ...manifest(id, c), workstream_count: 3, workstreams_completed: 3, workstreams_active: 0, total_findings: 0, integration_status: null };
  if (c.roles) row.roles = { stage: c.roles.stage, accepted: c.roles.accepted, requested_characters: ['global:builder', 'global:ui-fixer'], reviewer_character: 'global:code-reviewer',
    substitutions: c.roles.workstreams.concat(c.roles.reviews.map((r) => r.reviewer)).filter((x) => x.substituted) };
  return row;
}
const RUNS = {
  reviewer: { runs: [{ session_id: 'r1', status: 'completed', task: 'Review ws_001', started_at: '2026-11-01 10:00', run_role: 'reviewer', requested_character: 'global:code-reviewer', effective_engine: { provider: 'claude', model: 'claude-fable-5-1', effort: 'high', session_id: 's' } }],
    total: 1, offset: 0, limit: 50, role_counts: { orchestrator: 1, worker: 3, reviewer: 2 } },
  worker: { runs: [{ session_id: 'w1', status: 'completed', task: 'Build ws_001', started_at: '2026-11-01 09:00', run_role: 'worker', requested_character: 'global:builder', effective_engine: null }],
    total: 1, offset: 0, limit: 50, role_counts: { orchestrator: 1, worker: 3, reviewer: 2 } },
};

const failures = [];
function check(ok, label, detailTxt) {
  if (ok) console.log('  ok  ' + label);
  else { console.error('  FAIL ' + label + (detailTxt ? ' -- ' + detailTxt : '')); failures.push(label); }
}

async function newPage(browser, mobile) {
  const ctx = await browser.newContext(mobile
    ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2,
        userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1' }
    : { viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const seen = { runs: [] };
  await page.route('**/*', (route) => {
    const u = new URL(route.request().url());
    const path = u.pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/hivemind/list') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(Object.keys(CASES).map(listRow)) });
    let m = path.match(/^\/api\/hivemind\/(hm_[a-z]+)\/runs$/);
    if (m) {
      const role = u.searchParams.get('role');
      seen.runs.push({ id: m[1], role });
      if (!CASES[m[1]].roster) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ runs: [{ session_id: 'o1', status: 'completed', task: 'Legacy run', started_at: 'x' }], total: 1, offset: 0, limit: 50 }) });
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(RUNS[role] || RUNS.worker) });
    }
    m = path.match(/^\/api\/hivemind\/(hm_[a-z]+)\/knowledge\/findings$/);
    if (m) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    m = path.match(/^\/api\/hivemind\/(hm_[a-z]+)$/);
    if (m && CASES[m[1]]) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(detail(m[1])) });
    return route.abort();
  });
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof allProjects !== 'undefined' && allProjects.length > 0, null, { timeout: 15000 });
  await page.waitForFunction(() => !!window.hmReview && typeof window.openHivemindDashboard === 'function');
  return { ctx, page, seen, pageErrors };
}

// Everything the review view paints must sit inside the viewport horizontally,
// and the scrolling pane must not need a horizontal scrollbar.
async function layoutProblems(page, mainSel) {
  return page.evaluate((sel) => {
    const vw = window.innerWidth, out = [];
    const main = document.querySelector(sel);
    if (!main) return ['main pane missing'];
    if (main.scrollWidth > main.clientWidth + 1) out.push(`main pane scrolls horizontally (${main.scrollWidth} > ${main.clientWidth})`);
    if (document.documentElement.scrollWidth > vw + 1) out.push(`page scrolls horizontally (${document.documentElement.scrollWidth} > ${vw})`);
    for (const el of main.querySelectorAll('.hmr-sha, .hmr-reason, .hmr-checks li, .hmr-engine, .hmr-char, .hmr-badge, .hmr-kv-row dd')) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      if (r.left < -1 || r.right > vw + 1) out.push(`${el.className || el.tagName} spills outside viewport (${Math.round(r.left)}..${Math.round(r.right)} of ${vw})`);
    }
    return out;
  }, mainSel);
}

async function runViewport(browser, label, mobile) {
  console.log(`\n== ${label} ==`);
  const { ctx, page, seen, pageErrors } = await newPage(browser, mobile);
  const vw = mobile ? 390 : 1440;
  try {
    for (const id of Object.keys(CASES)) {
      const c = CASES[id];
      await page.evaluate((i) => window.openHivemindDashboard(i), id);
      const mainSel = `#hm-dash-main-${id}`;
      await page.waitForSelector(mainSel, { timeout: 8000 });
      await page.waitForSelector(`${mainSel} .hmr-panel`, { timeout: 8000 });
      const win = page.locator(`.modal-window[data-modal-id="__hivemind_${id}"]`);
      const header = (await win.locator('.modal-header').first().innerText()).replace(/\s+/g, ' ');
      const text = (await page.locator(mainSel).innerText()).replace(/\s+/g, ' ');
      const all = header + ' ' + text;
      console.log(`- ${id} (${c.title})`);
      for (const t of c.expect.text) check(all.includes(t), `${id}: shows "${t.length > 50 ? t.slice(0, 47) + '...' : t}"`);
      const greenInPanel = await page.locator(`${mainSel} .hmr-panel .hmr-tone-ok`).count();
      const greenInHeader = await win.locator('.modal-header .hmr-tone-ok').count();
      if (greenInPanel + greenInHeader && 'green' in c.expect && c.expect.green === 0) console.log('     green elements: ' + JSON.stringify(await page.locator(`${mainSel} .hmr-tone-ok`).evaluateAll((els) => els.map((e) => e.textContent.trim()))));
      if ('green' in c.expect) check(greenInPanel + greenInHeader === c.expect.green, `${id}: green badges = ${c.expect.green}`, `got ${greenInPanel + greenInHeader}`);
      if ('greenMin' in c.expect) check(greenInPanel >= c.expect.greenMin, `${id}: accepted run renders green`, `got ${greenInPanel}`);
      // 'completed' must not stand alone as the badge of a non-accepted roster run.
      if (c.roster && c.status === 'completed' && c.roles && c.roles.stage !== 'accepted') {
        check(!/\bcompleted\b/i.test(await win.locator('.modal-header .hm-status-badge').first().innerText()), `${id}: header badge does not say "completed"`);
      }
      if (id === 'hm_rp') {
        const stats = (await page.locator(`${mainSel} .hmr-stats`).innerText()).replace(/\s+/g, ' ');
        check(/2 reviewers/.test(stats) && /3 workers/.test(stats), 'hm_rp: reviewer count (2) is separate from worker count (3)', stats);
      }
      const problems = await layoutProblems(page, mainSel);
      check(problems.length === 0, `${id}: layout inside ${vw}px viewport`, problems.join('; '));
      if (['hm_wc', 'hm_rp', 'hm_cr', 'hm_ok', 'hm_stale'].includes(id)) {
        await page.screenshot({ path: resolve(SHOTS, `${id}_${vw}_top.png`) });
        const target = page.locator(`${mainSel} .hmr-review, ${mainSel} .hmr-hold`).first();
        if (await target.count()) {
          await target.scrollIntoViewIfNeeded();
          await page.screenshot({ path: resolve(SHOTS, `${id}_${vw}_detail.png`) });
        }
      }
      await page.evaluate((i) => window.closeModalById('__hivemind_' + i), id);
    }

    // Per-workstream detail still carries character + engine.
    await page.evaluate(() => window.openHivemindDashboard('hm_rp', 'ws_002'));
    await page.waitForSelector('#hm-dash-main-hm_rp .hmr-ws-chips', { timeout: 8000 });
    const wsText = (await page.locator('#hm-dash-main-hm_rp').innerText()).replace(/\s+/g, ' ');
    check(wsText.includes('global:ui-fixer') && wsText.includes('claude · claude-opus-5-5 · medium'), 'workstream detail: requested character + effective engine');
    await page.evaluate(() => window.closeModalById('__hivemind_hm_rp'));

    // Runs modal: reviewer role is requested, counts are distinct, rows are annotated.
    await page.evaluate(() => window.openHmRunsModal('hm_rp', 'smoke_alpha', 'reviewer', '', 'Reviewer'));
    await page.waitForSelector('[data-hmr-role-counts]', { timeout: 8000 });
    check(seen.runs.some((r) => r.id === 'hm_rp' && r.role === 'reviewer'), 'runs: request carried role=reviewer');
    const rolesTxt = (await page.locator('[data-hmr-role-counts]').innerText()).replace(/\s+/g, ' ');
    check(/1 Orchestrator/.test(rolesTxt) && /3 Worker/.test(rolesTxt) && /2 Reviewer/.test(rolesTxt), 'runs: orchestrator 1 / worker 3 / reviewer 2 shown separately', rolesTxt);
    check(await page.locator('[data-hmr-role="reviewer"][disabled]').count() === 1, 'runs: active role is the reviewer button');
    const runTxt = (await page.locator('.hmr-run').first().innerText()).replace(/\s+/g, ' ');
    check(/reviewer/i.test(runTxt) && runTxt.includes('global:code-reviewer') && runTxt.includes('claude · claude-fable-5-1 · high'), 'runs: row shows role, requested character, effective engine', runTxt);
    const rv = await page.evaluate(() => { const b = document.querySelector('[data-hmr-role="reviewer"]'); const r = b.getBoundingClientRect(); return [r.left, r.right, window.innerWidth]; });
    check(rv[0] >= -1 && rv[1] <= rv[2] + 1, 'runs: role buttons inside viewport', JSON.stringify(rv));
    await page.screenshot({ path: resolve(SHOTS, `runs_reviewer_${vw}.png`) });
    await page.evaluate(() => window.closeModalById('__hm_runs_hm_rp_reviewer_'));
    await page.evaluate(() => window.openHmRunsModal('hm_legacy', 'smoke_alpha', 'worker', '', 'Legacy'));
    await page.waitForSelector('.run-row', { timeout: 8000 });
    check(await page.locator('[data-hmr-role-counts]').count() === 0, 'runs: legacy run has no role-count header');

    // List view (cross-project Hivemind).
    await page.evaluate(() => window.openAllHivemindsForProject('smoke_alpha'));
    await page.waitForSelector('#ahm-list .hm-global-card', { timeout: 8000 });
    await page.waitForFunction((n) => document.querySelectorAll('#ahm-list .hm-global-card').length >= n, Object.keys(CASES).length);
    const cards = await page.evaluate(() => [...document.querySelectorAll('#ahm-list .hm-global-card')].map((c) => ({
      title: c.querySelector('.hm-global-card-title')?.textContent || '',
      text: c.innerText.replace(/\s+/g, ' '),
      green: c.querySelectorAll('.hmr-tone-ok').length,
    })));
    const byTitle = Object.fromEntries(cards.map((c) => [c.title, c]));
    for (const id of Object.keys(CASES)) {
      const c = CASES[id], card = byTitle[c.title];
      check(!!card, `list: card for "${c.title}"`);
      if (!card) continue;
      check(c.title === 'Accepted' ? card.green >= 1 : card.green === 0, `list: "${c.title}" green badges ${c.title === 'Accepted' ? '>= 1' : '= 0'}`, 'got ' + card.green);
    }
    check(byTitle['Workers complete']?.text.includes('Workers done · review pending') && /workers done/i.test(byTitle['Workers complete']?.text || ''), 'list: workers-complete card says workers done / review pending');
    check(byTitle['Workers complete']?.text.includes('global:builder') && byTitle['Workers complete']?.text.includes('codex · gpt-6 · high'), 'list: card shows requested character + effective engine');
    check(byTitle['Review pending']?.text.includes('Reviewer global:code-reviewer'), 'list: card names the reviewer character');
    check(byTitle['Disclosed substitutions']?.text.includes('requested global:missing-worker → ran as global:standin')
      && byTitle['Disclosed substitutions']?.text.includes('requested global:missing-reviewer → ran as generic worker'),
      'list: both worker and reviewer substitutions are disclosed');
    check(byTitle['Legacy run']?.text.includes('Legacy run · no reviewer'), 'list: legacy card is explicit');
    check(byTitle['Roster without roles']?.text.includes('Review state unavailable'), 'list: roster card without roles is explicit');
    const listOverflow = await page.evaluate(() => { const l = document.getElementById('ahm-list'); return [l.scrollWidth, l.clientWidth, document.documentElement.scrollWidth, window.innerWidth]; });
    check(listOverflow[0] <= listOverflow[1] + 1 && listOverflow[2] <= listOverflow[3] + 1, `list: no horizontal overflow at ${vw}px`, JSON.stringify(listOverflow));
    await page.screenshot({ path: resolve(SHOTS, `list_${vw}.png`) });

    check(pageErrors.length === 0, 'no uncaught page errors', pageErrors.join(' | '));
  } finally { await ctx.close(); }
}

let browser;
try {
  browser = await chromium.launch();
  await runViewport(browser, 'desktop 1440x900', false);
  await runViewport(browser, 'mobile 390x844', true);
} catch (e) {
  console.error('HARNESS ERROR: ' + (e && e.stack || e));
  failures.push('harness: ' + (e && e.message));
} finally { if (browser) await browser.close(); }

if (failures.length) { console.error(`\n${failures.length} check(s) FAILED`); process.exit(1); }
console.log('\nAll hivemind roster UI checks passed. Screenshots: ' + SHOTS);
