#!/usr/bin/env node
/**
 * Terminal pop-out fixes (Ron, 2026-10-03): a real-PTY session opened from the
 * agent's `[terminal:<sid>:<cmd>]` chat marker must be typeable, its Send box
 * must submit with Enter, and the window must widen without clipping the TUI.
 *
 * WHY THIS EXISTS
 * ----------------
 * Three defects, one pop-out:
 *   1. The marker handler (resume-preview.js) opened the pop-out WITHOUT is_pty,
 *      so a PTY session rendered as a pipe terminal: xterm disableStdin, no
 *      onData wiring, nothing the user typed reached the process.
 *   2. The Send box appended '\n'. A TUI on a PTY submits on Enter ('\r').
 *   3. xterm was fit to a container wider than the window's visible box, so the
 *      right-hand columns were clipped (box-drawing, logos, long lines).
 *
 * REAL BACKEND, OWN INSTANCE: spawns a second server.py from THIS checkout on
 * its own port with a disposable MC_DATA_DIR and a throwaway project, so
 * /api/terminal/* (launch, SSE stream, stdin, resize) hit a REAL PTY and the
 * served SPA is this checkout's. It never touches the operator's live server:
 * a pty launched there auto-pops a terminal window on their dashboard (which
 * is how an earlier version of this smoke stacked five over their chat). The
 * agent SSE stream is faked so the real `[terminal:...]` marker handler runs.
 *
 * Needs: .venv python in the checkout, and xterm.js from jsdelivr (the
 * pop-out loads it lazily).
 *
 * RUN   node tools/smoke/terminal-pty-popout.mjs
 * Exit  0 = all checks hold; 1 = a regression (or the backend is unavailable).
 */
import { writeFileSync, mkdirSync, mkdtempSync, appendFileSync, existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';
import { tmpdir } from 'node:os';
import { spawn } from 'node:child_process';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const PORT = Number(process.env.MC_SMOKE_PORT || 5321);
const BASE = `http://127.0.0.1:${PORT}`;
const PID = 'smoke_term';
const AGENT_SID = 'smoke-agent-session';
const PYTHON = resolve(REPO_ROOT, '.venv', 'Scripts', 'python.exe');
const RUN_ID = `${Date.now()}-${process.pid}`;
const DATA_DIR = resolve(REPO_ROOT, '_scratch', `mc_term_smoke_data_${RUN_ID}`);
const LOG_FILE = resolve(REPO_ROOT, '_scratch', `mc_term_smoke_server_${RUN_ID}.log`);

const fail = (m) => { console.error('FAIL ' + m); process.exitCode = 1; };
const ok = (m) => console.log('ok   ' + m);
const check = (cond, m) => (cond ? ok(m) : fail(m));

// A harmless interactive child: announces itself, then echoes each line it
// reads as GOT:<repr>. Written to a temp file so no shell quoting is involved.
const dir = mkdtempSync(join(tmpdir(), 'mc-pty-smoke-'));
const script = join(dir, 'pty_echo.py');
writeFileSync(script, [
  'import sys',
  'print("PTY_READY", flush=True)',
  'while True:',
  '    l = sys.stdin.readline()',
  '    if not l: break',
  '    print("GOT:" + repr(l), flush=True)',
  '',
].join('\n'));

const post = async (path, body) => {
  const r = await fetch(BASE + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return { status: r.status, body: await r.json().catch(() => ({})) };
};

// Register the child with the operator's Process Manager (it is OUR process,
// and registering never opens a window). Best-effort.
async function registerOwned(pid, name, command) {
  try {
    await fetch(process.env.MC_TEST_PROCESS_REGISTER_URL || 'http://localhost:5199/api/processes/register', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ pid, name, project_id: process.env.MC_TEST_PROCESS_PROJECT || 'mission_control', command }),
    });
  } catch (e) { console.error(`process registration unreachable for pid ${pid}: ${e}`); }
}

// ── own server instance: own port, disposable data dir, throwaway project ────
let serverProc, browser, SID = null;
const killServer = () => { if (serverProc && serverProc.pid) { try { process.kill(serverProc.pid, 'SIGTERM'); } catch { /* gone */ } } };
if (!existsSync(PYTHON)) { console.error(`FAIL checkout venv python not found: ${PYTHON}`); process.exit(1); }
const projDir = join(DATA_DIR, 'data', 'projects');
mkdirSync(projDir, { recursive: true });
const fixture = JSON.parse(readFileSync(resolve(__dirname, 'fixtures', 'projects.json'), 'utf8'))[0];
writeFileSync(join(projDir, `${PID}.json`), JSON.stringify({ ...fixture, id: PID, name: 'Terminal smoke (throwaway)', project_path: '' }, null, 2));
serverProc = spawn(PYTHON, ['server.py'], {
  cwd: REPO_ROOT,
  env: { ...process.env, MC_PORT: String(PORT), MC_DATA_DIR: DATA_DIR, MC_REMOTE_ENABLED: '0' },
  stdio: ['ignore', 'pipe', 'pipe'],
});
serverProc.stdout.on('data', d => appendFileSync(LOG_FILE, d));
serverProc.stderr.on('data', d => appendFileSync(LOG_FILE, d));
await registerOwned(serverProc.pid, 'terminal-pty-popout smoke: second MC instance', `MC_PORT=${PORT} MC_DATA_DIR=${DATA_DIR} MC_REMOTE_ENABLED=0 ${PYTHON} server.py`);
let up = false;
for (let i = 0; i < 40 && !up; i++) {
  try { up = (await fetch(BASE + '/api/config')).ok; } catch { /* not up yet */ }
  if (!up) await new Promise(r => setTimeout(r, 500));
}
if (!up) { console.error(`FAIL second instance on :${PORT} never came up — see ${LOG_FILE}`); killServer(); process.exit(1); }
console.log(`second instance up on :${PORT} (own MC_DATA_DIR, project ${PID})`);

const launch = await post('/api/terminal/launch', { project_id: PID, command: `python -u ${script}`, pty: true });
if (launch.status !== 200 || !launch.body.session_id) {
  console.error(`FAIL could not launch a pty session on ${BASE}: ${launch.status} ${JSON.stringify(launch.body)}`);
  killServer(); process.exit(1);
}
SID = launch.body.session_id;
console.log(`launched pty session ${SID}`);

browser = await chromium.launch({ ignoreDefaultArgs: ['--hide-scrollbars'] }); // keep classic scrollbars: the clipped-grid defect hides under one
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
const page = await ctx.newPage();
const pageErrors = [];
page.on('pageerror', e => pageErrors.push(String(e)));

// Fake agent stream: one line, the marker the launch endpoint appends.
await page.route(`${BASE}/api/project/${PID}/agent/stream**`, r => r.fulfill({
  status: 200, contentType: 'text/event-stream',
  body: `data: ${JSON.stringify({ type: 'output', text: `[terminal:${SID}:python pty_echo.py]`, seq: 1 })}

`,
}));

const stdinPosts = [], resizePosts = [];
page.on('request', req => {
  if (req.method() !== 'POST') return;
  if (req.url().endsWith('/api/terminal/stdin')) stdinPosts.push(JSON.parse(req.postData() || '{}').text);
  if (req.url().endsWith('/api/terminal/resize')) resizePosts.push(JSON.parse(req.postData() || '{}'));
});

const processOutput = async () => {
  // Re-read what the server holds for the session: the process's own output.
  const r = await fetch(`${BASE}/api/project/${PID}/terminal/status`).then(x => x.json());
  const s = (r.sessions || []).find(x => x.session_id === SID);
  return s ? s.output_lines.join('') : '';
};
const waitFor = async (fn, ms = 8000) => {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (await fn()) return true; await new Promise(r => setTimeout(r, 150)); }
  return false;
};

try {
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.connectAgentStream === 'function' && typeof window.openTerminalPopout === 'function', null, { timeout: 15000 });

  // ── 1. open through the REAL marker handler ──────────────────────────────
  await page.evaluate(([pid, sid]) => window.connectAgentStream(pid, sid), [PID, AGENT_SID]);
  const modal = page.locator(`[data-modal-id="__terminal_${SID}"]`);
  await modal.waitFor({ state: 'visible', timeout: 15000 });
  await page.waitForSelector(`#terminal-container-${SID} .xterm`, { timeout: 15000 });
  ok('marker path opened the pop-out');

  const state = await page.evaluate(sid => ({
    isPty: window.terminalIsPty[sid],
    disableStdin: window.terminalInstances[sid].options.disableStdin,
    stdinRowShown: getComputedStyle(document.getElementById('terminal-stdin-row-' + sid)).display !== 'none',
  }), SID);
  check(state.isPty === true, `marker-opened session is a PTY pop-out (terminalIsPty=${state.isPty})`);
  check(state.disableStdin === false, 'xterm stdin is enabled for a PTY session');
  check(state.stdinRowShown === false, 'pipe-only "Send input" row is hidden for a PTY session');

  // Keystrokes typed INTO xterm must reach the process.
  check(await waitFor(async () => (await processOutput()).includes('PTY_READY')), 'process is up (PTY_READY seen)');
  await page.click(`#terminal-container-${SID} .xterm`);
  await page.keyboard.type('hello42');
  await page.keyboard.press('Enter');
  const got = await waitFor(async () => (await processOutput()).includes('GOT:') && (await processOutput()).includes('hello42'));
  check(got, 'typed keystrokes reached the process (child printed GOT:...hello42)');
  check(stdinPosts.join('').includes('hello42'), `xterm onData posted the keystrokes (${JSON.stringify(stdinPosts.slice(0, 3))}...)`);
  check(stdinPosts.some(t => t === '\r'), 'Enter posted as \\r');

  // ── 2. the Send box, if it is ever shown for a PTY, submits with \r ──────
  stdinPosts.length = 0;
  await page.evaluate(sid => {
    const row = document.getElementById('terminal-stdin-row-' + sid);
    row.style.display = '';
    document.getElementById('terminal-stdin-' + sid).value = 'viaSendBox';
    window.sendTerminalInput(sid);
  }, SID);
  await waitFor(async () => stdinPosts.length > 0);
  check(stdinPosts.length === 1 && stdinPosts[0] === 'viaSendBox\r', `Send box on a PTY posts text + \\r (${JSON.stringify(stdinPosts)})`);
  check(await waitFor(async () => (await processOutput()).includes('viaSendBox')), 'process received the Send-box line');

  // ── 3. width: wider window, no clipping, PTY told the new size ───────────
  const geom = async () => page.evaluate(sid => {
    const win = document.querySelector(`[data-modal-id="__terminal_${sid}"]`);
    const content = win.querySelector('.modal-content');
    const cont = document.getElementById('terminal-container-' + sid);
    const screen = cont.querySelector('.xterm-screen');
    const w = win.getBoundingClientRect(), c = content.getBoundingClientRect();
    const k = cont.getBoundingClientRect(), s = screen.getBoundingClientRect();
    const t = window.terminalInstances[sid];
    const vp = cont.querySelector('.xterm-viewport'), vr = vp.getBoundingClientRect();
    return { viewportClientRight: vr.left + vp.clientWidth, winW: w.width, contentW: c.width, contentRight: c.right, containerW: k.width, containerRight: k.right,
             screenRight: s.right, cols: t.cols, winRight: w.right };
  }, SID);
  const before = await geom();
  console.log('geometry before widen', JSON.stringify(before));
  check(Math.abs(before.winW - before.contentW) <= 2, `window box and content box are the same width (${before.winW} vs ${before.contentW})`);
  check(before.screenRight <= before.contentRight + 1, `terminal screen sits inside the visible box (screen right ${before.screenRight}, box right ${before.contentRight})`);
  check(before.screenRight <= before.viewportClientRight + 1, `grid ends before the scrollbar, nothing under it (screen right ${before.screenRight}, scrollbar starts ${before.viewportClientRight})`);

  // Drag the real east handle (what a mouse does).
  const handle = await modal.locator('.mc-resize-e').boundingBox();
  const colsBefore = before.cols;
  resizePosts.length = 0;
  await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
  await page.mouse.down();
  await page.mouse.move(handle.x + 200, handle.y + handle.height / 2, { steps: 8 });
  await page.mouse.up();
  await new Promise(r => setTimeout(r, 600));
  const after = await geom();
  console.log('geometry after widen ', JSON.stringify(after));
  check(after.contentW > before.contentW + 100, `east-edge drag widened the window (${before.contentW} -> ${after.contentW})`);
  check(Math.abs(after.winW - after.contentW) <= 2, 'window and content stay the same width after widening');
  check(after.screenRight <= after.viewportClientRight + 1, `grid still clear of the scrollbar after widening (${after.screenRight} vs ${after.viewportClientRight})`);
  check(after.cols > colsBefore, `xterm gained columns (${colsBefore} -> ${after.cols})`);
  check(resizePosts.some(p => p.cols === after.cols), `PTY resize posted the new width (${JSON.stringify(resizePosts.slice(-1))})`);

  // ── mobile (<=960px): a full-width sheet, opened at that width ────────────
  const mctx = await browser.newContext({ viewport: { width: 390, height: 800 }, isMobile: true, hasTouch: true });
  const mpage = await mctx.newPage();
  mpage.on('pageerror', e => pageErrors.push(String(e)));
  await mpage.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  await mpage.waitForFunction(() => typeof window.openTerminalPopout === 'function', null, { timeout: 15000 });
  await mpage.evaluate(([pid, sid]) => window.openTerminalPopout(pid, sid, 'python pty_echo.py', true), [PID, SID]);
  await mpage.waitForSelector(`#terminal-container-${SID} .xterm`, { timeout: 15000 });
  await new Promise(r => setTimeout(r, 800));
  const mob = await mpage.evaluate(sid => {
    const win = document.querySelector(`[data-modal-id="__terminal_${sid}"]`);
    const w = win.getBoundingClientRect();
    const sc = document.getElementById('terminal-container-' + sid).querySelector('.xterm-screen').getBoundingClientRect();
    const vp = document.getElementById('terminal-container-' + sid).querySelector('.xterm-viewport');
    const vr = vp.getBoundingClientRect();
    return { left: w.left, width: w.width, vw: innerWidth, screenRight: sc.right, viewportClientRight: vr.left + vp.clientWidth };
  }, SID);
  console.log('geometry mobile      ', JSON.stringify(mob));
  check(mob.left === 0 && Math.abs(mob.width - mob.vw) <= 1, `mobile: sheet fills the viewport width (left ${mob.left}, ${mob.width}px of ${mob.vw})`);
  check(mob.screenRight <= mob.viewportClientRight + 1, 'mobile: terminal grid ends before the scrollbar');

  check(pageErrors.length === 0, `no page errors (${pageErrors.slice(0, 2).join(' | ') || 'none'})`);
} catch (e) {
  fail('smoke aborted: ' + (e && e.stack || e));
} finally {
  await post('/api/terminal/delete', { session_id: SID }).catch(() => {});
  await browser.close().catch(() => {});
  killServer();
}
console.log(process.exitCode ? 'RESULT: FAIL' : 'RESULT: PASS');
