'use strict';
const { connectDashboard, evalIn } = require('./lib/cdp');
const adb = require('./lib/adb');

(async () => {
  const { client } = await connectDashboard();
  try {
    const info = await evalIn(client, `(() => ({
      innerWidth: window.innerWidth, innerHeight: window.innerHeight,
      dpr: window.devicePixelRatio, href: location.href,
      hasComposer: !!document.querySelector('.agent-task-input'),
      composerIds: Array.from(document.querySelectorAll('.agent-task-input')).map(e=>e.id),
    }))()`);
    console.log('info', info);
    const sid = process.env.MC_SID;
    if (sid) {
      const rect = await evalIn(client, `(() => {
        const el = document.getElementById('agent-followup-' + ${JSON.stringify(sid)});
        if (!el) return null;
        el.scrollIntoView({block:'center'});
        const r = el.getBoundingClientRect();
        return {x:r.x,y:r.y,w:r.width,h:r.height,visible: r.width>0 && r.height>0, dpr: window.devicePixelRatio};
      })()`);
      console.log('composer rect', rect);
      if (rect && rect.visible) {
        const x = Math.round((rect.x + rect.w/2) * rect.dpr);
        const y = Math.round((rect.y + rect.h/2) * rect.dpr);
        console.log('tapping', x, y);
        adb.adbShell(`input tap ${x} ${y}`);
        await new Promise(r => setTimeout(r, 800));
        const active = await evalIn(client, `(() => ({tag: document.activeElement && document.activeElement.tagName, id: document.activeElement && document.activeElement.id}))()`);
        console.log('active after tap', active);
      }
    }
  } finally {
    await client.close().catch(()=>{});
  }
})().catch(e => { console.error(e); process.exit(1); });
