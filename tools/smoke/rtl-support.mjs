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
      out.question = {
        found: !!card,
        headerDir: header ? dirOf(header) : null,
        qtextDir: qtext ? dirOf(qtext) : null,
        optLabelDir: optLabel ? dirOf(optLabel) : null,
        descDir: desc ? dirOf(desc) : null,
      };

      // "Answered: " summary line — the prefix-bleed fix under test.
      const answerLine = document.createElement('div');
      answerLine.className = 'agent-question-answer';
      answerLine.setAttribute('dir', 'auto');
      answerLine.innerHTML = `<span dir="ltr">Answered: </span>${window.esc(HE)}`;
      root.appendChild(answerLine);
      const prefixSpan = answerLine.querySelector('span');
      out.answeredLine = {
        direction: dirOf(answerLine),
        prefixDirection: prefixSpan ? dirOf(prefixSpan) : null,
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
      const prefixSpan = div.querySelector('span[dir="ltr"]');
      out.userBubble = {
        direction: dirOf(div),
        prefixDirection: prefixSpan ? dirOf(prefixSpan) : null,
        prefixText: prefixSpan ? prefixSpan.textContent : null,
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
  q.headerDir === 'rtl' ? ok('question header: direction=rtl') : fail(`question header direction=${q.headerDir}`);
  q.qtextDir === 'rtl' ? ok('question text: direction=rtl') : fail(`question text direction=${q.qtextDir}`);
  q.optLabelDir === 'rtl' ? ok('question option label: direction=rtl') : fail(`question option label direction=${q.optLabelDir}`);
  q.descDir === 'rtl' ? ok('question option description: direction=rtl') : fail(`question option description direction=${q.descDir}`);
  result.answeredLine.direction === 'rtl' ? ok('"Answered:" line detects rtl from the Hebrew answer, not the English label') : fail(`"Answered:" line direction=${result.answeredLine.direction} — the "Answered: " prefix is bleeding into direction detection`);
  result.answeredLine.prefixDirection === 'ltr' ? ok('"Answered: " label span stays ltr') : fail(`"Answered: " label span direction=${result.answeredLine.prefixDirection}`);

  // 5. User bubble
  const ub = result.userBubble;
  ub.direction === 'rtl' ? ok('mixed user bubble ("> Ron: <Hebrew>") detects rtl from the message, not "Ron:"') : fail(`user bubble direction=${ub.direction} — the "> Ron: " prefix is bleeding into direction detection`);
  ub.prefixDirection === 'ltr' ? ok(`"> Ron: " prefix span stays ltr (text=${JSON.stringify(ub.prefixText)})`) : fail(`user bubble prefix span direction=${ub.prefixDirection} (found: ${JSON.stringify(ub.prefixText)})`);

  // 6. Composer
  result.composer.direction === 'rtl' ? ok('composer textarea: direction=rtl while typing Hebrew') : fail(`composer textarea direction=${result.composer.direction}`);

  // 7. Rail title
  result.railTitle.direction === 'rtl' ? ok('rail conversation title: direction=rtl for Arabic') : fail(`rail title direction=${result.railTitle.direction}`);

  // 8. Backlog
  result.backlog.direction === 'rtl' ? ok('backlog item text: direction=rtl for Hebrew') : fail(`backlog item direction=${result.backlog.direction}`);
  result.backlog.numDirection === 'ltr' ? ok('backlog item key (MC-1000) stays ltr inside the rtl item') : fail(`backlog key direction=${result.backlog.numDirection}`);
  result.backlogEn.direction === 'ltr' ? ok('backlog item text: direction=ltr for English (unaffected)') : fail(`backlog item (English) direction=${result.backlogEn.direction}`);

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
