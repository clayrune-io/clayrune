// Shared by the piece editor and What's article importer. Never executes HTML.
(function () {
  const MAX_BODY = 100000;
  const MAX_FILE_BYTES = 4 * 1024 * 1024;
  const ACCEPT = '.txt,.md,.markdown,.html,.htm';

  function htmlText(text) {
    const doc = new DOMParser().parseFromString(text, 'text/html');
    doc.querySelectorAll('script,style,template,noscript').forEach((el) => el.remove());
    doc.querySelectorAll('br').forEach((el) => el.replaceWith('\n'));
    doc.querySelectorAll('p,div,section,article,li,h1,h2,h3,h4,h5,h6,tr').forEach((el) => el.append('\n'));
    return (doc.body.textContent || '').replace(/\n{3,}/g, '\n\n').trim();
  }

  async function read(file) {
    if (!/\.(txt|md|markdown|html|htm)$/i.test(file.name)) throw new Error('Choose a text, Markdown or HTML file.');
    if (file.size > MAX_FILE_BYTES) throw new Error('The file exceeds the 4 MB import limit.');
    const raw = await file.text();
    const text = /\.html?$/i.test(file.name) ? htmlText(raw) : raw;
    if (text.length > MAX_BODY) throw new Error('The file exceeds 100,000 characters.');
    return text;
  }

  window.DeskV1TextImport = { read, ACCEPT };
})();
