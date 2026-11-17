// MC-1183: real renderers, inert hostile records, no live API or stored writes.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const assets = loadStaticJsCss(root);
// Expose private renderers only in this harness; production exports stay small.
for (const [file, names] of Object.entries({
  'floor.js': ['_floorFigure'],
  'conversation.js': ['_channelRowHTML'],
  'hivemind.js': ['buildWsDetailHTML', 'buildHmOverviewHTML'],
  'skills-panel.js': ['_renderSkillRow', '_quarantineBannerHTML'],
  'mcp.js': ['_renderMCPRow', '_mcpUrlRenderPreview'],
  'backup-panel.js': ['_backupRestoreRowHTML'],
  'project-forms.js': ['renderBreadcrumb'],
  'settings-sections.js': ['refreshRemoteSessions'],
  'workflow-builder.js': ['_wfTabsRowHTML'],
  'provider-auth.js': ['_renderRemoteLoginBox'],
  'mobile-pairing.js': ['_mobilePairRenderAuto'],
  'project-actions.js': ['attHTML'],
})) assets['/static/js/' + file][1] += `\nObject.assign(window.__sinkRenderers ||= {}, {${names.join(',')}});`;
const browser = await chromium.launch();
const page = await browser.newPage();
let sessionLabel = '';
let skillResponse = {};
let checks = 0;
await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
await page.route('**/*', route => {
  const path = new URL(route.request().url()).pathname;
  if (path === '/') return route.fulfill({ contentType: 'text/html', body: readFileSync(resolve(root, 'static/index.html'), 'utf8') });
  if (assets[path]) return route.fulfill({ contentType: assets[path][0], body: assets[path][1] });
  if (path === '/api/projects') return route.fulfill({ json: [] });
  if (path === '/api/config') return route.fulfill({ json: {} });
  if (path === '/api/remote/sessions') return route.fulfill({ json: { sessions: [{ session_id: 'sid', label: sessionLabel }] } });
  if (path.startsWith('/api/skills')) return route.fulfill({ json: skillResponse });
  if (path.startsWith('/api/mcp/')) return route.fulfill({ json: { config: {}, transport: 'stdio' } });
  if (path === '/api/mobile-pair/tokens') return route.fulfill({ json: { tokens: [{ token_id: 'token', label: sessionLabel }] } });
  if (path === '/api/steward/loop-health') return route.fulfill({ json: { enabled: [{ project_id: sessionLabel, project: 'Fixture', cadence_minutes: 60 }] } });
  if (path === '/api/steward-role/assignments') return route.fulfill({ json: { assignments: [{ project_id: sessionLabel, project_name: 'Fixture', charter_text: 'Fixture', schedule_id: 'schedule', interval_minutes: 60 }], blockers: [] } });
  return route.abort();
});
try {
  await page.goto('http://mc.smoke.test', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.modalContentHTML === 'function');
  const result = await page.evaluate(() => {
    const attack = `x\" data-injected=\"yes\"><img data-injected=\"yes\" src=x>`;
    const item = { id: 'item', text: 'keep me', status: attack, priority: attack, github_issue_number: attack };
    const p = { id: 'fixture', name: 'Fixture', status: 'active', backlog: [item], _backlogFull: true, modal_color: { color: attack } };
    modalActiveTab[p.id] = 'backlog';
    const doc = new DOMParser().parseFromString(window.modalContentHTML(p) + window.tileHTML(p), 'text/html');
    return { injections: doc.querySelectorAll('[data-injected]').length, text: doc.body.textContent, count: doc.querySelectorAll('.backlog-item').length };
  });
  assert.equal(result.injections, 0, 'stored status, priority, issue number and color must stay inert');
  assert.equal(result.count, 1, 'legacy record must still render');
  assert.ok(result.text.includes('keep me'));
  console.log('PASS stored backlog and color markup stays inert; legacy record retained');
  checks++;
  await page.waitForFunction(() => Object.keys(window.__sinkRenderers || {}).length === 15);
  const cases = await page.evaluate(async () => {
    const render = window.__sinkRenderers;
    const bad = `O'Brien\\notes\n\r\u2028\u2029 & <tag> " x');window.__injected=1;//`;
    const pid = `old-project');window.__injected=1;//\\"`;
    window.__injected = 0;
    const passed = [];
    function test(label, markup, selector, fn, expected, event = 'click') {
      const host = document.createElement('div');
      host.innerHTML = markup;
      if (host.querySelector('[data-injected]')) throw Error(label + ': injected attribute');
      document.body.append(host);
      let got;
      const old = window[fn];
      window[fn] = (...args) => { got = args.filter(a => !(a instanceof Event)); };
      try {
        const el = host.querySelector(selector);
        if (!el) throw Error(label + ': missing ' + selector);
        el.dispatchEvent(new Event(event, { bubbles: false }));
        if (JSON.stringify(got) !== JSON.stringify(expected)) throw Error(label + ': argument mismatch ' + JSON.stringify(got));
        if (window.__injected) throw Error(label + ': payload executed');
        passed.push(label);
      } finally { window[fn] = old; host.remove(); }
    }
    for (const value of [bad, '\\', "\\'", '\r\n', '\x00\t\b\x7f', '&quot;&#39;', '" onmouseover="window.__injected=1']) {
      test('helper round trip ' + JSON.stringify(value), `<button onclick="__capture('${window.jsAttr(value)}')">go</button>`, 'button', '__capture', [value]);
    }
    const item = { id: 'item', text: 'Legacy', status: bad, priority: bad };
    const project = { id: pid, name: 'Legacy', status: 'active', backlog: [item], _backlogFull: true };
    modalActiveTab[pid] = 'backlog';
    const markup = window.modalContentHTML(project);
    test('FE13 status + FE20 project id', markup, '.backlog-check', 'toggleDone', [pid, 'item', bad]);
    test('FE13 priority', markup, '.priority-badge', 'cyclePriority', [pid, 'item', bad]);
    test('FE20 attachment project id', render.attHTML({ id: 'a', type: 'text', stored_name: 'a.txt' }, pid, 'item'), '.att-del', 'deleteAttachment', [pid, 'item', 'a']);
    test('FE20 Interrupt project id', window.mcSendIntent.interruptButtonHTML(pid, 'sid', 'running', false), 'button', 'sendFollowup', [pid, 'sid', 'interrupt']);
    test('FE2 Floor name', render._floorFigure(pid, { session_id: 'sid', name: bad, state: 'idle' }), '.fl-who', 'floorRename', ['sid', bad]);
    test('CV1 persona name', render._channelRowHTML(project, { key: 'global:helper', hired: true, char: { name: 'helper', agent_name: bad } }, false, false), '.conv-unhire', 'unhireFromProject', [pid, 'global:helper', bad]);
    const ws = { id: 'ws', title: bad, priority: '<img data-injected=yes>', status: 'pending' };
    const full = { manifest: { project_id: pid } };
    const wsMarkup = render.buildWsDetailHTML('hm', ws, full);
    test('FE3 workstream title', wsMarkup, 'button[onclick^="openHmRunsModal"]', 'openHmRunsModal', ['hm', pid, 'worker', 'ws', bad]);
    if (new DOMParser().parseFromString(wsMarkup, 'text/html').querySelector('[data-injected]')) throw Error('FE13 workstream priority');
    passed.push('FE13 workstream priority');
    const skill = { scope: 'project', name: bad, project_id: pid };
    test('SK1 skill name', render._renderSkillRow(skill), 'button[onclick^="openSkillEditor"]', 'openSkillEditor', ['project', bad, pid, false]);
    test('MC2 server name', render._renderMCPRow(skill), 'button[onclick^="openMCPEditor"]', 'openMCPEditor', ['project', bad, pid, false]);
    test('MC1 repository env key', render._mcpUrlRenderPreview('modal', { preview: { secrets: [{ key: bad, hint: 'Fill' }] }, secretValues: {} }), 'input[data-secret-key]', '_mcpUrlSecretChange', ['modal', bad, ''], 'input');
    test('BK1 backup path', render._backupRestoreRowHTML({ path: bad }), 'button', '_backupRestoreConfirm', [bad]);
    test('WB11 workflow label', render._wfTabsRowHTML('fixture', [{ id: 'wf', name: bad }], 'wf'), 'button[onclick^="_wfTabClick"]', '_wfTabClick', ['fixture', 'wf', bad]);
    test('SK3 quarantine force retry', render._quarantineBannerHTML({ findings: [] }, `_doSkillImportGitInstallOne('modal', ${JSON.stringify(bad)}, ${JSON.stringify(bad)}, true)`), 'button', '_doSkillImportGitInstallOne', ['modal', bad, bad, true]);
    const crumb = document.createElement('div'); crumb.id = 'folder-breadcrumb'; document.body.append(crumb);
    render.renderBreadcrumb("C:/O'Brien/notes", "C:/O'Brien");
    test('FE4 directory breadcrumb', crumb.innerHTML, 'span[title="Go up"]', 'loadDirectory', ["C:/O'Brien"]);
    crumb.remove();
    // Exercise cross-backlog's real full renderer against legacy records.
    const oldProjects = allProjects;
    allProjects = [project];
    await window.openAllBacklog();
    const cross = openModals.get('__all_backlog').element;
    test('FE13 cross-backlog status', cross.innerHTML, '.backlog-check', 'toggleDone', [pid, 'item', bad]);
    window.closeModalById('__all_backlog');
    allProjects = oldProjects;
    return passed;
  });
  for (const label of cases) { console.log('PASS ' + label); checks++; }
  sessionLabel = `Phone's \\name " onmouseover="window.__injected=1`;
  const remote = await page.evaluate(async label => {
    const host = document.createElement('div'); host.id = 'remote-sessions-list'; document.body.append(host);
    await window.__sinkRenderers.refreshRemoteSessions();
    let args;
    const old = window.renameRemoteSession;
    window.renameRemoteSession = (...a) => { args = a; };
    host.querySelector('a[onclick^="renameRemoteSession"]').click();
    window.renameRemoteSession = old;
    host.remove();
    return { args, injected: window.__injected };
  }, sessionLabel);
  assert.deepEqual(remote, { args: ['sid', sessionLabel], injected: 0 });
  console.log('PASS ST1 named session rename'); checks++;
  const uiCases = await page.evaluate(async label => {
    const r = window.__sinkRenderers;
    const host = document.createElement('div'); document.body.append(host);
    const result = [];
    async function capture(fn, el, expected) {
      let args;
      const old = window[fn]; window[fn] = (...a) => { args = a; };
      el.click(); window[fn] = old;
      if (JSON.stringify(args) !== JSON.stringify(expected)) throw Error(fn + ': bad args ' + JSON.stringify(args));
      if (window.__injected) throw Error(fn + ': injected script');
    }
    host.id = 'settings-remote-login-smoke';
    const url = "https://example.test/login?name=O'Brien&path=\\notes";
    r._renderRemoteLoginBox('smoke', url);
    await capture('openBrowserPane', host.querySelector('button'), [url, window.currentProjectId, null, 'smoke-login']);
    result.push('FE4 provider login URL');
    await r._mobilePairRenderAuto(host, {});
    await capture('_mobilePairRevokeToken', host.querySelector('button[onclick^="_mobilePairRevokeToken"]'), ['token', label]);
    result.push('FE4 mobile label');
    host.id = 'steward-list';
    await window.renderStewards();
    await capture('stewardOpenCharter', host.querySelector('button[onclick^="stewardOpenCharter"]'), [label]);
    result.push('FE20 legacy steward project id');
    await window.renderStewardRoles();
    const roleArea = document.getElementById('steward-role-area');
    const oldStart = window.StewardRoleEditor.start;
    let rolePid;
    window.StewardRoleEditor.start = id => { rolePid = id; };
    roleArea.querySelector('[data-srole-act="start"]').click();
    window.StewardRoleEditor.start = oldStart;
    if (rolePid !== label || window.__injected) throw Error('FE20 steward role project id');
    result.push('FE20 steward role project id');
    roleArea.remove();
    host.remove();
    return result;
  }, sessionLabel);
  for (const label of uiCases) { console.log('PASS ' + label); checks++; }
  const pickerCases = await page.evaluate(async voiceLabel => {
    const rel = "skills\\xlsx\\O'Brien\n\" onmouseover=\"window.__injected=1";
    const name = "O'Brien\\notes\" <tag>";
    const oldProof = window.humanProofFetch;
    window.humanProofFetch = async () => ({ ok: true, body: { multiple: true, staging_id: 'stage', candidates: [{ rel_dir: rel, name }] } });
    const done = [];
    async function clickAndCapture(win, selector, fn, expected) {
      let args;
      const old = window[fn]; window[fn] = (...a) => { args = a; };
      win.querySelector(selector).click(); window[fn] = old;
      if (JSON.stringify(args) !== JSON.stringify(expected)) throw Error(fn + ': path lost ' + JSON.stringify(args));
      if (window.__injected) throw Error(fn + ': injected');
    }
    try {
      window.openSkillImportFolder();
      document.getElementById('sif-path').value = 'C:/smoke';
      await window._doSkillImportFolder('__skill_import_folder');
      await clickAndCapture(openModals.get('__skill_import_folder').element, '#sif-candidates button[onclick^="_doSkillImportFolder"]', '_doSkillImportFolder', ['__skill_import_folder', rel]);
      window.closeModalById('__skill_import_folder');
      done.push('SK2 folder candidate Windows path');
      window.openSkillImportGit();
      document.getElementById('sig-url').value = 'https://example.test/skills.git';
      await window._doSkillImportGit('__skill_import_git');
      await clickAndCapture(openModals.get('__skill_import_git').element, 'button[onclick^="_doSkillImportGitInstallOne"]', '_doSkillImportGitInstallOne', ['__skill_import_git', rel, name]);
      window.closeModalById('__skill_import_git');
      done.push('SK2 Git candidate Windows path');
      window.openSkillEditor('global', name, null, false);
      const id = '__skill_global_' + name;
      const win = openModals.get(id).element;
      const radioNames = [...win.querySelectorAll('input[type=radio]')].map(e => e.name);
      if (!radioNames.every(n => n === 'se-scope-' + id)) throw Error('SK1 radio name escaped incorrectly');
      await clickAndCapture(win, 'button[onclick^="saveSkillFromEditor"]', 'saveSkillFromEditor', [id, false, 'global', name, null]);
      let request;
      window.humanProofFetch = async (url, options) => {
        request = { url, data: JSON.parse(options.body) };
        return { ok: true, body: { name } };
      };
      win.querySelector('#se-description').value = 'Preserve this description';
      win.querySelector('#se-body').value = 'Preserve this body';
      await window.saveSkillFromEditor(id, false, 'global', name, null);
      if (request?.url !== '/api/skills/global/' + encodeURIComponent(name) || request.data.body !== 'Preserve this body') throw Error('SK1 editor save lost its identity or body');
      window.closeModalById(id);
      done.push('SK1 editor modal id and radio attributes');
    } finally { window.humanProofFetch = oldProof; }
    return done;
  }, sessionLabel);
  for (const label of pickerCases) { console.log('PASS ' + label); checks++; }
  console.log(`${checks} passed, 0 failed`);
} finally { await browser.close(); }
