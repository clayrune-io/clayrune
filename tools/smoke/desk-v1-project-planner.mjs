#!/usr/bin/env node
// Home agent coverage and the project chat's default-planner picker.
// Real worktree SPA, hermetic fixture API; never writes live project data.
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { seedDeskV1Fixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const SHOTS = resolve(ROOT, '_scratch/project-planner');
mkdirSync(SHOTS, { recursive: true });
const STATIC = loadStaticJsCss(ROOT);
for (const f of readdirSync(resolve(ROOT, 'assets'))) {
  const type = { png: 'image/png', jpg: 'image/jpeg', svg: 'image/svg+xml', webp: 'image/webp' }[f.split('.').pop()];
  if (type) STATIC['/assets/' + f] = [type, readFileSync(resolve(ROOT, 'assets', f))];
}
const AGENTS = [
  { scope: 'global', name: 'dave', agent_name: 'Dave', avatar: '' },
  { scope: 'global', name: 'fenn', agent_name: 'Fenn', avatar: '' },
];
let checks = 0;
const check = (condition, message) => { assert.ok(condition, message); console.log('PASS ' + message); checks++; };

async function boot(browser, width, { delayedRoster = false, projectFirst = false, initialDefault = null } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 950 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  let releaseRoster;
  const gate = new Promise((r) => { releaseRoster = r; });
  let firstRoster = true;
  await page.route('**/*', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/') return route.fulfill({ contentType: 'text/html', body: readFileSync(resolve(ROOT, 'static/index.html'), 'utf8') });
    if (STATIC[path]) return route.fulfill({ contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/config') return json({ desk_v1: true });
    if (path === '/api/projects') return json([{ id: 'clayrune', name: 'Mobile Test', status: 'active', roster: [], project_path: '/smoke', activity_log: [], backlog: [] }]);
    if (path === '/api/characters') {
      if (firstRoster && delayedRoster) { firstRoster = false; await gate; }
      return json(AGENTS);
    }
    return route.abort();
  });
  await seedDeskV1Fixtures(page);
  await page.addInitScript((initialDefault) => {
    const fx = window.DeskV1Fixtures;
    const p = fx.projects[0];
    p.name = 'Mobile Test';
    p.presence.desk_agent = initialDefault;
    fx.projects = [p];
    const c = fx.campaigns[0];
    c.projectId = p.id;
    c.plan.title = 'Mobile Test campaign';
    c.state = 'active';
    c.how = { ...c.how, agent: 'global:dave' };
    fx.campaigns = [c];
  }, initialDefault);
  await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.desk-v1-home-block-agent');
  if (projectFirst) await page.click('.desk-v1-home-block-name');
  return { ctx, page, errors, releaseRoster };
}

async function setBoard(page, refs, defaultRef = null) {
  await page.evaluate(({ refs, defaultRef }) => {
    const fx = window.DeskV1Fixtures;
    fx.projects[0].presence.desk_agent = defaultRef;
    const original = fx.campaigns[0];
    fx.campaigns = refs.map((entry, i) => {
      const c = structuredClone(original);
      c.id = 'planner-' + i;
      c.state = entry.state || 'active';
      c.how.agent = entry.ref;
      return c;
    });
    window.deskV1Nav('home');
  }, { refs: refs.map((entry) => entry && typeof entry === 'object' ? entry : { ref: entry }), defaultRef });
}

async function header(page, expected, unresolved = false) {
  const chip = page.locator('.desk-v1-home-block-agent');
  await chip.filter({ hasText: expected }).waitFor();
  const text = await chip.innerText();
  check(text.includes(expected) && text.includes('⚠') === unresolved, `Home: ${expected}; warning=${unresolved}`);
  check((await chip.getAttribute('class')).includes('-unresolved') === unresolved, 'Header warning styling agrees with agent coverage');
}

async function run(browser, width) {
  const { ctx, page, errors, releaseRoster } = await boot(browser, width, { delayedRoster: true });
  releaseRoster();
  await header(page, 'Dave plans & writes');
  await page.screenshot({ path: resolve(SHOTS, `home_${width}.png`) });
  await setBoard(page, ['global:dave', 'global:dave', { ref: null, state: 'completed' }, { ref: 'missing', state: 'archived' }]);
  await header(page, 'Dave plans & writes');
  await setBoard(page, ['global:dave', 'global:fenn']);
  await header(page, 'Each campaign has its own agent');
  await setBoard(page, ['global:dave', null]);
  await header(page, 'Pick who plans for this project', true);
  await setBoard(page, ['global:dave', 'global:deleted']);
  await header(page, 'Pick who plans for this project', true);
  await setBoard(page, ['global:dave', null], 'global:fenn');
  await header(page, 'Fenn plans & writes');
  await setBoard(page, ['global:dave']);
  await page.click('.desk-v1-home-block-name');
  const prompt = page.locator('#desk-v1-project-posy button[data-project-agent]');
  await prompt.filter({ hasText: "Set this project's default planner" }).waitFor();
  check(await prompt.getAttribute('aria-haspopup') === 'listbox', 'Project chat prompt is a native picker button');
  await prompt.scrollIntoViewIfNeeded();
  await page.screenshot({ path: resolve(SHOTS, `project_${width}.png`) });
  await prompt.focus();
  await page.keyboard.press('Enter');
  const picker = page.getByRole('listbox', { name: 'Default planner' });
  await picker.waitFor();
  const choices = await picker.locator('[role=option]').allTextContents();
  check(choices.some((s) => s.includes('Dave')) && choices.some((s) => s.includes('Fenn')), 'Keyboard opens existing default-planner picker from project chat');
  await page.screenshot({ path: resolve(SHOTS, `picker_${width}.png`) });
  await page.keyboard.press('Escape');
  await page.locator('.desk-v1-project-header [data-project-agent]').click();
  await picker.waitFor();
  check(JSON.stringify(await picker.locator('[role=option]').allTextContents()) === JSON.stringify(choices), 'Header and chat open the same picker choices');
  await picker.locator('[data-agent-id="global:fenn"]').click();
  await page.waitForFunction(() => window.DeskV1Fixtures.projects[0].presence.desk_agent === 'global:fenn');
  check((await page.locator('#desk-v1-project-posy .desk-thread-name').innerText()) === 'Fenn', 'Picking default updates the project chat name');
  check(await page.evaluate(() => window.DeskV1Fixtures.campaigns[0].how.agent === 'global:dave'), 'Setting project default preserves the campaign agent');
  await page.locator('#desk-v1-undo').click();
  await prompt.waitFor();
  check(await page.evaluate(() => !window.DeskV1Fixtures.projects[0].presence.desk_agent), 'Existing Undo restores the unset default and its picker prompt');
  const layout = await page.locator('.desk-v1-project').evaluate((el) => ({
    width: el.clientWidth, scrollWidth: el.scrollWidth,
    overflow: [...el.querySelectorAll('*')].filter((node) => node.getBoundingClientRect().right > window.innerWidth + 1).map((node) => ({ tag: node.tagName, class: node.className, right: node.getBoundingClientRect().right })),
  }));
  check(layout.overflow.length === 0, `Project controls fit ${width}px viewport: ${JSON.stringify(layout)}`);
  check(errors.length === 0, 'No page errors: ' + JSON.stringify(errors));
  await ctx.close();
}

let browser;
try {
  browser = await chromium.launch();
  for (const width of [1440, 390]) await run(browser, width);
  const { ctx, page, errors, releaseRoster } = await boot(browser, 1440, { delayedRoster: true, projectFirst: true });
  const prompt = page.locator('#desk-v1-project-posy button[data-project-agent]');
  await prompt.waitFor();
  releaseRoster();
  await page.waitForFunction(() => window.DeskV1Kit.resolveDeskAgent('global:dave').name === 'Dave');
  await prompt.filter({ hasText: "Set this project's default planner" }).waitFor();
  await prompt.click();
  await page.getByRole('listbox', { name: 'Default planner' }).waitFor();
  check(errors.length === 0, 'Project chat prompt survives late roster hydration and opens its picker');
  await ctx.close();
  const hydrated = await boot(browser, 1440, { delayedRoster: true, projectFirst: true, initialDefault: 'global:fenn' });
  hydrated.releaseRoster();
  await hydrated.page.waitForFunction(() => document.querySelector('#desk-v1-project-posy .desk-thread-name')?.textContent === 'Fenn');
  check(await hydrated.page.locator('#desk-v1-project-posy .desk-thread-name').count() === 1 && hydrated.errors.length === 0, 'Late roster hydration restores exactly one resolved project chat name');
  check(await hydrated.page.locator('#desk-v1-project-posy [data-project-agent]').count() === 0, 'Resolved project chat name remains plain text');
  await hydrated.ctx.close();
  console.log(`PASS ${checks} project planner checks; screenshots: ${SHOTS}`);
} finally {
  if (browser) await browser.close();
}
