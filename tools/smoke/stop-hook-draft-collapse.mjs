#!/usr/bin/env node
/**
 * Stop-hook draft collapse (2026-09-15).
 *
 * WHY THIS EXISTS
 * ----------------
 * A global Stop hook (reply-length-guard.py / permission-ask-guard.py /
 * turn-guard.py) blocks an over-long or premature reply and feeds its reason
 * back so the model re-sends a compressed version in the SAME turn. Nothing
 * told the chat renderer the first draft had been retracted, so both replies
 * showed as ordinary assistant text -- every blocked reply appeared twice.
 * The backend now emits a '[stop-hook-redo]' boundary marker
 * (mc/agent_runtime.is_stop_hook_feedback); the frontend must collapse
 * whatever narration preceded that marker into a closed <details>, leaving
 * only the final reply visible by default.
 *
 * This smoke drives the REAL appendAgentLine()/collapseIntoDraftBlock() code
 * from THIS checkout (static/js served from ROOT, not the live server's own
 * copy) against a live MC instance's page shell, and screenshots the result.
 *
 * RUN: node stop-hook-draft-collapse.mjs   (needs MC on localhost:5199)
 */
import { chromium } from 'playwright';
import { existsSync, readFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, m) => (cond ? ok(m) : fail(m));

const SID = 'stophooksmoke0001';

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 900, height: 500 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message || String(e)));
  await page.route('**/*', (route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (req.method() !== 'GET') return route.abort();
    if (url.hostname === 'localhost' && /^\/static\/(js|css)\//.test(url.pathname)) {
      const f = join(ROOT, url.pathname);
      if (existsSync(f)) {
        return route.fulfill({ status: 200,
          contentType: url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript; charset=utf-8',
          body: readFileSync(f, 'utf8') });
      }
    }
    return route.continue();
  });
  await page.goto('http://localhost:5199/', { waitUntil: 'domcontentloaded' });
  const bridged = await page.waitForFunction(
    () => typeof window.appendAgentLine === 'function'
      && typeof window.collapseIntoDraftBlock === 'function',
    null, { timeout: 20000 }).then(() => true).catch(() => false);
  check(bridged, 'checkout JS loaded (appendAgentLine, collapseIntoDraftBlock bridged)');
  if (!bridged) throw new Error('no bridge');

  await page.evaluate((sid) => {
    const wrap = document.createElement('div');
    wrap.className = 'agent-chat';
    wrap.style.cssText = 'width:800px;background:#1a1a1a;padding:12px;';
    const out = document.createElement('div');
    out.className = 'agent-output';
    out.id = `agent-output-${sid}`;
    wrap.appendChild(out);
    document.body.appendChild(wrap);
  }, SID);

  const DRAFT = 'This is a very long retracted draft reply that the reply-length ' +
    'guard blocked because it ran well past the brevity ceiling and had to be ' +
    'redone in the very same turn right away.';
  const FINAL = 'Short compressed final reply.';

  await page.evaluate(({ sid, draft, final }) => {
    appendAgentLine(sid, `> Ron: Summarize the recent changes.`);
    appendAgentLine(sid, draft);
    appendAgentLine(sid, '[stop-hook-redo]');
    appendAgentLine(sid, final);
  }, { sid: SID, draft: DRAFT, final: FINAL });

  const result = await page.evaluate(({ sid, draft, final }) => {
    const el = document.getElementById(`agent-output-${sid}`);
    const details = el.querySelector('details.draft-block');
    return {
      text: el.textContent || '',
      hasMarkerText: /\[stop-hook-redo\]/.test(el.textContent || ''),
      hasDetails: !!details,
      detailsOpen: details ? details.open : null,
      draftInsideDetails: details ? (details.textContent || '').includes(draft) : false,
      finalOutsideDetails: !details
        ? false
        : Array.from(el.children).some((c) => c !== details
            && (c.textContent || '').includes(final)),
      draftVisibleOutsideDetails: Array.from(el.children).some((c) => c.tagName !== 'DETAILS'
        && (c.textContent || '').includes(draft)),
    };
  }, { sid: SID, draft: DRAFT, final: FINAL });

  check(result.hasDetails, 'the retracted draft collapsed into a <details class="draft-block">');
  check(result.detailsOpen === false, 'the draft toggle is CLOSED by default');
  check(result.draftInsideDetails, 'the draft text is preserved inside the collapsed toggle (not deleted)');
  check(!result.draftVisibleOutsideDetails, 'the draft text does NOT also render as a visible bubble');
  check(result.finalOutsideDetails, 'the final compressed reply renders as a normal, visible bubble');
  check(!result.hasMarkerText, 'the raw "[stop-hook-redo]" marker text never reaches the screen');
  check(errors.length === 0, `no page errors (${errors.length}): ${errors.slice(0, 3).join(' | ')}`);

  // Legacy buffer: a chat rebuilt from its transcript BEFORE the server fix
  // holds the hook feedback as a fake user prompt. It must still collapse the
  // draft and never show as a Ron bubble.
  const LEGACY_SID = SID + 'legacy';
  const legacy = await page.evaluate(({ sid, draft, final }) => {
    const out = document.createElement('div');
    out.className = 'agent-output';
    out.id = `agent-output-${sid}`;
    document.querySelector('.agent-chat').appendChild(out);
    appendAgentLine(sid, '\n> Ron: What is the status?\n');
    appendAgentLine(sid, draft);
    appendAgentLine(sid, '\n> Ron: Stop hook feedback:\nBREVITY RULE VIOLATED: that reply was 211 prose words.\n');
    appendAgentLine(sid, '\n> Ron: Stop hook feedback:\nYou ended your turn ASKING PERMISSION to do something reversible.\n');
    appendAgentLine(sid, final);
    const prompts = Array.from(out.querySelectorAll('.agent-line-prompt')).map((e) => e.textContent);
    const details = out.querySelectorAll('details.draft-block');
    const visible = Array.from(out.children).filter((c) => c.tagName !== 'DETAILS').map((c) => c.textContent).join('\n');
    out.remove();
    return { prompts, detailCount: details.length,
      draftHidden: details.length === 1 && !details[0].open && details[0].textContent.includes(draft),
      visibleHasDraft: visible.includes(draft), visibleHasFinal: visible.includes(final),
      hookText: /Stop hook feedback/.test(visible) };
  }, { sid: LEGACY_SID, draft: DRAFT, final: FINAL });
  check(legacy.prompts.length === 1 && !legacy.prompts.some((p) => /Stop hook feedback/.test(p)),
    'legacy buffer: "> Ron: Stop hook feedback" never renders as a Ron bubble');
  check(legacy.draftHidden && !legacy.visibleHasDraft, 'legacy buffer: the draft collapses into one closed toggle');
  check(legacy.visibleHasFinal && !legacy.hookText, 'legacy buffer: only the final reply is visible, no hook text');

  // The substitution advisory stays in log_lines (agent-facing) but must not
  // be visible in the chat.
  const coverage = await page.evaluate((sid) => {
    const out = document.getElementById(`agent-output-${sid}`);
    appendAgentLine(sid, '[coverage] you specified /tmp/x.pdf — no tool call this turn used it');
    const el = Array.from(out.children).pop();
    const shown = el && getComputedStyle(el).display !== 'none';
    const cls = el ? el.className : '';
    el && el.remove();
    return { shown, cls };
  }, SID);
  check(/agent-line-coverage/.test(coverage.cls) && !coverage.shown,
    `[coverage] advisory is in the DOM but hidden (display:none) — class "${coverage.cls}"`);

  const shotDir = join(ROOT, 'data', 'uploads');
  mkdirSync(shotDir, { recursive: true });
  const closedShotPath = join(shotDir, 'smoke-stop-hook-draft-collapse-closed.png');
  await page.locator('.agent-chat').screenshot({ path: closedShotPath });
  console.log('  screenshot (default, closed): ' + closedShotPath);

  // Open the toggle and confirm the draft becomes readable — never destroyed.
  await page.click(`#agent-output-${SID} details.draft-block summary`);
  const afterOpen = await page.evaluate((sid) => {
    const details = document.querySelector(`#agent-output-${sid} details.draft-block`);
    return { open: details.open, visibleText: details.textContent || '' };
  }, SID);
  check(afterOpen.open === true, 'clicking "Show earlier draft" opens the toggle');
  check(afterOpen.visibleText.includes(DRAFT), 'opened toggle shows the full draft text');

  const openShotPath = join(shotDir, 'smoke-stop-hook-draft-collapse-open.png');
  await page.locator('.agent-chat').screenshot({ path: openShotPath });
  console.log('  screenshot (opened): ' + openShotPath);
} finally {
  await browser.close();
}

console.log('');
if (bad > 0) {
  console.error(`FAIL — ${bad} check(s) failed`);
  process.exit(1);
} else {
  console.log('PASS — stop-hook draft collapses to a closed toggle; final reply stays visible.');
}
