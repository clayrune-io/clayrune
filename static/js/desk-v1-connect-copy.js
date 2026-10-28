import { ConnectionWords as CW } from './desk-v1-connection-words.js';
export { ConnectionWords } from './desk-v1-connection-words.js';
// The public vocabulary of every Connect screen (MC-1062/14, Ron 2026-10-06).
// Screen bodies retain their safety facts; technical names belong in Details.
let enabled = false;
const screens = Object.freeze({
  service: ['Add service', 'Enter the service name or web address.'],
  connection: ['Connection options', 'Choose how you want to connect the service.'],
  setup: ['Connection details', 'Choose an account or enter the details for your chosen connection.'],
  permissions: ['Permissions', 'Choose what agents may do with this connection.'],
  review: ['Review', 'Check the details, then save.'],
  result: ['Result', 'Read the connection status below.'],
});
const words = Object.freeze({ continue: 'Continue', back: 'Back', save: 'Save', done: 'Done',
  packageStaging: 'Reading the repository and checking its files; this can take a minute.',
  packageDetecting: 'Reading the package listing to find the required details.',
  packageBuild: 'This package needs building: in Review, select its dependency and build steps before saving.',
  packageBuildRequired: 'Select the required build step in Review before saving.',
  packageRequired: 'Required before saving.',
  packageSaving: 'Setting up and saving the connection; this can take a few minutes.',
  packageSaveFailed: 'The connection was not saved because setup failed.',
  packageResult: 'Read the connection status below.',
  packageRegistered: CW.status.unchecked, packagePending: CW.status.broken,
  packageStartNote: 'Saving does not start the server or check that it works. It starts the first time an agent session uses it.',
  packageWorking: CW.status.ready, packageNotWorking: CW.status.broken,
  packageCheckedNote: 'Clayrune started the server once to check it, then stopped it. Agents start it again when they use it.',
  packageCheckAgain: CW.buttons.check, packageChecking: 'Starting the server to check it…',
  credentialsOptional: 'The instructions also mention these, as optional extras:',
  credentialsTitle: 'Credentials, by name only', credentialsTitleOptional: 'Credentials, by name only (optional)',
  commandHelp: 'This is the program Clayrune starts; keep the suggestion unless the package instructions say otherwise.',
  packageFileHelp: 'Leave this empty to use the package’s default start file, or enter a file path inside it.',
  argumentsHelp: 'Keep any suggested file path and put each extra option on a separate line, without secret values.',
  variableLabel: 'Required name',
  credentialPickHelp: 'Choose a saved credential, or add a new one.',
  credentialValueHelp: 'Paste the value. It is saved securely and not shown again.',
  useSaved: 'Use a saved credential',
  credentialIncomplete: 'Choose a saved credential or paste a value for each credential, or remove the row.',
  credentialsLoadFailed: 'Saved credentials could not be listed.',
  credentialSaveTitle: 'Save this credential', credentialSaveProof: 'Re-enter your dashboard passcode to save this credential securely on this computer.',
  credentialSaving: 'Saving the credential…', credentialSaveFailed: 'The credential could not be saved.',
  packageNameHelp: 'Choose a short display name or leave this empty to use the package name.',
  packageHelp: 'Help me fill this in',
  packageHelpIntro: 'Ask about this package; review any suggested values before using them.',
  packageQuestion: 'Your question', packageQuestionHelp: 'Ask what a field means or what this package needs, without sharing secret values.',
  packageAsk: 'Ask', packageHelpBusy: 'Reading the package information to answer your question.',
  packageDefaultQuestion: 'Explain what this package needs, what each setup field means, and suggest values with reasons.',
  packageUseSuggestions: 'Use suggestions for empty fields',
  packageHelpApplied: 'Suggestions were added to empty fields; review them before continuing.',
  packageHelpFailed: 'Setup help could not answer; no fields changed.',
  service: 'Service name or web address', signin: 'Sign in', api: 'Access key or app',
  mcp: 'Connection software', reference: 'Save information only', details: 'Details',
  investigating: 'Finding connection options…', suggestions: 'Suggestions',
  incomplete: 'Some connection options could not be checked.', failed: 'Connection options could not be checked.',
  empty: 'No connection options were found.', referenceOnly: 'Information only; this does not connect the service.',
  noPermission: 'This connection offers no separate Read or Post permission.',
  account: 'Account', identity: 'Account name or address', member: 'Personal profile', organization: 'Company Page',
  newAccount: 'Add another account', addNew: 'Add a new credential', packageHint: 'Enter a package name or a GitHub web address.', command: 'Start command', commandMissing: 'No start command was found. Enter the command to run.', existingAccount: 'Choose an account',
  package: 'Software package', remote: 'Server address', username: 'Username or email', password: 'Password',
  keyId: 'Access key ID', keySecret: 'Access key secret', key: 'Access key', clientId: 'App ID', clientSecret: 'App secret',
  read: CW.labels.read, post: CW.labels.post, check: CW.buttons.check, open: 'Open sign-in', retry: 'Try again',
  change: CW.buttons.edit, checking: CW.buttons.checking, checkFailed: CW.status.broken,
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
  loading: 'Loading saved information…', saved: 'Saved', notSaved: 'Not saved', notChecked: CW.status.unchecked,
  unchanged: 'Unchanged', connected: CW.status.ready, notConnected: CW.status.unchecked,
  statusLabel: 'Status', connectionLabel: 'Connection', signinLabel: 'Sign-in',
  exampleAddress: 'Example: https://example.com', next:'Next', previousPage:'Previous',
  addCredential:'Add a credential', packageFile:'Start file in the package', arguments:'Arguments, one per line',
  approve:'Approve this connection', noPermissions:'No permissions are allowed.',
  referenceStatus:'Information only; not connected or verified', none:'None',
  selected:'Selected', credentialChoice:'How to provide a credential',
  assistedFillOn:'Fill in on', assistedWorking:'Filling in the post in your browser pane…',
  assistedProofTitle:'Fill in the post',
  assistedProofText:'Re-enter your dashboard passcode to fill in this post in your signed-in browser pane. Clayrune stops before the final button: you press it yourself.',
  assistedNoPasscode:'The dashboard passcode was not entered, so nothing was done.',
  assistedDone:'The post is filled in. Check it in the pane, then press the final button yourself.',
  assistedNoFinal:'The final button was not found on the screen: check the pane before you go on.',
  assistedFailed:'The post could not be filled in.',
  assistedStatus:'Assisted posting', assistedLine:'Clayrune fills in the post in your signed-in browser pane and stops. You press the final button yourself.',
  assistedEnable:'Allow assisted posting', assistedEnabling:'Saving…',
  assistedEnableProofTitle:'Allow assisted posting',
  assistedEnableProofText:'Re-enter your dashboard passcode to let the Desk fill in approved posts for this account. It never presses the final button.',
  assistedEnableFailed:'The account was not changed.',
  readingMethod:'How to read this account', readingHelp:'Choose how agents read this account.',
  readPane:'Browser sign-in (no charge)', readApp:'Platform app (may cost money)',
  readChoiceNeeded:'The saved reading method is unavailable. Choose how to read this account.',
  activityPages:'Activity pages', activityHelp:'Enter the web address of the page that lists activity on this account.',
  addAddress:'Add address', httpsRequired:'The address must start with https://',
  readingSaved:'Reading settings are saved.', readingFailed:'Reading settings were not saved',
  currentConnection:'Current connection', readingLoading:'Checking which activity pages are needed…',
  permissionsUnchanged:'Existing connection permissions stay as saved.',
  readingLoadFailed:'Activity pages could not be checked.',
  addressPending:'Add the address before continuing.',
  nameRequired:'Enter the service name.', handleRequired:'Enter the account handle, for example @yourname.',
  accountHandle:'Account handle', accountAdded:'Added as an account', accountNoPost:'This adds an account to the Desk. Nothing is posted: Clayrune never presses a final Post or Share button.',
  attachAccountHelp:'Choose the account this saved sign-in belongs to, or enter its handle.',
  profileRequired:'Enter a browser profile name using lowercase letters, digits, ., - or _ (no ..).',
  manualHelp:'Sign in in the browser pane. Clayrune keeps the browser session.',
  browserNoExecution:'Read and Post are off. This browser sign-in grants no permission to run actions.',
  saveConnection:'Save connection',saveProof:'Re-enter your dashboard passcode to save this connection.',
  checkConnection:CW.buttons.check,noAutoCheck:'Clayrune has no automatic sign-in check for this service.',
  signedInQuestion:'Are you signed in now?',yesSignedIn:"Yes, I’m signed in",notYet:'Not yet',
  paneUnavailable:'The browser pane could not be opened. Try again.',
  browserProof:'Browser sign-in works. Read and Post keep their own permissions.',
  readingSave:'Save the chosen reading settings.', readingWillSave:'Reading changes are applied when you save.',
});
const fields = Object.freeze({ key_id:'keyId', key_secret:'keySecret', key:'key', api_key:'key',
  client_id:'clientId', client_secret:'clientSecret', identity:'identity', label:'label',
  name:'name', source:'source', address:'address', url:'address', vaultName:'credentialName',
  credential_names:'credentialNames', placements:'placements', scopes:'permissionsList',
  mode:'credentialChoice', entry:'credentialKind', auth_type:'authentication',
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
// Vocabulary is opt-in for UI copy. A text node can be a URL, package, saved
// name or command just as easily as an input value: never rewrite arbitrary
// rendered text. Adapters can mark fixed prose with data-connect-copy.
function isCopy(node) {
  const parent = node.parentElement;
  if (!parent || parent.closest('details, [data-cfw-details], input, textarea, option, code, pre, dd, a, [data-connect-value], [data-cfa-r], [data-open-vault-unlock]')) return false;
  return parent.matches('label, button, dt, .desk-v1-cfw-fact-head, [data-cfw-copy], [data-connect-copy]');
}
function body(html, api) {
  const t = document.createElement('template'); t.innerHTML = html;
  const connection=t.content.querySelector('[data-sum-row="connection"] dd');
  if(connection && api) {
    const variant=(api.info?.picker || []).flatMap(type=>type.variants).find(v=>v.id===api.sel.variant);
    connection.textContent=api.sel.type==='account' ? words.currentConnection : api.sel.type==='signin' || variant?.setup?.signs_in ? words.signin : words.api;
  }
  const walker = document.createTreeWalker(t.content, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const n = walker.currentNode;
    if (isCopy(n)) n.textContent = plain(n.textContent);
  }
  return t.innerHTML;
}
function screen(def) {
  const copy = screens[def.step];
  if (!copy) return def;
  return { ...def, title: api => enabled ? (def.step === 'result' && def.resultState?.(api) === 'setup_failed' ? words.notSaved : copy[0]) : def.title(api), copy: api => enabled ? copy[1] : def.copy(api),
    body: api => enabled ? body(def.body ? def.body(api) : '', def.step==='review'?api:undefined) : (def.body ? def.body(api) : ''),
    primary: def.primary ? (api) => { const p = def.primary(api); return enabled && p ? { ...p, label: /^(Checking|Reading|Saving)/.test(p.label) ? p.label : def.step === 'review' && /^sav/i.test(p.label) ? words.save : def.step === 'result' ? words.done : words.continue } : p; } : undefined,
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
    if (isCopy(n)) n.textContent = plain(n.textContent);
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
    if (input.matches('[data-ref-user]')) word=root.querySelector('[data-ref-select="entry"]')?.value==='login'?'username':'credentialUser';
    if (input.matches('[data-ref-secret]')) word=root.querySelector('[data-ref-select="entry"]')?.value==='login'?'password':'secretValue';
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
    if (!button.closest('[data-cfw-details]') && button.getAttribute('aria-busy') !== 'true') button.textContent=words[word];
  });
  root.querySelector('[data-cfw-back]')?.replaceChildren(document.createTextNode(words.back));
  root.querySelector('[data-cfw-details] > summary')?.replaceChildren(document.createTextNode(words.details));
  root.querySelector('[data-cfw-input]')?.setAttribute('placeholder',words.exampleAddress);
  root.querySelectorAll('dt, .desk-v1-cfw-fact-head').forEach(label => {
    if (label.closest('[data-cfw-details]')) return;
    const word=factWords[label.textContent.trim()];if(word) label.textContent=words[word];
  });
  const connection=root.querySelector('[data-sum-row="connection"] dd');
  if(connection && root.dataset.cfwStep==='review' && connection.textContent!==words.currentConnection) connection.textContent=/sign/i.test(connection.textContent)?words.signin:words.api;
  root.querySelectorAll('[data-sum-status]').forEach(n=>{n.textContent=connectionStatus(n.dataset.sumStatus);});
  root.querySelectorAll('[data-cfw-page]').forEach(b=>{b.textContent=b.dataset.cfwPage==='next'?words.next:words.previousPage;});
  root.querySelectorAll('[data-ref-select="mode"] option').forEach(option => {
    const word={none:'noCredential',existing:'credential',new:'newCredential'}[option.value];
    if(word) option.textContent=words[word];
  });
  root.querySelectorAll('[data-lg-mode]').forEach(input => {
    const label=input.closest('label')?.querySelector('.desk-v1-cfw-option-label');
    const word={new:'newAccount',saved:'savedLogin',manual:'manualLogin'}[input.value];
    if(label&&word) label.textContent=words[word];
  });
}
const connectionStatus = value => CW.from(value).word;
export const ConnectCopy = Object.freeze({ screens, words, plain, body, screen, bind, status: connectionStatus, isEnabled: () => enabled, enable: () => { enabled = true; } });
window.DeskV1ConnectCopy = ConnectCopy;
