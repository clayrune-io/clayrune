// Desk v1 — platform chrome for the post preview (desk-v1-post-preview.js). One
// entry per platform; each draws the SAME real content (the post's copy, its
// media, the account's name) inside that platform's frame. Nothing here invents
// content: no like/reply counts, no timestamps, no avatar photos (the mark is the
// platform's logo), no follower numbers. A platform with no entry falls back to
// `_plain`. Window-bridged module, no `import` (ground rule 1).
//
// A skin is `(m, h) => html`. `m` is the model built by the controller:
//   { platform, word, name, identity, title, body, media: [{kind, src, play, title}], large }
// `h` is the controller's helper set: esc, mark(), text(clampLines), media(opts), icons(names), empty(msg).
(function () {
  function head(m, h, sub, name) {
    return `<div class="desk-v1-pv-head">${h.mark()}<div class="desk-v1-pv-who"><div class="desk-v1-pv-name">${h.esc(name || m.name || m.word)}</div>${sub ? `<div class="desk-v1-pv-sub">${h.esc(sub)}</div>` : ''}</div></div>`;
  }
  const handle = (m) => (m.identity && m.identity !== m.name ? m.identity : '');

  const SKINS = {
    // An X account's identity is its handle.
    x: (m, h) => `${head(m, h, '', m.identity ? '@' + m.identity.replace(/^@/, '') : '')}
      <div class="desk-v1-pv-text">${h.text(m.large ? 0 : 7)}</div>
      ${h.media({ limit: m.large ? 4 : 1, grid: true })}
      ${h.icons(['message', 'repeat', 'heart', 'share'])}`,

    linkedin: (m, h) => `${head(m, h, m.word)}
      <div class="desk-v1-pv-text">${h.text(m.large ? 0 : 5)}</div>
      ${h.media({ limit: 1 })}
      ${h.icons(['thumb', 'message', 'repeat', 'send'], true)}`,

    instagram: (m, h) => `${head(m, h, '')}
      ${h.media({ limit: 1, ratio: '1 / 1', required: 'Instagram posts need a picture or video — none is attached yet.' })}
      ${h.icons(['heart', 'message', 'send', 'bookmark'])}
      <div class="desk-v1-pv-text"><strong>${h.esc(m.name)}</strong> ${h.text(m.large ? 0 : 3)}</div>`,

    youtube: (m, h) => `${h.media({ limit: 1, kind: 'video', ratio: '16 / 9', required: 'YouTube needs exactly one video attachment.', plain: true })}
      <div class="desk-v1-pv-yt-title">${h.esc(m.title)}</div>
      <div class="desk-v1-pv-yt-chan">${h.mark()}<span>${h.esc(m.name || m.word)}</span></div>
      <div class="desk-v1-pv-text">${h.text(m.large ? 0 : 3)}</div>`,

    tiktok: (m, h) => `${h.media({ limit: 1, ratio: '9 / 16', required: 'No video attached yet.', plain: true })}
      <div class="desk-v1-pv-tt-cap"><strong>@${h.esc((m.identity || m.name || '').replace(/^@/, ''))}</strong><div class="desk-v1-pv-text">${h.text(m.large ? 0 : 2)}</div></div>`,

    reddit: (m, h) => `${head(m, h, handle(m) ? `u/${handle(m).replace(/^u\//, '')}` : '')}
      <div class="desk-v1-pv-rd-title">${h.esc(m.title)}</div>
      <div class="desk-v1-pv-text">${h.text(m.large ? 0 : 4)}</div>
      ${h.media({ limit: 1 })}
      ${h.icons(['message', 'share'])}`,

    discord: (m, h) => `<div class="desk-v1-pv-dc">${h.mark()}<div class="desk-v1-pv-dc-main"><div class="desk-v1-pv-name">${h.esc(m.name || m.word)}</div>
      <div class="desk-v1-pv-text">${h.text(m.large ? 0 : 5)}</div>${h.media({ limit: 1 })}</div></div>`,

    blog: (m, h) => `${h.media({ limit: 1, plain: true })}
      <div class="desk-v1-pv-blog-title">${h.esc(m.title)}</div>
      <div class="desk-v1-pv-sub">${h.esc(m.name || m.word)}</div>
      <div class="desk-v1-pv-text">${h.text(m.large ? 0 : 6)}</div>`,

    // No platform yet (a message not placed on a channel) or one with no skin.
    _plain: (m, h) => `<div class="desk-v1-pv-plain-title">${h.esc(m.title)}</div>
      <div class="desk-v1-pv-text">${h.text(m.large ? 0 : 6)}</div>
      ${h.media({ limit: m.large ? 4 : 1, grid: true })}`,
  };

  window.DeskV1PostPreviewSkins = { render: (m, h) => (SKINS[m.platform] || SKINS._plain)(m, h) };
})();
