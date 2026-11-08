// Mashup covers: the user names the song the lyrics come from and the song the chords come from.

const $ = (id) => document.getElementById(id);

export function createSources({ api, notice, onChange, onTab }) {
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
    $('check-tab').hidden = $('check-note').hidden = true;
    const btn = $('auto-tab');
    btn.hidden = true;
    $('hf-box').hidden = $('hf-login').hidden = $('hf-code-link').hidden = true;
    api(`/api/tabdata/${id}/auto`).then((d) => {
      const ready = d.available && d.kinds.length;
      btn.hidden = !ready || !d.signedIn;
      $('check-tab').hidden = $('check-note').hidden = !d.available || !d.signedIn;
      $('hf-box').hidden = $('hf-login').hidden = !ready || d.signedIn;
      if (ready) btn.textContent = `Transcribe ${d.kinds.join(', ')}`;
    }).catch(() => {});
    dlg.showModal();
  });

  $('check-tab').addEventListener('click', async () => {
    const target = id, btn = $('check-tab');
    if (!target) return;
    btn.disabled = true;
    $('auto-msg').textContent = 'Listening to every instrument and comparing it with the tab… this can take a few minutes.';
    try {
      const tab = await api(`/api/tabdata/${target}/check`, { method: 'POST' });
      const flagged = Object.values(tab.tracks || {}).filter((t) => !t.auto && !t.edited).reduce((n, t) => Math.max(n, t.bars.filter((b) => b.weak).length), 0);
      const fixed = Object.entries(tab.tracks || {}).filter(([, t]) => t.origin?.startsWith('check:')).map(([, t]) => t.kind);
      close();
      const skipped = tab.unverified?.length ? ` Could not judge ${tab.unverified.join(', ')}: its recording is too unlike the tab.` : '';
      notice((flagged ? `${flagged} bar${flagged > 1 ? 's' : ''} differ from the recording; corrected ${fixed.join(', ') || 'none'} added as auto parts.` : 'No bar clearly differs from the recording.') + skipped, flagged || skipped ? 'info' : 'ok');
      onTab(target, Object.keys(tab.tracks || {}).find((k) => tab.tracks[k].origin?.startsWith('check:')));
    } catch (err) {
      $('auto-msg').textContent = err.message;
    } finally {
      btn.disabled = false;
    }
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
      const tab = await api('/api/tabdata', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ videoId: target, url }),
      });
      $('slice-msg').textContent = '';
      $('src-slice').value = '';
      close();
      added(target, tab, 'Soundslice');
    } catch (err) {
      $('slice-msg').textContent = err.message;
    } finally {
      $('slice-import').disabled = false;
    }
  });

  $('file-import').addEventListener('click', async () => {
    const target = id, file = $('src-file').files[0];
    if (!target || !file) { $('file-msg').textContent = 'Choose a file first'; return; }
    $('file-import').disabled = true;
    $('file-msg').textContent = 'Reading the file…';
    try {
      const body = new FormData();
      body.append('file', file);
      const tab = await api(`/api/tabdata/${encodeURIComponent(target)}/file`, { method: 'POST', body });
      $('file-msg').textContent = '';
      $('src-file').value = '';
      close();
      added(target, tab, file.name);
    } catch (err) {
      $('file-msg').textContent = err.message;
    } finally {
      $('file-import').disabled = false;
    }
  });

  // The score is added beside the tab the song has: say what arrived, and open the first new part.
  function added(target, tab, from) {
    const keys = tab.added || [];
    if (tab.status === 'duplicate') {
      notice(`${from} is already imported with exactly the same tabs. Nothing was added.`, 'ok');
      onTab(target);
      return;
    }
    const names = keys.map((k) => tab.tracks[k]?.name?.split('|').at(-1).trim() || k);
    const newer = tab.status === 'updated' ? ' It is a newer version of a file already imported and replaces it.' : '';
    notice(`Added from ${from}: ${names.join(', ') || 'tab'}.${newer} Shown in the Tabs view, labelled by source.`, 'ok');
    onTab(target, keys[0]);
  }

  return { setTrack: (v) => { id = v; } };
}
