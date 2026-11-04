// YouTube's version text mapping and upload requirements. No store writes.
(function () {
  function fields(body) {
    const lines = String(body || '').trim().split('\n');
    return { title: lines.shift().trim(), description: lines.join('\n').trim() };
  }
  function errors(body, assets = []) {
    const f = fields(body), out = [];
    if (assets.length !== 1 || assets[0].kind !== 'video') out.push('YouTube needs exactly one video attachment; images and text-only posts cannot be uploaded');
    if (!f.title) out.push('YouTube needs a title on the first line');
    if ([...f.title].length > 100) out.push('YouTube title (first line) takes at most 100 characters');
    if ([...f.description].length > 5000) out.push('YouTube description (remaining lines) takes at most 5000 characters');
    if (/[<>]/.test(f.title + f.description)) out.push('YouTube title and description cannot contain < or >');
    return out;
  }
  function selected(camp, draft) {
    return window.DeskV1ComposerChannels.accounts(camp).some((c) => c.platform === 'youtube' && draft.selected.has(c.id));
  }
  function update(root, camp, draft) {
    let hint = root.querySelector('[data-youtube-requirements]');
    if (!hint) {
      hint = document.createElement('p'); hint.className = 'desk-v1-composer-hint';
      hint.setAttribute('data-youtube-requirements', ''); hint.setAttribute('role', 'status');
      root.querySelector('[data-composer-count]').after(hint);
    }
    const active = selected(camp, draft);
    hint.hidden = !active;
    const problems = active ? errors(draft.text, draft.fam?.assets) : [];
    hint.textContent = 'YouTube: attach exactly one video. First line = title (100 characters); remaining lines = description (5000). Assisted upload stops on Details; you finish settings and Publish/Save. Attaching uploads a private draft.' + (problems.length ? ' ' + problems.join('. ') + '.' : '');
    hint.toggleAttribute('data-failed', problems.length > 0);
    return problems;
  }
  const uploadProof = 'Re-enter your dashboard passcode to upload this approved video in your signed-in YouTube Studio pane. Uploading may save a private draft immediately. Clayrune fills title and description, then stops on Details. You finish audience and visibility and press Publish/Save yourself.';
  window.DeskV1YouTubePost = { fields, errors, selected, update, uploadProof };
})();
