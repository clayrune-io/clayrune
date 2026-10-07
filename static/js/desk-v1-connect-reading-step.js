// MC-1062 follow-up: account reading settings are a section of Permissions.
// Drafts never write. Review applies only changed fields through the existing
// human-only account PATCH; no connection/grant/credential is invented here.
import { ConnectCopy as C } from './desk-v1-connect-copy.js';
const W = window.DeskV1ConnectWizard, P = window.DeskV1ConnectPermissionsStep;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const READABLE = ['x', 'linkedin', 'youtube', 'instagram', 'tiktok'];
const fresh = () => ({ key:'', account:null, via:null, originalVia:null, profile:'', originalProfile:'', pages:null, needed:false, loading:false, error:'', ctx:null, pid:null });
let R = fresh(), seq = 0;
function account(c) { return c.api.ctx?.channels?.().find(a => a.id === c.target?.account?.id); }
function matches(c) { return C.isEnabled() && !!c.target && READABLE.includes(account(c)?.platform || c.api.info?.service?.id); }
function readingProjectId(ch) {
  const state = window.DeskV1Store.state();
  return state.campaigns.find(c => c.projectId && c.plan?.accounts?.includes(ch?.id))?.projectId || state.projects[0]?.id || null;
}
function init(c) {
  if (!matches(c)) return;
  const ch = account(c), key = `${c.api.sel.service}|${c.target.account.id || c.target.account.new?.identity}`;
  if (R.key === key) return;
  seq++;
  R = fresh(); R.key = key; R.account = ch; R.ctx = c.api.ctx; R.pid = readingProjectId(ch);
  R.originalVia = ch?.read_via || 'pane';
  R.via = R.originalVia === 'api' && (ch?.platform || c.api.info.service.id) !== 'x' ? null : R.originalVia;
  R.originalProfile = ch?.browser_profile || c.profile || '';
  R.profile = c.profile || R.originalProfile;
  R.pages = window.DeskV1ReadPages.create(ch?.read_pages || []);
  load(c);
}
function load(c) {
  if (!R.account || !R.pid) return;
  const n = ++seq; R.loading = true; R.error = '';
  c.api.ctx.api('GET', `/api/desk/engagement/coverage/${encodeURIComponent(R.pid)}`).then(result => {
    if (n !== seq) return;
    const entries = result.coverage || [];
    const entry = entries.find(e => e.channel_id === R.account.id || e.account_id === R.account.id)
      || entries.find(e => e.platform === R.account.platform);
    R.needed = entry?.pages?.status === 'pages_needed';
  }).catch(e => { if (n === seq) R.error = e.message || C.words.readingLoadFailed; })
    .finally(() => { if (n === seq) { R.loading = false; c.api.repaint(); } });
}
function problem(c) {
  if (!matches(c)) return '';
  init(c);
  return R.loading ? C.words.readingLoading : R.error ? C.words.readingLoadFailed : !R.via ? C.words.readChoiceNeeded : (R.via === 'pane' ? R.pages.problem() : '');
}
function html(c) {
  if (!matches(c)) return '';
  init(c);
  // A newly configured sign-in's profile comes from Setup, with its own save.
  if (c.api.sel.type === 'signin' && c.profile) R.profile = c.profile;
  const w = C.words, platform = R.account?.platform || c.api.info.service.id;
  const option = (via, label) => `<label class="desk-v1-cfw-option"><input type="radio" name="reading-method" data-reading-via="${via}" ${R.via === via ? 'checked' : ''}><span class="desk-v1-cfw-option-label">${label}</span></label>`;
  const pages = R.via === 'pane' ? R.pages.html(R.needed) : '';
  return `<fieldset class="desk-v1-cfw-group" data-reading-settings><legend class="desk-v1-cfw-dtitle">${w.readingMethod}</legend>
    ${R.account ? `<div class="desk-v1-cfw-fact-text" data-reading-account>${w.account}: ${esc(R.account.label || R.account.identity)}</div>` : ''}
    <p class="desk-v1-cfw-fact-text">${w.readingHelp}</p>${!R.via ? `<p role="alert" data-reading-choice-needed>${w.readChoiceNeeded}</p>` : ''}
    <div class="desk-v1-cfw-options">${option('pane', w.readPane)}${platform === 'x' ? option('api', w.readApp) : ''}</div>
    ${R.via === 'pane' ? `<label class="desk-v1-conn-add-field">${w.browserProfile}<input type="text" class="desk-v1-rules-textinput" data-reading-profile value="${esc(R.profile)}" maxlength="64" ${R.account && c.api.sel.type !== 'signin' ? '' : 'readonly'}></label>` : ''}
    ${R.loading ? `<p role="status">${w.readingLoading}</p>` : ''}${R.error ? `<p role="alert">${w.readingLoadFailed} ${esc(R.error)} <button type="button" class="desk-v1-conn-btn" data-reading-retry>${w.retry}</button></p>` : ''}${pages}</fieldset>`;
}
function bind(root, c) {
  if (!matches(c)) return;
  init(c);
  const sync = () => { root.querySelector('[data-cfw-primary]').disabled = !!problem(c); };
  root.querySelectorAll('[data-reading-via]').forEach(r => r.addEventListener('change', () => { R.via = r.dataset.readingVia; R.pages.clearInput(); c.api.repaint(); }));
  root.querySelector('[data-reading-profile]')?.addEventListener('input', e => { R.profile = e.target.value.trim(); });
  R.pages.bind(root, c.api.repaint, sync);
  root.querySelector('[data-reading-retry]')?.addEventListener('click', () => { load(c); c.api.repaint(); });
}
function draft() {
  if (!R.key) return {};
  const body = {};
  if (R.via && R.via !== R.originalVia) body.read_via = R.via;
  if (R.via === 'pane' && R.profile !== R.originalProfile) body.browser_profile = R.profile;
  if (R.via === 'pane' && R.pages.changed()) body.read_pages = R.pages.list();
  return body;
}
function summary() {
  if (!R.key) return { lines:[], pending:false };
  return { pending:!!Object.keys(draft()).length, lines:[`${C.words.readingMethod}: ${R.via === 'api' ? C.words.readApp : C.words.readPane}`,
    ...(R.via === 'pane' ? [`${C.words.browserProfile}: ${R.profile || C.words.none}`, ...R.pages.summary()] : [])] };
}
async function apply(id) {
  const current = R;
  const body = draft();
  if (!Object.keys(body).length) return { status:'unchanged' };
  try {
    const saved = await R.ctx.api('PATCH', `/api/desk/accounts/${encodeURIComponent(id)}`, { ...body, ...(R.pid ? { project_id:R.pid } : {}) });
    if (current.account) Object.assign(current.account, saved);
    if (R === current) { R.originalVia = R.via; R.originalProfile = R.profile; R.pages.accept(); }
    return { status:'saved' };
  } catch (e) { return { status:'failed', error:e.message || C.words.notSaved }; }
}
P?.registerSection({ matches, html, bind, problem, summary, apply,
  discard:() => { seq++; R = fresh(); },
  outcome:r => r.status === 'saved' ? C.words.readingSaved : r.status === 'failed' ? `${C.words.readingFailed}: ${r.error}` : '',
});

// Existing accounts have a connection already. This registered branch saves only
// their wishes through Permissions.apply; it neither signs in nor claims a check.
window.DeskV1ConnectSummaryStep?.registerBranch({ id:'saved-account', word:C.words.currentConnection,
  match:sel => sel.type === 'account', problem:() => '',
  save:async () => ({ ok:true, result:{ account_id:R.account.id, unchanged:true } }),
  accountId:r => r.account_id,
  factsHTML:() => `<dl class="desk-v1-cf-facts"><div><dt>${C.words.account}</dt><dd>${esc(R.account?.label || R.account?.identity)}</dd></div></dl>`,
  result:{ rows:() => [[C.words.connectionLabel, C.words.unchanged, 'connection']], actions:() => [] },
});
W?.registerScreen({ id:'saved-account-setup', step:'setup', match:sel => sel.type === 'account',
  title:() => C.screens.setup[0], copy:() => C.screens.setup[1],
  body:() => `<p>${esc(R.account?.label || R.account?.identity)}</p><p>${C.words.currentConnection}</p>`,
  primary:() => ({ label:C.words.continue, run:() => true }),
});
