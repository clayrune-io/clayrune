// Regression guard for two Android bugs in the browser pane (Ron, 2026-10-01):
//
//  1. TYPING: on a soft keyboard (Gboard) letters never reached the page; only
//     tapping an autocomplete suggestion inserted text. Gboard reports keydown
//     keyCode 229 / key 'Unidentified' and moves text through composition or
//     beforeinput/input, so the pane now mirrors the hidden input's VALUE
//     (as a diff) instead of forwarding keydowns.
//  2. KEYBOARD RESIZE / LONG-PRESS PASTE: the keyboard opening shrank the pane
//     (mobile.js --mc-app-vh), rescaled the frame and moved the long-press
//     target out from under the finger. The pane is now frozen while focus is
//     inside it, finger-down no longer opens the keyboard, and a long-press
//     opens the pane's own Paste/Copy menu (+ a paste sheet when the clipboard
//     API is unavailable).
//
// Hermetic, like browser-pane-ime-shortcuts.mjs: the REAL static/js/browser-pane.js
// against a stubbed API in real Chromium with an Android UA, touch and the
// soft-keyboard sequences driven through CDP (Input.imeSetComposition /
// insertText are real composition + beforeinput + input events). Set
// SMOKE_PANE_JS to run it against another copy of the file (e.g. master's, to
// prove it goes red without the fix).
import { chromium } from 'playwright';
import { readFileSync } from 'fs';

const JS = readFileSync(process.env.SMOKE_PANE_JS
  || new URL('../../static/js/browser-pane.js', import.meta.url), 'utf8');
const ANDROID_UA = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36';

const browser = await chromium.launch();
const fails = [];
const fail = m => { fails.push(m); console.log(`❌ FAIL — ${m}`); };
const ok = m => console.log(`✅ ${m}`);

async function newPage({ android = true, firstPostDelayMs = 0 } = {}) {
  const posts = [];
  const context = await browser.newContext(android
    ? { userAgent: ANDROID_UA, viewport: { width: 412, height: 915 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2.6 }
    : { viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  let n = 0;
  await page.route('**/*', async route => {
    const url = route.request().url();
    if (url.endsWith('/browser-pane.js'))
      return route.fulfill({ contentType: 'application/javascript', body: JS });
    if (url.includes('/api/browser/launch'))
      return route.fulfill({ status: 201, contentType: 'application/json',
        body: JSON.stringify({ session_id: 'sid-1', url: 'about:blank',
                               profile: 'main', view: { w: 412, h: 700 } }) });
    if (url.includes('/api/browser/input')) {
      const body = JSON.parse(route.request().postData() || '{}');
      posts.push(body);
      if (body.type === 'edit' && n++ === 0 && firstPostDelayMs) await new Promise(r => setTimeout(r, firstPostDelayMs));
      return route.fulfill({ contentType: 'application/json', body: '{"ok":true}' });
    }
    if (url.includes('/browser/status'))
      return route.fulfill({ contentType: 'application/json', body: '{"sessions":[]}' });
    if (url.includes('/api/browser/profiles'))
      return route.fulfill({ contentType: 'application/json', body: '{"profiles":[]}' });
    if (url.includes('/api/browser/stream'))
      return route.fulfill({ contentType: 'text/event-stream', body: ': ping\n\n' });
    return route.fulfill({ contentType: 'text/html', body:
      `<head><meta name="viewport" content="width=device-width,initial-scale=1"></head>
       <body style="margin:0"><div id="modal-layer"></div><div id="minimized-tray"></div><div id="toast-container"></div>
       <script>
         window.nextModalZ=100; window.showToast=m=>window.__toast=m;
         // Stand-in for mobile.js: it shrinks --mc-app-vh by the soft keyboard's
         // height whenever a text field is focused. Reproduced here (not loaded)
         // because the pane's reaction to that variable is what is under test.
         document.documentElement.style.setProperty('--mc-app-vh','915px');
         addEventListener('focusin', e => { if (/INPUT|TEXTAREA/.test(e.target.tagName)) document.documentElement.style.setProperty('--mc-app-vh','560px'); });
         addEventListener('focusout', () => setTimeout(() => {
           if (!/INPUT|TEXTAREA/.test((document.activeElement||{}).tagName||'')) document.documentElement.style.setProperty('--mc-app-vh','915px'); }, 0));
       </script>
       <script type="module" src="/static/js/browser-pane.js"></script></body>` });
  });
  await page.goto('http://localhost:9/');
  await page.waitForFunction(() => typeof window.openBrowserPane === 'function');
  await page.evaluate(() => window.openBrowserPane('about:blank', 'p1'));
  await page.locator('#mc-browser-pane [data-bp="ime-shadow"]').waitFor({ state: 'attached' });
  await page.waitForTimeout(400);                   // the pane's own 100ms focus + viewport debounce
  const cdp = await context.newCDPSession(page);
  return { page, posts, cdp, context };
}

// What the remote page's focused field would contain after these POSTs.
function remoteText(posts) {
  let s = [];
  for (const p of posts) {
    if (p.type === 'edit') { s = s.slice(0, Math.max(0, s.length - (p.delete || 0))); s.push(...Array.from(p.text || '')); }
    else if (p.type === 'text') s.push(...Array.from(p.text));
    else if (p.type === 'key' && p.key === 'Backspace') s.pop();
    else if (p.type === 'key' && p.key === 'Enter') s.push('\n');
    else if (p.type === 'ime') s.push('<IME:' + p.phase + '>');
  }
  return s.join('');
}
const shadow = page => page.locator('#mc-browser-pane [data-bp="ime-shadow"]');
const settle = page => page.waitForTimeout(150);

// Gboard hands each keystroke over as keydown 229/'Unidentified'. This listener
// is registered AFTER the pane's own, so it sees whether the pane cancelled it.
async function watchKeydowns(page) {
  await page.evaluate(() => {
    window.__kd = [];
    document.querySelector('#mc-browser-pane [data-bp="ime-shadow"]')
      .addEventListener('keydown', e => window.__kd.push({ key: e.key, kc: e.keyCode, prevented: e.defaultPrevented }));
  });
}
const gboardKeydown = page => page.evaluate(() =>
  document.querySelector('#mc-browser-pane [data-bp="ime-shadow"]').dispatchEvent(
    new KeyboardEvent('keydown', { key: 'Unidentified', keyCode: 229, bubbles: true, cancelable: true })));
const compose = (cdp, text) => cdp.send('Input.imeSetComposition', { text, selectionStart: text.length, selectionEnd: text.length });
const commit = (cdp, text) => cdp.send('Input.insertText', { text });
const gboardDelete = page => page.evaluate(() => {
  const el = document.querySelector('#mc-browser-pane [data-bp="ime-shadow"]');
  el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Unidentified', keyCode: 229, bubbles: true, cancelable: true }));
  document.execCommand('delete');
});

// ── 1. typing ───────────────────────────────────────────────────────────────
async function testTyping() {
  const { page, posts, cdp, context } = await newPage();
  await shadow(page).focus();
  await watchKeydowns(page);

  // An email typed with no space: Gboard keeps the whole word composing, so
  // nothing ever commits. It must still reach the page, letter by letter.
  const email = 'ron@x.com';
  for (let i = 1; i <= email.length; i++) { await gboardKeydown(page); await compose(cdp, email.slice(0, i)); }
  await settle(page);
  const got = remoteText(posts);
  if (got !== email) fail(`typing: composing "${email}" char by char reached the page as ${JSON.stringify(got)} (posts ${JSON.stringify(posts)})`);
  else ok(`typing: "${email}" typed with no space (still composing) reaches the page exactly`);

  const kd = await page.evaluate(() => window.__kd);
  if (!kd.length || kd.some(k => k.prevented)) fail(`typing: keydown 229 must be left alone, but the pane prevented ${JSON.stringify(kd.filter(k => k.prevented))}`);
  else ok('typing: keydown keyCode 229 / "Unidentified" is not preventDefault-ed');
  if (posts.some(p => p.type === 'ime' || p.type === 'text')) fail(`typing: touch typing must not also forward ime/text posts (double-send): ${JSON.stringify(posts)}`);

  // The user keeps typing past the word and Gboard commits it (compositionend).
  await commit(cdp, email);                                    // commit == same text
  await settle(page);
  if (remoteText(posts) !== email) fail(`typing: committing the composition doubled/changed it → ${JSON.stringify(remoteText(posts))}`);
  else ok('typing: compositionend commit does not double the text');

  // Backspace, Gboard style (keydown 229 + deleteContentBackward on the field).
  await gboardDelete(page); await settle(page);
  if (remoteText(posts) !== 'ron@x.co') fail(`typing: Backspace must delete exactly one char, page has ${JSON.stringify(remoteText(posts))}`);
  else ok('typing: Backspace deletes one char');

  // A real Backspace key (keydown 8) while the field holds text: one delete, not two.
  await page.keyboard.press('Backspace'); await settle(page);
  if (remoteText(posts) !== 'ron@x.c') fail(`typing: real Backspace keydown with text in the field deleted wrong → ${JSON.stringify(remoteText(posts))}`);
  else ok('typing: a real Backspace keydown deletes once (no double with the input event)');
  await context.close();
}

async function testSuggestionAndAutocorrect() {
  const { page, posts, cdp, context } = await newPage();
  await shadow(page).focus();
  // Tapping a suggestion replaces the composing "ro" with "ron " — must not double.
  await gboardKeydown(page); await compose(cdp, 'r');
  await gboardKeydown(page); await compose(cdp, 'ro');
  await commit(cdp, 'ron ');
  await settle(page);
  if (remoteText(posts) !== 'ron ') fail(`suggestion: "ro" + pick "ron " must be exactly "ron ", page has ${JSON.stringify(remoteText(posts))}`);
  else ok('suggestion: picking an autocomplete suggestion commits once, no doubling');

  // Autocorrect: composing "teh" is replaced by "the".
  await gboardKeydown(page); await compose(cdp, 'teh');
  await compose(cdp, 'the');
  await settle(page);
  if (remoteText(posts) !== 'ron the') fail(`autocorrect: "teh"→"the" should leave "ron the", page has ${JSON.stringify(remoteText(posts))}`);
  else ok('autocorrect: a replaced composing word is Backspaced and retyped');
  await context.close();
}

async function testEmojiAndEmptyBackspaceAndEnter() {
  const { page, posts, cdp, context } = await newPage();
  await shadow(page).focus();
  await commit(cdp, 'a😀'); await settle(page);
  await gboardDelete(page); await settle(page);
  if (remoteText(posts) !== 'a') fail(`emoji: one Backspace must remove the whole emoji, page has ${JSON.stringify(remoteText(posts))}`);
  else ok('emoji: an astral char is one Backspace, not two');

  // Empty field: Gboard's Backspace changes no value → beforeinput only.
  posts.length = 0;
  await page.evaluate(() => { const el = document.querySelector('#mc-browser-pane [data-bp="ime-shadow"]'); el.value = ''; });
  await page.evaluate(() => document.querySelector('#mc-browser-pane [data-bp="ime-shadow"]')
    .dispatchEvent(new InputEvent('beforeinput', { inputType: 'deleteContentBackward', bubbles: true, cancelable: true })));
  await settle(page);
  if (!posts.some(p => p.type === 'key' && p.key === 'Backspace')) fail(`empty Backspace: expected a Backspace key POST, got ${JSON.stringify(posts)}`);
  else ok('Backspace on an empty field still reaches the page (as a key)');

  posts.length = 0;
  await page.keyboard.press('Enter'); await settle(page);
  if (!posts.some(p => p.type === 'key' && p.key === 'Enter')) fail(`Enter must forward as a key, got ${JSON.stringify(posts)}`);
  else ok('Enter forwards as a key');
  await context.close();
}

async function testOrdering() {
  // First POST is slow. Without ordering the second overtakes it ("rno").
  const { page, posts, cdp, context } = await newPage({ firstPostDelayMs: 250 });
  await shadow(page).focus();
  await commit(cdp, 'r'); await commit(cdp, 'o'); await commit(cdp, 'n');
  await page.waitForTimeout(100);
  const early = posts.filter(p => p.type === 'edit').length;
  await page.waitForTimeout(900);
  if (remoteText(posts) !== 'ron') fail(`ordering: slow first request let later ones overtake it → ${JSON.stringify(remoteText(posts))}`);
  else if (early !== 1) fail(`ordering: expected later keystrokes to wait for the slow one (1 in flight), saw ${early}`);
  else ok('ordering: keystrokes are POSTed one at a time, in order, even when one is slow');
  await context.close();
}

async function testDesktopUnchanged() {
  const { page, posts, context } = await newPage({ android: false });
  await shadow(page).focus();
  await page.keyboard.type('hi'); await settle(page);
  if (remoteText(posts) !== 'hi' || posts.some(p => p.type === 'edit')) fail(`desktop: plain keys must still forward as text, got ${JSON.stringify(posts)}`);
  else ok('desktop: keydown-per-char forwarding is unchanged');
  posts.length = 0;
  await page.evaluate(() => {
    const el = document.querySelector('#mc-browser-pane [data-bp="ime-shadow"]');
    el.dispatchEvent(new CompositionEvent('compositionupdate', { data: 'n' }));
    el.dispatchEvent(new CompositionEvent('compositionend', { data: '你好' }));
  });
  await settle(page);
  if (!posts.some(p => p.type === 'ime' && p.phase === 'update') || !posts.some(p => p.type === 'ime' && p.phase === 'end' && p.text === '你好'))
    fail(`desktop: CJK composition must still forward as {type:ime}, got ${JSON.stringify(posts)}`);
  else ok('desktop: CJK IME composition still forwards as ime update/end');
  await context.close();
}

// ── 2. keyboard resize + long-press paste ───────────────────────────────────
const paneBox = page => page.evaluate(() => {
  const w = document.querySelector('#mc-browser-pane'), i = w.querySelector('[data-bp="screen"]');
  const r = x => { const b = x.getBoundingClientRect(); return [b.left, b.top, b.width, b.height].map(Math.round).join(','); };
  return { pane: r(w), img: r(i) };
});
const touchAt = async (cdp, type, x, y) => cdp.send('Input.dispatchTouchEvent',
  { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y }] });

async function testKeyboardDoesNotResizeFrame() {
  const { page, posts, cdp, context } = await newPage();
  await page.evaluate(() => document.activeElement && document.activeElement.blur());
  await page.waitForTimeout(300);
  const before = await paneBox(page);
  const viewportPosts = () => posts.filter(p => p.type === 'viewport').length;
  const vp0 = viewportPosts();

  // Keyboard opens: a text field takes focus → the app shrinks --mc-app-vh.
  await shadow(page).focus();
  await page.waitForTimeout(500);
  const after = await paneBox(page);
  if (JSON.stringify(before) !== JSON.stringify(after))
    fail(`keyboard: the pane/frame moved when the keyboard opened: ${JSON.stringify(before)} → ${JSON.stringify(after)}`);
  else ok(`keyboard: opening the keyboard leaves the pane and frame where they were (${after.img})`);
  if (viewportPosts() !== vp0) fail('keyboard: opening the keyboard re-fit the remote page (a viewport POST was sent) — it would rescale');
  else ok('keyboard: no viewport re-fit is sent when the keyboard opens');

  // Keyboard closes and focus leaves → the pane tracks the app height again.
  await page.evaluate(() => document.activeElement.blur());
  await page.waitForTimeout(300);
  const h = await page.evaluate(() => document.querySelector('#mc-browser-pane').style.height);
  if (!/--mc-app-vh/.test(h)) fail(`keyboard: pane never thawed after focus left it (height=${h})`);
  else ok('keyboard: pane returns to tracking the app height once focus leaves it');
  await context.close();
}

async function testTapAndLongPress() {
  const { page, posts, cdp, context } = await newPage();
  await page.evaluate(() => document.activeElement && document.activeElement.blur());
  const r = await page.evaluate(() => { const b = document.querySelector('#mc-browser-pane [data-bp="screen"]').getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2 }; });
  const mouse = () => posts.filter(p => p.type === 'mouse');

  // A tap: exactly one press + one release (no synthesized-mouse double click), keyboard focus.
  await page.touchscreen.tap(r.x, r.y);
  await page.waitForTimeout(300);
  const m = mouse();
  if (m.filter(p => p.action === 'mousePressed').length !== 1) fail(`tap: expected ONE mousePressed, got ${m.filter(p => p.action === 'mousePressed').length} (${JSON.stringify(m)})`);
  else ok('tap: one click reaches the page (no double press from the synthesized mouse events)');
  if (!(await page.evaluate(() => document.activeElement && document.activeElement.dataset.bp === 'ime-shadow'))) fail('tap: the typing input did not take focus');
  else ok('tap: focus moves to the typing input (keyboard opens)');

  // Long-press: finger down must NOT focus anything (that raised the keyboard and shifted the target).
  await page.evaluate(() => document.activeElement.blur());
  posts.length = 0;
  const before = await paneBox(page);
  await touchAt(cdp, 'touchStart', r.x, r.y);
  await page.waitForTimeout(150);
  const focusedDown = await page.evaluate(() => (document.activeElement || {}).tagName);
  if (focusedDown === 'INPUT' || focusedDown === 'TEXTAREA') fail(`long-press: finger-down focused a text field (${focusedDown}) — the keyboard would open mid-press`);
  else ok('long-press: finger-down does not open the keyboard');
  await page.waitForTimeout(650);
  const menuShown = await page.locator('#mc-browser-pane [data-bp="lp-menu"]').isVisible();
  if (!menuShown) fail('long-press: held 800ms but no Paste/Copy menu appeared');
  else ok('long-press: Paste/Copy menu appears at the finger');
  await touchAt(cdp, 'touchEnd');
  await page.waitForTimeout(250);
  const after = await paneBox(page);
  if (JSON.stringify(before) !== JSON.stringify(after)) fail(`long-press: the screen moved during the press: ${JSON.stringify(before)} → ${JSON.stringify(after)}`);
  else ok('long-press: nothing moved under the finger');
  if (mouse().filter(p => p.action === 'mousePressed').length !== 1) fail(`long-press: should click once to focus the field, got ${JSON.stringify(mouse())}`);
  if (mouse().some(p => p.button === 'right')) fail('long-press: must not be forwarded as a right-click');
  if (!(await page.locator('#mc-browser-pane [data-bp="lp-menu"]').isVisible())) fail('long-press: menu vanished on finger-up');

  // Paste → clipboard API available (secure context): text goes to the page.
  await page.evaluate(() => { Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { readText: async () => 'hunter2' } }); });
  posts.length = 0;
  await page.locator('#mc-browser-pane [data-bp="lp-paste"]').tap();
  await settle(page);
  if (remoteText(posts) !== 'hunter2') fail(`long-press paste: expected "hunter2" to reach the page, got ${JSON.stringify(posts)}`);
  else ok('long-press → Paste: clipboard text reaches the page');

  // Paste → clipboard API unavailable (plain http / denied): the paste sheet opens.
  await page.evaluate(() => { Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { readText: async () => { throw new Error('denied'); } } }); });
  await touchAt(cdp, 'touchStart', r.x, r.y); await page.waitForTimeout(800); await touchAt(cdp, 'touchEnd');
  posts.length = 0;
  await page.locator('#mc-browser-pane [data-bp="lp-paste"]').tap();
  await page.waitForTimeout(200);
  const sheet = page.locator('#mc-browser-pane [data-bp="paste-sheet"]');
  if (!(await sheet.isVisible())) fail('paste fallback: no paste sheet when the clipboard API is unavailable');
  else {
    const box = await sheet.boundingBox();
    if (box.y > 120) fail(`paste fallback: sheet is at y=${box.y}, below the top (the keyboard would cover it)`);
    await page.locator('#mc-browser-pane [data-bp="paste-text"]').fill('pasted by hand');
    await page.locator('#mc-browser-pane [data-bp="paste-send"]').tap();
    await settle(page);
    if (remoteText(posts) !== 'pasted by hand') fail(`paste fallback: Send should type the text into the page, got ${JSON.stringify(posts)}`);
    else ok('paste fallback: a long-pressable text box + Send types into the page when readText() is unavailable');
  }
  await context.close();
}

async function testMenuPaste() {
  const { page, posts, context } = await newPage();
  await page.locator('#mc-browser-pane [data-bp="menu"]').tap();
  const item = page.locator('#mc-browser-pane [data-bp="mm-paste"]');
  if (!(await item.isVisible())) { fail('menu: "Paste clipboard" is not visible after opening the ⋮ menu'); await context.close(); return; }
  const b = await item.boundingBox();
  const vp = page.viewportSize();
  if (b.x < 0 || b.y < 0 || b.x + b.width > vp.width || b.y + b.height > vp.height) fail(`menu: Paste clipboard is clipped (${JSON.stringify(b)} in ${vp.width}x${vp.height})`);
  else ok(`menu: ⋮ → "Paste clipboard" is on screen on a phone (${Math.round(b.width)}x${Math.round(b.height)})`);
  await page.evaluate(() => { Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { readText: async () => 'from clipboard' } }); });
  await item.tap(); await settle(page);
  if (remoteText(posts) !== 'from clipboard') fail(`menu paste: got ${JSON.stringify(posts)}`);
  else ok('menu: Paste clipboard types the clipboard into the page');

  await page.evaluate(() => { Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined }); });
  await page.locator('#mc-browser-pane [data-bp="menu"]').tap();
  await page.locator('#mc-browser-pane [data-bp="mm-paste"]').tap();
  await page.waitForTimeout(200);
  if (!(await page.locator('#mc-browser-pane [data-bp="paste-sheet"]').isVisible())) fail('menu paste: no clipboard API → expected the paste sheet (not window.prompt)');
  else ok('menu: with no clipboard API the paste sheet opens (not a window.prompt)');
  await context.close();
}

await testTyping();
await testSuggestionAndAutocorrect();
await testEmojiAndEmptyBackspaceAndEnter();
await testOrdering();
await testDesktopUnchanged();
await testKeyboardDoesNotResizeFrame();
await testTapAndLongPress();
await testMenuPaste();
await browser.close();

if (!fails.length) console.log('✅ PASS — Android soft-keyboard typing, keyboard-stable frame, long-press paste and paste fallback verified against the real static/js/browser-pane.js.');
else process.exitCode = 1;
