"""Live MIDI drum input for rehearsal follow.

The app's window is a WKWebView, which has no Web MIDI, so the computer's CoreMIDI ports are read here and the hits are
pushed to the page over a WebSocket. Every event carries ``time.monotonic()`` taken the moment the driver hands it over; the
page measures the offset between that clock and its own with a few ping-pongs (see ``rehearse.js``), so the hit lands on the
audio timeline to about a millisecond.
"""
import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

RESCAN = 2.0  # seconds between looks for a plugged or unplugged kit while a page is connected


def _mido():
    import mido

    return mido


def input_names() -> list[str]:
    try:
        return list(_mido().get_input_names())
    except Exception:  # no backend or no MIDI subsystem: the same as having no ports
        log.debug("MIDI ports unavailable", exc_info=True)
        return []


class MidiSession:
    """One page's view of the MIDI inputs: the list of ports, the chosen one, and a stream of note-on events."""

    def __init__(self, loop: asyncio.AbstractEventLoop, names: Callable[[], list[str]] = input_names, open_port=None):
        self.loop = loop
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=512)
        self._names = names
        self._open = open_port or (lambda name, cb: _mido().open_input(name, callback=cb))
        self._port = None
        self.wanted = ""  # the port the page asked for; reopened when the kit is plugged back in
        self.ports: list[str] = []

    def _on_message(self, msg) -> None:
        # runs on the driver's thread: stamp first, do the rest on the loop
        stamp = time.monotonic()
        if msg.type != "note_on" or not msg.velocity:
            return
        event = {"type": "hit", "s": stamp * 1000, "n": msg.note, "v": msg.velocity, "ch": msg.channel}
        self.loop.call_soon_threadsafe(self._put, event)

    def _put(self, event: dict) -> None:
        with contextlib.suppress(asyncio.QueueFull):  # a stalled page must not grow memory; old hits are worthless anyway
            self.queue.put_nowait(event)

    def use(self, name: str) -> None:
        self.wanted = name
        self.close()
        self.scan()

    def scan(self) -> bool:
        """Refresh the port list and (re)open the wanted port if it is there. True when the list changed."""
        names = self._names()
        changed = names != self.ports
        self.ports = names
        if self._port is not None and self.wanted not in names:
            self.close()  # unplugged
            changed = True
        if self._port is None and self.wanted in names:
            try:
                self._port = self._open(self.wanted, self._on_message)
            except Exception:
                log.warning("could not open MIDI port %r", self.wanted, exc_info=True)
        return changed

    @property
    def open(self) -> str:
        return self.wanted if self._port is not None else ""

    def close(self) -> None:
        port, self._port = self._port, None
        if port is not None:
            try:
                port.close()
            except Exception:
                log.debug("closing MIDI port", exc_info=True)

    def state(self) -> dict:
        return {"type": "ports", "ports": self.ports, "open": self.open}
