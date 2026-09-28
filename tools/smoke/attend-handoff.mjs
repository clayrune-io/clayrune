#!/usr/bin/env node
/**
 * MC-994 (backlog fb822042) — the attended-handoff control.
 *
 * A dispatched/scheduled chat is fenced by steward/fence.py even when a human
 * is actively reading it (2026-09-28 incident: a scheduled Dave handed its
 * thread to a new chat via POST /agent/dispatch at Ron's request, and the
 * fence blocked `git push origin master` even after Ron approved it — "this
 * conversation is not supposed to be guarded"). The fix is a human-click
 * control in the chat header: a "Guarded" pill on any chat whose trigger_type
 * is in the unattended set, with a click that POSTs .../attend and flips it
 * to an "Attended" confirmation.
 *
 * This smoke stubs the server entirely (page.route, no real MC process) —
 * the point is the CLIENT render + click wiring, not the server route (that
 * has its own request-level tests in tests/test_agent_routes.py). Checked at
 * both desktop (1400px) and mobile (390px), since the pill lives in the same
 * shared header markup both layouts render (conversation.js's tabContent).
 *
 * RUN: cd tools/smoke && node attend-handoff.mjs
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const PROJECTS_JSON = readFileSync(resolve(__dirname, 'fixtures', 'projects.json'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_alpha';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

const DAVE = { name: 'dave', agent_name: 'Dave', scope: 'global', avatar: 'fig:guard' };

async function runOnce(viewport, label) {
  console.log(`\n── ${label} (${viewport.width}x${viewport.height}) ──`);
  const ok = (m) => console.log('  ✓ ' + m);
  let bad = 0;
  const fail = (m) => { console.error('  ✗ ' + m); bad++; };
  const check = (cond, pass, failMsg) => (cond ? ok(pass) : fail(failMsg));
  let attendCalls = 0;
  let attendFlipped = false;

  const SESSIONS = () => ({
    mcDispatch: {
      session_id: 'mcDispatch', status: 'idle',
      trigger_type: attendFlipped ? 'manual' : 'dispatch',
      claude_session_id: 'csidDispatch', started_at: '2026-09-28T21:00:00Z',
      task: 'Please push the fix we discussed', character: DAVE,
      log_lines: ['> Ron: please push the fix we discussed', 'On it.'],
    },
  });

  let browser, exitCode = 1;
  try {
    browser = await chromium.launch();
    const page = await (await browser.newContext({ viewport })).newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

    await page.route('**/*', (route) => {
      const req = route.request();
      const url = new URL(req.url());
      const path = url.pathname;
      const json = (o) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(o) });
      if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
      const hit = STATIC[path];
      if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
      if (path === '/api/projects') {
        const patched = JSON.parse(PROJECTS_JSON);
        patched[0].project_path = '/smoke/alpha';
        return json(patched);
      }
      if (path === '/api/config') return json({});
      if (/\/agent\/status$/.test(path)) return json({ sessions: Object.values(SESSIONS()) });
      if (req.method() === 'POST' && /\/agent\/mcDispatch\/attend$/.test(path)) {
        attendCalls++;
        attendFlipped = true;
        return json({ ok: true, session_id: 'mcDispatch', trigger_type: 'manual', persisted: true });
      }
      return route.abort();
    });

    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    // '#projects-col .card' (the desktop grid tile) is CSS-hidden at the
    // mobile breakpoint, so wait on the boot signal both layouts share
    // instead of a desktop-only selector.
    await page.waitForFunction(() => typeof window.openProjectModal === 'function', null, { timeout: 15000 });

    await page.evaluate((pid) => {
      conversationsCache[pid] = [
        { claude_session_id: 'csidDispatch', mc_session_id: 'mcDispatch', live: true, status: 'idle',
          label: 'Please push the fix we discussed',
          character: { name: 'dave', agent_name: 'Dave', scope: 'global', avatar: 'fig:guard' },
          trigger_type: 'dispatch', turns: 2, mtime: 100 },
      ];
      agentLogCache[pid] = [];
      openProjectModal(pid);
      setRailMode(pid, 'channel');
    }, PID);
    const sc = `.modal-window[data-modal-id="${PID}"] `;
    await page.waitForSelector(sc + '.channel-row', { timeout: 10000 });

    await page.locator(`${sc}.channel-row`).filter({ hasText: 'Dave' }).first().click();
    await page.waitForFunction(() => agentStatusCache['mcDispatch'] && agentStatusCache['mcDispatch'].triggerType === 'dispatch', null, { timeout: 5000 });

    const pill = () => page.locator('.attend-pill');
    await page.waitForSelector('.attend-pill.guarded', { timeout: 5000 });
    check(await pill().count() === 1, 'exactly one attend pill rendered',
      `expected 1 attend pill, got ${await pill().count()}`);
    check((await pill().first().innerText()).toLowerCase().includes('guarded'),
      'pill reads Guarded before the click', `pill text: ${await pill().first().innerText()}`);

    await pill().first().click();
    await page.waitForFunction(() => document.querySelector('.attend-pill.attended'), null, { timeout: 5000 });
    check(attendCalls === 1, `exactly one POST .../attend fired (got ${attendCalls})`, `attend POST count wrong: ${attendCalls}`);
    check(await page.locator('.attend-pill.guarded').count() === 0, 'guarded pill is gone after the click',
      'guarded pill still present after the click');
    check((await page.locator('.attend-pill.attended').first().innerText()).toLowerCase().includes('attended'),
      'pill reads Attended after the click', `pill text: ${await page.locator('.attend-pill.attended').first().innerText()}`);
    check(await page.evaluate(() => agentStatusCache['mcDispatch'].triggerType) === 'manual',
      'client-side triggerType flipped to manual optimistically',
      `agentStatusCache triggerType: ${await page.evaluate(() => agentStatusCache['mcDispatch'].triggerType)}`);

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    uncaught.length ? uncaught.forEach((e) => fail('uncaught page error: ' + e)) : ok('no uncaught page errors');
    exitCode = bad ? 1 : 0;
  } catch (err) {
    console.error('❌ harness error:', err && err.stack ? err.stack : err);
    exitCode = 1;
  } finally {
    if (browser) await browser.close().catch(() => {});
  }
  return exitCode;
}

let overallBad = 0;
overallBad += await runOnce({ width: 1400, height: 900 }, 'desktop');
overallBad += await runOnce({ width: 390, height: 844 }, 'mobile 390px');

console.log(overallBad ? `\nFAILED` : '\nALL PASS');
process.exit(overallBad ? 1 : 0);
