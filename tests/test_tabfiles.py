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
