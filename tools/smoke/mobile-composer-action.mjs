#!/usr/bin/env node
/**
 * Mobile composer, WhatsApp style (<=960px) — headless behaviour test.
 *
 * ONE round action button sits OUTSIDE the text field on the right: the MIC
 * while the field is empty, the SEND arrow once there is text or a queued
 * attachment, back to the mic when cleared. Inside the field, right: 📎 and a
 * 📷 camera button (file input accept=image/* capture=environment feeding the
 * existing agentPendingImages path). Desktop keeps its layout.
 *
 * Covers both mobile composers: the +New dispatch row and the in-chat
 * follow-up row (a RUNNING chat, to prove Stop in the pane header is untouched
 * while the slot swaps).
 *
 * Hermetic: real index.html + real static/*, no server.
 * RUN:  node tools/smoke/mobile-composer-action.mjs
 *       SHOT_DIR=_scratch/composer-shots node tools/smoke/mobile-composer-action.mjs
 */
import { readFileSync, readdirSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const SHOT_DIR = process.env.SHOT_DIR || '';
const ORIGIN = 'http://mc.smoke.test';
const PID = 'smoke_composer';
const SID = 'sess-composer';

const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));

const project = {
  id: PID, name: 'Composer Smoke', status: 'active', domain: 'general', emoji: '🧪',
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
// 1x1 transparent PNG.
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64');

const failures = [];
function check(name, cond, detail) {
  if (cond) console.log(`✅ ${name}`);
  else { console.error(`❌ ${name}${detail ? ` — ${detail}` : ''}`); failures.push(name); }
}

const browser = await chromium.launch();

async function openPage(viewport) {
  const ctx = await browser.newContext({ viewport, hasTouch: viewport.width <= 960 });
  const page = await ctx.newPage();
  page.__errors = [];
  page.on('pageerror', (e) => {
    const m = e.message || String(e);
    if (/dynamically imported module/.test(m) && /cdn\./.test(m)) return;
    page.__errors.push(m);
  });
  // Headless Chromium may not expose a speech backend; the mic only renders
  // when one exists, so guarantee it.
  await page.addInitScript(() => {
    if (!window.SpeechRecognition && !window.webkitSpeechRecognition) {
      window.webkitSpeechRecognition = function () { this.start = () => {}; this.stop = () => {}; this.abort = () => {}; };
    }
  });
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
    agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Composer Smoke', task: 'a task', status, startedAt: now });
    agentStatusCache[sid] = { status, task: 'a task', projectId: pid, startedAt: now, claudeSessionId: 'csid-composer',
      character: { name: 'rusk', agent_name: 'Rusk' }, provider: 'claude' };
    agentOutputBuffers[sid] = ['> hello', 'assistant line'];
    conversationsCache[pid] = [{ claude_session_id: 'csid-composer', mc_session_id: sid, character: null, mtime: 1000, ts_relative: 'just now', status, turns: 1, label: 'a task', first_user: 'a task', last_user: 'a task' }];
    openProjectModal(pid);
    openConversation(pid, 'csid-composer', sid, status === 'running');
  }, { pid: PID, sid: SID, status });
  await page.waitForSelector(`#agent-followup-${SID}`, { timeout: 5000 });
  await page.waitForTimeout(700);
}

// Reads the slot's visible state: which of mic/send actually shows, and where
// they sit relative to the field.
const probe = (page, taId) => page.evaluate((id) => {
  const ta = document.getElementById(id);
  const row = ta.closest('.agent-input-row, .agent-chat-input-row');
  const field = ta.closest('.composer-field');
  const slot = row.querySelector('.composer-action');
  const vis = (el) => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0; };
  const mic = row.querySelector('.btn-mic');
  const send = row.querySelector('.btn-send-arrow');
  const attach = field && field.querySelector('.btn-attach:not(.btn-camera)');
  const camera = field && field.querySelector('.btn-camera');
  const fr = field && field.getBoundingClientRect();
  const sr = slot && slot.getBoundingClientRect();
  const tr = ta.getBoundingClientRect();
  return {
    hasField: !!field, hasSlot: !!slot,
    micVisible: vis(mic), sendVisible: vis(send),
    micInsideField: !!(field && mic && field.contains(mic)),
    sendInsideField: !!(field && send && field.contains(send)),
    attachInField: vis(attach), cameraInField: vis(camera),
    fieldRight: fr && fr.right, slotLeft: sr && sr.left, fieldW: fr && fr.width, taW: tr.width,
    rowW: row.getBoundingClientRect().width,
    camAccept: row.querySelector('input[capture]') && row.querySelector('input[capture]').getAttribute('accept'),
    camCapture: row.querySelector('input[capture]') && row.querySelector('input[capture]').getAttribute('capture'),
    placeholder: ta.placeholder,
  };
}, taId);

async function runCycle(page, label, taId, imgKey) {
  const ta = `#${taId}`;
  let m = await probe(page, taId);
  check(`${label}: field + action slot rendered`, m.hasField && m.hasSlot);
  check(`${label}: empty → mic shows, send hidden`, m.micVisible && !m.sendVisible, JSON.stringify(m));
  check(`${label}: action button sits OUTSIDE the field, right of it`,
    !m.micInsideField && m.slotLeft >= m.fieldRight - 1, `slotLeft=${m.slotLeft} fieldRight=${m.fieldRight}`);
  check(`${label}: paperclip and camera are inside the field`, m.attachInField && m.cameraInField);
  check(`${label}: camera input is accept=image/* capture=environment`,
    m.camAccept === 'image/*' && m.camCapture === 'environment', `${m.camAccept}/${m.camCapture}`);
  check(`${label}: field takes the freed width (>= 60% of row)`, m.fieldW >= m.rowW * 0.6, `field=${m.fieldW} row=${m.rowW}`);

  await page.click(ta);
  await page.keyboard.type('hello');
  m = await probe(page, taId);
  check(`${label}: typing → send shows, mic hidden`, m.sendVisible && !m.micVisible, JSON.stringify(m));
  check(`${label}: send is outside the field too`, !m.sendInsideField);

  await page.fill(ta, '');
  m = await probe(page, taId);
  check(`${label}: cleared → mic again`, m.micVisible && !m.sendVisible, JSON.stringify(m));

  // Whitespace alone is not a message.
  await page.fill(ta, '   ');
  m = await probe(page, taId);
  check(`${label}: whitespace only → still mic`, m.micVisible && !m.sendVisible);
  await page.fill(ta, '');

  // A queued attachment alone flips to send (the picker path rebuilds the modal).
  await page.evaluate((key) => {
    agentPendingImages[key] = [{ file: new File([new Uint8Array([1])], 'a.png', { type: 'image/png' }), objectUrl: null, serverPath: null, isDocument: true, fileName: 'a.png' }];
    refreshModal();
  }, imgKey);
  await page.waitForTimeout(300);
  m = await probe(page, taId);
  check(`${label}: attachment queued, field empty → send shows`, m.sendVisible && !m.micVisible, JSON.stringify(m));
  await page.evaluate((key) => { delete agentPendingImages[key]; refreshModal(); }, imgKey);
  await page.waitForTimeout(300);
  m = await probe(page, taId);
  check(`${label}: attachment removed → mic again`, m.micVisible && !m.sendVisible, JSON.stringify(m));

  // Typed text survives a rebuild and the slot follows it.
  await page.fill(ta, 'keep me');
  await page.evaluate(() => refreshModal());
  await page.waitForTimeout(300);
  m = await probe(page, taId);
  check(`${label}: text survives refreshModal and send stays shown`, m.sendVisible && !m.micVisible, JSON.stringify(m));
  await page.fill(ta, '');

  // Camera feeds the existing attachment path.
  await page.setInputFiles(`#agent-camera-input-${imgKey}`, { name: 'shot.png', mimeType: 'image/png', buffer: PNG });
  await page.waitForTimeout(300);
  const queued = await page.evaluate((key) => (agentPendingImages[key] || []).length, imgKey);
  check(`${label}: camera capture lands in agentPendingImages[${imgKey}]`, queued === 1, `queued=${queued}`);
  m = await probe(page, taId);
  check(`${label}: ...and flips the slot to send`, m.sendVisible && !m.micVisible);
  await page.evaluate((key) => { delete agentPendingImages[key]; refreshModal(); }, imgKey);
  await page.waitForTimeout(200);
}

// ── Mobile: +New dispatch composer ─────────────────────────────────────────
{
  const page = await openPage({ width: 390, height: 844 });
  await openNewComposer(page);
  if (SHOT_DIR) { mkdirSync(SHOT_DIR, { recursive: true }); await page.screenshot({ path: resolve(SHOT_DIR, 'new-empty.png') }); }
  await runCycle(page, 'mobile +New', `agent-task-${PID}`, PID);
  await page.fill(`#agent-task-${PID}`, 'hello there');
  if (SHOT_DIR) await page.screenshot({ path: resolve(SHOT_DIR, 'new-typed.png') });
  const ph = (await probe(page, `agent-task-${PID}`)).placeholder;
  check('mobile +New: short one-line placeholder', ph === 'Describe a task…', ph);
  check('mobile +New: no uncaught exceptions', page.__errors.length === 0, page.__errors.join(' | '));
  await page.context().close();
}

// ── Mobile: follow-up composer, completed and RUNNING ──────────────────────
for (const status of ['completed', 'running']) {
  const page = await openPage({ width: 390, height: 844 });
  await openFollowupComposer(page, status);
  if (SHOT_DIR) await page.screenshot({ path: resolve(SHOT_DIR, `followup-${status}-empty.png`) });
  await runCycle(page, `mobile follow-up (${status})`, `agent-followup-${SID}`, `fu_${SID}`);
  if (status === 'running') {
    const stop = await page.evaluate((sid) => {
      const b = document.querySelector(`#stop-btn-${sid} .btn-stop`);
      if (!b) return { present: false };
      const r = b.getBoundingClientRect();
      return { present: true, visible: r.width > 0 && r.height > 0, inComposer: !!b.closest('.agent-chat-input-row') };
    }, SID);
    check('running: Stop stays in the pane header, visible, not in the composer row',
      stop.present && stop.visible && !stop.inComposer, JSON.stringify(stop));
    await page.fill(`#agent-followup-${SID}`, 'redirect');
    const stop2 = await page.evaluate((sid) => !!document.querySelector(`#stop-btn-${sid} .btn-stop`), SID);
    check('running: Stop still present while send is showing', stop2);
    const ph = (await probe(page, `agent-followup-${SID}`)).placeholder;
    check('running: short one-line placeholder', ph === 'Redirect agent…', ph);
  }
  check(`mobile follow-up (${status}): no uncaught exceptions`, page.__errors.length === 0, page.__errors.join(' | '));
  await page.context().close();
}

// ── Desktop: layout untouched ──────────────────────────────────────────────
{
  const page = await openPage({ width: 1280, height: 800 });
  await openNewComposer(page);
  const d = await page.evaluate((pid) => {
    const ta = document.getElementById(`agent-task-${pid}`);
    const row = ta.closest('.agent-input-row');
    return {
      field: !!row.querySelector('.composer-field'), slot: !!row.querySelector('.composer-action'),
      camera: !!row.querySelector('.btn-camera'),
      mic: !!row.querySelector('.btn-mic'), send: !!row.querySelector('.btn-dispatch'),
      arrow: !!row.querySelector('.btn-send-arrow'),
    };
  }, PID);
  check('desktop: no mobile field/slot/camera, mic + Dispatch unchanged',
    !d.field && !d.slot && !d.camera && d.mic && d.send && !d.arrow, JSON.stringify(d));
  check('desktop: no uncaught exceptions', page.__errors.length === 0, page.__errors.join(' | '));
  await page.context().close();
}

await browser.close();
if (failures.length) {
  console.error(`\n❌ FAIL — ${failures.length} behaviour(s) broken: ${failures.join(', ')}`);
  process.exit(1);
}
console.log('\n✅ PASS — mobile composer swaps mic ⇄ send and the desktop layout is untouched.');
