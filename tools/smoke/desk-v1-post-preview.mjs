#!/usr/bin/env node
// Where: hover/tap preview of a message tile (static/js/desk-v1-post-preview.js).
// Real mouse hover at 1440, real touch taps at 390. Hermetic: real index.html +
// static/js|css, mocked /api, the product's own captures from assets/.
//
// Checks: hover shows a popup with the post's REAL copy and media (a version's own
// body wins over the piece body); the platform skin matches the version's account;
// nothing is invented (no counts, no copy when there is none, a missing Instagram
// picture is said to be missing); click opens the larger modal with a tab per
// version; Esc closes and focus returns; leaving the tile hides the popup; touch
// has no hover, so the eye button opens it and a tap enlarges it; dragging a tile
// does not change assignments (Where smoke).
//
// RUN   cd tools/smoke && node desk-v1-post-preview.mjs     (exit 0 = all hold)
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const index = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const files = loadStaticJsCss(root);
const shots = resolve(root, '_scratch');
mkdirSync(shots, { recursive: true });
const PIECE = 'fam-30-testers';
const PIECE_BODY = 'Piece copy: we need 30 Windows testers for Clayrune.';
const LI_BODY = 'LinkedIn copy: join the Windows beta.';

async function boot(browser, width, touch) {
  const fx = loadFixtures();
  const ctx = await browser.newContext({ viewport: { width, height: 950 }, hasTouch: !!touch, isMobile: !!touch });
  const page = await ctx.newPage();
  page.setDefaultTimeout(10000);
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route('**/*', (route) => {
    const req = route.request(), path = new URL(req.url()).pathname;
    const J = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (path === '/') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: index });
    if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
    if (path.startsWith('/assets/')) return route.fulfill({ contentType: 'image/png', body: readFileSync(resolve(root, path.slice(1))) });
    if (path.startsWith('/static/desk-icons/')) return route.fulfill({ contentType: path.endsWith('.svg') ? 'image/svg+xml' : 'image/png', body: readFileSync(resolve(root, path.slice(1))) });
    if (path.startsWith('/api/desk/engagement/coverage/')) return J({ coverage: [] });
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true });
    if (path === '/api/projects') return J(fx.projects.map((p) => ({ ...p, status: 'active', activity_log: [], backlog: [], roster: [], project_path: '/smoke/' + p.id })));
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (path === '/api/desk/workspace') return J(workspaceFromFixtures(fx));
    if (path === '/api/desk/services') return J([]);
    if (path === '/api/desk/connect/services') return J({ services: [] });
    if (req.method() !== 'GET' && path.startsWith('/api/desk/')) return J({ ok: true });
    return route.abort();
  });
  await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForFunction(() => window.DeskV1Store.state().campaigns.length > 0);
  await page.evaluate(([id, body, li]) => {
    const st = window.DeskV1Store.state();
    const fam = st.families.find((f) => f.id === id);
    fam.body = body;
    fam.versions.find((v) => v.id === 'v-testers-li').body = li;
    // an Instagram account + a version on it, with no picture on that piece
    st.channels.push({ id: 'ig-1', platform: 'instagram', identity: 'clayrune.app', label: 'clayrune.app', connected: true, capability: 'assisted', publish: { ready: false } });
    const camp = st.campaigns.find((c) => c.id === 'camp-1');
    camp.plan.accounts.push('ig-1');
    const bare = st.families.find((f) => f.id === 'fam-agent-live-run');
    bare.versions.push({ id: 'v-agent-ig', channelId: 'ig-1', state: 'drafting', revision: 0 });
  }, [PIECE, PIECE_BODY, LI_BODY]);
  await page.evaluate(() => window.deskV1GotoCampaignPanel('where', { campaignId: 'camp-1' }));
  await page.waitForSelector('[data-where]');
  return { ctx, page, errors };
}

const msg = (id) => `[data-where-message][data-family-id="${id}"]`;
const ver = (id) => `[data-where-version][data-version-id="${id}"]`;
const popText = (page) => page.locator('[data-post-preview-pop]').innerText();
const loaded = (page) => page.waitForFunction(() => [...document.querySelectorAll('.desk-v1-pv-card img')].every((i) => i.complete && i.naturalWidth > 0));
const real = (errors) => errors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));

async function hover(page, sel) {
  const loc = page.locator(sel).first();
  await loc.scrollIntoViewIfNeeded();
  const b = await loc.boundingBox();
  await page.mouse.move(2, 2);
  await page.mouse.move(b.x + 20, b.y + 14, { steps: 4 });
  await page.waitForSelector('[data-post-preview-pop]', { timeout: 3000 });
}

let browser, failed = 0;
try {
  browser = await chromium.launch();

  // ── desktop: real hover ──────────────────────────────────────────────────
  {
    const { ctx, page, errors } = await boot(browser, 1440, false);
    // a version on LinkedIn: that version's own copy, LinkedIn chrome, both real images
    await hover(page, ver('v-testers-li'));
    assert.equal(await page.locator('[data-post-preview-pop] .desk-v1-pv-card').getAttribute('data-pv-platform'), 'linkedin');
    let t = await popText(page);
    assert.match(t, new RegExp(LI_BODY.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), 'the version\'s own copy wins');
    assert.doesNotMatch(t, /we need 30 Windows testers/, 'the piece body is not mixed in when the version has its own');
    assert.match(t, /Clayrune/);
    assert.equal(await page.locator('[data-post-preview-pop] .desk-v1-pv-media img').getAttribute('src'), '/assets/desk-thumb-campaign.png', 'the real asset is shown');
    assert.doesNotMatch(t, /\d+\s*(likes?|comments?|reposts?|views?|followers?)/i, 'no invented counts');
    await page.screenshot({ path: resolve(shots, 'preview-linkedin-pop-1440.png') });
    // leaving hides it
    await page.mouse.move(2, 2);
    await page.waitForFunction(() => !document.querySelector('[data-post-preview-pop]'));
    // the X version of the same piece: no version copy, so the piece's copy; X chrome
    await hover(page, ver('v-followup-x'));
    assert.equal(await page.locator('[data-post-preview-pop] .desk-v1-pv-card').getAttribute('data-pv-platform'), 'x');
    t = await popText(page);
    assert.match(t, /Piece copy: we need 30 Windows testers/);
    assert.match(t, /@/, 'an X account is named by its handle');
    await loaded(page); await page.waitForTimeout(100);
    const pr = await page.locator('[data-post-preview-pop]').boundingBox();
    assert.ok(pr.y >= 0 && pr.y + pr.height <= 950, `the popup stays inside the window once its picture has loaded: ${JSON.stringify(pr)}`);
    await page.screenshot({ path: resolve(shots, 'preview-x-pop-1440.png') });
    // click -> larger modal, one tab per version plus the plain message
    await page.locator('[data-post-preview-pop]').click();
    await page.waitForSelector('[data-post-preview-modal] .desk-v1-pv-card.is-large');
    assert.equal(await page.locator('[data-post-preview-pop]').count(), 0, 'the popup gives way to the modal');
    assert.equal(await page.locator('[data-post-preview-modal] [data-pv-tab]').count(), 3, 'Message + the two versions of this piece');
    assert.equal(await page.locator('[data-post-preview-modal] .desk-v1-pv-card').getAttribute('data-pv-platform'), 'x', 'opens on the version that was hovered');
    assert.equal(await page.locator('[data-post-preview-modal] .desk-v1-pv-media img').count(), 2, 'the large view shows both real images');
    await page.locator('[data-pv-tab="v-testers-li"]').click();
    assert.equal(await page.locator('[data-post-preview-modal] .desk-v1-pv-card').getAttribute('data-pv-platform'), 'linkedin');
    await loaded(page); await page.waitForTimeout(150);
    await page.screenshot({ path: resolve(shots, 'preview-linkedin-modal-1440.png') });
    await page.locator('[data-pv-tab="message"]').click();
    assert.equal(await page.locator('[data-post-preview-modal] .desk-v1-pv-card').getAttribute('data-pv-platform'), 'plain');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('[data-post-preview-modal]'));
    await page.waitForTimeout(500);
    assert.equal(await page.locator('[data-post-preview-pop]').count(), 0, 'closing the modal does not pop the preview back up');
    // a message tile (no platform yet): plain frame, with the piece's own copy
    await hover(page, msg(PIECE));
    assert.equal(await page.locator('[data-post-preview-pop] .desk-v1-pv-card').getAttribute('data-pv-platform'), 'plain');
    assert.match(await popText(page), /Piece copy: we need 30 Windows testers/);
    await page.mouse.move(2, 2);
    await page.waitForFunction(() => !document.querySelector('[data-post-preview-pop]'));
    // no copy, no picture: say so; never fill in
    await hover(page, ver('v-agent-ig'));
    assert.equal(await page.locator('[data-post-preview-pop] .desk-v1-pv-card').getAttribute('data-pv-platform'), 'instagram');
    t = await popText(page);
    assert.match(t, /No copy written yet/);
    assert.match(t, /none is attached yet/, 'a missing picture is said to be missing');
    await page.screenshot({ path: resolve(shots, 'preview-instagram-empty-pop-1440.png') });
    await page.mouse.move(2, 2);
    // C5: preview remains, individual placement drag is retired (covered by Where smoke).
    assert.equal(await page.locator('[data-where-remove-version]').count(), 0);
    assert.deepEqual(real(errors), []);
    await ctx.close();
    console.log('PASS 1440px: hover popup, platform skins, real copy/media, modal tabs, Esc, read-only overview');
  }

  // ── phone: no hover; the eye button, then a tap to enlarge ───────────────
  {
    const { ctx, page, errors } = await boot(browser, 390, true);
    const eye = page.locator(`${ver('v-testers-li')} [data-post-preview]`);
    await eye.scrollIntoViewIfNeeded();
    assert.ok(Number(await eye.evaluate((n) => getComputedStyle(n).opacity)) > 0.9, 'the eye is visible without hover');
    const eb = await eye.boundingBox();
    assert.ok(eb.width >= 44 && eb.height >= 44, `touch target ${eb.width}x${eb.height} >= 44`);
    await eye.tap();
    await page.waitForSelector('[data-post-preview-pop]');
    const pb = await page.locator('[data-post-preview-pop]').boundingBox();
    assert.ok(pb.x >= 0 && pb.x + pb.width <= 390 && pb.y >= 0 && pb.y + pb.height <= 950, `popup on screen ${JSON.stringify(pb)}`);
    assert.equal(await page.locator('[data-post-preview-pop] .desk-v1-pv-card').getAttribute('data-pv-platform'), 'linkedin');
    await page.screenshot({ path: resolve(shots, 'preview-linkedin-pop-390.png') });
    await page.locator('[data-post-preview-pop]').tap();
    await page.waitForSelector('[data-post-preview-modal] .desk-v1-pv-card.is-large');
    const mb = await page.locator('[data-post-preview-modal] .desk-v1-pv-box').boundingBox();
    assert.ok(mb.x >= 0 && mb.x + mb.width <= 390, 'modal fits the phone width');
    await loaded(page); await page.waitForTimeout(150);
    await page.screenshot({ path: resolve(shots, 'preview-linkedin-modal-390.png') });
    await page.locator('[data-pv-close]').tap();
    await page.waitForFunction(() => !document.querySelector('[data-post-preview-modal]'));
    // tapping the eye on a message tile
    await page.locator(`${msg(PIECE)} [data-post-preview]`).tap();
    await page.waitForSelector('[data-post-preview-pop]');
    assert.equal(await page.locator('[data-post-preview-pop] .desk-v1-pv-card').getAttribute('data-pv-platform'), 'plain');
    // tapping elsewhere dismisses a pinned popup
    await page.locator('.desk-v1-where-rule').tap();
    await page.waitForFunction(() => !document.querySelector('[data-post-preview-pop]'));
    // the eye did not open the add-version menu
    assert.equal(await page.locator('.desk-v1-addto-menu, [data-addto-menu]').count(), 0);
    assert.deepEqual(real(errors), []);
    await ctx.close();
    console.log('PASS 390px: eye visible + 44px, tap popup, tap to enlarge, dismiss');
  }
} catch (e) { failed = 1; console.error(e.stack || e); }
finally { if (browser) await browser.close(); }
process.exitCode = failed;
