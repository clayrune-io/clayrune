// One grammar for workflow slot references, shared by every client-side check
// (MC-1179 WB3). The server substitutes a placeholder only when the whole
// thing matches mc/workflows.py::_SLOT_RE, `\{\{\s*([a-zA-Z0-9_.\-]+)\s*\}\}`,
// and reads the step name as the text between the first two dots. So a step
// name can be referenced exactly when it is non-empty and made of letters,
// digits, `_` and `-` (a `.` would split it; anything else, a space included,
// never matches and is left in the prompt verbatim).
//
// Anything that LOOKS like a step reference but does not fit that grammar is
// "malformed": nothing will ever substitute it, so every client check treats it
// as broken instead of ignoring it.

const NAME_CLASS = 'A-Za-z0-9_\\-';

// A step name that can be written inside {{steps.<name>.<field>}}.
export const WF_STEP_NAME_RE = new RegExp(`^[${NAME_CLASS}]+$`);

// A whole step reference (the text between the braces) the server can resolve.
export const WF_STEP_REF_RE = new RegExp(`^steps\\.([${NAME_CLASS}]+)(?:\\.[${NAME_CLASS}.]+)?$`);

// Every {{steps.…}} in a text, well formed or not. Group 1 is the inner text.
export function wfSlotStepRe() { return /\{\{\s*(steps\.[^{}]*?)\s*\}\}/g; }

// Every {{ref}} the chip legend should show: a server-grammar reference, or any
// steps.* text (so a malformed one is shown, flagged, rather than hidden).
export function wfSlotAnyRe() { return /\{\{\s*([a-zA-Z0-9_.\-]+|steps\.[^{}]*?)\s*\}\}/g; }

export function isValidStepName(name) { return WF_STEP_NAME_RE.test(String(name || '')); }

// The step-name part of a steps.* reference, however malformed ('' if none).
export function stepNameOfRef(ref) {
  const rest = String(ref || '').replace(/^steps\./, '');
  const dot = rest.indexOf('.');
  return dot < 0 ? rest : rest.slice(0, dot);
}

export const STEP_NAME_RULE_TEXT = 'letters, digits, "-" and "_" only';
