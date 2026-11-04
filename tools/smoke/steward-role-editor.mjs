#!/usr/bin/env node
/**
 * Steward role editor (BUILTINS-3 SR-5) -> static/js/steward-role-editor.js.
 *
 * Pins the human surface for a steward role, at desktop (1100) and phone (390):
 *   - the Automation window lists role cards apart from raw schedule rows, with
 *     the install-level blockers stated one by one and Start disabled while any
 *     blocker stands (an accurate blocked state, not a hidden one);
 *   - the schedule form's "Run as a steward role" section hides the task prompt,
 *     lists charter items (one already another role's charter is disabled),
 *     previews read-only, and refuses an unpicked charter / empty journal;
 *   - Save sends steward_role + task:'' + enabled:false through humanProofFetch
 *     (the passcode path), never a bare fetch; a cancelled prompt sends nothing;
 *   - Start / Stop go through humanProofFetch too; Stop on a role project from
 *     the old Stewards card is redirected to the role control;
 *   - editing a role then unchecking the box detaches it (steward_role:null,
 *     enabled:false) only after a confirm;
 *   - no horizontal overflow at 390px.
 *
 * Hermetic, same idiom as scheduler-run-condition.mjs: page + static assets
 * served from THIS checkout, API canned in memory, everything else aborted.
 * humanProofFetch is replaced with a recorder so no passcode modal is needed.
 *
 * RUN: node tools/smoke/steward-role-editor.mjs
 *      SMOKE_SHOT_DIR=<dir> also writes screenshots.
 */
import { readFileSync, existsSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const STATIC_ROOT = resolve(REPO_ROOT, 'static');
const ORIGIN = 'http://mc.smoke.test';
const SHOT_DIR = process.env.SMOKE_SHOT_DIR || '';
if (SHOT_DIR) mkdirSync(SHOT_DIR, { recursive: true });

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, m) => (cond ? ok(m) : fail(m));

const projects = JSON.parse(readFileSync(resolve(__dirname, 'fixtures', 'projects.json'), 'utf8'))
  .map((p) => ({ ...p, project_path: 'C:/smoke/alpha' }));
const PID = projects[0].id;

const BLOCKERS = [
  { code: 'core_adapter_unapplied', message: 'The SR-2 core adapter patch is not applied, so a role cycle cannot be composed. A person applies it.' },
  { code: 'lifecycle_adapter_unavailable', message: 'The decision-channel readiness adapter is not installed, so role delivery is held.' },
];
const ROLE_ROW = {
  id: 'sr1', enabled: false, project_id: PID, project_name: projects[0].name, task: '', workflow_id: '',
  character: 'dave', description: '', continue_session: true, schedule_type: 'interval', interval_minutes: 240,
  time: '09:00', days: [], run_at: '', cron_expr: '', last_run: null, next_run: null, run_condition: null,
  character_display: null, workflow_display: null,
  steward_role: { version: 1, charter_item_id: 'item1', journal_path: 'docs/_journal/item1-pilot.md' },
};
const TASK_ROW = { ...ROLE_ROW, id: 'plain1', task: 'Do the thing', steward_role: undefined, character: '' };
delete TASK_ROW.steward_role;
const ASSIGNMENT = {
  schedule_id: 'sr1', project_id: PID, project_name: projects[0].name, character: 'dave', enabled: false,
  steward_mode: 'off', active: false, pointed: true, charter_item_id: 'item1',
  journal_path: 'docs/_journal/item1-pilot.md', next_run: null, last_error: null, interval_minutes: 240,
  charter_text: 'Pilot steward\nKeep the docs honest.', charter_status: 'open', role_error: '',
};
const OPTIONS = {
  project_id: PID, default_character: 'dave', steward_mode: 'off', blockers: BLOCKERS,
  charters: [
    { id: 'item1', title: 'Pilot steward', status: 'open', bound_schedule_id: 'sr1', suggested_journal: 'docs/_journal/item1-pilot-steward.md' },
    { id: 'item2', title: 'Docs charter', status: 'open', bound_schedule_id: null, suggested_journal: 'docs/_journal/item2-docs-charter.md' },
  ],
};

async function run(label, viewport) {
  console.log(`\n── ${label} (${viewport.width}px) ──`);
  let assignmentRows = [ASSIGNMENT];
  const proof = [];           // every humanProofFetch call
  const barePosts = [];       // any non-GET that bypassed it
  let proofAnswer = 'ok';     // 'ok' | 'cancel' | 'fail'
  let previewBody = null;

  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  page.on('dialog', (d) => d.accept());

  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const json = (body, status = 200) => route.fulfill({
      status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html')
      return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8',
        body: readFileSync(resolve(STATIC_ROOT, 'index.html'), 'utf8') });
    if (path.startsWith('/static/')) {
      const file = normalize(join(STATIC_ROOT, path.slice('/static/'.length)));
      if (file.startsWith(STATIC_ROOT) && existsSync(file)) {
        const type = file.endsWith('.js') ? 'text/javascript; charset=utf-8'
          : file.endsWith('.css') ? 'text/css; charset=utf-8' : 'application/octet-stream';
        return route.fulfill({ status: 200, contentType: type, body: readFileSync(file, 'utf8') });
      }
      return route.abort();
    }
    if (path === '/api/projects') return json(projects);
    if (path === '/api/local-auth/status') return json({ configured: true });
    if (path === '/api/config') return json({});
    if (path === '/api/characters') return json([{ name: 'dave', agent_name: 'Dave', scope: 'global' }]);
    if (path === '/api/schedules' && req.method() === 'GET') return json([ROLE_ROW, TASK_ROW]);
    if (path === '/api/steward/loop-health') return json({ enabled: [] });
    if (path === '/api/steward-role/assignments') return json({ assignments: assignmentRows, blockers: BLOCKERS });
    if (path === '/api/steward-role/options') return json(OPTIONS);
    if (path === '/api/steward-role/preview' && req.method() === 'POST') {
      previewBody = JSON.parse(req.postData() || '{}');
      return json({ ok: true, runnable_now: false, blockers: BLOCKERS, owner: 'dave',
        journal_path: previewBody.steward_role.journal_path,
        checks: [{ name: 'charter', ok: true, detail: 'open: Docs charter' },
                 { name: 'journal', ok: true, detail: 'journal file is created by the first cycle' }],
        charter: { id: 'item2', text: 'Docs charter\nKeep docs current.', status: 'open' },
        note: 'Preview only: nothing was saved.' });
    }
    if (req.method() !== 'GET') barePosts.push(req.method() + ' ' + path);
    return route.abort();
  });

  const shot = async (name) => { if (SHOT_DIR) await page.screenshot({ path: join(SHOT_DIR, `${name}-${viewport.width}.png`), fullPage: false }); };

  try {
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => typeof window.openScheduler === 'function'
      && typeof window.StewardRoleEditor === 'object', { timeout: 20000 });
    // Recorder in place of the passcode modal.
    await page.evaluate((initial) => {
      window.__proof = [];
      window.__answer = initial;
      window.humanProofFetch = async (url, init, opts) => {
        window.__proof.push({ url, method: init.method, body: JSON.parse(init.body || '{}'), title: opts && opts.title });
        if (window.__answer === 'cancel') return null;
        if (window.__answer === 'fail') return { ok: false, status: 400, body: { error: 'role refused by the server' } };
        return { ok: true, status: 200, body: {} };
      };
    }, proofAnswer);
    const proofs = () => page.evaluate(() => window.__proof);

    await page.evaluate(() => window.openScheduler());
    await page.waitForSelector('[data-srole-card]', { timeout: 10000 });

    // ── 1. Cards: role apart from the plain schedule rows ───────────────
    const listText = await page.evaluate(() => document.getElementById('schedule-list')
      ? document.getElementById('schedule-list').textContent : document.body.textContent);
    check(await page.evaluate(() => document.querySelectorAll('[data-srole-card]').length) === 1,
      'one steward role card rendered');
    check(listText.includes('Do the thing') && !listText.includes('Keep the docs honest'),
      'the plain task schedule stays in the schedule list; the role does not');
    const blockerCodes = await page.evaluate(() =>
      [...document.querySelectorAll('[data-srole-blockers] li')].map((l) => l.dataset.code));
    check(blockerCodes.join() === 'core_adapter_unapplied,lifecycle_adapter_unavailable',
      'each install blocker is listed separately');
    check(await page.evaluate(() => document.querySelector('[data-srole-act="start"]').disabled),
      'Start is disabled while blockers stand');
    check((await page.evaluate(() => document.querySelector('[data-srole-card]').textContent)).includes('Disabled'),
      'card state reads Disabled (not Active)');
    await page.locator('#steward-role-area').scrollIntoViewIfNeeded();
    await shot('role-card-blocked');

    // ── 2. Role form: edit the existing role ────────────────────────────
    await page.evaluate(() => window.editSchedule('sr1'));
    await page.waitForSelector('#sched-role-charter', { timeout: 5000 });
    const form = await page.evaluate(() => ({
      on: document.getElementById('sched-role-on').checked,
      taskHidden: document.getElementById('sched-task-block').style.display === 'none',
      charter: document.getElementById('sched-role-charter').value,
      journal: document.getElementById('sched-role-journal').value,
    }));
    check(form.on && form.taskHidden && form.charter === 'item1' && form.journal === 'docs/_journal/item1-pilot.md',
      'edit opens with the role checked, task prompt hidden, charter + journal prefilled');
    check(await page.evaluate(() => [...document.querySelectorAll('#sched-role-charter option')]
        .filter((o) => o.disabled).length) === 0,
      "this role's own charter is selectable (not shown as taken)");
    await page.locator('#sched-role-area').scrollIntoViewIfNeeded();
    await shot('role-form-edit');

    // ── 3. New role: taken charter disabled, preview read-only, validation ──
    await page.evaluate(() => window.showScheduleForm());
    await page.waitForSelector('#sched-role-on', { timeout: 5000 });
    await page.evaluate(() => document.getElementById('sched-role-on').click());
    await page.waitForSelector('#sched-role-charter', { timeout: 5000 });
    check(await page.evaluate(() => document.getElementById('sched-task-block').style.display === 'none'),
      'ticking the box hides the task prompt');
    check(await page.evaluate(() => [...document.querySelectorAll('#sched-role-charter option')]
        .find((o) => o.value === 'item1').disabled),
      "a charter already bound to another role is disabled");
    check((await page.evaluate(() => document.querySelector('#sched-role-fields [data-srole-blockers]')?.textContent || ''))
        .includes('Cycle composer patch not applied'),
      'the form states the blocker too');

    await page.evaluate(() => window.saveSchedule());
    check((await page.evaluate(() => document.getElementById('sched-role-error').textContent)).includes('Pick the backlog item'),
      'saving with no charter is refused in the form');
    check((await proofs()).length === 0, 'no passcode request was made for the refused save');

    await page.selectOption('#sched-role-charter', 'item2');
    check(await page.inputValue('#sched-role-journal') === 'docs/_journal/item2-docs-charter.md',
      'picking a charter suggests its journal path');
    await page.click('#sched-role-preview-btn');
    await page.waitForSelector('#sched-role-preview .srole-verdict', { timeout: 5000 });
    const verdict = await page.evaluate(() => document.querySelector('.srole-verdict').textContent);
    check(/cannot run on this install yet/.test(verdict), 'preview verdict says valid-but-cannot-run');
    check(previewBody && previewBody.steward_role.charter_item_id === 'item2' && previewBody.enabled === false,
      'preview request carries the draft role, disabled');
    check((await proofs()).length === 0, 'preview never goes through the passcode path (it writes nothing)');
    await page.locator('#sched-role-preview').scrollIntoViewIfNeeded();
    await shot('role-form-preview');

    // ── 4. Save: passcode path, role body, disabled, empty task ─────────
    await page.evaluate(() => { document.getElementById('sched-project').value = ''; });
    await page.evaluate((pid) => { document.getElementById('sched-project').value = pid; }, PID);
    await page.evaluate(() => window.saveSchedule());
    await page.waitForFunction(() => window.__proof.length === 1, { timeout: 5000 });
    const save = (await proofs())[0];
    check(save.method === 'POST' && save.url.endsWith('/api/schedules')
        && save.body.steward_role.charter_item_id === 'item2'
        && save.body.steward_role.journal_path === 'docs/_journal/item2-docs-charter.md'
        && save.body.task === '' && save.body.enabled === false,
      'create sends steward_role + empty task + enabled:false through humanProofFetch');
    await page.waitForFunction(() => !document.querySelector('#sched-role-on'), { timeout: 5000 });
    ok('form closes after a successful save');

    // ── 5. Cancelled / refused prompts ──────────────────────────────────
    await page.evaluate(() => { window.__proof.length = 0; window.__answer = 'cancel'; window.showScheduleForm(); });
    await page.waitForSelector('#sched-role-on', { timeout: 5000 });
    await page.evaluate(() => document.getElementById('sched-role-on').click());
    await page.waitForSelector('#sched-role-charter', { timeout: 5000 });
    await page.selectOption('#sched-role-charter', 'item2');
    await page.evaluate(() => window.saveSchedule());
    await page.waitForFunction(() => window.__proof.length === 1, { timeout: 5000 });
    check(await page.evaluate(() => !!document.querySelector('#sched-role-on')),
      'a cancelled passcode prompt leaves the form open and saves nothing');
    await page.evaluate(() => { window.__answer = 'fail'; window.saveSchedule(); });
    await page.waitForFunction(() => document.getElementById('sched-role-error').style.display !== 'none', { timeout: 5000 });
    check((await page.evaluate(() => document.getElementById('sched-role-error').textContent)).includes('role refused by the server'),
      "a server refusal is shown in the form, in the server's words");
    await page.evaluate(() => window.hideScheduleForm());

    // ── 6. Detach: editing a role, unticking it ─────────────────────────
    await page.evaluate(() => { window.__proof.length = 0; window.__answer = 'ok'; window.editSchedule('sr1'); });
    await page.waitForSelector('#sched-role-charter', { timeout: 5000 });
    await page.evaluate(() => document.getElementById('sched-role-on').click());
    check(await page.evaluate(() => !!document.querySelector('[data-srole-detach]')),
      'unticking an existing role explains that saving detaches it');
    await page.evaluate(() => { document.getElementById('sched-task').value = 'Back to a plain task'; window.saveSchedule(); });
    await page.waitForFunction(() => window.__proof.length === 1, { timeout: 5000 });
    const det = (await proofs())[0];
    check(det.method === 'PUT' && det.url.endsWith('/api/schedules/sr1') && det.body.steward_role === null
        && det.body.enabled === false && det.body.task === 'Back to a plain task',
      'detach sends steward_role:null + enabled:false + the typed task via the passcode path');

    // ── 7. Start / Stop ────────────────────────────────────────────────
    await page.evaluate(() => { window.__proof.length = 0; });
    await page.evaluate((pid) => window.StewardRoleEditor.start(pid), PID);
    await page.evaluate((pid) => window.StewardRoleEditor.stop(pid), PID);
    const ctl = await proofs();
    check(ctl.length >= 2 && ctl[ctl.length - 2].url.endsWith(`/api/project/${PID}/steward/enable`)
        && ctl[ctl.length - 1].url.endsWith(`/api/project/${PID}/steward/disable`),
      'Start and Stop post to the project steward enable/disable via the passcode path');
    // Old Stewards card Stop redirects to the role control for a role project.
    await page.evaluate(() => { window.__proof.length = 0; });
    await page.evaluate((pid) => window.stewardDisable(pid), PID);
    check((await proofs()).length === 1 && (await proofs())[0].url.endsWith('/steward/disable'),
      'Stop on a role project from the Stewards list uses the role control, not the bare confirm path');

    // ── 8. Active role (no blockers case is the server\'s; here Stop shows) ──
    assignmentRows = [{ ...ASSIGNMENT, enabled: true, steward_mode: 'on', active: true, next_run: new Date(Date.now() + 3600e3).toISOString() }];
    await page.evaluate(() => window.renderStewardRoles());
    await page.waitForSelector('[data-srole-act="stop"]', { timeout: 5000 });
    check((await page.evaluate(() => document.querySelector('[data-srole-card]').textContent)).includes('Active, cannot run'),
      'an active role on a blocked install reads "Active, cannot run"');

    // ── 9. Overflow ────────────────────────────────────────────────────
    const overflow = await page.evaluate(() => {
      const root = document.getElementById('steward-role-area');
      return root.scrollWidth - root.clientWidth;
    });
    check(overflow <= 1, `role area has no horizontal overflow (${overflow}px)`);
    await page.evaluate(() => window.editSchedule('sr1'));
    await page.waitForSelector('#sched-role-charter', { timeout: 5000 });
    const formOverflow = await page.evaluate(() => {
      const a = document.getElementById('sched-role-area');
      return a.scrollWidth - a.clientWidth;
    });
    check(formOverflow <= 1, `role form section has no horizontal overflow (${formOverflow}px)`);
    await page.locator('#sched-role-area').scrollIntoViewIfNeeded();
    await shot('role-form-active');

    check(barePosts.length === 0, 'no write bypassed humanProofFetch' + (barePosts.length ? ': ' + barePosts.join(', ') : ''));
    check(pageErrors.length === 0, 'no uncaught page errors' + (pageErrors.length ? ': ' + pageErrors.join(' | ') : ''));
  } catch (e) {
    fail('harness error: ' + (e && e.stack || e));
  }
  await browser.close();
}

await run('desktop', { width: 1100, height: 1000 });
await run('phone', { width: 390, height: 844 });

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('\nsteward-role-editor: all checks passed');
