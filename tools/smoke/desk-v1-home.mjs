#!/usr/bin/env node
/**
 * Desk v1 (MC-977, R0 plan T1) — Desk Home smoke (frame 11a, not in the
 * repo — built from THE_DESK_V1_UI.md §2 text; see docs/desk_v1_r0_plan.md
 * ground rule 8).
 *
 * Closes: A13 (worker-offline heartbeat surfaces as a hold row), A4 (the
 * Home half — dropping a channel onto a campaign card adds it as a
 * destination), A12 on Home (every Needs-you row deep-links to review/
 * conversations with the params those surfaces expect).
 *
 * Real headless boot (real index.html + real static/js|css, no network), same
 * hermetic shape as desk-v1-harness.mjs / desk-v1-review.mjs.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-home.mjs
 * Exit 0 = every case holds (render checks in all three tones, interaction
 * checks once); 1 = a case regressed / harness error.
 */
import { readFileSync, readdirSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const ASSETS_DIR = resolve(REPO_ROOT, 'assets');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const SHOT_DIR = resolve(REPO_ROOT, 'docs', 'desk_v1', 'screens');
mkdirSync(SHOT_DIR, { recursive: true });

const MIME = { '.webp': 'image/webp', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml' };

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];
// See desk-v1-review.mjs's identical stub: the Claydo FAB requests
// /assets/claydo-*.webp; without this the network stub aborts it and every
// screenshot near the FAB shows a broken-image glyph.
for (const f of readdirSync(ASSETS_DIR)) {
  const ext = f.slice(f.lastIndexOf('.'));
  if (MIME[ext]) STATIC[`/assets/${f}`] = [MIME[ext], readFileSync(resolve(ASSETS_DIR, f))];
}

const PID = 'smoke_deskv1home';
const PROJECTS = [{
  id: PID, name: 'Desk v1 home smoke', status: 'active', domain: 'general', emoji: '🧪',
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
  // Clayrune's project has presence.desk_agent 'global:claydo' (resolved here);
  // Engulfing's has none (unresolved) - the two block-header chips in the mockup.
  if (path === '/api/characters') return J([{ scope: 'global', name: 'claydo', agent_name: 'Claydo', avatar: '' }]);
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
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  // Home is the default route on open (desk-v1-shell.js _stack resets to
  // 'home') — no explicit deskV1Nav needed to land here.
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-home', { timeout: 8000 });
  return { ctx, page, pageErrors };
}

function reportUncaught(pageErrors, tag) {
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail(`${tag} uncaught page error: ${e}`));
}

// ── render checks, one per tone: header, promote box, status board (ONE
// column header, bordered per-project blocks, per-row Needs-you column),
// shelves. R2-2 (§8 amended row, §11.6 item 2): the old flat project-card
// grid + grouped Needs-you section are retired — each project is its own
// bordered block of campaign rows, and Needs-you is a COLUMN in that row
// showing the top reason (blocker > held channel > piece/video/reply,
// highest first) plus "+n" for the other kinds present. ────────────────────
async function runToneRenderChecks(browser, tone) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, tone);

  // ONE column header for the whole page, not one per block (§8 amended row).
  const headCount = await page.$$eval('.desk-v1-home-board-head', (els) => els.length);
  if (headCount === 1) ok(`[${tone.name}] exactly one page-wide column header`);
  else fail(`[${tone.name}] expected 1 column header, got ${headCount}`);
  const headText = (await page.textContent('.desk-v1-home-board-head').catch(() => '') || '');
  if (/CAMPAIGN/.test(headText) && /STAGE/.test(headText) && /GOAL PROGRESS/.test(headText) && /PACE/.test(headText) && /NEXT POST/.test(headText) && /NEEDS YOU/.test(headText)) {
    ok(`[${tone.name}] column header names all six columns`);
  } else {
    fail(`[${tone.name}] column header missing a column: ${JSON.stringify(headText)}`);
  }

  // Two bordered project blocks (both fixture projects), campaign grid retired.
  const blockCount = await page.$$eval('.desk-v1-home-block', (els) => els.length);
  if (blockCount === 2) ok(`[${tone.name}] both fixture project blocks render`);
  else fail(`[${tone.name}] expected 2 project blocks, got ${blockCount}`);
  const campCardsGone = await page.$('.desk-v1-home-camp-card');
  !campCardsGone ? ok(`[${tone.name}] no flat campaign-card grid on Home`) : fail(`[${tone.name}] a retired .desk-v1-home-camp-card still rendered`);

  // IA6: clayrune's block holds camp-1/camp-2/camp-4 (camp-archived-1 excluded,
  // its state is 'archived'); engulfing_scanner's block holds camp-3 alone.
  const blockRowTitles = await page.evaluate(() => {
    const out = {};
    document.querySelectorAll('.desk-v1-home-block').forEach((b) => {
      const name = b.querySelector('.desk-v1-home-block-name').textContent.trim();
      out[name] = Array.from(b.querySelectorAll('.desk-v1-home-row-title')).map((t) => t.textContent.trim());
    });
    return out;
  });
  const clayruneTitles = blockRowTitles['Clayrune'] || [];
  if (['Windows beta testers', 'Restore points launch', 'Community Discord launch'].every((t) => clayruneTitles.includes(t)) && clayruneTitles.length === 3) {
    ok(`[${tone.name}] Clayrune block: camp-1/camp-2/camp-4 rows, camp-archived-1 excluded`);
  } else {
    fail(`[${tone.name}] Clayrune block rows wrong: ${JSON.stringify(clayruneTitles)}`);
  }
  const engulfingTitles = blockRowTitles['Engulfing scanner'] || [];
  if (engulfingTitles.length === 1 && engulfingTitles[0] === 'Signal alerts for day traders') {
    ok(`[${tone.name}] Engulfing scanner block: camp-3 row alone`);
  } else {
    fail(`[${tone.name}] Engulfing scanner block rows wrong: ${JSON.stringify(engulfingTitles)}`);
  }

  // Goal text keeps a zero count: camp-4 is tracked at 0 of 40, and the
  // app-wide esc()'s old `str||''` guard rendered it as "of 40 Discord joins".
  const goalTexts = await page.evaluate(() => {
    const out = {};
    document.querySelectorAll('.desk-v1-home-row').forEach((r) => {
      const t = r.querySelector('.desk-v1-home-row-title');
      const g = r.querySelector('.desk-v1-home-goal-text');
      if (t && g) out[t.textContent.trim()] = g.textContent.trim();
    });
    return out;
  });
  const discordGoal = goalTexts['Community Discord launch'] || '';
  if (/^0 of 40 /.test(discordGoal)) ok(`[${tone.name}] zero goal count renders as "0 of 40", not blank`);
  else fail(`[${tone.name}] zero goal count lost: ${JSON.stringify(discordGoal)}`);
  if (/^11 of 30 tester signups$/.test(goalTexts['Windows beta testers'] || '')) ok(`[${tone.name}] goal text "11 of 30 tester signups"`);
  else fail(`[${tone.name}] Windows beta testers goal text wrong: ${JSON.stringify(goalTexts['Windows beta testers'])}`);

  // Needs-you column, per row, unmutated fixture state:
  //  - camp-1: ch-li-page (held) is one of its accounts — held beats the
  //    needs_review piece/video also sitting on this campaign (§11.6 item 2
  //    priority: blocker > held > piece/video/reply).
  //  - camp-2 (proposed): its own sequencing blocker beats everything.
  //  - camp-3: no held channel, no needs_review family — its one needs_you
  //    conversation is the only bucket, so no "+n" suffix.
  //  - camp-4: nothing pending — empty dash, no pill.
  const needsYouByRow = await page.evaluate(() => {
    const out = {};
    document.querySelectorAll('.desk-v1-home-row').forEach((r) => {
      const pill = r.querySelector('.desk-v1-home-needsyou-pill');
      out[r.dataset.campaignId] = pill ? { kind: pill.dataset.needsyouKind, text: pill.textContent.trim() } : { empty: !!r.querySelector('.desk-v1-home-needsyou-empty') };
    });
    return out;
  });
  if (needsYouByRow['camp-1'] && needsYouByRow['camp-1'].kind === 'held' && /LinkedIn disconnected/.test(needsYouByRow['camp-1'].text)) {
    ok(`[${tone.name}] camp-1 Needs you: held channel wins over its own pending piece/video: "${needsYouByRow['camp-1'].text}"`);
  } else {
    fail(`[${tone.name}] camp-1 Needs you wrong: ${JSON.stringify(needsYouByRow['camp-1'])}`);
  }
  if (needsYouByRow['camp-2'] && needsYouByRow['camp-2'].kind === 'blocker' && /Needs your answer/.test(needsYouByRow['camp-2'].text)) {
    ok(`[${tone.name}] camp-2 Needs you: proposed sequencing blocker: "${needsYouByRow['camp-2'].text}"`);
  } else {
    fail(`[${tone.name}] camp-2 Needs you wrong: ${JSON.stringify(needsYouByRow['camp-2'])}`);
  }
  if (needsYouByRow['camp-3'] && needsYouByRow['camp-3'].kind === 'reply' && /^\S\s1 reply waiting$/.test(needsYouByRow['camp-3'].text)) {
    ok(`[${tone.name}] camp-3 Needs you: "1 reply waiting" (no held channel, no "+n")`);
  } else {
    fail(`[${tone.name}] camp-3 Needs you wrong: ${JSON.stringify(needsYouByRow['camp-3'])}`);
  }
  if (needsYouByRow['camp-4'] && needsYouByRow['camp-4'].empty) {
    ok(`[${tone.name}] camp-4 Needs you: empty dash (nothing pending)`);
  } else {
    fail(`[${tone.name}] camp-4 Needs you should be empty: ${JSON.stringify(needsYouByRow['camp-4'])}`);
  }

  // A13 worker-offline: a banner above the board, not a per-row hold (it's a
  // worker-wide condition, §11.6/A13 comment in desk-v1-home.js).
  const bannerText = (await page.textContent('.desk-v1-home-worker-banner').catch(() => '') || '');
  if (/Scheduling paused/.test(bannerText) && /worker offline since 14:02/.test(bannerText) && /2 posts missed/.test(bannerText)) {
    ok(`[${tone.name}] A13: worker-offline banner: "Scheduling paused — worker offline since 14:02 · 2 posts missed"`);
  } else {
    fail(`[${tone.name}] A13: worker-offline banner missing/wrong: ${JSON.stringify(bannerText)}`);
  }

  // R2-2d: Channels + Material shelves and the promote box are gone from Home
  // (they are WHERE's / WHAT's side trays; New campaign is the only entry).
  const gone = await page.evaluate(() => ({
    shelfSection: !!document.querySelector('.desk-v1-home-shelf, .desk-v1-home-shelves, [class*="desk-v1-home-shelf-"], .desk-v1-home-material-tile, .desk-v1-home-material-assets'),
    promote: !!document.querySelector('.desk-v1-home-promote, textarea, #desk-v1-home-promote-input'),
    shelfText: /\b(Channels|Material)\b/.test(document.querySelector('.desk-v1-home').innerText),
  }));
  !gone.shelfSection ? ok(`[${tone.name}] R2-2d: no shelf section on Home`) : fail(`[${tone.name}] R2-2d: a shelf element still renders on Home`);
  !gone.shelfText ? ok(`[${tone.name}] R2-2d: no Channels / Material heading on Home`) : fail(`[${tone.name}] R2-2d: Home still shows a Channels/Material heading`);
  !gone.promote ? ok(`[${tone.name}] R2-2d: no promote textarea on Home`) : fail(`[${tone.name}] R2-2d: a promote textarea still renders on Home`);

  // R2-2d row parity with mockups_r2/1-home.png. Stage / pace / next post read
  // per row; the fixture clock is the real one, camp-4's term starts today.
  const rows = await page.evaluate(() => {
    const out = {};
    document.querySelectorAll('.desk-v1-home-row').forEach((r) => {
      const cell = (sel) => (r.querySelector(sel) ? r.querySelector(sel).textContent.replace(/\s+/g, ' ').trim() : null);
      out[r.dataset.campaignId] = { stage: cell('.desk-v1-home-row-stage'), pace: cell('.desk-v1-home-row-pace'), next: cell('.desk-v1-home-row-next') };
    });
    return out;
  });
  const wantRows = [
    ['camp-1 stage is dot + word', rows['camp-1'] && rows['camp-1'].stage, /^● Active$/],
    ['camp-2 stage is diamond + word', rows['camp-2'] && rows['camp-2'].stage, /^◇ Proposed$/],
    ['camp-4 (day 0) stage reads "Active · just started"', rows['camp-4'] && rows['camp-4'].stage, /^● Active · just started$/],
    ['camp-2 next post reads "Draft · at Launch", never a dash', rows['camp-2'] && rows['camp-2'].next, /^Draft · at Launch$/],
    ['camp-1 next post names the LinkedIn account', rows['camp-1'] && rows['camp-1'].next, /^\w{3} \d\d:\d\d · LinkedIn$/],
    ['camp-4 next post carries the X handle', rows['camp-4'] && rows['camp-4'].next, /^\w{3} \d\d:\d\d · 𝕏 @ron$/],
    ['camp-3 next post carries the X handle', rows['camp-3'] && rows['camp-3'].next, /^\w{3} \d\d:\d\d · 𝕏 @ron$/],
    ['camp-4 tracked goal at ~0% elapsed paces "On track", not a dash', rows['camp-4'] && rows['camp-4'].pace, /^On track$/],
    ['camp-2 untracked goal keeps the dash pace', rows['camp-2'] && rows['camp-2'].pace, /^—$/],
  ];
  for (const [label, got, re] of wantRows) {
    re.test(got || '') ? ok(`[${tone.name}] R2-2d: ${label}: ${JSON.stringify(got)}`) : fail(`[${tone.name}] R2-2d: ${label}: got ${JSON.stringify(got)}`);
  }

  // Top-right "＋ New campaign" is the filled accent primary; the per-block
  // one stays a text link (transparent background).
  const btnStyles = await page.evaluate(() => {
    const top = getComputedStyle(document.querySelector('.desk-v1-home-newcamp-page-btn'));
    const blk = getComputedStyle(document.querySelector('.desk-v1-home-block-newcamp'));
    return { topBg: top.backgroundColor, topColor: top.color, blockBg: blk.backgroundColor };
  });
  const isFilled = (bg) => !/^rgba\(0, 0, 0, 0\)$|^transparent$/.test(bg);
  if (isFilled(btnStyles.topBg) && btnStyles.topColor === 'rgb(255, 255, 255)') ok(`[${tone.name}] R2-2d: page-level "＋ New campaign" is a filled primary (${btnStyles.topBg}, white text)`);
  else fail(`[${tone.name}] R2-2d: page-level "＋ New campaign" is not a filled primary: ${JSON.stringify(btnStyles)}`);
  if (!isFilled(btnStyles.blockBg)) ok(`[${tone.name}] R2-2d: per-block "＋ New campaign" stays a text link (transparent)`);
  else fail(`[${tone.name}] R2-2d: per-block "＋ New campaign" is filled: ${btnStyles.blockBg}`);

  // Block-header agent chip: Clayrune resolved, Engulfing not (mockup).
  const chips = await page.evaluate(() => {
    const out = {};
    document.querySelectorAll('.desk-v1-home-block').forEach((b) => {
      const chip = b.querySelector('.desk-v1-home-block-agent');
      out[b.querySelector('.desk-v1-home-block-name').textContent.trim()] = { text: chip.textContent.replace(/\s+/g, ' ').trim(), unresolved: chip.classList.contains('desk-v1-home-block-agent-unresolved') };
    });
    return out;
  });
  if (chips['Clayrune'] && /Claydo plans & writes$/.test(chips['Clayrune'].text) && !chips['Clayrune'].unresolved) ok(`[${tone.name}] R2-2d: Clayrune header chip reads "Claydo plans & writes"`);
  else fail(`[${tone.name}] R2-2d: Clayrune header chip wrong: ${JSON.stringify(chips['Clayrune'])}`);
  if (chips['Engulfing scanner'] && /Pick who plans for this project ›$/.test(chips['Engulfing scanner'].text) && chips['Engulfing scanner'].unresolved) ok(`[${tone.name}] R2-2d: Engulfing header chip is the unresolved "Pick who plans for this project ›"`);
  else fail(`[${tone.name}] R2-2d: Engulfing header chip wrong: ${JSON.stringify(chips['Engulfing scanner'])}`);

  reportUncaught(pageErrors, `[${tone.name}]`);
  await ctx.close();
}

// ── A12 on Home: Needs-you rows deep-link to review/conversations with the
// params those surfaces expect. camp-1's unmutated fixture state has a held
// channel that outranks its own pending piece/video (§11.6 item 2 priority,
// verified above in runToneRenderChecks) — so exercising the 'piece' and
// 'video' kinds here first un-holds ch-li-page to let those buckets surface,
// the same fixture mutation technique the Add-to test below already uses. ──
async function runNeedsYouDeepLinks(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });

  // Un-hold ch-li-page: camp-1 now has 3 live buckets (piece, video, reply);
  // priority puts piece first, so its pill reads "1 piece to approve +2".
  await page.evaluate(() => {
    window.DeskV1Fixtures.channels.find((c) => c.id === 'ch-li-page').health = 'ok';
    window.__deskV1HomeTickHeartbeatNow();
  });
  const camp1PillText = (await page.textContent('.desk-v1-home-row[data-campaign-id="camp-1"] .desk-v1-home-needsyou-pill').catch(() => '') || '');
  if (/1 piece to approve \+2/.test(camp1PillText)) ok(`A12 setup: camp-1 Needs you shows top bucket + "+n": "${camp1PillText.trim()}"`);
  else fail(`A12 setup: camp-1 pill did not read "1 piece to approve +2": ${JSON.stringify(camp1PillText)}`);

  await page.click('.desk-v1-home-row[data-campaign-id="camp-1"] .desk-v1-home-needsyou-pill');
  await page.waitForSelector('.desk-v1-review', { timeout: 8000 });
  let onReview = await page.evaluate(() => document.querySelector('[data-act-primary]') ? true : false);
  if (onReview) ok('A12: "1 piece to approve" deep-links straight into the review surface (12b)');
  else fail('A12: piece row did not land on review');
  const claimBar = (await page.textContent('.desk-v1-claimbar-blocked').catch(() => '') || '');
  if (/No source for this/.test(claimBar)) ok('A12: piece deep-link resolved to v-restore-blog (its own blocked claim renders)');
  else fail(`A12: piece deep-link landed on the wrong version: ${JSON.stringify(claimBar)}`);

  // Resolve the piece so video becomes camp-1's top bucket ("1 video to watch +1").
  await page.evaluate(() => window.deskV1Nav('home', {}));
  await page.waitForSelector('.desk-v1-home', { timeout: 8000 });
  await page.evaluate(() => {
    const fam = window.DeskV1Fixtures.families.find((f) => f.id === 'fam-restore-points');
    fam.versions.find((v) => v.id === 'v-restore-blog').state = 'approved';
    window.__deskV1HomeTickHeartbeatNow();
  });
  const camp1PillText2 = (await page.textContent('.desk-v1-home-row[data-campaign-id="camp-1"] .desk-v1-home-needsyou-pill').catch(() => '') || '');
  if (/1 video to watch \+1/.test(camp1PillText2)) ok(`A12 setup: with the piece resolved, camp-1 Needs you promotes video: "${camp1PillText2.trim()}"`);
  else fail(`A12 setup: camp-1 pill did not read "1 video to watch +1": ${JSON.stringify(camp1PillText2)}`);
  await page.click('.desk-v1-home-row[data-campaign-id="camp-1"] .desk-v1-home-needsyou-pill');
  await page.waitForSelector('.desk-v1-stub-title:has-text("Video"), .desk-v1-video', { timeout: 8000 });
  // Route-name assertion (Dave's review): a video row must land on the
  // 'video' route (12d, T5's surface — still a stub in this ticket's tree),
  // never 'review' (12b) — §2's "12b, 12c, or 12d, not to a list" names them
  // as three distinct surfaces. The crumb title is ROUTES.video's own label
  // ('Video'), so it's the route-table entry talking, not a guess.
  const crumbAfterVideo = (await page.textContent('#desk-v1-crumb .desk-v1-crumb-title').catch(() => '') || '');
  if (/^Video$/.test(crumbAfterVideo.trim())) ok(`A12: "1 video to watch" deep-links to route 'video' (12d), not 'review': crumb "${crumbAfterVideo.trim()}"`);
  else fail(`A12: video row landed on the wrong route: crumb ${JSON.stringify(crumbAfterVideo)}`);

  // camp-3 is naturally reply-only (no held channel, no needs_review family)
  // — no mutation needed to isolate the 'reply' kind.
  await page.evaluate(() => window.deskV1Nav('home', {}));
  await page.waitForSelector('.desk-v1-home', { timeout: 8000 });
  await page.click('.desk-v1-home-row[data-campaign-id="camp-3"] .desk-v1-home-needsyou-pill');
  await page.waitForSelector('.desk-v1-stub, .desk-v1-conversations', { timeout: 8000 });
  // T2 (§2, §8): 'conversations' is a panel alias now, not a pushed route —
  // it lands on the SAME campaign crumb (the campaign's own name). R2-3:
  // Conversations has no map-stop button (§6, moves to Engagement at
  // R2-12), so "selected" is read off the rendered panel body, not a tab.
  const crumbTitle = (await page.textContent('#desk-v1-crumb .desk-v1-crumb-title').catch(() => '') || '');
  const convPanelShown = await page.$('.desk-v1-conv-layout, .desk-v1-conversations, .desk-v1-conv-empty');
  if (convPanelShown) ok(`A12: "1 reply waiting" deep-links to the conversations panel (campaign crumb "${crumbTitle.trim()}")`);
  else fail(`A12: reply row did not select the conversations panel: crumb ${JSON.stringify(crumbTitle)}`);

  reportUncaught(pageErrors, '[deep-links]');
  await ctx.close();
}

// ── R2-2d/R2-2f: `＋ New campaign` is Home's ONLY entry (the promote box and
// the Channels/Material shelves are gone; the campaign page's Add tray covers
// attach - desk-v1-campaign.mjs). R2-2f (Ron 2026-09-30, "we have duality in
// functionality"): the page-level button no longer opens a project menu - one
// click starts a Draft on its Brief stop (R2-18; was Goal). R2-2g (Ron
// 2026-09-30, "two project selectors"): the Project select is on Brief (first
// field) and Launch, never both at once (desk-v1-campaign.mjs), and the crumb's Projects: All picker is
// absent on the campaign route (present on Home and project routes). The
// per-block link still PREFILLS its own project. ─────────────────────────────
async function runNewCampaignEntry(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });

  // (a) Crumb-row button, Projects: All -> no menu, lands on the campaign page
  // at ① Brief with ONE empty project select and NO crumb picker.
  const homePicker = await page.$('#desk-v1-crumb .desk-v1-projects-picker');
  if (homePicker) ok('R2-2g: Home still shows the crumb Projects: All picker');
  else fail('R2-2g: Home lost the crumb Projects: All picker');
  const before = await page.evaluate(() => window.DeskV1Fixtures.campaigns.length);
  await page.click('.desk-v1-home-newcamp-page-btn');
  await page.waitForSelector('.desk-v1-campaign', { timeout: 4000 });
  const menuOpen = await page.$('.desk-v1-addto-menu');
  if (!menuOpen) ok('R2-2f: page-level "＋ New campaign" opens NO project menu');
  else fail('R2-2f: page-level "＋ New campaign" still opens a menu');
  const top = await page.evaluate(() => {
    const list = window.DeskV1Fixtures.campaigns;
    const c = list[list.length - 1];
    const camp = document.querySelector('.desk-v1-campaign');
    return {
      count: list.length, projectId: c.projectId, state: c.state,
      crumb: (document.querySelector('#desk-v1-crumb .desk-v1-crumb-title') || {}).textContent,
      briefHere: (document.querySelector('.desk-v1-map-stop[data-stop="how"]') || {}).dataset && document.querySelector('.desk-v1-map-stop[data-stop="how"]').dataset.state,
      briefProjectValue: (document.querySelector('select[data-setup-project]') || {}).value,
      projectSelects: document.querySelectorAll('select[data-setup-project]').length,
      crumbPicker: !!document.querySelector('#desk-v1-crumb .desk-v1-projects-picker'),
      pickPill: /Setup\s*[—-]\s*Pick a project/.test(camp ? camp.innerText : ''),
      step1: !!document.querySelector('[data-setup-title]'),
      setupText: /Setup\s+\d\s+of\s+3/.test(document.body.innerText),
    };
  });
  if (top.count === before + 1 && top.state === 'draft' && top.projectId === null) ok('R2-2f: page-level New campaign creates ONE Draft with no project');
  else fail(`R2-2f: page-level New campaign wrong: ${JSON.stringify({ before, top })}`);
  if (/New campaign/.test(top.crumb || '') && top.briefHere === 'here') ok(`R2-18: lands on the campaign page at ① Brief (crumb "${(top.crumb || '').trim()}")`);
  else fail(`R2-18: did not land on Brief: ${JSON.stringify(top)}`);
  // R2-18: the project select is the first thing on Brief (empty, optional); no crumb picker.
  if (top.projectSelects === 1 && top.briefProjectValue === '' && !top.crumbPicker) ok('R2-18: a new campaign page has ONE empty project select (on Brief) and no crumb picker');
  else fail(`R2-18: Brief project select wrong on the new campaign page: ${JSON.stringify(top)}`);
  // R2-3b: IA4's setup steps are gone — no gate, no "Setup n of 3", no step-1 form.
  if (!top.pickPill && !top.step1 && !top.setupText) ok('R2-3b: no "Setup — Pick a project" gate and no IA4 setup step; the draft opens straight on the map');
  else fail(`R2-2g: project-less draft still gated: ${JSON.stringify(top)}`);

  // (b) Per-block link prefills that block's project (Project select shows it).
  await page.evaluate(() => window.deskV1Nav('home', {}));
  await page.waitForSelector('.desk-v1-home-block[data-project-id="engulfing_scanner"]', { timeout: 4000 });
  const before2 = await page.evaluate(() => window.DeskV1Fixtures.campaigns.length);
  await page.click('.desk-v1-home-block[data-project-id="engulfing_scanner"] .desk-v1-home-block-newcamp');
  await page.waitForSelector('.desk-v1-campaign', { timeout: 4000 });
  await page.click('.desk-v1-map-stop[data-stop="launch"]');
  await page.waitForSelector('[data-setup-project]', { timeout: 4000 });
  const blk = await page.evaluate(() => {
    const list = window.DeskV1Fixtures.campaigns;
    const c = list[list.length - 1];
    return { count: list.length, projectId: c.projectId, state: c.state, selected: document.querySelector('[data-setup-project]').value, crumbPicker: !!document.querySelector('#desk-v1-crumb .desk-v1-projects-picker') };
  });
  if (blk.count === before2 + 1 && blk.projectId === 'engulfing_scanner' && blk.state === 'draft' && blk.selected === 'engulfing_scanner' && !blk.crumbPicker) ok('R2-2g: per-block "＋ New campaign" creates a Draft prefilled with that block\'s project (shown on the Launch stop\'s Project select)');
  else fail(`R2-2f: per-block New campaign wrong: ${JSON.stringify({ before2, blk })}`);

  await page.evaluate(() => window.deskV1Nav('project', { projectId: 'engulfing_scanner' }));
  await page.waitForSelector('#desk-v1-crumb .desk-v1-back', { timeout: 4000 });
  if (await page.$('#desk-v1-crumb .desk-v1-projects-picker')) ok('R2-2g: a project page still shows the crumb Projects: All picker');
  else fail('R2-2g: project page lost the crumb Projects: All picker');

  reportUncaught(pageErrors, '[new-campaign]');
  await ctx.close();
}

// ── R2-2f: a draft Home starts is never invisible and never litters. A
// project-less draft the user TOUCHED lists under a "No project yet" block
// after the project blocks; one left completely untouched is discarded when
// the user backs out, so a stray click leaves nothing behind. ────────────────
async function runNoProjectDrafts(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  const noProjBlock = () => page.$('[data-no-project-block]');
  const campCount = () => page.evaluate(() => window.DeskV1Fixtures.campaigns.length);

  if (!(await noProjBlock())) ok('R2-2f: no "No project yet" block while no project-less campaign exists');
  else fail('R2-2f: "No project yet" block shown with nothing to list');

  // Untouched -> Back discards it, no block.
  const n0 = await campCount();
  await page.click('.desk-v1-home-newcamp-page-btn');
  await page.waitForSelector('.desk-v1-map-stop[data-stop="goal"]', { timeout: 4000 });
  if ((await campCount()) === n0 + 1) ok('R2-2f: New campaign created the draft');
  else fail('R2-2f: draft not created');
  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-home-block', { timeout: 4000 });
  if ((await campCount()) === n0 && !(await noProjBlock())) ok('R2-2f: untouched draft + Back leaves nothing behind (campaign removed, no "No project yet" block)');
  else fail(`R2-2f: untouched draft survived Back: count ${await campCount()} vs ${n0}, block=${!!(await noProjBlock())}`);

  // Per-block untouched (project prefilled, nothing changed) -> Back discards too.
  await page.click('.desk-v1-home-block[data-project-id="engulfing_scanner"] .desk-v1-home-block-newcamp');
  await page.waitForSelector('.desk-v1-campaign', { timeout: 4000 });
  // MC-977 G-4: Home -> campaign pushes only the campaign, so ONE Back lands on Home.
  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-home-block', { timeout: 4000 });
  if ((await campCount()) === n0) ok('R2-2f: untouched per-block draft + Back (straight to Home) leaves nothing behind');
  else fail(`R2-2f: untouched per-block draft survived Back: count ${await campCount()} vs ${n0}`);

  // Touched (typed a goal field on the Goal stop) -> Back keeps it, listed
  // under "No project yet".
  await page.evaluate(() => { const c = document.getElementById('toast-container'); if (c) c.innerHTML = ''; }); // a lingering toast would cover the button
  await page.click('.desk-v1-home-newcamp-page-btn');
  await page.click('.desk-v1-map-stop[data-stop="goal"]'); // R2-18: New campaign lands on Brief; the goal field is one stop on
  await page.waitForSelector('[data-goal-field="metric"]', { timeout: 4000 });
  await page.fill('[data-goal-field="metric"]', 'tester signups');
  await page.dispatchEvent('[data-goal-field="metric"]', 'change');
  await page.click('.desk-v1-back');
  await page.waitForSelector('[data-no-project-block]', { timeout: 4000 });
  const listed = await page.evaluate(() => {
    const blocks = Array.from(document.querySelectorAll('.desk-v1-home-block'));
    const blk = document.querySelector('[data-no-project-block]');
    return {
      last: blocks[blocks.length - 1] === blk,
      title: (blk.querySelector('.desk-v1-home-block-name') || {}).textContent,
      rows: blk.querySelectorAll('.desk-v1-home-row').length,
      hasNewLink: !!blk.querySelector('.desk-v1-home-block-newcamp'),
    };
  });
  if (listed.last && /No project yet/.test(listed.title) && listed.rows === 1 && !listed.hasNewLink) ok('R2-2f: a touched project-less draft shows in a "No project yet" block, after the project blocks');
  else fail(`R2-2f: "No project yet" block wrong: ${JSON.stringify(listed)}`);

  // Clicking its row opens the campaign (no project page to push); the
  // Launch stop still offers the (empty) Project select.
  await page.click('[data-no-project-block] .desk-v1-home-row');
  await page.waitForSelector('.desk-v1-campaign', { timeout: 4000 });
  const back = await page.textContent('.desk-v1-back');
  await page.click('.desk-v1-map-stop[data-stop="launch"]');
  await page.waitForSelector('[data-setup-project]', { timeout: 4000 });
  const reopened = await page.evaluate(() => ({ value: document.querySelector('[data-setup-project]').value, back: '' }));
  reopened.back = back;
  if (reopened.value === '' && /Desk/.test(reopened.back || '')) ok('R2-2f: a "No project yet" row opens its campaign (Back reads "Desk", no project page in between)');
  else fail(`R2-2f: reopening a project-less row wrong: ${JSON.stringify(reopened)}`);

  reportUncaught(pageErrors, '[no-project-drafts]');
  await ctx.close();
}

// ── R2-3b (Ron 2026-09-30: "an option to delete a drafted campaign, at least
// from the main Desk page"): only Draft rows carry a trash button; click
// deletes without opening the row and without a confirm, an Undo toast brings
// it back, non-draft rows (active/proposed) have none. ──────────────────────
async function runDraftDelete(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  const count = () => page.evaluate(() => window.DeskV1Fixtures.campaigns.length);

  // Non-draft rows: no trash.
  const nonDraft = await page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll('.desk-v1-home-row')).filter((r) => r.dataset.state !== 'draft');
    return { rows: rows.length, withTrash: rows.filter((r) => r.querySelector('[data-delete-draft]')).length, states: Array.from(new Set(rows.map((r) => r.dataset.state))) };
  });
  if (nonDraft.rows >= 2 && nonDraft.withTrash === 0 && nonDraft.states.includes('active') && nonDraft.states.includes('proposed')) ok(`R2-3b: non-draft rows (${nonDraft.states.join('/')}) have no trash button`);
  else fail(`R2-3b: non-draft rows wrong: ${JSON.stringify(nonDraft)}`);

  // Make a touched draft (typing a goal field) so Back keeps it on Home.
  const n0 = await count();
  await page.click('.desk-v1-home-newcamp-page-btn');
  await page.click('.desk-v1-map-stop[data-stop="goal"]'); // R2-18: New campaign lands on Brief; the goal field is one stop on
  await page.waitForSelector('[data-goal-field="metric"]', { timeout: 4000 });
  await page.fill('[data-goal-field="metric"]', 'tester signups');
  await page.dispatchEvent('[data-goal-field="metric"]', 'change');
  await page.click('.desk-v1-back');
  await page.waitForSelector('[data-no-project-block] .desk-v1-home-row[data-state="draft"]', { timeout: 4000 });
  const trashInfo = await page.evaluate(() => {
    const row = document.querySelector('.desk-v1-home-row[data-state="draft"]');
    const t = row.querySelector('[data-delete-draft]');
    const rr = row.getBoundingClientRect(); const tr = t ? t.getBoundingClientRect() : null;
    return { has: !!t, label: t && t.getAttribute('aria-label'), title: row.querySelector('.desk-v1-home-row-title').textContent, rightGap: tr ? rr.right - tr.right : null, insideRow: !!tr && tr.left >= rr.left && tr.right <= rr.right };
  });
  if (trashInfo.has && trashInfo.label === `Delete draft ${trashInfo.title}` && trashInfo.insideRow && trashInfo.rightGap < 24) ok(`R2-3b: Draft row has a trash at its right edge, aria-label "${trashInfo.label}"`);
  else fail(`R2-3b: draft trash wrong: ${JSON.stringify(trashInfo)}`);

  // Click: deleted, not navigated (still on Home), no confirm sheet.
  await page.click('[data-delete-draft]');
  const after = await page.evaluate(() => ({
    onHome: !!document.querySelector('.desk-v1-home-board'), onCampaign: !!document.querySelector('.desk-v1-campaign'),
    drafts: document.querySelectorAll('.desk-v1-home-row[data-state="draft"]').length, confirm: !!document.querySelector('.desk-v1-rules-confirm-overlay'),
  }));
  if ((await count()) === n0 && after.onHome && !after.onCampaign && after.drafts === 0 && !after.confirm) ok('R2-3b: trash click deletes the draft, stays on Home (row not opened), no confirm dialog');
  else fail(`R2-3b: trash click wrong: ${JSON.stringify({ n0, now: await count(), after })}`);

  // Undo brings the row back.
  await page.locator('.toast-action').last().locator('.toast-btn.primary').click();
  await page.waitForSelector('.desk-v1-home-row[data-state="draft"]', { timeout: 4000 });
  if ((await count()) === n0 + 1) ok('R2-3b: Undo restores the deleted draft row');
  else fail(`R2-3b: Undo did not restore the draft: ${await count()} vs ${n0 + 1}`);

  reportUncaught(pageErrors, '[draft-delete]');
  await ctx.close();
}

// ── Desktop layout (§8 amended row: "each project is its own bordered block
// with a visible gap between blocks", Ron r2 (a)) — each block must actually
// be bordered and have visible daylight before the next one. ────────────────
async function runDesktopLayoutChecks(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });

  const blockLayout = await page.evaluate(() => {
    const blocks = Array.from(document.querySelectorAll('.desk-v1-home-block'));
    const [first, second] = blocks;
    const firstStyle = getComputedStyle(first);
    return {
      count: blocks.length,
      borderStyle: firstStyle.borderStyle,
      borderWidth: parseFloat(firstStyle.borderWidth) || 0,
      gap: second ? second.getBoundingClientRect().top - first.getBoundingClientRect().bottom : null,
    };
  });
  if (blockLayout.borderStyle !== 'none' && blockLayout.borderWidth > 0) {
    ok(`Each project block is bordered (${blockLayout.borderStyle}, ${blockLayout.borderWidth}px)`);
  } else {
    fail(`Project block has no visible border: ${blockLayout.borderStyle} ${blockLayout.borderWidth}px`);
  }
  if (blockLayout.count === 2 && blockLayout.gap !== null && blockLayout.gap > 0) {
    ok(`Visible gap between project blocks: ${blockLayout.gap.toFixed(0)}px`);
  } else {
    fail(`No visible gap between project blocks: ${JSON.stringify(blockLayout)}`);
  }

  reportUncaught(pageErrors, '[desktop-layout]');
  await ctx.close();
}

// ── R2-2c (Dave's review pass 4): one header row, the status board pushed up
// to the top of the page — checked at the exact 1440x900 the review shot was
// taken at, where the old two-row header + promote-box-first ordering pushed
// the board's own column header down to y~537. Acceptance: the column header
// sits above y=320 and both project blocks' own headers are visible without
// scrolling. Also a regression guard for the two things this ticket removed
// outright (the "Clayrune ▾" second-row scope duplicate, "Pause all"). ──────
async function runR2_2cHeaderAcceptance(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} }, { width: 1440, height: 900 });

  const layout = await page.evaluate(() => {
    const colHead = document.querySelector('.desk-v1-home-board-head');
    const blockHeads = Array.from(document.querySelectorAll('.desk-v1-home-block-head'));
    return {
      colHeadTop: colHead ? colHead.getBoundingClientRect().top : null,
      blockHeadCount: blockHeads.length,
      blockHeadBottoms: blockHeads.map((b) => b.getBoundingClientRect().bottom),
      viewportHeight: window.innerHeight,
      hasScopeDupe: !!document.querySelector('.desk-v1-home-scope'),
      hasPauseAll: !!document.querySelector('.desk-v1-home-pause-btn'),
    };
  });
  if (layout.colHeadTop !== null && layout.colHeadTop < 320) {
    ok(`R2-2c: column header row (CAMPAIGN/STAGE/...) sits above y=320 (top ${layout.colHeadTop.toFixed(0)}px)`);
  } else {
    fail(`R2-2c: column header row too low: top ${layout.colHeadTop}`);
  }
  if (layout.blockHeadCount === 2 && layout.blockHeadBottoms.every((b) => b <= layout.viewportHeight)) {
    ok(`R2-2c: both project blocks' headers visible without scrolling at 1440x900 (bottoms ${layout.blockHeadBottoms.map((b) => b.toFixed(0)).join(', ')})`);
  } else {
    fail(`R2-2c: a project block header is below the fold at 1440x900: ${JSON.stringify(layout.blockHeadBottoms)}`);
  }
  if (!layout.hasScopeDupe) ok('R2-2c: Home no longer renders its own "Clayrune ▾" second-row scope (crumb\'s Projects: All ▾ is the only picker)');
  else fail('R2-2c: Home still renders the duplicate "Clayrune ▾" scope button');
  if (!layout.hasPauseAll) ok('R2-2c: "Pause all" is gone from Home (project page owns Pause project)');
  else fail('R2-2c: "Pause all" is still on Home');

  reportUncaught(pageErrors, '[r2-2c-header]');
  await ctx.close();
}

// ── R2-2e (Ron 2026-09-30, live Home after R2-2d): the crumb row is the ONE
// header row — no Settings button in it, "＋ New campaign" sits in it beside
// Engagement, and the "The Desk" h2 (duplicating the crumb title) is gone, at
// 1440 and at 390. R2-2f removed the third R2-2e claim (the project menu that
// button opened was anchored below/right of it and stayed inside the modal):
// the menu no longer exists, so its three position assertions went with it —
// the no-menu behaviour is asserted in runNewCampaignEntry instead. ─────────
async function runR2_2eHeaderCleanup(browser) {
  for (const vp of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const tag = `${vp.width}x${vp.height}`;
    const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} }, vp);

    const hdr = await page.evaluate(() => {
      const tools = document.getElementById('desk-v1-crumb-tools');
      const eng = document.querySelector('.desk-v1-home-engagement-btn');
      const newBtn = document.querySelector('.desk-v1-home-newcamp-page-btn');
      const newStyle = newBtn && getComputedStyle(newBtn);
      return {
        hasSettings: !!(tools && Array.from(tools.querySelectorAll('button')).some((b) => /Settings/.test(b.textContent))) || !!document.querySelector('.desk-v1-home-settings-btn'),
        engTop: eng && eng.getBoundingClientRect().top,
        newTop: newBtn && newBtn.getBoundingClientRect().top,
        newInCrumbTools: !!(tools && newBtn && tools.contains(newBtn)),
        newBg: newStyle && newStyle.backgroundColor,
        hasDeskH2: Array.from(document.querySelectorAll('.desk-v1-home h2')).some((h) => /^\s*The Desk\s*$/.test(h.textContent)),
      };
    });
    if (!hdr.hasSettings) ok(`[${tag}] R2-2e: no Settings button in the Desk crumb row`);
    else fail(`[${tag}] R2-2e: a Settings button is still in the Desk crumb row`);
    if (!hdr.hasDeskH2) ok(`[${tag}] R2-2e: no "The Desk" h2 in the Home body`);
    else fail(`[${tag}] R2-2e: "The Desk" h2 is still in the Home body`);
    if (hdr.newInCrumbTools && !/^rgba\(0, 0, 0, 0\)$/.test(hdr.newBg)) ok(`[${tag}] R2-2e: New campaign lives in the crumb row, still filled (${hdr.newBg})`);
    else fail(`[${tag}] R2-2e: New campaign not in the crumb row or not filled: ${JSON.stringify(hdr)}`);
    if (vp.width >= 1440) {
      if (hdr.engTop !== null && hdr.newTop !== null && Math.abs(hdr.engTop - hdr.newTop) <= 4) ok(`[${tag}] R2-2e: New campaign and Engagement share a row (tops ${hdr.newTop.toFixed(1)} / ${hdr.engTop.toFixed(1)})`);
      else fail(`[${tag}] R2-2e: New campaign not on Engagement's row: ${JSON.stringify(hdr)}`);
    }

    reportUncaught(pageErrors, `[r2-2e-${tag}]`);
    await ctx.close();
  }
}

// ── Phone (§11): Home's content rows stack (they always do now — the campaign
// grid's rail layout that used to need a desktop/phone split is retired), hit
// targets >=44px. R2-2d removed the promote box + shelves, so their phone
// checks went with them. ────────────────────────────────────────────────────
async function runPhoneLayout(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} }, { width: 390, height: 844 });

  const layout = await page.evaluate(() => {
    const board = document.querySelector('.desk-v1-home-board');
    return {
      boardWidth: board.getBoundingClientRect().width,
      homeWidth: document.querySelector('.desk-v1-home').getBoundingClientRect().width,
    };
  });
  if (Math.abs(layout.boardWidth - layout.homeWidth) < 2) ok(`§11: status board is full width on phone, no fixed rail (${layout.boardWidth.toFixed(0)}px)`);
  else fail(`§11: status board is not full width: ${layout.boardWidth}px vs ${layout.homeWidth}px column`);

  // .desk-v1-home-needsyou-pill is deliberately smaller (32px, CSS comment
  // at desk-v1.css's phone media query) — it's a secondary jump-straight-in
  // control nested inside the row, which is itself the 44px primary target.
  const hitTargets = await page.evaluate(() => {
    const els = [
      document.querySelector('.desk-v1-home-newcamp-page-btn'),
      document.querySelector('.desk-v1-home-row'),
    ].filter(Boolean);
    return els.map((el) => el.getBoundingClientRect().height);
  });
  if (hitTargets.length === 2 && hitTargets.every((h) => h >= 44)) ok(`§11: header/row hit targets all >=44px (${hitTargets.map((h) => h.toFixed(0)).join(', ')})`);
  else fail(`§11: a hit target is under 44px: ${JSON.stringify(hitTargets)}`);

  reportUncaught(pageErrors, '[phone]');
  await ctx.close();
}

// ── item 1 (MC-977 R0 UX pass, Ron 2026-09-28: "the menu opens on the full
// dashboard size"). desk-v1-shell.js used to call toggleModalMaximize() on
// open; removed so Desk rides the SAME sizing convention every other project
// modal uses (interactions.js's snap machinery, no bespoke full-size mode).
// Checked at a wide (2000px) window — the width the screenshot that reported
// this bug was taken at. ────────────────────────────────────────────────────
async function runModalSizeCheck(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} }, { width: 2000, height: 1100 });

  const size = await page.evaluate(() => {
    const win = document.querySelector('.modal-window[data-modal-id="__desk"]');
    const r = win.getBoundingClientRect();
    return { isMaximized: win.classList.contains('is-maximized'), width: r.width, height: r.height };
  });
  !size.isMaximized
    ? ok(`item 1: Desk does not force-maximize on open (no .is-maximized class)`)
    : fail('item 1: Desk still opens maximized');
  size.width < 2000 - 200
    ? ok(`item 1: Desk modal is bounded on a 2000px window (${size.width.toFixed(0)}px wide, not edge-to-edge)`)
    : fail(`item 1: Desk modal stretched edge-to-edge on a 2000px window: ${size.width.toFixed(0)}px wide`);

  await page.screenshot({ path: resolve(SHOT_DIR, 'ux_item1_home_2000.png') });
  ok('desktop (2000px) screenshot saved: ux_item1_home_2000.png');

  reportUncaught(pageErrors, '[modal-size]');
  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  for (const tone of TONES) await runToneRenderChecks(browser, tone);
  await runNeedsYouDeepLinks(browser);
  await runNewCampaignEntry(browser);
  await runNoProjectDrafts(browser);
  await runDraftDelete(browser);
  await runDesktopLayoutChecks(browser);
  await runR2_2cHeaderAcceptance(browser);
  await runR2_2eHeaderCleanup(browser);
  await runPhoneLayout(browser);
  await runModalSizeCheck(browser);
  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error('❌ FAIL — smoke harness error: ' + (e && e.message ? e.message : e));
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}

console.log(bad
  ? `\n❌ FAIL — ${bad} Desk v1 home check(s) regressed.`
  : '\n✅ PASS — Desk v1 T1 home: A13 (heartbeat -> hold row), R2-2d (mockup parity, no shelves/promote), A12 (Needs-you deep-links), phone layout all hold.');
process.exit(exitCode);
