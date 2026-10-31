// Cover images that never show the browser's empty-image glyph: the box is a quiet placeholder, the picture fades in once decoded,
// a failed load is retried once, then falls back to YouTube's thumbnail. Above-the-fold covers load eagerly; the rest lazily.

const UPLOAD = /^u-[0-9a-f]{9}$/;
export const isUpload = (id) => UPLOAD.test(id);
export const ytThumb = (id) => `https://i.ytimg.com/vi/${id}/mqdefault.jpg`;

/** Point `img` at the song's cover. `local` overrides the saved-file URL (e.g. one carrying a pairing key); `eager` skips lazy loading. */
export function paintCover(img, id, { eager = false, local = `/media/${id}/thumb.jpg` } = {}) {
  img.classList.add('cv');
  img.classList.remove('ok', 'none');
  img.decoding = 'async';
  img.loading = eager ? 'eager' : 'lazy';
  if (eager) img.fetchPriority = 'high';
  let tries = 0;
  const shown = () => img.classList.add('ok');
  img.onload = shown;
  img.onerror = () => {
    tries += 1;
    if (tries === 1 && !isUpload(id)) img.src = ytThumb(id);
    else if (tries === 1) setTimeout(() => { img.src = `${local}${local.includes('?') ? '&' : '?'}r=1`; }, 800); // an upload has no YouTube twin: one more try
    else img.classList.add('none');
  };
  img.src = local;
  if (img.complete && img.naturalWidth) shown();
  return img;
}
