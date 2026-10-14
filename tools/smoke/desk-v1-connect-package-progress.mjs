// Real Connect frame + package modules, delayed read-only API, no live service.
import assert from 'node:assert/strict';
import {readFileSync, mkdirSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
import {loadStaticJsCss} from './_static.mjs';
const root = fileURLToPath(new URL('../../', import.meta.url));
const files = loadStaticJsCss(root), out = resolve(root, '_scratch/mc1095');
mkdirSync(out, {recursive:true});
const browser = await chromium.launch({headless:true});
try {
  for (const width of [1440,390]) {
    const page = await browser.newPage({viewport:{width,height:900}}), errors=[];
    page.on('pageerror', e=>errors.push(e.message));
    await page.route('**/*', route=> {
      const path=new URL(route.request().url()).pathname;
      if(files[path]) return route.fulfill({contentType:files[path][0],body:files[path][1]});
      return route.fulfill({contentType:'text/html',body:`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/app.css"><link rel="stylesheet" href="/static/css/desk-v1-connections.css"><link rel="stylesheet" href="/static/css/desk-v1-connect-wizard.css"><link rel="stylesheet" href="/static/css/desk-v1-connect-package-step.css"><main style="padding:12px;max-width:1040px;margin:auto" id="host"></main><script type="module">
        import '/static/js/desk-v1-connect-wizard.js';
        import '/static/js/desk-v1-connect-github.js';
        import '/static/js/desk-v1-connect-package-step.js';
        const W=window.DeskV1ConnectWizard;
        window.calls=[];window.pending=[];
        const ctx={live:true,api:(method,path,body)=>{calls.push({path,body});return new Promise((resolve,reject)=>pending.push({resolve,reject}));},repaint:()=>{host.innerHTML=W.html(ctx);W.bind(host,ctx);}};
        window.reset=()=>{W.resume({picker:[]},{service:'youtube',type:'mcp',variant:'custom-npm'},'setup');ctx.repaint();};
        W.setEnabled(true);window.DeskV1ConnectCopy.enable();reset();
      </script>`});
    });
    await page.goto('http://mc.smoke.test');
    await page.waitForSelector('[data-pk-package]');
    for (const action of ['[data-pk-detect]','[data-cfw-primary]']) {
      await page.fill('[data-pk-package]','https://github.com/ZubeidHendricks/youtube-mcp-server');
      await page.click(action);
      await page.waitForSelector('[data-pk-detect-state="busy"]');
      assert(await page.isDisabled('[data-pk-detect]'));
      assert(await page.isDisabled('[data-cfw-primary]'));
      assert.equal(await page.locator('.desk-v1-cfk-spinner').count(),1);
      assert.match(await page.locator('[data-pk-detect]').innerText(),/Checking/);
      await page.screenshot({path:resolve(out,`progress_${action.includes('primary')?'continue':'github'}_${width}.png`)});
      await page.evaluate(()=>pending.shift().reject(new Error('Repository unavailable')));
      await page.waitForSelector('[data-cfw-msg]');
      assert(!(await page.isDisabled('[data-pk-detect]')));
      assert.match(await page.locator('[data-cfw-msg]').innerText(),/Repository unavailable/);
      await page.evaluate(()=>reset());
    }
    await page.fill('[data-pk-package]','example-mcp');await page.click('[data-pk-detect]');
    await page.waitForSelector('[data-pk-detect-state="busy"]');
    assert.match(await page.locator('[role="status"]').innerText(),/package listing/);
    await page.screenshot({path:resolve(out,`progress_npm_${width}.png`)});
    await page.fill('[data-pk-package]','changed-mcp');
    await page.evaluate(()=>pending.shift().resolve({alternatives:[{package:{ecosystem:'npm',name:'old'}}]}));
    assert.equal(await page.inputValue('[data-pk-package]'),'changed-mcp');
    assert.equal(await page.locator('[data-pk-detect-state]').count(),0);
    assert.deepEqual(errors,[]);
    console.log(`PASS package progress ${width}`);await page.close();
  }
} finally {await browser.close();}
