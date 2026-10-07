// Label + one plain sentence for every row on the Connection options page
// (MC-1062/14; Ron 2026-10-07: two rows both read "Sign in" with nothing under them).
// One shared set for every service: only the service name differs. The row is
// classified from what the connection does for the person, never from a title the
// service ships, so two rows of the same type still read differently.
import { ConnectCopy as C } from './desk-v1-connect-copy.js';

const rows = Object.freeze({
  browser:     { label: 'Sign in on the website',   sentence: s => `Sign in on the ${s} website in Clayrune's browser.` },
  account:     { label: 'Sign in with your account', sentence: s => `Approve access with your ${s} account.` },
  key:         { label: C.words.api,                sentence: s => `Enter an access key from your ${s} account.` },
  software:    { label: C.words.mcp,                sentence: s => `Add software that connects Clayrune to ${s}.` },
  information: { label: C.words.reference,         sentence: s => `Keep notes about ${s}. This does not connect it.` },
});
// Discovered suggestions are evidence only: each says what it would be, and that it connects nothing.
const suggestions = Object.freeze({
  signin: s => `Keep notes on how to sign in to ${s}. This does not connect it.`,
  api:    s => `Keep notes on how to get an access key for ${s}. This does not connect it.`,
  mcp:    s => `Keep notes on connection software for ${s}. This does not connect it.`,
});

// type: picker type id (signin | api | mcp | reference); signsIn: the variant's setup.signs_in.
export function kindOf(type, signsIn) {
  if (type === 'reference') return 'information';
  if (type === 'signin') return 'browser';
  if (signsIn) return 'account';
  return type === 'mcp' ? 'software' : 'key';
}
export function optionCopy(kind, service) {
  const row = rows[kind] || rows.information;
  return { label: row.label, sentence: row.sentence(service) };
}
export function suggestionCopy(family, service) {
  return { label: `${C.words[family]} — ${C.words.informationOnly}`, sentence: suggestions[family](service) };
}
