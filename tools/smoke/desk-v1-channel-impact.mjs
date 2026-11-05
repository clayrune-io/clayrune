// MC-1152 P4: the dark Channel impact module on the Goal screen, against the REAL
// Desk / impact-store / cached-comparison / route code in an isolated temp dir
// (tools/smoke/_desk_channel_impact_server.py). Not a live GitHub check: no GitHub
// connection exists on this machine, so the server injects one connection record.
// Covers: flag off draws nothing; every state in words (no definition, reasons not
// zeros, removed account, not-attributable conversions, one separate repository
// block, stale label, error + retry); 1440 desktop and 390 mobile cards; keyboard.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const SHOTS = resolve(ROOT, '_scratch/mc1152-p4');
mkdirSync(SHOTS, { recursive: true });
let fixture, stderr = '';
let browser, origin;
const NOT_ATTR = 'Not attributable by this source';
const cfg = (q) => fetch(`${origin}/__cfg?${q}`).then((r) => r.json());

async function goal(page) {
  await page.goto(origin, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.desk-v1-shell');
  await page.waitForFunction(() => window.DeskV1Store.gate() === null);
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-p4', panel: 'goal' }));
  await page.waitForSelector('.desk-v1-results');
}
const mount = (page) => page.locator('#desk-v1-ci-mount');
const text = (loc) => loc.innerText();
let failed = false;
try {
  browser = await chromium.launch();

  for (const width of [1440, 390]) {
    // A fresh isolated backend per width: the seeding below must not leak into the next run.
    stderr = '';
    fixture = spawn('python', [resolve(ROOT, 'tools/smoke/_desk_channel_impact_server.py')], { cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
    fixture.stderr.on('data', (d) => { stderr += d; });
    const reg = await fetch('http://localhost:5199/api/processes/register', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pid: fixture.pid, name: 'MC-1152 P4 isolated impact fixture', project_id: 'mission_control', command: 'python tools/smoke/_desk_channel_impact_server.py' }) });
    assert(reg.ok, 'fixture PID registered');
    const port = await new Promise((ok, bad) => {
      let out = '';
      const t = setTimeout(() => bad(new Error('startup timeout: ' + stderr)), 20000);
      fixture.stdout.on('data', (d) => { out += d; const nl = out.indexOf(String.fromCharCode(10)); if (nl >= 0) { clearTimeout(t); try { ok(JSON.parse(out.slice(0, nl)).port); } catch (e) { bad(e); } } });
      fixture.once('exit', (c) => { clearTimeout(t); bad(new Error('fixture exited ' + c + ': ' + stderr)); });
    });
    origin = `http://127.0.0.1:${port}`;
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 } });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (e) => { errors.push(e.message); console.error('PAGE ERROR: ' + e.message); });
    const shot = (n) => page.locator('#desk-v1-ci-mount').screenshot({ path: resolve(SHOTS, `${n}-${width}.png`) });

    // 1. Flag OFF (default): nothing is drawn and the empty mount takes no space.
    await cfg('on=0&fail=0');
    await goal(page);
    await page.waitForTimeout(300);
    assert.equal(await page.locator('[data-ci]').count(), 0, 'flag off: no module');
    assert.equal(await page.evaluate(() => document.getElementById('desk-v1-ci-mount').children.length), 0, 'flag off: mount is empty');
    assert.equal(await mount(page).evaluate((el) => getComputedStyle(el).display), 'none', 'flag off: mount has no layout');
    assert.equal((await fetch(`${origin}/api/desk/impact/compare/camp-p4`)).status, 404, 'flag off: compare route 404');

    // 2. Flag ON, no definition: every plan account shown with reasons, never zeros; removed account kept.
    await cfg('on=1');
    await goal(page);
    await page.waitForSelector('[data-ci] [data-ci-row]');
    const rows = page.locator('[data-ci-row]');
    assert.equal(await rows.count(), 3, 'x + linkedin (plan) + the removed account');
    const li = rows.filter({ hasText: 'Clayrune page' });
    assert.match(await text(li), /No posts published in this period/);
    assert.match(await text(li), /No conversion definition/);
    for (const label of ['Impressions / views', 'Engagement', 'Platform clicks']) {
      assert.doesNotMatch((await li.locator(`td[data-label="${label}"]`).innerText()).trim(), /^0$/, `unread ${label} shows its reason, not 0`);
    }
    assert.equal((await li.locator('td[data-label="Published posts"]').innerText()).trim(), '0', 'a real count of zero posts is shown as 0');
    assert.match(await text(rows.filter({ hasText: 'Removed account' })), /Removed account — history kept/);
    const x = rows.filter({ hasText: '@clay' });
    assert.match(await text(x), /2\s*\n?.*published|2 published posts/i);
    assert.match(await text(x), /150/, 'impressions are the sum of each post\'s latest total (140 + 10)');
    assert.match(await text(x), /likes\s*10/i);
    assert.equal(await page.locator('[data-ci-repo]').count(), 0, 'no definition: no repository block');
    assert.match(await text(mount(page)), /No conversion definition is set for this campaign\./);
    assert.equal(await page.locator('[data-ci-refresh]:not([hidden])').count(), 0, 'nothing to refresh without a definition');
    await shot('no-definition');

    // 3. A definition + a cached (stale) star observation: GitHub is ONE separate block.
    assert.equal((await fetch(`${origin}/__seed`).then((r) => r.json())).seeded, true);
    await goal(page);
    await page.waitForSelector('[data-ci-repo]');
    for (const cell of await page.locator('[data-ci-conversion]').all()) {
      assert.equal((await cell.innerText()).trim(), NOT_ATTR, 'every channel conversion cell is not attributable');
    }
    assert.equal(await page.locator('[data-ci-repo]').count(), 1, 'one repository block, not one per channel');
    const repo = text(page.locator('[data-ci-repo]'));
    assert.match(await repo, /Repository context — not attributed to this campaign/);
    assert.match(await repo, /not split across the channels above/);
    assert.match(await repo, /clayrune\/clayrune|[\w.-]+\/[\w.-]+/, 'target named');
    assert.match(await repo, /Stale/, 'stale is a word, not a colour');
    for (const r of await rows.all()) assert.doesNotMatch(await r.innerText(), /star|repository/i, 'repo outcomes never appear in a channel row');
    assert.match(await text(mount(page)), /Latest totals for posts published in this period/);
    assert.match(await text(mount(page)), /No rates, rankings or winners/);
    await shot('with-definition');

    // 4. Layout + keyboard.
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert(overflow <= 1, `no horizontal page overflow (${overflow}px)`);
    const box = await page.locator('[data-ci]').boundingBox();
    assert(box.x >= 0 && box.x + box.width <= width + 1, 'module sits inside the viewport');
    // The Goal column is 680px wide at any desktop size, so rows are cards at both widths and
    // no column can sit clipped behind a sideways scroll.
    assert.equal(await page.locator('[data-ci-row]').first().evaluate((el) => getComputedStyle(el).display), 'block', 'rows are cards in the Goal column');
    assert.equal(await page.locator('[data-ci-row] td[data-label="Platform clicks"]').first().evaluate((el) => el.dataset.label), 'Platform clicks', 'cards keep their labels');
    const clipped = await page.locator('.desk-v1-ci-scroll').evaluate((el) => el.scrollWidth - el.clientWidth);
    assert(clipped <= 1, `nothing in the module is clipped sideways (${clipped}px)`);
    // Keyboard: Tab reaches the module's focusable parts and focus is visible (no hover needed).
    await page.locator('.desk-v1-goal-editor input, .desk-v1-goal-editor select').last().focus();
    let seenRefresh = false, steps = 0;
    for (; steps < 60 && !seenRefresh; steps++) {
      await page.keyboard.press('Tab');
      seenRefresh = await page.evaluate(() => !!document.activeElement && document.activeElement.matches('[data-ci-refresh]'));
    }
    assert(seenRefresh, 'Tab reaches "Read the source now"');
    assert.equal(await page.locator('[data-ci-refresh]').isVisible(), true, 'refresh offered for a finished period with a connection');
    const focused = await page.evaluate(() => { const s = getComputedStyle(document.activeElement); return s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0; });
    assert(focused, 'focus ring is visible');

    // 5. Error state: words + a working retry.
    await cfg('fail=1');
    await goal(page);
    await page.waitForSelector('[data-ci-retry]');
    assert.match(await text(mount(page)), /Could not load channel impact/);
    await cfg('fail=0');
    await page.click('[data-ci-retry]');
    await page.waitForSelector('[data-ci] [data-ci-row]');
    await shot('after-retry');

    // 6. Nothing the page did read the source.
    assert.equal((await fetch(`${origin}/__reads`).then((r) => r.json())).n, 0, 'opening the view never built a transport');
    assert.equal(errors.length, 0, errors.join('\n'));
    console.log(`PASS ${width}: flag-off absent, states, not-attributable, one repository block, stale/error in words, keyboard, no overflow`);
    await context.close();
    await fetch(`${origin}/__shutdown`).catch(() => {});
  }
} catch (error) {
  failed = true;
  console.error(error);
} finally {
  if (browser) await browser.close();
  if (fixture && fixture.exitCode === null) { try { await fetch(`${origin}/__shutdown`); } catch (e) { /* already down */ } }
  setTimeout(() => process.exit(failed ? 1 : 0), 300);
}
