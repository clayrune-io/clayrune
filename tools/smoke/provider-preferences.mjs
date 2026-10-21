#!/usr/bin/env node
// Real Settings/Floor/project UI against bounded, non-executing API fixtures.
import assert from 'node:assert/strict';
import {readFileSync, mkdirSync, writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, resolve} from 'node:path';
import {chromium} from 'playwright';
import {loadStaticJsCss} from './_static.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const STATIC = loadStaticJsCss(ROOT);
const SHOTS = resolve(ROOT, '_scratch/provider-preferences');
mkdirSync(SHOTS, {recursive: true});
const initial = () => ({setup_completed: true, default_provider: 'codex', user_name: 'Example user',
  agent_name: 'Default helper', agent_avatar: '🙂', agent_max_turns: 0,
  agent_permission_mode: '', agent_emojis_enabled: false, sticky_agent_settings: true,
  codex_unattended_sandbox: true, auto_model_enabled: false,
  provider_defaults: {claude: {model: 'claude-balanced', effort: 'low'}, codex: {model: 'gpt-balanced', effort: 'high'}}});
const registry = [
  {name:'claude', display_name:'Claude Code', models:[{id:'claude-balanced',label:'Claude balanced'},{id:'claude-fast',label:'Claude fast'}], efforts:['low','medium','high','xhigh','max']},
  {name:'codex', display_name:'Codex CLI', models:[{id:'gpt-balanced',label:'GPT balanced'},{id:'gpt-fast',label:'GPT fast'}], efforts:['low','medium','high']},
  {name:'qwen', display_name:'Qwen Code', models:[{id:'qwen-code',label:'Qwen code'}], efforts:[]},
  {name:'kiro', display_name:'Kiro', models:[], efforts:[], model_selection:false},
];
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    let config = initial();
    let project = {id:'prefs_test', name:'Preference test', emoji:'', status:'active', tags:[], provider:'', provider_overrides:{}};
    const puts = [], posts = [], errors = [];
    const ctx = await browser.newContext({viewport:{width, height:1000}});
    const page = await ctx.newPage();
    await page.addInitScript(() => localStorage.setItem('walkthrough_done','1'));
    page.on('pageerror', error => errors.push(error.message));
    const fixtureView = projectId => ({default_provider:config.default_provider, project_provider:projectId ? project.provider : '',
      providers:registry.map(provider => {
        const defaults = config.provider_defaults[provider.name] || {};
        const overrides = projectId ? project.provider_overrides[provider.name] || {} : {};
        const own = Object.hasOwn(overrides,'model');
        return {...provider, tiers:provider.models.length ? {balanced:provider.models[0].id} : {}, defaults, overrides,
          effective:{provider:provider.name, model:own ? overrides.model : defaults.model || '',
            requested_model:own ? overrides.model : defaults.model || '', model_source:own ? 'project' : 'global',
            effort:Object.hasOwn(overrides,'effort') ? overrides.effort : defaults.effort || ''}};
      })});
    const patch = (target, changes) => {
      for (const [provider, fields] of Object.entries(changes)) {
        target[provider] ||= {};
        for (const [key,value] of Object.entries(fields)) value === null ? delete target[provider][key] : target[provider][key] = value;
      }
    };
    await page.route('**/*', route => {
      const req = route.request(), url = new URL(req.url()), path = url.pathname;
      const json = (data, status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
      if (path === '/') return route.fulfill({status:200,contentType:'text/html',body:readFileSync(resolve(ROOT,'static/index.html'),'utf8')});
      if (STATIC[path]) return route.fulfill({status:200,contentType:STATIC[path][0],body:STATIC[path][1]});
      if (path === '/api/projects') return json([project]);
      if (path === '/api/agent/providers') return json({providers:registry.map(p => ({...p,installed:true,auth_status:'ok',capabilities:{},default:p.name === config.default_provider})), default:config.default_provider});
      if (path === '/api/engine/preferences') return json(fixtureView(url.searchParams.get('project_id')));
      if (path === '/api/config') {
        if (req.method() === 'PUT') {
          const body = req.postDataJSON();
          if (body.provider_defaults?.codex?.model?.startsWith('claude-')) return json({error:'Model belongs to another provider'},400);
          puts.push(body);
          if (body.provider_defaults) patch(config.provider_defaults, body.provider_defaults);
          for (const [key,value] of Object.entries(body)) if (!['provider_defaults','passcode'].includes(key)) config[key] = value;
          return json({ok:true});
        }
        return json(config);
      }
      if (path === '/api/project/prefs_test' && req.method() === 'POST') {
        const body = req.postDataJSON(); posts.push(body);
        if (body.provider_overrides) patch(project.provider_overrides, body.provider_overrides);
        if ('provider' in body) project.provider = body.provider;
        return json({ok:true});
      }
      if (path === '/api/floor') return json({rooms:[],quiet:[],bench:[],counts:{rooms:0,figures:0,quiet:0,bench:0},poll_seconds:60});
      if (path === '/api/avatars') return json({figures:[]});
      if (path === '/api/local-auth/status') return json({configured:true});
      return route.abort();
    });
    const proof = async cancel => {
      await page.waitForSelector('[data-modal-id^="__human-proof-"]');
      await page.evaluate(cancel => {
        const id = document.querySelector('[data-modal-id^="__human-proof-"]').dataset.modalId;
        if (cancel) window._hpCancel(id);
        else { document.getElementById('hp-passcode-' + id).value = 'fixture-passcode'; window._hpSubmit(id); }
      }, cancel);
      await page.waitForSelector('[data-modal-id^="__human-proof-"]',{state:'detached'});
    };
    const settingsSection = async (category,title) => page.evaluate(({category,title}) => {
      window.drillSettings(category);
      const index = Array.from(document.querySelector(`[data-cat="${category}"]`).children).filter(el => el.classList.contains('settings-section'))
        .findIndex(el => el.querySelector('.settings-section-title')?.textContent.trim() === title);
      window.drillSettingsSub(index);
    }, {category,title});
    await page.goto('http://mc.smoke.test/',{waitUntil:'domcontentloaded'});
    await page.waitForFunction(() => typeof window.openSettings === 'function' && typeof allProjects !== 'undefined' && allProjects.some(p => p.id === 'prefs_test'));
    await page.evaluate(() => window.openSettings());
    assert.equal(await page.locator('[data-cat="agent"]').count(),0,'Retired Agent pane is absent');
    assert.equal(await page.locator('#settings-master button').filter({hasText:/^Agent$/}).count(),0);
    await settingsSection('providers','Model defaults');
    const codex = page.locator('#engine-pref-global-codex-model');
    assert.equal(await page.locator('#engine-pref-global-kiro-model').count(),0,'Native-only runtimes have no inert model picker');
    assert.equal(await codex.inputValue(),'gpt-balanced');
    assert.ok(!(await codex.locator('option').allTextContents()).some(label => label.includes('Claude')));
    await codex.selectOption('gpt-fast'); await proof(true);
    await page.waitForFunction(() => document.querySelector('#engine-pref-global-codex-model')?.value === 'gpt-balanced');
    assert.equal(puts.length,0,'Cancelled proof causes no write');
    await codex.selectOption('gpt-fast'); await proof(false);
    await page.waitForFunction(() => document.querySelector('#engine-pref-global-codex-model')?.value === 'gpt-fast');
    assert.equal(config.provider_defaults.claude.model,'claude-balanced');
    assert.equal(config.provider_defaults.codex.effort,'high');
    assert.deepEqual(puts[0].provider_defaults,{codex:{model:'gpt-fast'}},'Only the changed field was sent');
    await codex.selectOption('__custom__');
    const custom = codex.locator('..').locator('input');
    await custom.fill('claude-fast'); await custom.press('Tab'); await proof(false);
    await page.waitForFunction(() => document.querySelector('#engine-pref-global-codex-model')?.value === 'gpt-fast');
    assert.equal(config.provider_defaults.codex.model,'gpt-fast','Invalid pair did not become a saved preference');
    await page.locator('#toast-container .toast').evaluateAll(elements => elements.forEach(el => el.remove()));
    await page.screenshot({path:resolve(SHOTS,`providers-${width}.png`),animations:'disabled'});
    await page.evaluate(() => window.openAgentSettingsDialog('prefs_test'));
    const projectModel = page.locator('#engine-pref-prefs_test-codex-model');
    assert.equal(await projectModel.inputValue(),'__inherit__');
    await projectModel.selectOption('');
    await page.waitForFunction(() => document.querySelector('#engine-pref-prefs_test-codex-model')?.value === '');
    assert.equal(project.provider_overrides.codex.model,'','Native explicitly stops inheritance');
    await projectModel.selectOption('__inherit__');
    await page.waitForFunction(() => document.querySelector('#engine-pref-prefs_test-codex-model')?.value === '__inherit__');
    assert.ok(!Object.hasOwn(project.provider_overrides.codex,'model'));
    assert.equal(puts.length,1,'Project edits do not change global config');
    await page.locator('#toast-container .toast').evaluateAll(elements => elements.forEach(el => el.remove()));
    await page.screenshot({path:resolve(SHOTS,`project-${width}.png`),animations:'disabled'});
    await page.evaluate(() => window.closeAgentSettingsDialog());
    await page.evaluate(() => window.closeModalById('__settings'));
    await page.evaluate(() => window.openFloor());
    await page.locator('[data-modal-id="__floor"]').getByRole('button',{name:'Floor defaults',exact:true}).click();
    await page.waitForSelector('#floor-pref-agent_name');
    await page.locator('#floor-pref-agent_name').fill('Draft only');
    await page.locator('[data-modal-id="__floor_preferences"]').getByRole('button',{name:'Cancel',exact:true}).click();
    assert.equal(config.agent_name,'Default helper');
    await page.evaluate(() => window.openFloorPreferences());
    await page.locator('#floor-pref-agent_name').fill('Saved helper');
    await page.getByRole('button',{name:'Save',exact:true}).click(); await proof(false);
    await page.waitForSelector('[data-modal-id="__floor_preferences"]',{state:'detached'});
    assert.deepEqual(Object.keys(puts[1]).filter(key => key !== 'passcode'),['agent_name']);
    assert.equal(config.agent_name,'Saved helper');
    await page.evaluate(() => window.openFloorPreferences());
    assert.equal(await page.locator('#floor-pref-agent_name').inputValue(),'Saved helper');
    await page.locator('#toast-container .toast').evaluateAll(elements => elements.forEach(el => el.remove()));
    await page.screenshot({path:resolve(SHOTS,`floor-${width}.png`),animations:'disabled'});
    await page.evaluate(() => window.closeModalById('__floor_preferences'));
    await page.evaluate(() => window.closeFloor());
    await page.evaluate(() => window.openSettings());
    await page.locator('#settings-search').fill('brief replies');
    assert.ok(await page.locator('#settings-moved-results').isVisible(),'Settings search links to relocated controls');
    await page.locator('#settings-search').fill('');
    await settingsSection('system','Security');
    assert.ok(await page.locator('#codex-sandbox-row').isVisible(),'Human-gated sandbox control remains reachable');
    await page.locator('#toast-container .toast').evaluateAll(elements => elements.forEach(el => el.remove()));
    await page.screenshot({path:resolve(SHOTS,`security-${width}.png`),animations:'disabled'});
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    assert.equal(overflow,false,'No page overflow on either viewport');
    const inventory = await page.evaluate(() => [...document.querySelectorAll('#settings-detail .settings-section')].map(section => ({
      category:section.closest('[data-cat]')?.dataset.cat, section:section.querySelector('.settings-section-title')?.textContent.trim(),
      labels:[...section.querySelectorAll('.settings-label')].map(el => el.textContent.trim())})));
    writeFileSync(resolve(SHOTS,`inventory-${width}.json`),JSON.stringify(inventory,null,2));
    assert.deepEqual(errors.filter(error => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(error)),[]);
    console.log(`PASS ${width}px: provider fields, cancellation, invalid tuple, project native/inherit, Floor save/cancel, search, security and overflow`);
    await ctx.close();
  }
} finally { await browser.close(); }
