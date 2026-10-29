# Changelog

Every release lists what changed. The section of a tag (`vX.Y.Z`) becomes the GitHub release notes automatically.

## 0.15.0

- Stickers can be dragged to a new moment, deleted with a × on the sticker, and are readable on an opaque card.
- Fingering is only for string instruments with a fretboard (not bass, drums or keys), has its own show/hide button, is larger, and its settings are open in the panel. The player strip no longer shows fingering, the chord grid no longer highlights it, and "Next" is no longer printed on the next chord.
- Cloud: songs not in your library show their cover (with a fallback to the video thumbnail), and any cloud song can be deleted after a confirmation.
- Tabs: fixed a crash while fitting tabs to a recording when notes carry technique flags, which left some songs without tabs.
- Lyrics: "(unofficial)", "(audio)" and similar tags no longer break the lookup, audio-only results are retried once, speech recognition uses Whisper large-v3-turbo (about 1.6 GB, downloaded on first use), and impossible words are dropped.
- With synced lyrics, tab and drum bars share a row with the lyric line they are played under, so both scroll together.

## 0.14.0

- New "Play on" button (like Cast or Spotify Connect, no Spotify account needed): send Prismix's sound to another output on this computer (speakers, AirPlay, Bluetooth) or to a phone connected through Band. The choice is remembered.
- The accent colour follows the cover of the open song, in both themes.
- On phones: a bottom navigation bar (Library, Upload, Playlists, Cloud, Band) and a swipe-down handle to leave the studio; the mini player sits above the bar.
- Top bar fits at any desktop width; buttons no longer stack or get cut off.
- Library covers that were missing (a certificate error while downloading them) are fetched again on startup.

## 0.13.0

- The menu-bar mini player shows the song cover; Now Playing, the media overlay and Android already do.
- New look: deep zinc-950 dark theme by default (cards #18181b), with a refreshed light theme; frosted-glass top bar, controls and mini player. The theme still follows Settings → Theme.
- The cloud badge on covers and list rows is readable on any cover in both themes.
- The song keeps playing when you go back to the library; a mini player at the bottom shows it with play/pause, time and a way back, or stop it with ✕.
- Chord fingerings are three blocks: now and next (also shown in the chord strip above the controls), all chords, and settings. Point at any chord (tap on a phone) to see its fingering; both can be switched off in Settings. The tuning and chord-key dropdowns are readable and fit the block.
- Lyrics, chords and tab panes are taller and size themselves to the screen above the controls, so the line being sung is never hidden under them.

- "Restart to update" really restarts the app and is a proper button; it is enabled only when nothing is processing. When an update is ready, a pop-up and a macOS banner (with the app icon and a Restart Now action) appear.
- "Preparing songs" no longer starts over on every launch: songs that could not be completed are retried at most once a day. The pop-up is semi-transparent until hovered, opens a list of the songs being prepared (each one a link) and uses the app icon.
- The cloud screen is headed "TigerKiss cloud" instead of "not on this computer".
- The "Stickers" button is now "Notes". Notes gain Play and Pause symbols and a third visibility option, "Show on hover" (next to "Show on tap" and "Always show").

## 0.12.0

- The Android app names the band cloud after the band and shows its logo ("TigerKiss cloud"), in the player header and on the cloud screens; unknown clouds stay "Band cloud".
- While the band cloud is open, the app now watches the Wi-Fi for a Prismix computer. When one appears it offers to join it with a card over the player, or, if the app is in the background, a notification with a Connect button. "Not now" is remembered until the app is closed.
- Stems you have muted (or that a solo silences) no longer use CPU while tempo or key is changed, so the phone lags less with many stems; they start again, in sync, when un-muted.
- The Drums role now really opens the tab view by default (songs without a tab still open on lyrics).

## 0.11.0

- Smoother playback on phones and laptops. At normal tempo and key the stems now play directly, with no time-stretch processing (the heavy part that made phones lag); the stretch engine and its memory are only used while tempo or key is changed.
- Following the host no longer jumps around: sync only re-seeks after real drift (over 120 ms, twice in a row).
- The Android app keeps cloud stems on the phone after the first download (up to 1.5 GB, oldest songs dropped first), so a song opens instantly the next time.

## 0.10.0

- Phone page: picking a song shows a loading bar with progress while its audio downloads, so it no longer looks stuck.
- Phone page: sound is more stable. Overlapping downloads are merged, a stale player can no longer replace a new one, and a suspended audio engine is woken up or reported instead of silently "playing".
- Tempo, key and chord key moved next to the stems in one Playback & stems panel.
- Stems get "M" (mute) and "S" (solo) buttons, as on the desktop; they name their action (Mute/Unmute, Solo/Unsolo) for screen readers and work on the host in Follow mode and on the phone otherwise.
- Drum tabs now show (hi-hat/cymbals, toms, snare, kick lanes); the Drums role opens them by default.
- Tab, chord and lyric views keep the playing row, with its lyrics, in view instead of drifting to the bottom of the screen.

## 0.9.4

- The Android app is now just "Prismix": its name on the phone, its title screen and the release file (`Prismix.apk`, was `Prismix-Band.apk`). It installs over the old one and keeps its settings.

## 0.9.3

- Releases made in quick succession no longer queue up: starting a new build cancels the one still running for the previous version.

## 0.9.2

- The Follow button no longer lingers on the main page after leaving a song, and it now appears while a song is paused when you scroll the lyrics or chords away. Its icon is centered in the button.

## 0.9.1

- Notices that are not errors ("Added to …", "Moved to …", sync done) are no longer red: successes are green, hints are neutral, and both fade after a few seconds. Red stays for errors.

## 0.9.0

- Updating shows overall progress in Settings: a percentage, a progress bar and a short live note (download size and speed, refreshing Homebrew, installing, restarting).
- The queue shows the song's real name instead of a YouTube id while it waits.
- Dropdowns and text fields share one modern look everywhere, including the chord key, capo and tuning pickers that were unstyled.
- Tapping ⌘ alone opens the shortcut sheet; combos such as ⌘⇧S, even ones the system handles itself, no longer trigger it.

## 0.8.0

- macOS menu-bar mini player: an icon in the menu bar opens a popover with play/pause, seek, per-stem volume, mute and solo, master volume, tempo and key switchers, and the current BPM and key. It drives the song playing in the main window.
- The phone app shows the song's BPM (at the current tempo) in the player bar.
- Fixed per-stem volume sliders on a phone in Follow mode: the host ignored those commands.

## 0.7.0

- Updates install themselves: the app downloads a new release in the background and, once nothing is running, shows a native macOS alert with a Restart Now button (Settings offers the same on every platform). It never restarts by itself, and refuses while songs are being split or prepared or the Band cloud is syncing.
- The processing queue is now a small dock in the corner showing the running song, expandable to the whole queue. Starting a second song no longer hides the first one's progress.
- Fixed "No such file or directory … bs_models.json" in the packaged app: the separation model's data files are now bundled. A model that cannot load now gives a readable error.

## 0.6.1

- Guitar tunings: pick Drop D, half or whole step down, Drop C, DADGAD or an open tuning (or the one the song's tab uses) and the chord diagrams and names follow it. Prismix says in plain words how to fret a down-tuned guitar and suggests a tuning when it makes the chords easier.
- The key you set for a song (audio key, chords-only shift, guitar tuning) is saved with the song and shared through the Band cloud, so everybody opens it the same way. The cloud server must keep the new `setup` field of the song manifest.
- Uploaded files can be renamed by clicking the title. "(Караоке)" is no longer part of song titles.
- Lighter on memory: the separation, lyrics and alignment models are released when the queue is empty.

## 0.6.0

- Tag cut without its version bump; 0.6.1 is the same code, properly versioned.

## 0.5.0

- Dragging a song into a playlist moves the whole card (it lifts off as a tilted ghost, and playlists pulse as drop targets), not just the cover.
- Cloud markers: a song not in the cloud has an always-visible Upload button; one that is shows a white cloud on hover. Playlist rows carry the same markers, with Download for songs that are only in the cloud.
- "Sync" button above the library uploads what the cloud lacks and downloads what this computer lacks; a song already in the library is never downloaded or overwritten.

## 0.4.0

- The in-app update goes through Homebrew when brew installed Prismix (`brew update`, then upgrade of the prismix cask only), so brew's records stay correct; it retries once if another brew holds the lock and falls back to the release download if brew fails. Other installs update from the GitHub release as before.

## 0.3.0

- Cloud and library are one list: every song shows a cloud badge (outlined with an arrow = not uploaded yet, click to upload; filled = in the cloud). Songs only in the cloud sit in their own section; Download turns one into a normal library song with stems, lyrics and tab, no reprocessing.
- A cloud that has a registered band (TigerKiss) shows the band's name and logo in the app; other clouds stay "Band cloud".
- Android: a band cloud invite link opens the app and signs in, instead of the browser chooser (needs the cloud site to publish its app-link file).

## 0.1.0

- Processing a song can be cancelled: a Cancel button on the progress card and on every waiting or running row in the queue.
- Uploading to Band Cloud can be cancelled with a ✕ next to the upload button. Files already sent stay invisible until a song's commit.

## 0.0.51

- Band Cloud: one kind of link for everyone. Paste the invite link in the desktop app (no address or admin token field) and you can play and publish. Removing a song for everyone stays with the cloud admin.
- Clicking the link on a computer opens the Prismix app and signs it in, or the web player when the app is not installed.

## 0.0.50

- Homebrew: the cask uses the current `postflight_steps` form, so `brew install --cask prismix` no longer prints the deprecated `postflight` warning.

## 0.0.49

- Android: the Band Cloud screen is the app's own player now, with the same lyrics, chords, tab, mixer, tempo and key, fed from the cloud. Publishing also uploads the song's structure, lyrics and tab so members see them.
- Android: the invite link opens the app and signs in on its own. If the phone keeps the link in the browser, the web player opens instead.

## 0.0.48

- Android: Band Cloud sign-in is one field now. Paste the invite link; it already carries the cloud address and the token, so nothing else is asked.

## 0.0.47

- Tabs: bars where the recording plays but the tab rests (an instrument dropping out mid-song, a tab that stops early or skips a repeated section) are now filled from the tab's own bars that match the recording's chords almost exactly. Filled bars have a dashed outline; anything without a close match stays empty.
- Android: a build can carry an encrypted read-only Band Cloud invite from a local, untracked file, so the app signs in on first launch. The public release build contains none.

## 0.0.46

- Fixed the Settings button doing nothing in the desktop app: the panel no longer depends on the browser popover feature, and closes on an outside click or Esc.

## 0.0.45

- Band Cloud: sign in with the admin token on the computer to publish a processed song to your private band cloud, and see the cloud library beside the local one. The Android app has a new Band Cloud section: paste a member invite link, then browse, stream with seeking and download. The LAN connection page is unchanged.
- Stickers: pin a symbol and an optional comment to a moment of the song, from the computer or a phone. It appears on the lyrics, chords and tab for the whole band, and the comment is either always visible or shown on tap.

## 0.0.44

- Score fixes: the key signature now follows the notes (no more sharps over flat songs), staff lines run through the clef column, flags are curved, and rests keep clear of stems. Tabs open larger and have A− / A+ buttons to change their size. The Video button is gone from the bottom bar. Lyrics light up a little earlier to cover output latency.

## 0.0.43

- Keys and other pitched instruments now show as staff notation (treble and bass clefs, key signature, stems, beams, rests, ties) instead of numbered tab. Drums are a new part and show as a percussion score. Numbered tab stays for guitar and bass only. Cached tabs refresh once to add the drum parts.

## 0.0.42

- Chords above the lyrics: a line that begins over a chord that started earlier now shows that chord (dimmed) instead of leaving the first words bare. Settings open as a small panel under their button, with no dimming or blur of the page. The tab instrument picker is Guitar, Bass, Keys and Others, each with a dropdown of its parts by name. Notes (flats or sharps) stay a per-device setting. `scripts/build-mac.sh --install` builds and installs the Mac app locally.

## 0.0.41

- Tabs now look like printed tab: every bar of the tab in its own order (none dropped when the recording match skips one), section names, tempo, rhythm stems and beams, ties, slides, hammer-ons, dead notes and let-ring brackets, with bars sized to their content. Cached tabs refresh once to pick this up. The background-fetch panel no longer covers the player.

## 0.0.40

- Chords, lyrics and tabs are now fetched in the background for every library song that lacks them, so songs open with everything ready. A translucent panel shows the progress and hides itself when all is done.

## 0.0.39

- Fixes 0.0.38, where the page stopped starting: Settings did nothing and the library never finished loading.

## 0.0.38

- Android app: only local-network addresses are joined, so a link or QR from elsewhere cannot point it at the internet. It re-searches when Wi-Fi returns and the host is gone, and a name that nobody answers to now ends in "not found" instead of searching forever.
- Android app: the notification and battery prompts are asked once instead of on every media update, the cover no longer reloads every few seconds, and the connect screens scroll, with rounded buttons that react to touch.
- Band links reconnect on their own with backoff, notice a dead Wi-Fi within seconds, and retry the moment the network or the tab comes back. The phone link, QR and mDNS name follow the computer when its address changes.
- A stalled phone no longer freezes updates for the rest of the band. Another website can no longer drive this computer's API or its band socket.
- Joining a computer gives clearer errors and cannot be double-submitted; buttons have disabled, touch and hover-less states, and dialogs animate in.

## 0.0.37

- Settings no longer says Up to date when a newer release exists; the update check ignores the rolling master build.

## 0.0.36

- A Notes setting (Auto, Flats, Sharps) in the app and on phones decides how chords and keys are spelled; Auto follows the key.

## 0.0.35

- The chord header links to the exact page the chords were matched to, with its artist and title.

## 0.0.34

- Chords and tab can be read in another key without moving the audio (Chord key, on the computer and on phones).
- A tab found for another key than the audio (a karaoke cut, a lowered cover) is now matched and shifted to the audio's key.
- Changes to tempo, key, chord key and song show for everyone, with who made them.

## 0.0.33

- A Queue button on the host opens the processing queue: the song being prepared with its progress, the ones waiting in order, and the latest finished. A waiting song has a Run next button that pauses the current one and starts it first.

## 0.0.32

- Hear here on a phone now makes the phone play and the computer go quiet. In the Band dialog the computer can pick which device plays the sound: itself or any connected phone. Phone sound errors now show a message instead of failing silently, and a phone wakes its audio on the next touch.

## 0.0.31

- Settings moved behind a gear button (theme, shortcuts, version and update), and Band and Join are now one Band dialog with two tabs. Clicking a queued song in a playlist now processes it right away: the song being processed pauses and continues after it.

## 0.0.29

- The desktop app updates itself: when a newer release exists, click the update link and Prismix downloads it, replaces itself and reopens. Versions before this one need one manual update first.

## 0.0.28

- A phone in Hear here mode can play the room's sound while the host goes silent. The phone's tempo and key controls now show the song's BPM and key name, the master volume shows a percentage, and tab chord names no longer overlap.

## 0.0.27

- `prismix` in the terminal now offers band mode by default, so the Band panel shows its QR code in the browser version too. Add `--no-share` to keep it local.

## 0.0.26

- A desktop can join another desktop's band: click Join, pick a host found on the network (or type its name or address), and the window shows that host's library and follows its player like a phone does. Leave from Controls, Joined host. The host's certificate is trusted for that session only, and a PIN or view-only lock set on the host applies.

## 0.0.25

- The app checks GitHub for a newer release and shows Update to vX.Y.Z next to the version label; it downloads the installer for your system, or opens the release page where there is none.

## 0.0.24

- Offline mode in the Android app: tap Save next to a song (or Save all in a playlist) while connected, and its stems, lyrics, chords and tab are kept on the phone. Without the host, the app offers Play saved songs, which opens the same page in Solo with the saved songs only, so the band can rehearse anywhere. Tap Saved, then Remove?, to free the space. The menu's Offline switch, Keep every song I open, saves each song as you open it.

## 0.0.23

- Songsterr and Soundslice tabs now keep every instrument of a song: all guitars, basses, keys and other parts, each switchable (instrument buttons, then 1/2/3 within one). Cached tabs refresh on their own.
- Tabs can be edited in place: Edit, click a spot on the staff, type a fret, arrows move, Backspace deletes, Undo. Edits save automatically per part, survive re-downloads, and Original restores the downloaded tab.
- The phone shows the same instrument and part pickers.

## 0.0.22

- Desktop app and `uv run prismix` now share one library and one set of playlists (the per-user app folder), so a song split in one is already there in the other. Existing `./data` libraries are merged into it.
- The phone's Stem volumes dialog controls the host's stems in Follow mode (volume, mute, reset); in Hear here and Solo it still shapes only the phone's own sound.
- The phone's position bar follows your finger and seeks once on release instead of jumping back mid-drag. Tapping a lyric line or bar seeks in every mode, not only Solo.

## 0.0.21

- Mashup covers: the new Sources button in the lyrics header names the song the words come from and the song the chords come from. Lyrics are fetched for the first and laid onto these vocals, online chords for the second are matched in any key, and wherever the recording goes its own way the detected chords stay.
- Tabs from Soundslice: paste the link of a public slice in the Sources dialog and its guitar and bass tab is matched to the recording's bars like a Songsterr one; a slice that is not this recording is refused.

## 0.0.20

- Lyrics and chords stay level on songs whose synced words stretch across an instrumental: a line is no longer held for tens of seconds, and a long run of bars next to one line scrolls on its own instead of pushing the text out of view.
- Scrollbars in the lyrics, chords and tab panes are hidden and appear only while you scroll by hand.

## 0.0.19

- Removed the "Chords aligned with … / agrees / differs / Use tab chords" strip under the chords. Online chords still apply in the background.
- A chord that carries on from the previous line is no longer repeated at the start of the next one. Chords above the lyrics now take their own space, so chords never overlap each other or the words.

## 0.0.18

- Instrumental stretches show a small ♪ in the lyrics instead of a tall empty gap: the lyrics wait on the note while the chords keep scrolling.
- Removed the Reanalyze button, the bar count and the "Check against online chords" button; online chords are matched automatically. The Follow playback button is now a small icon.

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
