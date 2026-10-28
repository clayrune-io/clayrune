// Public vocabulary for saved connections and Connect. Keys describe evidence,
// never permission grants: those stay with their existing server owners.
const status = Object.freeze({ready:'Ready', signin:'Needs sign-in', unchecked:'Not checked yet', broken:'Not working'});
const buttons = Object.freeze({check:'Check now', edit:'Edit', signin:'Sign in again', remove:'Remove', checking:'Checking…', saveLimit:'Save limit'});
const labels = Object.freeze({permissions:'Permissions', read:'Read', post:'Post', on:'On', off:'Off', details:'Details', browser:'Browser', api:'App', profile:'Browser profile', method:'Connection method', scope:'Scopes'});
const next = Object.freeze({ready:'No action is needed.', signin:'Sign in again, then check now.', unchecked:'Check now to see whether this connection works.', broken:'Open Edit to review this connection.'});
const reasons = Object.freeze({
  readOff:'Read is off. Open Edit and turn on Read in Permissions.',
  postOff:'Post is off. Open Edit and turn on Post in Permissions.',
  unavailable:'This connection cannot do this action.',
  noAccount:'Read and Post are unavailable for this saved sign-in.',
  loading:'The saved permissions are being read.',
  failed:'The saved permissions could not be read. Open Edit to review them.',
  legacy:'No choice is recorded; the account keeps its existing behavior.',
  locked:'Open Details to unlock your saved credentials, then check now.',
  reference:'This is saved information; no connection is set up or checked.',
  noCheck:'This connection has no automatic check; open Edit to review it.',
});
const keys = Object.freeze({ready:'ok', signin:'reauth', unchecked:'saved', broken:'off', waiting:'saved'});
function state(kind, service) {
  const word = kind === 'waiting' ? `Waiting on ${service}` : status[kind];
  if (!word) throw new Error(`Unknown connection status: ${kind}`);
  return {key:keys[kind], word, kind, action:null};
}
const states = Object.freeze({
  verified:'ready', ready:'ready', working:'ready', ok:'ready', unchanged:'ready', adopted:'ready', baseline_recorded:'ready',
  sign_in_required:'signin', needs_signin:'signin',
  key_stored:'unchecked', signed_in:'unchecked', not_checked:'unchecked', never_checked:'unchecked', registered:'unchecked', pending_runtime:'broken', partial:'unchecked',
  check_failed:'broken', failed:'broken', key_unreadable:'broken', vault_locked:'broken', setup_failed:'broken', not_connected:'broken',
  changed:'broken', package_missing:'broken', credential_missing:'broken', missing:'broken', unknown:'unchecked',
});
function from(value) { return state(states[value] || 'unchecked'); }
function serviceName(id) {return {x:'X',linkedin:'LinkedIn',youtube:'YouTube',instagram:'Instagram'}[id] || id;}
const waiting = service => `Wait for ${service} to finish reviewing this connection.`;
export const ConnectionWords = Object.freeze({status, buttons, labels, next, reasons, state, from, serviceName, waiting});
