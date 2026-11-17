// Contents of a single-quoted JavaScript string inside an HTML attribute.
// Escape JS first: the HTML parser decodes entities before compiling handlers.
window.jsAttr = function jsAttr(value) {
  return String(value ?? '')
    .replace(/\\/g, '\\\\').replace(/'/g, "\\'")
    .replace(/[\u0000-\u001f\u007f]/g, c => '\\x' + c.charCodeAt(0).toString(16).padStart(2, '0'))
    .replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
};
