// Real UI module, synthetic metadata only: notices never enter the chat log
// or trigger a request. Rebuilds, archived tabs and review clearing included.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {chromium} from 'playwright';

const script = readFileSync(new URL('../../static/js/agent-mail-hold-notice.js', import.meta.url), 'utf8');
const css = readFileSync(new URL('../../static/css/agent-mail-hold-notice.css', import.meta.url), 'utf8');
const text = 'Your email reply is held for review. Release or reject it in Settings > Connectivity > Agent mail.';
const browser = await chromium.launch({headless: true});
let checks = 0;
try {
  for (const width of [390, 1000]) {
    const page = await browser.newPage({viewport: {width, height: 700}});
    const errors = [], requests = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => requests.push(r.url()));
    await page.setContent(`<style>${css}</style><div class="modal-window"><div class="agent-chat"><div id="agent-output-s1">Original transcript</div><textarea>Original draft</textarea></div></div>`);
    await page.addScriptTag({content: script, type: 'module'});
    await page.waitForFunction(() => typeof window.updateAgentMailHoldNotices === 'function');
    await page.evaluate(text => {
      window.updateAgentMailHoldNotices('p1', {s1: {count: 1, text}, archived: {count: 1, text}});
      window.updateAgentMailHoldNotices('p1', {s1: {count: 1, text}, archived: {count: 1, text}});
    }, text);
    assert.equal(await page.locator('#agent-mail-hold-s1').count(), 1); checks++;
    assert.equal(await page.locator('#agent-mail-hold-s1').innerText(), text); checks++;
    assert.equal(await page.locator('#agent-output-s1').innerText(), 'Original transcript'); checks++;
    assert.equal(await page.locator('textarea').inputValue(), 'Original draft'); checks++;
    const box = await page.locator('#agent-mail-hold-s1').boundingBox();
    assert(box.x >= 0 && box.x + box.width <= width); checks++;
    await page.evaluate(() => {
      document.querySelector('.agent-chat').innerHTML = '<div id="agent-output-s1">Rebuilt transcript</div><div id="agent-output-archived">Archived transcript</div>';
    });
    await page.waitForSelector('#agent-mail-hold-s1');
    await page.waitForSelector('#agent-mail-hold-archived'); checks++;
    await page.evaluate(() => window.updateAgentMailHoldNotices('other', {}));
    assert.equal(await page.locator('.agent-mail-hold-notice').count(), 2); checks++;
    await page.evaluate(() => window.updateAgentMailHoldNotices('p1', null));
    assert.equal(await page.locator('.agent-mail-hold-notice').count(), 2); checks++;
    await page.evaluate(() => window.updateAgentMailHoldNotices('p1', {}));
    assert.equal(await page.locator('.agent-mail-hold-notice').count(), 0); checks++;
    assert.deepEqual(requests, []); checks++;
    assert.deepEqual(errors, []); checks++;
    await page.close();
  }
  console.log(`PASS: ${checks} held-mail chat notice checks (390px, 1000px)`);
} finally { await browser.close(); }
