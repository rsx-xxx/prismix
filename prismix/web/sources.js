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
    $('hf-box').hidden = $('hf-login').hidden = $('hf-code-link').hidden = true;
    api(`/api/tabdata/${id}/auto`).then((d) => {
      const ready = d.available && d.kinds.length;
      btn.hidden = !ready || !d.signedIn;
      $('hf-box').hidden = $('hf-login').hidden = !ready || d.signedIn;
      if (ready) btn.textContent = `Transcribe ${d.kinds.join(', ')}`;
    }).catch(() => {});
    dlg.showModal();
  });

  $('hf-login').addEventListener('click', async () => {
    const login = $('hf-login');
    login.disabled = true;
    try {
      const s = await api('/api/hf/login', { method: 'POST' });
      $('hf-code').textContent = s.code;
      $('hf-code-link').href = s.url;
      $('hf-code-link').hidden = false;
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 2500));
        const st = await api('/api/hf/login');
        if (st.status === 'done') {
          $('hf-box').hidden = login.hidden = true;
          $('auto-tab').hidden = false;
          $('auto-msg').textContent = 'Signed in.';
          return;
        }
        if (st.status === 'error') throw new Error(st.message);
      }
    } catch (err) {
      $('auto-msg').textContent = err.message;
    } finally {
      login.disabled = false;
    }
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
