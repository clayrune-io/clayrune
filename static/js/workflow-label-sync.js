// Outcome / option labels are typed into pill inputs, but an edge stores the
// label as plain text and the row's `+`, port dot and ✕ were baked with the
// label (or row index) the row had at the LAST render (MC-1179 WB4). Typing a
// new label and then using that same row stranded the edge on the old text; the
// server refuses it at Save ("not a declared outcome/option") with no visible
// edge to fix. These two helpers carry the typed label to the edges.

// Reconcile a node's label list with what the pills now say, by row position.
// `edges` are repointed IN PLACE for every label that was renamed. A cleared
// pill that has an edge keeps its label (clearing text must not silently drop a
// wire; the ✕ is the explicit way to remove one, through the slot-break guard);
// a cleared pill with no edge is dropped, as before. If the pill count and the
// label count disagree the DOM is stale, so nothing is repointed and the old
// "non-empty pills" read stands. Returns the node's new label list.
export function wfReconcileLabels(from, oldLabels, typedLabels, edges) {
  const old = Array.isArray(oldLabels) ? oldLabels : [];
  const typed = typedLabels.map(s => String(s || '').trim());
  if (typed.length !== old.length) return typed.filter(Boolean);
  const renamed = new Map();
  const labels = [];
  typed.forEach((t, i) => {
    const o = old[i];
    if (!t) {
      if ((edges || []).some(e => e.from === from && e.when === o)) labels.push(o);
      return;
    }
    if (t !== o) renamed.set(o, t);
    labels.push(t);
  });
  if (renamed.size) {
    (edges || []).forEach((e) => {
      if (e.from === from && renamed.has(e.when)) e.when = renamed.get(e.when);
    });
  }
  return labels;
}

// The label a port control stands for RIGHT NOW: the text in its own row's
// pill if that row is editable and non-empty, else the label baked at render.
export function wfLiveWhen(el, baked) {
  const pill = el && el.closest ? el.closest('.wfb-vocab-row')?.querySelector('input.wfb-vocab-pill') : null;
  const typed = pill ? String(pill.value || '').trim() : '';
  return typed || baked;
}

// What the inspector pill at row `idx` says right now (trimmed; '' if cleared or
// absent). The ✕ buttons bake only a row index, and the sync that runs first
// can rename or drop that row's label, so the caller reads the pill BEFORE the
// sync and resolves the label from it afterwards.
export function wfTypedLabelAt(inputClass, idx) {
  const pills = document.querySelectorAll(`#wfb-inspector .${inputClass}`);
  return pills[idx] ? String(pills[idx].value || '').trim() : '';
}

// 'outcome' / 'option' when `el` sits in an editable label row whose pill is
// empty right now, else null. Such a row has no label to wire from: the sync
// drops it, so an edge made from it would be stored against a label that no
// longer exists. The `+` and the port-dot drag refuse it instead.
export function wfBlankLabelKind(el) {
  const pill = el && el.closest ? el.closest('.wfb-vocab-row')?.querySelector('input.wfb-vocab-pill') : null;
  if (!pill || String(pill.value || '').trim()) return null;
  return pill.classList.contains('wfb-option-input') ? 'option' : 'outcome';
}
