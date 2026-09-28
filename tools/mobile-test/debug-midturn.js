'use strict';
const { connectDashboard, evalIn } = require('./lib/cdp');
const app = require('./lib/app');
const adb = require('./lib/adb');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const SCALE = 2.787; // empirical CSS-px -> physical-px factor for this AVD (see MC-988 journal)

async function rectOf(client, expr) {
  return evalIn(client, `(() => {
    const el = ${expr};
    if (!el) return null;
    el.scrollIntoView({block:'center'});
    const r = el.getBoundingClientRect();
    return {x:r.x,y:r.y,w:r.width,h:r.height};
  })()`);
}
function tapRect(rect) {
  const x = Math.round((rect.x + rect.w / 2) * SCALE);
  const y = Math.round((rect.y + rect.h / 2) * SCALE);
  adb.adbShell(`input tap ${x} ${y}`);
  return { x, y };
}
async function metrics(client) {
  return evalIn(client, `(() => { const vv=window.visualViewport; return {
    innerHeight: window.innerHeight, vvHeight: vv?vv.height:null,
    appVh: getComputedStyle(document.documentElement).getPropertyValue('--mc-app-vh'),
    active: document.activeElement && document.activeElement.tagName }; })()`);
}
async function status(sid) {
  const d = await app.serverSession('mobiletest', sid);
  return d && d.status;
}

(async () => {
  const { client } = await connectDashboard();
  const prompt = 'Write a 60-line numbered list of interesting, surprising ocean facts, one fact per line, no tools, do not stop early, keep going to line 60.';
  const sid = await app.dispatchAgentTask(client, 'mobiletest', prompt);
  console.log('dispatched', sid);

  const composerExpr = `document.getElementById('agent-followup-${sid}')`;
  let rect = await rectOf(client, composerExpr);
  console.log('composer rect', rect, 'status', await status(sid));
  const focusTap = tapRect(rect);
  console.log('focus tap', focusTap);
  await sleep(700);
  console.log('after focus tap status', await status(sid), await metrics(client));

  adb.adbShell('input text redirectmidturn');
  await sleep(200);

  const sendExpr = `Array.from(document.querySelectorAll('.btn-send-arrow')).find(b => (b.getAttribute('onclick')||'').includes('${sid}'))`;
  const sendRect = await rectOf(client, sendExpr);
  console.log('send rect', sendRect, 'status right before tap', await status(sid));
  const st = await status(sid);
  const sendTap = tapRect(sendRect);
  console.log('send tap', sendTap, 'status AT tap time was', st);

  const t0 = Date.now();
  for (let i = 0; i < 16; i++) {
    const m = await metrics(client).catch((e) => ({ error: e.message }));
    const s = await status(sid).catch(() => null);
    console.log(Date.now() - t0, s, m);
    await sleep(150);
  }
  await client.close().catch(() => {});
})().catch((e) => { console.error(e); process.exit(1); });
