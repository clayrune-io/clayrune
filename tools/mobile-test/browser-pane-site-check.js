'use strict';
// Real Capacitor WebView on the emulator -> the browser pane served by
// tools/smoke/browser_pane_harness.py (real browser_routes + headless Chromium)
// on host :5391, reaching live sites. Checks (Ron, 2026-10-02):
//   (a) a session an agent launched at 1280x800 (desktop HTML already loaded) is
//       RELOADED as the mobile site when the phone attaches;
//   (b) a pane opened FROM the phone is mobile before its first navigation.
// Usage: node tools/mobile-test/_emu_site_check.js [url ...]
const CDP = require('chrome-remote-interface');
const fs = require('fs');
const path = require('path');
const { findWebviewSocket, forwardDevtools, CDP_PORT, sleep, adb } = require('./lib/adb');
const { evalIn } = require('./lib/cdp');

const HARNESS = process.env.MC_HARNESS || 'http://10.0.2.2:5391';
const SITES = process.argv.slice(2).length ? process.argv.slice(2) : ['https://www.google.com/', 'https://en.wikipedia.org/wiki/Special:Random'];

(async () => {
  const sock = findWebviewSocket();
  if (!sock) throw new Error('no WebView devtools socket: is the Clayrune dev APK running?');
  forwardDevtools(sock, CDP_PORT);
  const targets = await CDP.List({ port: CDP_PORT });
  const page = targets.find((t) => t.type === 'page' && !/about:blank/.test(t.url || '')) || targets.find((t) => t.type === 'page');
  const client = await CDP({ port: CDP_PORT, target: page.id, local: true });
  await client.Runtime.enable(); await client.Page.enable();
  await client.Page.navigate({ url: HARNESS + '/' });
  for (let i = 0; i < 40; i++) { await sleep(500); try { if (await evalIn(client, `typeof window.openBrowserPane === 'function'`)) break; } catch (_) {} }
  const J = (p, body) => evalIn(client, `fetch(${JSON.stringify(p)}, ${body ? `{method:'POST',headers:{'Content-Type':'application/json'},body:${JSON.stringify(JSON.stringify(body))}}` : '{}'}).then(r => r.json())`);
  const info = await evalIn(client, `({ua: navigator.userAgent, w: innerWidth, h: innerHeight, touch: navigator.maxTouchPoints})`);
  console.log('WEBVIEW', JSON.stringify(info));

  const sess = async (sid) => {
    const s = await J('/_harness/session/' + sid);
    const st = await J('/api/project/emu/browser/status');
    const mine = (st.sessions || []).find((x) => x.session_id === sid) || {};
    const t = await J('/api/browser/read', { session_id: sid, selector: 'body' });
    return { url: mine.url, device_mode: s.device_mode, page_scale: s.page_scale && +s.page_scale.toFixed(3), frame: s.frame,
             ua: (s.ua || '').slice(0, 40), text: ((t.content && t.content.text) || t.error || '').replace(/\s+/g, ' ').slice(0, 90) };
  };
  const results = [];
  let n = 0;
  for (const url of SITES) {
    n++;
    // (b) opened from the phone
    await evalIn(client, `window.openBrowserPane(${JSON.stringify(url)}, 'emu')`);
    let sid;
    for (let i = 0; i < 40 && !sid; i++) { await sleep(300); sid = (((await J('/api/project/emu/browser/status')).sessions) || [])[0]?.session_id; }
    await sleep(9000);
    const fromPhone = await sess(sid);
    if (n === 1) { fs.writeFileSync(path.join(__dirname, '..', '..', '_scratch', 'emu_from_phone.png'), adb(['exec-out', 'screencap', '-p'], { encoding: 'buffer' })); }
    await J('/api/browser/stop', { session_id: sid });
    await evalIn(client, `window.closeBrowserPane && window.closeBrowserPane()`);
    await sleep(800);
    // (a) agent-launched desktop session, then the phone attaches
    const l = await J('/api/browser/launch', { project_id: 'emu', url, w: 1280, h: 800 });
    await sleep(8000);
    const before = await sess(l.session_id);
    await evalIn(client, `window.openBrowserPane(${JSON.stringify(url)}, 'emu', ${JSON.stringify(l.session_id)})`);
    await sleep(9000);
    const after = await sess(l.session_id);
    if (n === 1) { fs.writeFileSync(path.join(__dirname, '..', '..', '_scratch', 'emu_attached.png'), adb(['exec-out', 'screencap', '-p'], { encoding: 'buffer' })); }
    await J('/api/browser/stop', { session_id: l.session_id });
    await evalIn(client, `window.closeBrowserPane && window.closeBrowserPane()`);
    results.push({ url, openedFromPhone: fromPhone, agentLaunched_beforeAttach: before, agentLaunched_afterAttach: after });
    console.log(JSON.stringify(results[results.length - 1], null, 1));
  }
  await client.close();
})().catch((e) => { console.error('ERR', e.stack || e.message); process.exit(1); });
