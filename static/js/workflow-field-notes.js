// A refused edit in the workflow builder's inspector says so NEXT TO THE FIELD it
// refused, not in a corner toast the person is not looking at (MC-1179 WB3/WB4
// follow-up, Ron 2026-11-18). Two kinds of refusal live here:
//   - a step name the slot grammar cannot express ("Review draft"): the model keeps
//     the old valid name, the field keeps what was typed and is marked invalid, and
//     on blur the field reverts to the old name while the reason stays up a few
//     seconds;
//   - an emptied outcome / approval-option pill whose `+` or port was used: the
//     reason sits under that row and focus goes back into the pill.
//
// The note is STATE on the builder (`st.fieldNote`), never only DOM: the builder
// rebuilds the inspector's HTML on every structural render, and a message that
// lived only in the DOM would vanish the instant one landed while it was showing.
// The render helpers below read it, so a re-render paints the same message; the
// delegated listeners (installed once, by wfFieldNotesInit) keep it live as the
// person types.
import { isValidStepName, STEP_NAME_RULE_TEXT } from './workflow-slot-grammar.js';

// How long the "kept the old name" message stays after the field has reverted.
export const WF_NOTE_HOLD_MS = 4000;

const esc = (s) => (window.esc ? window.esc(String(s == null ? '' : s)) : String(s == null ? '' : s));

// The one live note, or null. A note carries:
//   kind    'name' | 'outcome' | 'option'
//   node    the step it belongs to (a note for a step the inspector is not showing
//           is dropped)
//   idx     the pill row, for outcome/option
//   typed   what the person typed (name only, while pending)
//   pending true while the bad name is still in the field; false once reverted
//   until   epoch ms after which a reverted message goes away
//   focus   the field should be re-focused after a re-render (cleared when the
//           person moves focus elsewhere)
function liveNote(st) {
  const n = st && st.fieldNote;
  if (!n) return null;
  if (n.until && Date.now() >= n.until) { st.fieldNote = null; return null; }
  if (n.node !== st.inspectorOpen) { st.fieldNote = null; return null; }
  return n;
}

export function wfNameMessage(oldName, pending) {
  return `Step names take ${STEP_NAME_RULE_TEXT}, so other steps can use its result. `
    + (pending ? `Keeping "${oldName}" until this is fixed.` : `Kept "${oldName}".`);
}

export function wfLabelMessage(kind) {
  return `Name this ${kind} first: an unnamed ${kind} can't be wired to a next step.`;
}

// ── Render helpers (called from the inspector's own HTML builders) ───────────

// The step-name input plus its (empty, hidden until needed) note line.
export function wfNameFieldHTML(st, node, placeholder) {
  const n = liveNote(st);
  const mine = n && n.kind === 'name' && n.node === node.name ? n : null;
  const shown = mine && mine.pending ? mine.typed : node.name;
  const msg = mine ? wfNameMessage(node.name, mine.pending) : '';
  return `<input class="wfb-name" value="${esc(shown || '')}" placeholder="${esc(placeholder)}"`
    + `${mine && mine.pending ? ' aria-invalid="true"' : ''} aria-describedby="wfb-name-note">`
    + `<div id="wfb-name-note" class="wfb-node-inline-error wfb-field-note" data-note="name" role="alert"${msg ? '' : ' hidden'}>${esc(msg)}</div>`;
}

// The value a pill should show: '' when its row is the one the note refused.
export function wfPillValue(st, node, kind, idx, label) {
  const n = liveNote(st);
  return (n && n.kind === kind && n.node === node.name && n.idx === idx) ? '' : label;
}

export function wfPillInvalidAttr(st, node, kind, idx) {
  const n = liveNote(st);
  return (n && n.kind === kind && n.node === node.name && n.idx === idx) ? ' aria-invalid="true"' : '';
}

// The note line that follows a pill row.
export function wfVocabNoteHTML(st, node, kind, idx) {
  const n = liveNote(st);
  const mine = n && n.kind === kind && n.node === node.name && n.idx === idx;
  return `<div class="wfb-node-inline-error wfb-field-note" data-note="label" data-idx="${idx}" role="alert"${mine ? '' : ' hidden'}>${mine ? esc(wfLabelMessage(kind)) : ''}</div>`;
}

// ── Live paint (no re-render): sync the inspector's DOM to st.fieldNote ──────

function paint(st) {
  const insp = document.getElementById('wfb-inspector');
  if (!insp) return;
  const n = liveNote(st);
  const node = (st.def.nodes || []).find(x => x.name === st.inspectorOpen);
  const nameEl = insp.querySelector('.wfb-name');
  const nameNote = insp.querySelector('[data-note="name"]');
  const nameMine = n && n.kind === 'name' && node;
  if (nameEl) {
    if (nameMine && n.pending) nameEl.setAttribute('aria-invalid', 'true'); else nameEl.removeAttribute('aria-invalid');
  }
  if (nameNote) {
    nameNote.textContent = nameMine ? wfNameMessage(node.name, n.pending) : '';
    nameNote.hidden = !nameMine;
  }
  const pills = [...insp.querySelectorAll('.wfb-vocab-row .wfb-vocab-pill')];
  const labelKind = n && (n.kind === 'outcome' || n.kind === 'option') ? n.kind : null;
  pills.forEach((p, i) => {
    if (labelKind && i === n.idx) p.setAttribute('aria-invalid', 'true'); else p.removeAttribute('aria-invalid');
  });
  insp.querySelectorAll('[data-note="label"]').forEach((el) => {
    const mine = labelKind && Number(el.dataset.idx) === n.idx;
    el.textContent = mine ? wfLabelMessage(labelKind) : '';
    el.hidden = !mine;
  });
}

let _expiryTimer = null;
function scheduleExpiry(getState) {
  clearTimeout(_expiryTimer);
  const st = getState();
  const n = st && st.fieldNote;
  if (!n || !n.until) return;
  _expiryTimer = setTimeout(() => {
    const s = getState();
    if (s && liveNote(s) === null) paint(s);
  }, Math.max(0, n.until - Date.now()) + 20);
}

function clearNote(st) { if (st) { st.fieldNote = null; paint(st); } }

// ── Call sites in the builder ────────────────────────────────────────────────

// The sync saw `typed` in the name field and it is not a legal step name. The model
// keeps the old name (the caller does not apply `typed`); the field keeps what was
// typed and is marked invalid until fixed or left. No toast.
export function wfNameRefused(st, node, typed) {
  if (!st) return;
  const prev = st.fieldNote;
  st.fieldNote = { kind: 'name', node: node.name, typed, pending: true, until: 0,
    focus: !!(prev && prev.kind === 'name' && prev.focus) };
  paint(st);
}

// The sync saw a legal (or unchanged) name: a still-pending refusal is over. A
// reverted note is left alone so its message gets its full hold.
export function wfNameAccepted(st, node) {
  const n = st && st.fieldNote;
  if (n && n.kind === 'name' && n.node === node.name && n.pending) clearNote(st);
}

// A structural sync reconciles an emptied pill (drops it, or restores a wired
// label), so a "name this first" note about that pill no longer describes anything.
export function wfLabelNoteSettled(st) {
  const n = st && st.fieldNote;
  if (n && (n.kind === 'outcome' || n.kind === 'option')) st.fieldNote = null;
}

// `+` or a port drag on an emptied pill was refused: say so on that row and put
// focus in the pill. `kind` is what wfBlankLabelKind returned.
export function wfNoteBlankLabel(st, el, kind) {
  const row = el && el.closest ? el.closest('.wfb-vocab-row') : null;
  const insp = document.getElementById('wfb-inspector');
  if (!st || !row || !insp) return;
  const idx = [...insp.querySelectorAll('.wfb-vocab-row')].indexOf(row);
  if (idx < 0) return;
  st.fieldNote = { kind, node: st.inspectorOpen, idx, until: 0, focus: true };
  paint(st);
  const pill = row.querySelector('input.wfb-vocab-pill');
  if (pill) { pill.focus(); row.scrollIntoView({ block: 'nearest' }); }
}

// After the builder rebuilds its HTML: drop a note that no longer applies, put
// focus back where the person was typing, and keep the expiry timer armed.
export function wfFieldNotesAfterRender(st, getState) {
  const n = liveNote(st);
  if (n && n.focus) {
    const insp = document.getElementById('wfb-inspector');
    const el = n.kind === 'name'
      ? insp && insp.querySelector('.wfb-name')
      : insp && insp.querySelectorAll('.wfb-vocab-row .wfb-vocab-pill')[n.idx];
    if (el && document.activeElement !== el) {
      el.focus();
      try { el.setSelectionRange(el.value.length, el.value.length); } catch (e) { /* not a text input */ }
    }
  }
  if (getState) scheduleExpiry(getState);
}

// ── Delegated listeners (installed once) ─────────────────────────────────────

export function wfFieldNotesInit(getState) {
  if (window.__wfFieldNotesInit) return;
  window.__wfFieldNotesInit = true;
  const inspectorField = (t, sel) => (t && t.closest && t.closest('#wfb-inspector') && t.matches && t.matches(sel)) ? t : null;

  // Judge a name as it is typed, so the reason shows while the person is still in
  // the field. Typing only ever touches the note, never the model: the model keeps
  // its old name until the next sync accepts a legal one.
  document.addEventListener('input', (e) => {
    const st = getState(); if (!st) return;
    const nameEl = inspectorField(e.target, '.wfb-name');
    if (nameEl) {
      const node = (st.def.nodes || []).find(x => x.name === st.inspectorOpen);
      if (!node) return;
      const typed = nameEl.value.trim();
      if (typed && typed !== node.name && !isValidStepName(typed)) {
        st.fieldNote = { kind: 'name', node: node.name, typed, pending: true, until: 0, focus: true };
        paint(st);
      } else if (st.fieldNote && st.fieldNote.kind === 'name') {
        clearNote(st);
      }
      return;
    }
    const pill = inspectorField(e.target, '.wfb-vocab-pill');
    if (pill && st.fieldNote && (st.fieldNote.kind === 'outcome' || st.fieldNote.kind === 'option') && pill.value.trim()) {
      const pills = [...document.querySelectorAll('#wfb-inspector .wfb-vocab-row .wfb-vocab-pill')];
      if (pills.indexOf(pill) === st.fieldNote.idx) clearNote(st);
    }
  });

  // Leaving the name field with the bad name still in it: put the old name back
  // and keep the reason up for a few seconds so the revert is not silent.
  // Chromium fires focusout on a focused field that a re-render is about to remove,
  // while it is still connected. That is the builder rebuilding its HTML, not the
  // person leaving the field, so judge it one task later: by then a removed field is
  // detached and ignored.
  document.addEventListener('focusout', (e) => {
    const target = e.target;
    setTimeout(() => onFocusOut(getState, target), 0);
  });

  function onFocusOut(getState, target) {
    const st = getState(); if (!st || !target || !target.isConnected) return;
    const n = st.fieldNote;
    if (!n) return;
    if (document.activeElement === target) return; // focus came straight back (a re-render restored it)
    const nameEl = inspectorField(target, '.wfb-name');
    if (nameEl && n.kind === 'name') {
      const node = (st.def.nodes || []).find(x => x.name === st.inspectorOpen);
      if (!node) return;
      if (n.pending && nameEl.value.trim() && !isValidStepName(nameEl.value.trim())) {
        nameEl.value = node.name;
        st.fieldNote = { kind: 'name', node: node.name, pending: false, until: Date.now() + WF_NOTE_HOLD_MS, focus: false };
        paint(st);
        scheduleExpiry(getState);
      } else {
        n.focus = false;
      }
      return;
    }
    const pill = inspectorField(target, '.wfb-vocab-pill');
    if (pill && (n.kind === 'outcome' || n.kind === 'option')) n.focus = false;
  }
}
