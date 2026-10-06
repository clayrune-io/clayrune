#!/usr/bin/env node
/**
 * Documents tab content search (static/js/documents-search.js, MC-950 front end).
 *
 * Hermetic, same harness as tab-filter-reachability.mjs: the real
 * static/index.html + every extracted module this feature touches are fulfilled
 * by Playwright route interception, with canned /documents, /docs/search and
 * /document-file responses. No running server (the server on :5199 predates
 * the /docs/search route, so it could not answer anyway), no network.
 *
 * Per viewport (1440 and 390):
 *   - 1-char query fires no request (min 2 chars)
 *   - fast typing is debounced to ONE request carrying the full query
 *   - hits replace the list; file, heading, 'journal' tag and highlight render
 *   - a hostile snippet/heading is escaped, never injected (no <img>, no onerror)
 *   - results survive a refreshModal() rebuild (input value + hits kept)
 *   - clicking a hit opens the viewer, scrolled to line_start
 *   - a 0-hit response renders 'No matches'
 *   - a failed request renders 'unavailable', NOT 'No matches'
 *   - clearing the box restores the normal document list
 *   - no horizontal overflow of the results at that width
 *
 * Screenshots go to _scratch/docs-search-shots/ (gitignored).
 *
 * RUN: node documents-search.mjs
 */
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const rd = (...p) => readFileSync(resolve(REPO_ROOT, ...p), 'utf8');
const SHOTS = resolve(REPO_ROOT, '_scratch', 'docs-search-shots');
mkdirSync(SHOTS, { recursive: true });

const INDEX_HTML = rd('static', 'index.html');

// Same module list as tab-filter-reachability.mjs, plus documents-search.js.
const JS_MODULES = [
  'claydo.js', 'mobile-pairing.js', 'walkthrough.js', 'skills-panel.js', 'media.js',
  'settings-drill.js', 'settings-sections.js', 'terminal.js', 'mermaid.js',
  'search-chats.js', 'backlog-actions.js', 'cross-backlog.js', 'scheduler.js',
  'schedule-calendar.js', 'automation-suggestions.js', 'mcp.js', 'secret-form.js', 'secrets-panel.js',
  'system-status.js', 'update-power.js', 'provider-auth.js', 'schedule-banner.js',
  'provider-settings.js', 'process-manager.js', 'cross-hivemind.js', 'feed.js',
  'beacon.js', 'mobile.js', 'project-actions.js', 'composer-extras.js',
  'slash-autocomplete.js', 'mention-autocomplete.js', 'appearance.js',
  'project-forms.js', 'interactions.js', 'render-core.js', 'modal-manager.js',
  'agent-console.js', 'floor.js', 'hivemind.js', 'agent-log.js', 'documents-search.js',
  'resume-preview.js', 'conversation.js', 'rich-text.js', 'team-card.js', 'cross-social.js',
  'desk.js', 'workflow-builder.js', 'first-run.js',
];
const STATIC_MAP = {
  '/static/css/app.css': ['text/css; charset=utf-8', rd('static', 'css', 'app.css')],
  '/static/css/beacon.css': ['text/css; charset=utf-8', rd('static', 'css', 'beacon.css')],
  '/static/css/documents-search.css': ['text/css; charset=utf-8', rd('static', 'css', 'documents-search.css')],
};
for (const name of JS_MODULES) {
  STATIC_MAP[`/static/js/${name}`] = ['text/javascript; charset=utf-8', rd('static', 'js', name)];
}

const PID = 'smoke_docsearch';
const PROJECT = {
  id: PID, name: 'Docs Search Smoke', status: 'active', domain: 'general', emoji: '🧪',
  description: 'Fixture project for the Documents-tab search smoke.', summary: '',
  current_task: 'Idle', next_action: '', blocked: false, blocked_reason: null,
  activity_log: [], backlog: [], project_path: '/tmp/smoke-docsearch',
  last_updated: '2026-10-05T00:00:00Z', last_updated_relative: 'today', last_completed: null,
  live_agent: null, display_order: 0, provider: 'claude', use_streaming_agent: true,
  distiller_mode: 'proposed', distiller_min_recurrence: 3, distiller_max_topics_per_session: 3,
  distiller_max_preferences_per_session: 3, distiller_max_explorations_per_session: 3,
  distiller_min_turns: 5, distiller_skip_errors: true,
};
const DOCUMENTS = [
  { path: 'docs/PLAN_ONE.md', title: 'Plan One', kind: 'plan', ts_relative: '2h ago',
    task: 'First plan', filename: 'PLAN_ONE.md', deletable: true },
  { path: 'docs/DOC_TWO.md', title: 'Doc Two', kind: 'doc', ts_relative: '1h ago',
    location: 'docs/DOC_TWO.md', filename: 'DOC_TWO.md', deletable: false },
];

const TARGET_LINE = 70;
const HITS = [
  { file: 'docs/PRICING_NOTES.md', path: '/tmp/smoke-docsearch/docs/PRICING_NOTES.md',
    heading: 'Tier pricing', tier: 'docs', line_start: TARGET_LINE, line_end: TARGET_LINE + 6, score: 4.2,
    snippet: 'the free tier keeps »pricing« flat while the paid tier » adds seats « … and a very long unbroken token ' + 'x'.repeat(90) },
  { file: 'docs/_journal/13affa86-rag-documents.md', path: '/tmp/smoke-docsearch/docs/_journal/13affa86-rag-documents.md',
    heading: 'Cycle 3', tier: 'journal', line_start: 12, line_end: 20, score: 1.1,
    snippet: 'measured »pricing« page copy on the live site' },
  // Hostile: raw HTML in snippet and heading. Must render as text.
  { file: 'docs/EVIL.md', path: '/tmp/smoke-docsearch/docs/EVIL.md',
    heading: '<img src=x onerror="window.__xss=1">', tier: 'docs', line_start: 3, line_end: 4, score: 0.5,
    snippet: '<img src=x onerror="window.__xss=2"> »pricing« <script>window.__xss=3</script>' },
];
const DOC_LINES = Array.from({ length: 120 }, (_, i) => `Line ${i + 1} of the pricing notes document.`).join('\n');

const ORIGIN = 'http://mc.smoke.test';
let searchReqs = [];

function json(route, body, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

function handle(route) {
  const u = new URL(route.request().url());
  const path = u.pathname;
  if (path === '/' || path === '/index.html')
    return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  if (STATIC_MAP[path]) return route.fulfill({ status: 200, contentType: STATIC_MAP[path][0], body: STATIC_MAP[path][1] });
  if (path === '/api/projects') return json(route, [PROJECT]);
  if (path === '/api/config') return json(route, {});
  if (path === `/api/project/${PID}/backlog`) return json(route, []);
  if (path === `/api/project/${PID}/agent/status`) return json(route, { sessions: [] });
  if (path === `/api/project/${PID}/terminal/status`) return json(route, { sessions: [] });
  if (path === `/api/project/${PID}/social/queue`) return json(route, []);
  if (path === `/api/project/${PID}/documents`) return json(route, DOCUMENTS);
  if (path === '/api/document-file') return json(route, { path: u.searchParams.get('path'), filename: 'PRICING_NOTES.md', content: DOC_LINES });
  if (path === `/api/project/${PID}/docs/search`) {
    const q = u.searchParams.get('q') || '';
    searchReqs.push(q);
    if (q.startsWith('zzzz')) return json(route, []);
    if (q.startsWith('boom')) return json(route, { error: 'nope' }, 500);
    return json(route, HITS);
  }
  return route.abort();
}

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg));

async function runAtViewport(browser, width, height, label) {
  console.log(`\n-- ${label} (${width}x${height}) --`);
  searchReqs = [];
  const ctx = await browser.newContext({ viewport: { width, height } });
  try {
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
    await page.route('**/*', handle);
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 20000 });
    await page.evaluate((pid) => window.openProjectModal(pid), PID);
    await page.waitForTimeout(600);
    await page.evaluate((pid) => window.switchModalTab(pid, 'documents'), PID);
    await page.waitForSelector('.modal-tab-content.active .plan-history-card', { timeout: 10000 });

    const input = page.locator('.modal-tab-content.active .doc-search-input');
    check(await input.count() === 1, 'search box present on the Documents tab', 'expected exactly 1 .doc-search-input');
    const box = await input.boundingBox();
    check(box && box.width > 100 && box.height > 20, `search box visible (${Math.round(box.width)}x${Math.round(box.height)})`, 'search box not visible');
    const listCards = await page.$$eval('.modal-tab-content.active .plan-history-card', (e) => e.length);

    // min 2 chars
    await input.pressSequentially('p');
    await page.waitForTimeout(500);
    check(searchReqs.length === 0, '1-char query fires no request', `1-char query fired ${searchReqs.length} request(s)`);

    // debounce: rapid typing → one request with the full query
    await input.fill('');
    await input.pressSequentially('pricing', { delay: 20 });
    await page.waitForSelector('.doc-search-hit', { timeout: 5000 });
    check(searchReqs.length === 1 && searchReqs[0] === 'pricing',
      'fast typing debounced to one request with the full query', `requests: ${JSON.stringify(searchReqs)}`);

    const hitCount = await page.$$eval('.doc-search-hit', (e) => e.length);
    check(hitCount === HITS.length, `${hitCount} hits replace the list`, `expected ${HITS.length} hits, got ${hitCount}`);
    check(await page.$$eval('.modal-tab-content.active .plan-history-card', (e) => e.length) === 0,
      'document list hidden while a query is active', 'document cards still visible during search');
    const first = page.locator('.doc-search-hit').first();
    check((await first.locator('.doc-search-path').innerText()) === 'docs/PRICING_NOTES.md', 'hit shows its file path', 'wrong/missing file path');
    check((await first.locator('.doc-search-heading').innerText()) === 'Tier pricing', 'hit shows its heading', 'wrong/missing heading');
    const marks = await first.locator('mark').allInnerTexts();
    check(marks.length === 2 && marks[0] === 'pricing', `query terms highlighted (${JSON.stringify(marks)})`, `unexpected marks ${JSON.stringify(marks)}`);
    const tags = await page.$$eval('.doc-search-hit .doc-search-tier', (e) => e.map((x) => x.textContent.trim()));
    check(tags.length === 1 && tags[0] === 'journal', 'only the journal hit carries the journal tag', `tags: ${JSON.stringify(tags)}`);

    // XSS: nothing from the snippet/heading became markup
    const injected = await page.evaluate(() => ({
      img: document.querySelectorAll('.doc-search-hit img, .doc-search-hit script').length,
      xss: window.__xss || 0,
    }));
    check(injected.img === 0 && !injected.xss, 'hostile snippet/heading rendered as text (no img/script, no handler ran)', `injected: ${JSON.stringify(injected)}`);
    const evilText = await page.locator('.doc-search-hit').nth(2).innerText();
    check(evilText.includes('<img src=x'), 'hostile markup visible as literal text', `evil hit text: ${evilText}`);

    // layout: no horizontal overflow at this width
    const overflow = await page.evaluate(() => {
      const hit = document.querySelector('.doc-search-hit');
      const sb = hit.closest('.modal-scroll-body') || hit.closest('.modal-tab-content');
      return { sw: sb.scrollWidth, cw: sb.clientWidth, hitRight: hit.getBoundingClientRect().right, vw: window.innerWidth };
    });
    check(overflow.sw <= overflow.cw + 1 && overflow.hitRight <= overflow.vw,
      `no horizontal overflow (scroll ${overflow.sw} / client ${overflow.cw})`, `overflow: ${JSON.stringify(overflow)}`);
    await page.screenshot({ path: resolve(SHOTS, `results-${width}.png`) });

    // survives a modal rebuild
    await page.evaluate(() => window.refreshModal());
    await page.waitForTimeout(300);
    check((await input.inputValue()) === 'pricing' && await page.$$eval('.doc-search-hit', (e) => e.length) === HITS.length,
      'query and hits survive refreshModal()', 'search state lost on refreshModal()');

    // click a hit → viewer opens scrolled to line_start
    const viewersBefore = await page.$$eval('.plan-viewer-content', (e) => e.length);
    pageErrors.length = 0;
    await page.locator('.doc-search-hit').first().click();
    await page.waitForSelector('.plan-viewer-content', { timeout: 5000 });
    await page.waitForTimeout(500);
    const viewersAfter = await page.$$eval('.plan-viewer-content', (e) => e.length);
    check(viewersAfter === viewersBefore + 1, 'clicking a hit opens the document viewer', `viewers ${viewersBefore} → ${viewersAfter}`);
    const scrolled = await page.evaluate((line) => {
      const body = document.querySelector('.plan-viewer-body');
      const t = body && body.querySelector('.doc-search-target');
      if (!t) return { found: false };
      const r = t.getBoundingClientRect(), b = body.getBoundingClientRect();
      return { found: true, line: Number(t.dataset.line), inView: r.top >= b.top - 1 && r.bottom <= b.bottom + 1, scrollTop: body.scrollTop };
    }, TARGET_LINE);
    check(scrolled.found && scrolled.line === TARGET_LINE && scrolled.inView,
      `viewer scrolled to line ${TARGET_LINE} (scrollTop ${scrolled.scrollTop})`, `scroll check: ${JSON.stringify(scrolled)}`);
    check(pageErrors.length === 0, 'no uncaught error on the click path', `uncaught: ${pageErrors.join(' | ')}`);
    await page.screenshot({ path: resolve(SHOTS, `viewer-${width}.png`) });
    await page.evaluate(() => document.querySelectorAll('.modal-window[data-modal-id^="__planhistory_"]')
      .forEach((m) => window.closeModalById(m.dataset.modalId)));
    await page.waitForTimeout(200);

    // 0 hits
    await input.fill('zzzznomatch');
    await page.waitForFunction(() => document.querySelector('.modal-tab-content.active .doc-search-note'), null, { timeout: 5000 });
    const note0 = await page.locator('.modal-tab-content.active .doc-search-note').innerText();
    check(note0.trim() === 'No matches', '0-hit response renders "No matches"', `0-hit text: ${JSON.stringify(note0)}`);
    await page.screenshot({ path: resolve(SHOTS, `nomatch-${width}.png`) });

    // failure ≠ no matches
    await input.fill('boom query');
    await page.waitForFunction(() => /unavailable/i.test(document.querySelector('.modal-tab-content.active .doc-search-note')?.textContent || ''), null, { timeout: 5000 });
    ok('failed request renders "unavailable", not "No matches"');

    // stale response must not overwrite a newer query: type pricing then clear fast
    await input.fill('pricing');
    await input.fill('');
    await page.waitForTimeout(700);
    check(await page.$$eval('.doc-search-hit', (e) => e.length) === 0, 'cleared query is not repainted by a late response', 'late response repainted hits after clear');

    // clear → normal list back
    check(await page.$$eval('.modal-tab-content.active .plan-history-card', (e) => e.length) === listCards,
      `clearing the box restores the ${listCards}-row document list`, 'document list not restored after clear');

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    check(uncaught.length === 0, 'no uncaught JS errors', 'uncaught: ' + uncaught.join(' | '));
  } finally {
    await ctx.close();
  }
}

const browser = await chromium.launch();
try {
  await runAtViewport(browser, 1440, 900, 'desktop');
  await runAtViewport(browser, 390, 844, 'mobile');
} finally {
  await browser.close();
}
console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
