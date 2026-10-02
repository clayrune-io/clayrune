'use strict';
// "Split screen" + "Send needs two taps" repro on the REAL Capacitor WebView
// (CDP over adb, real OS touches + real IME). Records what the page saw with a
// page-side recorder (50ms samples + every touch/click/focus/vv/fetch event)
// so the numbers are the page's own, not a polled approximation.
//
//   MC_SERIAL=emulator-5556 MC_CDP_PORT=9333 MC_BASE=http://10.0.2.2:5399/ \
//   NODE_PATH=<main>/tools/mobile-test/node_modules node tools/mobile-test/split4.js [scenario ...]
//
// MC_BASE: which origin the WebView loads. 5199 = the live server (main checkout);
// 5399 = static-proxy.js serving THIS worktree's static/ (see that file).
const CDP = require('chrome-remote-interface');
const adb = require('./lib/adb');
const app = require('./lib/app');
const { evalIn } = require('./lib/cdp');

const sleep = adb.sleep;
const BASE = process.env.MC_BASE || 'http://10.0.2.2:5399/';
const PROJECT = process.env.MC_TEST_PROJECT || 'mobiletest';
const DPR = 2.625;
const COMPOSER = `document.querySelector('textarea.agent-task-input[id^=agent-followup-]')`;

const RECORDER = `(() => {
  if (window.__rec && window.__rec.stop) window.__rec.stop();
  const R = { ev: [], sm: [], t0: performance.now() };
  const now = () => Math.round(performance.now() - R.t0);
  const d = (el) => { try { if (!el) return null; return (el.tagName||'') + (el.id ? '#' + el.id : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.split(/\\s+/).filter(Boolean).slice(0,2).join('.') : ''); } catch (e) { return '?'; } };
  const vv = window.visualViewport;
  const modal = () => document.querySelector('.modal-window');
  const sendBtn = () => document.querySelector('.btn-send-arrow');
  const snap = () => {
    const m = modal(), s = sendBtn(), a = document.activeElement;
    const mr = m && m.getBoundingClientRect(), sr = s && s.getBoundingClientRect();
    return {
      ih: window.innerHeight, ch: document.documentElement.clientHeight, vvh: vv ? Math.round(vv.height*10)/10 : null, vvt: vv ? vv.offsetTop : null,
      app: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--mc-app-vh')) || null,
      mb: mr ? Math.round(mr.bottom) : null, sy: sr ? Math.round(sr.top + sr.height/2) : null, sx: sr ? Math.round(sr.left + sr.width/2) : null,
      mcInline: (document.querySelector('.modal-content')||{style:{}}).style.height || null, mcB: (()=>{const c=document.querySelector('.modal-content');return c?Math.round(c.getBoundingClientRect().bottom):null})(), act: d(a), val: a && a.value != null ? a.value.length : null,
    };
  };
  const ev = (type, extra) => R.ev.push(Object.assign({ t: now(), type }, snap(), extra || {}));
  const L = (target, type, fn, opt) => { target.addEventListener(type, fn, opt || true); R.off = (R.off||[]); R.off.push(() => target.removeEventListener(type, fn, opt || true)); };
  ['touchstart','touchend'].forEach(k => L(document, k, e => { const t = (e.changedTouches||e.touches||[])[0]; ev(k, { cx: t && Math.round(t.clientX), cy: t && Math.round(t.clientY), tgt: d(e.target) }); }));
  L(window, 'touchstart', e => ev('ts-after-handlers', { dp: e.defaultPrevented, tgt: d(e.target) }), { capture: false, passive: true });
  L(document, 'click', e => ev('click', { cx: Math.round(e.clientX), cy: Math.round(e.clientY), tgt: d(e.target) }));
  L(document, 'focusin', e => ev('focusin', { tgt: d(e.target) }));
  L(document, 'focusout', e => ev('focusout', { tgt: d(e.target) }));
  L(document, 'visibilitychange', () => ev('vis:' + document.visibilityState));
  L(window, 'pageshow', () => ev('pageshow'));
  L(window, 'resize', () => ev('win-resize'));
  if (vv) { L(vv, 'resize', () => ev('vv-resize')); L(vv, 'scroll', () => ev('vv-scroll')); }
  const of = window.fetch;
  window.fetch = function (u, o) { try { if (/followup|agent\\/send|dispatch/.test(String(u))) ev('POST', { url: String(u).replace(/^.*\\/api/, '/api') }); } catch (e) {} return of.apply(this, arguments); };
  const iv = setInterval(() => R.sm.push(Object.assign({ t: now() }, snap())), 50);
  R.stop = () => { clearInterval(iv); window.fetch = of; (R.off||[]).forEach(f => f()); };
  window.__rec = R;
  return true;
})()`;

async function connect() {
  const sock = adb.findWebviewSocket();
  if (!sock) throw new Error('no webview devtools socket (launch the app: adb shell am start -n io.clayrune.app/.MainActivity)');
  adb.forwardDevtools(sock, adb.CDP_PORT);
  const targets = await CDP.List({ port: adb.CDP_PORT });
  const page = targets.find((t) => t.type === 'page' && !/about:blank/.test(t.url || '')) || targets.find((t) => t.type === 'page');
  if (!page) throw new Error('no page target');
  const client = await CDP({ port: adb.CDP_PORT, target: page.id, local: true });
  await client.Runtime.enable(); await client.Page.enable().catch(() => {});
  return client;
}

async function loadBase(client) {
  await client.Page.navigate({ url: BASE });
  for (let i = 0; i < 40; i++) {
    await sleep(1000);
    try { if (await evalIn(client, `location.href.startsWith(${JSON.stringify(BASE.replace(/\/$/, ''))}) && document.readyState==='complete' && typeof allProjects !== 'undefined' && allProjects.length>0`)) return; } catch (e) {}
  }
  throw new Error('SPA did not load from ' + BASE);
}

async function ensureChat(client) {
  const st = await app.httpJson(`${app.HOST_API}/api/project/${PROJECT}/agent/status`);
  const done = (st.sessions || []).find((s) => ['idle', 'completed', 'complete'].includes(s.status));
  if (done) return done.session_id;
  console.log('dispatching a throwaway chat…');
  const sid = await app.dispatchAgentTask(client, PROJECT, 'Reply with exactly one word: done. Do not use any tools.');
  for (let i = 0; i < 60; i++) {
    await sleep(2000);
    const s = (await app.serverSession(PROJECT, sid));
    if (s && ['idle', 'completed', 'complete'].includes(s.status)) return sid;
  }
  throw new Error('chat never settled');
}

async function openChat(client, sid) {
  await loadBase(client);
  await evalIn(client, `(() => { openProjectModal(${JSON.stringify(PROJECT)}); return true; })()`);
  await sleep(1500);
  await calibrate(client);            // taps the inert 'CONVERSATIONS' heading
  // Open the chat the way a user does: a real tap on its row in the picker.
  const inChat = await evalIn(client, `!!${COMPOSER}`);
  if (!inChat) await tapEl(client, `document.querySelector('.conv-list-scroll .conv-row')`);
  await sleep(2500);
  await evalIn(client, RECORDER);
}

const rec = (client) => evalIn(client, `JSON.stringify({ ev: window.__rec.ev, sm: window.__rec.sm })`).then(JSON.parse);
const mark = (client, label) => evalIn(client, `window.__rec.ev.push(Object.assign({t: Math.round(performance.now()-window.__rec.t0), type:'MARK:'+${JSON.stringify(label)}}, {}))`);
const snapNow = (client) => evalIn(client, `(() => { const R = window.__rec; const n = R.sm.length; return R.sm[n-1]; })()`);

// screen px = css px * DPR + (webview top offset). Calibrated from a real tap.
let TOP_OFF = null;
async function calibrate(client) {
  await evalIn(client, `window.__cal = null; document.addEventListener('touchstart', e => { window.__cal = {x: e.touches[0].clientX, y: e.touches[0].clientY}; }, {once:true, capture:true, passive:true})`);
  adb.adbShell('input tap 400 540');
  await sleep(400);
  const c = await evalIn(client, 'window.__cal');
  if (!c) throw new Error('calibration tap never reached the page');
  TOP_OFF = 540 - c.y * DPR;
  console.log(`calibration: tap y=540 -> client y=${c.y.toFixed(1)}  => top offset ${TOP_OFF.toFixed(0)}px, x: 400 -> ${c.x.toFixed(1)}`);
}
async function tapAtCss(x, y) { adb.adbShell(`input tap ${Math.round(x * DPR)} ${Math.round(y * DPR + TOP_OFF)}`); }
async function tapEl(client, selExpr) {
  const r = await evalIn(client, `(() => { const el = ${selExpr}; if (!el) return null; const b = el.getBoundingClientRect(); return {x:b.left+b.width/2, y:b.top+b.height/2, w:b.width, h:b.height}; })()`);
  if (!r || !r.w) throw new Error('target missing: ' + selExpr);
  await tapAtCss(r.x, r.y);
  return r;
}
const SEND = `document.querySelector('.btn-send-arrow')`;
const kb = (client) => evalIn(client, `(() => { const R = window.__rec; return R.sm[R.sm.length-1]; })()`);

function fmt(rows, keys) { return rows.map((r) => keys.map((k) => String(r[k] ?? '-').padEnd(k === 'type' || k === 'tgt' ? 22 : 6)).join(' ')).join('\n'); }

// Collapse the 50ms samples to the moments something changed.
function changes(sm) {
  const out = []; let prev = null;
  for (const s of sm) {
    const key = [s.ih, s.vvh, s.app, s.mb, s.mcInline, s.act].join('|');
    if (key !== prev) { out.push(s); prev = key; }
  }
  return out;
}

const SCEN = {};

// A: keyboard OPEN, text typed, ONE tap on Send. Did a POST fire from that tap?
SCEN.sendOpen = async (client, sid) => {
  await openChat(client, sid);
  await tapEl(client, COMPOSER); await sleep(1200);
  adb.adbShell('input text splitprobe'); await sleep(500);
  await mark(client, 'before-send-tap');
  const before = await kb(client);
  await tapEl(client, SEND); await sleep(2500);
  return { before, ...(await rec(client)) };
};

// B: keyboard dismissed with the keyboard's own down button / BACK (focus stays), then ONE tap on Send.
SCEN.sendAfterDismiss = async (client, sid) => {
  await openChat(client, sid);
  await tapEl(client, COMPOSER); await sleep(1200);
  adb.adbShell('input text splitprobe'); await sleep(500);
  await mark(client, 'BACK-key (dismiss IME)');
  adb.adbShell('input keyevent KEYCODE_BACK'); await sleep(3000);
  await mark(client, 'settled-3s; tapping Send once');
  const before = await kb(client);
  await tapEl(client, SEND); await sleep(2500);
  return { before, ...(await rec(client)) };
};

// C: background (HOME) with keyboard open, return.
SCEN.bgResume = async (client, sid) => {
  await openChat(client, sid);
  await tapEl(client, COMPOSER); await sleep(1200);
  adb.adbShell('input text splitprobe'); await sleep(500);
  await mark(client, 'HOME');
  adb.pressHome(); await sleep(2500);
  await mark(client, 'foreground');
  adb.adbShell('am start -n io.clayrune.app/.MainActivity'); await sleep(4000);
  return await rec(client);
};

// D: composer focused + keyboard dismissed, a turn completes meanwhile.
SCEN.turnSettles = async (client, sid) => {
  await openChat(client, sid);
  await tapEl(client, COMPOSER); await sleep(1200);
  adb.adbShell('input keyevent KEYCODE_BACK'); await sleep(2500);
  await mark(client, 'trigger: updateAgentStatusUI running->idle');
  await evalIn(client, `(() => { updateAgentStatusUI(${JSON.stringify(sid)}, 'running'); setTimeout(() => updateAgentStatusUI(${JSON.stringify(sid)}, 'idle'), 600); })()`);
  await sleep(4000);
  return await rec(client);
};


// E: the corner-resize zone. interactions.js:829 preventDefault()s a single-finger touchstart within
// 40px of .modal-content's bottom-right corner. Send lives there. Tap the LOWER-RIGHT part of the
// Send button (inside the zone, still on the button) with the keyboard open: does a POST fire?
const zoneGeom = (client) => evalIn(client, `(() => { const c = document.querySelector('.modal-content').getBoundingClientRect(); const s = document.querySelector('.btn-send-arrow').getBoundingClientRect(); return { cr: c.right, cb: c.bottom, sl: s.left, sr: s.right, st: s.top, sb: s.bottom, inZoneX: s.right > c.right - 40, inZoneY: s.bottom > c.bottom - 40 }; })()`);
SCEN.sendZoneTap = async (client, sid) => {
  await openChat(client, sid);
  await tapEl(client, COMPOSER); await sleep(1200);
  adb.adbShell('input text splitprobe'); await sleep(500);
  const g = await zoneGeom(client); console.log('geom', JSON.stringify(g));
  const x = Math.min(g.sr - 4, g.cr - 30), y = Math.min(g.sb - 3, g.cb - 30);
  await mark(client, `tap Send at lower-right (${Math.round(x)},${Math.round(y)}) — inside the 40px corner zone`);
  await tapAtCss(x, y); await sleep(2500);
  return { before: g, ...(await rec(client)) };
};
SCEN.sendCenterTap = async (client, sid) => {
  await openChat(client, sid);
  await tapEl(client, COMPOSER); await sleep(1200);
  adb.adbShell('input text splitprobe'); await sleep(500);
  await mark(client, 'tap Send dead centre (control)');
  await tapEl(client, SEND); await sleep(2500);
  return await rec(client);
};
// F: same corner, but the finger drifts (a real thumb rolls). Then dismiss the keyboard.
// If .modal-content keeps an inline height afterwards, --mc-app-vh can recover all it likes.
SCEN.sendZoneDrift = async (client, sid) => {
  await openChat(client, sid);
  await tapEl(client, COMPOSER); await sleep(1200);
  adb.adbShell('input text splitprobe'); await sleep(500);
  const g = await zoneGeom(client);
  const x = Math.min(g.sr - 4, g.cr - 30), y = Math.min(g.sb - 3, g.cb - 30);
  await mark(client, 'thumb drifts 24px up inside the corner zone');
  adb.adbShell(`input swipe ${Math.round(x * DPR)} ${Math.round(y * DPR + TOP_OFF)} ${Math.round(x * DPR)} ${Math.round((y - 24) * DPR + TOP_OFF)} 120`);
  await sleep(800);
  await mark(client, 'BACK (dismiss keyboard)');
  adb.adbShell('input keyevent KEYCODE_BACK'); await sleep(3000);
  return await rec(client);
};

(async () => {
  const names = process.argv.slice(2).length ? process.argv.slice(2) : Object.keys(SCEN);
  const client = await connect();
  try {
    await loadBase(client);
    // A fresh origin (the :5399 proxy) has no localStorage, so the first-run Setup wizard
    // would cover every tap. walkthrough_done is the wizard's own "already did it" flag.
    await evalIn(client, `localStorage.setItem('walkthrough_done','1')`);
    await loadBase(client);
    const sid = process.env.MC_SID || await ensureChat(client);
    console.log('chat', sid, 'base', BASE);
    for (const n of names) {
      if (!SCEN[n]) { console.log('unknown scenario', n); continue; }
      console.log(`\n===== ${n} =====`);
      try {
        const r = await SCEN[n](client, sid);
        if (r.before) console.log('before:', JSON.stringify(r.before));
        console.log('--- events'); console.log(fmt(r.ev, ['t', 'type', 'tgt', 'dp', 'cx', 'cy', 'ih', 'app', 'mb', 'mcB', 'mcInline']));
        console.log('--- viewport changes'); console.log(fmt(changes(r.sm), ['t', 'ih', 'app', 'mb', 'mcB', 'mcInline']));
        const posts = r.ev.filter((e) => e.type === 'POST').length;
        console.log(`RESULT ${n}: POSTs=${posts} finalApp=${(r.sm[r.sm.length - 1] || {}).app} finalModalBottom=${(r.sm[r.sm.length - 1] || {}).mb} innerH=${(r.sm[r.sm.length - 1] || {}).ih}`);
      } catch (e) { console.log(`SCENARIO ${n} ERROR`, e.message); }
    }
  } finally { await client.close().catch(() => {}); }
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
