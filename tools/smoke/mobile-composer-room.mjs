#!/usr/bin/env node
/**
 * Mobile composer ROOM (<=960px) — headless layout test.
 *
 * Ron, 2026-10-02, real Android phone: the WhatsApp-style composer was cramped —
 * the placeholder 'Interrupt and redirect agent...' showed as 'Interrupt and'
 * plus a half-clipped second row, there was no emoji button, and the pill sat
 * inside an outer bordered box so two frames ate the width.
 *
 * Asserts at 360 / 390 / 412 wide, light + dark, for the +New composer and the
 * in-chat follow-up (completed and running):
 *   * emoji (left), paperclip, camera and the round action button are all
 *     visible and inside the viewport;
 *   * the placeholder fits ONE line (measured text width <= content width),
 *     and the empty field is exactly one line tall — no clipped second row;
 *   * typing hides the camera, clearing brings it back;
 *   * the field grows with content to ~5 lines, then scrolls;
 *   * the emoji button opens the picker inside the viewport and above the pill,
 *     it inserts, and a height-only resize (keyboard) keeps it open + on screen;
 *   * no outer frame: the chat box has no border, the row is wide.
 *
 * Hermetic: real index.html + real static/*, no server.
 * RUN:  node tools/smoke/mobile-composer-room.mjs
 *       SHOT_DIR=docs/screenshots node tools/smoke/mobile-composer-room.mjs
 */
import { readFileSync, readdirSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const SHOT_DIR = process.env.SHOT_DIR || '';
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_room';
const SID = 'sess-room';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

const project = {
  id: PID, name: 'Composer Room', status: 'active', domain: 'general', emoji: '🧪',
  description: '', summary: '', current_task: 'Idle', next_action: '',
  blocked: false, blocked_reason: null, activity_log: [], backlog: [],
  project_path: '/smoke/' + PID, last_updated: '2026-10-02T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null,
  display_order: 0, provider: 'claude', use_streaming_agent: true,
  distiller_mode: 'proposed', distiller_min_recurrence: 3,
  distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
  distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
  distiller_skip_errors: true,
};
const PROJECTS_JSON = JSON.stringify([project]);

const failures = [];
function check(name, cond, detail) {
  if (cond) console.log(`✅ ${name}`);
  else { console.error(`❌ ${name}${detail ? ` — ${detail}` : ''}`); failures.push(name); }
}

const browser = await chromium.launch();

async function openPage(viewport, tone) {
  const ctx = await browser.newContext({ viewport, hasTouch: true });
  const page = await ctx.newPage();
  page.__errors = [];
  page.on('pageerror', (e) => {
    const m = e.message || String(e);
    if (/dynamically imported module/.test(m) && /cdn\./.test(m)) return;
    page.__errors.push(m);
  });
  await page.addInitScript((t) => {
    localStorage.setItem('mc_tone', t);
    if (!window.SpeechRecognition && !window.webkitSpeechRecognition) {
      window.webkitSpeechRecognition = function () { this.start = () => {}; this.stop = () => {}; this.abort = () => {}; };
    }
  }, tone);
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  return page;
}

async function openNewComposer(page) {
  await page.evaluate(async (pid) => {
    openProjectModal(pid);
    window.agentConvNew = window.agentConvNew || {};
    agentConvNew[pid] = true;
    if (typeof refreshModal === 'function') refreshModal();
    await new Promise(r => setTimeout(r, 600));
  }, PID);
  await page.waitForSelector(`#agent-task-${PID}`, { timeout: 10000 });
}

async function openFollowupComposer(page, status) {
  await page.evaluate(({ pid, sid, status }) => {
    const now = new Date().toISOString();
    agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Composer Room', task: 'a task', status, startedAt: now });
    agentStatusCache[sid] = { status, task: 'a task', projectId: pid, startedAt: now, claudeSessionId: 'csid-room',
      character: { name: 'rusk', agent_name: 'Rusk' }, provider: 'claude' };
    agentOutputBuffers[sid] = ['> hello', 'assistant line'];
    conversationsCache[pid] = [{ claude_session_id: 'csid-room', mc_session_id: sid, character: null, mtime: 1000, ts_relative: 'just now', status, turns: 1, label: 'a task', first_user: 'a task', last_user: 'a task' }];
    openProjectModal(pid);
    openConversation(pid, 'csid-room', sid, status === 'running');
  }, { pid: PID, sid: SID, status });
  await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });
  await page.waitForTimeout(700);
}

// Geometry + placeholder-fit probe for one composer.
const probe = (page, taId) => page.evaluate((id) => {
  const ta = document.getElementById(id);
  const row = ta.closest('.agent-input-row, .agent-chat-input-row');
  const field = ta.closest('.composer-field');
  const vw = window.innerWidth, vh = window.innerHeight;
  const geo = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    const shown = cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0;
    return { shown, inside: shown && r.left >= 0 && r.right <= vw && r.top >= 0 && r.bottom <= vh, w: r.width, h: r.height, left: r.left, right: r.right };
  };
  const cs = getComputedStyle(ta);
  const padX = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
  // Measure the placeholder with the textarea's own font.
  const c = document.createElement('canvas').getContext('2d');
  c.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  const phW = c.measureText(ta.placeholder).width;
  const act = row.querySelector('.composer-action');
  const actBtn = act && [...act.children].find((b) => getComputedStyle(b).display !== 'none');
  return {
    emoji: geo(field.querySelector('.btn-emoji')),
    clip: geo(field.querySelector('.btn-attach:not(.btn-camera)')),
    camera: geo(field.querySelector('.btn-camera')),
    action: geo(actBtn),
    field: geo(field), ta: geo(ta), row: geo(row),
    emojiFirst: field.firstElementChild && field.firstElementChild.classList.contains('btn-emoji'),
    placeholder: ta.placeholder, phW, contentW: ta.clientWidth - padX,
    taH: ta.offsetHeight, taScrollH: ta.scrollHeight, taClientH: ta.clientHeight,
    chatBorder: (() => { const ch = ta.closest('.agent-chat'); return ch ? getComputedStyle(ch).borderTopWidth : null; })(),
    emojiOutsideField: [...row.querySelectorAll('.btn-emoji')].filter((b) => !field.contains(b)).length,
  };
}, taId);

async function runComposer(page, label, taId, shotName) {
  const ta = `#${taId}`;
  const m = await probe(page, taId);
  const detail = JSON.stringify({ e: m.emoji, c: m.clip, k: m.camera, a: m.action });
  check(`${label}: emoji, clip, camera, action all visible and inside the viewport`,
    m.emoji && m.emoji.inside && m.clip && m.clip.inside && m.camera && m.camera.inside && m.action && m.action.inside, detail);
  check(`${label}: emoji is the first thing in the pill (left) and none sits outside it`,
    m.emojiFirst && m.emojiOutsideField === 0, `first=${m.emojiFirst} outside=${m.emojiOutsideField}`);
  check(`${label}: tap targets >= 40px`,
    [m.emoji, m.clip, m.camera].every((g) => g && g.w >= 40 && g.h >= 40) && m.action.w >= 44,
    `emoji=${m.emoji.w}x${m.emoji.h} clip=${m.clip.w}x${m.clip.h} cam=${m.camera.w}x${m.camera.h} act=${m.action.w}`);
  check(`${label}: placeholder "${m.placeholder}" fits one line (${m.phW.toFixed(0)}px <= ${m.contentW.toFixed(0)}px)`,
    m.phW <= m.contentW, `phW=${m.phW} contentW=${m.contentW}`);
  check(`${label}: empty field is exactly one line (no clipped second row)`,
    m.taH <= 48 && m.taScrollH <= m.taClientH + 1, `h=${m.taH} scrollH=${m.taScrollH} clientH=${m.taClientH}`);
  check(`${label}: no outer frame (chat box border 0), row spans >= 85% of the viewport`,
    (m.chatBorder === null || m.chatBorder === '0px') && m.row.w >= (await page.evaluate(() => innerWidth)) * 0.85,
    `border=${m.chatBorder} rowW=${m.row.w}`);
  if (shotName && SHOT_DIR) { mkdirSync(SHOT_DIR, { recursive: true }); await page.screenshot({ path: resolve(SHOT_DIR, shotName) }); }

  // Camera steps aside while there is text.
  await page.click(ta);
  await page.keyboard.type('hello');
  let t = await probe(page, taId);
  check(`${label}: text present → camera hidden`, !t.camera.shown, JSON.stringify(t.camera));
  check(`${label}: ...and typing gets the room (field wider text area)`, t.ta.w > m.ta.w + 30, `${m.ta.w} -> ${t.ta.w}`);
  check(`${label}: one short line stays one line tall`, t.taH === m.taH, `${m.taH} -> ${t.taH}`);
  if (shotName && SHOT_DIR) await page.screenshot({ path: resolve(SHOT_DIR, shotName.replace('.png', '-typed.png')) });

  // Grows with content up to ~5 lines, then scrolls.
  await page.fill(ta, Array.from({ length: 9 }, (_, i) => `line ${i + 1}`).join('\n'));
  t = await probe(page, taId);
  check(`${label}: 9 lines → grows, capped near 5 lines, then scrolls`,
    t.taH > m.taH + 30 && t.taH <= 124 && t.taScrollH > t.taClientH, `h=${t.taH} scrollH=${t.taScrollH} clientH=${t.taClientH}`);
  if (shotName && SHOT_DIR) await page.screenshot({ path: resolve(SHOT_DIR, shotName.replace('.png', '-multiline.png')) });
  check(`${label}: grown field keeps icons + action inside the viewport`,
    t.emoji.inside && t.clip.inside && t.action.inside);
  await page.fill(ta, '');
  t = await probe(page, taId);
  check(`${label}: cleared → camera back, field back to one line`, t.camera.shown && t.taH === m.taH, `cam=${t.camera.shown} h=${t.taH}`);

  // Emoji picker from the pill.
  const emojiBtn = `#btn-emoji-${taId}`;
  await page.tap(emojiBtn);
  await page.waitForSelector('.chat-emoji-pop', { timeout: 2000 });
  const pickerGeo = () => page.evaluate((id) => {
    const p = document.querySelector('.chat-emoji-pop'); if (!p) return null;
    const r = p.getBoundingClientRect();
    const f = document.getElementById(id).closest('.composer-field').getBoundingClientRect();
    return { l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, pillTop: f.top, vw: innerWidth, vh: innerHeight };
  }, taId);
  let g = await pickerGeo();
  check(`${label}: emoji picker opens inside the viewport and above the pill`,
    g && g.l >= 0 && g.r <= g.vw && g.t >= 0 && g.b <= g.pillTop, JSON.stringify(g));
  check(`${label}: picker cells are comfortable tap targets (>= 36px)`,
    await page.evaluate(() => document.querySelector('.cep-cell').getBoundingClientRect().width >= 36));
  if (shotName && SHOT_DIR) await page.screenshot({ path: resolve(SHOT_DIR, shotName.replace('.png', '-emoji.png')) });
  const first = await page.locator('.cep-cell').first().getAttribute('data-emoji');
  await page.locator('.cep-cell').first().tap();
  check(`${label}: tapping a cell inserts it`, (await page.inputValue(ta)) === first, JSON.stringify(await page.inputValue(ta)));
  check(`${label}: picker stays open for more picks`, await page.locator('.chat-emoji-pop').count() === 1);
  // Keyboard opening = height-only resize: picker re-places, stays on screen.
  const vp = page.viewportSize();
  await page.setViewportSize({ width: vp.width, height: Math.round(vp.height * 0.55) });
  await page.waitForTimeout(900);
  g = await pickerGeo();
  check(`${label}: short viewport (keyboard) keeps the picker open, on screen, above the pill`,
    g && g.t >= 0 && g.b <= g.pillTop && g.b <= g.vh, JSON.stringify(g));
  await page.setViewportSize(vp);
  await page.waitForTimeout(100);
  await page.mouse.click(5, 5);
  await page.waitForTimeout(100);
  check(`${label}: outside tap closes it`, await page.locator('.chat-emoji-pop').count() === 0);
  await page.fill(ta, '');
}

const WIDTHS = [360, 390, 412];
for (const tone of ['warm', 'dark']) {
  for (const width of WIDTHS) {
    const tag = `${width}px ${tone}`;
    const shot = (n) => (tone === 'warm' ? `mobile-composer-room-${width}-${n}.png` : `mobile-composer-room-${width}-dark-${n}.png`);
    // +New
    {
      const page = await openPage({ width, height: 800 }, tone);
      await openNewComposer(page);
      await runComposer(page, `${tag} +New`, `agent-task-${PID}`, width === 360 || width === 390 ? shot('new') : null);
      check(`${tag} +New: no uncaught exceptions`, page.__errors.length === 0, page.__errors.join(' | '));
      await page.context().close();
    }
    // Follow-up, completed + running
    for (const status of ['completed', 'running']) {
      const page = await openPage({ width, height: 800 }, tone);
      await openFollowupComposer(page, status);
      await runComposer(page, `${tag} follow-up (${status})`, `agent-followup-${SID}`,
        (width === 360 || width === 390) && status === 'running' ? shot('followup-running') : null);
      check(`${tag} follow-up (${status}): no uncaught exceptions`, page.__errors.length === 0, page.__errors.join(' | '));
      await page.context().close();
    }
  }
}

// Desktop untouched: emoji button stays outside any pill, camera/field absent.
{
  const page = await openPage({ width: 1280, height: 800 }, 'warm');
  await openNewComposer(page);
  const d = await page.evaluate((pid) => {
    const row = document.getElementById(`agent-task-${pid}`).closest('.agent-input-row');
    const e = row.querySelector('.btn-emoji');
    return { field: !!row.querySelector('.composer-field'), emojiShown: !!e && e.getBoundingClientRect().width > 0 };
  }, PID);
  check('desktop: emoji button still shown, no mobile pill', d.emojiShown && !d.field, JSON.stringify(d));
  await page.context().close();
}

await browser.close();
if (failures.length) {
  console.error(`\n❌ FAIL — ${failures.length} check(s) broken: ${[...new Set(failures)].slice(0, 12).join(' | ')}`);
  process.exit(1);
}
console.log('\n✅ PASS — mobile composer has room: emoji in the pill, one-line placeholder, camera yields to text.');
