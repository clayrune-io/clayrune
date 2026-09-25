#!/usr/bin/env node
// Scratch screenshot helper for the first-run holistic pass — NOT a smoke
// test, not committed. Walks every visible setup step/state and captures it
// at 1280x800 and 390x844, saving into the MAIN checkout's _scratch dir.
import { readFileSync, readdirSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const OUT_DIR = 'C:\\Users\\levir\\Documents\\_claude\\mission-control\\_scratch\\first-run-holistic';
mkdirSync(OUT_DIR, { recursive: true });

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

const CLAYRUNE_PROJECT = {
  id: 'clayrune', name: 'Clayrune', status: 'active', domain: 'general',
  _is_onboarding_project: true, emoji: '', description: '', summary: 'tour',
  current_task: 'Tour Clayrune', next_action: '', blocked: false, blocked_reason: null,
  activity_log: [], backlog: [], project_path: '/smoke/clayrune',
  last_updated: '2026-09-14T00:00:00Z', last_updated_relative: 'today',
  last_completed: null, live_agent: null, display_order: 0, provider: 'claude',
  use_streaming_agent: true, roster: [],
};
const TWO_PROVIDERS_UNSET = {
  providers: [
    { name: 'claude', display_name: 'Claude Code', installed: true, in_use: true, default: false, auth_status: 'not_logged_in' },
    { name: 'codex', display_name: 'Codex', installed: false, in_use: false, default: false, install_hint: 'npm install -g codex' },
  ],
  default: '',
};

async function setupRoutes(page) {
  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([CLAYRUNE_PROJECT]) });
    if (path === '/api/config') {
      if (req.method() === 'PUT') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ setup_completed: false }) });
    }
    if (path === '/api/walkthrough/sample-project') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, id: 'clayrune', existed: true }) });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/agent/providers') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(TWO_PROVIDERS_UNSET) });
    if (path === '/api/local-auth/status') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ configured: false }) });
    if (path === '/api/system/update/status') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ is_git_repo: true, behind: 0, ahead: 0, has_local_changes: false, update_available: false, projects_in_install_dir: [] }) });
    return route.abort();
  });
}

const saved = [];
async function shot(page, viewportLabel, name) {
  const p = `${OUT_DIR}\\${name}-${viewportLabel}.png`;
  await page.screenshot({ path: p });
  saved.push(p);
}

async function walkAndShoot(browser, viewport, label) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  await setupRoutes(page);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#setup-overlay', { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(200);
  await shot(page, label, 'step-1-welcome');

  await page.click('#setup-overlay .wt-btn-primary'); // Get started
  await page.waitForTimeout(200);
  await shot(page, label, 'step-2-connections');

  page.once('dialog', (d) => d.accept());
  await page.click('#setup-overlay .wt-btn-primary'); // Next (confirm no vendor ready)
  await page.waitForTimeout(200);
  await shot(page, label, 'step-3-essentials-yours');

  await page.click('#setup-overlay .wt-btn-primary'); // Next
  await page.waitForTimeout(200);
  await shot(page, label, 'step-4-essentials-phone-unrevealed');

  await page.click('#setup-overlay button:has-text("Set it up")');
  await page.waitForTimeout(200);
  await shot(page, label, 'step-4-essentials-phone-revealed');

  await page.click('#setup-overlay .wt-btn-primary'); // Next
  await page.waitForTimeout(200);
  await shot(page, label, 'step-5-essentials-detail-collapsed');

  await page.click('#setup-overlay button:has-text("Choose individually")');
  await page.waitForTimeout(150);
  await shot(page, label, 'step-5-essentials-detail-expanded');

  await page.click('#setup-overlay .wt-btn-primary'); // Next -> tour offer
  await page.waitForTimeout(200);
  await shot(page, label, 'step-6-tour-offer');

  await ctx.close();
}

const browser = await chromium.launch();
try {
  await walkAndShoot(browser, { width: 1280, height: 800 }, 'desktop');
  await walkAndShoot(browser, { width: 390, height: 844 }, 'mobile');
} finally {
  await browser.close();
}
console.log(JSON.stringify(saved, null, 2));
