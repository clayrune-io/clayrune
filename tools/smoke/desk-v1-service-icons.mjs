#!/usr/bin/env node
/**
 * Desk v1 — service marks on Connections (Ron 2026-10-15: "the plain letter icons on
 * each connection card do not identify the service"; confirmed: the real logo AND the
 * account name right next to it). Asserts:
 *   - the mapping: every service Ron named resolves to its own local mark by id, by
 *     host (www.instagram.com, drive.google.com) and, for an MCP server or saved
 *     service, by a whole word in its name; an account is never matched by name;
 *   - every file the module can point at exists, is served from /static/desk-icons/
 *     (no remote URL anywhere), is documented in the README, and actually loads;
 *   - an unknown service gets a TWO-letter monogram on a colour that is the same for
 *     the same name every time, and its white letters read at WCAG AA on every hue;
 *   - the gallery grid renders every mark at 1440 and 390 with no broken image, and the
 *     connection's name sits beside the mark (UX-02), not only in a subtitle;
 *   - the wizard's suggestion list carries the mark too.
 * Screenshots: docs/desk_v1/screens/service_icons_{1440,390}.png, service_icons_wizard_1440.png
 *
 * Hermetic: real index.html + static/, every /api route mocked.
 * RUN  cd tools/smoke && node desk-v1-service-icons.mjs
 */
import { readFileSync, mkdirSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { seedDeskV1Fixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ICON_DIR = resolve(REPO_ROOT, 'static', 'desk-icons');
const README = readFileSync(resolve(ICON_DIR, 'README.md'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const SHOT_DIR = resolve(REPO_ROOT, 'docs', 'desk_v1', 'screens');
mkdirSync(SHOT_DIR, { recursive: true });
const STATIC = loadStaticJsCss(REPO_ROOT);

const PROJECTS = [{
  id: 'smoke_icons', name: 'Icons smoke', status: 'active', domain: 'general', emoji: '🧪',
  description: '', summary: '', current_task: 'Idle', next_action: '', blocked: false, blocked_reason: null,
  activity_log: [], backlog: [], project_path: '/smoke/icons', last_updated: '2026-10-15T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null, display_order: 0, provider: 'claude',
  use_streaming_agent: true, distiller_mode: 'proposed', distiller_min_recurrence: 3,
  distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
  distiller_max_explorations_per_session: 3, distiller_min_turns: 5, distiller_skip_errors: true, roster: [],
}];

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));
const remote = [];

async function fulfill(route) {
  const req = route.request();
  const url = new URL(req.url());
  const path = url.pathname;
  if (url.origin !== ORIGIN) { remote.push(req.url()); return route.abort(); }
  const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  const hit = STATIC[path];
  if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
  if (path === '/api/projects') return J(PROJECTS);
  if (path === '/api/config') return J({ desk_v1: true, user_timezone: '' });
  if (path === '/api/characters') return J([]);
  if (path === '/api/desk/connect/suggest') {
    return J({ suggestions: [
      { id: 'instagram', label: 'Instagram', host: 'instagram.com' },
      { id: 'youtube', label: 'YouTube', host: 'youtube.com' },
      { id: 'higgsfield', label: 'Higgsfield', host: 'higgsfield.ai' },
      { id: 'plausible', label: 'Plausible Analytics', host: 'plausible.io' },
    ] });
  }
  return route.abort();
}

async function open(browser, viewport) {
  const ctx = await browser.newContext({ viewport, ...(viewport.width < 600 ? { hasTouch: true, isMobile: true } : {}) });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message || String(e)));
  await page.route('**/*', fulfill);
  await seedDeskV1Fixtures(page);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-connections] [data-conn-tile]', { timeout: 4000 });
  return { ctx, page, errors };
}

// Every service Ron named, as the Connections screen hands it to the grid.
const GALLERY = [
  { key: 'g1', kind: 'account', platform: 'youtube', brand: 'youtube', name: 'Clayrune channel', kindLabel: 'Social account' },
  { key: 'g2', kind: 'account', platform: 'instagram', brand: 'instagram', name: '@clayrune', kindLabel: 'Social account' },
  { key: 'g3', kind: 'account', platform: 'facebook', brand: 'facebook', name: 'Clayrune page', kindLabel: 'Social account' },
  { key: 'g4', kind: 'account', platform: 'tiktok', brand: 'tiktok', name: '@clayrune', kindLabel: 'Social account' },
  { key: 'g5', kind: 'account', platform: 'x', brand: 'x', name: '@RanLevi15', kindLabel: 'Social account' },
  { key: 'g6', kind: 'account', platform: 'reddit', brand: 'reddit', name: 'u/clayrune', kindLabel: 'Social account' },
  { key: 'g7', kind: 'account', platform: 'linkedin', brand: 'linkedin', name: 'Clayrune', kindLabel: 'Social account' },
  { key: 'g8', kind: 'account', platform: 'snapchat', brand: 'snapchat', name: 'clayrune', kindLabel: 'Social account' },
  { key: 'g9', kind: 'service', name: 'GitHub', link: 'https://github.com', kindLabel: 'Saved service' },
  { key: 'g10', kind: 'service', name: 'Notion workspace', link: 'https://www.notion.so', kindLabel: 'Saved service' },
  { key: 'g11', kind: 'source', brand: 'gdrive', name: 'Google Drive', kindLabel: 'Content source' },
  { key: 'g12', kind: 'source', brand: 'gphotos', name: 'Google Photos', kindLabel: 'Content source' },
  { key: 'g13', kind: 'engine', brand: 'openai', name: 'OpenAI image', kindLabel: 'Generation engine' },
  { key: 'g14', kind: 'engine', brand: 'higgsfield_mcp', name: 'Higgsfield (sign in)', kindLabel: 'Generation engine' },
  { key: 'g15', kind: 'engine', brand: 'google', name: 'Google (Veo + Gemini image)', kindLabel: 'Generation engine' },
  { key: 'g16', kind: 'service', name: 'www.instagram.com', link: 'https://www.instagram.com', kindLabel: 'Browser sign-in' },
  { key: 'g17', kind: 'mcp', name: 'ZubeidHendricks-youtube-mcp-server-1', kindLabel: 'MCP server' },
  { key: 'g18', kind: 'account', platform: 'blog', brand: 'blog', name: 'https://clayrune.io/blog', kindLabel: 'Social account' },
  { key: 'g19', kind: 'service', name: 'Plausible Analytics', link: 'https://plausible.io', kindLabel: 'Saved service' },
  { key: 'g20', kind: 'mcp', name: 'acme-tools', kindLabel: 'MCP server' },
].map((it) => ({ ...it, status: { key: 'ok', word: 'Connected' } }));

const shape = (p) => p.$$eval('[data-conn-tile]', (els) => els.map((e) => {
  const m = e.querySelector('[data-svc-icon]'); const img = m.querySelector('img'); const r = m.getBoundingClientRect();
  const name = e.querySelector('.desk-v1-conn-tile-name');
  return { key: e.dataset.connTile, icon: m.dataset.svcIcon, text: img ? '' : m.textContent.trim(), loaded: img ? img.naturalWidth > 0 : null,
           w: Math.round(r.width), h: Math.round(r.height), name: name.textContent.trim(), hidden: m.getAttribute('aria-hidden'),
           alt: img ? img.getAttribute('alt') : null, beside: name.getBoundingClientRect().left >= r.right - 1 };
}));

const paint = (page, items) => page.evaluate((its) => {
  document.querySelector('[data-conn-tiles]').outerHTML = window.DeskV1ConnTiles.gridHTML({ items: its });
}, items);

async function run(browser) {
  const { ctx, page, errors } = await open(browser, { width: 1440, height: 900 });

  // -- the mapping --------------------------------------------------------------
  const map = await page.evaluate(() => {
    const I = window.DeskV1ServiceIcons;
    const v = (o) => I.vendor(o);
    return {
      ids: ['youtube', 'instagram', 'facebook', 'tiktok', 'x', 'reddit', 'linkedin', 'snapchat', 'github', 'notion', 'gdrive', 'gphotos', 'openai', 'higgsfield', 'higgsfield_mcp', 'google', 'discord']
        .map((id) => [id, v({ brand: id })]),
      hosts: [['https://www.instagram.com', 'instagram'], ['https://m.youtube.com/@x', 'youtube'], ['drive.google.com', 'googledrive'], ['https://photos.google.com/', 'googlephotos'],
        ['https://accounts.google.com', 'google'], ['https://twitter.com/ron', 'x'], ['notion.so', 'notion'], ['https://console.higgsfield.ai', 'higgsfield'],
        ['https://plausible.io', null], ['https://notinstagram.com', null], ['instagram.com.evil.example', null]].map(([link, want]) => [link, want, v({ link })]),
      words: [['ZubeidHendricks-youtube-mcp-server-1', 'mcp', 'youtube'], ['GitHub', 'service', 'github'], ['x-mcp', 'mcp', null], ['acme-tools', 'mcp', null]]
        .map(([name, kind, want]) => [name, want, v({ name, kind })]),
      accountByNameIgnored: v({ kind: 'account', platform: 'blog', name: 'My YouTube diary' }),
      all: I.vendors().map((vv) => [vv, I.file(vv)]),
    };
  });
  const idsBad = map.ids.filter(([, got]) => got == null);
  check(idsBad.length === 0, `all ${map.ids.length} service ids resolve to a mark`, `ids with no mark: ${JSON.stringify(idsBad)}`);
  const byId = Object.fromEntries(map.ids);
  check(byId.gdrive === 'googledrive' && byId.gphotos === 'googlephotos' && byId.higgsfield_mcp === 'higgsfield' && byId.x === 'x',
        'Drive, Photos, Higgsfield (sign in) and X map to the right marks', `ids wrong: ${JSON.stringify(byId)}`);
  const hostsBad = map.hosts.filter(([, want, got]) => want !== got);
  check(hostsBad.length === 0, 'hosts map (subdomains match, look-alike hosts do not)', `host mapping wrong: ${JSON.stringify(hostsBad)}`);
  const wordsBad = map.words.filter(([, want, got]) => want !== got);
  check(wordsBad.length === 0 && map.accountByNameIgnored == null, 'an MCP / saved service is matched by a whole word in its name; an account never is, and "x" alone never matches',
        `name matching wrong: ${JSON.stringify([wordsBad, map.accountByNameIgnored])}`);

  // -- the files ---------------------------------------------------------------
  const onDisk = new Set(readdirSync(ICON_DIR));
  const missing = map.all.filter(([, url]) => !onDisk.has(String(url).split('/').pop()));
  check(map.all.length === 16 && missing.length === 0, `${map.all.length} marks, each a file in static/desk-icons/`, `mark files missing: ${JSON.stringify(missing)}`);
  check(map.all.every(([, url]) => url.startsWith('/static/desk-icons/')), 'every mark is served from the repo (no remote URL)', `non-local mark URL: ${JSON.stringify(map.all)}`);
  check(/Licen[cs]e/.test(README), 'the README beside the files records source and licence', 'static/desk-icons/README.md has no licence column');
  const undocumented = [...onDisk].filter((f) => f !== 'README.md' && !README.includes('`' + f + '`'));
  check(undocumented.length === 0, 'every file has a README row', `files with no README row: ${JSON.stringify(undocumented)}`);

  // -- monograms -----------------------------------------------------------------
  const mono = await page.evaluate(() => {
    const I = window.DeskV1ServiceIcons;
    const lin = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
    const hslRgb = (h, s, l) => { const a = s * Math.min(l, 1 - l); const f = (n) => { const k = (n + h / 30) % 12; return l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)); }; return [f(0) * 255, f(8) * 255, f(4) * 255]; };
    let worst = 99;
    for (let h = 0; h < 360; h++) { const [r, g, b] = hslRgb(h, 0.45, 0.30); const L = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b); worst = Math.min(worst, 1.05 / (L + 0.05)); }
    return {
      letters: ['Acme Tools', 'https://clayrune.io/blog', 'www.plausible.io', 'Plausible', 'x', 'a-b-c', ''].map((n) => [n, I.letters(n)]),
      same: I.monogramColour('Acme Tools') === I.monogramColour('acme tools'),
      differ: new Set(['Acme Tools', 'Plausible Analytics', 'Zed', 'Orbit', 'Kiln', 'Lumen', 'Quill'].map((n) => I.monogramColour(n))).size,
      worst,
    };
  });
  const L = Object.fromEntries(mono.letters);
  check(L['Acme Tools'] === 'AT' && L['https://clayrune.io/blog'] === 'CL' && L['www.plausible.io'] === 'PL' && L.Plausible === 'PL' && L.x === 'X' && L['a-b-c'] === 'AB' && L[''] === '?',
        'monogram letters: two for two words, first two of one word or a web address', `letters wrong: ${JSON.stringify(mono.letters)}`);
  check(mono.same && mono.differ >= 6, `a monogram colour is stable per name and varies between names (${mono.differ} of 7 distinct)`, `colour stability wrong: ${JSON.stringify(mono)}`);
  check(mono.worst >= 4.5, `white monogram letters read at WCAG AA on every hue (worst ${mono.worst.toFixed(2)}:1)`, `a monogram hue is under 4.5:1 (${mono.worst.toFixed(2)})`);

  // -- the gallery at 1440 -------------------------------------------------------
  await paint(page, GALLERY);
  await page.waitForFunction(() => [...document.querySelectorAll('[data-conn-tiles] img')].every((i) => i.complete), null, { timeout: 5000 });
  const g = await shape(page);
  const gk = Object.fromEntries(g.map((x) => [x.key, x]));
  const want = { g1: 'youtube', g2: 'instagram', g3: 'facebook', g4: 'tiktok', g5: 'x', g6: 'reddit', g7: 'linkedin', g8: 'snapchat', g9: 'github', g10: 'notion',
    g11: 'googledrive', g12: 'googlephotos', g13: 'openai', g14: 'higgsfield', g15: 'google', g16: 'instagram', g17: 'youtube', g18: 'monogram', g19: 'monogram', g20: 'monogram' };
  const wrong = Object.entries(want).filter(([k, v]) => gk[k].icon !== v);
  check(wrong.length === 0, `the gallery shows ${g.length} marks, each the right one (YouTube, Instagram, Facebook, TikTok, X, Reddit, LinkedIn, Snapchat, GitHub, Notion, Drive, Photos, OpenAI, Higgsfield, Google)`,
        `wrong marks: ${JSON.stringify(wrong.map(([k, v]) => [k, v, gk[k].icon]))}`);
  check(g.filter((x) => x.loaded !== null).every((x) => x.loaded), 'every logo file in the gallery loads (no broken image)', `broken: ${JSON.stringify(g.filter((x) => x.loaded === false))}`);
  check(g.every((x) => x.w === 28 && x.h === 28), 'every mark is the same 28px box', `box sizes differ: ${JSON.stringify(g.map((x) => [x.key, x.w, x.h]))}`);
  check(g.every((x) => x.name && x.beside && x.hidden === 'true' && (x.alt === null || x.alt === '')),
        'the connection name sits beside every mark, and the mark is decoration (aria-hidden, empty alt)', `name/mark relation wrong: ${JSON.stringify(g.filter((x) => !(x.name && x.beside)))}`);
  const mg = g.filter((x) => x.icon === 'monogram');
  check(mg.length === 3 && mg.every((x) => /^[A-Z0-9]{2}$/.test(x.text)), `unknown services get two letters (${mg.map((x) => x.text).join(', ')})`, `monograms wrong: ${JSON.stringify(mg)}`);
  await page.screenshot({ path: resolve(SHOT_DIR, 'service_icons_1440.png') });

  const stray = remote.filter((u) => !/^https:\/\/fonts\.(googleapis|gstatic)\.com\//.test(u));   // index.html's own web-font link, not a mark
  check(stray.length === 0, 'no mark is fetched from outside the app (no CDN, no remote fetch)', `remote requests: ${JSON.stringify(stray)}`);
  check(errors.length === 0, 'no page errors at 1440', `page errors: ${JSON.stringify(errors)}`);
  await ctx.close();

  // -- the wizard's suggestion list ---------------------------------------------
  const w = await open(browser, { width: 1440, height: 900 });
  // The live wizard needs the whole connect fixture (desk-v1-connect-simplify.mjs); the
  // suggestion list is its own module, so mount it on the real screen and drive it.
  const suggestions = [
    { id: 'instagram', label: 'Instagram', host: 'instagram.com' }, { id: 'youtube', label: 'YouTube', host: 'youtube.com' },
    { id: 'higgsfield', label: 'Higgsfield', host: 'higgsfield.ai' }, { id: 'plausible', label: 'Plausible Analytics', host: 'plausible.io' }];
  await w.page.evaluate((list) => {
    const host = document.querySelector('[data-connections]');
    host.insertAdjacentHTML('beforeend', `<div class="desk-v1-conn-detail" data-sg-root><label class="desk-v1-conn-add-field">Service name or web address<input type="text" data-sg-input></label>${window.DeskV1ConnectSuggest.listHTML()}</div>`);
    const root = host.querySelector('[data-sg-root]');
    window.DeskV1ConnectSuggest.bind(root, { input: root.querySelector('[data-sg-input]'), onPick: () => {}, api: async () => ({ suggestions: list }) });
  }, suggestions);
  await w.page.fill('[data-sg-input]', 'in');
  await w.page.waitForSelector('[data-cf-suggest-pick]', { timeout: 4000 });
  const sug = await w.page.$$eval('[data-cf-suggest-pick]', (els) => els.map((e) => [e.querySelector('[data-svc-icon]') && e.querySelector('[data-svc-icon]').dataset.svcIcon, e.textContent.trim()]));
  check(sug.length === 4 && sug[0][0] === 'instagram' && sug[1][0] === 'youtube' && sug[2][0] === 'higgsfield' && sug[3][0] === 'monogram',
        `the wizard's suggestion list shows each service's mark (${sug.map((x) => x[0]).join(', ')})`, `suggestions wrong: ${JSON.stringify(sug)}`);
  await w.page.waitForFunction(() => [...document.querySelectorAll('[data-cf-suggest] img')].every((i) => i.complete && i.naturalWidth > 0), null, { timeout: 5000 });
  await w.page.locator('[data-sg-root]').scrollIntoViewIfNeeded();
  await w.page.screenshot({ path: resolve(SHOT_DIR, 'service_icons_wizard_1440.png') });
  check(w.errors.length === 0, 'no page errors in the wizard list', `page errors: ${JSON.stringify(w.errors)}`);
  await w.ctx.close();

  // -- 390 -----------------------------------------------------------------------
  const m = await open(browser, { width: 390, height: 844 });
  await paint(m.page, GALLERY);
  await m.page.waitForFunction(() => [...document.querySelectorAll('[data-conn-tiles] img')].every((i) => i.complete), null, { timeout: 5000 });
  const g2 = await shape(m.page);
  check(g2.length === GALLERY.length && g2.every((x) => x.w === 28 && x.h === 28 && x.beside && (x.loaded === null || x.loaded)),
        '390px: every mark is 28px, loaded, with its name beside it', `390 marks wrong: ${JSON.stringify(g2.filter((x) => !(x.w === 28 && x.beside)))}`);
  const fit = await m.page.evaluate(() => ({ scrollW: document.querySelector('[data-connections]').scrollWidth, clientW: document.querySelector('[data-connections]').clientWidth }));
  check(fit.scrollW <= fit.clientW, `390px: no horizontal overflow (${fit.scrollW} <= ${fit.clientW})`, `390px overflows: ${JSON.stringify(fit)}`);
  await m.page.screenshot({ path: resolve(SHOT_DIR, 'service_icons_390.png') });
  check(m.errors.length === 0, 'no page errors at 390', `page errors: ${JSON.stringify(m.errors)}`);
  await m.ctx.close();
}

const browser = await chromium.launch();
try { await run(browser); } catch (e) { fail('smoke crashed: ' + (e && e.stack || e)); } finally { await browser.close(); }
console.log(bad ? `\n${bad} check(s) failed` : '\nAll checks passed.');
process.exit(bad ? 1 : 0);
