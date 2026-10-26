# Changelog

Every release lists what changed. The section of a tag (`vX.Y.Z`) becomes the GitHub release notes automatically.

## 0.0.6

- Spotify links work like YouTube ones: paste a track to split it, or an album, playlist or artist link to open it in Playlists and save or process all. Each song is matched to a YouTube video by length and name; nothing is downloaded from Spotify (up to 100 songs per list).
- The Android app reaches the host even with a VPN or per-app proxy on the phone (for example Cloudflare WARP): its traffic is pinned to the Wi-Fi network.
- The Android app takes a host by name (`prismix.local`) as well as by address, and shows "Connecting…" until the page is there instead of a black screen. A host that moved is found again.
- Release builds are code-signed on Windows and macOS once the signing secrets are set up (see `packaging/SIGNING.md`).

## 0.0.5

- With several hosts on the network the Band app lists all of them, showing which ones it has joined before and when; a single host is still joined directly.
- Each host has a name you can change in its Band panel, by default "User · computer", and phones see that name.
- The screen stays on while music plays (the app and the phone browser) and is released when playback stops.

## 0.0.4

- Phones open `https://prismix.local/` with nothing to add: the host also listens on 443 (and redirects plain http), keeping 8443 for the Android app.
- Landscape on the phone gives the song more room: slimmer header and a one-row player.
- The phone no longer reuses an old copy of the library list, so songs added in the browser show up in the app too.
- Model names are clearer ("4 stems" and "6 stems · Deep"), and a failed job says what went wrong instead of a generic message.

## 0.0.3

- Songs get a Tab view on the phone: the best matching guitar and bass tab from Songsterr, checked bar by bar against the recording and shown with lyrics and chords. Songs without a trustworthy match have no tab.
- Chords sit on the letter where the strum starts, not just on the syllable.

## 0.0.2

- The phone has a Stem volumes screen: a level and a mute per stem for the sound played on that phone.
- Buttons on the phone respond to a tap with a ripple and a short vibration.
- The Android app can save the original audio, or every stem as its own file, to Music/Prismix as M4A.
- Exported and saved audio carries the cover, title, artist, album, year and genre from the iTunes catalogue (the YouTube title and a square thumbnail when nothing matches), in MP3, M4A and FLAC.

## 0.0.1

- Stem separation, a synced lyrics and chord studio, and Band mode: phones on the same network follow or drive the host's player.
- Playlists: add songs from the library to a new or existing playlist, or paste a YouTube playlist link (or pick one from a channel) to save it and split every song in it. Playlists show on the main page and on the phone, and you can switch songs inside one.
- Lyrics on the phone are highlighted continuously and scroll with a damped glide instead of jumping.
- The Android app shows the song in the system media controls, keeps playing and syncing with the screen off, and asks once to be exempt from battery saving.
- The Android app can scan the host's QR code.
