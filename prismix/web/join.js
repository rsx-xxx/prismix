// Join another desktop's band: pick a host found on the network (or type one) and open its library in this window.
export function createJoin({ $ }) {
  const err = (m) => { $('join-err').textContent = m || ''; };

  async function join(address) {
    err('Connecting…');
    try {
      const r = await fetch('/api/client/connect', {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ host: address }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Error ${r.status}`);
      location.href = '/remote';
    } catch (e) { err(e.message); }
  }

  async function scan() {
    const list = $('join-hosts');
    list.replaceChildren();
    $('join-empty').hidden = true;
    err('Looking for hosts…');
    try {
      const hosts = await (await fetch('/api/client/hosts')).json();
      for (const h of hosts) {
        const b = Object.assign(document.createElement('button'), {
          type: 'button', className: 'btn', textContent: `Join${h.pin ? ' (PIN)' : ''}`,
        });
        b.addEventListener('click', () => join(h.address));
        const li = document.createElement('li');
        li.append(Object.assign(document.createElement('span'), { textContent: `${h.name} · ${h.address}` }), b);
        list.append(li);
      }
      $('join-empty').hidden = hosts.length > 0;
      err('');
    } catch { err('Could not search the network.'); }
  }

  $('band-mode').addEventListener('click', (e) => {
    const pane = e.target.dataset?.pane;
    if (!pane) return;
    document.querySelectorAll('#band-mode button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.pane === pane)));
    $('pane-host').hidden = pane !== 'host';
    $('pane-join').hidden = pane !== 'join';
    if (pane === 'join') scan();
  });
  $('join-form').addEventListener('submit', (e) => { e.preventDefault(); join($('join-addr').value); });
}
