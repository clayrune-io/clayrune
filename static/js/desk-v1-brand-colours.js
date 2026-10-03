// Desk v1 — brand colours for the one-letter marks on Connections tiles (Ron
// 2026-10-03). The marks stay GLYPHS, never logo assets; only their background takes
// the vendor's colour. ONE table, one line per vendor: to colour a new vendor add a
// line to BRANDS (and an alias if its ids differ). Anything not listed is unknown and
// the tile keeps the neutral mark (blog, saved services / "Something else", new ids).
// The glyph colour is not stored: it is whichever of white or black reads better on
// the background (WCAG contrast), so a pair can never be entered wrong.
// Window-bridged module, no `import` (ground rule 1).
(function () {
  const BRANDS = {
    // social accounts (keyed by platform id)
    x:        '#000000',
    linkedin: '#0A66C2',
    youtube:  '#FF0000',
    discord:  '#5865F2',
    reddit:   '#FF4500',
    // content sources: one colour per Google product so the two sit apart in the grid
    gdrive:   '#1A73E8',   // Google blue (Drive's blue)
    gphotos:  '#EA4335',   // Google red (the Photos pinwheel's red petals)
    // generation engines (keyed by engine id)
    google:   '#4285F4',   // Veo + Gemini image: Google logo blue
    openai:   '#10A37F',   // OpenAI image: the OpenAI/ChatGPT green
    // Higgsfield: no brand colour verified, so it stays neutral until someone adds one.
  };
  // Ids that name the same vendor as a key above (a content source's id is not the
  // platform id).
  const ALIASES = { yt: 'youtube', google_drive: 'gdrive', google_photos: 'gphotos' };

  function _lin(c) { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }
  function luminance(hex) {
    const n = parseInt(String(hex).replace('#', ''), 16);
    return 0.2126 * _lin((n >> 16) & 255) + 0.7152 * _lin((n >> 8) & 255) + 0.0722 * _lin(n & 255);
  }
  function contrast(a, b) {
    const la = luminance(a), lb = luminance(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  }

  // { bg, fg, ratio } for a vendor id, or null when unknown (neutral mark).
  function colour(id) {
    const key = ALIASES[id] || id;
    const bg = BRANDS[key];
    if (!bg) return null;
    const white = contrast(bg, '#ffffff'), black = contrast(bg, '#000000');
    return white >= black ? { bg, fg: '#ffffff', ratio: white } : { bg, fg: '#000000', ratio: black };
  }

  // The inline style that paints a mark, or '' for the neutral one.
  function markStyle(id) {
    const c = colour(id);
    return c ? `--mark-bg:${c.bg};--mark-fg:${c.fg}` : '';
  }

  window.DeskV1BrandColours = { colour, markStyle, ids: () => Object.keys(BRANDS).concat(Object.keys(ALIASES)) };
})();
