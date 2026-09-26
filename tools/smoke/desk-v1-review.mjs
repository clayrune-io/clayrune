#!/usr/bin/env node
/**
 * Desk v1 (MC-977, R0 plan T3) — full-width review smoke (frame 12b).
 *
 * Closes: A6 (selection toolbar below the selection, covering neither it nor
 * the next line), A7 (source -> simulated validation -> block clears only on
 * 'supports' or on accepting a revision, rN increments), A8 (manual
 * destinations never show "Approve and schedule"; primary label by
 * capability x schedule; disabled with a reason while a claim is blocked or
 * a revision is waiting; Publish now only in the ⋯ menu).
 *
 * Real headless boot (real index.html + real static/js|css, no network), same
 * hermetic shape as desk-v1-harness.mjs / desk-v1-kit.mjs.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-review.mjs
 * Exit 0 = every case holds (render checks in all three tones, interaction
 * checks once); 1 = a case regressed / harness error.
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
const ORIGIN = 'http://mc.smoke.test';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

const PID = 'smoke_deskv1review';
const PROJECTS = [{
  id: PID, name: 'Desk v1 review smoke', status: 'active', domain: 'general', emoji: '🧪',
  description: '', summary: '', current_task: 'Idle', next_action: '',
  blocked: false, blocked_reason: null, activity_log: [], backlog: [],
  project_path: '/smoke/' + PID, last_updated: '2026-09-09T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null,
  display_order: 0, provider: 'claude', use_streaming_agent: true,
  distiller_mode: 'proposed', distiller_min_recurrence: 3,
  distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
  distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
  distiller_skip_errors: true, roster: [],
}];

const TONES = [
  { name: 'default/dark', ls: {} },
  { name: 'tone-warm', ls: { mc_tone: 'warm' } },
  { name: 'tone-editorial', ls: { mc_tone: 'editorial' } },
];

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

async function fulfillOrAbort(route) {
  const req = route.request();
  const path = new URL(req.url()).pathname;
  const J = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  const hit = STATIC[path];
  if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
  if (path === '/api/projects') return J(PROJECTS);
  if (path === '/api/config') return J({ desk_v1: true, user_timezone: '' });
  if (path === '/api/characters') return J([]);
  return route.abort();
}

async function newBootedPage(browser, tone, viewport) {
  const ctx = await browser.newContext({ viewport: viewport || { width: 1440, height: 950 } });
  const page = await ctx.newPage();
  await page.addInitScript((ls) => {
    try { for (const k of Object.keys(ls)) localStorage.setItem(k, ls[k]); } catch (e) {}
  }, (tone && tone.ls) || {});
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', fulfillOrAbort);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  // Phone widths (<=960px) render #projects-col in mc-chat-mode — a WhatsApp-
  // style .mc-chat-row list, never .card (mobile.js renderMobileChatList()).
  // Wait for whichever the current viewport actually produces.
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  // .sidebar-item is display:none at <=960px (replaced by the hamburger ->
  // mobile-drawer -> mobileDrawerNav('social'), which itself just calls this
  // same global). Call it directly so the check isn't gated on the drawer's
  // own open/close UI, which this ticket doesn't touch.
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors };
}

function reportUncaught(pageErrors, tag) {
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail(`${tag} uncaught page error: ${e}`));
}

// ── render checks, one per tone: header, manual notice, claim block bar,
// A8's primary-label-never-"Approve and schedule"-for-manual, ⋯ gating. ─────
async function runToneRenderChecks(browser, tone) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, tone);
  await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-restore-blog' }));
  await page.waitForSelector('.desk-v1-review', { timeout: 8000 });

  const header = (await page.textContent('.desk-v1-review-kind').catch(() => '') || '').trim();
  if (/1 of 2 to review/.test(header) && /Article/.test(header)) {
    ok(`[${tone.name}] header: "${header}"`);
  } else {
    fail(`[${tone.name}] header wrong: ${JSON.stringify(header)}`);
  }

  const notice = (await page.textContent('.desk-v1-review-notice').catch(() => '') || '');
  if (/You publish this one/.test(notice) && /Clayrune blog/.test(notice)) {
    ok(`[${tone.name}] manual-destination notice present`);
  } else {
    fail(`[${tone.name}] manual-destination notice missing/wrong: ${JSON.stringify(notice)}`);
  }

  const claimBar = (await page.textContent('.desk-v1-claimbar-blocked').catch(() => '') || '');
  if (/No source for this/.test(claimBar)) {
    ok(`[${tone.name}] claim-1 renders the blocked bar "⛔ No source for this."`);
  } else {
    fail(`[${tone.name}] claim block bar missing/wrong: ${JSON.stringify(claimBar)}`);
  }

  // A8: this channel is manual AND the fixture has a schedule (whenISO set) —
  // the exact case the rule exists for. Must never read "Approve and schedule".
  const primary = await page.$eval('[data-act-primary]', (b) => ({ label: b.textContent.trim(), disabled: b.disabled })).catch(() => null);
  if (primary && primary.label === 'Approve and create publishing task' && !/schedule/i.test(primary.label)) {
    ok(`[${tone.name}] A8: manual destination + schedule set -> "${primary.label}" (never "Approve and schedule")`);
  } else {
    fail(`[${tone.name}] A8 violated: manual+scheduled primary label = ${JSON.stringify(primary)}`);
  }
  if (primary && primary.disabled) {
    ok(`[${tone.name}] A8: primary disabled while claim-1 is blocked`);
  } else {
    fail(`[${tone.name}] A8: primary should be disabled while a claim is blocked, got ${JSON.stringify(primary)}`);
  }
  const reason = (await page.textContent('.desk-v1-review-reason').catch(() => '') || '');
  if (/source.*claim first/i.test(reason)) {
    ok(`[${tone.name}] A8: disabled reason shown under the button: "${reason.trim()}"`);
  } else {
    fail(`[${tone.name}] A8: disabled reason missing/wrong: ${JSON.stringify(reason)}`);
  }

  // Publish now only in ⋯, disabled with a reason while blocked, never a
  // second top-level button next to primary (CNT-05).
  const secondTopLevelPublish = await page.$('.desk-v1-review-actions-row [data-publish-now]');
  if (!secondTopLevelPublish) ok(`[${tone.name}] CNT-05: no "Publish now" button next to the primary`);
  else fail(`[${tone.name}] CNT-05 violated: Publish now rendered outside the ⋯ menu`);

  // v-restore-blog's channel (ch-blog) is manual, which _openMoreMenu gates
  // ahead of the blocked-claim reason (a manual destination can never
  // "Publish now" regardless of claim state) — that ordering, not the claim,
  // is what this version exercises.
  await page.click('[data-more-btn]');
  const publishNow = await page.$eval('[data-publish-now]', (b) => ({ disabled: b.disabled, title: b.title })).catch(() => null);
  if (publishNow && publishNow.disabled && /Manual destinations/i.test(publishNow.title)) {
    ok(`[${tone.name}] ⋯ Publish now: disabled with reason "${publishNow.title}" for a manual destination`);
  } else {
    fail(`[${tone.name}] ⋯ Publish now gating wrong: ${JSON.stringify(publishNow)}`);
  }

  reportUncaught(pageErrors, `[${tone.name}]`);
  await ctx.close();
}

// ── A6: selection toolbar renders BELOW the selection, overlapping neither
// the selection itself nor the paragraph after it. ──────────────────────────
async function runA6SelectionToolbar(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-restore-blog' }));
  await page.waitForSelector('.desk-v1-review-article', { timeout: 8000 });

  const rects = await page.evaluate(() => {
    const p = document.querySelector('[data-para-id="p-why"]');
    const range = document.createRange();
    range.selectNodeContents(p.firstChild || p);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    p.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    const selRect = range.getBoundingClientRect();
    const bar = document.querySelector('.desk-v1-review-seltoolbar');
    const barRect = bar ? bar.getBoundingClientRect() : null;
    const nextEl = document.querySelector('[data-para-id="p-claim"]');
    const nextRect = nextEl ? nextEl.getBoundingClientRect() : null;
    return { selRect: { top: selRect.top, bottom: selRect.bottom }, barRect, nextRect: nextRect && { top: nextRect.top } };
  });

  if (!rects.barRect) { fail('A6: selection toolbar did not appear on mouseup after a text selection'); }
  else {
    if (rects.barRect.top >= rects.selRect.bottom) {
      ok(`A6: toolbar top (${rects.barRect.top.toFixed(1)}) is below the selection bottom (${rects.selRect.bottom.toFixed(1)})`);
    } else {
      fail(`A6 violated: toolbar (top ${rects.barRect.top}) overlaps the selection (bottom ${rects.selRect.bottom})`);
    }
    if (rects.nextRect && rects.barRect.bottom <= rects.nextRect.top) {
      ok(`A6: toolbar bottom (${rects.barRect.bottom.toFixed(1)}) clears the next paragraph (top ${rects.nextRect.top.toFixed(1)})`);
    } else if (rects.nextRect) {
      fail(`A6 violated: toolbar (bottom ${rects.barRect.bottom}) covers the next paragraph (top ${rects.nextRect.top})`);
    }
  }

  // Ask Posy -> Shorter applies a canned rewrite via the command bus (Undo-able).
  const askPosyResult = await page.evaluate(() => {
    return new Promise((resolvePromise) => {
      const btn = document.querySelector('[data-sel-askposy]');
      btn.click();
      setTimeout(() => {
        document.querySelector('[data-sel-style="shorter"]').click();
        setTimeout(() => {
          const text = (document.querySelector('[data-para-id="p-why"]') || {}).textContent || '';
          resolvePromise({ text });
        }, 20);
      }, 10);
    });
  });
  if (/cheap mistake/.test(askPosyResult.text)) ok('A6: "Ask Posy -> Shorter" applies the rewrite in place');
  else fail(`A6: Ask Posy rewrite did not apply: ${JSON.stringify(askPosyResult.text)}`);

  reportUncaught(pageErrors, '[A6]');
  await ctx.close();
}

// ── A7: source -> simulated validation -> block clears ONLY on 'supports' or
// accepting a revision; rN increments each step. ────────────────────────────
async function runA7ClaimFlow(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-restore-blog' }));
  await page.waitForSelector('.desk-v1-review', { timeout: 8000 });

  // Adding a source does NOT itself clear the block (§4) — the block bar
  // stays up through the "doesn't support" outcome.
  await page.click('[data-claim-addsource]');
  await page.fill('[data-source-input]', 'this source rejects it');
  await page.click('[data-source-run]');
  await page.waitForSelector('.desk-v1-claimbar-blocked:has-text("doesn’t support")', { timeout: 2000 }).catch(() => {});
  let bar = (await page.textContent('.desk-v1-claimbar').catch(() => '') || '');
  if (/doesn.t support this/.test(bar)) ok('A7: an unsupportive source keeps the block ("doesn\'t support")');
  else fail(`A7: "doesn't support" outcome not rendered: ${JSON.stringify(bar)}`);
  let primaryDisabled = await page.$eval('[data-act-primary]', (b) => b.disabled);
  if (primaryDisabled) ok('A7: primary stays disabled after a "doesn\'t support" source');
  else fail('A7: primary should still be disabled after a "doesn\'t support" source');

  // Try another source that only partially checks out -> 'checked': inline
  // diff appears, block bar STILL up (clears only on supports/accept), r2 on
  // the Accept button (r1 -> r2).
  await page.click('[data-claim-addsource]');
  await page.fill('[data-source-input]', 'benchmark-sep-22.md');
  await page.click('[data-source-run]');
  await page.waitForSelector('.desk-v1-claimbar-checked', { timeout: 2000 });
  const acceptLabel = (await page.textContent('[data-claim-accept]').catch(() => '') || '').trim();
  if (acceptLabel === 'Accept → r2') ok(`A7: "checked" outcome offers "${acceptLabel}" (r1 -> r2)`);
  else fail(`A7: Accept button label wrong: ${JSON.stringify(acceptLabel)}`);
  const diffVisible = await page.$('.desk-v1-review-p-claim del') && await page.$('.desk-v1-review-p-claim ins');
  if (diffVisible) ok('A7: proposed revision shown as an inline diff (del/ins)');
  else fail('A7: no inline diff rendered for the "checked" outcome');
  primaryDisabled = await page.$eval('[data-act-primary]', (b) => b.disabled);
  const reasonText = (await page.textContent('.desk-v1-review-reason').catch(() => '') || '');
  if (primaryDisabled && /revision first/i.test(reasonText)) {
    ok(`A7: primary still disabled with a revision waiting: "${reasonText.trim()}"`);
  } else {
    fail(`A7: primary should stay disabled with a revision waiting, got disabled=${primaryDisabled} reason=${JSON.stringify(reasonText)}`);
  }

  // Accept the revision -> block clears (status: supports), revision is now
  // r2, primary becomes enabled (the ONLY remaining gate on this version).
  await page.click('[data-claim-accept]');
  await page.waitForSelector('.desk-v1-claimbar-ok', { timeout: 2000 });
  const okBar = (await page.textContent('.desk-v1-claimbar-ok').catch(() => '') || '');
  if (/Source supports it/.test(okBar)) ok('A7: accepting a revision clears the block ("✓ Source supports it")');
  else fail(`A7: block did not clear after accept: ${JSON.stringify(okBar)}`);
  primaryDisabled = await page.$eval('[data-act-primary]', (b) => b.disabled);
  if (!primaryDisabled) ok('A7: primary enabled once the only blocking claim is resolved');
  else fail('A7: primary still disabled after the blocking claim was resolved');

  const toastText = (await page.textContent('.toast').catch(() => '') || '');
  if (/r2/.test(toastText)) ok(`A7: toast announces the new revision: "${toastText.trim()}"`);
  else fail(`A7: no revision-bearing toast after accept: ${JSON.stringify(toastText)}`);

  // Undo puts it back exactly where accept found it (checked, r1, blocked).
  await page.click('.toast .toast-btn.primary');
  await page.waitForSelector('.desk-v1-claimbar-checked', { timeout: 2000 });
  primaryDisabled = await page.$eval('[data-act-primary]', (b) => b.disabled);
  if (primaryDisabled) ok('A7: Undo restores the block (revision -> r1, "checked")');
  else fail('A7: Undo did not restore the block');

  reportUncaught(pageErrors, '[A7]');
  await ctx.close();
}

// ── A8: primary label is capability x schedule, held/unrendered add their
// own disable reasons, manual NEVER reads "Approve and schedule". ───────────
async function runA8Matrix(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });

  const cases = [
    { desc: 'direct + schedule set -> "Approve and schedule"', setup: `
        const ch = window.DeskV1Fixtures.channels.find(c => c.id === 'ch-blog');
        ch.capability = 'direct'; ch.health = 'ok';
        window.DeskV1Fixtures.reviewDetail['v-restore-blog'].claims = [];
      `, expectLabel: 'Approve and schedule', expectDisabled: false },
    { desc: 'direct + no schedule -> "Approve"', setup: `
        const ch = window.DeskV1Fixtures.channels.find(c => c.id === 'ch-blog');
        ch.capability = 'direct'; ch.health = 'ok';
        window.DeskV1Fixtures.reviewDetail['v-restore-blog'].whenISO = null;
        window.DeskV1Fixtures.reviewDetail['v-restore-blog'].claims = [];
      `, expectLabel: 'Approve', expectDisabled: false },
    { desc: 'manual + schedule set -> still "Approve and create publishing task" (never "...and schedule")', setup: `
        const ch = window.DeskV1Fixtures.channels.find(c => c.id === 'ch-blog');
        ch.capability = 'manual'; ch.health = 'ok';
        window.DeskV1Fixtures.reviewDetail['v-restore-blog'].whenISO = '2026-09-30T12:00:00-07:00';
        window.DeskV1Fixtures.reviewDetail['v-restore-blog'].claims = [];
      `, expectLabel: 'Approve and create publishing task', expectDisabled: false },
    { desc: 'held destination -> disabled, "Reconnect ... first"', setup: `
        const ch = window.DeskV1Fixtures.channels.find(c => c.id === 'ch-blog');
        ch.capability = 'direct'; ch.health = 'held'; ch.holdReason = 'disconnected';
        window.DeskV1Fixtures.reviewDetail['v-restore-blog'].claims = [];
      `, expectLabel: 'Approve and schedule', expectDisabled: true, expectReason: /Reconnect/i },
  ];

  for (const c of cases) {
    await page.evaluate(c.setup);
    await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-restore-blog' }));
    await page.waitForSelector('.desk-v1-review', { timeout: 8000 });
    const primary = await page.$eval('[data-act-primary]', (b) => ({ label: b.textContent.trim(), disabled: b.disabled }));
    if (primary.label === c.expectLabel && primary.disabled === c.expectDisabled) {
      ok(`A8: ${c.desc} -> "${primary.label}"${primary.disabled ? ' (disabled)' : ''}`);
    } else {
      fail(`A8: ${c.desc} -> got ${JSON.stringify(primary)}`);
    }
    if (c.expectReason) {
      const reason = (await page.textContent('.desk-v1-review-reason').catch(() => '') || '');
      if (c.expectReason.test(reason)) ok(`A8: reason matches ${c.expectReason}: "${reason.trim()}"`);
      else fail(`A8: reason did not match ${c.expectReason}: ${JSON.stringify(reason)}`);
    }
  }

  // ⋯ Publish now: the held-destination reason, exercised on the last
  // matrix case's held+direct channel (a distinct code path from the
  // primary button's own reason text, checked above via _primaryInfo).
  await page.click('[data-more-btn]');
  const publishNowBlocked = await page.$eval('[data-publish-now]', (b) => ({ disabled: b.disabled, title: b.title })).catch(() => null);
  if (publishNowBlocked && publishNowBlocked.disabled && /Reconnect/i.test(publishNowBlocked.title)) {
    ok(`A8: ⋯ Publish now on a held direct channel: "${publishNowBlocked.title}"`);
  } else {
    fail(`A8: ⋯ Publish now (held direct) gating wrong: ${JSON.stringify(publishNowBlocked)}`);
  }

  // Video not rendered (MED-05): approval blocked until the current revision
  // has actually rendered.
  await page.evaluate(() => {
    const fam = window.DeskV1Fixtures.families.find(f => f.id === 'fam-install-video');
    const ch = window.DeskV1Fixtures.channels.find(c => c.id === 'ch-li-page');
    ch.health = 'ok'; // isolate the render gate from the held gate
    fam.versions.find(v => v.id === 'v-install-li').revision = 4; // != render.revision (3)
  });
  await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-install-li' }));
  await page.waitForSelector('.desk-v1-review-video', { timeout: 8000 });
  const videoPrimary = await page.$eval('[data-act-primary]', (b) => b.disabled);
  const videoReason = (await page.textContent('.desk-v1-review-reason').catch(() => '') || '');
  if (videoPrimary && /Render this version/i.test(videoReason)) {
    ok(`A8/MED-05: unrendered video output disables approval: "${videoReason.trim()}"`);
  } else {
    fail(`A8/MED-05: unrendered video should disable approval, got disabled=${videoPrimary} reason=${JSON.stringify(videoReason)}`);
  }

  reportUncaught(pageErrors, '[A8]');
  await ctx.close();
}

// ── Phone (§11): actions in a bottom bar, >=44px hit targets. ───────────────
async function runPhoneLayout(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} }, { width: 390, height: 844 });
  await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-restore-blog' }));
  await page.waitForSelector('.desk-v1-review', { timeout: 8000 });

  const layout = await page.evaluate(() => {
    const actions = document.querySelector('.desk-v1-review-actions');
    const cs = getComputedStyle(actions);
    const primary = document.querySelector('.desk-v1-review-primary');
    const more = document.querySelector('.desk-v1-review-morebtn');
    return {
      position: cs.position,
      bottom: cs.bottom,
      primaryH: primary.getBoundingClientRect().height,
      moreH: more.getBoundingClientRect().height,
      moreW: more.getBoundingClientRect().width,
    };
  });
  if (layout.position === 'sticky' && parseFloat(layout.bottom) === 0) {
    ok('§11: actions bar is pinned to the bottom of the scroll container (position: sticky; bottom: 0)');
  } else {
    fail(`§11: actions bar not pinned to bottom: ${JSON.stringify(layout)}`);
  }
  if (layout.primaryH >= 44) ok(`§11: primary button hit target ${layout.primaryH.toFixed(0)}px >= 44px`);
  else fail(`§11: primary button too short: ${layout.primaryH}px`);
  if (layout.moreH >= 44 && layout.moreW >= 44) ok(`§11: ⋯ button hit target ${layout.moreH.toFixed(0)}x${layout.moreW.toFixed(0)}px >= 44px`);
  else fail(`§11: ⋯ button hit target too small: ${layout.moreH}x${layout.moreW}`);

  const rowsStacked = await page.$eval('.desk-v1-review-layout', (el) => getComputedStyle(el).flexDirection === 'column');
  if (rowsStacked) ok('§11: article + rail stack vertically on phone width');
  else fail('§11: article + rail did not stack on phone width');

  reportUncaught(pageErrors, '[phone]');
  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  for (const tone of TONES) await runToneRenderChecks(browser, tone);
  await runA6SelectionToolbar(browser);
  await runA7ClaimFlow(browser);
  await runA8Matrix(browser);
  await runPhoneLayout(browser);
  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error('❌ FAIL — smoke harness error: ' + (e && e.message ? e.message : e));
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}

console.log(bad
  ? `\n❌ FAIL — ${bad} Desk v1 review check(s) regressed.`
  : '\n✅ PASS — Desk v1 T3 review: A6 (selection toolbar), A7 (claim validation gating), A8 (primary label + Publish now gating), phone bottom bar all hold.');
process.exit(exitCode);
