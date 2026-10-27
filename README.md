# Prismix

**Practice any song with your band.** Prismix splits a track into vocals, drums, bass, guitar, piano and the rest, then shows its chords and synced lyrics while you change tempo and key. Phones on the same Wi-Fi follow the player and show each musician their own part.

[![Release](https://img.shields.io/github/v/release/rsx-xxx/prismix)](https://github.com/rsx-xxx/prismix/releases/latest)
[![Build](https://img.shields.io/github/actions/workflow/status/rsx-xxx/prismix/release.yml)](https://github.com/rsx-xxx/prismix/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

## Features

- **Stems:** Demucs v4 (4 stems) or BS-RoFormer (6 stems with guitar and piano), on CUDA, Apple Silicon or CPU. Mute or solo each stem.
- **Tempo and key:** 50–150 % tempo and ±12 semitones plus fine tune without the robotic sound, A-B loop, and export of the mix or single stems (mp3, m4a, flac, wav, ogg, opus).
- **Chords and bars:** beats, bars, key and chords are detected once per track. Chord chart, fingering diagrams, capo suggestions, PDF export, and a check against Songsterr chord sheets.
- **Lyrics:** synced lyrics from Genius and LRCLIB, timed to the vocals word by word.
- **Band mode:** phones scan a QR code and follow the host's player. Each picks a part (vocals, guitar, bass, keys, drums), sees the current bar, chord and lyric line, and can control playback. No outside server.  Phones can simplify chords for themselves, and the song shows in the system media controls. The Android app can save songs on the phone (about 25 MB each) and play them in Solo with no host at all.
- **Sources:** a YouTube or Spotify link (track, album, playlist, artist), a search, a playlist, or a local audio or video file (up to 500 MB). Every split is cached, so nothing is processed twice.

## Install

**Desktop app:** download from the [latest release](https://github.com/rsx-xxx/prismix/releases/latest).

| Platform | File |
|---|---|
| Windows | `Prismix-Setup.exe` |
| macOS (Apple Silicon) | `Prismix.dmg` |
| Android (band phone) | `Prismix-Band.apk` |

Desktop builds are unsigned (signing is paid; see `packaging/SIGNING.md`), so expect a SmartScreen or Gatekeeper prompt on first launch. The Android APK is signed with a fixed key and updates in place. The library lives in `%LOCALAPPDATA%\Prismix` (Windows) or `~/Library/Application Support/Prismix` (macOS).

**From source** (Python 3.11–3.13, [uv](https://docs.astral.sh/uv/)):

```bash
uv sync
uv run prismix            # http://stems.localhost:8000
```

Paste a YouTube link or search by name, then press **Split**. Press `?` in the app for keyboard shortcuts. The first run downloads the Beat This and BTC chord models from Hugging Face (about 100 MB).

## Band mode

1. Open Prismix on the computer and click **Band**.
2. Scan the QR code with a phone on the same Wi-Fi (the Prismix Band app opens by itself, or tap Scan in the app) and, in a browser, accept the one-time certificate warning (the host serves HTTPS with a self-signed certificate).
3. Pick your part. The phone follows the host's position, or plays the song itself in step with the room.

The link uses `prismix.local` (or `prismix-<computer>.local` if another host has it, or your own via `PRISMIX_HOST_NAME`), with the IP address as a fallback. The Android app finds the host on its own (mDNS, then a Wi-Fi sweep) and reconnects by name if the address changes. You can also type a name or address in the app. It pins its traffic to Wi-Fi, so a VPN on the phone does not get in the way (unless the VPN blocks all non-VPN traffic). Use `prismix --share` to offer band mode from the CLI. You can lock playback and set a PIN in the Band panel.

Another desktop can join as well: click **Join** in the toolbar, pick the host that appears (or type its name or address) and enter its PIN if it has one. The window then shows the host's library and follows its player, view-only if the host locked playback. Leave with Controls, Joined host, Leave.

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
