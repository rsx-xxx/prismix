"""macOS Now Playing in the desktop shell: the page reports what plays, the media keys and the Control Center widget drive it back."""
from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)
_keep: list = []


class NowPlaying:
    """Exposed to the page as window.pywebview.api (js_api); `window` is the pywebview window used to call the page back."""

    def __init__(self) -> None:
        self.window = None

    def now_playing(self, title: str, artist: str, playing: bool, pos: float, dur: float, rate: float) -> bool:
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
            center.setNowPlayingInfo_({
                MPMediaItemPropertyTitle: title, MPMediaItemPropertyArtist: artist,
                MPMediaItemPropertyPlaybackDuration: float(dur), MPNowPlayingInfoPropertyElapsedPlaybackTime: float(pos),
                MPNowPlayingInfoPropertyPlaybackRate: float(rate if playing else 0.0),
            })
            center.setPlaybackState_(1 if playing else 2)  # MPNowPlayingPlaybackStatePlaying / Paused
            return True
        except Exception:  # noqa: BLE001 - the page keeps working without the system widget
            log.exception("Now Playing unavailable")
            return False

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
