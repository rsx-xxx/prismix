import { GUITAR_TUNINGS } from './theory.js';
import { decimate, detect, nearest, nearestString, noteName, octaveOf, rms, toHz } from './pitch.js';

// Listening tuner (microphone -> AudioWorklet -> pitch) and reference tones (tuning fork, open strings) in one dialog.
// Shared by the desktop page and the phone page, so it builds its own markup and only needs a button to open it.

const INSTRUMENTS = [
  { id: 'chromatic', name: 'Chromatic', tunings: [{ id: 'chromatic', name: 'Any note', open: [] }] },
  { id: 'guitar', name: 'Guitar', tunings: GUITAR_TUNINGS },
  { id: 'guitar7', name: '7-string guitar', tunings: [{ id: 'std', name: 'Standard', open: [35, 40, 45, 50, 55, 59, 64] }] },
  { id: 'bass', name: 'Bass', tunings: [
    { id: 'b4', name: '4 strings', open: [28, 33, 38, 43] },
    { id: 'b4d', name: '4 strings, drop D', open: [26, 33, 38, 43] },
    { id: 'b5', name: '5 strings', open: [23, 28, 33, 38, 43] },
  ] },
  { id: 'ukulele', name: 'Ukulele', tunings: [{ id: 'std', name: 'Standard (G C E A)', open: [67, 60, 64, 69] }] },
  { id: 'violin', name: 'Violin, mandolin', tunings: [{ id: 'std', name: 'G D A E', open: [55, 62, 69, 76] }] },
];
const KEY = 'prismix.tuner';
const IN_TUNE = 4; // cents
const label = (m) => `${noteName(m)}${octaveOf(m)}`;
const median = (a) => [...a].sort((x, y) => x - y)[a.length >> 1];

const load = () => { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; } };
const save = (v) => { try { localStorage.setItem(KEY, JSON.stringify(v)); } catch { /* private mode */ } };

export function createTuner({ opener, onOpen = () => {}, guitarTuning = () => 'std' }) {
  const st = { a4: 440, inst: 'guitar', tuning: null, mode: 'listen', lock: -1, ...load() };
  const dlg = document.createElement('dialog');
  dlg.className = 'tuner';
  dlg.setAttribute('aria-label', 'Tuner');
  dlg.innerHTML = `
    <div class="tn-bar"><h2>Tuner</h2><button type="button" class="tn-x" aria-label="Close">×</button></div>
    <div class="tn-modes" role="tablist"><button type="button" role="tab" data-mode="listen">Listen</button><button type="button" role="tab" data-mode="tone">Tone</button></div>
    <div class="tn-pick"><select class="tn-inst" aria-label="Instrument"></select><select class="tn-tun" aria-label="Tuning"></select></div>
    <div class="tn-listen">
      <div class="tn-read" aria-live="polite"><b class="tn-note">–</b><sup class="tn-oct"></sup></div>
      <div class="tn-meter" role="meter" aria-label="Cents off pitch" aria-valuemin="-50" aria-valuemax="50" aria-valuenow="0">
        <i class="tn-zone"></i><i class="tn-needle"></i>
      </div>
      <p class="tn-hint">Play one string</p>
      <p class="tn-hz"><span class="tn-freq">– Hz</span> · <span class="tn-cents">–</span></p>
    </div>
    <div class="tn-tone" hidden>
      <button type="button" class="tn-fork" aria-pressed="false"><span>Tuning fork</span><b class="tn-fork-hz"></b></button>
    </div>
    <div class="tn-strings" role="group" aria-label="Strings"></div>
    <div class="tn-ref"><span>Reference A4</span><button type="button" data-d="-1" aria-label="Lower A4">−</button><output class="tn-a4"></output><button type="button" data-d="1" aria-label="Raise A4">+</button></div>
    <p class="tn-msg" role="status"></p>`;
  document.body.append(dlg);
  const q = (s) => dlg.querySelector(s);
  const $inst = q('.tn-inst'), $tun = q('.tn-tun'), $strings = q('.tn-strings'), $msg = q('.tn-msg');
  $inst.innerHTML = INSTRUMENTS.map((i) => `<option value="${i.id}">${i.name}</option>`).join('');

  const inst = () => INSTRUMENTS.find((i) => i.id === st.inst) ?? INSTRUMENTS[1];
  const tuning = () => inst().tunings.find((t) => t.id === st.tuning) ?? inst().tunings[0];
  const open = () => tuning().open;
  const persist = () => save({ a4: st.a4, inst: st.inst, tuning: st.tuning });

  function paintTunings() {
    $tun.innerHTML = inst().tunings.map((t) => `<option value="${t.id}">${t.name}</option>`).join('');
    $tun.value = tuning().id;
    $tun.hidden = inst().tunings.length < 2;
  }
  function paintStrings() {
    $strings.hidden = !open().length;
    $strings.innerHTML = open().map((m, i) => `<button type="button" data-i="${i}" aria-pressed="false"><b>${noteName(m)}</b><small>${octaveOf(m)}</small></button>`).join('');
  }
  function paintAll() {
    for (const b of dlg.querySelectorAll('.tn-modes button')) b.setAttribute('aria-selected', String(b.dataset.mode === st.mode));
    q('.tn-listen').hidden = st.mode !== 'listen';
    q('.tn-tone').hidden = st.mode !== 'tone';
    $inst.value = st.inst;
    paintTunings();
    paintStrings();
    q('.tn-a4').textContent = `${st.a4} Hz`;
    q('.tn-fork-hz').textContent = `A4 · ${st.a4} Hz`;
    idle();
  }

  // ── tones ──
  let tctx = null, voice = null;
  const stopTone = () => {
    if (!voice) return;
    const { osc, gain } = voice;
    const t = tctx.currentTime;
    gain.gain.cancelScheduledValues(t);
    gain.gain.setTargetAtTime(0, t, 0.05);
    osc.stop(t + 0.4);
    voice = null;
    for (const b of dlg.querySelectorAll('[aria-pressed=true]')) if (!b.classList.contains('lock')) b.setAttribute('aria-pressed', 'false');
  };
  function tone(hz, pure, btn) {
    const was = voice?.btn;
    stopTone();
    if (was === btn) return;
    tctx ??= new AudioContext({ latencyHint: 'playback' });
    tctx.resume();
    const osc = tctx.createOscillator();
    // phone speakers cannot reproduce 82 Hz, so strings get overtones (the ear still hears the pitch); the fork stays a pure sine
    if (!pure) {
      const re = new Float32Array([0, 1, 0.55, 0.4, 0.25, 0.15, 0.08]);
      osc.setPeriodicWave(tctx.createPeriodicWave(re, new Float32Array(re.length)));
    }
    osc.frequency.value = hz;
    const gain = tctx.createGain();
    const t = tctx.currentTime;
    gain.gain.setValueAtTime(0, t);
    gain.gain.linearRampToValueAtTime(pure ? 0.4 : 0.3, t + 0.03);
    if (!pure) gain.gain.setTargetAtTime(0.06, t + 0.1, 2.5); // a plucked string dies away; the fork rings on
    osc.connect(gain).connect(tctx.destination);
    osc.start();
    osc.stop(t + (pure ? 60 : 12));
    voice = { osc, gain, btn };
    btn.setAttribute('aria-pressed', 'true');
    osc.onended = () => { if (voice?.osc === osc) stopTone(); };
  }

  // ── listening ──
  let ctx = null, stream = null, node = null, timer = 0, wake = null;
  let recent = [], lastGood = 0, ok = 0, vibrated = false;
  const $note = q('.tn-note'), $oct = q('.tn-oct'), $needle = q('.tn-needle'), $meter = q('.tn-meter');

  function idle(text = 'Play one string') {
    $note.textContent = '–';
    $oct.textContent = '';
    $needle.style.setProperty('--c', '0');
    q('.tn-hint').textContent = text;
    q('.tn-freq').textContent = '– Hz';
    q('.tn-cents').textContent = '–';
    dlg.dataset.state = 'idle';
    for (const b of $strings.children) b.classList.remove('near');
  }

  function feed({ hz, clarity, level }) {
    const now = performance.now();
    const lo = open().length ? toHz(Math.min(...open()) - 3, st.a4) : 28;
    if (hz && clarity > 0.85 && level > 0.004 && hz > lo * 0.9 && hz < 1400) {
      // a jump of more than a semitone is a new note, not jitter: start the average over
      if (recent.length && Math.abs(12 * Math.log2(hz / median(recent))) > 1) recent = [];
      recent.push(hz);
      if (recent.length > 5) recent.shift();
      lastGood = now;
      show(median(recent));
    } else if (now - lastGood > 900) {
      recent = [];
      idle();
    }
  }

  function show(hz) {
    const strings = open();
    let cents, target, idx = -1;
    if (strings.length) {
      idx = st.lock >= 0 ? st.lock : nearestString(hz, strings, st.a4).index;
      target = strings[idx];
      cents = 1200 * Math.log2(hz / toHz(target, st.a4));
    } else {
      const n = nearest(hz, st.a4);
      target = n.midi;
      cents = n.cents;
    }
    const c = Math.max(-50, Math.min(50, cents));
    $note.textContent = noteName(target);
    $oct.textContent = octaveOf(target);
    $needle.style.setProperty('--c', (c / 50).toFixed(3));
    $meter.setAttribute('aria-valuenow', String(Math.round(c)));
    q('.tn-freq').textContent = `${hz.toFixed(1)} Hz`;
    q('.tn-cents').textContent = `${cents > 0 ? '+' : ''}${cents.toFixed(0)} ¢`;
    const inTune = Math.abs(cents) <= IN_TUNE;
    q('.tn-hint').textContent = inTune ? 'In tune' : cents < 0 ? 'Tune up ↑' : 'Tune down ↓';
    dlg.dataset.state = inTune ? 'ok' : Math.abs(cents) < 15 ? 'close' : 'far';
    if (inTune) {
      ok = ok || performance.now();
      if (!vibrated && performance.now() - ok > 250) { vibrated = true; navigator.vibrate?.(12); }
    } else { ok = 0; vibrated = false; }
    [...$strings.children].forEach((b, i) => b.classList.toggle('near', i === idx));
  }

  async function listen() {
    stopListen();
    if (!navigator.mediaDevices?.getUserMedia) {
      $msg.textContent = 'The microphone needs a secure page (https or localhost). The tones still work.';
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
      });
    } catch (e) {
      $msg.textContent = e?.name === 'NotAllowedError' || e?.name === 'SecurityError'
        ? 'Microphone is blocked. Allow it for Prismix in the system settings, then reopen the tuner.'
        : 'No microphone found.';
      return;
    }
    if (!dlg.open) { stopListen(); return; }
    $msg.textContent = '';
    ctx = new AudioContext({ latencyHint: 'interactive' });
    const src = ctx.createMediaStreamSource(stream);
    const factor = Math.max(1, Math.round(ctx.sampleRate / 24000));
    try {
      await ctx.audioWorklet.addModule(new URL('./tuner-worklet.js', import.meta.url));
      node = new AudioWorkletNode(ctx, 'pitch-processor', { numberOfOutputs: 0 });
      node.port.onmessage = (e) => feed(e.data);
      src.connect(node);
    } catch {
      // no AudioWorklet (old WebView): poll an analyser instead; same detector, a little more main-thread work
      const an = ctx.createAnalyser();
      an.fftSize = 8192;
      src.connect(an);
      const buf = new Float32Array(an.fftSize);
      timer = setInterval(() => {
        an.getFloatTimeDomainData(buf);
        const level = rms(buf);
        const hit = level > 0.004 ? detect(decimate(buf, factor), ctx.sampleRate / factor) : null;
        feed({ level, hz: hit?.hz ?? 0, clarity: hit?.clarity ?? 0 });
      }, 50);
    }
    try { wake = await navigator.wakeLock?.request('screen'); } catch { /* optional */ }
  }

  function stopListen() {
    clearInterval(timer);
    timer = 0;
    node?.disconnect();
    node = null;
    stream?.getTracks().forEach((t) => t.stop());
    stream = null;
    ctx?.close();
    ctx = null;
    wake?.release?.().catch(() => {});
    wake = null;
    recent = [];
  }

  const refresh = () => { if (st.mode === 'listen') listen(); else stopListen(); };
  const close = () => dlg.close();

  function openDialog() {
    onOpen();
    if (st.inst === 'guitar' && !load().tuning) st.tuning = guitarTuning();
    st.lock = -1;
    paintAll();
    dlg.showModal();
    refresh();
  }

  dlg.addEventListener('close', () => { stopTone(); stopListen(); });
  dlg.addEventListener('click', (e) => { if (e.target === dlg) close(); });
  q('.tn-x').onclick = close;
  document.addEventListener('visibilitychange', () => { if (document.hidden && dlg.open) { stopTone(); stopListen(); } else if (dlg.open) refresh(); });
  q('.tn-modes').onclick = (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    st.mode = b.dataset.mode; st.lock = -1; stopTone(); paintAll(); refresh();
  };
  $inst.onchange = () => { st.inst = $inst.value; st.tuning = null; st.lock = -1; persist(); paintAll(); };
  $tun.onchange = () => { st.tuning = $tun.value; st.lock = -1; persist(); paintAll(); };
  q('.tn-ref').onclick = (e) => {
    const b = e.target.closest('button[data-d]');
    if (!b) return;
    st.a4 = Math.max(400, Math.min(480, st.a4 + Number(b.dataset.d)));
    persist(); stopTone(); paintAll();
  };
  q('.tn-fork').onclick = (e) => tone(st.a4, true, e.currentTarget);
  $strings.onclick = (e) => {
    const b = e.target.closest('button[data-i]');
    if (!b) return;
    const i = Number(b.dataset.i);
    if (st.mode === 'tone') { tone(toHz(open()[i], st.a4), false, b); return; }
    st.lock = st.lock === i ? -1 : i;
    [...$strings.children].forEach((x, k) => { x.setAttribute('aria-pressed', String(k === st.lock)); x.classList.toggle('lock', k === st.lock); });
  };
  opener?.addEventListener('click', openDialog);
  return { open: openDialog, close };
}
