// Exact piece-page computer chooser against isolated real Flask + filesystem.
// No operator data or production endpoints; the small MP4 is real H.264.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
const root = fileURLToPath(new URL('../..', import.meta.url));
async function register(pid, command) {
  const r = await fetch('http://localhost:5199/api/processes/register', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pid, name: 'Real MP4 upload verification', command, project_id: 'mission_control' }) });
  assert(r.ok, 'process registration failed');
}
await register(process.pid, 'node tools/smoke/desk-video-upload-real.mjs');
const fixture = spawn('python', ['tools/smoke/_desk_what_add_server.py'], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
await register(fixture.pid, 'python tools/smoke/_desk_what_add_server.py');
let origin, browser, stderr = '';
fixture.stderr.on('data', data => { stderr += data; });
try {
  origin = await new Promise((done, reject) => {
    let output = '';
    fixture.stdout.on('data', data => { output += data; const match = output.match(/\{"port":\s*(\d+)\}/); if (match) done('http://127.0.0.1:' + match[1]); });
    fixture.once('exit', code => reject(new Error(`fixture exited ${code}: ${stderr}`)));
    setTimeout(() => reject(new Error('fixture timed out: ' + stderr)), 15000).unref();
  });
  browser = await chromium.launch();
  for (const width of [1440, 390]) {
    const page = await browser.newPage({ viewport: { width, height: 950 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(origin);
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(() => window.sidebarNav('social'));
    await page.waitForSelector('.desk-v1-home');
    const response = await page.request.post(origin + '/api/desk/pieces', { data: { campaign_id: 'camp-test', kind: 'video', title: 'Real MP4 ' + width } });
    assert.equal(response.status(), 201);
    const piece = await response.json();
    await page.evaluate(() => window.DeskV1Store.load({ force: true }));
    await page.evaluate(id => window.deskV1Nav('piece', { campaignId: 'camp-test', familyId: id }), piece.id);
    await page.click('[data-piece-media] [data-add-media]');
    const chooser = page.waitForEvent('filechooser');
    await page.getByRole('menuitem', { name: 'From this computer…', exact: true }).click();
    const fc = await chooser;
    assert.match(await fc.element().getAttribute('accept'), /\.mp4/);
    const upload = page.waitForResponse(r => r.url().endsWith(`/pieces/${piece.id}/assets`) && r.request().method() === 'POST');
    await fc.setFiles(resolve(root, 'tools/smoke/fixtures/local-video.mp4'));
    const savedResponse = await upload;
    assert.equal(savedResponse.status(), 201, await savedResponse.text());
    const saved = await savedResponse.json();
    assert.equal(saved.assets.length, 1);
    assert.equal(saved.assets[0].kind, 'video');
    assert.match(saved.assets[0].path, /desk\/library\/video\/Uploads\/local-video.*\.mp4/);
    await page.waitForFunction(() => document.querySelector('[data-piece-media] video')?.readyState >= 1);
    const duration = await page.locator('[data-piece-media] video').evaluate(el => el.duration);
    assert(duration > 0);
    await page.reload(); await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(() => window.sidebarNav('social')); await page.waitForSelector('.desk-v1-home');
    await page.evaluate(id => window.deskV1Nav('piece', { campaignId: 'camp-test', familyId: id }), piece.id);
    await page.waitForSelector('[data-piece-media] [data-pmed-open]');
    assert.equal(await page.locator('[data-piece-media] [data-pmed-open]').count(), 1);
    assert.deepEqual(errors, []);
    console.log(`PASS ${width}: exact piece-page From computer MP4 -> real M21 201 -> saved path -> video metadata ${duration}s -> reload attachment`);
    await page.close();
  }
} finally {
  if (browser) await browser.close();
  if (origin) await fetch(origin + '/__shutdown', { method: 'POST' });
  if (fixture.exitCode === null) {
    await Promise.race([new Promise(done => fixture.once('exit', done)), new Promise(done => setTimeout(done, 2000))]);
    if (fixture.exitCode === null) fixture.kill();
  }
}
