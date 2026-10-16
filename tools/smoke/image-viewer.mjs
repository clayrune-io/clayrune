#!/usr/bin/env node
// MC-1100: hermetic real-module coverage, no live server or user files touched.
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const OUT = resolve(ROOT, '_scratch/mc1100');
mkdirSync(OUT, { recursive: true });
const assets = loadStaticJsCss(ROOT);
const origin = 'https://image.smoke.test';
const files = ['C:/pictures/Material-study.png', 'C:/pictures/Portrait.png', 'C:/pictures/Details.png'];
const sources = files.map(path => '/api/serve-image?path=' + encodeURIComponent(path));
const html = `<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/static/css/app.css"><link rel="stylesheet" href="/static/css/image-viewer.css"></head>
<body style="margin:0;background:#e9e6de"><div id="minimized-tray"></div><textarea id="composer" aria-label="Composer"></textarea>
<script>window.API_BASE='';window.esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));</script>
<script type="module" src="/static/js/image-viewer.js"></script><script type="module" src="/static/js/desk-v1-library-viewer.js"></script>
<script type="module" src="/static/js/mermaid.js"></script><script type="module" src="/static/js/rich-text.js"></script><script type="module" src="/static/js/resume-preview.js"></script><script type="module" src="/static/js/media.js"></script></body></html>`;
let checks = 0;
function check(condition, label) { assert.ok(condition, label); checks++; console.log('PASS ' + label); }
const browser = await chromium.launch();
try {
  for (const platform of ['windows', 'mac']) for (const width of [1440, 390]) {
    const label = `${platform}-${width}`;
    const context = await browser.newContext({ viewport: { width, height: 900 }, hasTouch: true, acceptDownloads: true });
    const page = await context.newPage();
    const errors = [], requests = [];
    page.on('pageerror', e => { errors.push(e.message); console.error('PAGE ERROR', e.message); });
    await page.addInitScript(platform => {
      window.__imageViewerPlatform = platform;
      window.__listeners = {};
      const add = document.addEventListener.bind(document), remove = document.removeEventListener.bind(document);
      document.addEventListener = (type, fn, opt) => { window.__listeners[type] = (window.__listeners[type] || 0) + 1; return add(type, fn, opt); };
      document.removeEventListener = (type, fn, opt) => { window.__listeners[type] = (window.__listeners[type] || 0) - 1; return remove(type, fn, opt); };
      window.__copies = [];
      // OS clipboard and print are instrumented, not sent to the user's devices.
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { write: async items => {
        const blob = await items[0].getType('image/png'), bitmap = await createImageBitmap(blob);
        window.__copies.push({ type: blob.type, width: bitmap.width, height: bitmap.height }); bitmap.close();
      } } });
      window.print = () => { parent.__printed = true; window.dispatchEvent(new Event('afterprint')); };
    }, platform);
    let fixtures = [];
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      requests.push(url.pathname);
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (assets[url.pathname]) { const [contentType, body] = assets[url.pathname]; return route.fulfill({ contentType, body }); }
      if (url.pathname === '/api/serve-image') {
        if (url.searchParams.get('path') === 'broken') return route.fulfill({ status: 404, body: 'missing' });
        const i = Math.max(0, files.indexOf(url.searchParams.get('path')));
        return route.fulfill({ contentType: 'image/png', body: Buffer.from(fixtures[i], 'base64') });
      }
      return route.abort();
    });
    await page.goto(origin);
    await page.waitForFunction(() => window._openImageViewer && window.DeskV1LibraryViewer && window.formatAgentText && window.escPromptWithImages && window._mediaOpen);
    fixtures = await page.evaluate(() => [0,1,2].map(i => {
      const c = document.createElement('canvas'); c.width = i === 1 ? 800 : 1800; c.height = i === 1 ? 1200 : 1100;
      const x = c.getContext('2d');
      x.fillStyle = ['#efe3c9','#d8dfd3','#e8c7ac'][i]; x.fillRect(0,0,c.width,c.height);
      x.fillStyle = '#be6845'; x.beginPath(); x.moveTo(0,c.height); x.lineTo(c.width*.45,c.height*.28); x.lineTo(c.width,c.height); x.fill();
      x.fillStyle = '#626f59'; x.beginPath(); x.moveTo(c.width*.4,c.height); x.lineTo(c.width*.78,c.height*.4); x.lineTo(c.width,c.height*.7); x.lineTo(c.width,c.height); x.fill();
      x.fillStyle = '#f8f3e6'; x.beginPath(); x.arc(c.width*.78,c.height*.22,90,0,Math.PI*2); x.fill();
      return c.toDataURL('image/png').split(',')[1];
    }));
    const initialListeners = await page.evaluate(() => ({ ...window.__listeners }));
    const open = () => page.evaluate(({ sources }) => window._openImageViewer(sources[0], { images: sources }), { sources });
    const ready = async (dimensions = '1800 \u00d7 1100') => {
      await page.waitForFunction(dim => document.querySelector('[data-info=dimensions]')?.textContent === dim && !document.querySelector('.iv-image').hidden, dimensions);
      await page.waitForFunction(() => !['Loading\u2026','Unavailable'].includes(document.querySelector('[data-info=size]')?.textContent));
    };
    const click = name => page.locator(`[data-iv="${name}"]`).click();
    const zoom = () => page.locator('.image-viewer').evaluate(el => Number(el.dataset.zoom));
    await open(); await ready();
    check(await page.locator('.iv-title').textContent() === 'Material-study.png', `${label} filename`);
    check(await page.locator('.iv-count').textContent() === '1 / 3', `${label} scope and counter`);
    check(await page.locator('.image-viewer').evaluate((el, platform) => el.classList.contains('iv-mac') === (platform === 'mac'), platform), `${label} platform chrome`);
    const fit = await zoom();
    const anchor = await page.locator('.iv-image').evaluate(img => {
      const r = img.getBoundingClientRect(); return { x: r.left + r.width*.6, y: r.top + r.height*.4, ux: .6, uy: .4 };
    });
    await page.mouse.move(anchor.x, anchor.y); await page.mouse.wheel(0,-200);
    await page.waitForFunction(s => Number(document.querySelector('.image-viewer').dataset.zoom) > s, fit);
    const drift = await page.locator('.iv-image').evaluate((img,a) => { const r = img.getBoundingClientRect(); return Math.hypot(r.left + r.width*a.ux-a.x,r.top+r.height*a.uy-a.y); }, anchor);
    check(drift < 1, `${label} cursor-anchored wheel (${drift.toFixed(2)}px)`);
    const rectBefore = await page.locator('.iv-image').boundingBox();
    await page.mouse.down(); await page.mouse.move(anchor.x-40,anchor.y+25); await page.mouse.up();
    const rectAfter = await page.locator('.iv-image').boundingBox();
    check(Math.abs(rectAfter.x-rectBefore.x+40)<1 && Math.abs(rectAfter.y-rectBefore.y-25)<1, `${label} drag pan`);
    await click('fit');
    const stage = await page.locator('.iv-stage').boundingBox();
    await page.mouse.dblclick(stage.x+stage.width/2,stage.y+stage.height/2);
    check(await zoom() === 1, `${label} double click actual size`);
    await page.mouse.dblclick(stage.x+stage.width/2,stage.y+stage.height/2);
    check(Math.abs(await zoom()-fit)<.001, `${label} double click fit`);
    await page.locator('[data-iv=zoom]').selectOption('150'); check(await zoom() === 1.5, `${label} zoom dropdown`);
    await page.locator('[data-iv=slider]').fill('200'); check(await zoom() === 2, `${label} zoom slider`);
    await click('actual'); check(await zoom() === 1, `${label} actual size`);
    await page.keyboard.press('+');check(await zoom()===1.25,`${label} keyboard zoom`);
    await page.keyboard.press('1');check(await zoom()===1,`${label} keyboard actual size`);
    await click('rotate'); check(await page.locator('.image-viewer').getAttribute('data-rotation') === '90', `${label} rotate`);
    await click('copy'); await page.waitForFunction(() => window.__copies.length === 1);
    check(await page.evaluate(() => window.__copies[0].width === 1100 && window.__copies[0].height === 1800), `${label} copy rotated PNG (clipboard stub)`);
    await click('print'); await page.waitForFunction(() => window.__printed === true);
    check(true, `${label} print prepared image (print stub)`);
    const downloadPromise = page.waitForEvent('download'); await click('save');
    const download = await downloadPromise;
    check(download.suggestedFilename() === 'Material-study.png', `${label} save filename`);
    await page.locator('.iv-nav-next').click(); await ready('800 \u00d7 1200');
    check(await page.locator('.iv-count').textContent() === '2 / 3' && await page.locator('.iv-title').textContent() === 'Portrait.png', `${label} next updates filename/dimensions`);
    check(await page.locator('.image-viewer').getAttribute('data-rotation') === '0', `${label} navigation resets rotation`);
    await page.keyboard.press('ArrowLeft'); await ready(); check(await page.locator('.iv-count').textContent() === '1 / 3', `${label} keyboard previous`);
    await page.locator('.iv-nav-prev').click(); await ready(); check(await page.locator('.iv-count').textContent() === '3 / 3', `${label} previous wraps`);
    await click('filmstrip'); await page.locator('.iv-thumb').first().click(); await ready();
    check(await page.locator('.iv-thumb').first().getAttribute('aria-current') === 'true', `${label} filmstrip selection`);
    await click('info');
    check(await page.locator('[data-info=format]').textContent() === 'PNG' && (await page.locator('[data-info=size]').textContent()).endsWith('KB'), `${label} info MIME and byte size`);
    await page.screenshot({ path: resolve(OUT, `${label}-info.png`) });
    await click('info'); await click('filmstrip'); await click('background');
    check(await page.locator('.iv-stage').getAttribute('data-background') === 'checker', `${label} background toggle`);
    await click('background'); await click('background');
    await page.screenshot({ path: resolve(OUT, `${label}.png`) });
    // Geometry guards replace the former fixed-count narrow-toolbar test.
    for (const name of ['rotate','copy','print','save','open','background','info','fullscreen','zoom','slider','fit','actual','plus','minus','filmstrip','close']) {
      const control = page.locator(`[data-iv="${name}"]`), r = await control.boundingBox();
      check(r && r.x >= 0 && r.x+r.width <= width+1 && r.y+r.height <= 901, `${label} ${name} reachable`);
    }
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${label} no page horizontal overflow`);
    // Touch Events exercise real gesture handlers, including pinch midpoint motion.
    await click('fit');
    const pinched = await page.locator('.iv-stage').evaluate(el => {
      const r=el.getBoundingClientRect(),cx=r.left+r.width/2,cy=r.top+r.height/2;
      const fire=(type, points, ended=[])=> { const make=pts=>pts.map((p,i)=>new Touch({identifier:i,target:el,clientX:p[0],clientY:p[1]})); el.dispatchEvent(new TouchEvent(type,{touches:make(points),changedTouches:make(ended.length?ended:points),bubbles:true,cancelable:true})); };
      const before=Number(el.closest('.image-viewer').dataset.zoom);
      fire('touchstart',[[cx-30,cy],[cx+30,cy]]);fire('touchmove',[[cx-60,cy],[cx+60,cy]]);fire('touchend',[],[[cx-60,cy],[cx+60,cy]]);
      return Number(el.closest('.image-viewer').dataset.zoom)/before;
    });
    check(Math.abs(pinched-2)<.01, `${label} pinch zoom`);
    const touchPan = await page.locator('.iv-stage').evaluate(el => {
      const r=el.getBoundingClientRect(),cx=r.left+r.width/2,cy=r.top+r.height/2;
      const old=el.querySelector('img').getBoundingClientRect().x;
      const t=x=>new Touch({identifier:0,target:el,clientX:x,clientY:cy});
      for(const [type,points,end] of [['touchstart',[t(cx)],[t(cx)]],['touchmove',[t(cx-70)],[t(cx-70)]],['touchend',[],[t(cx-70)]]]) el.dispatchEvent(new TouchEvent(type,{touches:points,changedTouches:end,bubbles:true,cancelable:true}));
      return el.querySelector('img').getBoundingClientRect().x-old;
    });
    check(Math.abs(touchPan+70)<1 && await page.locator('.iv-count').textContent()==='1 / 3', `${label} zoomed touch pans without navigating`);
    await click('fit');
    await page.locator('.iv-stage').evaluate(el => {
      const r=el.getBoundingClientRect(),t=new Touch({identifier:0,target:el,clientX:r.left+r.width/2,clientY:r.top+r.height/2});
      for(let i=0;i<2;i++) {
        el.dispatchEvent(new TouchEvent('touchstart',{touches:[t],changedTouches:[t],bubbles:true,cancelable:true}));
        el.dispatchEvent(new TouchEvent('touchend',{touches:[],changedTouches:[t],bubbles:true,cancelable:true}));
      }
    });
    check(await zoom()===1, `${label} double tap actual size`);
    await click('fit');
    await page.locator('.iv-stage').evaluate(el => {
      const r=el.getBoundingClientRect(),x=r.left+r.width*.7,y=r.top+r.height/2;
      const t=(cx)=>new Touch({identifier:0,target:el,clientX:cx,clientY:y});
      for(const [type,points,end] of [['touchstart',[t(x)],[t(x)]],['touchmove',[t(x-100)],[t(x-100)]],['touchend',[],[t(x-100)]]]) el.dispatchEvent(new TouchEvent(type,{touches:points,changedTouches:end,bubbles:true,cancelable:true}));
    });
    await ready('800 \u00d7 1200'); check(await page.locator('.iv-count').textContent() === '2 / 3', `${label} swipe next at fit`);
    if (width > 960) {
      await click('minimize'); check(await page.locator('.image-viewer-overlay').isHidden(), `${label} minimize`);
      await page.locator('#minimized-tray .minimized-chip').click(); check(await page.locator('.iv-count').textContent() === '2 / 3', `${label} restore keeps image`);
      await click('maximize'); check(Math.abs((await page.locator('.image-viewer').boundingBox()).width-width)<1, `${label} maximize`); await click('maximize');
      const before = await page.locator('.image-viewer').boundingBox();
      const tb = await page.locator('.iv-title').boundingBox();
      await page.mouse.move(tb.x+tb.width/2,tb.y+tb.height/2);await page.mouse.down();await page.mouse.move(tb.x+tb.width/2-30,tb.y+tb.height/2+20);await page.mouse.up();
      const after=await page.locator('.image-viewer').boundingBox();check(Math.abs(after.x-before.x+30)<1 && Math.abs(after.y-before.y-20)<1, `${label} window drag`);
      // Native CSS resize changes the frame, and a fitted image follows it.
      await page.locator('.image-viewer').evaluate(el => { el.style.width='700px';el.style.height='600px'; });
      await page.waitForTimeout(50); check((await page.locator('.image-viewer').boundingBox()).width===700, `${label} resizable frame`);
      check(await page.evaluate(() => getComputedStyle(document.querySelector('.image-viewer-overlay')).pointerEvents === 'none'), `${label} desktop app stays reachable`);
      await page.setViewportSize({width:390,height:900});
      const mobileBox=await page.locator('.image-viewer').boundingBox();
      check(mobileBox.x===0 && mobileBox.width===390 && mobileBox.height===900,`${label} dragged/resized window adapts to phone viewport`);
      await page.setViewportSize({width,height:900});
    }
    await click('fullscreen');
    await page.waitForFunction(() => !!document.fullscreenElement || document.querySelector('.image-viewer').classList.contains('is-maximized'));
    check(true, `${label} fullscreen`);
    await page.evaluate(() => document.fullscreenElement ? document.exitFullscreen() : Promise.resolve());
    await click('close'); check(await page.locator('.image-viewer').count() === 0, `${label} close`);
    await open(); await ready(); await page.keyboard.press('Escape'); check(await page.locator('.image-viewer').count() === 0, `${label} Escape`);
    // Real formatting + real click: only contiguous chat lines join the list.
    await page.evaluate(({files}) => {
      const root=document.createElement('div');root.id='messages';
      root.innerHTML='<div class="agent-line">'+window.formatAgentText(files[0])+'</div><div class="agent-line">'+window.formatAgentText(files[1])+'</div><div class="agent-line agent-line-prompt">next turn</div><div class="agent-line">'+window.formatAgentText(files[2])+'</div>';
      document.body.append(root);
    }, {files});
    await page.locator('#messages .agent-img').first().click();await ready();
    check(await page.locator('.iv-count').textContent()==='1 / 2', `${label} real chat handler limits message scope`);
    await page.locator('#composer').focus();await page.keyboard.press('ArrowRight');await page.keyboard.type('-');await page.keyboard.press('Escape');
    check(await page.locator('.iv-count').textContent()==='1 / 2' && await page.locator('#composer').inputValue()==='-', `${label} composer keys stay in composer`);
    await click('close');
    await page.evaluate(({sources}) => window.DeskV1LibraryViewer.open([{kind:'image',src:sources[0],title:'Material-study.png'},{kind:'video',play:'ignored'},{kind:'image',src:sources[1],title:'Portrait.png'}],0), {sources});
    await ready();check(await page.locator('.iv-count').textContent()==='1 / 2', `${label} real Desk library passes image-only shelf`);await click('close');
    await page.evaluate(({files}) => {
      window.stripSysPreamble=s=>s;
      const root=document.createElement('div');root.id='prompt';root.className='agent-line agent-line-prompt';
      root.innerHTML=window.escPromptWithImages(files[0]+'\n'+files[1],false);document.body.append(root);
    }, {files});
    await page.locator('#prompt .agent-img').first().click();await ready();
    check(await page.locator('.iv-count').textContent()==='1 / 2',`${label} real user-attachment handler scopes prompt`);await click('close');
    // Drive the real media surface's indexed click rather than a test-only list.
    await page.evaluate(({files})=> {
      window.__realFetch=window.fetch;
      window.fetch=async (...args)=>String(args[0]).endsWith('/media') ? new Response(JSON.stringify({items:[{path:files[0],kind:'image',ts:1},{kind:'diagram',source:'graph TD;A-->B'},{path:files[1],kind:'image',ts:1},{path:files[2],kind:'image',missing:true}]}),{headers:{'Content-Type':'application/json'}}) : window.__realFetch(...args);
      const grid=document.createElement('div');grid.id='media-grid';document.body.append(grid);
      window.setMediaProject('smoke');
    },{files});
    await page.waitForFunction(()=>document.querySelectorAll('#media-grid .media-tile:not(.media-tile-diagram)').length===2);
    await page.locator('#media-grid .media-tile:not(.media-tile-diagram)').nth(1).click();await ready('800 \u00d7 1200');
    check(await page.locator('.iv-count').textContent()==='2 / 2',`${label} real media gallery excludes diagrams and hidden missing images`);await click('close');
    await page.evaluate(()=>{window.fetch=window.__realFetch;document.getElementById('media-grid').remove();});
    // Multiple picture windows and the existing diagram viewer share stacking.
    await open();await ready();
    await page.evaluate(async () => {
      window.mermaid={render:async()=>({svg:'<svg viewBox="0 0 500 300"><rect width="500" height="300" fill="#bc6848"/></svg>'})};
      window.dispatchEvent(new Event('excalidraw-failed'));
      const block=document.createElement('div');block.id='diagram';block.className='mermaid-block';block.dataset.source='graph TD; A-->B';document.body.append(block);
      window._renderAllMermaidPlaceholders(block.parentElement);
    });
    await page.waitForSelector('#diagram svg', {state:'attached'});
    await page.locator('#diagram').dispatchEvent('click');
    await page.waitForSelector('.mermaid-viewer-overlay');
    await page.locator('.mermaid-viewer-toolbar').click({position:{x:8,y:8}});
    await page.keyboard.press('Escape');
    check(await page.locator('.mermaid-viewer-overlay').count()===0 && await page.locator('.image-viewer').count()===1,`${label} Escape closes only top diagram`);
    await click('close');
    await open();await ready();
    await page.evaluate(({sources})=>window._openImageViewer(sources[1]),{sources});
    await page.waitForFunction(()=>document.querySelectorAll('.image-viewer').length===2 && [...document.querySelectorAll('[data-info=dimensions]')].at(-1).textContent==='800 \u00d7 1200');
    await page.keyboard.press('Escape');
    check(await page.locator('.image-viewer').count()===1,`${label} Escape closes only top picture`);await click('close');
    await page.evaluate(({sources})=> {
      window.__realFetch=window.fetch;
      window.fetch=(url,opts)=>String(url).includes('slow=1') ? new Promise(resolve=>setTimeout(()=>window.__realFetch(url).then(resolve),100)) : window.__realFetch(url,opts);
      const slow=sources[0]+'&slow=1';window._openImageViewer(slow,{images:[slow,sources[1]]});
    },{sources});
    await page.locator('.iv-nav-next').click();await ready('800 \u00d7 1200');await page.waitForTimeout(200);
    const portraitSize=(Buffer.from(fixtures[1],'base64').length/1024).toFixed(1)+' KB';
    check(await page.locator('[data-info=size]').textContent()===portraitSize && await page.locator('[data-info=dimensions]').textContent()==='800 \u00d7 1200' && await page.locator('.iv-title').textContent()==='Portrait.png',`${label} late metadata cannot replace current picture`);
    await page.evaluate(()=>{window.fetch=window.__realFetch;});
    await page.evaluate(()=>{window.__savedClipboard=navigator.clipboard.write;navigator.clipboard.write=async()=>{throw new Error('permission denied');};});
    await click('copy');await page.waitForFunction(()=>document.querySelector('.iv-notice')?.textContent.includes('permission denied'));
    check(true,`${label} clipboard denial is visible`);
    await page.evaluate(()=>{navigator.clipboard.write=window.__savedClipboard;});await click('close');
    await page.evaluate(({sources}) => window._openImageViewer(sources[0]), {sources});await ready();
    check(await page.locator('.iv-nav').count()===0 && await page.locator('[data-iv=filmstrip]').isDisabled(), `${label} src-only single image compatibility`);await click('close');
    await page.evaluate(() => window._openImageViewer('/api/serve-image?path=broken'));
    await page.waitForFunction(() => document.querySelector('.iv-notice')?.textContent.includes('could not be loaded'));
    check(await page.locator('.iv-image').isHidden(), `${label} load failure is visible`);await click('close');
    const remaining=await page.evaluate(before=>Object.entries(window.__listeners).filter(([k,v])=>v!==(before[k]||0)),initialListeners);
    check(!remaining.length,`${label} no document listener leak (${JSON.stringify(remaining)})`);
    // Folder lookup belongs to chat/source opens only (the two real handler opens above);
    // explicit lists (Media, Desk library) and src-only calls never enumerate a folder.
    check(requests.filter(p => p === '/api/serve-image/siblings').length === 2, `${label} folder lookup only for the two chat source opens`);
    check(!errors.length, `${label} no runtime exceptions (${errors.join('; ')})`);
    await context.close();
  }
  console.log(`PASS ${checks} native image viewer checks; screenshots: ${OUT}`);
} finally { await browser.close(); }
