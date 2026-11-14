// The Studio image panel's Resolution picker. A model that lists resolutions
// (every Google Gemini image model, the Higgsfield API-key Soul) is refused by the
// server without one: "resolution must be one of ['1K','2K','4K']". Models that
// list none (the Higgsfield sign-in models) get no picker and no field.
//
// Pure functions: the panel owns the state.

function optionsOf(model) {
  return Array.isArray(model?.resolutions) ? model.resolutions.filter((r) => typeof r === 'string' && r) : [];
}

// The picked resolution if the model still offers it, else the model's first
// value (listed cheapest first), else null when the model has none.
export function pickResolution(model, current) {
  const options = optionsOf(model);
  if (!options.length) return null;
  return options.includes(current) ? current : options[0];
}

export function resolutionPickerHTML(model, current, esc) {
  const options = optionsOf(model);
  if (!options.length) return '';
  return `<label>Resolution <select class="desk-v1-cap-select" data-eng-resolution>${options.map((r) =>
    `<option value="${esc(r)}"${r === current ? ' selected' : ''}>${esc(r)}</option>`).join('')}</select></label>`;
}
