# Prismix

**Practice any song with your band.** Prismix splits a track into vocals, drums, bass, guitar, piano and the rest, then shows its chords and synced lyrics while you change tempo and key. Phones on the same Wi-Fi follow the player and show each musician their own part.

[![Release](https://img.shields.io/github/v/release/rsx-xxx/prismix)](https://github.com/rsx-xxx/prismix/releases/latest)
[![Build](https://img.shields.io/github/actions/workflow/status/rsx-xxx/prismix/release.yml)](https://github.com/rsx-xxx/prismix/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

## Get started

**1. Install**

| Platform | How |
|---|---|
| macOS (Apple Silicon) | `brew tap rsx-xxx/prismix && brew install --cask prismix` |
| Windows | [`Prismix-Setup.exe`](https://github.com/rsx-xxx/prismix/releases/latest) |
| Android (band phone) | [`Prismix-Band.apk`](https://github.com/rsx-xxx/prismix/releases/latest) |
| Any (from source) | `uv sync && uv run prismix` (Python 3.11–3.13, [uv](https://docs.astral.sh/uv/)) |

All installers are on the [latest release](https://github.com/rsx-xxx/prismix/releases/latest). They are unsigned, so macOS or Windows asks for confirmation on first launch.

**2. Split a song.** Paste a YouTube or Spotify link, search by name, or drop in a local file, then press **Split**. The first run downloads the chord models (about 100 MB). Press `?` for keyboard shortcuts.

**3. Play with it.** Mute or solo stems, change tempo and key, loop a section, read the chords and lyrics. Chords and tab can also be moved to another key without touching the sound (Chord key).

**4. Bring the band.** Click **Band**, scan the QR code with a phone on the same Wi-Fi, pick your part. See [Band mode](#band-mode).

### Homebrew (macOS)

```bash
brew tap rsx-xxx/prismix
brew install --cask prismix                 # install
brew update && brew upgrade --cask prismix --greedy   # update (the app also updates itself from the toolbar)
```

Already installed Prismix by hand? Link that copy to brew with `brew install --cask --force prismix`, which replaces it with the brew-managed app. Your library is kept. Run `brew update` before every upgrade: brew does not refresh third-party taps on its own, so it can say "latest version is already installed" while a newer release exists. `--greedy` is needed because the app updates itself.

Your library lives in `~/Library/Application Support/Prismix` (macOS) or `%LOCALAPPDATA%\Prismix` (Windows).

## What it does

- **Stems:** splits a track into vocals, drums, bass, guitar, piano and the rest (Demucs v4 or BS-RoFormer, on CUDA, Apple Silicon or CPU). Mute or solo each one.
- **Tempo and key:** 50–150 % tempo and ±12 semitones without the robotic sound, A-B loop, export of the mix or single stems (mp3, m4a, flac, wav, ogg, opus).
- **Chords and tab:** beats, bars, key and chords are detected once per track. Chord chart, fingering diagrams, capo suggestions, PDF export, and Songsterr chord sheets and tab matched to the recording, in its key. A Notes setting picks flats or sharps.
- **Lyrics:** synced lyrics from Genius and LRCLIB, timed to the vocals word by word.
- **Band mode:** phones follow the host's player and show each musician their own part. No outside server.
- **Sources:** YouTube or Spotify links (track, album, playlist, artist), a search, a playlist, or a local audio or video file (up to 500 MB). Every split is cached, so nothing is processed twice.

## Band mode

1. Open Prismix on the computer and click **Band**.
2. Scan the QR code with a phone on the same Wi-Fi. The Prismix Band app opens by itself, or tap Scan in the app. In a browser, accept the one-time certificate warning (the host uses a self-signed certificate).
3. Pick your part. The phone follows the host's position, or plays the song itself in step with the room.

What phones can do:

- **Hear here:** a phone plays the sound and the host goes silent, so the computer needs no speakers. The host can also choose which device plays.
- **Change things:** tempo, key and chord key are shared, and everyone sees who changed what. A phone can simplify chords for itself only.
- **Queue:** the host's **Queue** view shows songs being processed; click one to run it next.
- **Solo (Android app):** save songs on the phone (about 25 MB each) and play them with no host at all.
- **Lock and PIN:** lock playback and set a PIN in the Band panel.

Another desktop can join too: click **Band**, switch to **Join**, pick the host that appears (or type its name or address) and enter its PIN. The window then shows the host's library and follows its player.

Details: the link uses `prismix.local` (or `prismix-<computer>.local` if taken, or your own via `PRISMIX_HOST_NAME`), with the IP address as a fallback. The Android app finds the host on its own (mDNS, then a Wi-Fi sweep), reconnects by name if the address changes, and pins its traffic to Wi-Fi so a VPN does not get in the way. `prismix` from the CLI offers band mode like the desktop app; `--no-share` keeps it local.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PRISMIX_DATA` | per-user app folder | Library location, shared by the desktop app and `uv run` |
| `PRISMIX_MAX_DURATION` | `1200` | Longest track, in seconds |
| `PRISMIX_MAX_UPLOAD_MB` | `500` | Largest uploaded file |
| `PRISMIX_COOKIES` | `cookies.txt` if present | Netscape cookies file for age-gated or bot-checked YouTube videos |
| `PRISMIX_GOOGLE_CLIENT_ID` | unset | OAuth web client ID for "my playlists" |
| `GENIUS_API_TOKEN` | unset | Enables Genius lyrics |

"My playlists" needs an OAuth client of type *Web application* with YouTube Data API v3 enabled and your app origin as an authorised JavaScript origin. Only the `youtube.readonly` scope is requested, and the token stays in the browser session.

To serve on the network, run `uv run prismix --host 0.0.0.0`. There is no authentication on that port, so keep it on a trusted network. On macOS, `sudo scripts/stems-port80.sh install` makes `http://stems.localhost` work without a port.

## Privacy

Only artist, title and length leave your machine, to look up lyrics, cover art, chords and a YouTube match. A Spotify link is read from Spotify's public embed page (no account). Audio never does. Lookups are cached, and nothing changes when you are offline.

## Development

```bash
uv run pytest
uv run ruff check .
```

Build the desktop app with `packaging/build.py` (see `packaging/`) and the Android app with `android/build.sh`. `v*` tags publish a release with all installers. See the [changelog](CHANGELOG.md) for what changed.

## Disclaimer

For education and personal use. Process only content you own or have permission to use; you are responsible for complying with the law and the terms of the services you use. Prismix does not host or distribute copyrighted content.

## Licences

MIT. Bundled: Rubber Band (GPL) for export, jsPDF (MIT) and Signalsmith Stretch (MIT) in `prismix/web/vendor/`.
