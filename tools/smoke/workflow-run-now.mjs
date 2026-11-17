#!/usr/bin/env node
/**
 * MC-1201 — the workflow toolbar's "Update" and "Run now" buttons on a phone.
 *
 * Ron's phone screenshot showed (1) Update as a white browser-default button
 * and Run now as a plain outlined one, and (2) a "Run failed: a run of this
 * workflow is already live" banner beside a run that WAS running, because one
 * tap sent two POST /api/workflows/<id>/run (200 then 409).
 *
 * Real headless boot (real index.html + real static/js/*.js, mocked network),
 * same shape as workflow-builder.mjs. Checked at 390px (touch, isMobile) and
 * 1440px:
 *   - both buttons carry the toolbar's own button class and render with the
 *     toolbar's font-size/radius, not the browser default (white background);
 *   - one tap on Run now sends exactly ONE run POST;
 *   - a second tap while the first request is still in flight sends nothing
 *     and the button is disabled in the meantime;
 *   - a 409 for a run this tab just started shows no "Run failed" toast, a
 *     409 for a run started elsewhere still does.
 *
 * RUN
 *   cd tools/smoke && node workflow-run-now.mjs
 * Exit 0 = all cases behave; 1 = a case regressed / harness error.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_runnow';
const WFID = 'wf-runnow';

const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

const PROJECTS_JSON = JSON.stringify([{
  id: PID, name: 'Run Now Smoke', status: 'active', domain: 'general', emoji: '🧪',
  description: '', summary: '', current_task: 'Idle', next_action: '',
  blocked: false, blocked_reason: null, activity_log: [], backlog: [],
  project_path: '/smoke/' + PID, last_updated: '2026-11-17T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null,
  display_order: 0, provider: 'claude', use_streaming_agent: true,
  agent_model: 'claude-sonnet-5', agent_effort: 'medium',
}]);
const WORKFLOW = {
  id: WFID, format: 2, name: 'Check US stocks', enabled: true,
  trigger: { type: 'manual' }, edges: [],
  nodes: [{ type: 'agent', name: 'us-stock-investor', project_id: PID, character: '', prompt: 'scan', x: 40, y: 40 }],
};
const RUN = (id) => ({
  id, workflow_id: WFID, status: 'running', created: '2026-11-17T15:39:48Z',
  trigger: { type: 'manual', fired_at: '2026-11-17T15:39:48Z' },
  steps: { 'us-stock-investor': { status: 'running', project_id: PID, session_id: 'abc123' } },
});

// One fixture per case: `mode` decides what the run endpoint answers.
//  'real'    — first POST 200 + live run, later POSTs 409 (the real server)
//  'foreign' — every POST 409; a run this tab never started is live
// `delayMs` holds the response so a second tap can land mid-flight.
function makeServer() {
  const s = { runPosts: [], liveRun: null, mode: 'real', delayMs: 0, nextRunId: 'run-own-1', foreignRun: RUN('run-foreign') };
  s.handler = async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return json(JSON.parse(PROJECTS_JSON));
    if (path === '/api/config') return json({});
    if (path === '/api/local-auth/status') return json({ configured: true });
    if (path === '/api/characters') return json([]);
    if (path === '/api/floor') return json({ rooms: [], quiet: [], bench: [], counts: {} });
    if (path === '/api/engine/preview') return json({ provider: 'claude', model: '', model_source: 'native', effort: '' });
    if (path === '/api/workflows' && req.method() === 'GET') return json([WORKFLOW]);
    if (path === `/api/project/${PID}/workflows`) return json({ workflows: [] });
    if (path === `/api/project/${PID}/agent/log`) return json([]);
    if (path === `/api/project/${PID}/conversations`) return json([]);
    if (path.startsWith('/api/schedules')) return json([]);
    if (path === `/api/workflows/${WFID}/runs`) return json(s.liveRun ? [s.liveRun] : (s.mode === 'foreign' ? [s.foreignRun] : []));
    if (path === `/api/workflows/${WFID}/run` && req.method() === 'POST') {
      s.runPosts.push(Date.now());
      if (s.delayMs) await new Promise((r) => setTimeout(r, s.delayMs));
      if (s.mode === 'foreign' || s.liveRun) return json({ error: 'a run of this workflow is already live', busy: true }, 409);
      s.liveRun = RUN(s.nextRunId);
      return json({ ok: true, run: s.liveRun });
    }
    return route.abort();
  };
  return s;
}

async function openBuilder(page) {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, .mc-chat-row', { timeout: 15000 });
  await page.evaluate((pid) => { openProjectModal(pid); }, PID);
  await page.waitForTimeout(150);
  await page.evaluate((pid) => { switchModalTab(pid, 'workflows'); }, PID);
  await page.waitForSelector(`#wfb-clayrune-section-${PID}`, { timeout: 5000 });
  await page.waitForTimeout(150);
  await page.evaluate(({ id, pid }) => { window.openWorkflowBuilder(id, pid); }, { id: WFID, pid: PID });
  await page.waitForSelector('#wfb-canvas-viewport', { timeout: 5000 });
  await page.waitForSelector('.wfb-toolbar .btn-sched-cancel', { timeout: 5000 });
}

const toastTexts = (page) => page.evaluate(() => window.__toasts.slice());

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  for (const vp of [
    { label: '390px phone (touch)', width: 390, height: 844, mobile: true },
    { label: '1440px desktop', width: 1440, height: 900, mobile: false },
  ]) {
    console.log(`\n── ${vp.label} ──`);
    const tap = async (page, sel) => (vp.mobile ? page.tap(sel) : page.click(sel));

    // ── 1) button styling ────────────────────────────────────────────────
    {
      const srv = makeServer();
      const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, hasTouch: vp.mobile, isMobile: vp.mobile });
      const page = await ctx.newPage();
      const errs = []; page.on('pageerror', (e) => errs.push(e.message || String(e)));
      await page.route('**/*', srv.handler);
      await openBuilder(page);
      const look = await page.evaluate(() => {
        const cs = (el) => { const c = getComputedStyle(el); return { bg: c.backgroundColor, fs: c.fontSize, br: c.borderRadius, color: c.color, bw: c.borderTopWidth, ff: c.fontFamily }; };
        const q = (s) => document.querySelector(s);
        const reset = [...document.querySelectorAll('.wfb-toolbar .wfb-toolbar-btn')].find((b) => /Reset/.test(b.textContent));
        return { update: cs(q('.wfb-toolbar .btn-sched-save')), run: cs(q('.wfb-toolbar .btn-sched-cancel')), reset: cs(reset),
          updateCls: q('.wfb-toolbar .btn-sched-save').className, runCls: q('.wfb-toolbar .btn-sched-cancel').className };
      });
      const notDefault = (s) => s.bg !== 'rgb(239, 239, 239)' && s.bg !== 'rgb(255, 255, 255)' && s.bg !== 'buttonface';
      const sameAsToolbar = (s) => s.fs === look.reset.fs && s.br === look.reset.br && s.ff === look.reset.ff;
      (notDefault(look.update) && sameAsToolbar(look.update))
        ? ok(`Update matches the toolbar buttons (font ${look.update.fs}, radius ${look.update.br}, bg ${look.update.bg}, class "${look.updateCls}")`)
        : fail(`Update still renders as a browser default: ${JSON.stringify(look.update)} vs Reset ${JSON.stringify(look.reset)}`);
      (notDefault(look.run) && sameAsToolbar(look.run))
        ? ok(`Run now matches the toolbar buttons (font ${look.run.fs}, radius ${look.run.br}, class "${look.runCls}")`)
        : fail(`Run now does not match the toolbar: ${JSON.stringify(look.run)} vs Reset ${JSON.stringify(look.reset)}`);
      const e = errs.filter((x) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(x));
      e.length ? e.forEach((x) => fail('uncaught exception (styling case): ' + x)) : 0;
      await ctx.close();
    }

    // ── 2-4) run POST behaviour ──────────────────────────────────────────
    for (const c of [
      { name: 'one tap', mode: 'real', delayMs: 0, taps: 1, expectPosts: 1, expectFailToast: false },
      { name: 'two taps while the first is in flight', mode: 'real', delayMs: 600, taps: 2, expectPosts: 1, expectFailToast: false },
      { name: 'a run started elsewhere is already live', mode: 'foreign', delayMs: 0, taps: 1, expectPosts: 1, expectFailToast: true },
    ]) {
      const srv = makeServer();
      srv.mode = c.mode; srv.delayMs = c.delayMs;
      const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, hasTouch: vp.mobile, isMobile: vp.mobile });
      const page = await ctx.newPage();
      const errs = []; page.on('pageerror', (e) => errs.push(e.message || String(e)));
      await page.route('**/*', srv.handler);
      // Capture every toast the app shows (showToast is a global function).
      await page.addInitScript(() => { window.__toasts = []; });
      await openBuilder(page);
      await page.evaluate(() => {
        const orig = window.showToast;
        window.showToast = function (msg, ...rest) { window.__toasts.push(String(msg)); return orig ? orig.call(this, msg, ...rest) : undefined; };
      });
      const sel = '.wfb-toolbar .btn-sched-cancel';
      await tap(page, sel);
      let disabledMidFlight = null;
      if (c.taps > 1) {
        disabledMidFlight = await page.$eval(sel, (b) => b.disabled).catch(() => null);
        // The user taps again while the request is still pending. Dispatch a
        // second real click (a disabled button swallows it; a live one fires).
        await page.evaluate((s) => document.querySelector(s).click(), sel);
      }
      await page.waitForTimeout(c.delayMs + 700);
      const toasts = await toastTexts(page);
      const failToast = toasts.find((t) => /Run failed/i.test(t));
      srv.runPosts.length === c.expectPosts
        ? ok(`${c.name}: exactly ${c.expectPosts} POST /run`)
        : fail(`${c.name}: expected ${c.expectPosts} POST /run, got ${srv.runPosts.length}`);
      (!!failToast === c.expectFailToast)
        ? ok(`${c.name}: ${c.expectFailToast ? 'the "Run failed" toast still shows' : 'no "Run failed" toast'}${failToast ? ` ("${failToast}")` : ''}`)
        : fail(`${c.name}: expected failToast=${c.expectFailToast}, got toasts ${JSON.stringify(toasts)}`);
      if (c.taps > 1) {
        disabledMidFlight === true
          ? ok(`${c.name}: Run now is disabled while its request is in flight`)
          : fail(`${c.name}: Run now was not disabled mid-flight (disabled=${disabledMidFlight})`);
      }
      if (c.mode === 'real') {
        const strip = await page.$eval('#wfb-live-run-slot', (el) => el.textContent.trim()).catch(() => '');
        /run-own-1/.test(strip)
          ? ok(`${c.name}: the live-run strip shows the run it started`)
          : fail(`${c.name}: expected the live-run strip to show run-own-1, got "${strip.slice(0, 80)}"`);
        const enabledAfter = await page.$eval(sel, (b) => !b.disabled).catch(() => false);
        enabledAfter ? ok(`${c.name}: Run now is enabled again once the request settles`) : fail(`${c.name}: Run now stayed disabled after the request settled`);
      }
      const e = errs.filter((x) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(x));
      e.length ? e.forEach((x) => fail(`uncaught exception (${c.name}): ` + x)) : 0;
      await ctx.close();
    }
  }

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0
    ? '\n✅ PASS — Update and Run now share the toolbar look at 390px and 1440px; one tap is one POST /run, a tap during the request is ignored, and a run this tab started never shows a "Run failed" banner (a run started elsewhere still does).'
    : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
