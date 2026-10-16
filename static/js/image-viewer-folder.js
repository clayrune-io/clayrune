// Folder scope for the picture viewer (MC-1100 regression fix). A chat picture
// that is the only image in its message still steps through every image in its
// folder, as the pre-1100 viewer did. Pure lookup: no DOM, no viewer state.
// Resolves to { images:[{src,name}], paths:[abs path] } or null when the src is
// not a /api/serve-image?path= URL or the route fails / lists fewer than two files.

const pathKey = p => String(p).replace(/\\/g, '/').toLowerCase();

export function servedPath(src) {
  try {
    const u = new URL(src, location.href);
    return /\/api\/serve-image$/.test(u.pathname) ? u.searchParams.get('path') : null;
  } catch { return null; }
}

export async function folderImages(src, signal) {
  const path = servedPath(src);
  if (!path) return null;
  try {
    const u = new URL(src, location.href);
    const route = u.pathname + '/siblings';
    const response = await fetch(`${u.origin}${route}?path=${encodeURIComponent(path)}`, { signal });
    if (!response.ok) return null;
    const data = await response.json();
    if (!Array.isArray(data?.files) || data.files.length < 2) return null;
    const files = data.files.filter(f => typeof f === 'string');
    if (files.length < 2) return null;
    return {
      paths: files,
      index: Number.isInteger(data.index) ? data.index : -1,
      images: files.map(f => ({ src: `${u.origin}${u.pathname}?path=${encodeURIComponent(f)}`, name: f.split(/[\\/]/).pop() })),
    };
  } catch (error) {
    if (error.name !== 'AbortError') console.warn('[image-viewer] Folder list unavailable:', error.message);
    return null;
  }
}

// Index of `src` within a folder result, or -1 when that file is not listed.
// The server's own index (realpath-resolved) backs up the path match for the
// picture the viewer opened on, whose path may be a symlink/relative spelling.
export function folderIndex(folder, src, openedSrc) {
  const path = servedPath(src);
  const found = path ? folder.paths.findIndex(f => pathKey(f) === pathKey(path)) : -1;
  if (found >= 0) return found;
  return src === openedSrc && folder.index >= 0 && folder.index < folder.paths.length ? folder.index : -1;
}
