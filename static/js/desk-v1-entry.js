// Desk v1 entry (MC-1205). The ONE module that pulls the whole Desk family in:
// its scripts and its stylesheets. A new Desk module or stylesheet is added to
// the lists below, NOT to static/index.html.
//
// WHY. index.html used to carry 134 script tags and 84 stylesheet links for the
// Desk, so every dashboard load fetched, parsed and ran the Desk whether or not
// it was ever opened (docs/_review/2026-11-16_04_frontend.md, FE21). desk-load.js
// (a boot-time module) now imports this file once the dashboard has booted and
// the browser is idle; openDesk() awaits that same single load (desk-open.js).
//
// ORDER IS THE OLD TAG ORDER. A module script tag evaluated in document order,
// and several modules read a global another one publishes at the moment they run
// (the shell reads DeskV1Kit.commandBus), so MODULES is imported strictly in
// order and the list must not be sorted. The files are fetched in parallel first
// (modulepreload), so the order costs no waterfall.
//
// THE VERSION TOKEN. The server stamps `?v=<asset version>` onto the src/href of
// every /static tag in index.html (server.py `index()`), and only there: it
// cannot see a URL built in JavaScript, and a relative `import './x.js'` drops
// the importer's query. So this module reads the token off ITS OWN url (which
// desk-load.js built from its own stamped tag) and appends it to every script
// and stylesheet it requests. Without that the files would be requested bare,
// which the server answers `no-cache` (never stale, but a revalidation round
// trip for each of ~220 files on every open) instead of `immutable`.
//
// ONE URL PER MODULE (FE22). A module that is both loaded here and statically
// imported elsewhere runs twice, once per URL. The modules in BARE are also
// imported by a bare relative path from outside this list, so they are loaded
// by that same bare URL: one URL, one instance. tests/test_desk_entry.py pins
// both halves of that.

const MODULES = [
  'desk-v1-store', 'desk-v1-store-unreadable', 'desk-v1-kit',
  'desk-v1-product-materials', 'desk-v1-shell', 'desk-v1-home',
  'desk-v1-project', 'desk-v1-vault-gate', 'desk-v1-guides',
  'desk-v1-engines', 'desk-v1-service-icons', 'desk-v1-connections-tiles',
  'desk-v1-services', 'desk-v1-add-service', 'desk-v1-connect-suggest',
  'desk-v1-connect-saved-fields', 'desk-v1-connect-adapter', 'desk-v1-connect-install',
  'desk-v1-connect-result', 'desk-v1-connect-discover', 'desk-v1-connect-held',
  'desk-v1-connect-signin', 'desk-v1-connect-purpose', 'desk-v1-connect-custom-deps',
  'desk-v1-connect-custom', 'desk-v1-connect-remote', 'desk-v1-connect-agent-read',
  'desk-v1-connect-browser-permission', 'desk-v1-connect-read-pages', 'desk-v1-connect-wizard',
  'desk-v1-connect-wizard-pick', 'desk-v1-connect-vault-picker', 'desk-v1-connect-account-step',
  'desk-v1-connect-login-step', 'desk-v1-connect-browser-check', 'desk-v1-connect-permissions-step',
  'desk-v1-connect-github', 'desk-v1-connect-package-step', 'desk-v1-connect-signin-reuse',
  'desk-v1-connect-api-step', 'desk-v1-connect-remote-step', 'desk-v1-connect-summary-step',
  'desk-v1-connect-browser-profile', 'desk-v1-browser-service', 'desk-v1-browser-account',
  'desk-v1-connect-reading-step', 'desk-v1-connect-unknown-step', 'desk-v1-connect-options-step',
  'desk-v1-connect-flow', 'desk-v1-assisted-post', 'desk-v1-post-permission',
  'desk-v1-campaign-ended', 'desk-v1-reads-off', 'desk-v1-connection-status',
  'desk-v1-account-signin', 'desk-v1-status-signin', 'desk-v1-connect-saved-check',
  'desk-v1-connections', 'desk-v1-connect-integration', 'desk-v1-setup',
  'desk-v1-reapproval', 'desk-v1-campaign', 'desk-v1-campaign-dispose',
  'desk-v1-post-delete', 'desk-v1-piece-copy', 'desk-v1-piece-title',
  'desk-v1-media-formats', 'desk-v1-local-upload', 'desk-v1-piece-media',
  'desk-v1-piece-versions-empty', 'desk-v1-piece', 'desk-v1-rules',
  'desk-v1-review', 'desk-v1-calendar-preview', 'desk-v1-calendar-unschedule',
  'desk-v1-calendar-detail', 'desk-v1-calendar-tray', 'desk-v1-calendar',
  'desk-v1-how', 'desk-v1-suggestion-pieces', 'desk-v1-piece-lifecycle',
  'desk-v1-article-media', 'desk-v1-where-tiles', 'desk-v1-where-services',
  'desk-v1-where-highlight', 'desk-v1-post-preview-skins', 'desk-v1-post-preview',
  'desk-v1-where', 'desk-v1-what-cards', 'desk-v1-text-import',
  'desk-v1-what-article-import', 'desk-v1-what-post-reuse', 'desk-v1-what-add-modal',
  'desk-v1-piece-names', 'desk-v1-what', 'desk-v1-composer-channels',
  'desk-v1-composer-when', 'desk-v1-composer', 'desk-v1-video',
  'desk-v1-studio-delete', 'desk-v1-library-viewer', 'desk-v1-file-explorer-drop',
  'desk-v1-file-explorer', 'desk-v1-library-folders', 'desk-v1-library-picker',
  'desk-v1-project-media', 'desk-v1-library-refs', 'desk-v1-article-pictures',
  'desk-v1-studio-article', 'desk-v1-article-chat', 'desk-v1-story',
  'desk-v1-story-expand', 'desk-v1-story-resize', 'desk-v1-chat-move',
  'desk-v1-chat-chrome', 'desk-v1-split', 'desk-v1-story-chat',
  'desk-v1-campaign-chat', 'desk-v1-dispatch', 'desk-v1-studio-timebar',
  'desk-v1-studio-timeline', 'desk-v1-studio-insert', 'desk-v1-studio-preview',
  'desk-v1-studio-export', 'desk-v1-studio', 'desk-v1-studio-clips',
  'desk-v1-studio-title', 'desk-v1-video-open', 'desk-v1-studio-capture',
  'desk-v1-conversations', 'desk-v1-engagement', 'desk-v1-results',
  'desk-v1-channel-impact', 'desk-v1-retro',
];

const STYLES = [
  'desk-v1-shell', 'desk-v1-kit',
  'desk-v1-product-materials', 'desk-v1-home',
  'desk-v1-campaign', 'desk-v1-review',
  'desk-v1-calendar', 'desk-v1-video',
  'desk-v1-conversations', 'desk-v1-results',
  'desk-v1-channel-impact', 'desk-v1-map',
  'desk-v1-where', 'desk-v1-reapproval',
  'desk-v1-what', 'desk-v1-what-add-modal',
  'desk-v1-piece-media', 'desk-v1-studio',
  'desk-v1-studio-clips', 'desk-v1-studio-timeline',
  'desk-v1-studio-insert', 'desk-v1-studio-preview',
  'desk-v1-studio-export', 'desk-v1-add-scene',
  'desk-v1-video-open', 'desk-v1-studio-capture',
  'desk-v1-studio-delete', 'desk-v1-studio-article',
  'desk-v1-article-chat', 'desk-v1-library',
  'desk-v1-file-explorer', 'desk-v1-project-media',
  'desk-v1-story', 'desk-v1-story-expand',
  'desk-v1-story-resize', 'desk-v1-chat-chrome',
  'desk-v1-story-chat', 'desk-v1-campaign-chat',
  'desk-v1-dispatch', 'desk-v1-split',
  'desk-v1-mobile-b2', 'desk-v1-what-cards',
  'desk-v1-connections', 'desk-v1-connections-tiles',
  'desk-v1-service-icons', 'desk-v1-where-tiles',
  'desk-v1-post-preview', 'desk-v1-connect-flow',
  'desk-v1-connect-discover', 'desk-v1-connect-adapter',
  'desk-v1-connect-saved-fields', 'desk-v1-connect-install',
  'desk-v1-connect-purpose', 'desk-v1-connect-custom',
  'desk-v1-connect-custom-deps', 'desk-v1-connect-remote',
  'desk-v1-connect-signin', 'desk-v1-connect-held',
  'desk-v1-connect-agent-read', 'desk-v1-connect-read-pages',
  'desk-v1-connect-reading-step', 'desk-v1-connect-site-attach',
  'desk-v1-connect-wizard', 'desk-v1-connect-login-step',
  'desk-v1-connect-permissions-step', 'desk-v1-connect-package-step',
  'desk-v1-connect-package-helper', 'desk-v1-connect-api-step',
  'desk-v1-connect-remote-step', 'desk-v1-connect-summary-step',
  'desk-v1-connection-status', 'desk-v1-connection-account',
  'desk-v1-connect-unknown-step', 'desk-v1-vault-gate',
  'desk-v1-piece-copy', 'desk-v1-piece-title',
  'desk-v1-piece-layout', 'desk-v1-piece-versions-empty',
  'desk-v1-calendar-interactions', 'desk-v1-campaign-content',
  'desk-v1-where-highlight', 'desk-v1-composer',
  'desk-v1-composer-preview', 'desk-v1-composer-media',
];

const BARE = new Set(['desk-v1-post-preview-skins', 'desk-v1-post-preview']);

// Without these the Desk cannot open at all; every other module that fails is
// logged and skipped, exactly as one failing <script> tag used to leave the
// rest of the page running.
const CORE = ['desk-v1-store', 'desk-v1-kit', 'desk-v1-shell', 'desk-v1-home'];

function _param(key) {
  try { return new URL(import.meta.url).searchParams.get(key) || ''; } catch (_) { return ''; }
}
const _token = () => _param('v');

// What an earlier run of this entry already did. A "Try again" imports this file
// under a new URL (`&try=N`): a browser remembers a FAILED module fetch per URL
// and would answer the same URL with the same failure, so the retry has to ask
// for a new one. That makes a second instance of this module, so what has already
// run is kept on `window`, and only the part that failed is requested again.
const _state = (window.__deskV1Entry = window.__deskV1Entry || { done: new Set(), sheets: new Set() });

function _url(kind, name, token, bare) {
  const ext = kind === 'css' ? 'css' : 'js';
  const attempt = _param('try');
  const q = [];
  if (token && !bare) q.push('v=' + encodeURIComponent(token));
  if (attempt) q.push('try=' + encodeURIComponent(attempt));
  return `/static/${kind}/${name}.${ext}` + (q.length ? '?' + q.join('&') : '');
}

const _hinted = new Set();
function _hint(rel, href, as) {
  if (_hinted.has(href)) return null;
  _hinted.add(href);
  const l = document.createElement('link');
  l.rel = rel; l.href = href;
  if (as) l.as = as;
  return l;
}

// Appended at the end of <head>, after every other sheet. Checked 2026-11-17:
// no selector is shared between the Desk sheets and the five non-Desk sheets
// that used to sit among them, bar `.desk-v1-vg`, which the vault popup
// overrides at a higher specificity, so the move cannot change a winner.
function _loadStyles(token) {
  return Promise.all(STYLES.map((name) => new Promise((resolve) => {
    if (_state.sheets.has(name)) { resolve(); return; }
    const href = _url('css', name, token, false);
    const l = _hint('stylesheet', href);
    if (!l) { resolve(); return; }
    // A sheet that fails must not hold the Desk shut: it opens unstyled in that
    // one place, as a failed <link> always did.
    l.onload = () => { _state.sheets.add(name); resolve(); };
    l.onerror = () => { console.warn('[desk] stylesheet failed: ' + href); resolve(); };
    document.head.appendChild(l);
  })));
}

async function _loadModules(token) {
  const urls = MODULES.filter((n) => !_state.done.has(n)).map((n) => ({ n, u: _url('js', n, token, BARE.has(n)) }));
  const frag = document.createDocumentFragment();
  urls.forEach(({ u }) => { const l = _hint('modulepreload', u); if (l) frag.appendChild(l); });
  document.head.appendChild(frag);
  for (const { n, u } of urls) {
    try {
      await import(u);
      _state.done.add(n);
    } catch (e) {
      if (CORE.includes(n)) throw e;
      console.error('[desk] module failed: ' + n, e);
    }
  }
}

// Resolves when the whole family has run. Rejects only when a CORE module could
// not be loaded; desk-load.js turns that into an error with Try again.
export async function loadDesk() {
  const token = _token();
  await Promise.all([_loadStyles(token), _loadModules(token)]);
}

export { MODULES, STYLES, BARE, CORE };
