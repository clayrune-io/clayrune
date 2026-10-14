// Real Connect frame + package modules, delayed read-only API, no live service.
import assert from 'node:assert/strict';
import {readFileSync, mkdirSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
import {loadStaticJsCss} from './_static.mjs';
const root = fileURLToPath(new URL('../../', import.meta.url));
const files = loadStaticJsCss(root), out = resolve(root, '_scratch/mc1095');
const styles=[...readFileSync(resolve(root,'static/index.html'),'utf8').matchAll(/<link rel="stylesheet" href="(\/static\/css\/[^\"]+)"[^>]*>/g)].map(match=>match[0]).join('');
mkdirSync(out, {recursive:true});
const browser = await chromium.launch({headless:true});
try {
  for (const width of [1440,390]) {
    const page = await browser.newPage({viewport:{width,height:900}}), errors=[];
    page.on('pageerror', e=>errors.push(e.message));
    await page.route('**/*', route=> {
      const path=new URL(route.request().url()).pathname;
      if(files[path]) return route.fulfill({contentType:files[path][0],body:files[path][1]});
      return route.fulfill({contentType:'text/html',body:`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">${styles}<main style="padding:12px;max-width:1040px;margin:auto" id="host"></main><script type="module">
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
    await page.evaluate(()=>reset());
    const value='https://github.com/ZubeidHendricks/youtube-mcp-server';
    await page.fill('[data-pk-package]',value);await page.click('[data-pk-detect]');
    await page.evaluate(()=>pending.shift().resolve({stage_id:'fixture',command:'node',args:['mcp/vault/transport.js'],credentials:[]}));
    await page.waitForSelector('[data-pk-pkgline]');
    assert.equal(await page.locator('[data-pk-pkgline] strong').textContent(),value);
    assert.equal(await page.inputValue('[data-pk-args]'),'mcp/vault/transport.js');
    assert.equal(await page.locator('[data-pk-entry]').count(),0);
    assert(await page.locator('#pk-command-help').isVisible());
    assert(await page.locator('#pk-args-help').isVisible());
    await page.click('[data-pk-help-open]');await page.click('[data-pk-help-ask]');
    await page.waitForSelector('[data-pk-help-ask][disabled]');
    assert.equal(await page.evaluate(()=>calls.at(-1).path),'/api/desk/connect/custom/setup-help');
    assert.equal(await page.evaluate(()=>calls.at(-1).body.fields.args[0]),'mcp/vault/transport.js');
    await page.evaluate(()=>pending.shift().resolve({content:{answer:'Start this package with node and its compiled file; select the build step in Review first. YOUTUBE_API_KEY is the credential name.',warning:'Suggestions need your review; nothing is saved.',suggestions:{command:'python',entry:'',args:['dist/cli.js'],credentials:['YOUTUBE_API_KEY']}}}));
    await page.waitForSelector('[data-pk-help-answer]');
    // A proposed value never changes a field until accepted, and never overwrites edits.
    assert.equal(await page.inputValue('[data-pk-command-input]'),'node');
    assert.equal(await page.locator('[data-pk-cred-env]').count(),0);
    await page.screenshot({path:resolve(out,`helper_${width}.png`),fullPage:true});
    await page.click('[data-pk-help-accept]');
    assert.equal(await page.inputValue('[data-pk-command-input]'),'node');
    assert.equal(await page.inputValue('[data-pk-args]'),'mcp/vault/transport.js');
    assert.equal(await page.inputValue('[data-pk-cred-env="0"]'),'YOUTUBE_API_KEY');
    assert.equal(await page.inputValue('[data-pk-cred-vault="0"]'),'');
    await page.fill('[data-pk-help-question]','Why is that needed?');await page.click('[data-pk-help-ask]');
    assert.match(await page.evaluate(()=>calls.at(-1).body.previous_answer),/compiled file/);
    await page.evaluate(()=>pending.shift().reject(new Error('Help unavailable')));
    await page.waitForSelector('[data-pk-helper] [role="alert"]');
    assert(!(await page.isDisabled('[data-pk-help-ask]')));
    assert.equal(await page.evaluate(()=>calls.some(c=>/commit|review/.test(c.path))),false);
    // A late model answer after changing package is discarded.
    await page.click('[data-pk-help-ask]');await page.click('[data-pk-change]');
    await page.fill('[data-pk-package]','next-package');
    await page.evaluate(()=>pending.shift().resolve({content:{answer:'old answer',suggestions:{}}}));
    assert.equal(await page.locator('[data-pk-help-answer]').count(),0);
    // Copy is applied twice (body, then bind); all arbitrary rendered values stay literal.
    assert.equal(await page.evaluate(()=>{
      const C=window.DeskV1ConnectCopy, el=document.createElement('div');
      el.innerHTML=C.body('<div data-cfw-actions></div><p>mcp-vault-route</p><dd>OAuth API</dd><label>mcp-vault<input value="MCP route"></label><span data-connect-value>https://x.test/MCP/vault</span><textarea>MCP transport</textarea>');
      C.bind(el);return [el.querySelector('p').textContent,el.querySelector('dd').textContent,el.querySelector('input').value,el.querySelector('span').textContent,el.querySelector('textarea').value].join('|');
    }),'mcp-vault-route|OAuth API|MCP route|https://x.test/MCP/vault|MCP transport');
    assert.deepEqual(errors,[]);
    console.log(`PASS package progress ${width}`);await page.close();
  }
} finally {await browser.close();}
