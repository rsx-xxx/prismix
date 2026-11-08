import guitarpro as gp
import mido

from prismix import tabfiles


def _midi(path):
    mid = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    tr.append(mido.MetaMessage("time_signature", numerator=4, denominator=4))
    tr.append(mido.Message("program_change", channel=0, program=33, time=0))
    for i, note in enumerate([40, 45, 47, 52]):
        tr.append(mido.Message("note_on", channel=0, note=note, velocity=80, time=0 if i == 0 else 0))
        tr.append(mido.Message("note_off", channel=0, note=note, velocity=0, time=480))
    mid.save(path)


def test_midi_bass_lands_on_quarter_grid(tmp_path):
    f = tmp_path / "a.mid"
    _midi(f)
    parts = tabfiles.read_file(f)
    (key, p), = parts.items()
    assert p["kind"] == "bass" and len(p["bars"]) == 1
    assert [n[0] for n in p["bars"][0]["n"]] == [0.0, 0.25, 0.5, 0.75]


def test_guitar_pro_roundtrip(tmp_path):
    song = gp.Song()
    tr = song.tracks[0]
    head = song.measureHeaders[0] if song.measureHeaders else None
    if head is None:
        song.addMeasureHeader(gp.MeasureHeader())
        head = song.measureHeaders[0]
    m = tr.measures[0] if tr.measures else gp.Measure(tr, head)
    if not tr.measures:
        tr.measures.append(m)
    beat = gp.Beat(m.voices[0])
    beat.start = head.start
    beat.status = gp.BeatStatus.normal
    beat.duration = gp.Duration(4)
    note = gp.Note(beat)
    note.string, note.value, note.type = 2, 5, gp.NoteType.normal
    beat.notes.append(note)
    m.voices[0].beats.append(beat)
    f = tmp_path / "x.gp5"
    gp.write(song, str(f))
    parts = tabfiles.read_file(f)
    (p,) = parts.values()
    assert p["kind"] == "guitar"
    assert p["bars"][0]["n"][0][2] == [[1, 5]]


def test_name_score_prefers_the_right_song(tmp_path):
    info = {"title": "Master of Puppets", "artist": "Metallica"}
    good = tmp_path / "Metallica - Master of Puppets.gp5"
    bad = tmp_path / "Foo Fighters - Everlong.gp5"
    assert tabfiles.name_score(good, info) > tabfiles.MIN_NAME > tabfiles.name_score(bad, info)


def test_missing_folder():
    try:
        tabfiles.scan("/nonexistent/zzz")
    except tabfiles.FileError:
        return
    raise AssertionError


def test_unroll_repeats_and_voltas():
    plain = [{}, {"start": True}, {"end": True, "times": 2}, {}]
    assert tabfiles.unroll(plain) == [0, 1, 2, 1, 2, 3]
    volta = [{"start": True}, {"alt": [1], "end": True, "times": 2}, {"alt": [2]}, {}]
    assert tabfiles.unroll(volta) == [0, 1, 0, 2, 3]


def test_unroll_is_bounded():
    assert len(tabfiles.unroll([{"start": True, "end": True, "times": 10**9}])) <= 1500


GPIF = """<GPIF><MasterTrack><Automations><Automation><Type>Tempo</Type><Bar>0</Bar><Position>0</Position>
<Value>96 2</Value></Automation></Automations></MasterTrack>
<Tracks><Track id="0"><Name>Guitar</Name><InstrumentSet><Type>electricGuitar</Type></InstrumentSet>
<Staves><Staff><Properties><Property name="Tuning"><Pitches>40 45 50 55 59 64</Pitches></Property>
<Property name="DiagramCollection"><Items><Item id="0" name="Em"/></Items></Property></Properties></Staff></Staves></Track></Tracks>
<MasterBars><MasterBar><Time>4/4</Time><Repeat start="true" end="false" count="0"/><Section><Text>Verse</Text></Section><Bars>0</Bars></MasterBar>
<MasterBar><Time>4/4</Time><Repeat start="false" end="true" count="2"/><Bars>1</Bars></MasterBar></MasterBars>
<Bars><Bar id="0"><Voices>0 -1</Voices></Bar><Bar id="1"><Voices>1 -1</Voices></Bar></Bars>
<Voices><Voice id="0"><Beats>0 1</Beats></Voice><Voice id="1"><Beats>2</Beats></Voice></Voices>
<Beats><Beat id="0"><Rhythm ref="0"/><Chord>0</Chord><Notes>0 1</Notes>
<Properties><Property name="Brush"><Direction>Up</Direction></Property></Properties></Beat>
<Beat id="1"><Rhythm ref="0"/></Beat><Beat id="2"><Rhythm ref="1"/><Notes>2</Notes></Beat></Beats>
<Notes><Note id="0"><Properties><Property name="String"><String>0</String></Property><Property name="Fret"><Fret>0</Fret></Property>
<Property name="PalmMuted"><Enable/></Property></Properties></Note>
<Note id="1"><Properties><Property name="String"><String>5</String></Property><Property name="Fret"><Fret>3</Fret></Property></Properties></Note>
<Note id="2"><Properties><Property name="String"><String>1</String></Property><Property name="Fret"><Fret>7</Fret></Property></Properties></Note></Notes>
<Rhythms><Rhythm id="0"><NoteValue>Half</NoteValue></Rhythm><Rhythm id="1"><NoteValue>Whole</NoteValue></Rhythm></Rhythms></GPIF>"""


def _gp7(path, gpif=GPIF):
    import zipfile

    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Content/score.gpif", gpif)


def test_gpif_reads_strings_marks_and_unrolls(tmp_path):
    f = tmp_path / "x.gp"
    _gp7(f)
    parts = tabfiles.read_file(f)
    bars = parts["guitar"]["bars"]
    assert parts["guitar"]["tuning"] == [64, 59, 55, 50, 45, 40]
    assert len(bars) == 4  # the repeated pair is played twice
    assert bars[0]["mark"] == "Verse" and bars[0]["ch"] == [[0.0, "Em"]]
    first = bars[0]["n"][0]
    assert first[2] == [[0, 3], [5, 0, "m"]]  # string 0 of the file is the lowest; Prismix counts from the highest
    assert first[3] == {"st": "u", "bpm": 96}
    assert bars[2]["n"] == bars[0]["n"] and bars[3]["n"][0][1] == 1.0


def test_gpif_rejects_garbage(tmp_path):
    f = tmp_path / "x.gp"
    f.write_bytes(b"not a zip")
    try:
        tabfiles.read_file(f)
    except tabfiles.FileError:
        return
    raise AssertionError


def _xml(path):
    path.write_text("""<score-partwise><part-list><score-part id="P1"><part-name>Guitar</part-name></score-part></part-list>
<part id="P1"><measure number="1"><attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time></attributes>
<harmony><root><root-step>A</root-step></root><kind>minor</kind></harmony>
<note><pitch><step>E</step><octave>2</octave></pitch><duration>4</duration><staff>1</staff></note><backup><duration>4</duration></backup>
<note><pitch><step>E</step><octave>2</octave></pitch><duration>4</duration><staff>2</staff>
<notations><technical><string>6</string><fret>0</fret><palm-mute/></technical></notations></note></measure></part></score-partwise>""")


def test_musicxml_uses_the_tab_staff_once(tmp_path):
    f = tmp_path / "x.xml"
    _xml(f)
    bar = tabfiles.read_file(f)["guitar"]["bars"][0]
    assert bar["n"] == [[0.0, 1.0, [[5, 0, "m"]]]]
    assert bar["ch"] == [[0.0, "Am"]]


def test_import_file_saves_fitted_tab(tmp_path, monkeypatch):
    from prismix import analysis, library, tabdata

    saved = {}
    monkeypatch.setattr(library, "read_info", lambda v: {"title": "x"})
    monkeypatch.setattr(analysis, "read_structure", lambda v: {"beats": [], "bars": []})
    monkeypatch.setattr(tabdata, "fit", lambda parts, st: {"map": [0, 1, 2, 3], "shift": 0, "similarity": 1.0, "coverage": 1.0})
    monkeypatch.setattr(tabdata, "gapfill", lambda *a: None)
    monkeypatch.setattr(tabdata, "mark_weak", lambda *a: None)
    monkeypatch.setattr(tabdata, "retune", lambda *a: None)
    monkeypatch.setattr(tabdata, "_raw", lambda v: None)
    monkeypatch.setattr(tabdata, "_save", lambda v, d: saved.update(d))
    monkeypatch.setattr(tabdata, "read", lambda v: None)
    monkeypatch.setattr(tabdata, "_edits_path", lambda v: tmp_path / "edits.json")
    f = tmp_path / "x.gp"
    _gp7(f)
    tabfiles.import_file("dQw4w9WgXcQ", "My Song.gp", f.read_bytes())
    assert saved["source"] == "file" and saved["title"] == "My Song" and "guitar" in saved["tracks"]


def test_import_file_refuses_unknown_type():
    try:
        tabfiles.import_file("vid", "x.exe", b"MZ")
    except tabfiles.FileError:
        return
    raise AssertionError


def test_add_source_lays_score_beside_existing_tab(tmp_path, monkeypatch):
    from prismix import tabdata

    store = {}
    monkeypatch.setattr(tabdata, "_raw", lambda v: store.get("tab"))
    monkeypatch.setattr(tabdata, "_save", lambda v, d: store.__setitem__("tab", d))
    monkeypatch.setattr(tabdata, "_edits_path", lambda v: tmp_path / "edits.json")
    bar = lambda f: {"len": 1.0, "n": [[0.0, 1.0, [[0, f]]]]}  # noqa: E731
    store["tab"] = {
        "found": True, "source": "songsterr", "url": "https://s/x", "map": [0, 1, None, 3],
        "tracks": {"guitar": {"kind": "guitar", "name": "G", "tuning": [64], "bars": [bar(0)] * 4}},
    }
    # the new score's bar k plays recording bar 3 - k: its bars are found through the recording, not by position
    part = {"kind": "guitar", "name": "Lead", "tuning": [64], "bars": [bar(9), bar(8), bar(7), bar(6)]}
    tab = tabdata.add_source("v", {"guitar": part}, {"map": [3, 2, 1, 0]}, {"source": "file", "origin": "file:a.gp", "label": "a.gp"})
    assert tab["added"] == ["guitar2"]
    new = tab["tracks"]["guitar2"]
    assert new["source"] == "file" and new["label"] == "a.gp" and tab["tracks"]["guitar"]["source"] == "songsterr"
    assert [b["n"][0][2][0][1] if b["n"] else None for b in new["bars"]] == [6, 7, None, 9]
    again = tabdata.add_source("v", {"guitar": dict(part, bars=[bar(5)] * 4)}, {"map": [0, 1, 2, 3]}, {"source": "file", "origin": "file:a.gp", "label": "a.gp"})
    assert list(again["tracks"]) == ["guitar", "guitar2"]  # the same file again replaces its parts


def test_add_source_same_file_twice_is_a_duplicate_and_changes_are_an_update(tmp_path, monkeypatch):
    from prismix import tabdata

    store = {}
    monkeypatch.setattr(tabdata, "_raw", lambda v: store.get("tab"))
    monkeypatch.setattr(tabdata, "_save", lambda v, d: store.__setitem__("tab", d))
    monkeypatch.setattr(tabdata, "_edits_path", lambda v: tmp_path / "edits.json")
    bar = lambda f: {"len": 1.0, "n": [[0.0, 1.0, [[0, f]]]]}  # noqa: E731
    store["tab"] = {
        "found": True, "source": "songsterr", "url": "", "map": [0, 1],
        "tracks": {"guitar": {"kind": "guitar", "name": "G", "tuning": [64], "bars": [bar(0)] * 2}},
    }
    meta = {"source": "file", "origin": "file:a.gp", "label": "a.gp"}
    fitted = {"map": [0, 1]}
    parts = lambda f: {"guitar": {"kind": "guitar", "name": "Solo", "tuning": [64], "bars": [bar(f)] * 2}}  # noqa: E731
    assert tabdata.add_source("v", parts(5), fitted, meta)["status"] == "new"
    same = tabdata.add_source("v", parts(5), fitted, dict(meta, origin="file:a (1).gp"))  # another file name, same music
    assert same["status"] == "duplicate" and same["added"] == [] and list(same["tracks"]) == ["guitar", "guitar2"]
    newer = tabdata.add_source("v", parts(7), fitted, meta)
    assert newer["status"] == "updated" and list(newer["tracks"]) == ["guitar", "guitar2"]
    assert newer["tracks"]["guitar2"]["bars"][0]["n"][0][2][0][1] == 7
