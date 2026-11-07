#!/usr/bin/env node
// Real worktree SPA: local-time wait label and cancellation without opening chat.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const assets = loadStaticJsCss(root);
const index = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const projects = readFileSync(resolve(root, 'tools/smoke/fixtures/projects.json'), 'utf8');
const resumeAt = '2030-01-01T12:02:00+00:00';
const figure = { session_id: 'reset-child', claude_session_id: 'native',
  name: 'Reset worker', state: 'idle', provider: 'claude', task: 'Continue existing task',
  limit_resume: { phase: 'waiting', resume_at: resumeAt, attempts: 0 } };
const snapshot = { rooms: [{ id: 'smoke_alpha', name: 'Reset test', figures: [figure] }],
  quiet: [], bench: [], counts: { figures: 1, rooms: 1 }, poll_seconds: 60 };
let cancels = 0;
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 }, timezoneId: 'America/Los_Angeles' });
  const errors = [];
  page.on('pageerror', err => errors.push(err.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    const json = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (url.origin !== 'http://limit-resume.smoke') return route.abort();
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: index });
    if (assets[url.pathname]) {
      const [contentType, body] = assets[url.pathname];
      return route.fulfill({ contentType, body });
    }
    if (url.pathname === '/api/projects') return route.fulfill({ contentType: 'application/json', body: projects });
    if (url.pathname === '/api/config') return json({});
    if (url.pathname === '/api/floor') return json(snapshot);
    if (url.pathname === '/api/project/smoke_alpha/agent/reset-child/limit-resume/cancel') {
      assert.equal(route.request().method(), 'POST');
      cancels++;
      figure.limit_resume = null;
      return json({ ok: true });
    }
    return route.abort();
  });
  await page.goto('http://limit-resume.smoke/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card');
  await page.evaluate(() => window.openFloor());
  const button = page.getByRole('button', { name: 'Cancel resume', exact: true });
  await button.waitFor();
  const local = await page.evaluate(at => new Date(at).toLocaleString(), resumeAt);
  assert.ok(await page.getByText('Waiting for reset until ' + local).count());
  await button.click();
  await button.waitFor({ state: 'detached' });
  assert.equal(cancels, 1);
  assert.equal(await page.locator('[data-modal-id="smoke_alpha"]').count(), 0);
  assert.deepEqual(errors, []);
  console.log('PASS: reset wait shows local time; cancel posts once, refreshes Floor, and does not open chat.');
} finally {
  await browser.close();
}
