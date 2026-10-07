// The public vocabulary of every Connect screen (MC-1062/14, Ron 2026-10-06).
// Screen bodies retain their safety facts; technical names belong in Details.
let enabled = false;
const screens = Object.freeze({
  service: ['Add service', 'Enter the service name or web address.'],
  connection: ['Connection options', 'Choose how you want to connect the service.'],
  setup: ['Connection details', 'Choose an account or enter the details for your chosen connection.'],
  permissions: ['Permissions', 'Choose what agents may do with this connection.'],
  review: ['Review', 'Check the details, then save with your passcode.'],
  result: ['Result', 'Read the status below and finish any remaining sign-in.'],
});
const words = Object.freeze({ continue: 'Continue', back: 'Back', save: 'Save', done: 'Done',
  service: 'Service name or web address', signin: 'Sign in', api: 'Access key or app',
  mcp: 'Connection software', reference: 'Save information only', details: 'Details',
  investigating: 'Finding connection options…', suggestions: 'Suggestions',
  incomplete: 'Some connection options could not be checked.', failed: 'Connection options could not be checked.',
  empty: 'No connection options were found.', referenceOnly: 'Information only; this does not connect the service.',
  noPermission: 'This connection offers no separate Read or Post permission.',
  account: 'Account', identity: 'Account name or address', member: 'Personal profile', organization: 'Company Page',
  newAccount: 'Add another account', existingAccount: 'Choose an account',
  package: 'Software package', remote: 'Server address', username: 'Username or email', password: 'Password',
  keyId: 'Access key ID', keySecret: 'Access key secret', key: 'Access key', clientId: 'App ID', clientSecret: 'App secret',
  read: 'Read', post: 'Post', check: 'Check it now', open: 'Open sign-in', retry: 'Try again',
  change: 'Change connection', checking: 'Checking…', checkFailed: 'Check failed',
  label: 'Display name', address: 'Web address', savedLogin: 'Saved sign-in', browserProfile: 'Browser profile',
  credentialName: 'Saved credential name', name: 'Service name', source: 'Information address',
  cancel: 'Cancel', refresh: 'Refresh status', detect: 'Find the required details', remove: 'Remove',
  newLogin: 'Username and password', manualLogin: 'Sign in yourself',
  preview: 'Enable live connections in Settings to add a service.',
  informationOnly: 'Information only', choose: 'Choose', authorize: 'Open sign-in',
  credential: 'Saved credential', secretValue: 'Credential value', credentialUser: 'Username or key ID',
  noApp: 'No app details are needed for this choice.',
  changeDetails: 'Change details', fill: 'Use saved sign-in', reopen: 'Open sign-in',
  previous: 'Back', scope: 'Who can use this connection', find: 'Find the required details',
  noCredential: 'No credential', newCredential: 'New credential', credentialKind: 'Credential kind',
  authentication: 'Sign-in kind', credentialNames: 'Required credential names', placements: 'Where credentials are used',
  permissionsList: 'Allowed actions', unattended: 'Allow use while you are away',
  loading: 'Loading saved information…', saved: 'Saved', notSaved: 'Not saved', notChecked: 'Not checked',
  unchanged: 'Unchanged', connected: 'Connected', notConnected: 'Not connected',
  statusLabel: 'Status', connectionLabel: 'Connection', signinLabel: 'Sign-in',
  exampleAddress: 'Example: https://example.com', next:'Next', previousPage:'Previous',
  addCredential:'Add a credential', packageFile:'Start file in the package', arguments:'Arguments, one per line',
  approve:'Approve this connection', noPermissions:'No permissions are allowed.',
  referenceStatus:'Information only; not connected or verified', none:'None',
});
const fields = Object.freeze({ key_id:'keyId', key_secret:'keySecret', key:'key', api_key:'key',
  client_id:'clientId', client_secret:'clientSecret', identity:'identity', label:'label',
  name:'name', source:'source', address:'address', url:'address', vaultName:'credentialName',
  credential_names:'credentialNames', placements:'placements', scopes:'permissionsList',
  mode:'credential', entry:'credentialKind', auth_type:'authentication',
});
const replacements = [
  [/oauth\.[\w.-]+/gi, 'saved sign-in'], [/\bOAuth\b/gi, 'app sign-in'], [/\bMCP\b/gi, 'connection software'],
  [/\bvault\b/gi, 'saved credentials'], [/\bSecrets\b/g, 'saved credentials'], [/\btransport\b/gi, 'connection'],
  [/\broutes?\b/gi, 'connection'], [/\bheld\b/gi, 'waiting'], [/\bU[12]\b/g, 'connection'],
  [/\bOpenAPI\b/g, 'Public specification'], [/\bAPI\b/g, 'app'], [/\bPyPI\b/g, 'Software package'],
];
// Every adapter uses these exact words for the same control. Selectors identify
// purpose; no display string supplied by a service chooses the public label.
const buttonWords = Object.freeze({
  '[data-lg-recheck], [data-api-retry], [data-ref-retry-vault]':'retry',
  '[data-lg-open], [data-sum-open], [data-cfh-go], [data-cfh-open], [data-cs-open]':'open', '[data-cfh-start]':'signin',
  '[data-api-back-fields]':'changeDetails', '[data-ref-detect]':'detect',
  '[data-ref-stop-detect], [data-cfh-cancel]':'cancel', '[data-ref-alt]':'changeDetails',
  '[data-ref-prev]':'back', '[data-sum-act="check"]':'check', '[data-sum-act="refresh"]':'refresh',
  '[data-cs-fillbtn], [data-signin-fill]':'fill', '[data-sum-act="retry-permissions"]':'retry',
  '[data-pk-detect]':'detect', '[data-pk-change], [data-rs-change]':'changeDetails',
  '[data-pk-cred-add], [data-rs-cred-add]':'addCredential', '[data-pk-cred-remove], [data-rs-cred-remove]':'remove',
  '[data-rs-check-run]':'check', '[data-rs-more]':'details', '[data-sum-act="signin"]':'open',
});
const publicPhrases = Object.freeze({
  'No connection permission is granted.':words.noPermissions,
  'No permissions are allowed.':words.noPermissions,
  'Reference only; not connected or verified':words.referenceStatus,
  'Not checked':words.notChecked, 'None':words.none, 'Saved':words.saved,
  'Connected':words.connected, 'Not connected':words.notConnected,
});
function plain(text) {
  const original=String(text), phrase=publicPhrases[original.trim()];
  if(phrase) return phrase;
  return replacements.reduce((s, [from, to]) => s.replace(from, to), original);
}
const factWords = Object.freeze({Service:'name',Name:'name',Address:'address',Status:'statusLabel',
  Connection:'connectionLabel',Account:'account',Login:'signinLabel','Sign-in login':'signinLabel',
  'Browser profile':'browserProfile',Credential:'credential',Permissions:'permissionsList',
  'API key ID':'keyId','API key secret':'keySecret','API key':'key','Key ID':'keyId',
  'app key ID':'keyId','app key secret':'keySecret','App ID':'clientId','Client ID':'clientId','Client Secret':'clientSecret'});
// Only text nodes are translated. Input values, option values, selectors, IDs,
// requests and authorization checks are never changed by copy presentation.
function body(html, api) {
  const t = document.createElement('template'); t.innerHTML = html;
  const connection=t.content.querySelector('[data-sum-row="connection"] dd');
  if(connection && api) {
    const variant=(api.info?.picker || []).flatMap(type=>type.variants).find(v=>v.id===api.sel.variant);
    connection.textContent=api.sel.type==='signin' || variant?.setup?.signs_in ? words.signin : words.api;
  }
  const walker = document.createTreeWalker(t.content, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const n = walker.currentNode;
    if (!n.parentElement?.closest('details, code, [data-cfa-r]')) n.textContent = plain(n.textContent);
  }
  return t.innerHTML;
}
function screen(def) {
  const copy = screens[def.step];
  if (!copy) return def;
  return { ...def, title: api => enabled ? copy[0] : def.title(api), copy: api => enabled ? copy[1] : def.copy(api),
    body: api => enabled ? body(def.body ? def.body(api) : '', def.step==='review'?api:undefined) : (def.body ? def.body(api) : ''),
    primary: def.primary ? (api) => { const p = def.primary(api); return enabled && p ? { ...p, label: def.step === 'review' && /^sav/i.test(p.label) ? words.save : def.step === 'result' ? words.done : words.continue } : p; } : undefined,
  };
}
function bind(root) {
  if (!enabled) return;
  // Exact commands, saved names and review values are evidence, not vocabulary.
  // Keep them verbatim in the one technical disclosure rather than translating
  // identifiers in an approval card into a different executable/configuration.
  const technical = new Set([...root.querySelectorAll('code, [data-cfa-r]')]
    .filter(n=>!n.closest('[data-cfw-details]'))
    .map(n=>n.closest('.desk-v1-cfw-fact, [data-cfa-present], dl > div') || n));
  if (technical.size) {
    let details=root.querySelector('[data-cfw-details]');
    if (!details) {
      details=document.createElement('details');details.className='desk-v1-cfw-details';details.dataset.cfwDetails='';
      const summary=document.createElement('summary');summary.textContent=words.details;details.append(summary);
      root.querySelector('[data-cfw-actions]').before(details);
    }
    for (const node of technical) details.append(node);
  }
  // Adapters attach their retained input nodes during bind, after body rendering.
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const n = walker.currentNode;
    if (!n.parentElement?.closest('[data-cfw-details], input, textarea, option, code, [data-cfa-r]')) n.textContent = plain(n.textContent);
  }
  root.querySelectorAll('label').forEach(label => {
    if (label.closest('[data-cfw-details]')) return;
    const input = label.querySelector('input,select,textarea');
    if (!input) return;
    const key = input.dataset.cfaField || input.dataset.refField || input.dataset.refSelect;
    let word = fields[key];
    if (input.matches('[data-cfw-input]')) word='service';
    if (input.matches('[data-lg-user]')) word='username';
    if (input.matches('[data-lg-pass]')) word='password';
    if (input.matches('[data-lg-profile], [data-lg-newprofile]')) word='browserProfile';
    if (input.matches('[data-lg-vaultname]')) word='credentialName';
    if (input.matches('[data-lg-label]')) word='label';
    if (input.matches('[data-lg-login], [data-api-pick]')) word='savedLogin';
    if (input.matches('[data-ref-user]')) word='credentialUser';
    if (input.matches('[data-ref-secret]')) word='secretValue';
    if (input.matches('[data-pk-package]')) word='package';
    if (input.matches('[data-pk-entry]')) word='packageFile';
    if (input.matches('[data-pk-args]')) word='arguments';
    if (input.matches('[data-rs-url], [data-rs-issuer]')) word='address';
    if (input.matches('[data-rs-name], [data-pk-name]')) word='label';
    if (input.matches('[data-rs-scopes]')) word='permissionsList';
    if (!word) return;
    const text = [...label.childNodes].find(n=>n.nodeType===Node.TEXT_NODE && n.textContent.trim());
    if (text) text.textContent=words[word];
  });
  for (const [selector, word] of Object.entries(buttonWords)) root.querySelectorAll(selector).forEach(button => {
    if (!button.closest('[data-cfw-details]')) button.textContent=words[word];
  });
  root.querySelector('[data-cfw-back]')?.replaceChildren(document.createTextNode(words.back));
  root.querySelector('[data-cfw-details] > summary')?.replaceChildren(document.createTextNode(words.details));
  root.querySelector('[data-cfw-input]')?.setAttribute('placeholder',words.exampleAddress);
  root.querySelectorAll('dt, .desk-v1-cfw-fact-head').forEach(label => {
    if (label.closest('[data-cfw-details]')) return;
    const word=factWords[label.textContent.trim()];if(word) label.textContent=words[word];
  });
  const connection=root.querySelector('[data-sum-row="connection"] dd');
  if(connection && root.dataset.cfwStep==='review') connection.textContent=/sign/i.test(connection.textContent)?words.signin:words.api;
  root.querySelectorAll('[data-sum-status]').forEach(n=>{n.textContent=connectionStatus(n.dataset.sumStatus);});
  root.querySelectorAll('[data-cfw-page]').forEach(b=>{b.textContent=b.dataset.cfwPage==='next'?words.next:words.previousPage;});
  root.querySelectorAll('[data-ref-select="mode"] option').forEach(option => {
    const word={none:'noCredential',existing:'credential',new:'newCredential'}[option.value];
    if(word) option.textContent=words[word];
  });
  root.querySelectorAll('[data-lg-mode]').forEach(input => {
    const label=input.closest('label')?.querySelector('.desk-v1-cfw-option-label');
    const word={new:'newLogin',saved:'savedLogin',manual:'manualLogin'}[input.value];
    if(label&&word) label.textContent=words[word];
  });
}
const statuses = Object.freeze({verified:'Verified',key_stored:'Key stored; not checked',signed_in:'Signed in; not checked',
  sign_in_required:'Sign-in required',key_unreadable:'Saved key cannot be read',vault_locked:'Saved credentials are locked',
  check_failed:'Check failed',registered:'Registered; not checked',setup_failed:'Saved; setup failed',not_connected:'Not connected'});
const connectionStatus = value => statuses[value] || words.checkFailed;
export const ConnectCopy = Object.freeze({ screens, words, plain, body, screen, bind, status: connectionStatus, enable: () => { enabled = true; } });
window.DeskV1ConnectCopy = ConnectCopy;
