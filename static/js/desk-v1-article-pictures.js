// Compatibility bridge; both article writers share the media implementation.
(function () {
  window.DeskV1ArticlePictures = {
    pick: (...args) => window.DeskV1ArticleMedia.pick(...args),
    mount: (...args) => window.DeskV1ArticleMedia.mount(...args),
    pathsIn: (...args) => window.DeskV1ArticleMedia.pathsIn(...args),
  };
})();
