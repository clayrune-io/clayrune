#!/usr/bin/env node
// Real Settings/Floor/project UI against bounded, non-executing API fixtures.
import assert from 'node:assert/strict';
import {readFileSync, mkdirSync, writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, resolve} from 'node:path';
import {chromium} from 'playwright';
import {loadStaticJsCss} from './_static.mjs';
import {checkProjectPreferenceRaces} from './_project-preferences-race.mjs';

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
        return {...provider, tiers:provider.models.length ? {balanced:provider.models[0].id} : {}, defaults, overrides, automatic_background_model:provider.models.at(-1)?.id || '',
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
      if (path === '/api/agent/providers') return json({providers:registry.map(p => ({...p,tiers:p.models.length ? {balanced:p.models[0].id} : {},installed:true,auth_status:'ok',capabilities:{},default:p.name === config.default_provider})), default:config.default_provider});
      if (path === '/api/characters/global/example') return json({name:'example',body:'Example instructions',engine:{provider:'codex',model:'tier:balanced'}});
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
    await settingsSection('providers','Connections');
    assert.equal(await page.locator('[data-cat="providers"] > .settings-section > .settings-section-title').filter({hasText:/^Model defaults$/}).count(),0,'Model defaults has no separate submenu');
    assert.equal(await page.locator('#settings-providers-section #engine-pref-global-codex-model').count(),1,'Model defaults belong to Connections');
    const codex = page.locator('#engine-pref-global-codex-model');
    const codexCard = page.locator('.prov-row[data-provider="codex"]');
    assert.equal(await codexCard.locator('#engine-pref-global-codex-model').count(),1,'Model defaults live inside their own vendor card');
    assert.equal(await page.locator('#settings-providers-section > .engine-preference-group').count(),0,'No detached defaults list follows the vendor cards');
    const advanced = codexCard.locator('.engine-provider-advanced');
    assert.equal(await advanced.getAttribute('open'),null,'Internal-task options start collapsed');
    assert.equal(await codexCard.locator('#engine-pref-global-codex-background_model').isVisible(),false);
    assert.equal(await page.locator('#engine-pref-global-kiro-model').count(),0,'Native-only runtimes have no inert model picker');
    assert.equal(await codex.inputValue(),'gpt-balanced');
    assert.equal(await codex.locator('option[value="tier:balanced"]').textContent(),'Auto-update balanced: GPT balanced (gpt-balanced)');
    assert.match(await advanced.locator('.engine-tracking-help').textContent(),/aliases.*-latest.*versioned model ID/);
    await advanced.locator('summary').click();
    assert.match(await advanced.textContent(),/increase token cost.*recommended/);
    assert.equal(await advanced.locator('#engine-pref-global-codex-background_model').inputValue(),'__inherit__');
    assert.match(await page.locator('#settings-providers-section .engine-effective').nth(1).textContent(),/Model requested at launch: GPT balanced \(gpt-balanced\)/);
    assert.equal(await page.evaluate(() => window.EnginePreferences.modelLabel({models:[],tiers:{best:'opus'}},'tier:best')),'Auto-update best: opus','An alias is shown honestly without inventing a version');
    assert.equal(await page.evaluate(() => window.EnginePreferences.modelLabel({models:[],tiers:{}},'tier:best')),'Auto-update best: no model mapped');
    assert.ok(!(await codex.locator('option').allTextContents()).some(label => label.includes('Claude')));
    await codex.selectOption('gpt-fast');
    assert.equal(await page.locator('[data-modal-id^="__human-proof-"]').count(),0,'Editing a vendor model does not prompt');
    await page.locator('#vendor-draft-save').click(); await proof(true);
    await page.waitForFunction(() => document.querySelector('#engine-pref-global-codex-model')?.value === 'gpt-fast');
    assert.equal(config.provider_defaults.codex.model,'gpt-balanced','Canceled save preserves applied preferences and keeps the draft');
    assert.equal(puts.length,0,'Cancelled proof causes no write');
    await page.locator('#vendor-draft-save').click(); await proof(false);
    await page.waitForFunction(() => document.querySelector('#vendor-draft-bar')?.hidden);
    await page.waitForFunction(() => document.querySelector('#engine-pref-global-codex-model')?.value === 'gpt-fast');
    assert.equal(config.provider_defaults.claude.model,'claude-balanced');
    assert.equal(config.provider_defaults.codex.effort,'high');
    assert.deepEqual(puts[0].provider_defaults,{codex:{model:'gpt-fast'}},'Only the changed field was sent');
    await codex.selectOption('__custom__');
    const custom = codex.locator('..').locator('input');
    await custom.fill('claude-fast'); await custom.press('Tab');
    await page.locator('#vendor-draft-save').click(); await proof(false);
    await page.waitForFunction(() => document.querySelector('.vendor-draft-status')?.textContent.includes('another provider'));
    assert.equal(await codex.inputValue(),'claude-fast','Validation failure keeps the draft for correction');
    await page.locator('#vendor-draft-cancel').click();
    await page.waitForFunction(() => document.querySelector('#engine-pref-global-codex-model')?.value === 'gpt-fast');
    assert.equal(config.provider_defaults.codex.model,'gpt-fast','Invalid pair did not become a saved preference');
    await page.locator('#toast-container .toast').evaluateAll(elements => elements.forEach(el => el.remove()));
    await page.screenshot({path:resolve(SHOTS,`providers-${width}.png`),animations:'disabled'});
    await page.evaluate(() => window.openAgentSettingsDialog('prefs_test'));
    const projectModel = page.locator('#engine-pref-prefs_test-codex-model');
    assert.equal(await projectModel.inputValue(),'__inherit__');
    assert.match(await projectModel.locator('option[value="__inherit__"]').textContent(),/gpt-fast/,'Inherited preferences disclose the actual request ID');
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
    await page.evaluate(() => window.openPersonaEditor('prefs_test','global','example'));
    assert.equal(await page.locator('#pe-model option[value="tier:balanced"]').textContent(),'Auto-update balanced: GPT balanced (gpt-balanced)','Agent-type tier labels use the same model mapping');
    await page.locator('#pe-cancel').click();
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
    await settingsSection('providers','Connections');
    await page.locator('#engine-advanced-codex > summary').click();
    await page.locator('#engine-pref-global-codex-background_model').selectOption('gpt-balanced');
    await page.locator('#vendor-draft-save').click(); await proof(false);
    await page.waitForFunction(() => document.querySelector('#vendor-draft-bar')?.hidden);
    await page.waitForFunction(() => document.querySelector('#engine-pref-global-codex-background_model')?.value === 'gpt-balanced');
    assert.equal(await page.locator('#engine-advanced-codex').getAttribute('open'),'','Advanced stays open after a save');
    const reset = () => page.locator('#engine-advanced-codex').getByRole('button',{name:'Use CR automatic for all internal tasks',exact:true}).click();
    await reset(); await page.locator('#vendor-draft-save').click(); await proof(true);
    await page.waitForFunction(() => document.querySelector('#engine-pref-global-codex-background_model')?.value === '__inherit__');
    assert.equal(config.provider_defaults.codex.background_model,'gpt-balanced','Canceled reset preserves overrides');
    await page.locator('#vendor-draft-save').click(); await proof(false);
    await page.waitForFunction(() => document.querySelector('#vendor-draft-bar')?.hidden);
    await page.waitForFunction(() => document.querySelector('#engine-pref-global-codex-background_model')?.value === '__inherit__');
    assert.equal(config.provider_defaults.codex.model,'gpt-fast','Restoring task defaults preserves the chat model');
    assert.equal(config.provider_defaults.codex.effort,'high','Restoring task defaults preserves effort');
    assert.equal(config.provider_defaults.claude.model,'claude-balanced','Restoring one provider preserves another');
    const beforeBatch = puts.length;
    await page.locator('#engine-pref-global-codex-effort').selectOption('low');
    await page.locator('#engine-pref-global-claude-model').selectOption('claude-fast');
    await page.locator('#settings-cli-auto-update').click();
    await page.locator('.prov-row[data-provider="claude"] input[name="prov-default"]').check();
    assert.equal(await page.locator('[data-modal-id^="__human-proof-"]').count(),0,'Multiple models and toggles prompt only at Save');
    assert.equal(puts.length,beforeBatch,'Drafting several preferences sends no PUT');
    assert.equal(config.provider_defaults.codex.effort,'high');
    assert.equal(config.default_provider,'codex');
    await page.evaluate(() => window.closeModalById('__settings'));
    await page.evaluate(() => window.openSettings());
    await settingsSection('providers','Connections');
    assert.equal(await page.locator('#engine-pref-global-codex-effort').inputValue(),'low','Draft survives closing and reopening Settings');
    const footer = await page.locator('#vendor-draft-save').boundingBox();
    await page.screenshot({path:resolve(SHOTS,`draft-${width}.png`),animations:'disabled'});
    assert.ok(footer.y >= 0 && footer.y + footer.height <= 1000,'Save stays inside viewport: ' + JSON.stringify({width,footer}));
    await page.locator('#vendor-draft-save').click(); await proof(false);
    await page.waitForFunction(() => document.querySelector('#vendor-draft-bar')?.hidden);
    assert.equal(puts.length,beforeBatch+1,'One Save produces one combined PUT');
    assert.equal(puts.at(-1).default_provider,'claude');
    assert.equal(puts.at(-1).cli_auto_update_enabled,false);
    assert.deepEqual(puts.at(-1).provider_defaults,{codex:{effort:'low'},claude:{model:'claude-fast'}});
    const beforeConflict = puts.length;
    await page.locator('#engine-pref-global-codex-model').selectOption('gpt-balanced');
    config.provider_defaults.codex.model = 'external-pin';
    await page.locator('#vendor-draft-save').click();
    await page.waitForFunction(() => document.querySelector('.vendor-draft-status')?.textContent.includes('changed elsewhere'));
    assert.equal(puts.length,beforeConflict,'Conflicting edits do not overwrite another session');
    assert.equal(await page.locator('[data-modal-id^="__human-proof-"]').count(),0);
    await page.locator('#vendor-draft-cancel').click();
    await page.waitForFunction(() => document.querySelector('#engine-pref-global-codex-model')?.value === 'external-pin');
    await page.evaluate(() => { window._settingsUpUI(); window._settingsUpUI(); });
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
    await page.evaluate(() => window.closeModalById('__settings'));
    await checkProjectPreferenceRaces(page, fixtureView);
    assert.deepEqual(errors.filter(error => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(error)),[]);
    console.log(`PASS ${width}px: delayed project success/error, dismissal and same-project reopen`);
    await ctx.close();
  }
} finally { await browser.close(); }
