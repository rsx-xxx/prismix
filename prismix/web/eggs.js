// Easter eggs of two releases: Shift+click the version label while running 0.67.x or 0.69.x.

const JOKES = {
  67: [
    'Six… seven! 🤷',
    'This release runs at 67 BPM. Do not tell the metronome.',
    'Sixty-seven stems walk into a bar. Only four were asked to leave.',
    'A bassist counts to 6-7 and still comes in on the one.',
  ],
  69: [
    'Nice.',
    'Nice. Nice. Nice. The drummer approves.',
    'Release 69: a bar of 6/9 chords, played with a straight face.',
    'Tuned to 69 cents flat. A real band calls that “vibe”.',
  ],
};

export function wireEggs({ label, version, notice }) {
  const minor = Number(/^\d+\.(\d+)\./.exec(version)?.[1]);
  const jokes = JOKES[minor];
  if (!jokes) return;
  let n = 0;
  label.addEventListener('click', (e) => {
    if (!e.shiftKey) return;
    e.preventDefault();
    notice(jokes[n++ % jokes.length], 'info');
  });
}
