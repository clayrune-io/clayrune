#!/usr/bin/env node
/**
 * Desk v1 (MC-977, R0 plan T0b) — shared kit smoke: vocabulary, copy-lint,
 * state label, channel badge, toast+Undo+command bus, Add to… menu, the ⓘ
 * popover, the Posy box.
 *
 * No later ticket wires a live UI consumer for most of this yet (T1/T2a do),
 * so this drives `window.DeskV1Kit` directly against a real headless boot
 * (real index.html + real static/js|css, no network) — same hermetic shape
 * as desk.mjs / desk-v1-harness.mjs — rather than waiting on a surface that
 * doesn't exist in R0 T0b.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-kit.mjs
 * Exit 0 = every kit contract holds in all three tones where tone matters;
 * 1 = a case regressed / harness error.
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

const PID = 'smoke_deskv1kit';
const PROJECTS = [{
  id: PID, name: 'Desk v1 kit smoke', status: 'active', domain: 'general', emoji: '🧪',
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

// Runs the tone-sensitive subset (state label + channel badge render, no
// uncaught errors) in each of the three tones (ground rule 5). The
// interaction-heavy checks (toast, command bus, menu, popover, Posy box) are
// tone-independent DOM/behavior contracts, so they run once, in the default
// tone, to avoid tripling the harness for no new coverage.
async function runToneRenderChecks(browser, tone) {
  const ctx = await browser.newContext({ viewport: { width: 1200, height: 800 } });
  const page = await ctx.newPage();
  await page.addInitScript((ls) => {
    try { for (const k of Object.keys(ls)) localStorage.setItem(k, ls[k]); } catch (e) {}
  }, tone.ls);
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', fulfillOrAbort);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });

  const result = await page.evaluate(() => {
    const K = window.DeskV1Kit;
    const out = {};
    out.hasKit = !!K && K._stub !== true;
    const held = { id: 'ch-li-page', platform: 'linkedin', identity: 'Clayrune page', label: 'in · Clayrune page', capability: 'direct', health: 'held', holdReason: 'LinkedIn page disconnected' };
    const manual = { id: 'ch-blog', platform: 'blog', identity: 'Clayrune blog', label: 'Clayrune blog', capability: 'manual', health: 'ok' };
    const direct = { id: 'ch-x-ron', platform: 'x', identity: '@ron', label: '𝕏 @ron', capability: 'direct', health: 'ok' };
    out.stateHTML = K.stateLabelHTML('verified_published');
    out.heldBadge = K.channelBadge(held, { reviewMode: 'each_piece' });
    out.manualBadge = K.channelBadge(manual, { reviewMode: 'each_piece' });
    out.directBadge = K.channelBadge(direct, { reviewMode: 'each_piece' });
    // Mount both so a11y/contrast checks below can read real computed styles.
    const host = document.createElement('div');
    host.id = 'kit-render-probe';
    host.innerHTML = out.stateHTML + out.heldBadge + out.manualBadge + out.directBadge;
    document.body.appendChild(host);
    return out;
  }).catch((e) => ({ error: String(e) }));

  if (result.error || !result.hasKit) {
    fail(`[${tone.name}] DeskV1Kit missing or still the T0a stub: ${JSON.stringify(result)}`);
  } else {
    ok(`[${tone.name}] window.DeskV1Kit loaded (real T0b implementation, not the stub)`);
    if (/✓/.test(result.stateHTML) && /Verified published/.test(result.stateHTML)) {
      ok(`[${tone.name}] stateLabelHTML: glyph + word both present`);
    } else {
      fail(`[${tone.name}] stateLabelHTML missing glyph or word: ${result.stateHTML}`);
    }
    if (/Held/.test(result.heldBadge) && /disconnected|LinkedIn page disconnected/.test(result.heldBadge)) {
      ok(`[${tone.name}] channelBadge: held channel carries the hold reason (in the title attr)`);
    } else {
      fail(`[${tone.name}] channelBadge held case wrong: ${result.heldBadge}`);
    }
    if (/You publish it/.test(result.manualBadge)) {
      ok(`[${tone.name}] channelBadge: manual capability -> "You publish it" (title attr)`);
    } else {
      fail(`[${tone.name}] channelBadge manual case wrong: ${result.manualBadge}`);
    }
    if (/Publishes after approval/.test(result.directBadge)) {
      ok(`[${tone.name}] channelBadge: direct + each_piece review -> "Publishes after approval"`);
    } else {
      fail(`[${tone.name}] channelBadge direct case wrong: ${result.directBadge}`);
    }
  }

  // A15 (greyscale): the glyph must differ per state even with color removed.
  // Assert the glyph text itself, not a color, carries "verified" vs "held"
  // vs "blocked" apart — already true by construction (distinct code points),
  // checked here so a future edit that collapses two glyphs to the same
  // character trips this instead of only showing up in a design review.
  const glyphsDistinct = await page.evaluate(() => {
    const K = window.DeskV1Kit;
    const glyphs = new Set(['verified_published', 'held', 'blocked', 'planned', 'failed'].map(s => K.stateLabel(s).glyph));
    return glyphs.size;
  });
  if (glyphsDistinct === 5) ok(`[${tone.name}] 5 sampled states render 5 distinct glyphs (greyscale-safe)`);
  else fail(`[${tone.name}] expected 5 distinct glyphs, got ${glyphsDistinct}`);

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail(`[${tone.name}] uncaught page error: ${e}`));
  await ctx.close();
}

async function runInteractionChecks(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1200, height: 800 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', fulfillOrAbort);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });

  // ── copy lint (A12) ────────────────────────────────────────────────────
  const lint = await page.evaluate(() => {
    const K = window.DeskV1Kit;
    return {
      clean: K.lintCopy('Publishes after approval — No video-generation charge yet.'),
      dirty: K.lintCopy('This piece posts automatically for free once it is released.'),
    };
  });
  if (lint.clean.length === 0) ok('lintCopy: compliant §9 copy passes with zero hits');
  else fail(`lintCopy: false positive on clean copy: ${JSON.stringify(lint.clean)}`);
  const dirtyPhrases = lint.dirty.map(h => h.phrase.toLowerCase());
  if (['posts automatically', 'free', 'released'].every(p => dirtyPhrases.includes(p))) {
    ok(`lintCopy: catches all 3 banned phrases in one string (${dirtyPhrases.join(', ')})`);
  } else {
    fail(`lintCopy: missed a banned phrase, got ${JSON.stringify(lint.dirty)}`);
  }

  // ── toast with Undo + command bus (§10) ───────────────────────────────
  const cmdResult = await page.evaluate(() => {
    return new Promise((resolvePromise) => {
      const K = window.DeskV1Kit;
      let didVal = 0, undidVal = 0;
      K.commandBus.run({
        label: 'Added Clayrune blog to "Install in two minutes"',
        do: () => { didVal = 1; },
        undo: () => { undidVal = 1; },
      });
      // announce() writes on the next animation frame (see desk-v1-kit.js's
      // own comment: clear then rAF-write, so back-to-back identical
      // messages both re-fire for a screen reader) — read it after one.
      requestAnimationFrame(() => {
        const announced = (document.getElementById('desk-v1-sr-announcer') || {}).textContent || null;
        const btn = document.querySelector('.toast.toast-action .toast-btn.primary');
        const hasBtn = !!btn && /undo/i.test(btn.textContent || '');
        if (btn) btn.click();
        setTimeout(() => {
          resolvePromise({ didVal, undidVal, hasBtn, announced, historyLenAfterUndo: K.commandBus.history.length });
        }, 50);
      });
    });
  });
  if (cmdResult.didVal === 1) ok('commandBus.run: do() fires immediately (optimistic update)');
  else fail('commandBus.run: do() did not fire');
  if (cmdResult.hasBtn) ok('commandBus.run: toast renders with a primary "Undo" button');
  else fail('commandBus.run: no Undo button found on the toast');
  if (cmdResult.undidVal === 1) ok('commandBus.run: clicking Undo calls the command\'s undo()');
  else fail('commandBus.run: Undo button did not invoke undo()');
  if (cmdResult.historyLenAfterUndo === 0) ok('commandBus.run: history pops the command once undone');
  else fail(`commandBus.run: expected empty history after undo, got length ${cmdResult.historyLenAfterUndo}`);
  if (cmdResult.announced && /Install in two minutes/.test(cmdResult.announced)) {
    ok('commandBus.run: announces the command label to the shared aria-live region');
  } else {
    fail(`commandBus.run: announcer text wrong: ${JSON.stringify(cmdResult.announced)}`);
  }

  const throws = await page.evaluate(() => {
    try { window.DeskV1Kit.commandBus.run({ label: 'no inverse', do: () => {} }); return false; }
    catch (e) { return true; }
  });
  if (throws) ok('commandBus.run: refuses a command with no undo()');
  else fail('commandBus.run: accepted a command with no undo() — a drop with no inverse should be impossible');

  // ── Add to… ▾ menu (UX-05) ─────────────────────────────────────────────
  const menuResult = await page.evaluate(() => {
    return new Promise((resolvePromise) => {
      const K = window.DeskV1Kit;
      const trigger = document.createElement('button');
      trigger.id = 'addto-trigger'; trigger.textContent = '+';
      trigger.className = 'desk-v1-addto-wrap';
      document.body.appendChild(trigger);
      let picked = null;
      K.bindAddToTrigger(trigger, () => [{ id: 'camp-1', label: 'Windows beta testers' }], (id) => { picked = id; });
      trigger.click();
      setTimeout(() => {
        const items = Array.from(document.querySelectorAll('.desk-v1-addto-menu button')).map(b => b.textContent);
        const focusedIsFirstItem = document.activeElement && document.activeElement.textContent === items[0];
        document.querySelectorAll('.desk-v1-addto-menu button')[0].click();
        setTimeout(() => {
          const closedAfterPick = !document.querySelector('.desk-v1-addto-menu');
          resolvePromise({ items, focusedIsFirstItem, picked, closedAfterPick });
        }, 10);
      }, 10);
    });
  });
  if (menuResult.items.includes('Windows beta testers') && menuResult.items.includes('+ New campaign')) {
    ok(`Add to… menu: lists the running campaign plus "+ New campaign" (${menuResult.items.join(', ')})`);
  } else {
    fail(`Add to… menu: wrong items ${JSON.stringify(menuResult.items)}`);
  }
  if (menuResult.focusedIsFirstItem) ok('Add to… menu: opening it (click path) moves focus to the first item');
  else fail('Add to… menu: focus did not land on the first item on open');
  if (menuResult.picked === 'camp-1') ok('Add to… menu: clicking an item calls onPick with its id');
  else fail(`Add to… menu: onPick got ${JSON.stringify(menuResult.picked)}`);
  if (menuResult.closedAfterPick) ok('Add to… menu: closes itself after a pick');
  else fail('Add to… menu: still open after a pick');

  const escResult = await page.evaluate(() => {
    return new Promise((resolvePromise) => {
      const K = window.DeskV1Kit;
      const trigger = document.getElementById('addto-trigger');
      K.addToMenu(trigger, [{ id: 'camp-1', label: 'Windows beta testers' }], () => {});
      setTimeout(() => {
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
        setTimeout(() => {
          resolvePromise({
            closed: !document.querySelector('.desk-v1-addto-menu'),
            refocused: document.activeElement === trigger,
          });
        }, 10);
      }, 10);
    });
  });
  if (escResult.closed) ok('Add to… menu: Esc closes it');
  else fail('Add to… menu: Esc did not close it');
  if (escResult.refocused) ok('Add to… menu: Esc returns focus to the trigger (UX-05)');
  else fail('Add to… menu: focus did not return to the trigger after Esc');

  // ── ⓘ popover ──────────────────────────────────────────────────────────
  const popoverResult = await page.evaluate(() => {
    return new Promise((resolvePromise) => {
      const K = window.DeskV1Kit;
      const host = document.createElement('div');
      host.innerHTML = `<span>Rate ${K.infoIconHTML('rate-explain')}</span>`;
      document.body.appendChild(host);
      K.bindInfoIcons(host, { 'rate-explain': 'AI usage is billed per render; this is not a subscription.' });
      host.querySelector('.desk-v1-info-btn').click();
      setTimeout(() => {
        const text = (document.querySelector('.desk-v1-info-popover') || {}).textContent || '';
        document.body.click(); // outside click
        setTimeout(() => {
          resolvePromise({ openedText: text, closedAfterOutsideClick: !document.querySelector('.desk-v1-info-popover') });
        }, 10);
      }, 10);
    });
  });
  if (/billed per render/.test(popoverResult.openedText)) ok('ℹ popover: opens with the bound explanation text');
  else fail(`ℹ popover: wrong/missing text: ${JSON.stringify(popoverResult.openedText)}`);
  if (popoverResult.closedAfterOutsideClick) ok('ℹ popover: an outside click closes it');
  else fail('ℹ popover: still open after an outside click');

  // ── Posy box (scope + suggestion + chips + input) ─────────────────────
  const posyResult = await page.evaluate(() => {
    return new Promise((resolvePromise) => {
      const K = window.DeskV1Kit;
      const host = document.createElement('div');
      host.id = 'posy-probe';
      const inputId = 'posy-smoke-input';
      host.innerHTML = K.posyBoxHTML({
        inputId, scopeLabel: 'Install in two minutes',
        suggestion: 'Signups are coming from the X clip. A LinkedIn cut is the cheapest next piece.',
        chips: ['Make the LinkedIn cut', 'Draft a follow-up post'],
      });
      document.body.appendChild(host);
      let sent = null;
      K.bindPosyBox(host, inputId, (text) => { sent = text; });
      const structOK = !!host.querySelector('.agent-output') && !!host.querySelector('.agent-question-chip')
        && !!host.querySelector('.agent-input-row') && !!host.querySelector('.desk-v1-posy-scope');
      host.querySelectorAll('.agent-question-chip')[0].click();
      const filledFromChip = document.getElementById(inputId).value;
      document.getElementById(inputId).value = 'Custom instruction';
      host.querySelector('[data-posy-send]').click();
      resolvePromise({ structOK, filledFromChip, sent, clearedAfterSend: document.getElementById(inputId).value });
    });
  });
  if (posyResult.structOK) ok('Posy box: built from .agent-output/.agent-question-chip/.agent-input-row (not forked)');
  else fail('Posy box: missing one of the reused chat classes');
  if (posyResult.filledFromChip === 'Make the LinkedIn cut') ok('Posy box: a chip fills the input rather than auto-sending');
  else fail(`Posy box: chip click did not fill the input: ${JSON.stringify(posyResult.filledFromChip)}`);
  if (posyResult.sent === 'Custom instruction') ok('Posy box: Send calls onSend with the typed text');
  else fail(`Posy box: onSend got ${JSON.stringify(posyResult.sent)}`);
  if (posyResult.clearedAfterSend === '') ok('Posy box: input clears after Send');
  else fail(`Posy box: input not cleared after Send: ${JSON.stringify(posyResult.clearedAfterSend)}`);

  // ── Posy box compact/arrow options (T3 shell polish): defaults stay
  // byte-identical; the new options are opt-in and additive. ─────────────
  const posyOptsResult = await page.evaluate(() => {
    const K = window.DeskV1Kit;
    const defaultHTML = K.posyBoxHTML({ inputId: 'posy-default-probe', scopeLabel: 'This article' });
    const compactHTML = K.posyBoxHTML({ inputId: 'posy-compact-probe', scopeLabel: 'This article', compact: true, sendStyle: 'arrow' });
    const before = document.createElement('div');
    before.innerHTML = defaultHTML;
    const after = document.createElement('div');
    after.innerHTML = compactHTML;
    return {
      defaultHasName: !!before.querySelector('.desk-thread-name'),
      defaultHasTextSend: !!before.querySelector('.btn-dispatch[data-posy-send]'),
      defaultHasArrow: !!before.querySelector('.desk-v1-posy-send-arrow'),
      compactHasName: !!after.querySelector('.desk-thread-name'),
      compactHasScope: !!after.querySelector('.desk-v1-posy-scope'),
      compactArrowLabel: (after.querySelector('.desk-v1-posy-send-arrow') || {}).getAttribute
        ? after.querySelector('.desk-v1-posy-send-arrow').getAttribute('aria-label') : null,
      compactHasTextSend: !!after.querySelector('.btn-dispatch[data-posy-send]'),
    };
  });
  if (posyOptsResult.defaultHasName && posyOptsResult.defaultHasTextSend && !posyOptsResult.defaultHasArrow) {
    ok('Posy box: default (no compact/sendStyle) still shows the name row and text Send button');
  } else fail(`Posy box: default markup changed: ${JSON.stringify(posyOptsResult)}`);
  if (!posyOptsResult.compactHasName && posyOptsResult.compactHasScope) {
    ok('Posy box: compact:true drops the name row but keeps the scope chip');
  } else fail(`Posy box: compact:true did not hide the name row / lost the scope chip: ${JSON.stringify(posyOptsResult)}`);
  if (posyOptsResult.compactArrowLabel === 'Send' && !posyOptsResult.compactHasTextSend) {
    ok(`Posy box: sendStyle:'arrow' renders a round icon button labelled "Send", not the text pill`);
  } else fail(`Posy box: sendStyle:'arrow' did not render the expected icon button: ${JSON.stringify(posyOptsResult)}`);

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail('uncaught page error: ' + e));
  await ctx.close();
}

// ── T3: Posy task lifecycle (§5) — Sending -> Working -> Ready / failed /
// timed out / question, built on Tilda's item-5 draft store extended (never
// forked). Uses Playwright's fake clock (installed after boot) so the
// ~1.5-4s simulated latency never costs real wall time — same "the smoke
// never sleeps" rule the doc calls out for the 35s 'slow' hook. ───────────
async function runTaskLifecycleChecks(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1200, height: 800 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', fulfillOrAbort);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });
  await page.clock.install();

  const mountAndSend = (key, text) => page.evaluate(({ key, text }) => {
    const K = window.DeskV1Kit;
    const id = 'lc-input-' + key.replace(/[^a-z0-9]/gi, '-');
    const host = document.createElement('div');
    host.innerHTML = K.posyBoxHTML({ inputId: id });
    document.body.appendChild(host);
    const box = host.querySelector('.desk-v1-posy-box');
    box.setAttribute('data-lc-box', key);
    K.bindPosyBox(box, id, () => { box.setAttribute('data-lc-onsend', '1'); }, { draftKey: key, taskLifecycle: true });
    const ta = document.getElementById(id);
    ta.value = text;
    const t0 = performance.now();
    box.querySelector('[data-posy-send]').click();
    return {
      elapsed: performance.now() - t0,
      sendingHTML: box.querySelector('.desk-v1-posy-output').innerHTML,
      footerText: (box.querySelector('.desk-v1-posy-footer') || {}).textContent || null,
    };
  }, { key, text });
  const boxState = (key) => page.evaluate((key) => {
    const box = document.querySelector(`[data-lc-box="${key}"]`);
    return {
      html: box.querySelector('.desk-v1-posy-output').innerHTML,
      onSendFired: box.getAttribute('data-lc-onsend') === '1',
      taValue: box.querySelector('textarea').value,
    };
  }, key);
  const setForce = (force) => page.evaluate((force) => { window.__deskV1PosyForce = force; }, force);

  // ── Send -> Sending bubble, < 100ms, R0 footer present ─────────────────
  await setForce(undefined);
  const sendTiming = await mountAndSend('lc:send', 'Make it shorter');
  if (sendTiming.elapsed < 100) ok(`Posy task lifecycle: Sending bubble paints in ${sendTiming.elapsed.toFixed(1)}ms (< 100ms)`);
  else fail(`Posy task lifecycle: Sending bubble took ${sendTiming.elapsed}ms (>= 100ms)`);
  if (/Make it shorter/.test(sendTiming.sendingHTML) && /Sending/.test(sendTiming.sendingHTML)) {
    ok('Posy task lifecycle: Send shows the request bubble + "Sending…" immediately');
  } else fail(`Posy task lifecycle: Sending state wrong: ${sendTiming.sendingHTML}`);
  if (sendTiming.footerText === 'Simulated reply (R0)') ok('Posy task lifecycle: R0 "Simulated reply (R0)" footer present');
  else fail(`Posy task lifecycle: R0 footer missing/wrong: ${JSON.stringify(sendTiming.footerText)}`);

  // ── forced fail: exact §5 copy, no "applied", text restored via Edit request ─
  await setForce('fail');
  await mountAndSend('lc:fail', 'Widen the audience');
  await page.clock.fastForward(300);
  const failWorking = await boxState('lc:fail');
  if (/data-act="tool"/.test(failWorking.html) && /Working on it/.test(failWorking.html)) {
    ok('Posy task lifecycle: Working state shows the typing indicator + stage text');
  } else fail(`Posy task lifecycle: Working state wrong: ${failWorking.html}`);
  await page.clock.fastForward(4200);
  const failed = await boxState('lc:fail');
  if (/Posy couldn.t finish: Simulated failure \(R0 test hook\)\. Nothing was changed\./.test(failed.html)
      && failed.html.includes('data-posy-retry') && failed.html.includes('data-posy-edit')) {
    ok('Posy task lifecycle: forced fail shows the exact §5 Failed copy + Retry/Edit request');
  } else fail(`Posy task lifecycle: forced fail copy wrong: ${failed.html}`);
  if (!failed.onSendFired) ok('Posy task lifecycle: onSend never fires on a Failed task (only on Ready)');
  else fail('Posy task lifecycle: onSend fired on a Failed task');
  const editResult = await page.evaluate((key) => {
    const box = document.querySelector(`[data-lc-box="${key}"]`);
    box.querySelector('[data-posy-edit]').click();
    return { taValue: box.querySelector('textarea').value };
  }, 'lc:fail');
  if (editResult.taValue === 'Widen the audience') ok('Posy task lifecycle: "Edit request" restores the original text into the box');
  else fail(`Posy task lifecycle: text not restored on Edit request: ${JSON.stringify(editResult.taValue)}`);

  // ── forced timeout: exact §5 copy + "Keep waiting" ──────────────────────
  await setForce('timeout');
  await mountAndSend('lc:timeout', 'Post every day');
  await page.clock.fastForward(300);
  await page.clock.fastForward(4200);
  const timedOut = await boxState('lc:timeout');
  if (/Posy couldn.t finish: timed out\. Nothing was changed\./.test(timedOut.html) && timedOut.html.includes('data-posy-keepwaiting')) {
    ok('Posy task lifecycle: forced timeout shows the exact §5 copy + "Keep waiting"');
  } else fail(`Posy task lifecycle: forced timeout copy wrong: ${timedOut.html}`);

  // ── forced question: exact §5 copy, Yes/No, resolves to Ready on answer ─
  await setForce('question');
  await mountAndSend('lc:question', 'Auto-answer every reply');
  await page.clock.fastForward(300);
  await page.clock.fastForward(4200);
  const question = await boxState('lc:question');
  if (/This would widen what Posy can do — go ahead\?/.test(question.html)
      && /data-posy-answer="Yes"/.test(question.html) && /data-posy-answer="No"/.test(question.html)) {
    ok('Posy task lifecycle: forced question shows the exact §5 question text + Yes/No');
  } else fail(`Posy task lifecycle: forced question copy wrong: ${question.html}`);
  await page.evaluate((key) => {
    document.querySelector(`[data-lc-box="${key}"] [data-posy-answer="Yes"]`).click();
  }, 'lc:question');
  await page.clock.fastForward(1600);
  const answered = await boxState('lc:question');
  if (answered.onSendFired) ok('Posy task lifecycle: answering the question resolves to Ready and fires onSend');
  else fail('Posy task lifecycle: answering the question never reached Ready/onSend');

  // ── no "applied" text anywhere except Ready (which kit never paints itself) ─
  const nonReadyHTML = [sendTiming.sendingHTML, failWorking.html, failed.html, timedOut.html, question.html].join('\n');
  if (!/applied/i.test(nonReadyHTML)) ok('Posy task lifecycle: no "applied" text in any non-Ready state');
  else fail(`Posy task lifecycle: "applied" leaked into a non-Ready state: ${nonReadyHTML}`);

  // ── navigate away mid-ask and back -> still Working; Home's anyPosyWorking ─
  await setForce('slow');
  await mountAndSend('lc:nav', 'Draft a longer plan');
  await page.clock.fastForward(300); // past Sending -> Working
  const stillWorking = await page.evaluate(() => window.DeskV1Kit.anyPosyWorking('lc:nav'));
  if (stillWorking) ok('Posy task lifecycle: anyPosyWorking(prefix) is true while a task is in flight');
  else fail('Posy task lifecycle: anyPosyWorking(prefix) false while a task is still working');
  const labelOk = await page.evaluate(() => window.DeskV1Kit.POSY_WORKING_LABEL === '⟳ Posy working');
  if (labelOk) ok('Posy task lifecycle: POSY_WORKING_LABEL is exactly "⟳ Posy working"');
  else fail('Posy task lifecycle: POSY_WORKING_LABEL wrong');
  const remount = await page.evaluate((key) => {
    document.querySelector(`[data-lc-box="${key}"]`).remove(); // simulate navigating away (DOM torn down)
    const K = window.DeskV1Kit;
    const id = 'lc-input-nav-remount';
    const host = document.createElement('div');
    host.innerHTML = K.posyBoxHTML({ inputId: id });
    document.body.appendChild(host);
    const box = host.querySelector('.desk-v1-posy-box');
    K.bindPosyBox(box, id, () => {}, { draftKey: key, taskLifecycle: true }); // navigating back: fresh mount, same draftKey
    return { html: box.querySelector('.desk-v1-posy-output').innerHTML };
  }, 'lc:nav');
  if (/data-act="tool"/.test(remount.html) && /Working on it/.test(remount.html)) {
    ok('Posy task lifecycle: navigating away mid-ask and back still shows Working (not blank)');
  } else fail(`Posy task lifecycle: remount after navigate-away lost the in-flight ask: ${remount.html}`);
  await page.clock.fastForward(35200); // let the slow task resolve so it stops reporting as working
  const doneWorking = await page.evaluate(() => window.DeskV1Kit.anyPosyWorking('lc:nav'));
  if (!doneWorking) ok('Posy task lifecycle: anyPosyWorking(prefix) is false once the task reaches Ready');
  else fail('Posy task lifecycle: anyPosyWorking(prefix) still true after the task resolved');

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail('uncaught page error: ' + e));
  await ctx.close();
}

// ── IA2 acceptance (§5 row IA2): "ask survives project -> campaign ->
// project nav". Every other check above drives window.DeskV1Kit directly
// against a bare boot; this one needs the real shell (desk-v1-shell.js's
// deskV1Nav + desk-v1-project.js's project-scoped Posy box) so a stale
// draftKey collision between the project scope and a campaign scope would
// actually show up here — matching desk-v1-project.mjs's own boot pattern. ─
async function runShellBootedPage(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', fulfillOrAbort);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors };
}

async function runProjectCampaignProjectNavDraftSurvival(browser) {
  const { ctx, page, pageErrors } = await runShellBootedPage(browser);

  await page.evaluate(() => window.deskV1Nav('project', { projectId: 'clayrune' }));
  await page.waitForSelector('#desk-v1-project-posy .agent-task-input', { timeout: 4000 });
  await page.fill('#desk-v1-project-posy-input', 'Ask surviving project -> campaign -> project nav');

  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1', projectId: 'clayrune' }));
  await page.waitForSelector('.desk-v1-camp-posy .agent-task-input', { timeout: 4000 });

  await page.evaluate(() => window.deskV1Nav('project', { projectId: 'clayrune' }));
  await page.waitForSelector('#desk-v1-project-posy .agent-task-input', { timeout: 4000 });
  const draftValue = await page.$eval('#desk-v1-project-posy-input', (el) => el.value);
  draftValue === 'Ask surviving project -> campaign -> project nav'
    ? ok(`draft key survives project -> campaign -> project nav: ${JSON.stringify(draftValue)}`)
    : fail(`draft lost across project -> campaign -> project nav: ${JSON.stringify(draftValue)}`);

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail('[project-campaign-project nav] uncaught page error: ' + e));
  await ctx.close();
}

// ── R2-1 (IA revision 2 §5/§8): validatePlan's new goal/term/how-budget
// gates and the bounds-hash widen/narrow contract. Pure-function checks
// against window.DeskV1Kit — no live UI consumer exists yet (R2-3/R2-4/
// R2-6/R2-11 wire the map, goal editor and how-budget UI), so this drives
// the kit the same way runInteractionChecks drives lintCopy above.
async function runR21PlanBoundsChecks(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1200, height: 800 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', fulfillOrAbort);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });

  const result = await page.evaluate(() => {
    const K = window.DeskV1Kit;
    const base = {
      accounts: [{ channel_id: 'ch-x-ron' }], cadence: { per_week: 2 }, end: { post_cap: 10 },
    };

    // §9 Q1 binding: goal needs target + source; plan without a goal target
    // -> validatePlan missing names 'goal' with stop 'goal'.
    const noTarget = K.validatePlan({ ...base, goal: { metric: 'signups', source: 'manual' } }, null, {});
    const withTarget = K.validatePlan({ ...base, goal: { metric: 'signups', source: 'manual', target: 30 } }, null, {});

    // §5.2/§9 Q3 binding: $120 earmark vs $100 project remaining -> missing
    // names 'how' with "short by $20".
    const short = K.validatePlan({ ...base, how: { budget: { source: 'project', amount: 120 } } }, null, { projectRemaining: 100 });
    const covered = K.validatePlan({ ...base, how: { budget: { source: 'project', amount: 80 } } }, null, { projectRemaining: 100 });

    // Bounds hash: widening how.budget.amount changes the hash; lowering
    // does not.
    const prevBounds = { budget: { source: 'project', amount: 60 } };
    const prevHash = K.computeBoundsHash(prevBounds);
    const widenedBounds = { budget: { source: 'project', amount: 80 } };
    const loweredBounds = { budget: { source: 'project', amount: 40 } };
    const widenedHash = K.nextBoundsHash(prevHash, prevBounds, widenedBounds);
    const loweredHash = K.nextBoundsHash(prevHash, prevBounds, loweredBounds);

    // Dave's review (2026-09-29): every §141 bounds-table dimension must
    // widen the hash, not just budget — an account add (R2-10), a cadence
    // raise (R2-9), an end date/post-cap raise or cap removal, and a term
    // extension (R2-11) all count. One prev/next pair per dimension, each
    // checked against a shared baseline so "widen" and "narrow" both
    // exercise the same field in isolation.
    const dims = {
      accounts: {
        prev: { accounts: [{ channel_id: 'ch-x-ron' }] },
        widen: { accounts: [{ channel_id: 'ch-x-ron' }, { channel_id: 'ch-li-page' }] },
        narrow: { accounts: [{ channel_id: 'ch-x-ron' }] },
      },
      cadence: {
        prev: { cadence: { per_week: 2 } },
        widen: { cadence: { per_week: 5 } },
        narrow: { cadence: { per_week: 1 } },
      },
      end_date: {
        prev: { end: { date: '2026-10-01' } },
        widen: { end: { date: '2026-12-25' } },
        narrow: { end: { date: '2026-09-15' } },
      },
      end_cap: {
        prev: { end: { post_cap: 10 } },
        widen: { end: { post_cap: null } }, // cap removed = unbounded = wider
        narrow: { end: { post_cap: 5 } },
      },
      term: {
        prev: { term: { ends: '2026-10-20' } },
        widen: { term: { ends: '2026-12-01' } },
        narrow: { term: { ends: '2026-10-10' } },
      },
    };
    const dimResults = {};
    for (const [name, { prev, widen, narrow }] of Object.entries(dims)) {
      const h0 = K.computeBoundsHash(prev);
      dimResults[name] = {
        widenChanged: K.nextBoundsHash(h0, prev, widen) !== h0,
        narrowUnchanged: K.nextBoundsHash(h0, prev, narrow) === h0,
      };
    }

    return {
      noTargetMissing: noTarget.missing.map((m) => m.bound),
      noTargetStop: (noTarget.missing.find((m) => m.bound === 'goal') || {}).stop,
      withTargetOk: withTarget.missing.some((m) => m.bound === 'goal'),
      shortMissing: short.missing.find((m) => m.bound === 'how_budget'),
      coveredMissing: covered.missing.some((m) => m.bound === 'how_budget'),
      prevHash, widenedHash, loweredHash,
      dimResults,
    };
  });

  result.noTargetMissing.includes('goal') && result.noTargetStop === 'goal'
    ? ok(`plan without goal target -> validatePlan missing names stop 'goal': ${JSON.stringify(result.noTargetMissing)}`)
    : fail(`goal-target gate wrong: ${JSON.stringify(result.noTargetMissing)}, stop=${result.noTargetStop}`);
  !result.withTargetOk
    ? ok('plan with goal target + source clears the goal bound')
    : fail('goal bound still missing once target + source are set');
  result.shortMissing && result.shortMissing.detail === 'short by $20'
    ? ok(`$120 earmark vs $100 project remaining -> missing names 'how' with "short by $20": ${JSON.stringify(result.shortMissing)}`)
    : fail(`budget-earmark gate wrong: ${JSON.stringify(result.shortMissing)}`);
  !result.coveredMissing
    ? ok('$80 earmark vs $100 project remaining clears the how_budget bound')
    : fail('how_budget bound still fired for a covered earmark');
  result.widenedHash !== result.prevHash
    ? ok(`widening how.budget.amount (60 -> 80) changes the bounds hash: ${result.prevHash} -> ${result.widenedHash}`)
    : fail(`widening did not change the bounds hash: ${result.prevHash}`);
  result.loweredHash === result.prevHash
    ? ok(`lowering how.budget.amount (60 -> 40) keeps the bounds hash: ${result.loweredHash}`)
    : fail(`lowering changed the bounds hash: ${result.prevHash} -> ${result.loweredHash}`);
  for (const [name, { widenChanged, narrowUnchanged }] of Object.entries(result.dimResults)) {
    widenChanged
      ? ok(`boundsWiden: widening '${name}' changes the bounds hash`)
      : fail(`boundsWiden: widening '${name}' did NOT change the bounds hash`);
    narrowUnchanged
      ? ok(`boundsWiden: narrowing '${name}' keeps the bounds hash`)
      : fail(`boundsWiden: narrowing '${name}' changed the bounds hash`);
  }

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail('[R2-1 plan bounds] uncaught page error: ' + e));
  await ctx.close();
}

// ── R2-1 (§8): fixture load has 0 'production' keys (renamed to
// presence.budget).
async function runR21FixtureProductionKeyCheck(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1200, height: 800 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', fulfillOrAbort);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });

  const productionKeyCount = await page.evaluate(() => {
    const src = Array.from(document.scripts)
      .map((s) => s.src)
      .find((s) => /desk-v1-fixtures\.js$/.test(s));
    return fetch(src).then((r) => r.text()).then((text) => (text.match(/\bproduction\b\s*:/g) || []).length);
  });
  productionKeyCount === 0
    ? ok('fixture load has 0 \'production\' keys (renamed to presence.budget)')
    : fail(`fixtures still declare ${productionKeyCount} 'production' key(s)`);

  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  for (const tone of TONES) await runToneRenderChecks(browser, tone);
  await runInteractionChecks(browser);
  await runTaskLifecycleChecks(browser);
  await runProjectCampaignProjectNavDraftSurvival(browser);
  await runR21PlanBoundsChecks(browser);
  await runR21FixtureProductionKeyCheck(browser);
  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error('❌ FAIL — smoke harness error: ' + (e && e.message ? e.message : e));
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}

console.log(bad
  ? `\n❌ FAIL — ${bad} Desk v1 kit check(s) regressed.`
  : '\n✅ PASS — the shared kit: vocabulary + copy-lint, state label, channel badge, toast+Undo+command bus, Add to… menu, ℹ popover and the Posy box all hold.');
process.exit(exitCode);
