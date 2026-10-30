// Browser-owned file chooser: also selects files on a phone over the tunnel.
// Persistence and Undo belong to the caller's existing M21 asset command.
(function () {
  let seq = 0;
  function open(fam, attach) {
    const video = fam.kind === 'video';
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = video ? window.DeskV1MediaFormats.videoAccept : 'image/*';
    input.hidden = true;
    input.dataset.pieceLocalUpload = fam.id;
    document.body.appendChild(input);
    const remove = () => input.remove();
    input.oncancel = remove;
    input.onchange = async () => {
      const file = input.files[0];
      remove();
      if (!file) return;
      const error = window.DeskV1MediaFormats.error(file, video ? 'video' : 'image');
      if (error) { window.DeskV1Kit.toast(error); return; }
      const src = URL.createObjectURL(file);
      const asset = { id: 'asset-' + Date.now().toString(36) + (++seq).toString(36), kind: video ? 'video' : 'image', title: file.name, src };
      try { await attach(asset, file); }
      catch (e) { window.DeskV1Kit.toast(`Could not add media: ${e && e.message ? e.message : String(e)}`); }
      finally {
        // Demo assets keep their blob until the page closes; live assets now
        // use the server URL, and failed live uploads have been rolled back.
        if (window.DeskV1Store.live()) URL.revokeObjectURL(src);
      }
    };
    input.click();
  }
  window.DeskV1LocalUpload = { open };
})();
