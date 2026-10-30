"""Notation the user already has on disk: a folder of Guitar Pro (.gp3-.gp5), MusicXML (.musicxml, .xml, .mxl) and MIDI files.

Every file is read into the same bars as a downloaded tab, matched to the library songs by its name, and kept only when it
really fits the recording (chords bar by bar, in whatever key it is written). A wrong guess from the name therefore costs nothing.
"""
from __future__ import annotations

import contextlib
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from difflib import SequenceMatcher
from pathlib import Path

from . import analysis, autotab, library, tabdata, tabs

log = logging.getLogger(__name__)

EXTS = {".gp3", ".gp4", ".gp5", ".mid", ".midi", ".musicxml", ".xml", ".mxl"}
MAX_FILES = 3000
MAX_BYTES = 8_000_000
MIN_NAME = 0.55  # name similarity (0..1) below which a file is not even tried against a song
TRIES = 3  # files fitted per song, best name first
_STEP = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


class FileError(RuntimeError):
    pass


def scan(folder: str) -> list[Path]:
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise FileError("That folder does not exist")
    files = []
    for p in root.rglob("*"):
        if p.suffix.lower() in EXTS and p.is_file() and 0 < p.stat().st_size <= MAX_BYTES and not p.name.startswith("."):
            files.append(p)
            if len(files) >= MAX_FILES:
                break
    return files


def _kind_of_program(program: int, name: str) -> str | None:
    n = name.casefold()
    if "vocal" in n or "voice" in n:
        return None
    if 32 <= program <= 39 or "bass" in n:
        return "bass"
    if 24 <= program <= 31 or "guitar" in n:
        return "guitar"
    return "keys" if program < 24 else "other"


def _bars_of(notes: list[tuple[int, float, float]], sigs: list[tuple[float, int]], kind: str, total: float) -> list[dict]:
    """Notes in quarter-note units on a metre map [(start, quarters per bar)] -> bars, through the same grid code as auto-transcription."""
    starts, at = [], 0.0
    sizes = sorted(sigs) or [(0.0, 4)]
    while at < total:
        size = next((q for s, q in reversed(sizes) if s <= at + 1e-6), 4)
        starts.append(at)
        at += size
    beats = [float(i) for i in range(max(round(at), 2))]  # one beat per quarter; the last bar ends at the last beat
    structure = {"beats": beats, "bars": [int(round(s)) for s in starts]}
    return autotab.to_bars(notes, structure, kind)


# ───────────────────────── MIDI ─────────────────────────

def read_midi(path: Path) -> dict[str, dict]:
    import mido

    mid = mido.MidiFile(str(path))
    tpb = mid.ticks_per_beat
    sigs: list[tuple[float, int]] = []
    tracks: dict[tuple[int, int], dict] = {}
    for ti, track in enumerate(mid.tracks):
        t, program, name = 0, {}, ""
        open_: dict[tuple[int, int], int] = {}
        for msg in track:
            t += msg.time
            if msg.type == "time_signature":
                sigs.append((t / tpb, max(1, round(msg.numerator * 4 / msg.denominator))))
            elif msg.type == "track_name":
                name = msg.name
            elif msg.type == "program_change":
                program[msg.channel] = msg.program
            elif msg.type == "note_on" and msg.velocity > 0:
                open_[(msg.channel, msg.note)] = t
            elif msg.type in ("note_off", "note_on"):
                begun = open_.pop((msg.channel, msg.note), None)
                if begun is not None:
                    tr = tracks.setdefault((ti, msg.channel), {"notes": [], "name": name, "program": program.get(msg.channel, 0)})
                    tr["notes"].append((msg.note, begun / tpb, t / tpb))
    out, count = {}, dict.fromkeys(tabdata.KINDS, 0)
    total = max((n[2] for tr in tracks.values() for n in tr["notes"]), default=0.0)
    for (_, channel), tr in sorted(tracks.items()):
        kind = "drums" if channel == 9 else _kind_of_program(tr["program"], tr["name"])
        if kind and tr["notes"] and count[kind] < tabdata.MAX_PARTS[kind]:
            count[kind] += 1
            bars = _bars_of(tr["notes"], sigs, kind, total)
            out[tabdata.part_key(kind, count[kind])] = {"kind": kind, "name": tr["name"] or kind.capitalize(),
                                                        "tuning": autotab.TUNINGS[kind], "bars": bars}
    return out


# ───────────────────────── MusicXML ─────────────────────────

def _xml_root(path: Path) -> ET.Element:
    data = path.read_bytes()
    if path.suffix.lower() == ".mxl":
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith((".xml", ".musicxml")) and not n.startswith("META-INF")]
            if not names:
                raise FileError("Empty MusicXML archive")
            data = z.read(names[0])
    root = ET.fromstring(data)
    for el in root.iter():  # namespaces are optional in the wild
        el.tag = el.tag.rsplit("}", 1)[-1]
    return root


def read_musicxml(path: Path) -> dict[str, dict]:
    root = _xml_root(path)
    names = {p.get("id"): (p.findtext("part-name") or "") for p in root.iter("score-part")}
    out, count = {}, dict.fromkeys(tabdata.KINDS, 0)
    for part in root.iter("part"):
        notes, sigs, at, div, size, drums, lastdur = [], [], 0.0, 1, 4, False, 0.0
        for m in part.findall("measure"):
            start, longest, cursor = at, at, at
            for el in m:
                if el.tag == "attributes":
                    div = int(el.findtext("divisions") or div)
                    if (t := el.find("time")) is not None and t.findtext("beats") and t.findtext("beat-type"):
                        with contextlib.suppress(ValueError):
                            size = max(1, round(int(t.findtext("beats")) * 4 / int(t.findtext("beat-type"))))
                        sigs.append((start, size))
                elif el.tag == "note":
                    dur = int(el.findtext("duration") or 0) / div
                    chord = el.find("chord") is not None
                    if el.find("rest") is None:
                        if el.find("unpitched") is not None:
                            drums = True
                            pitch = 38  # MusicXML names the instrument by id, not by GM note: everything lands on the snare line
                        elif (p := el.find("pitch")) is not None and p.findtext("step") in _STEP:
                            pitch = 12 * (int(p.findtext("octave") or 4) + 1) + _STEP[p.findtext("step")] + int(float(p.findtext("alter") or 0))
                        else:
                            pitch = None
                        if pitch is not None and not (el.find("tie") is not None and el.find("tie").get("type") == "stop"):
                            begin = cursor - (lastdur if chord else 0)
                            notes.append((pitch, begin, begin + max(dur, 0.1)))
                    if not chord:
                        lastdur = dur
                        cursor += dur
                    longest = max(longest, cursor)
                elif el.tag == "backup":
                    cursor -= int(el.findtext("duration") or 0) / div
                elif el.tag == "forward":
                    cursor += int(el.findtext("duration") or 0) / div
            at = max(start + size, longest)
        if not notes:
            continue
        name = names.get(part.get("id"), "") or "Part"
        kind = "drums" if drums else _kind_of_program(0 if not re.search(r"guitar|bass", name, re.I) else 24, name)
        if kind and count[kind] < tabdata.MAX_PARTS[kind]:
            count[kind] += 1
            out[tabdata.part_key(kind, count[kind])] = {
                "kind": kind, "name": name, "tuning": autotab.TUNINGS[kind], "bars": _bars_of(notes, sigs, kind, at),
            }
    return out


# ───────────────────────── Guitar Pro 3-5 ─────────────────────────

def read_gp(path: Path) -> dict[str, dict]:
    import guitarpro

    try:
        song = guitarpro.parse(str(path))
    except Exception as e:  # noqa: BLE001 - pyguitarpro raises assorted errors on damaged or unsupported files
        raise FileError(f"Could not read {path.name}") from e
    heads = song.measureHeaders
    out, count = {}, dict.fromkeys(tabdata.KINDS, 0)
    for track in song.tracks:
        perc = track.isPercussionTrack
        kind = "drums" if perc else _kind_of_program(track.channel.instrument, track.name)
        if not kind or count[kind] >= tabdata.MAX_PARTS[kind]:
            continue
        tuning = [] if perc else [s.value for s in track.strings]
        bars, sig = [], [4, 4]
        for head, measure in zip(heads, track.measures, strict=False):
            sig = [head.timeSignature.numerator, head.timeSignature.denominator.value]
            ticks = 3840 * sig[0] / sig[1]
            notes = []
            for beat in measure.voices[0].beats:
                if not beat.notes or beat.status.name != "normal":
                    continue
                frets = []
                for n in beat.notes:
                    flags = "t" if n.type.name == "tie" else "x" if n.type.name == "dead" else ""
                    if perc:
                        line = autotab.DRUM_LINES.get(autotab.DRUM_FOLD.get(n.value, n.value))
                        if line is not None:
                            frets.append([line, autotab.DRUM_FOLD.get(n.value, n.value)])
                    else:
                        frets.append([n.string - 1, n.value] + ([flags] if flags else []))
                if frets:
                    notes.append([round((beat.start - head.start) / ticks, 4), round(beat.duration.time / ticks, 4), frets])
            bar = {"len": round(sig[0] / sig[1], 4), "n": notes}
            if head.marker is not None and head.marker.title:
                bar["mark"] = head.marker.title
            bars.append(bar)
        if any(b["n"] for b in bars):
            count[kind] += 1
            out[tabdata.part_key(kind, count[kind])] = {"kind": kind, "name": track.name.strip() or kind.capitalize(),
                                                        "tuning": tuning, "bars": bars}
    return out


def read_file(path: Path) -> dict[str, dict]:
    ext = path.suffix.lower()
    try:
        if ext in (".mid", ".midi"):
            return read_midi(path)
        if ext in (".gp3", ".gp4", ".gp5"):
            return read_gp(path)
        return read_musicxml(path)
    except FileError:
        raise
    except Exception as e:  # noqa: BLE001 - a broken file must not stop a folder
        raise FileError(f"Could not read {path.name}") from e


# ───────────────────────── matching ─────────────────────────

def name_score(path: Path, info: dict) -> float:
    """How much a file's name (and its folder's) looks like this song: 0..1."""
    hay = tabs._norm(f"{path.parent.name} {path.stem}")
    best = 0.0
    for artist, title in tabs.guess_query(info) or [("", tabs._norm(info.get("title") or ""))]:
        if not title:
            continue
        want = f"{artist} {title}".strip()
        s = SequenceMatcher(None, hay, want).ratio()
        if title in hay:
            s = max(s, 0.6 + 0.4 * (len(title) / max(len(hay), 1)))
        best = max(best, s)
    return best


def import_folder(folder: str) -> dict:
    """Match the folder's files to the analysed library songs that have no tab yet and keep each one that fits."""
    files = scan(folder)
    done, tried = [], 0
    for info in library.list_library():
        vid = info["id"]
        if not info.get("analyzed") or (tabdata.read(vid) or {}).get("found"):
            continue
        structure = analysis.read_structure(vid)
        if not structure:
            continue
        ranked = sorted(((name_score(f, info), f) for f in files), key=lambda x: -x[0])
        best = None
        for score, f in ranked[:TRIES]:
            if score < MIN_NAME:
                break
            tried += 1
            try:
                parts = read_file(f)
            except FileError as e:
                log.info("%s", e)
                continue
            fitted = tabdata.fit(parts, structure) if parts else None
            if fitted and (best is None or fitted["similarity"] * fitted["coverage"] > best[0]):
                best = (fitted["similarity"] * fitted["coverage"], f, parts, fitted)
        if best:
            _, f, parts, fitted = best
            tabdata.gapfill(parts, fitted, structure)
            tabdata.mark_weak(parts, fitted, structure)
            tabdata.retune(parts, fitted["shift"])
            tabdata._save(vid, {
                "found": True, "v": tabdata.FORMAT, "source": "file", "url": "", "artist": "", "title": f.stem, **fitted, "tracks": parts,
            })
            done.append({"id": vid, "title": info.get("title") or vid, "file": f.name})
    return {"scanned": len(files), "tried": tried, "matched": done}
