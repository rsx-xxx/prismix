// Chord chart PDF (jsPDF, loaded on first use).

const loaded = {};
function script(src) {
  return (loaded[src] ??= new Promise((ok, fail) => {
    const s = Object.assign(document.createElement('script'), { src, onload: ok, onerror: () => fail(new Error(`Could not load ${src}`)) });
    document.head.append(s);
  }));
}

/** Chord chart -> PDF Blob. Standard PDF fonts lack ♯/♭, so names are written with # and b. */
export async function chartPdf({ title, sub, bars, first }) {
  await script('vendor/jspdf.umd.min.js');
  const doc = new window.jspdf.jsPDF({ unit: 'pt', format: 'a4' });
  doc.setProperties({ title });
  const W = doc.internal.pageSize.getWidth(), H = doc.internal.pageSize.getHeight();
  const M = 48, perRow = 4, cw = (W - 2 * M) / perRow, ch = 52;
  const ascii = (s) => s.replace(/♯/g, '#').replace(/♭/g, 'b').replace(/→/g, '->');
  doc.setFont('helvetica', 'bold').setFontSize(20).text(ascii(title), M, M);
  doc.setFont('helvetica', 'normal').setFontSize(10).setTextColor(110).text(ascii(sub || ''), M, M + 18);
  let y = M + 44;
  bars.forEach((list, i) => {
    const col = i % perRow;
    if (!col && i) y += ch;
    if (y + ch > H - M) { doc.addPage(); y = M; }
    const x = M + col * cw;
    doc.setDrawColor(150).setLineWidth(0.6).rect(x, y, cw, ch);
    doc.setFontSize(7).setTextColor(130).text(String(i + first), x + 4, y + 9);
    const names = list.length ? list : [''];
    doc.setFont('helvetica', 'bold').setFontSize(names.length > 2 ? 12 : 15).setTextColor(20);
    names.forEach((n, k) => doc.text(ascii(n), x + 8 + k * ((cw - 12) / names.length), y + ch / 2 + 9));
    doc.setFont('helvetica', 'normal');
  });
  return doc.output('blob');
}
