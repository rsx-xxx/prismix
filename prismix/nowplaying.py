"""macOS Now Playing in the desktop shell: the page reports what plays, the media keys and the Control Center widget drive it back."""
from __future__ import annotations

import logging
import sys
import threading

log = logging.getLogger(__name__)
_keep: list = []
_art: dict = {}  # url -> MPMediaItemArtwork (or None while loading / unavailable)


class NowPlaying:
    """Exposed to the page as window.pywebview.api (js_api); `window` is the pywebview window used to call the page back."""

    def __init__(self) -> None:
        self.window = None

    def now_playing(self, title: str, artist: str, playing: bool, pos: float, dur: float, rate: float, art: str = "") -> bool:
        if sys.platform != "darwin":
            return False
        try:
            from MediaPlayer import (
                MPMediaItemPropertyArtist,
                MPMediaItemPropertyPlaybackDuration,
                MPMediaItemPropertyTitle,
                MPNowPlayingInfoCenter,
                MPNowPlayingInfoPropertyElapsedPlaybackTime,
                MPNowPlayingInfoPropertyPlaybackRate,
            )

            self._remote()
            center = MPNowPlayingInfoCenter.defaultCenter()
            info = {
                MPMediaItemPropertyTitle: title, MPMediaItemPropertyArtist: artist,
                MPMediaItemPropertyPlaybackDuration: float(dur), MPNowPlayingInfoPropertyElapsedPlaybackTime: float(pos),
                MPNowPlayingInfoPropertyPlaybackRate: float(rate if playing else 0.0),
            }
            cover = self._cover(art)
            if cover is not None:
                from MediaPlayer import MPMediaItemPropertyArtwork

                info[MPMediaItemPropertyArtwork] = cover
            center.setNowPlayingInfo_(info)
            center.setPlaybackState_(1 if playing else 2)  # MPNowPlayingPlaybackStatePlaying / Paused
            return True
        except Exception:  # noqa: BLE001 - the page keeps working without the system widget
            log.exception("Now Playing unavailable")
            return False

    def _cover(self, url: str):
        """The song's cover as Now Playing artwork. Fetched off-thread from the local server; shown from the next update on."""
        if not url:
            return None
        if url not in _art:
            _art[url] = None
            threading.Thread(target=self._fetch_cover, args=(url,), daemon=True).start()
        return _art[url]

    def _fetch_cover(self, url: str) -> None:
        try:
            import urllib.request

            from AppKit import NSImage
            from Foundation import NSData
            from MediaPlayer import MPMediaItemArtwork

            with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310 - the page's own origin
                data = r.read()
            image = NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(data, len(data)))
            if image is None:
                return
            _art[url] = MPMediaItemArtwork.alloc().initWithBoundsSize_requestHandler_(image.size(), lambda size: image)
        except Exception:  # noqa: BLE001 - no cover is fine
            log.debug("cover for Now Playing failed", exc_info=True)

    def now_playing_stop(self) -> None:
        if sys.platform != "darwin":
            return
        try:
            from MediaPlayer import MPNowPlayingInfoCenter

            MPNowPlayingInfoCenter.defaultCenter().setNowPlayingInfo_(None)
            MPNowPlayingInfoCenter.defaultCenter().setPlaybackState_(0)
        except Exception:  # noqa: BLE001
            pass

    def _call(self, op: str, arg: float = 0) -> int:
        if self.window:
            self.window.evaluate_js(f"window.__media && window.__media({op!r}, {arg})")
        return 0  # MPRemoteCommandHandlerStatusSuccess

    def _remote(self) -> None:
        if _keep:
            return
        from MediaPlayer import MPRemoteCommandCenter

        c = MPRemoteCommandCenter.sharedCommandCenter()
        wiring = [
            (c.playCommand(), lambda e: self._call("play")),
            (c.pauseCommand(), lambda e: self._call("pause")),
            (c.togglePlayPauseCommand(), lambda e: self._call("toggle")),
            (c.skipForwardCommand(), lambda e: self._call("step", 10)),
            (c.skipBackwardCommand(), lambda e: self._call("step", -10)),
            (c.changePlaybackPositionCommand(), lambda e: self._call("seek", e.positionTime())),
        ]
        for command, fn in wiring:
            command.setEnabled_(True)
            _keep.append((command, command.addTargetWithHandler_(fn)))
