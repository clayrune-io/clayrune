#!/usr/bin/env node
/**
 * One-prompt team creation, end to end: an agent's ```mc:team``` block renders
 * as the editable team card (static/js/team-card.js), a human edits it, and
 * "Create team" runs the REAL POST /api/characters/team (via
 * fixtures/team_create_proxy.py, against a throwaway directory) so the result
 * is checked on disk: 1 existing agent hired unchanged + 2 new agents created
 * with the edited values.
 *
 * Also: a malformed block renders as an inert notice, a name conflict shows the
 * overwrite prompt and creates nothing until ticked, and Ask Claydo renders the
 * same card from a restored reply.
 *
 * RUN   cd tools/smoke && node team-card.mjs
 * Screenshots go to MC_SMOKE_SHOT_DIR if set.
 * Exit 0 = all checks pass; 1 = a check failed / harness error.
 */
import { readFileSync, readdirSync, mkdirSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const PROXY = resolve(__dirname, 'fixtures', 'team_create_proxy.py');
const ORIGIN = 'http://mc.smoke.test';
const SHOT_DIR = process.env.MC_SMOKE_SHOT_DIR || '';
if (SHOT_DIR) mkdirSync(SHOT_DIR, { recursive: true });

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];
const FIGURES = readdirSync(resolve(REPO_ROOT, 'assets', 'avatars')).filter((f) => f.endsWith('.webp')).map((f) => f.slice(0, -5));

const WORK = mkdtempSync(join(tmpdir(), 'mc-team-smoke-'));
mkdirSync(join(WORK, 'global'), { recursive: true });
const EXISTING = '---\nname: code-reviewer\ndescription: Use for strict review of diffs.\nagent_name: Fenn\nmodel: claude-sonnet-5\n---\nYou review diffs.\n';
writeFileSync(join(WORK, 'global', 'code-reviewer.md'), EXISTING);
const CATALOG = [{ name: 'code-reviewer', scope: 'global', agent_name: 'Fenn', description: 'Use for strict review of diffs.', engine: { model: 'claude-sonnet-5' }, avatar: 'fig:scholar' }];

const PROJECT = {
  id: 'smoke_team', name: 'Smoke Team', status: 'active', domain: 'general', emoji: '', description: '',
  summary: '', current_task: 'Idle', next_action: '', blocked: false, blocked_reason: null,
  activity_log: [], backlog: [], project_path: '/smoke/team', last_updated: '2026-09-14T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null, display_order: 0,
  provider: 'claude', use_streaming_agent: true, roster: [],
};

const TEAM = {
  title: 'Team for a 2D platformer',
  members: [
    { reuse: 'global:code-reviewer', reason: 'already reviews diffs', note: 'pinned to Sonnet; fine for reviews' },
    { name: 'game-designer', agent_name: 'Juniper', role: 'Use for core loop design.', persona: 'You design game loops.', avatar: 'fig:wizard', provider: 'claude', effort: 'high', scope: 'project' },
    { name: 'level-artist', agent_name: 'Moss', role: 'Use for level art direction.', persona: 'You direct level art.', avatar: 'fig:gardener', scope: 'project' },
  ],
};
const block = (obj) => '```mc:team\n' + JSON.stringify(obj, null, 1) + '\n```';

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, m) => (cond ? ok(m) : fail(m));

let lastProxy = null;
const teamCalls = [];
function runProxy(body) {
  const outFile = join(WORK, 'result.json');
  execFileSync('python', [PROXY, WORK, outFile], { input: body, cwd: REPO_ROOT, stdio: ['pipe', 'ignore', 'inherit'], timeout: 90000 });
  lastProxy = JSON.parse(readFileSync(outFile, 'utf8'));
  return lastProxy;
}

async function route(page) {
  await page.route('**/*', (r) => {
    const req = r.request();
    const path = new URL(req.url()).pathname;
    const json = (status, body) => r.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return r.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return r.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return json(200, [PROJECT]);
    if (path === '/api/config') return json(200, {});
    if (path === '/api/avatars') return json(200, { figures: FIGURES, prefix: 'fig:' });
    const fig = path.match(/^\/api\/avatars\/([a-z0-9_-]+)$/i);
    if (fig && FIGURES.includes(fig[1])) return r.fulfill({ status: 200, contentType: 'image/webp', body: readFileSync(resolve(REPO_ROOT, 'assets', 'avatars', fig[1] + '.webp')) });
    if (path === '/api/agent/providers') return json(200, [{ name: 'claude', display_name: 'Claude Code', models: [{ id: 'claude-sonnet-5', label: 'Sonnet 5' }] }]);
    if (path === '/api/characters' && req.method() === 'GET') return json(200, CATALOG);
    if (path === '/api/characters/team' && req.method() === 'POST') {
      teamCalls.push(JSON.parse(req.postData() || '{}'));
      const res = runProxy(req.postData() || '{}');
      return json(res.status, res.body);
    }
    return r.abort();
  });
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
  await route(page);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 }).catch(() => {});

  // A chat output inside a project modal, streamed line by line like a real turn.
  await page.evaluate(({ good, broken, conflict }) => {
    const mk = (sid) => {
      const modal = document.createElement('div');
      modal.dataset.modalId = 'smoke_team';
      modal.style.cssText = 'position:relative;width:760px;background:var(--surface);padding:8px';
      const out = document.createElement('div');
      out.id = 'agent-output-' + sid;
      out.className = 'agent-output';
      modal.appendChild(out);
      document.body.prepend(modal);
    };
    mk('team_sid'); mk('broken_sid'); mk('conflict_sid');
    window.appendAgentLine('team_sid', 'Here is who I would put on it.\n' + good);
    window.appendAgentLine('broken_sid', broken);
    window.appendAgentLine('conflict_sid', conflict);
  }, {
    good: block(TEAM),
    broken: '```mc:team\n{"members": [ not json\n```',
    conflict: block({ members: [{ name: 'game-designer', role: 'Use for loops.', persona: 'You design loops, again.', scope: 'project' }] }),
  });
  await page.waitForTimeout(600);

  const card = page.locator('#agent-output-team_sid .team-card');
  check(await card.count() === 1, 'the mc:team block rendered one team card in the chat');
  check(await page.locator('#agent-output-broken_sid .team-card-invalid').count() === 1, 'a malformed block renders an inert notice, not a card');
  check(await card.locator('.team-member').count() === 3, 'the card has 3 members');
  const reuseText = await card.locator('.team-member.reuse').innerText().catch(() => '');
  check(/REUSE\s+Fenn/.test(reuseText) && /claude-sonnet-5/.test(reuseText) && /pinned to Sonnet/.test(reuseText),
    'the reuse row names the existing agent, shows its engine read-only, and the note');

  // Flip reuse -> new -> reuse: the ref must survive the round trip.
  await card.locator('.team-member[data-idx="0"] [data-act="mode"][data-mode="new"]').click();
  check(await card.locator('.team-member[data-idx="0"].new').count() === 1, 'a reuse row flips to Create new');
  await card.locator('.team-member[data-idx="0"] [data-act="mode"][data-mode="reuse"]').click();
  check(await card.locator('.team-member[data-idx="0"].reuse select[data-f="ref"]').inputValue() === 'global:code-reviewer', 'flipping back restores the same existing agent');

  // Edit fields with real typing.
  await card.locator('.team-member[data-idx="1"] [data-f="agent_name"]').fill('Edited Juniper');
  await card.locator('.team-member[data-idx="2"] [data-f="description"]').fill('Use for edited level art direction.');
  await card.locator('.team-member[data-idx="2"] [data-f="effort"]').selectOption('medium');
  // Add a member, then remove it again.
  await card.locator('[data-act="add"]').click();
  check(await card.locator('.team-member').count() === 4, '+ Add member adds a row');
  await card.locator('.team-member[data-idx="3"] [data-act="remove"]').click();
  check(await card.locator('.team-member').count() === 3, 'remove takes the row out');
  check(await card.locator('.team-member[data-idx="1"] [data-f="agent_name"]').inputValue() === 'Edited Juniper', 'edits survive a re-render');
  await card.locator('[data-f="hireNew"]').uncheck();

  if (SHOT_DIR) {
    await card.screenshot({ path: resolve(SHOT_DIR, 'team-card.png') });
    console.log('  screenshot: ' + resolve(SHOT_DIR, 'team-card.png'));
  }

  await card.locator('[data-act="create"]').click();
  await page.waitForSelector('#agent-output-team_sid .team-card.created', { timeout: 90000 }).catch(() => {});
  check(await card.evaluate((el) => el.classList.contains('created')), 'Create team finished and the card shows created');
  const res = lastProxy || {};
  check(res.status === 201, `the real endpoint answered 201 (got ${res.status})`);
  const body = res.body || {};
  check((body.created || []).length === 2, `2 characters created (got ${(body.created || []).length})`);
  check(JSON.stringify(body.hired) === JSON.stringify(['global:code-reviewer']), `1 existing agent hired (got ${JSON.stringify(body.hired)})`);
  const files = res.files || {};
  check(files['global/code-reviewer.md'] === EXISTING, 'the reused agent\'s file is byte-for-byte unchanged');
  const gd = files['proj/.claude/agents/game-designer.md'] || '';
  const la = files['proj/.claude/agents/level-artist.md'] || '';
  check(/agent_name: Edited Juniper/.test(gd) && /effort: high/.test(gd) && /provider: claude/.test(gd), 'game-designer exists with the edited name and its engine');
  check(/description: Use for edited level art direction\./.test(la) && /effort: medium/.test(la), 'level-artist exists with the edited role and effort');
  check((res.roster || []).map((r) => r.character).join(',') === 'global:code-reviewer', 'the roster holds exactly the reused agent (hire-new was unticked)');
  const sent = teamCalls[0] || { members: [] };
  check(sent.members.length === 3 && sent.members[0].mode === 'reuse' && !('body' in sent.members[0]),
    'the reuse member was sent as a ref only: nothing about its persona or engine left the card');

  // Conflict: a second card proposing the now-existing name.
  const conf = page.locator('#agent-output-conflict_sid .team-card');
  await conf.locator('[data-act="create"]').click();
  await page.waitForTimeout(400);
  await conf.locator('.team-conflict').waitFor({ timeout: 90000 }).catch(() => {});
  check(await conf.locator('.team-conflict input[type="checkbox"]').count() === 1, 'an existing name shows the overwrite prompt');
  check(lastProxy.status === 409 && (lastProxy.files['proj/.claude/agents/game-designer.md'] || '') === gd, 'the conflict wrote nothing');
  await conf.locator('.team-conflict input[type="checkbox"]').check();
  await conf.locator('[data-act="create"]').click();
  await page.waitForSelector('#agent-output-conflict_sid .team-card.created', { timeout: 90000 }).catch(() => {});
  check(lastProxy.status === 201 && /You design loops, again\./.test(lastProxy.files['proj/.claude/agents/game-designer.md'] || ''), 'ticking overwrite then Create replaces it');

  // Ask Claydo renders the same card from a restored reply.
  const ctx2 = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
  const page2 = await ctx2.newPage();
  page2.on('pageerror', (e) => pageErrors.push('[claydo] ' + (e.message || String(e))));
  await page2.addInitScript((text) => {
    localStorage.setItem('walkthrough_done', '1');
    localStorage.setItem('mc_claydo_session', JSON.stringify({ mode: 'ask', minimized: false, draft: '',
      history: [{ role: 'user', text: 'who do I need for a platformer?' }, { role: 'assistant', text }] }));
  }, 'You would want these.\n' + block(TEAM));
  await route(page2);
  await page2.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page2.waitForTimeout(2500);
  check(await page2.locator('[data-modal-id="__claydo"] .team-card .team-member').count() === 3, 'Ask Claydo renders the same team card');
  await ctx2.close();

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail('uncaught: ' + e));
  await ctx.close();
  exitCode = bad === 0 ? 0 : 1;
  console.log(exitCode === 0 ? '\n✅ PASS — team card: render, edit, reuse+create, conflict, Ask Claydo.' : `\n❌ FAIL — ${bad} check(s).`);
} catch (err) {
  console.error('❌ FAIL — smoke harness error:', err && err.stack ? err.stack : err);
} finally {
  if (browser) await browser.close().catch(() => {});
  try { rmSync(WORK, { recursive: true, force: true }); } catch (e) {}
  process.exit(exitCode);
}
