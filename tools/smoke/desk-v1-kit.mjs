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

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail('uncaught page error: ' + e));
  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  for (const tone of TONES) await runToneRenderChecks(browser, tone);
  await runInteractionChecks(browser);
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
