#!/usr/bin/env node
/**
 * RTL language support — MC-1000.
 *
 * WHY THIS EXISTS
 * ----------------
 * Backlog 34fab303: a Hebrew/Arabic reply must render right-aligned with
 * bullets/punctuation on the correct side, while an English reply — and
 * inline code/paths/URLs *inside* an RTL line — stay LTR. The fix is
 * per-block `dir="auto"` (+ `dir="ltr"` on inline code/path spans and on
 * fixed-English prefixes like "Answered: " or a backlog item's key) rather
 * than a global page direction, so this drives the REAL shipped renderer
 * functions (`formatAgentText`, `escPromptWithImages`, `renderAgentQuestion`)
 * against the real app.css cascade and asserts the browser's OWN computed
 * `direction`/`text-align`, plus bullet geometry, per block — a string diff
 * of the HTML can't catch a CSS rule that silently overrides `dir="auto"`.
 *
 * Each block is built with the exact class names / attributes the shipped
 * templates use (conversation.js / rich-text.js / cross-backlog.js), so a
 * future template change that drops `dir="auto"` fails this test without
 * needing a live agent session or a fabricated backend.
 *
 * This is a real headless boot (real index.html + real static/js/*.js +
 * static/css/*.css served verbatim from THIS checkout, no server, no
 * network) — not a hit against localhost:5199. That distinction matters:
 * :5199 serves the MAIN checkout's static files regardless of which
 * worktree/branch an agent is sitting in, so hitting it here silently tested
 * pre-fix code and passed a red diff (caught live 2026-09-29 — .hl-code
 * rendered with no `dir="ltr"` and no unicode-bidi CSS at all against the
 * live server, then correctly failed once switched to this hermetic boot).
 * Static files are read via readdirSync (conversation-persona-filter.mjs's
 * pattern), not a hand-maintained map, so a new module never silently falls
 * through to `route.abort()`.
 *
 * ROUND 2 (MC-1000): the card's radios/'Other'/actions row have no text of
 * their own to auto-detect direction from, so `renderAgentQuestion` now sets
 * a real `dir` attribute on the card (`_firstStrongDir`, rich-text.js) and
 * flexbox reverses their layout for it — tested here via radio bounding-rect
 * x-position, not just computed `direction`. The "Answered:" summary line
 * moved from a baked-in trailing space to flex `gap` + a `<bdi>`-isolated
 * answer. `escPromptWithImages` moved from ONE dir="auto" block per bubble to
 * one per LINE, so a mixed "Q: <Hebrew>?\nA: <English>" bubble can't have one
 * line's direction leak into the other. `formatAgentText` and
 * `escPromptWithImages` also now isolate Hebrew/Arabic runs EMBEDDED in an
 * English-base line (`_isolateRtlRuns`) — the mirror of round 1's LTR-
 * isolated code/path spans — so quote marks/commas at the RTL boundary don't
 * resolve against the outer LTR paragraph.
 *
 * RUN: cd tools/smoke && node rtl-support.mjs
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

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

const HE = 'שלום עולם זהו טקסט בעברית';          // "Hello world, this is Hebrew text"
const AR = 'مرحبا بالعالم هذا نص عربي';           // Arabic equivalent
const EN = 'Hello world this is English text';

let browser;
try {
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 20000 });
  if (pageErrors.length) {
    pageErrors.forEach((e) => fail('uncaught page error during boot: ' + e));
  } else {
    ok('app booted clean from this worktree\'s real static files, no uncaught exceptions');
  }

  const bridged = await page.evaluate(() => ({
    formatAgentText: typeof window.formatAgentText === 'function',
    escPromptWithImages: typeof window.escPromptWithImages === 'function',
    renderAgentQuestion: typeof window.renderAgentQuestion === 'function',
    esc: typeof window.esc === 'function',
  }));
  for (const [name, present] of Object.entries(bridged)) {
    present ? ok(`window.${name} is bridged`) : fail(`window.${name} missing — is the page serving stale JS?`);
  }
  if (Object.values(bridged).some((v) => !v)) throw new Error('required render functions not bridged');

  // ── Build every scenario's real markup + measure it in one page.evaluate ──
  const result = await page.evaluate(({ HE, AR, EN }) => {
    const root = document.createElement('div');
    root.id = 'rtl-smoke-root';
    // Fixed width so bullet-geometry thresholds below are meaningful.
    root.style.cssText = 'position:fixed;top:0;left:0;width:420px;background:#000;z-index:99999;padding:8px';
    document.body.appendChild(root);

    const dirOf = (el) => getComputedStyle(el).direction;
    const alignOf = (el) => getComputedStyle(el).textAlign;

    const out = {};

    // 1. Agent reply, Hebrew, with inline code — matches conversation.js's
    //    per-line renderer: `<div class="${agentLineCls}" dir="auto">${formatAgentText(line)}</div>`
    {
      const line = `${HE} \`getUserById\` עוד טקסט`;
      const div = document.createElement('div');
      div.className = 'agent-line';
      div.setAttribute('dir', 'auto');
      div.innerHTML = window.formatAgentText(line);
      root.appendChild(div);
      const code = div.querySelector('.hl-code');
      out.heReply = {
        direction: dirOf(div), align: alignOf(div),
        codeDirection: code ? dirOf(code) : null,
        codeText: code ? code.textContent : null,
      };
    }

    // 1b. Hebrew line where a URL is the FIRST token — "https" is a strong-LTR
    //     character, so if the anchor lacks its own dir attribute the whole
    //     line mis-detects as LTR even though the CSS forces the URL itself
    //     ltr (verified live: CSS-only does not stop dir="auto" detection).
    {
      const line = `https://example.com/docs ${HE}`;
      const div = document.createElement('div');
      div.className = 'agent-line';
      div.setAttribute('dir', 'auto');
      div.innerHTML = window.formatAgentText(line);
      root.appendChild(div);
      const url = div.querySelector('.hl-url');
      out.heReplyLeadingUrl = {
        direction: dirOf(div),
        urlDirection: url ? dirOf(url) : null,
        urlFound: !!url,
      };
    }

    // 2. Agent reply, English — must stay LTR.
    {
      const line = `${EN} \`getUserById\` more text`;
      const div = document.createElement('div');
      div.className = 'agent-line';
      div.setAttribute('dir', 'auto');
      div.innerHTML = window.formatAgentText(line);
      root.appendChild(div);
      out.enReply = { direction: dirOf(div), align: alignOf(div) };
    }

    // 3. Hebrew markdown bullet list — two lines, bullet-geometry check.
    {
      const heLine = document.createElement('div');
      heLine.className = 'agent-line';
      heLine.setAttribute('dir', 'auto');
      heLine.innerHTML = window.formatAgentText(`- ${HE}`);
      root.appendChild(heLine);
      const enLine = document.createElement('div');
      enLine.className = 'agent-line';
      enLine.setAttribute('dir', 'auto');
      enLine.innerHTML = window.formatAgentText(`- ${EN}`);
      root.appendChild(enLine);

      const heBullet = heLine.querySelector('.hl-bullet');
      const enBullet = enLine.querySelector('.hl-bullet');
      const heRect = heLine.getBoundingClientRect(), heBRect = heBullet?.getBoundingClientRect();
      const enRect = enLine.getBoundingClientRect(), enBRect = enBullet?.getBoundingClientRect();
      out.bullets = {
        heDirection: dirOf(heLine), enDirection: dirOf(enLine),
        // Distance from the bullet to ITS side of the container — small on
        // the correct side, large on the wrong side.
        heBulletDistFromRight: heBRect ? (heRect.right - heBRect.right) : null,
        enBulletDistFromLeft: enBRect ? (enBRect.left - enRect.left) : null,
      };
    }

    // 4. mc:question card — drive the REAL renderAgentQuestion() against a
    //    scratch #agent-output-<sid> container, exactly as conversation.js
    //    wires it (it looks the container up by id, not by argument).
    {
      const sid = 'rtl_smoke_sess';
      const out2 = document.createElement('div');
      out2.id = `agent-output-${sid}`;
      root.appendChild(out2);
      const questions = [{
        header: `כותרת ${HE}`,
        question: `${HE} שאלה?`,
        options: [
          { label: HE.slice(0, 6), description: `${HE} תיאור` },
          { label: 'Option B', description: 'English description' },
        ],
      }];
      window.renderAgentQuestion(sid, 'smoke_pid', questions, 'qid-smoke-1');
      const card = out2.querySelector('.agent-question');
      const header = card?.querySelector('.agent-question-header');
      const qtext = card?.querySelector('.agent-question-text');
      const optLabel = card?.querySelector('.agent-question-option label');
      const desc = card?.querySelector('.aq-desc');
      // ROUND 2: card-level dir + radio/Other flex-order.
      const cardRect = card?.getBoundingClientRect();
      const firstOption = card?.querySelector('.agent-question-option');
      const firstRadio = firstOption?.querySelector('input');
      const otherOption = card ? card.querySelectorAll('.agent-question-option')[card.querySelectorAll('.agent-question-option').length - 1] : null;
      const otherRadio = otherOption?.querySelector('input');
      out.question = {
        found: !!card,
        cardDir: card ? card.getAttribute('dir') : null,
        headerDir: header ? dirOf(header) : null,
        qtextDir: qtext ? dirOf(qtext) : null,
        optLabelDir: optLabel ? dirOf(optLabel) : null,
        descDir: desc ? dirOf(desc) : null,
        radioOnRightHalf: (cardRect && firstRadio)
          ? (firstRadio.getBoundingClientRect().left - cardRect.left) > (cardRect.width / 2)
          : null,
        otherRadioOnRightHalf: (cardRect && otherRadio)
          ? (otherRadio.getBoundingClientRect().left - cardRect.left) > (cardRect.width / 2)
          : null,
      };

      // English card, same shape — radio must stay on the LEFT half. A
      // description on the options forces full radio-form mode (matching
      // the Hebrew card above) instead of the one-tap chip layout, which
      // has no `.agent-question-option` radios at all.
      const sidEn = 'rtl_smoke_sess_en';
      const out3 = document.createElement('div');
      out3.id = `agent-output-${sidEn}`;
      root.appendChild(out3);
      window.renderAgentQuestion(sidEn, 'smoke_pid', [{
        header: 'English header', question: `${EN}?`,
        options: [{ label: 'Option A', description: 'English description' }, { label: 'Option B' }],
      }], 'qid-smoke-en-1');
      const cardEn = out3.querySelector('.agent-question');
      const cardEnRect = cardEn?.getBoundingClientRect();
      const firstRadioEn = cardEn?.querySelector('.agent-question-option input');
      out.questionEn = {
        found: !!cardEn,
        cardDir: cardEn ? cardEn.getAttribute('dir') : null,
        radioOnLeftHalf: (cardEnRect && firstRadioEn)
          ? (firstRadioEn.getBoundingClientRect().left - cardEnRect.left) < (cardEnRect.width / 2)
          : null,
      };

      // "Answered:" summary line — drive the REAL submit path
      // (submitQuestionAnswer -> _dispatchQuestionAnswer) against the
      // already-rendered Hebrew card, exactly as a user checking a radio and
      // clicking Submit would. The resulting fetch() has nothing to talk to
      // in this hermetic page but is fire-and-forget (.catch(()=>{})), so it
      // doesn't block the summary from being inserted.
      const firstOptionInput = card.querySelector('.agent-question-option input');
      if (firstOptionInput) firstOptionInput.checked = true;
      window.submitQuestionAnswer('smoke_pid', sid, card.id, questions.length);
      const answerLine = card.querySelector('.agent-question-answer');
      const prefixSpan = answerLine?.querySelector('.aq-label');
      const labelWord = prefixSpan?.querySelector('bdi');
      const bdiEl = answerLine?.querySelector(':scope > bdi');
      // Colon position: in an rtl card it must sit LEFT of "Answered"
      // (between label and answer), never on the far right edge.
      let colonLeftOfWord = null;
      if (prefixSpan && labelWord && labelWord.nextSibling) {
        const cr = document.createRange();
        cr.setStart(labelWord.nextSibling, 0); cr.setEnd(labelWord.nextSibling, 1);
        const c = cr.getClientRects()[0], w = labelWord.getBoundingClientRect();
        colonLeftOfWord = c ? (c.right <= w.left + 1) : null;
      }
      const gapPx = (prefixSpan && bdiEl)
        ? Math.abs(bdiEl.getBoundingClientRect().left - prefixSpan.getBoundingClientRect().right)
        : null;
      out.answeredLine = {
        found: !!answerLine,
        direction: answerLine ? dirOf(answerLine) : null,
        prefixDirection: labelWord ? dirOf(labelWord) : null,
        prefixText: prefixSpan ? prefixSpan.textContent : null,
        bdiText: bdiEl ? bdiEl.textContent : null,
        gapPx,
        colonLeftOfWord,
      };
    }

    // 5. Mixed user bubble — "> Ron: <Hebrew + inline code>", the exact
    //    "> Label: " shape escPromptWithImages guards against prefix-bleed.
    {
      const raw = `> Ron: ${HE} \`npm run build\``;
      const div = document.createElement('div');
      div.className = 'agent-line agent-line-prompt';
      div.setAttribute('dir', 'auto');
      div.innerHTML = window.escPromptWithImages(raw);
      root.appendChild(div);
      const lineDiv = div.querySelector('div[dir="auto"]');
      const prefixSpan = div.querySelector('span[dir="ltr"]');
      out.userBubble = {
        lineCount: div.querySelectorAll('div[dir="auto"]').length,
        direction: lineDiv ? dirOf(lineDiv) : null,
        prefixDirection: prefixSpan ? dirOf(prefixSpan) : null,
        prefixText: prefixSpan ? prefixSpan.textContent : null,
      };
      // ROUND 4 — the "> " marker must sit OUTSIDE the ltr isolate, on the
      // right edge of the rtl line (RIGHT of the name), not left of it.
      const markerNode = lineDiv && lineDiv.firstChild;
      let markerRightOfName = null;
      if (markerNode && markerNode.nodeType === 3 && prefixSpan) {
        const r = document.createRange();
        r.setStart(markerNode, 0); r.setEnd(markerNode, 1);
        const m = r.getClientRects()[0], n = prefixSpan.getBoundingClientRect();
        markerRightOfName = m ? (m.left >= n.right - 1) : null;
      }
      out.userBubble.markerText = markerNode && markerNode.nodeType === 3 ? markerNode.textContent : null;
      out.userBubble.markerRightOfName = markerRightOfName;
      // Same prefix in an English line: marker stays LEFT of the name.
      const en = document.createElement('div');
      en.className = 'agent-line agent-line-prompt';
      en.setAttribute('dir', 'auto');
      en.innerHTML = window.escPromptWithImages(`> Ron: ${EN}`);
      root.appendChild(en);
      const enLine = en.querySelector('div[dir="auto"]');
      const enName = en.querySelector('span[dir="ltr"]');
      let enMarkerLeft = null;
      if (enLine && enLine.firstChild && enLine.firstChild.nodeType === 3 && enName) {
        const r2 = document.createRange();
        r2.setStart(enLine.firstChild, 0); r2.setEnd(enLine.firstChild, 1);
        const m2 = r2.getClientRects()[0];
        enMarkerLeft = m2 ? (m2.right <= enName.getBoundingClientRect().left + 1) : null;
      }
      out.userBubble.enMarkerLeftOfName = enMarkerLeft;
    }

    // 5b. ROUND 2 — a bubble mixing a Hebrew Q line and an English A line
    //     (exactly what renderAgentQuestion's answer message sends:
    //     "Q: <question>\nA: <answer>"). Each line must compute its OWN
    //     direction; one line's language must never leak into the other's.
    {
      const raw = `Q: ${HE}?\nA: ${EN}`;
      const div = document.createElement('div');
      div.className = 'agent-line agent-line-prompt';
      div.setAttribute('dir', 'auto');
      div.innerHTML = window.escPromptWithImages(raw);
      root.appendChild(div);
      const lines = Array.from(div.querySelectorAll('div[dir="auto"]'));
      // Colon after "Q" must sit LEFT of the isolated "Q" in the rtl line.
      let qColonLeftOfLabel = null;
      const qSpan = lines[0]?.querySelector('span[dir="ltr"]');
      if (qSpan && qSpan.nextSibling && qSpan.nextSibling.nodeType === 3) {
        const cr = document.createRange();
        cr.setStart(qSpan.nextSibling, 0); cr.setEnd(qSpan.nextSibling, 1);
        const c = cr.getClientRects()[0], w = qSpan.getBoundingClientRect();
        qColonLeftOfLabel = c ? (c.right <= w.left + 1) : null;
      }
      out.mixedBubble = {
        qColonLeftOfLabel,
        lineCount: lines.length,
        qLineDir: lines[0] ? dirOf(lines[0]) : null,
        aLineDir: lines[1] ? dirOf(lines[1]) : null,
        qPrefixText: lines[0]?.querySelector('span[dir="ltr"]')?.textContent ?? null,
        aPrefixText: lines[1]?.querySelector('span[dir="ltr"]')?.textContent ?? null,
      };
    }

    // 5c. English-only bubble must render exactly as before: every line ltr,
    //     no stray isolate markup, image path handling untouched.
    {
      const raw = `${EN} line one\n${EN} line two`;
      const div = document.createElement('div');
      div.className = 'agent-line agent-line-prompt';
      div.setAttribute('dir', 'auto');
      div.innerHTML = window.escPromptWithImages(raw);
      root.appendChild(div);
      const lines = Array.from(div.querySelectorAll('div[dir="auto"]'));
      out.englishBubble = {
        lineCount: lines.length,
        allLtr: lines.every((l) => dirOf(l) === 'ltr'),
        text: lines.map((l) => l.textContent).join('|'),
      };
    }

    // 6. Chat composer textarea while typing Hebrew.
    {
      const ta = document.createElement('textarea');
      ta.setAttribute('dir', 'auto');
      ta.className = 'agent-task-input';
      root.appendChild(ta);
      ta.value = HE;
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      out.composer = { direction: dirOf(ta) };
    }

    // 7. Conversation rail title (.conv-name).
    {
      const span = document.createElement('span');
      span.className = 'conv-name';
      span.setAttribute('dir', 'auto');
      span.textContent = AR;
      root.appendChild(span);
      out.railTitle = { direction: dirOf(span) };
    }

    // 8. Backlog item text — mirrors cross-backlog.js's
    //    `<div class="backlog-text" dir="auto">${key}${text}</div>` with the
    //    key isolated in its own dir="ltr" span (the prefix-bleed fix).
    {
      const div = document.createElement('div');
      div.className = 'backlog-text';
      div.setAttribute('dir', 'auto');
      div.innerHTML = `<span class="backlog-num" dir="ltr">MC-1000</span>${window.esc(HE)}`;
      root.appendChild(div);
      const num = div.querySelector('.backlog-num');
      out.backlog = { direction: dirOf(div), numDirection: num ? dirOf(num) : null };

      const enDiv = document.createElement('div');
      enDiv.className = 'backlog-text';
      enDiv.setAttribute('dir', 'auto');
      enDiv.innerHTML = `<span class="backlog-num" dir="ltr">MC-1001</span>${window.esc(EN)}`;
      root.appendChild(enDiv);
      out.backlogEn = { direction: dirOf(enDiv) };
    }

    // 9. ROUND 2 fix 4 — an ENGLISH agent line quoting a Hebrew phrase in
    //    double quotes must isolate the Hebrew run (quotes included) as one
    //    element, and must not disturb an unrelated "Answered:" label's own
    //    internal colon adjacency.
    {
      const HE_PHRASE = 'כן, נראה טוב';
      const line = `The reply was "${HE_PHRASE}" — looks fine.`;
      const div = document.createElement('div');
      div.className = 'agent-line';
      div.setAttribute('dir', 'auto');
      div.innerHTML = window.formatAgentText(line);
      root.appendChild(div);
      const bdis = div.querySelectorAll('bdi');
      out.englishQuotingHebrew = {
        lineDirection: dirOf(div),
        bdiCount: bdis.length,
        bdiText: bdis.length ? bdis[0].textContent : null,
      };

      // Colon adjacency: "Answered:" must render with no gap/reorder between
      // "Answered" and ":" — built via the real _dispatchQuestionAnswer
      // shape (isolated label span) next to an isolated Hebrew answer, the
      // exact case the original bug photo showed.
      const answerLine2 = document.createElement('div');
      answerLine2.className = 'agent-question-answer';
      answerLine2.setAttribute('dir', 'ltr');
      answerLine2.innerHTML = `<span class="aq-label"><bdi>Answered</bdi>:</span><bdi>${window.esc(HE_PHRASE)}</bdi>`;
      root.appendChild(answerLine2);
      // English (ltr) card: the colon must sit immediately RIGHT of "Answered".
      const word = answerLine2.querySelector('.aq-label bdi');
      const r2 = document.createRange();
      r2.setStart(word.nextSibling, 0); r2.setEnd(word.nextSibling, 1); // ":"
      const rect1 = word.getBoundingClientRect();
      const rect2 = r2.getClientRects()[0];
      out.colonAdjacency = {
        labelText: answerLine2.querySelector('.aq-label').textContent,
        gapBetweenAnsweredAndColon: (rect1 && rect2) ? Math.abs(rect2.left - rect1.right) : null,
      };
    }

    root.remove();
    return out;
  }, { HE, AR, EN });

  // ── Assertions ──────────────────────────────────────────────────────────
  const notLeft = (a) => a !== 'left';
  const notRight = (a) => a !== 'right';

  // 1. Hebrew reply
  result.heReply.direction === 'rtl' ? ok('Hebrew agent reply: direction=rtl') : fail(`Hebrew agent reply direction=${result.heReply.direction}`);
  notLeft(result.heReply.align) ? ok(`Hebrew agent reply: text-align=${result.heReply.align} (right-aligned)`) : fail(`Hebrew agent reply text-align=${result.heReply.align} (should not be left)`);
  result.heReply.codeDirection === 'ltr' ? ok(`inline code stays LTR inside Hebrew line (text="${result.heReply.codeText}")`) : fail(`inline code direction=${result.heReply.codeDirection}, expected ltr`);

  const lu = result.heReplyLeadingUrl;
  lu.urlFound ? ok('leading-URL Hebrew line: URL linkified') : fail('leading-URL Hebrew line: no .hl-url anchor found');
  lu.direction === 'rtl' ? ok('Hebrew line starting with a URL still detects rtl (URL prefix does not bleed)') : fail(`Hebrew line starting with a URL detected direction=${lu.direction} — the URL prefix is bleeding into direction detection`);
  lu.urlDirection === 'ltr' ? ok('leading URL span itself stays ltr') : fail(`leading URL span direction=${lu.urlDirection}, expected ltr`);

  // 2. English reply
  result.enReply.direction === 'ltr' ? ok('English agent reply: direction=ltr (unaffected)') : fail(`English agent reply direction=${result.enReply.direction}`);
  notRight(result.enReply.align) ? ok(`English agent reply: text-align=${result.enReply.align} (left-aligned)`) : fail(`English agent reply text-align=${result.enReply.align} (should not be right)`);

  // 3. Bullets
  const b = result.bullets;
  b.heDirection === 'rtl' ? ok('Hebrew list line: direction=rtl') : fail(`Hebrew list line direction=${b.heDirection}`);
  b.enDirection === 'ltr' ? ok('English list line: direction=ltr') : fail(`English list line direction=${b.enDirection}`);
  (b.heBulletDistFromRight !== null && b.heBulletDistFromRight < 20)
    ? ok(`Hebrew bullet sits on the right edge (${b.heBulletDistFromRight.toFixed(1)}px from it)`)
    : fail(`Hebrew bullet is NOT near the right edge (${b.heBulletDistFromRight}px from it) — bullet on wrong side`);
  (b.enBulletDistFromLeft !== null && b.enBulletDistFromLeft < 20)
    ? ok(`English bullet sits on the left edge (${b.enBulletDistFromLeft.toFixed(1)}px from it)`)
    : fail(`English bullet is NOT near the left edge (${b.enBulletDistFromLeft}px from it) — bullet on wrong side`);

  // 4. Question card
  const q = result.question;
  q.found ? ok('mc:question card rendered via the real renderAgentQuestion()') : fail('renderAgentQuestion() produced no .agent-question card');
  q.cardDir === 'rtl' ? ok('question card: dir="rtl" set on the container') : fail(`question card dir="${q.cardDir}", expected rtl`);
  q.headerDir === 'rtl' ? ok('question header: direction=rtl') : fail(`question header direction=${q.headerDir}`);
  q.qtextDir === 'rtl' ? ok('question text: direction=rtl') : fail(`question text direction=${q.qtextDir}`);
  q.optLabelDir === 'rtl' ? ok('question option label: direction=rtl') : fail(`question option label direction=${q.optLabelDir}`);
  q.descDir === 'rtl' ? ok('question option description: direction=rtl') : fail(`question option description direction=${q.descDir}`);
  q.radioOnRightHalf === true ? ok('RTL card: first option\'s radio sits on the RIGHT half of the row') : fail(`RTL card radio not on right half (radioOnRightHalf=${q.radioOnRightHalf})`);
  q.otherRadioOnRightHalf === true ? ok('RTL card: "Other" row radio sits on the RIGHT half too') : fail(`RTL card "Other" radio not on right half (otherRadioOnRightHalf=${q.otherRadioOnRightHalf})`);

  const qEn = result.questionEn;
  qEn.found ? ok('English question card rendered') : fail('English renderAgentQuestion() produced no card');
  qEn.cardDir === 'ltr' ? ok('English card: dir="ltr" (unaffected)') : fail(`English card dir="${qEn.cardDir}", expected ltr`);
  qEn.radioOnLeftHalf === true ? ok('English card: radio stays on the LEFT half of the row') : fail(`English card radio not on left half (radioOnLeftHalf=${qEn.radioOnLeftHalf})`);

  const al = result.answeredLine;
  al.found ? ok('"Answered:" summary line rendered by the real submit path') : fail('"Answered:" summary line not found after submitQuestionAnswer()');
  al.direction === 'rtl' ? ok('"Answered:" line detects rtl from the Hebrew answer, not the English label') : fail(`"Answered:" line direction=${al.direction} — the "Answered:" prefix is bleeding into direction detection`);
  al.prefixDirection === 'ltr' ? ok('"Answered:" label span stays ltr') : fail(`"Answered:" label span direction=${al.prefixDirection}`);
  (al.prefixText === 'Answered:') ? ok('"Answered:" label carries no baked-in trailing space (spacing comes from flex gap)') : fail(`"Answered:" label text=${JSON.stringify(al.prefixText)}`);
  al.colonLeftOfWord === true ? ok('rtl "Answered:" line: colon sits LEFT of "Answered", between label and answer') : fail(`rtl "Answered:" colon not between label and answer (colonLeftOfWord=${al.colonLeftOfWord})`);
  const HE_OPT = HE.slice(0, 6);
  al.bdiText === HE_OPT ? ok('answer text is isolated in its own <bdi>, text intact') : fail(`answer <bdi> text=${JSON.stringify(al.bdiText)}, expected ${JSON.stringify(HE_OPT)}`);
  (al.gapPx !== null && al.gapPx >= 4) ? ok(`"Answered:" keeps a visible gap from the answer (${al.gapPx.toFixed(1)}px)`) : fail(`"Answered:" / answer gap=${al.gapPx}px — no visible gap`);

  // 5. User bubble
  const ub = result.userBubble;
  ub.lineCount === 1 ? ok('single-line user bubble: exactly one per-line dir="auto" block') : fail(`single-line user bubble produced ${ub.lineCount} line blocks, expected 1`);
  ub.direction === 'rtl' ? ok('mixed user bubble ("> Ron: <Hebrew>") detects rtl from the message, not "Ron:"') : fail(`user bubble direction=${ub.direction} — the "> Ron: " prefix is bleeding into direction detection`);
  ub.prefixDirection === 'ltr' ? ok(`"> Ron: " prefix span stays ltr (text=${JSON.stringify(ub.prefixText)})`) : fail(`user bubble prefix span direction=${ub.prefixDirection} (found: ${JSON.stringify(ub.prefixText)})`);
  (ub.prefixText === 'Ron') ? ok('"> " marker is outside the name isolate') : fail(`name isolate text=${JSON.stringify(ub.prefixText)}, expected "Ron" (marker inside the isolate can't follow the line direction)`);
  ub.markerRightOfName === true ? ok(`rtl line: "${(ub.markerText || '').trim()}" marker sits on the right edge, right of the name`) : fail(`rtl "> Ron:" marker not right of the name (markerRightOfName=${ub.markerRightOfName})`);
  ub.enMarkerLeftOfName === true ? ok('ltr line: "> Ron:" marker still left of the name') : fail(`ltr "> Ron:" marker moved (enMarkerLeftOfName=${ub.enMarkerLeftOfName})`);

  // 5b. Mixed-language bubble: Q line (Hebrew) rtl, A line (English) ltr, independently.
  const mb = result.mixedBubble;
  mb.lineCount === 2 ? ok('mixed "Q:/A:" bubble: two independent per-line blocks') : fail(`mixed bubble produced ${mb.lineCount} line blocks, expected 2`);
  mb.qLineDir === 'rtl' ? ok('"Q: <Hebrew>?" line computes direction=rtl on its own') : fail(`"Q:" line direction=${mb.qLineDir}, expected rtl`);
  mb.aLineDir === 'ltr' ? ok('"A: <English>" line stays direction=ltr, unaffected by the Hebrew Q line') : fail(`"A:" line direction=${mb.aLineDir}, expected ltr — leaked from the other line`);
  mb.qPrefixText === 'Q' ? ok('"Q" label isolated ltr, its ": " left outside the isolate') : fail(`"Q" prefix text=${JSON.stringify(mb.qPrefixText)}`);
  mb.aPrefixText === 'A' ? ok('"A" label isolated ltr') : fail(`"A" prefix text=${JSON.stringify(mb.aPrefixText)}`);
  mb.qColonLeftOfLabel === true ? ok('rtl "Q:" line: colon sits LEFT of "Q", between label and Hebrew text') : fail(`rtl "Q:" colon on the wrong side (qColonLeftOfLabel=${mb.qColonLeftOfLabel})`);

  // 5c. English-only bubble unaffected.
  const eb = result.englishBubble;
  eb.lineCount === 2 ? ok('English-only bubble: two per-line blocks (one per source line)') : fail(`English bubble produced ${eb.lineCount} line blocks, expected 2`);
  eb.allLtr ? ok('English-only bubble: every line direction=ltr') : fail(`English-only bubble had a non-ltr line (text=${eb.text})`);

  // 6. Composer
  result.composer.direction === 'rtl' ? ok('composer textarea: direction=rtl while typing Hebrew') : fail(`composer textarea direction=${result.composer.direction}`);

  // 7. Rail title
  result.railTitle.direction === 'rtl' ? ok('rail conversation title: direction=rtl for Arabic') : fail(`rail title direction=${result.railTitle.direction}`);

  // 8. Backlog
  result.backlog.direction === 'rtl' ? ok('backlog item text: direction=rtl for Hebrew') : fail(`backlog item direction=${result.backlog.direction}`);
  result.backlog.numDirection === 'ltr' ? ok('backlog item key (MC-1000) stays ltr inside the rtl item') : fail(`backlog key direction=${result.backlog.numDirection}`);
  result.backlogEn.direction === 'ltr' ? ok('backlog item text: direction=ltr for English (unaffected)') : fail(`backlog item (English) direction=${result.backlogEn.direction}`);

  // 9. Round 2 fix 4 — RTL run isolation inside an English-base line.
  const eqh = result.englishQuotingHebrew;
  eqh.lineDirection === 'ltr' ? ok('English line quoting Hebrew: line direction stays ltr') : fail(`English-quoting-Hebrew line direction=${eqh.lineDirection}, expected ltr`);
  eqh.bdiCount === 1 ? ok('English line quoting Hebrew: exactly one isolated <bdi> run') : fail(`English-quoting-Hebrew line has ${eqh.bdiCount} <bdi> elements, expected 1`);
  // The surrounding double quotes are wrapped INTO the isolated run (not left
  // outside as neutrals) — per the round-2 spec, "do the same inside
  // double-quoted spans that contain RTL" — so the quote marks stay attached
  // to the correct edge of the Hebrew text instead of resolving against the
  // outer LTR paragraph.
  eqh.bdiText === '"כן, נראה טוב"' ? ok(`isolated run text includes its wrapping quotes, attached to the Hebrew phrase: ${JSON.stringify(eqh.bdiText)}`) : fail(`isolated <bdi> text=${JSON.stringify(eqh.bdiText)}, expected the quote-wrapped phrase`);

  const ca = result.colonAdjacency;
  (ca.gapBetweenAnsweredAndColon !== null && ca.gapBetweenAnsweredAndColon < 2)
    ? ok(`ltr "Answered:" colon sits immediately right of "Answered" (${ca.gapBetweenAnsweredAndColon.toFixed(2)}px gap)`)
    : fail(`"Answered" / ":" gap=${ca.gapBetweenAnsweredAndColon}px — colon is not immediately adjacent`);

} catch (e) {
  fail(`harness error: ${e.message}`);
} finally {
  if (browser) await browser.close().catch(() => {});
}

if (bad) {
  console.error(`\nRTL smoke: ${bad} failure(s).`);
  process.exit(1);
} else {
  console.log('\nRTL smoke: all checks passed.');
  process.exit(0);
}
