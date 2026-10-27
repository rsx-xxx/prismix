// The processing queue on the host: what is running, what waits, and a button to run a waiting song next.
export function createQueue({ $, api }) {
  const dlg = $('queue');
  let timer = 0;

  function row(j) {
    const li = document.createElement('li');
    const head = document.createElement('div');
    head.className = 'q-head';
    const t = document.createElement('b');
    t.textContent = j.title;
    const st = document.createElement('span');
    st.className = 'pl-tag';
    const active = j.stage !== 'queued' && j.stage !== 'done' && j.stage !== 'error';
    st.textContent = j.stage === 'done' ? 'Done' : j.stage === 'error' ? 'Failed' : j.stage === 'queued' ? 'Waiting' : `${Math.round(j.progress * 100)}%`;
    head.append(t, st);
    const msg = document.createElement('small');
    msg.className = 'muted';
    msg.textContent = j.message || '';
    li.append(head, msg);
    if (active) {
      const bar = document.createElement('div');
      bar.className = 'bar';
      const i = document.createElement('i');
      i.style.width = `${Math.round(j.progress * 100)}%`;
      bar.append(i);
      li.append(bar);
    }
    if (j.stage === 'queued') {
      const go = document.createElement('button');
      go.type = 'button';
      go.className = 'btn small ghost';
      go.textContent = 'Run next';
      go.onclick = async () => { go.disabled = true; try { await api(`/api/jobs/${j.id}/promote`, { method: 'POST' }); } catch { /* already started */ } refresh(); };
      li.append(go);
    }
    return li;
  }

  async function refresh() {
    let jobs;
    try { jobs = await api('/api/jobs'); } catch { return; }
    const busy = jobs.filter((j) => j.stage !== 'done' && j.stage !== 'error').length;
    const btn = $('queue-btn');
    btn.hidden = !busy && !dlg.open;
    btn.dataset.count = busy || '';
    $('queue-list').replaceChildren(...jobs.map(row));
    $('queue-empty').hidden = jobs.length > 0;
  }

  $('queue-btn').addEventListener('click', () => { dlg.showModal(); refresh(); });
  $('queue-close').addEventListener('click', () => dlg.close());
  timer = setInterval(() => { if (!document.hidden) refresh(); }, 3000);
  refresh();
  return { refresh, stop: () => clearInterval(timer) };
}
