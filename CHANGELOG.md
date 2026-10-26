# Changelog

Every release lists what changed. The section of a tag (`vX.Y.Z`) becomes the GitHub release notes automatically.

## 0.0.17

- Playlist tags have their own colors: amber for queued, blue for processing, gray for not split yet.
- Chord fingerings now sit in the same row as the chords. A divider between lyrics and chords resizes both (double-click for automatic), and by default the lyrics column widens so long lines fit. The Music and Lyrics jump buttons are gone from the player.

## 0.0.16

- Playlist dragging rebuilt: a copy of the song follows the pointer, the others make room live, and dropping on another playlist or Library works from anywhere on the row. Fixes songs not moving at all.
- Fix: the playlist status refresh crashed once a song turned ready, so progress stopped updating. Songs waiting in the queue now say "Queued".

## 0.0.15

- Fix: the playlist drag styles (grip, dragged row, drop targets, song numbers) had been lost from the stylesheet in 0.0.10, so dragging looked broken. Restored, and the dragged row now lifts with a shadow while the cover no longer drags on its own.
- Prepared songs in a playlist show a green dot instead of a "Ready" label.

## 0.0.14

- Playlist songs move only by dragging, and you can grab a song anywhere on its row (not just the grip); the ⇄ move button is gone.
- Starting a song from a playlist is now obvious: the row says "Starting…" then shows progress, and the progress card stays pinned at the top with a short highlight.

## 0.0.13

- Clicking a not-yet-processed song in a playlist now processes just that song, instead of asking you to use Process all.

## 0.0.12

- Library and player covers fall back to the YouTube thumbnail when the saved cover file is missing, instead of showing a broken image.

## 0.0.11

- The lyrics layout button now names what a click does ("Chords above" / "Side by side") instead of the current state.

## 0.0.10

- Tabs now share the Chords column beside the lyrics: a Chords | Tabs switch swaps them, bars align with the lyrics, each bar shows its chord, and strings are labelled with note names.
- New "Follow playback" button (key F) appears when the lyrics, chords or tabs are scrolled away from the playing position; manual scrolling now holds until you press it, seek or play.
- Calmer player: chord pills no longer overlap the lyric line and the dock is tighter.

## 0.0.9

- Playlist songs move freely: drag a row by its grip to any position (the page scrolls at the edges), drop it on another playlist or on "Library" shown while dragging, or use the new ⇄ button to move it to the top, bottom, another playlist or back to the library.
- Library cards can be dragged onto a playlist card to add the song there.

## 0.0.8

- Chord fingerings moved to a page side bar outside the Chords block, so the chord grid keeps its width.
- Chords found online (Songsterr) are looked up automatically and used by default; the detected chords remain the fallback.
- Guitar and bass tablature has its own Tabs block in the desktop UI, following playback and seekable by bar.
- Playlist previews (Spotify and YouTube) are numbered, and saved playlists have a three-stripe grip to drag songs into a new order (Alt+Up/Down from the keyboard).

## 0.0.7

- Pasting a Spotify playlist works on machines without system root certificates (the desktop builds), and matching a playlist's songs to YouTube no longer fails when several searches run at once.
- Every link Prismix opens goes to the Zen browser when it is installed, otherwise to the default one.
- Chord fingering diagrams sit in a side bar next to the bars, so they stay in view while the chords scroll.
- Light theme: song-part labels, the section strip and dimmed lyrics are readable on white.
- The lyrics header shows only a quiet gray Genius link instead of the long status line.
- Android release APKs are signed with one fixed key, so each update installs over the last. Desktop builds stay unsigned (paid); `packaging/SIGNING.md` lists the free options.

## 0.0.6

- Spotify links work like YouTube ones: paste a track to split it, or an album, playlist or artist link to open it in Playlists and save or process all. Each song is matched to a YouTube video by length and name; nothing is downloaded from Spotify (up to 100 songs per list).
- The Android app reaches the host even with a VPN or per-app proxy on the phone (for example Cloudflare WARP): its traffic is pinned to the Wi-Fi network.
- The Android app takes a host by name (`prismix.local`) as well as by address, and shows "Connecting…" until the page is there instead of a black screen. A host that moved is found again.

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
