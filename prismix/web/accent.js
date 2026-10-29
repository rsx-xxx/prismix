// Dynamic accent: the dominant hue of the song's cover tints the accent colour. Lightness and chroma stay fixed per theme, so contrast holds.
const root = document.documentElement;
let hue = null;

const dark = () => {
  const t = root.getAttribute('data-theme');
  return t ? t === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
};
const paint = () => {
  if (hue == null) root.style.removeProperty('--accent');
  else root.style.setProperty('--accent', dark() ? `oklch(0.8 0.13 ${hue})` : `oklch(0.5 0.2 ${hue})`);
};
new MutationObserver(paint).observe(root, { attributes: true, attributeFilter: ['data-theme'] });

/** Hue (0-360) of the most colourful part of an image, weighting saturation and skipping near-greys. */
export function dominantHue(data) {
  const bins = new Float32Array(36);
  for (let i = 0; i < data.length; i += 4) {
    const r = data[i] / 255, g = data[i + 1] / 255, b = data[i + 2] / 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    if (d < 0.12 || max < 0.15) continue;
    const s = d / max;
    const h = max === r ? ((g - b) / d + 6) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    bins[Math.floor((h * 60) / 10) % 36] += s * max;
  }
  let best = 0;
  for (let i = 1; i < 36; i++) if (bins[i] > bins[best]) best = i;
  return bins[best] > 0 ? best * 10 + 5 : null;
}

/** HSV hue to the OKLCH hue the accent uses: close enough per bucket via a small table of anchor hues. */
const OK = [29, 55, 95, 125, 142, 165, 195, 230, 262, 285, 320, 350];
const toOklch = (h) => {
  const x = (h / 360) * OK.length, i = Math.floor(x) % OK.length;
  const a = OK[i], b = OK[(i + 1) % OK.length] + (OK[(i + 1) % OK.length] < a ? 360 : 0);
  return Math.round((a + (b - a) * (x - Math.floor(x))) % 360);
};

export function setAccent(url) {
  const img = new Image();
  img.onload = () => {
    try {
      const c = document.createElement('canvas');
      c.width = c.height = 32;
      const g = c.getContext('2d', { willReadFrequently: true });
      g.drawImage(img, 0, 0, 32, 32);
      const h = dominantHue(g.getImageData(0, 0, 32, 32).data);
      hue = h == null ? null : toOklch(h);
    } catch { hue = null; }
    paint();
  };
  img.onerror = () => { hue = null; paint(); };
  img.src = url;
}

export function clearAccent() { hue = null; paint(); }
