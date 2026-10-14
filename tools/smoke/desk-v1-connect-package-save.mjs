// Real package wizard/passcode/result modules; all API replies are local fixtures.
import assert from 'node:assert/strict';
import {readFileSync, mkdirSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
import {loadStaticJsCss} from './_static.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const files=loadStaticJsCss(root), out=resolve(root,'_scratch/mc1097');
const styles=[...readFileSync(resolve(root,'static/index.html'),'utf8').matchAll(/<link rel="stylesheet" href="(\/static\/css\/[^\"]+)"[^>]*>/g)].map(m=>m[0]).join('');
mkdirSync(out,{recursive:true});
const browser=await chromium.launch({headless:true});
try {
  for(const width of [1440,390]) {
    const page=await browser.newPage({viewport:{width,height:900}}), errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/*',route=>{
      const path=new URL(route.request().url()).pathname;
      if(files[path]) return route.fulfill({contentType:files[path][0],body:files[path][1]});
      if(path==='/api/local-auth/status') return route.fulfill({json:{configured:true}});
      if(path==='/api/desk/connect/custom/commit') {
        return page.evaluate(body=>{window.commitBody=body;window.commitCount++;},route.request().postDataJSON())
          .then(()=>new Promise(resolve=>{page.once('release-commit',()=>resolve(route.fulfill({status:201,json:{state:'setup_failed',message:'The "build" step failed; the connection was not saved.',notice:'Vault passphrase note must not appear.'}})));}));
      }
      return route.fulfill({contentType:'text/html',body:`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">${styles}
        <main style="padding:14px;max-width:780px;margin:auto" id="host"></main><div id="modal-layer" class="modal-layer"></div>
        <script>window.API_BASE='';window.nextModalZ=100;window.openModals=new Map();window.commitCount=0;
        window.esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
        window._clampModalSize=(el,w)=>{el.style.width=Math.min(w,innerWidth-24)+'px';};
        window.centerModalElement=el=>{Object.assign(el.style,{position:'fixed',left:'50%',top:'50%',transform:'translate(-50%,-50%)'});};
        window.focusModal=()=>{};window.closeModalById=id=>{document.querySelector('[data-modal-id="'+id+'"]').remove();openModals.delete(id);};
        </script><script type="module">
        import '/static/js/masked-input.js';import '/static/js/human-proof-modal.js';
        import '/static/js/desk-v1-connect-wizard.js';import '/static/js/desk-v1-connect-github.js';
        import '/static/js/desk-v1-connect-custom-deps.js';import '/static/js/desk-v1-connect-package-step.js';
        const W=window.DeskV1ConnectWizard;window.DeskV1ConnectPermissionsStep={reach:()=>({scope:'global'})};
        W.registerScreen({id:'fixture-permissions',step:'permissions',match:()=>true,title:()=> 'Permissions',body:()=>'<p>Agents in every project.</p>',primary:()=>({label:'Continue'})});
        const ctx={live:true,api:async(method,path,body)=>{
          if(path.endsWith('/stage'))return {stage_id:'fixture',command:'node',args:['dist/cli.js'],credentials:[],build_required:true,required_steps:['script:build']};
          const selected=new Set(body.approve_scripts||[]);
          return {request_id:'fixture',fingerprint:'sha256:'+selected.size,title:'YouTube',server_name:'youtube',command:{command:'node',args:['dist/cli.js']},package:{version:'fixed',source:'GitHub',registry:'GitHub'},reach:{who:'Agents in every project.',local_code:''},required_steps:['script:build'],
            scripts:[{id:'dependencies:0',argv:['npm','ci'],script:'dependencies',body:'npm ci --ignore-scripts',approvable:true,approved:selected.has('dependencies:0')},
                     {id:'script:build',script:'build',body:'tsc',required:true,approvable:true,approved:selected.has('script:build')}].map(s=>({...s,package:'youtube',version:'fixed',path:''}))};
        },repaint:()=>{host.innerHTML=W.html(ctx);W.bind(host,ctx);}};
        W.setEnabled(true);window.DeskV1ConnectCopy.enable();W.resume({picker:[]},{service:'youtube',type:'mcp',variant:'custom-npm'},'setup');ctx.repaint();
        </script>`});
    });
    await page.goto('http://mc.smoke.test');
    await page.fill('[data-pk-package]','https://github.com/ZubeidHendricks/youtube-mcp-server');await page.click('[data-pk-detect]');
    await page.waitForSelector('[data-pk-build]');await page.click('[data-cfw-primary]');await page.click('[data-cfw-primary]');
    await page.waitForSelector('[data-pk-command]',{state:'attached'});await page.click('[data-cfw-primary]');
    await page.waitForSelector('[data-cu-script-tick="script:build"]');
    assert(await page.locator('[data-pk-required]').isVisible());
    await page.click('[data-cfw-primary]');await page.check('[data-pk-approve]');
    assert(await page.isDisabled('[data-cfw-primary]'));
    assert.match(await page.locator('[data-pk-required-missing]').innerText(),/required build/);
    assert.equal(await page.evaluate(()=>commitCount),0);
    await page.click('[data-pk-prev]');await page.check('[data-cu-script-tick="script:build"]');
    await page.waitForSelector('[data-cu-script-tick="script:build"]:checked');
    await page.check('[data-cu-script-tick="dependencies:0"]');
    await page.waitForSelector('[data-cu-script-tick="dependencies:0"]:checked');
    await page.click('[data-cfw-primary]');await page.check('[data-pk-approve]');
    assert(!(await page.isDisabled('[data-cfw-primary]')));await page.click('[data-cfw-primary]');
    await page.fill('input[id^="hp-passcode-"]','fixture-only-passcode');await page.click('.modal-content .btn-add');
    await page.waitForSelector('[data-hp-busy]');await page.waitForFunction(()=>commitCount===1);
    assert.equal(await page.locator('[data-hp-busy] .desk-v1-cfk-spinner').count(),1);
    assert.match(await page.locator('[data-hp-busy]').innerText(),/Setting up and saving/);
    assert(await page.isDisabled('input[id^="hp-passcode-"]'));
    assert.equal(await page.inputValue('input[id^="hp-passcode-"]'),'');
    assert(await page.evaluate(()=>{const busy=document.querySelector('[data-hp-busy]'), input=document.querySelector('[data-mc-masked]');return busy.getBoundingClientRect().top<input.getBoundingClientRect().top;}));
    await page.evaluate(()=>{const id=document.querySelector('[data-modal-id]').dataset.modalId;window._hpSubmit(id);window._hpCancel(id);});
    assert.equal(await page.evaluate(()=>commitCount),1);
    assert.equal(await page.locator('[data-hp-busy]').count(),1);
    await page.screenshot({path:resolve(out,'passcode_busy_'+width+'.png')});
    page.emit('release-commit');await page.waitForSelector('[data-pk-result="setup_failed"]');
    assert.match(await page.locator('[data-pk-result]').innerText(),/^Not saved: The "build" step failed/);
    assert.equal(await page.locator('[data-cfw-title]').innerText(),'Not saved');
    assert.equal(await page.locator('[data-pk-notice], [data-pk-start-note]').count(),0);
    assert.equal(await page.evaluate(()=>window.DeskV1ConnectPackageStep.summary().saved),false);
    assert(!(await page.locator('[data-cfw]').innerText()).includes('Saved, setup failed'));
    await page.screenshot({path:resolve(out,'setup_failed_'+width+'.png')});
    const fit=await page.evaluate(()=>({over:document.documentElement.scrollWidth-innerWidth,offenders:[...document.querySelectorAll('*')].filter(el=>el.getBoundingClientRect().right>innerWidth+1).map(el=>[el.tagName,el.className,el.getBoundingClientRect().right]).slice(0,6)}));
    assert(fit.over<=0,JSON.stringify(fit));
    assert.deepEqual(errors,[]);console.log('PASS GitHub Save required/busy/failed '+width);await page.close();
  }
} finally {await browser.close();}
