// Package Result owns failure/success copy; a failed setup grants no saved-state notes.
export function packageResultTitle(result, words) {
  return {registered:words.packageRegistered, pending_runtime:words.packagePending,
    setup_failed:words.notSaved}[(result || {}).state] || words.saved;
}
export function packageResultHTML(result, words, esc) {
  const r = result || {};
  const failed = r.state === 'setup_failed';
  const title = packageResultTitle(r, words);
  const message = r.message || (failed ? words.packageSaveFailed : '');
  return `<div class="desk-v1-cfw-msg" data-pk-result="${esc(r.state || '')}" role="status"><strong>${esc(title)}</strong>: ${esc(message)}</div>
    ${!failed && r.notice ? `<div class="desk-v1-cfw-msg" data-pk-notice role="status">${esc(r.notice)}</div>` : ''}
    ${failed ? '' : `<div class="desk-v1-cfw-fact-text" data-pk-start-note>${esc(words.packageStartNote)}</div>`}`;
}
