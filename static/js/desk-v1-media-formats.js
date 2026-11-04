// Shared chooser/validation policy for Desk uploads. Extension hints matter on
// Windows, where the native video/* filter can omit ordinary MP4 files.
(function () {
  const videoExtensions = ['mp4', 'm4v', 'mov', 'qt', 'webm', 'mkv', 'avi', 'wmv', 'asf', 'mpg', 'mpeg', 'mpe', 'm1v', 'm2v', 'mpv', '3gp', '3g2', 'ogv', 'ogg', 'ogm', 'flv', 'f4v', 'ts', 'mts', 'm2ts', 'vob', 'divx', 'mxf', 'dv', 'rm', 'rmvb', 'nut', 'y4m'];
  const videoAccept = videoExtensions.map(e => '.' + e).concat(['video/*', 'video/mp4', 'video/x-m4v', 'video/quicktime', 'video/webm', 'video/x-matroska', 'video/x-msvideo', 'video/x-ms-wmv', 'video/mpeg', 'video/3gpp', 'video/3gpp2', 'video/ogg', 'video/x-flv', 'video/mp2t']).join(',');
  function isVideo(file) {
    return !!file && (/^video\//i.test(file.type || '') || videoExtensions.includes((file.name || '').split('.').pop().toLowerCase()));
  }
  function error(file, kind) {
    const video = kind === 'video';
    const limit = video ? 500 : 25;
    if (!file || !file.size || file.size > limit * 1024 * 1024) return `Choose a nonempty ${video ? 'video' : 'image'} up to ${limit} MB.`;
    if (video ? !isVideo(file) : !/\.(png|jpe?g|gif|webp)$/i.test(file.name)) return video
      ? 'Choose a video file (for example MP4, MOV, MKV or AVI), up to 500 MB.'
      : 'Choose a PNG, JPG, GIF or WebP image, up to 25 MB.';
    return '';
  }
  window.DeskV1MediaFormats = { videoExtensions, videoAccept, isVideo, error };
})();
