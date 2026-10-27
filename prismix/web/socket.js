// A WebSocket that stays up on a flaky Wi-Fi: exponential backoff with jitter, a watchdog for connections that went
// silent without closing (sleep, roaming), and an immediate retry when the network or the tab comes back.
export function createSocket({ url, onopen, onmessage, onclose, fatal = [], silence = 12_000, auto = true }) {
  let sock = null;
  let timer = 0;
  let tries = 0;
  let heard = 0;
  let stopped = false;

  const alive = () => sock?.readyState === 1;

  function drop(s, code) {
    if (sock !== s) return;
    sock = null;
    s.onopen = s.onmessage = s.onclose = s.onerror = null;
    try { s.close(); } catch { /* already closed */ }
    const stop = fatal.includes(code) || stopped;
    if (!stop) retry();
    onclose?.({ code }, stop);
  }

  function retry(now = false) {
    clearTimeout(timer);
    if (now) tries = 0;
    const wait = now ? 0 : Math.min(8000, 400 * 2 ** tries++) * (0.7 + Math.random() * 0.6);
    timer = setTimeout(connect, wait);
  }

  function connect() {
    clearTimeout(timer);
    if (stopped || sock) return;
    let s;
    try { s = new WebSocket(typeof url === 'function' ? url() : url); } catch { retry(); return; }
    sock = s;
    heard = performance.now();
    s.onopen = () => { tries = 0; heard = performance.now(); onopen?.(s); };
    s.onmessage = (e) => {
      heard = performance.now();
      let m;
      try { m = JSON.parse(e.data); } catch { return; }
      onmessage?.(m);
    };
    s.onclose = (e) => drop(s, e.code);
    s.onerror = () => { /* onclose follows */ };
  }

  setInterval(() => {
    if (sock && document.visibilityState === 'visible' && performance.now() - heard > silence) drop(sock, 4408);
  }, 3000);
  const wake = () => {
    if (stopped) return;
    if (!sock) retry(true);
    else if (document.visibilityState === 'visible' && performance.now() - heard > silence / 2) drop(sock, 4408);
  };
  addEventListener('online', wake);
  document.addEventListener('visibilitychange', wake);

  if (auto) connect();
  return {
    get open() { return alive(); },
    send(msg) { if (!alive()) return false; sock.send(JSON.stringify(msg)); return true; },
    /** Connect now (when started with `auto: false`) or reconnect, e.g. after the user typed a new PIN. */
    reconnect() { if (sock) drop(sock, 4409); else retry(true); },
    stop() { stopped = true; clearTimeout(timer); if (sock) drop(sock, 1000); },
  };
}
