#!/usr/bin/env node
/**
 * Pointer-drag (MC-977, R0 plan T0c) — the generic state machine extracted
 * from floor.js's drag-to-hire. This drives `window.PointerDrag.begin()`
 * directly against a small synthetic fixture (not the Floor's own DOM),
 * so it exercises the MECHANICS in isolation: activation threshold, touch
 * long-press, `touch-action:none` only while active, Esc cancel, window-blur
 * cancel, teardown, and the two knobs drag-to-hire doesn't use (ghost
 * rotation/offset, focus-return). `drag-to-hire.mjs` and `boot-smoke.mjs`
 * are the regression guard that floor.js's own behaviour didn't change.
 *
 * Same hermetic shape as desk-v1-kit.mjs (real index.html + real static/js,
 * no network) and drag-to-hire.mjs's CDP touch-emulation technique for the
 * touch cases (real gesture pipeline, not synthetic PointerEvents).
 *
 * RUN
 *   cd tools/smoke && node pointer-drag.mjs
 * Exit 0 = every case holds; 1 = a case regressed / harness error.
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

const PID = 'smoke_pointerdrag';
const PROJECTS = [{
  id: PID, name: 'Pointer-drag smoke', status: 'active', domain: 'general', emoji: '🧪',
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
  if (path === '/api/config') return J({});
  if (path === '/api/characters') return J([]);
  return route.abort();
}

// Injects a minimal fixture: one draggable handle, two drop zones (one that
// will classify as "ok", one "refused"). Wires `window.PointerDrag.begin` on
// the handle's pointerdown through a small harness the tests below drive via
// real mouse/touch input — never by calling internal callbacks directly.
async function installFixture(page, opts) {
  await page.evaluate((o) => {
    const style = document.createElement('style');
    // Mirrors app.css's real rule shape (`.fl-fig.fl-draggable.fl-hire-dragging`
    // sets touch-action:none only once dragging) — the class toggle alone does
    // nothing without a stylesheet rule behind it.
    // Inline `style` attributes always beat a stylesheet rule regardless of
    // selector specificity, so the baseline touch-action has to live in this
    // stylesheet too, or the override below can never win.
    style.textContent = '#pd-handle { touch-action: pan-y; } '
      + '#pd-handle.pd-fixture-dragging { touch-action: none; }';
    document.head.appendChild(style);
    const wrap = document.createElement('div');
    wrap.id = 'pd-fixture';
    wrap.innerHTML = `
      <div id="pd-handle" style="position:fixed;left:40px;top:40px;width:60px;height:60px;background:#444;" tabindex="0"></div>
      <div id="pd-zone-ok" data-zone="ok" style="position:fixed;left:300px;top:40px;width:120px;height:120px;background:#262;"></div>
      <div id="pd-zone-bad" data-zone="bad" style="position:fixed;left:300px;top:220px;width:120px;height:120px;background:#622;"></div>
      <button id="pd-decoy" style="position:fixed;left:40px;top:220px;width:60px;height:30px;">decoy</button>
    `;
    document.body.appendChild(wrap);
    window.__pd = { activated: 0, dropped: null, teardownCount: 0, ended: [], hoverLog: [] };
    const handle = document.getElementById('pd-handle');
    handle.addEventListener('pointerdown', (e) => {
      window.PointerDrag.begin(handle, e, {
        isDragActive: () => !!window.__pdState,
        getDragState: () => window.__pdState,
        setDragState: (s) => { window.__pdState = s; },
        draggingClass: 'pd-fixture-dragging',
        activeBodyClass: 'pd-fixture-active',
        ghostClass: 'pd-ghost',
        ghostHTML: () => '<span>G</span>',
        ghostRotationDeg: o.rotate ? -3 : 0,
        ghostOffsetX: o.offset ? 12 : 0,
        ghostOffsetY: o.offset ? -12 : 0,
        returnFocus: !!o.returnFocus,
        onActivate: () => {
          window.__pd.activated++;
          // Simulates a mid-drag re-render stealing focus elsewhere (the same
          // class of event returnFocus exists for) so the drop/teardown test
          // below actually distinguishes returnFocus:true from :false, instead
          // of both reading "focused" because a real mousedown natively
          // focuses a tabindex'd element regardless of this module.
          if (o.stealFocusOnActivate) document.getElementById('pd-decoy').focus();
        },
        onMove: (st, x, y) => {
          const el = document.elementFromPoint(x, y);
          const zone = el && el.closest && el.closest('[data-zone]');
          window.__pd.hoverLog.push(zone ? zone.dataset.zone : null);
        },
        onDrop: (st, x, y) => {
          const el = document.elementFromPoint(x, y);
          const zone = el && el.closest && el.closest('[data-zone]');
          // "bad" is a REFUSED target, same as drag-to-hire's off-scope tile —
          // resolves to null, not the zone id.
          return zone && zone.dataset.zone === 'ok' ? 'ok' : null;
        },
        afterDrop: (st, result) => { window.__pd.dropped = result; },
        onTeardown: () => { window.__pd.teardownCount++; },
        onEnd: (st, wasDrag) => { window.__pd.ended.push(wasDrag); },
      });
    });
  }, opts || {});
}

async function main() {
  const browser = await chromium.launch();

  // ── Desktop: mouse activation, ghost, drop, teardown, rotation/offset ────
  {
    const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message || String(e)));
    await page.route('**/*', fulfillOrAbort);
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card', { timeout: 15000 });
    await installFixture(page, { rotate: true, offset: true, returnFocus: false, stealFocusOnActivate: true });

    const handle = await page.$('#pd-handle');
    const box = await handle.boundingBox();
    const cx = box.x + box.width / 2, cy = box.y + box.height / 2;

    await page.mouse.move(cx, cy);
    await page.mouse.down();
    // Under the 8px slop: must NOT activate yet.
    await page.mouse.move(cx + 3, cy + 2);
    const activatedEarly = await page.evaluate(() => window.__pd.activated);
    activatedEarly === 0
      ? ok('mouse: 3px of travel does not cross the 8px slop — no activation yet')
      : fail(`mouse: activated too early at 3px travel (activated=${activatedEarly})`);

    await page.mouse.move(cx + 20, cy + 2, { steps: 4 });
    await page.waitForSelector('body.pd-fixture-active', { timeout: 2000 })
      .then(() => ok('mouse: crossing the slop engages body.pd-fixture-active'),
        () => fail('mouse: body.pd-fixture-active never appeared past the slop'));

    const ghostVisible = await page.$eval('.pd-ghost', (el) => !!el).catch(() => false);
    ghostVisible ? ok('a ghost node is mounted while active') : fail('.pd-ghost did not appear');

    const ghostRotate = await page.$eval('.pd-ghost', (el) => el.style.getPropertyValue('--pd-rotate'));
    ghostRotate.trim() === '-3deg'
      ? ok('ghostRotationDeg reaches the ghost as the --pd-rotate CSS var')
      : fail(`expected --pd-rotate: -3deg, got "${ghostRotate}"`);

    // Move onto the "ok" zone and check ghost position honors the offset
    // (cursor position + configured offset, not the raw cursor position).
    const okZone = await page.$('#pd-zone-ok');
    const zoneBox = await okZone.boundingBox();
    const tx = zoneBox.x + zoneBox.width / 2, ty = zoneBox.y + zoneBox.height / 2;
    await page.mouse.move(tx, ty, { steps: 6 });
    const ghostPos = await page.$eval('.pd-ghost', (el) => ({ left: parseFloat(el.style.left), top: parseFloat(el.style.top) }));
    (Math.abs(ghostPos.left - (tx + 12)) < 1 && Math.abs(ghostPos.top - (ty - 12)) < 1)
      ? ok('ghostOffsetX/Y is applied on top of the raw pointer position')
      : fail(`expected ghost at (${tx + 12}, ${ty - 12}), got (${ghostPos.left}, ${ghostPos.top})`);

    const hoverZones = await page.evaluate(() => window.__pd.hoverLog.slice(-1)[0]);
    hoverZones === 'ok' ? ok('onMove hit-tests the real element under the (raw) pointer') : fail(`expected hover on "ok", got ${hoverZones}`);

    const draggingTouchAction = await page.$eval('#pd-handle', (el) => getComputedStyle(el).touchAction);
    draggingTouchAction === 'none'
      ? ok('draggingClass flips the handle to touch-action:none once active')
      : fail(`expected touch-action:none on the active handle, got ${draggingTouchAction}`);

    await page.mouse.up();
    const dropResult = await page.evaluate(() => window.__pd.dropped);
    dropResult === 'ok' ? ok('onDrop resolves to the zone under release, afterDrop receives it') : fail(`expected drop result "ok", got ${dropResult}`);

    const cleared = await page.evaluate(() => ({
      bodyClass: document.body.classList.contains('pd-fixture-active'),
      handleClass: document.getElementById('pd-handle').classList.contains('pd-fixture-dragging'),
      ghosts: document.querySelectorAll('.pd-ghost').length,
      state: window.__pdState,
    }));
    (!cleared.bodyClass && !cleared.handleClass && cleared.ghosts === 0 && cleared.state === null)
      ? ok('teardown on drop clears body/handle classes, the ghost, and the drag-state slot')
      : fail(`teardown incomplete after drop: ${JSON.stringify(cleared)}`);

    const teardownCount = await page.evaluate(() => window.__pd.teardownCount);
    teardownCount === 1 ? ok('onTeardown fires exactly once for the drag') : fail(`onTeardown fired ${teardownCount} times`);

    // onActivate stole focus to the decoy button, simulating a mid-drag
    // re-render — returnFocus:false must leave it there, not reclaim it.
    const focusedAfterDrop = await page.evaluate(() => document.activeElement === document.getElementById('pd-decoy'));
    focusedAfterDrop
      ? ok('returnFocus:false leaves focus on whatever holds it after a drop (no behaviour drag-to-hire never asked for)')
      : fail('returnFocus:false should not have reclaimed focus onto the handle');

    errors.length === 0 ? ok('no uncaught exceptions during the desktop pass') : fail('uncaught: ' + errors.join(' | '));
    await ctx.close();
  }

  // ── Desktop: refusal, Esc cancel, window-blur cancel, focus-return opt-in ─
  {
    const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
    const page = await ctx.newPage();
    await page.route('**/*', fulfillOrAbort);
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card', { timeout: 15000 });
    await installFixture(page, { returnFocus: true, stealFocusOnActivate: true });

    // Drop on the "bad" zone: onDrop returns null there, so afterDrop must
    // see null even though a drop DID occur (teardown ran with wasDrag=true).
    let handle = await page.$('#pd-handle');
    let box = await handle.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 20, box.y + box.height / 2, { steps: 3 });
    const badZone = await page.$('#pd-zone-bad');
    const badBox = await badZone.boundingBox();
    await page.mouse.move(badBox.x + badBox.width / 2, badBox.y + badBox.height / 2, { steps: 6 });
    await page.mouse.up();
    const badDrop = await page.evaluate(() => window.__pd.dropped);
    badDrop === null ? ok('dropping on the refused zone resolves to null, same code path as a real drop') : fail(`expected null on refusal, got ${badDrop}`);

    const focusedAfterRealDrop = await page.evaluate(() => document.activeElement === document.getElementById('pd-handle'));
    focusedAfterRealDrop
      ? ok('returnFocus:true returns focus to the handle after a completed drag')
      : fail('returnFocus:true should have returned focus to the handle');

    // ── Esc mid-drag: writes nothing, tears down cleanly, a fresh drag still works.
    handle = await page.$('#pd-handle');
    box = await handle.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 20, box.y + box.height / 2, { steps: 3 });
    await page.waitForSelector('body.pd-fixture-active', { timeout: 2000 });
    await page.keyboard.press('Escape');
    await page.mouse.up();   // the drag is already torn down; this must be inert
    const afterEsc = await page.evaluate(() => ({
      active: document.body.classList.contains('pd-fixture-active'),
      ghosts: document.querySelectorAll('.pd-ghost').length,
      dropped: window.__pd.dropped,
    }));
    (!afterEsc.active && afterEsc.ghosts === 0)
      ? ok('Escape mid-drag tears down cleanly (no ghost, no active class)')
      : fail(`Escape mid-drag left state behind: ${JSON.stringify(afterEsc)}`);

    handle = await page.$('#pd-handle');
    box = await handle.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 20, box.y + box.height / 2, { steps: 3 });
    const revived = await page.waitForSelector('body.pd-fixture-active', { timeout: 2000 }).then(() => true, () => false);
    revived ? ok('a fresh drag still starts after an Esc cancel') : fail('a fresh drag did not start after an Esc cancel');
    await page.mouse.up();

    await ctx.close();
  }

  // ── Mobile: long-press activation, slop-before-long-press cancels, native
  // scroll survives a quick swipe (real CDP touch events, drag-to-hire.mjs's
  // own technique — genuine gesture recognition, not synthetic PointerEvents).
  {
    const mctx = await browser.newContext({ viewport: { width: 390, height: 700 }, hasTouch: true, isMobile: true });
    const mpage = await mctx.newPage();
    const mErrors = [];
    mpage.on('pageerror', (e) => mErrors.push(e.message || String(e)));
    await mpage.route('**/*', fulfillOrAbort);
    await mpage.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    // At this viewport #projects-col renders `.mc-chat-row` rows, not `.card`
    // tiles (mobile.js renderMobileChatList) — same "boot finished" signal,
    // different markup for the width.
    await mpage.waitForSelector('#projects-col .mc-chat-row', { timeout: 15000 });
    await installFixture(mpage, {});

    const cdp = await mctx.newCDPSession(mpage);
    const touchPoint = (x, y) => ({ x, y, radiusX: 8, radiusY: 8, force: 1 });
    const dispatchTouch = (type, x, y) => cdp.send('Input.dispatchTouchEvent', {
      type, touchPoints: type === 'touchEnd' ? [] : [touchPoint(x, y)],
    });

    const handleBox = await mpage.$eval('#pd-handle', (el) => {
      const r = el.getBoundingClientRect();
      return { x: r.x, y: r.y, width: r.width, height: r.height };
    });
    const hx = handleBox.x + handleBox.width / 2, hy = handleBox.y + handleBox.height / 2;

    // Case 1: real movement before the long-press timer fires cancels the
    // gesture rather than activating (no accidental entry) — mirrors
    // drag-to-hire.mjs's swipe-vs-long-press split.
    await dispatchTouch('touchStart', hx, hy);
    for (let i = 1; i <= 4; i++) {
      await dispatchTouch('touchMove', hx, hy + i * 20);
      await mpage.waitForTimeout(15);
    }
    await dispatchTouch('touchEnd', hx, hy + 100);
    await mpage.waitForTimeout(50);
    const activatedDuringSwipe = await mpage.evaluate(() => window.__pd.activated);
    activatedDuringSwipe === 0
      ? ok('touch: real movement before the long-press timer cancels rather than activates')
      : fail(`touch: swipe should not have activated the drag (activated=${activatedDuringSwipe})`);

    // Case 2: a genuine long-press (>400ms, no movement) activates, and only
    // then does the handle read touch-action:none.
    await mpage.waitForTimeout(100);
    await dispatchTouch('touchStart', hx, hy);
    await mpage.waitForTimeout(500);   // past the 400ms default long-press
    const activatedByLongPress = await mpage.evaluate(() => window.__pd.activated);
    const touchActionWhileActive = await mpage.$eval('#pd-handle', (el) => getComputedStyle(el).touchAction);
    activatedByLongPress > 0
      ? ok('touch: a 500ms hold with no movement activates via long-press')
      : fail('touch: a long-press past 400ms should have activated');
    touchActionWhileActive === 'none'
      ? ok('touch: the handle reads touch-action:none once the long-press activates')
      : fail(`touch: expected touch-action:none once active, got ${touchActionWhileActive}`);
    await dispatchTouch('touchEnd', hx, hy);
    await mpage.waitForTimeout(50);

    mErrors.length === 0 ? ok('no uncaught exceptions during the touch pass') : fail('uncaught: ' + mErrors.join(' | '));
    await mctx.close();
  }

  await browser.close();

  if (bad > 0) {
    console.error(`\n❌ FAIL — ${bad} check(s) failed.`);
    process.exit(1);
  }
  console.log('\n✅ PASS — pointer-drag: activation threshold, touch long-press, ghost rotation/offset, '
    + 'drop resolution, Esc/blur teardown, touch-action gating and focus-return all hold.');
}

main().catch((e) => { console.error('HARNESS ERROR:', e); process.exit(1); });
