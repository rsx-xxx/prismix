// Mashup covers: the user names the song the lyrics come from and the song the chords come from.

const $ = (id) => document.getElementById(id);

export function createSources({ api, onChange, onTab }) {
  let id = null;
  const dlg = $('sources');
  const close = () => dlg.close();
  $('sources-close').addEventListener('click', close);

  $('ly-sources').addEventListener('click', async () => {
    if (!id) return;
    $('sources-msg').textContent = '';
    $('slice-msg').textContent = '';
    try {
      const got = await api(`/api/mashup/${id}`);
      $('src-lyrics').value = got.lyrics || '';
      $('src-chords').value = got.chords || '';
    } catch { /* the form opens empty */ }
    $('auto-msg').textContent = '';
    const btn = $('auto-tab');
    btn.hidden = true;
    api(`/api/tabdata/${id}/auto`).then((d) => {
      btn.hidden = !d.available || !d.kinds.length;
      if (d.available && d.kinds.length) btn.textContent = `Transcribe ${d.kinds.join(', ')}`;
    }).catch(() => {});
    dlg.showModal();
  });

  $('auto-tab').addEventListener('click', async () => {
    const target = id;
    if (!target) return;
    const btn = $('auto-tab');
    btn.disabled = true;
    $('auto-msg').textContent = 'Listening to the stems… this can take a few minutes.';
    try {
      await api(`/api/tabdata/${target}/auto`, { method: 'POST' });
      close();
      onTab(target);
    } catch (err) {
      $('auto-msg').textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  });

  $('sources-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const target = id;
    $('sources-save').disabled = true;
    try {
      const saved = await api(`/api/mashup/${target}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lyrics: $('src-lyrics').value, chords: $('src-chords').value }),
      });
      close();
      onChange(target, saved);
    } catch (err) {
      $('sources-msg').textContent = err.message;
    } finally {
      $('sources-save').disabled = false;
    }
  });

  $('slice-import').addEventListener('click', async () => {
    const target = id, url = $('src-slice').value.trim();
    if (!target || !url) return;
    $('slice-import').disabled = true;
    $('slice-msg').textContent = 'Reading the slice…';
    try {
      await api('/api/tabdata', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ videoId: target, url }),
      });
      close();
      onTab(target);
    } catch (err) {
      $('slice-msg').textContent = err.message;
    } finally {
      $('slice-import').disabled = false;
    }
  });

  return { setTrack: (v) => { id = v; } };
}
