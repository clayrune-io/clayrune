#!/usr/bin/env node
// Real SPA from this branch, stub APIs only: no paid calls or live settings.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const STATIC = loadStaticJsCss(ROOT);
const HTML = readFileSync(resolve(ROOT, 'static/index.html'), 'utf8');
const shots = resolve(ROOT, '_scratch/api_credits_phase2');
mkdirSync(shots, { recursive: true });
const pid = 'credits_smoke';
const project = { id: pid, name: 'Credits preview', status: 'active', domain: 'general',
  description: '', summary: '', current_task: 'Idle', next_action: '', activity_log: [], backlog: [],
  project_path: '/smoke', provider: 'claude', use_streaming_agent: true, display_order: 0 };
let checks = 0;
function check(value, label) { assert.ok(value, label); checks++; console.log('PASS ' + label); }

const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 1000 } });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
    let settings = { mode: 'on', secret_name: 'test-key', plan_credit_usd: 200,
      budget_usd: 100, stop_pct: 90, reset_day: 1, session_reserve_usd: 3 };
    const cycle = { background_spent_usd: 2.5, session_spent_usd: 8.25, spent_usd: 10.75 };
    const settingsWrites = [], scheduleWrites = [];
    await page.route('**/*', route => {
      const path = new URL(route.request().url()).pathname;
      const json = data => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
      if (route.request().url().includes('/mermaid@')) return route.fulfill({
        contentType: 'text/javascript', body: 'export default { initialize() {}, run: async () => {} };' });
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: HTML });
      if (STATIC[path]) return route.fulfill({ contentType: STATIC[path][0], body: STATIC[path][1] });
      if (path === '/api/projects') return json([project]);
      if (path === '/api/config') return json({ default_provider: 'claude' });
      if (path === '/api/local-auth/status') return json({ configured: true });
      if (path === '/api/characters') return json([]);
      if (path === '/api/secrets') return json({ locked: false, secrets: [{ name: 'test-key', allow_unattended: true }] });
      if (path === '/api/agent/providers') return json({ default: 'claude', providers: [{
        name: 'claude', display_name: 'Claude Code', installed: true, auth_status: 'ok', capabilities: {}, default: true }] });
      if (path === '/api/api-credits/status') return json({ settings, cycle, plan: { label: 'Ultra', credit_usd: 200 } });
      if (path === '/api/api-credits/settings') {
        const body = JSON.parse(route.request().postData());
        settingsWrites.push(body); settings = { ...settings, ...body };
        return json({ settings, cycle, plan: { label: 'Ultra', credit_usd: 200 } });
      }
      if (path === '/api/schedules') {
        if (route.request().method() === 'POST') scheduleWrites.push(JSON.parse(route.request().postData()));
        return json([]);
      }
      return route.abort();
    });
    await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(() => { window.openSettings(); window.drillSettings('providers'); });
    await page.waitForSelector('#settings-providers-section');
    await page.evaluate(() => {
      const pane = document.querySelector('#settings-detail .settings-detail-pane[data-cat="providers"]');
      const i = [...pane.children].filter(el => el.classList.contains('settings-section')).findIndex(s => s.id === 'settings-providers-section');
      window.drillSettingsSub(i);
    });
    await page.waitForSelector('#apc-reserve');
    check(await page.inputValue('#apc-reserve') === '3', `${width}: reserve defaults to $3`);
    const split = await page.locator('#apc-spend-split').innerText();
    check(split.includes('background calls $2.50') && split.includes('agent sessions $8.25'), `${width}: separate spend totals`);
    // Raising reserve narrows spending and does not require a new passcode.
    await page.fill('#apc-reserve', '4.25');
    await page.click('#apc-save');
    await page.waitForFunction(() => /Saved\./.test(document.getElementById('apc-msg')?.textContent || ''));
    check(settingsWrites.at(-1).session_reserve_usd === 4.25, `${width}: Save carries reserve`);
    await page.locator('#apc-reserve').scrollIntoViewIfNeeded();
    await page.screenshot({ path: resolve(shots, `settings-${width}.png`) });

    await page.evaluate(() => { window.closeModalById('__settings'); window.openScheduler(); window.showScheduleForm(); });
    await page.waitForSelector('#sched-billing');
    check(await page.inputValue('#sched-billing') === 'auto', `${width}: new schedule defaults Auto`);
    await page.selectOption('#sched-billing', 'subscription');
    await page.fill('#sched-task', 'Smoke task');
    await page.evaluate(() => window.saveSchedule());
    check(scheduleWrites.at(-1)?.billing === 'subscription', `${width}: schedule saves opt-out`);
    await page.evaluate(() => window.showScheduleForm({ id: 'saved', project_id: 'credits_smoke', task: 'Smoke task', billing: 'subscription' }));
    check(await page.inputValue('#sched-billing') === 'subscription', `${width}: edit restores opt-out`);
    await page.locator('#sched-billing').scrollIntoViewIfNeeded();
    await page.screenshot({ path: resolve(shots, `scheduler-${width}.png`) });

    await page.evaluate(pid => {
      const sid = 'credits-chat';
      agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Credits preview', task: 'Scheduled report',
        status: 'completed', startedAt: new Date().toISOString(), billing: 'credits' });
      agentStatusCache[sid] = { projectId: pid, status: 'completed', task: 'Scheduled report', billing: 'credits' };
      agentOutputBuffers[sid] = ['The scheduled report is complete.'];
      activeAgentTab[pid] = sid; window.openProjectModal(pid); activeAgentTab[pid] = sid; window.refreshModal();
    }, pid);
    await page.waitForSelector('.api-credits-badge');
    check((await page.locator('.api-credits-badge').textContent()).trim() === 'API credits', `${width}: credits session badge`);
    await page.screenshot({ path: resolve(shots, `chat-${width}.png`) });
    await page.evaluate(pid => { agentStatusCache['credits-chat'].billing = 'subscription';
      agentHistory.find(s => s.sessionId === 'credits-chat').billing = 'subscription'; window.refreshModal(); }, pid);
    check(await page.locator('.api-credits-badge').count() === 0, `${width}: subscription has no credits badge`);
    check(errors.length === 0, `${width}: no page errors: ${errors.join('; ')}`);
    await context.close();
  }
} finally { await browser.close(); }
console.log(`${checks} checks passed`);
