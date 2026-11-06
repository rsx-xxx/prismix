"""Notation the user already has on disk: a folder of Guitar Pro (.gp3-.gp5, .gp), MusicXML (.musicxml, .xml, .mxl) and MIDI files.

Every file is read into the same bars as a downloaded tab, matched to the library songs by its name, and kept only when it
really fits the recording (chords bar by bar, in whatever key it is written). A wrong guess from the name therefore costs nothing.
"""
from __future__ import annotations

import contextlib
import logging
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from difflib import SequenceMatcher
from pathlib import Path

from . import analysis, autotab, library, tabdata, tabs

log = logging.getLogger(__name__)

EXTS = {".gp3", ".gp4", ".gp5", ".gp", ".mid", ".midi", ".musicxml", ".xml", ".mxl"}
MAX_FILES = 3000
MAX_BYTES = 8_000_000
MAX_UNZIPPED = 64_000_000  # a zip bomb must not fill memory: the largest member a score archive may inflate to
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
    if "vocal" in n or "voice" in n or "singer" in n:
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


# ───────────────────────── repeats ─────────────────────────

def unroll(marks: list[dict]) -> list[int]:
    """Bar indexes in play order. Each mark: {"start": bool, "end": bool, "times": plays of the section, "alt": volta numbers}."""
    order, i, start, played, ended, back = [], 0, 0, 1, {}, False
    while i < len(marks) and len(order) < tabdata.MAX_BARS:
        m = marks[i]
        if m.get("start") and not back:
            start, played = i, 1
        back = False
        if m.get("alt") and played not in m["alt"]:
            i += 1
            continue
        order.append(i)
        if m.get("end"):
            done = ended.get(i, 0) + 1
            if done < max(int(m.get("times") or 2), 1):
                ended[i] = done
                played, i, back = done + 1, start, True
                continue
            ended.pop(i, None)
            played = 1
        i += 1
    return order


# ───────────────────────── MusicXML ─────────────────────────

_KINDS = {"major": "", "minor": "m", "dominant": "7", "major-seventh": "maj7", "minor-seventh": "m7", "diminished": "dim",
          "augmented": "aug", "suspended-fourth": "sus4", "suspended-second": "sus2", "major-sixth": "6", "minor-sixth": "m6",
          "dominant-ninth": "9", "power": "5", "half-diminished": "m7b5", "diminished-seventh": "dim7"}


def _xml_root(path: Path) -> ET.Element:
    data = path.read_bytes()
    if path.suffix.lower() == ".mxl":
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith((".xml", ".musicxml")) and not n.startswith("META-INF")]
            if not names:
                raise FileError("Empty MusicXML archive")
            if z.getinfo(names[0]).file_size > MAX_UNZIPPED:
                raise FileError("MusicXML archive is too large")
            data = z.read(names[0])
    root = ET.fromstring(data)
    for el in root.iter():  # namespaces are optional in the wild
        el.tag = el.tag.rsplit("}", 1)[-1]
    return root


def _chord_name(h: ET.Element) -> str:
    root = h.find("root")
    if root is None or root.findtext("root-step") not in _STEP:
        return ""
    alter = int(float(root.findtext("root-alter") or 0))
    kind = h.find("kind")
    name = root.findtext("root-step") + ("#" * alter if alter > 0 else "b" * -alter)
    name += kind.get("text") if kind is not None and kind.get("text") is not None else _KINDS.get(kind.text if kind is not None else "", "")
    bass = h.find("bass")
    if bass is not None and bass.findtext("bass-step") in _STEP:
        b = int(float(bass.findtext("bass-alter") or 0))
        name += "/" + bass.findtext("bass-step") + ("#" * b if b > 0 else "b" * -b)
    return name


def _xml_measures(part: ET.Element, midi: dict[str, int]) -> tuple[list[dict], list[dict]]:
    """A part's measures -> ([{size, notes, mark, sig, ch, tempo}], repeat marks); note offsets are in quarters."""
    out, marks, div, size, volta = [], [], 1, 4.0, []
    for m in part.findall("measure"):
        cur, longest, lastdur, notes, chords, closing = 0.0, 0.0, 0.0, [], [], False
        info = {"mark": "", "sig": None, "tempo": 0}
        for el in m:
            if el.tag == "attributes":
                div = int(el.findtext("divisions") or div) or 1
                t = el.find("time")
                if t is not None and t.findtext("beats") and t.findtext("beat-type"):
                    with contextlib.suppress(ValueError):
                        b, d = int(t.findtext("beats").split("+")[0]), int(t.findtext("beat-type"))
                        size = b * 4 / d
                        info["sig"] = [b, d]
            elif el.tag == "direction":
                if (r := el.find("direction-type/rehearsal")) is not None and (r.text or "").strip():
                    info["mark"] = r.text.strip()
                snd = el.find("sound")
                if snd is not None and snd.get("tempo"):
                    with contextlib.suppress(ValueError):
                        info["tempo"] = round(float(snd.get("tempo")))
            elif el.tag == "harmony":
                if name := _chord_name(el):
                    chords.append([cur, name])
            elif el.tag == "barline":
                rep, end = el.find("repeat"), el.find("ending")
                if end is not None:
                    nums = [int(x) for x in re.findall(r"\d+", end.get("number") or "")]
                    if end.get("type") == "start":
                        volta = nums
                    else:
                        closing = True
                if rep is not None:
                    info.setdefault("rep", {})[rep.get("direction")] = int(rep.get("times") or 2)
            elif el.tag == "note":
                dur = int(el.findtext("duration") or 0) / div
                chord = el.find("chord") is not None
                begin = cur - (lastdur if chord else 0)
                if el.find("rest") is None and el.find("grace") is None:
                    tech = el.find("notations/technical")
                    tie = [t.get("type") for t in el.findall("tie")]
                    note = {"at": begin, "dur": max(dur, 0.0), "fl": ""}
                    if tech is not None and tech.findtext("string") and tech.findtext("fret"):
                        note["string"], note["fret"] = int(tech.findtext("string")) - 1, int(tech.findtext("fret"))
                    inst = el.find("instrument")
                    note["drum"] = el.find("unpitched") is not None
                    if note["drum"]:
                        note["pitch"] = midi.get(inst.get("id", "") if inst is not None else "", 38)
                    elif (p := el.find("pitch")) is not None and p.findtext("step") in _STEP:
                        note["pitch"] = 12 * (int(p.findtext("octave") or 4) + 1) + _STEP[p.findtext("step")] + int(float(p.findtext("alter") or 0))
                    if tech is not None:
                        flag = {"hammer-on": "h", "pull-off": "h", "palm-mute": "m"}
                        note["fl"] += "".join(v for k, v in flag.items() if tech.find(k) is not None)
                    if el.find("notations/slide") is not None:
                        note["fl"] += "s"
                    if el.findtext("notehead") == "x":
                        note["fl"] += "x"
                    if "stop" in tie:
                        note["fl"] += "t"
                    if "pitch" in note or "string" in note:
                        notes.append(note)
                if not chord:
                    lastdur = dur
                    cur += dur
                longest = max(longest, cur)
            elif el.tag == "backup":
                cur -= int(el.findtext("duration") or 0) / div
            elif el.tag == "forward":
                cur += int(el.findtext("duration") or 0) / div
                longest = max(longest, cur)
        rep = info.get("rep", {})
        marks.append({"start": "forward" in rep, "end": "backward" in rep, "times": rep.get("backward", 2), "alt": list(volta)})
        if closing:
            volta = []
        info.update(size=max(size, longest) if longest > size + 1e-6 else size, notes=notes, ch=chords)
        out.append(info)
    return out, marks


def _xml_bars(ms: list[dict], order: list[int], kind: str) -> list[dict]:
    """Measures that carry string/fret numbers (a tab staff) -> bars directly; the notes keep their written strings."""
    bars = []
    for i in order:
        m = ms[i]
        length = m["size"] / 4
        beats: dict[float, dict] = {}
        for n in m["notes"]:
            if "string" not in n:
                continue
            b = beats.setdefault(round(n["at"], 4), {"dur": n["dur"], "frets": {}})
            b["dur"] = max(b["dur"], n["dur"])
            b["frets"].setdefault(n["string"], [n["string"], n["fret"], n["fl"]] if n["fl"] else [n["string"], n["fret"]])
        notes = [[round(at / m["size"], 4), round(b["dur"] / m["size"], 4), sorted(b["frets"].values())] for at, b in sorted(beats.items())]
        bar = {"len": round(length, 4), "n": notes}
        if m["sig"]:
            bar["sig"] = m["sig"]
        if m["mark"]:
            bar["mark"] = m["mark"]
        if m["ch"]:
            bar["ch"] = [[round(at / m["size"], 4), name] for at, name in m["ch"]]
        if m["tempo"] and notes:
            notes[0] = [*notes[0], {"bpm": m["tempo"]}]
        bars.append(bar)
    return bars


def read_musicxml(path: Path) -> dict[str, dict]:
    root = _xml_root(path)
    names = {p.get("id"): (p.findtext("part-name") or "") for p in root.iter("score-part")}
    midi = {i.get("id"): int(i.findtext("midi-unpitched")) - 1 for i in root.iter("midi-instrument") if (i.findtext("midi-unpitched") or "").isdigit()}
    progs = {p.get("id"): int(p.findtext(".//midi-program") or 0) for p in root.iter("score-part")}
    out, count = {}, dict.fromkeys(tabdata.KINDS, 0)
    for part in root.iter("part"):
        ms, marks = _xml_measures(part, midi)
        order = unroll(marks) if any(m["start"] or m["end"] or m["alt"] for m in marks) else list(range(len(ms)))
        every = [n for m in ms for n in m["notes"]]
        if not every:
            continue
        name = names.get(part.get("id"), "").strip() or "Part"
        drums = any(n.get("drum") for n in every)
        tabbed = not drums and any("string" in n for n in every)
        kind = "drums" if drums else _kind_of_program(progs.get(part.get("id"), 0) or (24 if re.search(r"guitar|bass", name, re.I) else 0), name)
        if not kind or count[kind] >= tabdata.MAX_PARTS[kind]:
            continue
        count[kind] += 1
        if tabbed:
            strings = max(n["string"] for n in every if "string" in n) + 1
            base = autotab.TUNINGS["bass" if kind == "bass" else "guitar"]
            tuning = base[:strings] + [base[-1] - 5 * (k + 1) for k in range(max(0, strings - len(base)))]
            tuning = tuning if strings >= len(base) or kind == "bass" else base
            bars = _xml_bars(ms, order, kind)
        else:
            seen, notes, sigs, at = set(), [], [], 0.0
            for i in order:
                m = ms[i]
                if m["sig"]:
                    sigs.append((at, round(m["size"])))
                for n in m["notes"]:
                    key = (round(at + n["at"], 3), n.get("pitch"))
                    if "pitch" in n and key not in seen and "t" not in n["fl"]:
                        seen.add(key)
                        notes.append((n["pitch"], at + n["at"], at + n["at"] + max(n["dur"], 0.1)))
                at += m["size"]
            bars, tuning = _bars_of(notes, sigs, kind, at), autotab.TUNINGS[kind]
            if len(bars) == len(order):  # the grid kept the written bars: their section names and chords still apply
                for bar, i in zip(bars, order, strict=True):
                    if ms[i]["mark"]:
                        bar["mark"] = ms[i]["mark"]
        out[tabdata.part_key(kind, count[kind])] = {"kind": kind, "name": name, "tuning": tuning, "bars": bars}
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


# ───────────────────────── Guitar Pro 7/8 (.gp) ─────────────────────────

_VALUES = {"Whole": 1.0, "Half": 0.5, "Quarter": 0.25, "Eighth": 0.125, "16th": 1 / 16, "32nd": 1 / 32, "64th": 1 / 64,
           "128th": 1 / 128, "256th": 1 / 256}


def _gpif_root(path: Path) -> ET.Element:
    try:
        with zipfile.ZipFile(path) as z:
            info = z.getinfo("Content/score.gpif")
            if info.file_size > MAX_UNZIPPED:
                raise FileError("Guitar Pro archive is too large")
            return ET.fromstring(z.read(info))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise FileError(f"Could not read {path.name}") from e


def _ids(text: str | None) -> list[str]:
    return [t for t in (text or "").split() if t != "-1"]


def _props(el: ET.Element) -> dict[str, ET.Element]:
    box = el.find("Properties")
    return {p.get("name"): p for p in box.findall("Property")} if box is not None else {}


def _gpif_tempos(root: ET.Element) -> dict[int, list[tuple[float, int]]]:
    out: dict[int, list[tuple[float, int]]] = {}
    for a in root.findall("MasterTrack/Automations/Automation"):
        if a.findtext("Type") == "Tempo":
            with contextlib.suppress(ValueError, IndexError):
                bpm = round(float((a.findtext("Value") or "").split()[0]))
                out.setdefault(int(a.findtext("Bar") or 0), []).append((float(a.findtext("Position") or 0), bpm))
    return out


def read_gpif(path: Path) -> dict[str, dict]:
    root = _gpif_root(path)
    bars_by = {b.get("id"): b for b in root.findall("Bars/Bar")}
    voices = {v.get("id"): v for v in root.findall("Voices/Voice")}
    beats = {b.get("id"): b for b in root.findall("Beats/Beat")}
    notes = {n.get("id"): n for n in root.findall("Notes/Note")}
    rhythms = {r.get("id"): r for r in root.findall("Rhythms/Rhythm")}
    masters = root.findall("MasterBars/MasterBar")
    tempos = _gpif_tempos(root)
    marks = []
    for m in masters:
        rep = m.find("Repeat")
        alt = [int(x) for x in re.findall(r"\d+", m.findtext("AlternateEndings") or "")]
        marks.append({"start": rep is not None and rep.get("start") == "true", "end": rep is not None and rep.get("end") == "true",
                      "times": int(rep.get("count") or 0) if rep is not None else 0, "alt": alt})
    order = unroll(marks) if any(m["start"] or m["end"] or m["alt"] for m in marks) else list(range(len(masters)))
    out, count, staff_at = {}, dict.fromkeys(tabdata.KINDS, 0), 0
    for track in root.findall("Tracks/Track"):
        staves = track.findall("Staves/Staff")
        col, staff_at = staff_at, staff_at + max(len(staves), 1)
        name = (track.findtext("Name") or "").strip()
        iset = track.find("InstrumentSet")
        perc = (iset.findtext("Type") if iset is not None else "") == "drumKit"
        kind = "drums" if perc else _kind_of_program(int(track.findtext("GeneralMidi/Program") or 0) or
                                                     (24 if (iset.findtext("Type") if iset is not None else "") == "electricGuitar" else 0), name)
        if (iset is not None and iset.findtext("Type") == "electricBass") or re.search(r"\bbass\b", name, re.I):
            kind = kind and "bass"
        if not kind or count[kind] >= tabdata.MAX_PARTS[kind] or not staves:
            continue
        sprops = _props(staves[0])
        pitches = [int(x) for x in ((sprops.get("Tuning").findtext("Pitches") if "Tuning" in sprops else "") or "").split()]
        tuning = [] if perc else pitches[::-1] or autotab.TUNINGS[kind]
        names = {}
        if "DiagramCollection" in sprops:
            names = {i.get("id"): i.get("name") for i in sprops["DiagramCollection"].iter("Item") if i.get("name")}
        arts = [a for e in iset.findall("Elements/Element") for a in e.findall("Articulations/Articulation")] if perc else []
        bars = []
        for mi in order:
            master = masters[mi]
            ids = (master.findtext("Bars") or "").split()
            bar_el = bars_by.get(ids[col]) if col < len(ids) else None
            num, den = (int(x) for x in (master.findtext("Time") or "4/4").split("/"))
            length = num / den
            beats_out, chords = [], []
            vs = [voices[v] for v in _ids(bar_el.findtext("Voices") if bar_el is not None else "") if v in voices]
            voice = next((v for v in vs if any(beats[b].find("Notes") is not None for b in _ids(v.findtext("Beats")))), None)
            at = 0.0
            for bid in _ids(voice.findtext("Beats") if voice is not None else ""):
                beat = beats[bid]
                rh = rhythms[beat.find("Rhythm").get("ref")]
                dur = _VALUES.get(rh.findtext("NoteValue"), 0.25)
                dots = rh.find("AugmentationDot")
                if dots is not None:
                    d = int(dots.get("count") or 1)
                    dur *= 2 - 0.5 ** d
                tup = rh.find("PrimaryTuplet")
                if tup is not None and int(tup.get("num") or 1) and int(tup.get("den") or 1):
                    dur *= int(tup.get("den")) / int(tup.get("num"))
                if beat.find("GraceNotes") is not None:
                    continue
                if (c := beat.findtext("Chord")) is not None and c in names:
                    chords.append([round(at / length, 4), names[c]])
                frets = []
                for nid in _ids(beat.findtext("Notes")):
                    n = notes[nid]
                    pr = _props(n)
                    flags = ""
                    tie = n.find("Tie")
                    flags += "t" if tie is not None and tie.get("destination") == "true" else ""
                    flags += "s" if "Slide" in pr else ""
                    flags += "h" if "HopoDestination" in pr else ""
                    flags += "x" if "Muted" in pr else ""
                    flags += "m" if "PalmMuted" in pr else ""
                    if perc:
                        with contextlib.suppress(IndexError, ValueError, TypeError):
                            midi_n = int(arts[int(n.findtext("InstrumentArticulation"))].findtext("OutputMidiNumber"))
                            line = autotab.DRUM_LINES.get(autotab.DRUM_FOLD.get(midi_n, midi_n))
                            if line is not None:
                                frets.append([line, autotab.DRUM_FOLD.get(midi_n, midi_n)])
                    elif "Fret" in pr and "String" in pr:
                        string = len(tuning) - 1 - int(pr["String"].findtext("String"))
                        if 0 <= string < len(tuning):
                            frets.append([string, int(pr["Fret"].findtext("Fret"))] + ([flags] if flags else []))
                if frets:
                    extra = {}
                    if beat.find("LetRing") is not None or any(notes[n].find("LetRing") is not None for n in _ids(beat.findtext("Notes"))):
                        extra["lr"] = 1
                    if (br := _props(beat).get("Brush")) is not None:
                        extra["st"] = "u" if br.findtext("Direction") == "Up" else "d"
                    entry = [round(at / length, 4), round(dur / length, 4), sorted(frets)]
                    beats_out.append(entry + [extra] if extra else entry)
                at += dur
            for pos, bpm in tempos.get(mi, []):
                tail = next((b for b in beats_out if b[0] >= pos - 1e-6), None)
                if tail is not None and 20 <= bpm <= 400:
                    tail[3:] = [{**(tail[3] if len(tail) > 3 else {}), "bpm": bpm}]
            bar = {"len": round(length, 4), "n": beats_out}
            if chords:
                bar["ch"] = chords
            sect = master.find("Section")
            if sect is not None and (sect.findtext("Text") or "").strip():
                bar["mark"] = sect.findtext("Text").strip()
            bars.append(bar)
        if any(b["n"] for b in bars):
            count[kind] += 1
            out[tabdata.part_key(kind, count[kind])] = {"kind": kind, "name": name or kind.capitalize(), "tuning": tuning, "bars": bars}
    return out


def read_file(path: Path) -> dict[str, dict]:
    ext = path.suffix.lower()
    try:
        if ext in (".mid", ".midi"):
            return read_midi(path)
        if ext == ".gp":
            return read_gpif(path)
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


def import_file(video_id: str, filename: str, data: bytes) -> dict:
    """Read one uploaded score, fit it to this recording's bars and keep it as the song's tab."""
    suffix = Path(filename).suffix.lower()
    if suffix not in EXTS:
        raise FileError("Unsupported file: use Guitar Pro (.gp, .gp3-.gp5), MusicXML (.musicxml, .xml, .mxl) or MIDI")
    if not 0 < len(data) <= MAX_BYTES:
        raise FileError("That file is empty or too large")
    info = library.read_info(video_id)
    structure = analysis.read_structure(video_id)
    if not info or not structure:
        raise tabdata.TabDataError("Analyse the track first")
    with tempfile.TemporaryDirectory(prefix="prismix-tab-") as d:
        path = Path(d) / f"score{suffix}"
        path.write_bytes(data)
        parts = read_file(path)
    if not parts:
        raise FileError("That file has no guitar, bass, keys or drum notes")
    fitted = tabdata.fit(parts, structure)
    if not fitted:
        raise tabdata.TabDataError("That score does not look like this recording")
    tabdata.gapfill(parts, fitted, structure)
    tabdata.mark_weak(parts, fitted, structure)
    tabdata.retune(parts, fitted["shift"])
    return tabdata.add_source(video_id, parts, fitted, {
        "source": "file", "origin": f"file:{Path(filename).name}", "label": Path(filename).name, "title": Path(filename).stem,
    })


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
