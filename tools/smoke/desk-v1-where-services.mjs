#!/usr/bin/env node
// Real SPA and Connect screens; hermetic workspace and registry projections.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const index = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const files = loadStaticJsCss(root);
// Fixture screenshots: browser checks are synthetic; no real profile is opened.
const shots = resolve(root, 'docs/desk_v1/screens');
const before = process.env.WHERE_BEFORE === '1';
if (before) for (const name of ['desk-v1-where.js','desk-v1-where-tiles.js','desk-v1-where-services.js']) {
  const path = 'static/js/' + name;
  files['/' + path][1] = execFileSync('git',['show','aa5ce01abdcaff62a3e4e04956713aa8c2f13b63:' + path],{cwd:root,encoding:'utf8'});
}
mkdirSync(shots, { recursive: true });
// Exercise the actual server-side destination filter and connection choices,
// without a live server or a second copy of the destination rule.
const catalog = JSON.parse(execFileSync('python', ['-c',
  'import json; from flask import Flask; from mc.blueprints.desk_connect_routes import bp; from mc.desk_connect import type_view; app = Flask(__name__); app.register_blueprint(bp); rows = app.test_client().get("/api/desk/connect/services").get_json()["services"]; print(json.dumps([{**s, "types": type_view.project_service(s["id"])} for s in rows]))',
], { cwd: root, encoding: 'utf8' }));

async function boot(browser, width, failCatalog = false) {
  const fx = loadFixtures();
  fx.channels = fx.channels.filter(c => ['ch-x-ron', 'ch-li-page', 'ch-blog'].includes(c.id));
  Object.assign(fx.channels.find(c => c.id === 'ch-li-page'), {connected:false, publish:{ready:false, reason:'LinkedIn app review pending (w_organization_social)'}, browser_profile:'linked-account'});
  Object.assign(fx.channels.find(c => c.id === 'ch-x-ron'), {connected:true, publish:{ready:true}, browser_setup:{refs:{browser_profile:'main'},status:{state:'sign_in_required'}}});
  fx.channels.push({id:'preview-tiktok', platform:'tiktok', identity:'Preview', preview:true, connected:false});
  let saved = [{id:'svc-instagram', name:'www.instagram.com', link:'https://www.instagram.com', kind:'browser_profile', browser_connection:{service:null, browser_profile:'main', status:{state:'verified'}}},
    {id:'svc-video',name:'Higgsfield',link:'https://higgsfield.ai',browser_connection:{service:'higgsfield',browser_profile:'video',status:{state:'verified'}}},
    {id:'svc-note',name:'TikTok note',link:'https://www.tiktok.com',kind:'saved_for_agents'}];
  fx.families.push({ id: 'p-empty', campaignId: 'camp-1', kind: 'post', title: 'Empty post', body: '', assets: [], versions: [] });
  const ctx = await browser.newContext({ viewport: { width, height: 950 } });
  const page = await ctx.newPage();
  page.setDefaultTimeout(10000);
  const errors = [], calls = [];
  let failTypes = false;
  let failSaved = false;
  let coverage = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    const J = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (path === '/') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: index });
    if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
    if (path.startsWith('/api/desk/engagement/coverage/')) return J({coverage});
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true });
    if (path === '/api/projects') return J(fx.projects.map(p => ({ ...p, status: 'active', activity_log: [], backlog: [], roster: [], project_path: '/smoke/' + p.id })));
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (path === '/api/desk/workspace') return J(workspaceFromFixtures(fx));
    if (path === '/api/desk/services') { if (failSaved) { failSaved=false; return J({error:'Saved list offline'},503); } return J(saved); }
    if (path === '/api/desk/connect/services') {
      if (failCatalog) { failCatalog = false; return J({ error: 'Catalogue offline' }, 503); }
      return J({ services: catalog.map(({ types, ...s }) => s) });
    }
    if (path === '/api/desk/connect/types') {
      calls.push({ method: req.method(), body: req.postDataJSON() });
      if (failTypes) { failTypes = false; return J({ error: 'Options temporarily unavailable' }, 503); }
      const svc = catalog.find(s => s.url === req.postDataJSON().input);
      return J({ ...svc.types, host: svc.host, url: svc.url, input: svc.url });
    }
    return route.abort();
  });
  await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForFunction(() => window.DeskV1Store.state().families.some(f => f.id === 'p-empty'));
  const where = async () => {
    await page.evaluate(() => window.deskV1GotoCampaignPanel('where', { campaignId: 'camp-1' }));
    await page.waitForSelector('[data-where]');
  };
  const piece = async () => {
    await page.evaluate(() => window.deskV1Nav('piece', { familyId: 'p-empty', campaignId: 'camp-1' }));
    await page.waitForSelector('[data-piece-put-on-channel], [data-piece-choose-campaign]');
  };
  return { ctx, page, errors, calls, where, piece, failNextTypes: () => { failTypes = true; }, setSaved: rows => { saved = rows; }, failNextSaved: () => { failSaved=true; }, setCoverage: rows => { coverage=rows; } };
}

let browser;
try {
  browser = await chromium.launch();
  for (const width of [1440, 390]) {
    const { ctx, page, errors, calls, where, piece, setSaved, failNextSaved, setCoverage } = await boot(browser, width, !before);
    await where();
    if (!before) await page.locator('[data-where-services-retry]').click();
    await page.waitForSelector(before ? '[data-where-service="instagram"]' : '[data-where-service="svc-instagram"]');
    await page.locator('[data-where-sources]').scrollIntoViewIfNeeded();
    await page.screenshot({path:resolve(shots, `where_configured_${before ? 'before' : 'after'}_${width}.png`)});
    if (before) { await ctx.close(); console.log(`BEFORE ${width}px captured`); continue; }
    assert.equal(await page.locator('[data-where-service]').count(),1,'only saved campaign destinations appear');
    assert.equal(await page.locator('[data-platform="tiktok"]').count(),0,'unconnected and preview TikTok hidden');
    assert.match(await page.locator('[data-where-service="svc-instagram"]').innerText(),/Ready/,'legacy Instagram uses saved check');
    assert.match(await page.locator('[data-channel-id="ch-li-page"][data-where-source]').innerText(), /Waiting on LinkedIn/);
    assert.equal(await page.locator('[data-where-service]').evaluateAll(nodes=>nodes.some(n=>n.draggable || n.hasAttribute('data-channel-id'))),false,'saved sign-in grants no account placement');
    assert.match(await page.locator('[data-channel-id="ch-x-ron"][data-where-source]').innerText(), /Needs sign-in/,'publish-ready is separate from saved browser sign-in');
    assert.equal(calls.length,0,'Where never starts a new connection');
    await page.locator('[data-where-service-open="svc-instagram"]').click();
    await page.waitForSelector('[data-browser-service="svc-instagram"]');
    // Where and Connections consume the same saved read evidence.
    setCoverage([{platform:'linkedin',account_id:'ch-li-page',via:'pane',state:'ok'}]);
    await page.evaluate(()=>window.DeskV1ConnectionStatus.invalidate());
    await where();
    await page.waitForFunction(()=>document.querySelector('[data-where-source][data-channel-id="ch-li-page"]')?.innerText.includes('Ready'));
    setCoverage([]);
    await page.evaluate(()=>window.DeskV1ConnectionStatus.invalidate());
    failNextSaved();
    await where();
    await page.waitForSelector('[data-where-services-retry]');
    assert.match(await page.locator('[data-where-sources]').innerText(),/Saved list offline/);
    await page.locator('[data-where-services-retry]').click();
    await page.waitForSelector('[data-where-service="svc-instagram"]');
    for (const [state,word] of [['sign_in_required','Needs sign-in'],['check_failed','Not working'],['not_checked','Not checked yet'],['never_checked','Not checked yet']]) {
      setSaved([{id:'svc-instagram',name:'www.instagram.com',link:'https://www.instagram.com',browser_connection:{service:null,browser_profile:'main',status:{state}}}]);
      await where();
      await page.waitForFunction(word=>document.querySelector('[data-where-service="svc-instagram"]')?.innerText.includes(word),word);
    }
    await page.evaluate(()=>window.DeskV1Store.state().channels.push({id:'ig-account',platform:'instagram',identity:'My Instagram',connected:true,capability:'assisted'}));
    setSaved([{id:'svc-instagram',name:'www.instagram.com',link:'https://www.instagram.com',account_id:'ig-account',browser_connection:{browser_profile:'main',status:{state:'verified'}}}]);
    await where();
    await page.waitForFunction(()=>!document.querySelector('[data-where-service="svc-instagram"]'));
    assert.equal(await page.locator('[data-channel-id="ig-account"][data-where-source]').count(),1,'attached account replaces saved sign-in');
    setSaved([]);
    await page.evaluate(()=>{window.DeskV1Store.state().channels=window.DeskV1Store.state().channels.filter(c=>c.id!=='ig-account');});
    await where();
    await page.waitForFunction(()=>!document.querySelector('[data-where-service]'));
    for (const kind of ['post', 'article', 'video']) {
      await page.evaluate(kind => { window.DeskV1Store.state().families.find(f => f.id === 'p-empty').kind = kind; }, kind);
      await piece();
      if (kind === 'post') await page.screenshot({ path: resolve(root, `_scratch/copy-${width}.png`) });
      await page.locator('[data-piece-put-on-channel]').click();
      await page.waitForSelector('[data-where][data-campaign-id="camp-1"]');
    }
    await page.evaluate(() => { const p = window.DeskV1Store.state().families.find(f => f.id === 'p-empty'); p.campaignId = null; p.kind = 'post'; });
    await piece();
    assert.match(await page.locator('[data-piece-section="versions"]').innerText(), /not in a campaign yet/);
    await page.screenshot({ path: resolve(root, `_scratch/copy-no-campaign-${width}.png`) });
    await page.locator('[data-piece-choose-campaign]').click();
    await page.waitForSelector('.desk-v1-home');
    assert.deepEqual(errors.filter(e => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e)), []);
    await ctx.close();
    console.log(`PASS ${width}px: configured sources, legacy Instagram states/recovery, broken account, retry, no grants, attached dedup/removal, Copy routes`);
  }
} catch (e) { console.error(e.stack || e); process.exitCode = 1; }
finally { if (browser) await browser.close(); }
