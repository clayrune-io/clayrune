// MC-1187 follow-up F2: a Desk request answered 503 desk_store_unreadable shows ONE plain
// notice and the failed call carries the server's plain message. Actual worktree SPA,
// intercepted fixture API; never contacts live data.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, resolve} from 'node:path';
import {chromium} from 'playwright';
import {loadFixtures, workspaceFromFixtures} from './desk-v1-fixture-api.mjs';
import {loadStaticJsCss, deskLoaded} from './_static.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const STATIC = loadStaticJsCss(ROOT);
const HTML = readFileSync(resolve(ROOT, 'static/index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const check = (value, text) => { assert.ok(value, text); console.log(`PASS ${text}`); };
const MESSAGE = "The Desk's saved data could not be read, so nothing can be saved right now.";

const fx = loadFixtures();
const browser = await chromium.launch();
try {
  const context = await browser.newContext({viewport:{width:1280,height:900}});
  const page = await context.newPage(), errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), path = new URL(req.url()).pathname, method = req.method();
    const json = (body, status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
    if (path === '/') return route.fulfill({contentType:'text/html',body:HTML});
    if (STATIC[path]) return route.fulfill({contentType:STATIC[path][0],body:STATIC[path][1]});
    if (path === '/api/projects') return json(fx.projects.map(p => ({...p,status:'active',domain:'general',project_path:'/fixture/'+p.id,roster:[],backlog:[],use_streaming_agent:true})));
    if (path === '/api/config') return json({desk_v1:true,desk_v1_live:true,user_timezone:''});
    if (path === '/api/characters') return json([]);
    if (path === '/api/local-auth/status') return json({configured:true});
    if (path === '/api/desk/workspace') return json({...workspaceFromFixtures(fx),accounts:[],pieces:[],studio:{}});
    if (path === '/api/desk/accounts' && method === 'GET') return json([]);
    if (path.startsWith('/api/desk/engagement/coverage/')) return json({coverage:[]});
    if (path === '/api/desk/probe-unreadable') return json({error:'desk_store_unreadable',message:MESSAGE,path:'desk.json.unreadable-20261116T000000000000Z'},503);
    if (path === '/api/desk/probe-other') return json({error:'some other failure'},500);
    return route.abort();
  });
  await page.goto(ORIGIN, {waitUntil:'domcontentloaded'});
  await deskLoaded(page);
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.desk-v1-shell');

  check(await page.locator('#desk-v1-store-unreadable-notice').count() === 0, 'no notice while the store is readable');
  const other = await page.evaluate(() => window.DeskV1Store.api('GET', '/api/desk/probe-other').then(() => null, e => e.message));
  check(other === 'some other failure' && await page.locator('#desk-v1-store-unreadable-notice').count() === 0, 'another error is untouched: its own message, no notice');
  const msgs = [];
  for (let i = 0; i < 2; i++) msgs.push(await page.evaluate(() => window.DeskV1Store.api('GET', '/api/desk/probe-unreadable').then(() => null, e => e.message)));
  check(msgs.every(m => m === MESSAGE), 'the failed call carries the server\'s plain message, not the error code');
  check(await page.locator('#desk-v1-store-unreadable-notice').count() === 1, 'two failures show one notice');
  const text = await page.locator('#desk-v1-store-unreadable-notice').innerText();
  check(text.includes(MESSAGE) && text.includes('desk.json.unreadable-20261116T000000000000Z'), 'the notice shows the plain words and the file name');
  await page.locator('#desk-v1-store-unreadable-notice button').click();
  check(await page.locator('#desk-v1-store-unreadable-notice').count() === 0, 'Dismiss removes it');
  check(!errors.length, 'no page errors: ' + errors.join('; '));
  await context.close();
} finally { await browser.close(); }
console.log('desk-v1-store-unreadable: ok');
