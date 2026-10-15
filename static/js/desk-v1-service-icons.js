// Desk v1 — the mark a connection wears (Ron 2026-10-15: the plain letters on the
// Connections cards "do not identify the service"; he expects YouTube as the red
// YouTube mark, Instagram in its gradient). ONE place maps a connection to a mark:
// a vendor logo shipped as a local file under static/desk-icons/ (source + licence
// per file: static/desk-icons/README.md), or, for a service with no mark here, a
// two-letter monogram on a colour that is stable for that service.
//
// UX-02 (docs/THE_DESK_V1_UI.md): a badge must also name the account, never a bare
// logo. Every caller keeps the connection's name beside the mark, so the mark is
// decoration (aria-hidden, empty alt) and never the only thing that says which.
//
// To add a vendor: drop its file in static/desk-icons/, add one line to MARKS, add
// the ids / hosts / name words that mean it to IDS / HOSTS / WORDS, and record its
// source in the README. Window-bridged module, no `import` (ground rule 1).
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const DIR = '/static/desk-icons/';
  // vendor key -> { file, bleed }. bleed: the file is already a complete rounded tile
  // (own background), so it fills the box; otherwise the logo sits on a white tile
  // with the clear space the vendor guidelines ask for. Marks are never recoloured.
  const MARKS = {
    youtube:      { file: 'youtube.svg' },
    instagram:    { file: 'instagram.svg', bleed: true },
    facebook:     { file: 'facebook.svg' },
    tiktok:       { file: 'tiktok.svg' },
    x:            { file: 'x.svg' },
    reddit:       { file: 'reddit.svg' },
    linkedin:     { file: 'linkedin.svg' },
    snapchat:     { file: 'snapchat.svg', bleed: true },
    github:       { file: 'github.svg' },
    notion:       { file: 'notion.svg' },
    discord:      { file: 'discord.svg' },
    googledrive:  { file: 'googledrive.svg' },
    googlephotos: { file: 'googlephotos.svg' },
    google:       { file: 'google.svg' },
    openai:       { file: 'openai.svg' },
    higgsfield:   { file: 'higgsfield.png', bleed: true },
  };

  // The ids the Desk itself uses (account platform, content source id, engine id).
  const IDS = {
    x: 'x', twitter: 'x', linkedin: 'linkedin', youtube: 'youtube', yt: 'youtube', instagram: 'instagram', facebook: 'facebook',
    tiktok: 'tiktok', snapchat: 'snapchat', reddit: 'reddit', discord: 'discord', github: 'github', notion: 'notion',
    gdrive: 'googledrive', google_drive: 'googledrive', gphotos: 'googlephotos', google_photos: 'googlephotos',
    google: 'google', openai: 'openai', higgsfield: 'higgsfield', higgsfield_mcp: 'higgsfield',
  };

  // Hosts, matched on the end of the hostname (so www.instagram.com and m.youtube.com
  // both match). Longest match wins, so drive.google.com beats google.com.
  const HOSTS = {
    'x.com': 'x', 'twitter.com': 'x', 'linkedin.com': 'linkedin', 'youtube.com': 'youtube', 'youtu.be': 'youtube',
    'instagram.com': 'instagram', 'facebook.com': 'facebook', 'fb.com': 'facebook', 'tiktok.com': 'tiktok',
    'snapchat.com': 'snapchat', 'reddit.com': 'reddit', 'discord.com': 'discord', 'github.com': 'github',
    'notion.so': 'notion', 'notion.com': 'notion', 'drive.google.com': 'googledrive', 'photos.google.com': 'googlephotos',
    'google.com': 'google', 'openai.com': 'openai', 'higgsfield.ai': 'higgsfield',
  };

  // Whole words in a package / server name ("zubeid-youtube-mcp-server"). Only used
  // for things the user installed by name (MCP servers, saved services), never for
  // accounts, and never for a one-letter word like "x".
  const WORDS = {
    youtube: 'youtube', instagram: 'instagram', facebook: 'facebook', tiktok: 'tiktok', snapchat: 'snapchat', reddit: 'reddit',
    discord: 'discord', github: 'github', notion: 'notion', linkedin: 'linkedin', twitter: 'x', openai: 'openai', higgsfield: 'higgsfield',
  };

  function _hostOf(text) {
    const m = String(text || '').trim().toLowerCase().match(/^(?:[a-z][a-z0-9+.-]*:\/\/)?([^\s/?#:]+\.[^\s/?#:]+)/);
    return m ? m[1] : '';
  }
  function _vendorByHost(text) {
    const host = _hostOf(text);
    if (!host) return null;
    let best = null;
    Object.keys(HOSTS).forEach((h) => {
      if ((host === h || host.endsWith('.' + h)) && (!best || h.length > best.length)) best = h;
    });
    return best ? HOSTS[best] : null;
  }
  function _vendorByWords(name) {
    const words = String(name || '').toLowerCase().split(/[^a-z0-9]+/);
    for (let i = 0; i < words.length; i++) if (WORDS[words[i]]) return WORDS[words[i]];
    return null;
  }

  // item: { brand?, platform?, id?, name?, link?, kind? } (a Connections tile item)
  // -> the vendor key in MARKS, or null (no mark known: monogram).
  function vendor(item) {
    const it = item || {};
    const ids = [it.brand, it.platform, it.id];
    for (let i = 0; i < ids.length; i++) {
      const k = String(ids[i] || '').toLowerCase();
      if (IDS[k]) return IDS[k];
    }
    const byHost = _vendorByHost(it.link) || _vendorByHost(it.name);
    if (byHost) return byHost;
    if (it.kind === 'mcp' || it.kind === 'service') return _vendorByWords(it.name);
    return null;
  }

  // Two letters for an unknown service: the first letters of its first two words
  // ("Acme Tools" -> AT), else the first two letters of its one word ("clayrune.io/blog"
  // -> CL). A web address is read by its first label.
  function letters(name) {
    let s = String(name || '').trim().replace(/^[a-z][a-z0-9+.-]*:\/\//i, '').replace(/^www\./i, '');
    if (/^\S+\.\S+/.test(s)) s = s.split(/[./:?#]/)[0];
    const words = s.split(/[^A-Za-z0-9]+/).filter(Boolean);
    if (!words.length) return '?';
    const out = words.length > 1 ? words[0][0] + words[1][0] : words[0].slice(0, 2);
    return out.toUpperCase();
  }

  // A hue from the service's name, so the same service is the same colour on every
  // screen and every visit. Saturation and lightness are fixed low enough that the
  // white letters read at WCAG AA on every hue (checked in the smoke).
  function monogramColour(key) {
    let h = 2166136261;
    const s = String(key || '').toLowerCase();
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
    return `hsl(${h % 360} 45% 30%)`;
  }

  // The mark for one connection: the vendor logo, or a monogram. Default box is 28px;
  // `inline` sits it in a line of text at 20px.
  function markHTML(item, inline) {
    const v = vendor(item);
    const box = inline ? '--svc-icon-size:20px' : '--svc-icon-size:28px';
    const cls = inline ? 'desk-v1-svc-icon desk-v1-svc-icon-inline' : 'desk-v1-svc-icon';
    if (v && MARKS[v]) {
      const m = MARKS[v];
      return `<span class="${cls}" data-svc-icon="${esc(v)}" data-icon-kind="${m.bleed ? 'tile' : 'logo'}" style="${box}" aria-hidden="true"><img src="${DIR}${esc(m.file)}" alt="" decoding="async" draggable="false"></span>`;
    }
    const it = item || {};
    const key = it.name || it.id || it.brand || '';
    return `<span class="${cls}" data-svc-icon="monogram" data-icon-kind="monogram" style="${box};--mono-bg:${monogramColour(key)}" aria-hidden="true">${esc(letters(it.name || it.id || it.brand))}</span>`;
  }

  window.DeskV1ServiceIcons = { vendor, letters, monogramColour, markHTML, vendors: () => Object.keys(MARKS), file: (v) => (MARKS[v] ? DIR + MARKS[v].file : null) };
})();
