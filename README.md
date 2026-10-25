# Prismix

Take a YouTube video, split it into instrument tracks, and play only the ones you want. Mute or solo each stem, change tempo and transpose without the robotic sound. Every split is cached, so the same video is never processed twice.

> **⚠️ DISCLAIMER: This software is provided for educational and personal use only.**
>
> This tool is intended for:
> - Educational purposes (learning about audio processing, ML models, web development)
> - Personal use with content you own or have explicit permission to use
> - Research and development purposes
>
> **By using this software, you agree that:**
> - You will only process content that you own, have created, or have explicit permission to use
> - You are solely responsible for ensuring your use complies with all applicable laws and terms of service
> - The developers are not responsible for any misuse of this software
> - This software does not host, store, or distribute any copyrighted content
>

## Quick start

```bash
uv sync
uv run prismix            # http://stems.localhost:8000
```

Paste a YouTube link (or search by name) in the top bar and press Split. Press `?` in the app for keyboard shortcuts.

## How it works

- **Separation:** Demucs v4 on GPU (CUDA), Apple Silicon (MPS) or CPU. Models: `htdemucs_ft` (Best), `htdemucs_6s` (adds guitar and piano).
- **Download:** yt-dlp with `yt-dlp-ejs` and a JS runtime (node, deno or bun) for YouTube's challenges. ffmpeg is bundled via `imageio-ffmpeg`.
- **Playback:** Web Audio with one Signalsmith Stretch (WASM) node per stem, all scheduled identically so stems stay in sync. Tempo 50–150 %, key ±12 semitones plus fine tune, formant preservation, A-B loop.
- **Download audio:** the Download button renders what you hear (or chosen stems, or one file per stem as a zip) with the current tempo and key, using Rubber Band R3 (`pylibrb`), as mp3, m4a, flac, wav, ogg or opus.
- **Bars and chords:** [Beat This](https://github.com/CPJKU/beat_this) finds beats and downbeats, [BTC](https://huggingface.co/puar-playground/btc-chord) the chords, and the key comes from the chords. The ruler above the waveforms shows bars and chords (click to seek, Shift-click to loop bars), and the dock shows the current bar, beat and chord.
- **Chords:** beats, bars, key and chords are detected once per track (Beat This + BTC). The chord chart, ruler and fingering diagrams follow the Key control, with an optional capo suggestion and a chart PDF. Chords are colour-coded: hue is the position on the circle of fifths (related chords get neighbouring hues), lightness separates major, minor and diminished. "Follow" scrolls with playback and pauses while you scroll by hand.
- **Lyrics:** synced lyrics from [LRCLIB](https://lrclib.net) (free, no key), shown Spotify-style with the current line lit; click a line to jump to it. Only artist, title and length are sent, once per track, and the result is cached as `lyrics.json`. If a track is not found, search by hand. Press `L` to hide or show.
- **Suggested playlists:** the Playlists dialog offers mixes from the artists in your library, plus a few starter searches.
- **Cache:** `data/library/<video_id>/<model>/` holds FLAC stems and metadata. A track is only listed once all of its files are written. Delete a track from the library grid to free space.

- **What to run:** the task select next to Split picks Everything, Stems only, or Chords only (no separation, the whole mix is analysed). Stems whose level is more than 24 dB below the mix (typically 6-stem piano or "other" on tracks without them) are dropped instead of added.
- **Chords:** the current chord is highlighted in the ruler, chart and diagrams. Show/hide chords with `H`. Diagrams show finger numbers and barres, with an Easiest option, plain triads for 7ths and 6ths, a capo suggestion, and a click-to-cycle alternative fingering.
- **Capo:** the Capo select (off, auto, or fret 1-7) shows chord shapes for a capo, with the sounding key unchanged. Each fret lists how much easier it makes the song; auto picks a fret only when it clearly beats open shapes, and when off a suggestion button appears if one would help.
- **Online chords:** "Check against online chords" looks the song up on [Songsterr](https://www.songsterr.com), aligns its chord sheet with the detected chords (tolerating a different key or a repeated section), links to the sheet, underlines chords that agree and flags the ones that differ. "Use tab chords" swaps in the sheet's version of the differing chords. Songsterr has no official API, so this uses the same endpoints as its website, only on click, once per track (cached as `tabs.json`). Many songs only have tabs there, not chord sheets; paste a Songsterr chords link if the automatic match is wrong.
- **Video:** `V` opens a YouTube panel (muted) that follows the studio's position and tempo, so you watch the original picture against your edited mix. Needs internet.
- **Playlists:** the playlists button lists your YouTube playlists after Google sign-in, or any public playlist or mix URL pasted in. Reset tempo and key with `R` (`Shift+R` for key only) or the reset buttons.

- **Upload:** the Upload button (or dropping a file on the page) adds a local audio or video file (up to 500 MB). The same bytes are never stored twice. The preview picture is the embedded cover or a video frame, else album art from the free iTunes Search API (only artist and title text is sent, and only art from `mzstatic.com` is accepted), else a waveform image. Title and artist come from the file's tags, else the cleaned filename (junk like `(Official Audio)` and track numbers removed). The track is also looked up on YouTube (artist and title text only); a match with the same words and length fills in a missing title and artist and is stored as `match`, and nothing is changed when there is no confident match or no internet.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PRISMIX_DATA` | `./data` | Cache location |
| `PRISMIX_MAX_DURATION` | `1200` | Max video length in seconds |
| `PRISMIX_MAX_UPLOAD_MB` | `500` | Max uploaded file size |
| `PRISMIX_GOOGLE_CLIENT_ID` | unset | OAuth web client ID for "my playlists" (see below) |
| `PRISMIX_COOKIES` | `cookies.txt` if present | Netscape cookies file for age-gated videos |

Google sign-in: in Google Cloud create an OAuth client of type Web application, enable YouTube Data API v3, add your app origin (e.g. `http://stems.localhost:8000`) as an authorised JavaScript origin, and set the client ID. Only the `youtube.readonly` scope is requested, and the token stays in the browser session.

For `http://stems.localhost` without the port (macOS), run `sudo scripts/stems-port80.sh install` once. It redirects loopback port 80 to 8000 and nothing else; `uninstall` removes it.

`uv run prismix --host 0.0.0.0 --port 8000` to serve on the network. There is no authentication, so keep it on a trusted network.

First run downloads the Beat This checkpoint (~77 MB) and the BTC chord model from Hugging Face (its repo ships code, loaded with `trust_remote_code` at a pinned revision).

## Licences

Rubber Band (GPL) for export, jsPDF (MIT), Signalsmith Stretch (MIT). Vendored browser libraries live in `prismix/web/vendor/`.

## Development

```bash
uv run pytest
uv run ruff check .
```
