// The processing queue on the host: a small dock showing the running song, expandable to the whole queue with cancel / run-next.
export function createQueue({ $, api }) {
  const dock = $('queue');
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
    const acts = document.createElement('div');
    acts.className = 'q-acts';
    if (j.stage !== 'done' && j.stage !== 'error') {
      const stop = document.createElement('button');
      stop.type = 'button';
      stop.className = 'btn small ghost';
      stop.textContent = 'Cancel';
      stop.onclick = async () => { stop.disabled = true; try { await api(`/api/jobs/${j.id}/cancel`, { method: 'POST' }); } catch { /* already finished */ } refresh(); };
      acts.append(stop);
    }
    if (j.stage === 'queued') {
      const go = document.createElement('button');
      go.type = 'button';
      go.className = 'btn small ghost';
      go.textContent = 'Run next';
      go.onclick = async () => { go.disabled = true; try { await api(`/api/jobs/${j.id}/promote`, { method: 'POST' }); } catch { /* already started */ } refresh(); };
      acts.append(go);
    }
    if (acts.children.length) li.append(acts);
    return li;
  }

  async function refresh() {
    let jobs;
    try { jobs = await api('/api/jobs'); } catch { return; }
    const live = jobs.filter((j) => j.stage !== 'done' && j.stage !== 'error');
    dock.hidden = !live.length;
    if (!live.length) { setOpen(false); return; }
    const run = live.find((j) => j.stage !== 'queued') || live[0];
    const pct = run.stage === 'queued' ? 0 : Math.round(run.progress * 100);
    $('queue-sum').textContent = `${run.title}${pct ? ` · ${pct}%` : ''}${live.length > 1 ? ` · +${live.length - 1} waiting` : ''}`;
    $('queue-bar').style.width = `${pct}%`;
    $('queue-list').replaceChildren(...live.map(row));
  }

  function setOpen(on) {
    dock.classList.toggle('open', on);
    $('queue-list').hidden = !on;
    $('queue-head').setAttribute('aria-expanded', String(on));
  }

  const WHAT = { chords: 'chords', lyrics: 'lyrics', tabs: 'tabs' };
  const box = $('prefetch');
  const list = $('prefetch-list');
  async function prefetch() {
    let st;
    try { st = await api('/api/prefetch'); } catch { return; }
    box.hidden = !st.active;
    if (!st.active) return;
    $('prefetch-title').textContent = `Preparing songs · ${st.done + 1} of ${st.total}`;
    $('prefetch-now').textContent = st.current ? `${st.current.title}: ${WHAT[st.current.step] || st.current.step}` : '';
    $('prefetch-bar').style.width = `${Math.round((st.done / Math.max(st.total, 1)) * 100)}%`;
    const rows = [st.current, ...(st.next || [])].filter(Boolean).map((it, i) => {
      const a = document.createElement('a');
      a.href = `#/v/${it.id}/${it.model}`;
      const name = document.createElement('span');
      name.textContent = it.title;
      const what = document.createElement('span');
      what.className = 'muted';
      what.textContent = i ? WHAT[it.step] || it.step : `${WHAT[it.step] || it.step}…`;
      a.append(name, what);
      if (!i) a.className = 'now';
      const li = document.createElement('li');
      li.append(a);
      return li;
    });
    if (st.total > rows.length) {
      const li = document.createElement('li');
      li.className = 'muted';
      li.textContent = `+${st.total - rows.length} more steps`;
      rows.push(li);
    }
    list.replaceChildren(...rows);
  }
  function setPrefetchOpen(on) {
    box.classList.toggle('open', on);
    list.hidden = !on;
    box.querySelector('.pf-note').hidden = !on;
    $('prefetch-head').setAttribute('aria-expanded', String(on));
  }
  $('prefetch-head').addEventListener('click', () => setPrefetchOpen(!box.classList.contains('open')));

  $('queue-head').addEventListener('click', () => setOpen(!dock.classList.contains('open')));
  timer = setInterval(() => { if (!document.hidden) { refresh(); prefetch(); } }, 3000);
  refresh();
  prefetch();
  return { refresh, stop: () => clearInterval(timer) };
}
